"""
utils.py - PyLink utilities module.

This module contains various utility functions related to IRC and/or the PyLink
framework.
"""

import string
import re
import inspect
import importlib
import os

from log import log
import world
import conf

class NotAuthenticatedError(Exception):
    """
    Exception raised by checkAuthenticated() when a user fails authentication
    requirements.
    """
    pass

class IncrementalUIDGenerator():
    """
    Incremental UID Generator module, adapted from InspIRCd source:
    https://github.com/inspircd/inspircd/blob/f449c6b296ab/src/server.cpp#L85-L156
    """

    def __init__(self, sid):
        if not (hasattr(self, 'allowedchars') and hasattr(self, 'length')):
             raise RuntimeError("Allowed characters list not defined. Subclass "
                                "%s by defining self.allowedchars and self.length "
                                "and then calling super().__init__()." % self.__class__.__name__)
        self.uidchars = [self.allowedchars[0]]*self.length
        self.sid = sid

    def increment(self, pos=None):
        """
        Increments the UID generator to the next available UID.
        """
        # Position starts at 1 less than the UID length.
        if pos is None:
            pos = self.length - 1

        # If we're at the last character in the list of allowed ones, reset
        # and increment the next level above.
        if self.uidchars[pos] == self.allowedchars[-1]:
            self.uidchars[pos] = self.allowedchars[0]
            self.increment(pos-1)
        else:
            # Find what position in the allowed characters list we're currently
            # on, and add one.
            idx = self.allowedchars.find(self.uidchars[pos])
            self.uidchars[pos] = self.allowedchars[idx+1]

    def next_uid(self):
        """
        Returns the next unused UID for the server.
        """
        uid = self.sid + ''.join(self.uidchars)
        self.increment()
        return uid

def add_cmd(func, name=None):
    """Binds an IRC command function to the given command name."""
    if name is None:
        name = func.__name__
    name = name.lower()
    world.commands[name].append(func)
    return func

def add_hook(func, command):
    """Binds a hook function to the given command name."""
    command = command.upper()
    world.hooks[command].append(func)
    return func

def toLower(irc, text):
    """Returns a lowercase representation of text based on the IRC object's
    casemapping (rfc1459 or ascii)."""
    if irc.proto.casemapping == 'rfc1459':
        text = text.replace('{', '[')
        text = text.replace('}', ']')
        text = text.replace('|', '\\')
        text = text.replace('~', '^')
    return text.lower()

_nickregex = r'^[A-Za-z\|\\_\[\]\{\}\^\`][A-Z0-9a-z\-\|\\_\[\]\{\}\^\`]*$'
def isNick(s, nicklen=None):
    """Returns whether the string given is a valid nick."""
    if nicklen and len(s) > nicklen:
        return False
    return bool(re.match(_nickregex, s))

def isChannel(s):
    """Returns whether the string given is a valid channel name."""
    return str(s).startswith('#')

def _isASCII(s):
    """Returns whether the string given is valid ASCII."""
    chars = string.ascii_letters + string.digits + string.punctuation
    return all(char in chars for char in s)

def isServerName(s):
    """Returns whether the string given is a valid IRC server name."""
    return _isASCII(s) and '.' in s and not s.startswith('.')

hostmaskRe = re.compile(r'^\S+!\S+@\S+$')
def isHostmask(text):
    """Returns whether the given text is a valid hostmask."""
    # Band-aid patch here to prevent bad bans set by Janus forwarding people into invalid channels.
    return hostmaskRe.match(text) and '#' not in text

