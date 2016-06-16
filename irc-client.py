#!/usr/bin/env python3

# Copyright (c) 2016 Akuli

# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:

# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Simple IRC client written in Tkinter.

Mostly based on this:
  http://www.ohjelmointiputka.net/koodivinkit/24802-python-ircbot

TODO: Add /me, /msg, /join and /part, but not support for multiple
      channels. This is a minimal client.
"""

import argparse
import collections
import threading
import time
import tkinter as tk
import socket
import sys


class ClientCore:
    """The core of the IRC client."""

    def __init__(self, server, nick, channel, username=None,
                 realname=None, port=6667, outfunc=None, logfile=None):
        """Initialize the client.

        The username and realname keyword arguments default to nick, and
        outfunc defaults to print.
        """
        self._server = server
        self._nick = nick
        self._channel = channel
        self._username = nick if username is None else username
        self._realname = nick if realname is None else realname
        self._port = port
        self._outfunc = print if outfunc is None else outfunc
        self._logfile = None if logfile is None else open(logfile, 'a')

    def _send(self, msg):
        """Send a message.

        Encode the message, add a line end and send it to the server.
        """
        self._socket.send(msg.encode('utf-8', errors='replace') + b'\r\n')

    def send_to_channel(self, msg):
        """Send a message to channel if it's non-empty."""
        if msg:
            self.show(self._nick, msg)
            self._send('PRIVMSG {} :{}'.format(self._channel, msg))

    def connect(self):
        """Connect the client.

        This will connect the socket, set nickname, username and
        realname and join a channel.
        """
        self._socket = socket.socket()
        self._socket.connect((self._server, self._port))
        self._send('NICK {}'.format(self._nick))
        self._send('USER {} a a :{}'.format(self._username, self._realname))
        self._send('JOIN {}'.format(self._channel))

    def _check(self, line):
        try:
            beginning, msg = line.lstrip(':').split(':', 1)
            sender, msg_type, target = beginning.split()
        except ValueError:
            # Not a message.
            if line.startswith('PING '):
                self._send('PONG :abc')
        else:
            self.show(sender.split('!')[0], msg)

    def show(self, sender, msg):
        """Show a message to the user.

        Call self._outfunc and write to the log.
        """
        self._outfunc(sender, msg)
        if self._logfile is not None:
            print(self.format_msg(sender, msg), file=self._logfile, flush=True)

    def outputloop(self):
        """Receive data from the channel and write it to outstream."""
        try:
            while True:
                try:
                    buf = b''
                    while True:
                        buf += self._socket.recv(4096)
                        *lines, buf = buf.split(b'\r\n')
                        for line in lines:
                            self._check(line.decode('utf-8', errors='replace'))
                except Exception as e:
                    self.show("<info>", "Error: {}".format(e))
                    self.show("<info>", "Trying again...")
                    self.connect()
        finally:
            if self._logfile is not None:
                self._logfile.close()

    def format_msg(self, sender, msg):
        """Return a printable form of the message."""
        return '[{}] {:<20} | {}'.format(time.strftime('%H:%M:%S'),
                                         sender, msg)


