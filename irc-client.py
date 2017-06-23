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

TODO:
- Better management of /msg.
- Identify when another user is using /me and adjust accordingly.

"""

import argparse
import collections
import socket
import sys
import threading
import time
import tkinter as tk


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

    def _send(self, msg, cmd=0):
        """Send a message.

        Encode the message, add a line end and send it to the server.
        """
        if cmd == 1:
            self.show("*"+self._nick, msg)
            msg1 = 'PRIVMSG {} :'.format(self._channel)
            msg2 ='ACTION {}'.format(msg)
            self._socket.send(msg1.encode('utf-8', errors='replace') +b'\x01' + msg2.encode('utf-8', errors='replace') + b'\x01\r\n')
        if cmd == 2:
            self.show("*"+self._nick+"*", msg)
            msg2 ='PRIVMSG {}'.format(msg)
            self._socket.send(msg2.encode('utf-8', errors='replace') + b'\r\n')
        if cmd == 3:
            msg1 = 'PART {} :'.format(self._channel)
            self._socket.send(msg1.encode('utf-8', errors='replace') + b'\r\n')
            msg2 ='JOIN {}'.format(msg)
            self._socket.send(msg2.encode('utf-8', errors='replace') + b'\r\n')
            self._channel = msg
        if cmd == 4:
            msg1 = 'PART {} :'.format(self._channel)
            self._socket.send(msg1.encode('utf-8', errors='replace') + b'\r\n')
        else:
            self._socket.send(msg.encode('utf-8', errors='replace') + b'\r\n')

    def send_to_channel(self, msg, cmd=0):
        """Send a message to channel if it's non-empty."""
        if msg:
            if cmd > 0:
                self._send(msg,cmd)
            else:
                self.show(self._nick, msg)
                self._send('PRIVMSG {} :{}'.format(self._channel, msg),cmd)


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
                        #print(buf)
                        *lines, buf = buf.split(b'\r\n')
                        for line in lines:
                            self._check(line.decode('utf-8', errors='replace'))
                except socket.error as e:
                    self.show("*", "Error: {}".format(e))
                    self.show("*", "Trying again...")
                    self.connect()
        finally:
            if self._logfile is not None:
                self._logfile.close()

    def format_msg(self, sender, msg):

        """Return a printable form of the message."""
        return '[{}] {:>10} | {}'.format(time.strftime('%H:%M:%S'),
                                         sender, msg)