def parseModes(irc, target, args):
    """Parses a modestring list into a list of (mode, argument) tuples.
    ['+mitl-o', '3', 'person'] => [('+m', None), ('+i', None), ('+t', None), ('+l', '3'), ('-o', 'person')]
    """
    # http://www.irc.org/tech_docs/005.html
    # A = Mode that adds or removes a nick or address to a list. Always has a parameter.
    # B = Mode that changes a setting and always has a parameter.
    # C = Mode that changes a setting and only has a parameter when set.
    # D = Mode that changes a setting and never has a parameter.
    assert args, 'No valid modes were supplied!'
    usermodes = not isChannel(target)
    prefix = ''
    modestring = args[0]
    args = args[1:]
    if usermodes:
        log.debug('(%s) Using irc.umodes for this query: %s', irc.name, irc.umodes)

        if target not in irc.users:
            log.warning('(%s) Possible desync! Mode target %s is not in the users index.', irc.name, target)
            return []  # Return an empty mode list

        supported_modes = irc.umodes
        oldmodes = irc.users[target].modes
    else:
        log.debug('(%s) Using irc.cmodes for this query: %s', irc.name, irc.cmodes)

        if target not in irc.channels:
            log.warning('(%s) Possible desync! Mode target %s is not in the channels index.', irc.name, target)
            return []

        supported_modes = irc.cmodes
        oldmodes = irc.channels[target].modes
    res = []
    for mode in modestring:
        if mode in '+-':
            prefix = mode
        else:
            if not prefix:
                prefix = '+'
            arg = None
            log.debug('Current mode: %s%s; args left: %s', prefix, mode, args)
            try:
                if mode in (supported_modes['*A'] + supported_modes['*B']):
                    # Must have parameter.
                    log.debug('Mode %s: This mode must have parameter.', mode)
                    arg = args.pop(0)
                    if prefix == '-' and mode in supported_modes['*B'] and arg == '*':
                        # Charybdis allows unsetting +k without actually
                        # knowing the key by faking the argument when unsetting
                        # as a single "*".
                        # We'd need to know the real argument of +k for us to
                        # be able to unset the mode.
                        oldargs = [m[1] for m in oldmodes if m[0] == mode]
                        if oldargs:
                            # Set the arg to the old one on the channel.
                            arg = oldargs[0]
                            log.debug("Mode %s: coersing argument of '*' to %r.", mode, arg)
                elif mode in irc.prefixmodes and not usermodes:
                    # We're setting a prefix mode on someone (e.g. +o user1)
                    log.debug('Mode %s: This mode is a prefix mode.', mode)
                    arg = args.pop(0)
                    # Convert nicks to UIDs implicitly; most IRCds will want
                    # this already.
                    arg = irc.nickToUid(arg) or arg
                    if arg not in irc.users:  # Target doesn't exist, skip it.
                        log.debug('(%s) Skipping setting mode "%s %s"; the '
                                  'target doesn\'t seem to exist!', irc.name,
                                  mode, arg)
                        continue
                elif prefix == '+' and mode in supported_modes['*C']:
                    # Only has parameter when setting.
                    log.debug('Mode %s: Only has parameter when setting.', mode)
                    arg = args.pop(0)
            except IndexError:
                log.warning('(%s/%s) Error while parsing mode %r: mode requires an '
                            'argument but none was found. (modestring: %r)',
                            irc.name, target, mode, modestring)
                continue  # Skip this mode; don't error out completely.
            res.append((prefix + mode, arg))
    return res

def applyModes(irc, target, changedmodes):
    """Takes a list of parsed IRC modes, and applies them on the given target.

    The target can be either a channel or a user; this is handled automatically."""
    usermodes = not isChannel(target)
    log.debug('(%s) Using usermodes for this query? %s', irc.name, usermodes)

    try:
        if usermodes:
            old_modelist = irc.users[target].modes
            supported_modes = irc.umodes
        else:
            old_modelist = irc.channels[target].modes
            supported_modes = irc.cmodes
    except KeyError:
        log.warning('(%s) Possible desync? Mode target %s is unknown.', irc.name, target)
        return

    modelist = set(old_modelist)
    log.debug('(%s) Applying modes %r on %s (initial modelist: %s)', irc.name, changedmodes, target, modelist)
    for mode in changedmodes:
        # Chop off the +/- part that parseModes gives; it's meaningless for a mode list.
        try:
            real_mode = (mode[0][1], mode[1])
        except IndexError:
            real_mode = mode

        if not usermodes:
            # We only handle +qaohv for now. Iterate over every supported mode:
            # if the IRCd supports this mode and it is the one being set, add/remove
            # the person from the corresponding prefix mode list (e.g. c.prefixmodes['op']
            # for ops).
            for pmode, pmodelist in irc.channels[target].prefixmodes.items():
                if pmode in irc.cmodes and real_mode[0] == irc.cmodes[pmode]:
                    log.debug('(%s) Initial prefixmodes list: %s', irc.name, pmodelist)
                    if mode[0][0] == '+':
                        pmodelist.add(mode[1])
                    else:
                        pmodelist.discard(mode[1])

                    log.debug('(%s) Final prefixmodes list: %s', irc.name, pmodelist)

            if real_mode[0] in irc.prefixmodes:
                # Don't add prefix modes to IrcChannel.modes; they belong in the
                # prefixmodes mapping handled above.
                log.debug('(%s) Not adding mode %s to IrcChannel.modes because '
                          'it\'s a prefix mode.', irc.name, str(mode))
                continue

        if mode[0][0] == '+':
            # We're adding a mode
            existing = [m for m in modelist if m[0] == real_mode[0] and m[1] != real_mode[1]]
            if existing and real_mode[1] and real_mode[0] not in irc.cmodes['*A']:
                # The mode we're setting takes a parameter, but is not a list mode (like +beI).
                # Therefore, only one version of it can exist at a time, and we must remove
                # any old modepairs using the same letter. Otherwise, we'll get duplicates when,
                # for example, someone sets mode "+l 30" on a channel already set "+l 25".
                log.debug('(%s) Old modes for mode %r exist on %s, removing them: %s',
                          irc.name, real_mode, target, str(existing))
                [modelist.discard(oldmode) for oldmode in existing]
            modelist.add(real_mode)
            log.debug('(%s) Adding mode %r on %s', irc.name, real_mode, target)
        else:
            log.debug('(%s) Removing mode %r on %s', irc.name, real_mode, target)
            # We're removing a mode
            if real_mode[1] is None:
                # We're removing a mode that only takes arguments when setting.
                # Remove all mode entries that use the same letter as the one
                # we're unsetting.
                for oldmode in modelist.copy():
                    if oldmode[0] == real_mode[0]:
                        modelist.discard(oldmode)
            else:
                # Swap the - for a + and then remove it from the list.
                modelist.discard(real_mode)
    log.debug('(%s) Final modelist: %s', irc.name, modelist)
    if usermodes:
        irc.users[target].modes = modelist
    else:
        irc.channels[target].modes = modelist

