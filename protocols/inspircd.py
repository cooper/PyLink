import socket
import time
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import *
from copy import copy
import traceback
from classes import *

def _sendFromServer(irc, msg):
    irc.send(':%s %s' % (irc.sid, msg))

def _sendFromUser(irc, numeric, msg):
    irc.send(':%s %s' % (numeric, msg))

def spawnClient(irc, nick, ident, host, *args):
    uid = next_uid(irc.sid)
    ts = int(time.time())
    if not isNick(nick):
        raise ValueError('Invalid nickname %r.' % nick)
    _sendFromServer(irc, "UID {uid} {ts} {nick} {host} {host} {ident} 0.0.0.0 {ts} +o +"
                    " :PyLink Client".format(ts=ts, host=host,
                                             nick=nick, ident=ident, uid=uid))
    u = irc.users[uid] = IrcUser(nick, ts, uid, ident, host, *args)
    irc.servers[irc.sid].users.append(uid)
    return u

def joinClient(irc, client, channel):
    # One channel per line here!
    if not isInternalClient(irc, client):
        raise LookupError('No such PyLink PseudoClient exists.')
    if not isChannel(channel):
        raise ValueError('Invalid channel name %r.' % channel)
    _sendFromServer(irc, "FJOIN {channel} {ts} + :,{uid}".format(
            ts=int(time.time()), uid=client, channel=channel))

def partClient(irc, client, channel, reason=None):
    if not isInternalClient(irc, client):
        raise LookupError('No such PyLink PseudoClient exists.')
    msg = "PART %s" % channel
    if not isChannel(channel):
        raise ValueError('Invalid channel name %r.' % channel)
    if reason:
        msg += " :%s" % reason
    _sendFromUser(irc, client, msg)

def removeClient(irc, numeric):
    """<irc object> <client numeric>

    Removes a client from our internal databases, regardless
    of whether it's one of our pseudoclients or not."""
    for k, v in copy(irc.channels).items():
        irc.channels[k].users.discard(numeric)
        if not irc.channels[k].users:
            # Clear empty channels
            del irc.channels[k]
    sid = numeric[:3]
    print('Removing client %s from irc.users' % numeric)
    del irc.users[numeric]
    print('Removing client %s from irc.servers[%s]' % (numeric, sid))
    irc.servers[sid].users.remove(numeric)

def isInternalClient(irc, numeric):
    """<irc object> <client numeric>

    Returns whether <client numeric> is a PyLink PseudoClient.
    """
    return numeric in irc.servers[irc.sid].users

def quitClient(irc, numeric, reason):
    """<irc object> <client numeric>

    Quits a PyLink PseudoClient."""
    if isInternalClient(irc, numeric):
        _sendFromUser(irc, numeric, "QUIT :%s" % reason)
        removeClient(irc, numeric)
    else:
        raise LookupError("No such PyLink PseudoClient exists. If you're trying to remove "
                          "a user that's not a PyLink PseudoClient from "
                          "the internal state, use removeClient() instead.")

def kickClient(irc, numeric, channel, target, reason=None):
    """<irc object> <kicker client numeric>

    Sends a kick from a PyLink PseudoClient."""
    if not isInternalClient(irc, numeric):
        raise LookupError('No such PyLink PseudoClient exists.')
    if not reason:
        reason = 'No reason given'
    _sendFromUser(irc, numeric, 'KICK %s %s :%s' % (channel, target, reason))

def nickClient(irc, numeric, newnick):
    """<irc object> <client numeric> <new nickname>

    Changes the nick of a PyLink PseudoClient."""
    if not isInternalClient(irc, numeric):
        raise LookupError('No such PyLink PseudoClient exists.')
    if not isNick(newnick):
        raise ValueError('Invalid nickname %r.' % nick)
    _sendFromUser(irc, numeric, 'NICK %s %s' % (newnick, int(time.time())))
    irc.users[numeric].nick = newnick

