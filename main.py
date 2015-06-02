#!/usr/bin/python3

import imp
import os
import socket
import time
import sys

from conf import conf
import proto

if conf['login']['password'] == 'changeme':
     print("You have not set the login details correctly! Exiting...")
     sys.exit(2)

class Irc():
    def __init__(self):
        # Initialize some variables
        self.socket = socket.socket()
        self.connected = False
        self.users = {}
        self.channels = {}
        self.name = conf['server']['netname']
        self.conf = conf
        self.servers = {}

        self.serverdata = conf['server']
        ip = self.serverdata["ip"]
        port = self.serverdata["port"]
        self.sid = self.serverdata["sid"]
        print("Connecting to network %r on %s:%s" % (self.name, ip, port))

        self.socket = socket.socket()
        self.socket.connect((ip, port))
        proto.connect(self)
        self.loaded = []
        self.load_plugins()
        self.connected = True
        self.run()

    def run(self):
        buf = ""
        data = ""
        while self.connected:
            try:
                data = self.socket.recv(2048).decode("utf-8")
                buf += data
                if not data:
                    break
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    print("<- {}".format(line))
                    proto.handle_events(self, line)
            except socket.error as e:
                print('Received socket.error: %s, exiting.' % str(e))
                break
        sys.exit(1)

    def send(self, data):
        data = data.encode("utf-8") + b"\n"
        print("-> {}".format(data.decode("utf-8").strip("\n")))
        self.socket.send(data)

    def load_plugins(self):
        to_load = conf['plugins']
        plugins_folder = [os.path.join(os.getcwd(), 'plugins')]
        # Here, we override the module lookup and import the plugins
        # dynamically depending on which were configured.
        for plugin in to_load:
            moduleinfo = imp.find_module(plugin, plugins_folder)
            self.loaded.append(imp.load_source(plugin, moduleinfo[1]))
        print("loaded plugins: %s" % self.loaded)

if __name__ == '__main__':
    print('PyLink starting...')
    irc_obj = Irc()