def joinModes(modes):
    """Takes a list of (mode, arg) tuples in parseModes() format, and
    joins them into a string.

    See testJoinModes in tests/test_utils.py for some examples."""
    prefix = '+'  # Assume we're adding modes unless told otherwise
    modelist = ''
    args = []
    for modepair in modes:
        mode, arg = modepair
        assert len(mode) in (1, 2), "Incorrect length of a mode (received %r)" % mode
        try:
            # If the mode has a prefix, use that.
            curr_prefix, mode = mode
        except ValueError:
            # If not, the current prefix stays the same; move on to the next
            # modepair.
            pass
        else:
            # If the prefix of this mode isn't the same as the last one, add
            # the prefix to the modestring. This prevents '+nt-lk' from turning
            # into '+n+t-l-k' or '+ntlk'.
            if prefix != curr_prefix:
                modelist += curr_prefix
                prefix = curr_prefix
        modelist += mode
        if arg is not None:
            args.append(arg)
    if not modelist.startswith(('+', '-')):
        # Our starting mode didn't have a prefix with it. Assume '+'.
        modelist = '+' + modelist
    if args:
        # Add the args if there are any.
        modelist += ' %s' % ' '.join(args)
    return modelist

def _flip(mode):
    """Flips a mode character."""
    # Make it a list first, strings don't support item assignment
    mode = list(mode)
    if mode[0] == '-':  # Query is something like "-n"
        mode[0] = '+'  # Change it to "+n"
    elif mode[0] == '+':
        mode[0] = '-'
    else:  # No prefix given, assume +
        mode.insert(0, '-')
    return ''.join(mode)

def reverseModes(irc, target, modes, oldobj=None):
    """Reverses/Inverts the mode string or mode list given.

    Optionally, an oldobj argument can be given to look at an earlier state of
    a channel/user object, e.g. for checking the op status of a mode setter
    before their modes are processed and added to the channel state.

    This function allows both mode strings or mode lists. Example uses:
        "+mi-lk test => "-mi+lk test"
        "mi-k test => "-mi+k test"
        [('+m', None), ('+r', None), ('+l', '3'), ('-o', 'person')
         => {('-m', None), ('-r', None), ('-l', None), ('+o', 'person')})
        {('s', None), ('+o', 'whoever') => {('-s', None), ('-o', 'whoever')})
    """
    origtype = type(modes)
    # If the query is a string, we have to parse it first.
    if origtype == str:
        modes = parseModes(irc, target, modes.split(" "))
    # Get the current mode list first.
    if isChannel(target):
        c = oldobj or irc.channels[target]
        oldmodes = c.modes.copy()
        possible_modes = irc.cmodes.copy()
        # For channels, this also includes the list of prefix modes.
        possible_modes['*A'] += ''.join(irc.prefixmodes)
        for name, userlist in c.prefixmodes.items():
            try:
                oldmodes.update([(irc.cmodes[name], u) for u in userlist])
            except KeyError:
                continue
    else:
        oldmodes = irc.users[target].modes
        possible_modes = irc.umodes
    newmodes = []
    log.debug('(%s) reverseModes: old/current mode list for %s is: %s', irc.name,
               target, oldmodes)
    for char, arg in modes:
        # Mode types:
        # A = Mode that adds or removes a nick or address to a list. Always has a parameter.
        # B = Mode that changes a setting and always has a parameter.
        # C = Mode that changes a setting and only has a parameter when set.
        # D = Mode that changes a setting and never has a parameter.
        mchar = char[-1]
        if mchar in possible_modes['*B'] + possible_modes['*C']:
            # We need to find the current mode list, so we can reset arguments
            # for modes that have arguments. For example, setting +l 30 on a channel
            # that had +l 50 set should give "+l 30", not "-l".
            oldarg = [m for m in oldmodes if m[0] == mchar]
            if oldarg:  # Old mode argument for this mode existed, use that.
                oldarg = oldarg[0]
                mpair = ('+%s' % oldarg[0], oldarg[1])
            else:  # Not found, flip the mode then.
                # Mode takes no arguments when unsetting.
                if mchar in possible_modes['*C'] and char[0] != '-':
                    arg = None
                mpair = (_flip(char), arg)
        else:
            mpair = (_flip(char), arg)
        if char[0] != '-' and (mchar, arg) in oldmodes:
            # Mode is already set.
            log.debug("(%s) reverseModes: skipping reversing '%s %s' with %s since we're "
                      "setting a mode that's already set.", irc.name, char, arg, mpair)
            continue
        elif char[0] == '-' and (mchar, arg) not in oldmodes and mchar in possible_modes['*A']:
            # We're unsetting a prefixmode that was never set - don't set it in response!
            # Charybdis lacks verification for this server-side.
            log.debug("(%s) reverseModes: skipping reversing '%s %s' with %s since it "
                      "wasn't previously set.", irc.name, char, arg, mpair)
            continue
        newmodes.append(mpair)

    log.debug('(%s) reverseModes: new modes: %s', irc.name, newmodes)
    if origtype == str:
        # If the original query is a string, send it back as a string.
        return joinModes(newmodes)
    else:
        return set(newmodes)