def connect(irc):
    irc.start_ts = ts = int(time.time())
    host = irc.serverdata["hostname"]
    irc.servers[irc.sid] = IrcServer(None)

    f = irc.send
    f('CAPAB START 1203')
    # This is hard coded atm... We should fix it eventually...
    f('CAPAB CAPABILITIES :NICKMAX=32 HALFOP=0 CHANMAX=65 MAXMODES=20'
      ' IDENTMAX=12 MAXQUIT=255 PROTOCOL=1203')
    f('CAPAB END')
    # TODO: check recvpass here
    f('SERVER {host} {Pass} 0 {sid} :PyLink Service'.format(host=host,
      Pass=irc.serverdata["sendpass"], sid=irc.sid))
    f(':%s BURST %s' % (irc.sid, ts))
    # InspIRCd documentation:
    # :751 UID 751AAAAAA 1220196319 Brain brainwave.brainbox.cc
    # netadmin.chatspike.net brain 192.168.1.10 1220196324 +Siosw
    # +ACKNOQcdfgklnoqtx :Craig Edwards
    irc.pseudoclient = spawnClient(irc, 'PyLink', 'pylink', host)
    f(':%s ENDBURST' % (irc.sid))
    for chan in irc.serverdata['channels']:
        joinClient(irc, irc.pseudoclient.uid, chan)

# :7NU PING 7NU 0AL
def handle_ping(irc, servernumeric, command, args):
    if args[1] == irc.sid:
        _sendFromServer(irc, 'PONG %s' % args[1])

def handle_privmsg(irc, source, command, args):
    prefix = irc.conf['bot']['prefix']
    if args[0] == irc.pseudoclient.uid:
        cmd_args = args[1].split(' ')
        cmd = cmd_args[0].lower()
        try:
            cmd_args = cmd_args[1:]
        except IndexError:
            cmd_args = []
        try:
            func = bot_commands[cmd]
        except KeyError:
            msg(irc, source, 'Unknown command %r.' % cmd)
            return
        try:
            func(irc, source, cmd_args)
        except Exception as e:
            traceback.print_exc()
            msg(irc, source, 'Uncaught exception in command %r: %s: %s' % (cmd, type(e).__name__, str(e)))
            return

def handle_kill(irc, source, command, args):
    killed = args[0]
    removeClient(irc, killed)
    if killed == irc.pseudoclient.uid:
        irc.pseudoclient = spawnClient(irc, 'PyLink', 'pylink', irc.serverdata["hostname"])
        for chan in irc.serverdata['channels']:
            joinClient(irc, irc.pseudoclient.uid, chan)

def handle_kick(irc, source, command, args):
    # :70MAAAAAA KICK #endlessvoid 70MAAAAAA :some reason
    channel = args[0]
    kicked = args[1]
    irc.channels[channel].users.discard(kicked)
    if kicked == irc.pseudoclient.uid:
        joinClient(irc, irc.pseudoclient.uid, channel)

def handle_part(irc, source, command, args):
    channel = args[0]
    # We should only get PART commands for channels that exist, right??
    irc.channels[channel].users.discard(source)

def handle_error(irc, numeric, command, args):
    print('Received an ERROR, killing!')
    irc.connected = False
    sys.exit(1)

def handle_fjoin(irc, servernumeric, command, args):
    # :70M FJOIN #chat 1423790411 +AFPfjnt 6:5 7:5 9:5 :o,1SRAABIT4 v,1IOAAF53R <...>
    channel = args[0]
    if channel not in irc.channels.keys():
        irc.channels[channel] = IrcChannel()
    # InspIRCd sends each user's channel data in the form of 'modeprefix(es),UID'
    userlist = args[-1].split()
    for user in userlist:
        modeprefix, user = user.split(',', 1)
        for mode in modeprefix:
            # Note that a user can have more than one mode prefix (e.g. they have both +o and +v),
            # so they would be added to both lists.
            '''
            # left to right: m_ojoin, m_operprefix, owner (~/+q), admin (&/+a), and op (!/+o)
            if mode in 'Yyqao':
                irc.channels[channel].ops.append(user)
            if mode == 'h':
                irc.channels[channel].halfops.append(user)
            if mode == 'v':
                irc.channels[channel].voices.append(user)
            '''
        irc.channels[channel].users.add(user)

def handle_uid(irc, numeric, command, args):
    # :70M UID 70MAAAAAB 1429934638 GL 0::1 hidden-7j810p.9mdf.lrek.0000.0000.IP gl 0::1 1429934638 +Wioswx +ACGKNOQXacfgklnoqvx :realname
    uid, ts, nick, realhost, host, ident, ip = args[0:7]
    realname = args[-1]
    irc.users[uid] = IrcUser(nick, ts, uid, ident, host, realname, realhost, ip)
    irc.servers[numeric].users.append(uid)