class ClientGUI(tk.Tk):
    """A GUI for ClientCore."""

    def __init__(self):
        """Initialize the GUI."""
        tk.Tk.__init__(self)
        w = 800 # width for the Tk root
        h = 600 # height for the Tk root
        ws = self.winfo_screenwidth() # width of the screen
        hs = self.winfo_screenheight() # height of the screen
        x = (ws/2) - (w/2)
        y = (hs/2) - (h/2)
        self.geometry('%dx%d+%d+%d' % (w, h, x, y))
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
        # to the window. The core is run in another thread and
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
    def _command_check(self, msg):

        commands = {
            '/me': 1,
            '/msg': 2,
            '/join': 3,
            '/part': 4
        }
        if msg.startswith('/'):
            cmdw = str(msg.split(' ', 1)[0])
            cmd = commands[cmdw]
            cmdw = cmdw + " "
            msg = msg.replace(cmdw,'')
            return msg, cmd
        else:
            return msg, 0

    def _on_enter(self, event):
        """Send a message to the channel."""
        entry = event.widget
        msg = entry.get()
        msg,cmd = self._command_check(msg)
        self._core.send_to_channel(msg,cmd)
        entry.delete(0, 'end')

    def _on_control_a(self, event):
        """Select all in the entry."""
        entry = event.widget
        entry.selection_range(0, 'end')
        return 'break'
    @staticmethod
    def ask(event=None):
        """Ask a string from the user and return it.

        This must be ran before making a ClientGUI instance or some
        other tk.Tk() window.
        """

        def on_submit(event=None):
            nonlocal results

            codex = {
                'Freenode': 'irc.freenode.net',
                'DALnet': 'irc.dal.net',
                'EFnet': 'irc.efnet.org',
                'Esper.net': 'irc.esper.net',
                'Mibbit': 'irc.mibbit.net',
                'Mozilla.org': 'irc.mozilla.org',
                'OFTC': 'irc.oftc.net',
                'QuakeNet': 'irc.quakenet.org',
                'Rizon': 'irc.rizon.net',
                'Snoonet': 'irc.snoonet.org',
                'Undernet': 'irc.undernet.org'
            }
            results=[]
            results.extend((codex[var.get()],entry_N.get(),entry_C.get(),entry_U.get(),entry_R.get(),entry_P.get()))
            for i in range(3,5):
                if results[i] =='':
                    results[i] = 'anon'
            if results[5]=='':
                results[5] = int(6667)
            root.destroy()
        def hidoption():
            if CheckVar1.get()==0:
                labelframe.pack_forget()
            else:
                labelframe.pack(fill="both", expand="yes")

        results = None
        root = tk.Tk()
        label = tk.Label(root, text='Login')
        label.config(font=("Courier", 44))
        label.pack()

        # Server
        label_S = tk.Label(root, text='Server:')
        label_S.pack()
        var = tk.StringVar(root)
        var.set("Freenode")  # default value
        entry_S = tk.OptionMenu(root, var, "Freenode", "DALnet", "EFnet",
                                "Esper.net","Mibbit", "Mozilla.org", "OFTC",
                                "QuakeNet", "Rizon", "Snoonet", "Undernet")
        entry_S.pack()

        # Nickname
        label_N = tk.Label(root, text="Nickname:")
        label_N.pack()
        entry_N = tk.Entry(root, font='TkFixedFont')
        entry_N.bind('<Return>', on_submit)
        entry_N.pack()

        # Channel
        label_C = tk.Label(root, text="Channel:")
        label_C.pack()
        entry_C = tk.Entry(root, font='TkFixedFont')
        entry_C.bind('<Return>', on_submit)
        entry_C.pack()

        button = tk.Button(root, text="OK", command=on_submit)
        button.pack()

        #optional fields:

        CheckVar1 = tk.IntVar()
        C1 = tk.Checkbutton(root, text = "Click Here for Optional Fields!", variable = CheckVar1, \
                 onvalue = 1, offvalue = 0, height=2, command=hidoption)
        C1.pack()

        #this is simply to widen the window... I looked for a bit,
        #but then I realized that I could be lazy

        laziness = tk.Label(root, text="                                                             ")
        laziness.pack()

        labelframe = tk.LabelFrame(root, text="Optional Fields:")



        # Username
        label_U = tk.Label(labelframe, text="Username:")
        label_U.pack()
        entry_U = tk.Entry(labelframe, font='TkFixedFont')
        entry_U.bind('<Return>', on_submit)
        entry_U.pack()

        # Realname
        label_R = tk.Label(labelframe, text="Realname:")
        label_R.pack()
        entry_R = tk.Entry(labelframe, font='TkFixedFont')
        entry_R.bind('<Return>', on_submit)
        entry_R.pack()

        # Port
        label_P = tk.Label(labelframe, text="Port:")
        label_P.pack()
        entry_P = tk.Entry(labelframe, font='TkFixedFont')
        entry_P.bind('<Return>', on_submit)
        entry_P.pack()


        root.mainloop()
        if (results[1] or results[2]) == '':
            sys.exit()
        else:
            return results


def main():
    """Run the program."""
    arg = ClientGUI.ask()
    core_args = {
        'server': arg[0],
        'nick': arg[1],
        'channel': arg[2],
        'username': arg[3],
        'realname': arg[4],
        'port': arg[5]
    }
    root = ClientGUI()
    root.title("IRC Client")
    root.create_core(**core_args)
    root.mainloop()


if __name__ == '__main__':
    main()