class ClientGUI(tk.Tk):
    """A GUI for ClientCore."""

    def __init__(self):
        """Initialize the GUI."""
        tk.Tk.__init__(self)

        textarea = tk.Frame(self)
        self._text = tk.Text(textarea, state='disabled')
        self._text.pack(side='left', fill='both', expand=True)
        scrollbar = tk.Scrollbar(textarea, command=self._text.yview)
        scrollbar.pack(side='right', fill='y')
        self._text['yscrollcommand'] = scrollbar.set
        textarea.pack(fill='both', expand=True)

        entry = tk.Entry(self, font='TkFixedFont')
        entry.pack(fill='x')
        entry.bind('<Return>', self._on_enter)
        entry.bind('<Control-A>', self._on_control_a)
        entry.bind('<Control-a>', self._on_control_a)

        self.bind_all('<Control-Q>', lambda e: sys.exit())
        self.bind_all('<Control-q>', lambda e: sys.exit())

        # New messages are stored here instead of adding them directly
        # to the window. The core is ran in another thread and it
        # doesn't access tkinter this way.
        self._msg_queue = collections.deque()

    def create_core(self, **core_args):
        """Create and connect a core.

        This can be called only once.
        """
        if hasattr(self, '_core'):
            raise RuntimeError("cannot create core twice")
        self._core = ClientCore(outfunc=self._queue_msg, **core_args)
        self._core.connect()
        threading.Thread(target=self._core.outputloop, daemon=True).start()
        self._clear_queue_loop()

    def _queue_msg(self, sender, msg):
        """Add a message to the message queue."""
        self._msg_queue.append(self._core.format_msg(sender, msg))

    def _clear_queue_loop(self):
        """Show each message in the queue."""
        if self._msg_queue:
            self._text['state'] = 'normal'
            while True:
                try:
                    self._text.insert('end', self._msg_queue.popleft())
                    self._text.insert('end', '\n')
                    self._text.see('end')
                except IndexError:
                    break
            self._text['state'] = 'disabled'
        self.after(100, self._clear_queue_loop)

    def _on_enter(self, event):
        """Send a message to the channel."""
        entry = event.widget
        msg = entry.get()
        self._core.send_to_channel(msg)
        entry.delete(0, 'end')

    def _on_control_a(self, event):
        """Select all in the entry."""
        entry = event.widget
        entry.selection_range(0, 'end')
        return 'break'

    @staticmethod
    def ask(prompt):
        """Ask a string from the user and return it.

        This must be ran before making a ClientGUI instance or some
        other tk.Tk() window.
        """
        def on_ok(event=None):
            nonlocal result
            result = entry.get()
            root.destroy()

        result = None
        root = tk.Tk()
        label = tk.Label(root, text=prompt)
        label.place(relx=0.5, rely=0.1, anchor='center')
        entry = tk.Entry(root, font='TkFixedFont')
        entry.bind('<Return>', on_ok)
        entry.place(relx=0.5, rely=0.4, anchor='center')
        button = tk.Button(root, text="OK", command=on_ok)
        button.place(relx=0.5, rely=0.8, anchor='center')

        entry.focus_set()
        root.geometry('300x150')
        root.mainloop()
        if result is None:
            sys.exit()
        return result


def main():
    """Run the program."""
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--server', help="the IRC server to connect to, "
                                               "for example irc.freenode.net")
    parser.add_argument('-n', '--nick', help="your nickname")
    parser.add_argument('-c', '--channel', help="the channel you want to join")
    parser.add_argument('-m', '--mode', default='gui',
                        help="gui or cli, defaults to gui")
    parser.add_argument('-u', '--username',
                        help="your username, defaults to nick")
    parser.add_argument('-r', '--realname',
                        help="your realname, defaults to nick")
    parser.add_argument('-p', '--port', type=int, default=6667,
                        help="the port to use, defaults to 6667")
    parser.add_argument('-l', '--logfile', help="a file used for logging")

    args = parser.parse_args()
    if args.mode == 'gui':
        ask = ClientGUI.ask
    elif args.mode == 'cli':
        ask = input
    else:
        parser.error("invalid mode: {}".format(args.mode))

    core_args = {
        'server': args.server or ask("Server: "),
        'nick': args.nick or ask("Your nickname: "),
        'channel': args.channel or ask("Channel: "),
        'username': args.username or args.nick,
        'realname': args.realname or args.nick,
        'port': args.port,
        'logfile': args.logfile,
    }

    if args.mode == 'gui':
        root = ClientGUI()
        root.title("IRC Client")
        root.create_core(**core_args)
        root.mainloop()
    else:
        core = ClientCore(**core_args)
        core.connect()
        threading.Thread(target=core.outputloop, daemon=True).start()
        while True:
            try:
                msg = input("{}> ".format(core_args['nick']))
            except (KeyboardInterrupt, EOFError):
                break
            core.send_to_channel(msg)

    # This function is not meant to be ran twice.
    sys.exit()


if __name__ == '__main__':
    main()