def isOper(irc, uid, allowAuthed=True, allowOper=True):
    """
    Returns whether the given user has operator status on PyLink. This can be achieved
    by either identifying to PyLink as admin (if allowAuthed is True),
    or having user mode +o set (if allowOper is True). At least one of
    allowAuthed or allowOper must be True for this to give any meaningful
    results.
    """
    if uid in irc.users:
        if allowOper and ("o", None) in irc.users[uid].modes:
            return True
        elif allowAuthed and irc.users[uid].identified:
            return True
    return False

def checkAuthenticated(irc, uid, allowAuthed=True, allowOper=True):
    """
    Checks whether the given user has operator status on PyLink, raising
    NotAuthenticatedError and logging the access denial if not.
    """
    lastfunc = inspect.stack()[1][3]
    if not isOper(irc, uid, allowAuthed=allowAuthed, allowOper=allowOper):
        log.warning('(%s) Access denied for %s calling %r', irc.name,
                    getHostmask(irc, uid), lastfunc)
        raise NotAuthenticatedError("You are not authenticated!")
    return True

def isManipulatableClient(irc, uid):
    """
    Returns whether the given user is marked as an internal, manipulatable
    client. Usually, automatically spawned services clients should have this
    set True to prevent interactions with opers (like mode changes) from
    causing desyncs.
    """
    return irc.isInternalClient(uid) and irc.users[uid].manipulatable

def getHostmask(irc, user, realhost=False, ip=False):
    """
    Returns the hostmask of the given user, if present. If the realhost option
    is given, return the real host of the user instead of the displayed host.
    If the ip option is given, return the IP address of the user (this overrides
    realhost)."""
    userobj = irc.users.get(user)

    try:
        nick = userobj.nick
    except AttributeError:
        nick = '<unknown-nick>'

    try:
        ident = userobj.ident
    except AttributeError:
        ident = '<unknown-ident>'

    try:
        if ip:
            host = userobj.ip
        elif realhost:
            host = userobj.realhost
        else:
            host = userobj.host
    except AttributeError:
        host = '<unknown-host>'

    return '%s!%s@%s' % (nick, ident, host)

def loadModuleFromFolder(name, folder):
    """
    Imports and returns a module, if existing, from a specific folder.
    """
    fullpath = os.path.join(folder, '%s.py' % name)
    m = importlib.machinery.SourceFileLoader(name, fullpath).load_module()
    return m

def getProtocolModule(protoname):
    """
    Imports and returns the protocol module requested.
    """
    return loadModuleFromFolder(protoname, world.protocols_folder)

def getDatabaseName(dbname):
    """
    Returns a database filename with the given base DB name appropriate for the
    current PyLink instance.

    This returns '<dbname>.db' if the running config name is PyLink's default
    (config.yml), and '<dbname>-<config name>.db' for anything else. For example,
    if this is called from an instance running as './pylink testing.yml', it
    would return '<dbname>-testing.db'."""
    if conf.confname != 'pylink':
        dbname += '-%s' % conf.confname
    dbname += '.db'
    return dbname

def fullVersion(irc):
    """
    Returns a detailed version string including the PyLink daemon version,
    the protocol module in use, and the server hostname.
    """
    fullversion = 'PyLink-%s. %s :[protocol:%s]' % (world.version, irc.serverdata['hostname'], irc.protoname)
    return fullversion