def handle_quit(irc, numeric, command, args):
    # :1SRAAGB4T QUIT :Quit: quit message goes here
    removeClient(irc, numeric)

def handle_burst(irc, numeric, command, args):
    # :70M BURST 1433044587
    irc.servers[numeric] = IrcServer(None)

def handle_server(irc, numeric, command, args):
    # :70M SERVER millennium.overdrive.pw * 1 1ML :a relatively long period of time... (Fremont, California)
    servername = args[0]
    sid = args[3]
    irc.servers[sid] = IrcServer(numeric)

def handle_nick(irc, numeric, command, args):
    newnick = args[0]
    irc.users[numeric].nick = newnick

def handle_save(irc, numeric, command, args):
    # This is used to handle nick collisions. Here, the client Derp_ already exists,
    # so trying to change nick to it will cause a nick collision. On InspIRCd,
    # this will simply set the collided user's nick to its UID.

    # <- :70MAAAAAA PRIVMSG 0AL000001 :nickclient PyLink Derp_
    # -> :0AL000001 NICK Derp_ 1433728673
    # <- :70M SAVE 0AL000001 1433728673
    user = args[0]
    irc.users[user].nick = user

'''
def handle_fmode(irc, numeric, command, args):
    # <- :70MAAAAAA FMODE #chat 1433653462 +hhT 70MAAAAAA 70MAAAAAD
    # Oh god, how are we going to handle this?!
    channel = args[0]
    modestrings = args[3:]
'''

def handle_squit(irc, numeric, command, args):
    # :70M SQUIT 1ML :Server quit by GL!gl@0::1
    split_server = args[0]
    print('Netsplit on server %s' % split_server)
    # Prevent RuntimeError: dictionary changed size during iteration
    old_servers = copy(irc.servers)
    for sid, data in old_servers.items():
        if data.uplink == split_server:
            print('Server %s also hosts server %s, removing those users too...' % (split_server, sid))
            handle_squit(irc, sid, 'SQUIT', [sid, "PyLink: Automatically splitting leaf servers of %s" % sid])
    for user in copy(irc.servers[split_server].users):
        print('Removing client %s (%s)' % (user, irc.users[user].nick))
        removeClient(irc, user)
    del irc.servers[split_server]

def handle_idle(irc, numeric, command, args):
    """Handle the IDLE command, sent between servers in remote WHOIS queries."""
    # <- :70MAAAAAA IDLE 1MLAAAAIG
    # -> :1MLAAAAIG IDLE 70MAAAAAA 1433036797 319
    sourceuser = numeric
    targetuser = args[0]
    _sendFromUser(irc, targetuser, 'IDLE %s %s 0' % (sourceuser, irc.start_ts))

def handle_events(irc, data):
    # Each server message looks something like this:
    # :70M FJOIN #chat 1423790411 +AFPfjnt 6:5 7:5 9:5 :v,1SRAAESWE
    # :<sid> <command> <argument1> <argument2> ... :final multi word argument
    args = data.split()
    if args and args[0] == 'SERVER':
       # SERVER whatever.net abcdefgh 0 10X :something
       servername = args[1]
       if args[2] != irc.serverdata['recvpass']:
            # Check if recvpass is correct
            print('Error: recvpass from uplink server %s does not match configuration!' % servername)
            sys.exit(1)
       return
    try:
        real_args = []
        for arg in args:
            real_args.append(arg)
            # If the argument starts with ':' and ISN'T the first argument.
            # The first argument is used for denoting the source UID/SID.
            if arg.startswith(':') and args.index(arg) != 0:
                # : is used for multi-word arguments that last until the end
                # of the message. We can use list splicing here to turn them all
                # into one argument.
                index = args.index(arg)  # Get the array index of the multi-word arg
                # Set the last arg to a joined version of the remaining args
                arg = args[index:]
                arg = ' '.join(arg)[1:]
                # Cut the original argument list right before the multi-word arg,
                # and then append the multi-word arg.
                real_args = args[:index]
                real_args.append(arg)
                break
        real_args[0] = real_args[0].split(':', 1)[1]
        args = real_args

        numeric = args[0]
        command = args[1]
        args = args[2:]
    except IndexError:
        return

    # We will do wildcard event handling here. Unhandled events are just ignored, yay!
    try:
        func = globals()['handle_'+command.lower()]
        func(irc, numeric, command, args)
    except KeyError:  # unhandled event
        pass
