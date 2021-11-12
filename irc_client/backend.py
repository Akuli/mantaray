# based on a thing that myst wrote for me
# thanks myst :)   https://github.com/PurpleMyst/
from __future__ import annotations
import collections
import dataclasses
import logging
import queue
import ssl
import re
import time
import socket
import threading
import traceback
from typing import Union

from . import config


log = logging.getLogger(__name__)


@dataclasses.dataclass
class _ReceivedAndParsedMessage:
    sender: str | None
    sender_is_server: bool
    command: str
    args: list[str]


# from rfc1459
_RPL_ENDOFMOTD = "376"
_RPL_NAMREPLY = "353"
_RPL_ENDOFNAMES = "366"

# https://tools.ietf.org/html/rfc2812#section-2.3.1
# unlike in the rfc, nicks are limited to 16 characters at least on freenode
# 15 is 16-1 where 1 is the first character
_special = re.escape(r"[]\`_^{|}")
NICK_REGEX = r"[A-Za-z%s][A-Za-z0-9-%s]{0,15}" % (_special, _special)

# https://tools.ietf.org/html/rfc2812#section-1.3
# at least freenode and spotchat disallow a channel named #
#    <siren.de.SpotChat.org> | toottootttt # Channel # is forbidden: Bad
#                              Channel Name, exposes client bugs
CHANNEL_REGEX = r"[&#+!][^ \x07,]{1,49}"


# fmt: off
@dataclasses.dataclass
class SelfJoined:
    channel: str
    nicklist: list[str]
@dataclasses.dataclass
class SelfChangedNick:
    old: str
    new: str
@dataclasses.dataclass
class SelfParted:
    channel: str
@dataclasses.dataclass
class SelfQuit:
    pass
@dataclasses.dataclass
class UserJoined:
    nick: str
    channel: str
@dataclasses.dataclass
class UserChangedNick:
    old: str
    new: str
@dataclasses.dataclass
class UserParted:
    nick: str
    channel: str
    reason: str | None
@dataclasses.dataclass
class UserQuit:
    nick: str
    reason: str | None
@dataclasses.dataclass
class SentPrivmsg:
    recipient: str  # channel or nick (PM)
    text: str
@dataclasses.dataclass
class ReceivedPrivmsg:
    sender: str  # channel or nick (PM)
    recipient: str  # channel or user's nick
    text: str
@dataclasses.dataclass
class ServerMessage:
    sender: str | None  # I think this is a hostname. Not sure.  TODO: can be None?
    # TODO: figure out meaning of command and args
    command: str
    args: list[str]
@dataclasses.dataclass
class UnknownMessage:
    sender: str | None  # TODO: can be None?
    command: str
    args: list[str]
@dataclasses.dataclass
class ConnectivityMessage:
    message: str  # one line
# fmt: on

_IrcEvent = Union[
    SelfJoined,
    SelfChangedNick,
    SelfParted,
    SelfQuit,
    UserJoined,
    UserChangedNick,
    UserParted,
    UserQuit,
    SentPrivmsg,
    ReceivedPrivmsg,
    ServerMessage,
    UnknownMessage,
    ConnectivityMessage,
]


def _recv_line(
    sock: socket.socket | ssl.SSLSocket, buffer: collections.deque[str]
) -> str:
    if not buffer:
        data = bytearray()

        # accepts both \r\n and \n
        while not data.endswith(b"\n"):
            assert sock is not None
            chunk = sock.recv(4096)
            if chunk:
                data += chunk
            else:
                raise OSError("Server closed the connection!")

        lines = data.decode("utf-8", errors="replace").splitlines()
        buffer.extend(lines)

    return buffer.popleft()


class IrcCore:

    # each channel in autojoin will be joined after connecting
    def __init__(self, server_config: config.ServerConfig):
        self.host = server_config["host"]
        self.port = server_config["port"]
        self._ssl = server_config["ssl"]
        self.nick = server_config["nick"]
        self.username = server_config["username"]
        self.realname = server_config["realname"]
        self.autojoin = server_config["joined_channels"].copy()

        self._sock: socket.socket | ssl.SSLSocket | None = None
        self._send_queue: queue.Queue[tuple[bytes, _IrcEvent | None]] = queue.Queue()
        self._recv_buffer: collections.deque[str] = collections.deque()

        self.event_queue: queue.Queue[_IrcEvent] = queue.Queue()
        self._threads: list[threading.Thread] = []

        # TODO: is automagic RPL_NAMREPLY in an rfc??
        # TODO: what do the rfc's say about huge NAMES replies with more nicks
        #       than maximum reply length?
        #
        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when a
        # client connects
        # the replies are collected here before emitting a self_joined event
        self._names_replys: dict[str, list[str]] = {}  # {channel: [nick1, nick2, ...]}

    def get_current_config(self) -> config.ServerConfig:
        return {
            "host": self.host,
            "port": self.port,
            "ssl": self._ssl,
            "nick": self.nick,
            "username": self.username,
            "realname": self.realname,
            "joined_channels": self.autojoin.copy(),
        }

    def start(self) -> None:
        assert not self._threads
        self._threads.append(threading.Thread(target=self._send_loop))
        self._threads.append(threading.Thread(target=self._connect_and_recv_loop))
        for thread in self.threads:
            thread.start()

    def wait_until_stopped(self) -> None:
        for thread in self._threads:
            thread.join()

    def _connect_and_recv_loop(self) -> None:
        while True:
            try:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Connecting to {self.host} port {self.port}..."
                    )
                )
                self._connect()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(f"Cannot connect (reconnecting in 10sec): {e}")
                )
                time.sleep(10)
                continue

            try:
                self._recv_loop()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(f"Error while receiving, reconnecting: {e}")
                )
                self._disconnect()
                continue

            # user quit
            self._disconnect()
            break

    def _send_soon(self, *parts: str, done_event: _IrcEvent | None = None) -> None:
        self._send_queue.put((" ".join(parts).encode("utf-8") + b"\r\n", done_event))

    def _handle_received_message(self, msg: _ReceivedAndParsedMessage) -> None:
        if msg.command == "PRIVMSG":
            assert msg.sender is not None
            recipient, text = msg.args
            self.event_queue.put(ReceivedPrivmsg(msg.sender, recipient, text))

        elif msg.command == "JOIN":
            assert msg.sender is not None
            [channel] = msg.args
            if msg.sender == self.nick:
                # channel will show up in ui once server finishes sending list of nicks
                self._names_replys[channel] = []
            else:
                self.event_queue.put(UserJoined(msg.sender, channel))

        elif msg.command == "PART":
            assert msg.sender is not None
            channel = msg.args[0]
            reason = msg.args[1] if len(msg.args) >= 2 else None
            if msg.sender == self.nick:
                self.event_queue.put(SelfParted(channel))
            else:
                self.event_queue.put(UserParted(msg.sender, channel, reason))

        elif msg.command == "NICK":
            assert msg.sender is not None
            old = msg.sender
            [new] = msg.args
            if old == self.nick:
                self.nick = new
                self.event_queue.put(SelfChangedNick(old, new))
            else:
                self.event_queue.put(UserChangedNick(old, new))

        elif msg.command == "QUIT":
            assert msg.sender is not None
            reason = msg.args[0] if msg.args else None
            self.event_queue.put(UserQuit(msg.sender, reason))

        elif msg.sender_is_server:
            if msg.command == _RPL_NAMREPLY:
                # TODO: wtf are the first 2 args?
                # rfc1459 doesn't mention them, but freenode
                # gives 4-element msg.args lists
                channel, names = msg.args[-2:]

                # TODO: don't ignore @ and + prefixes
                self._names_replys[channel].extend(
                    name.lstrip("@+") for name in names.split()
                )

            elif msg.command == _RPL_ENDOFNAMES:
                # joining a channel finished
                channel, human_readable_message = msg.args[-2:]
                nicks = self._names_replys.pop(channel)
                self.event_queue.put(SelfJoined(channel, nicks))

            else:
                # TODO: there must be a better way than relying on MOTD
                if msg.command == _RPL_ENDOFMOTD:
                    for channel in self.autojoin:
                        self.join_channel(channel)

                self.event_queue.put(ServerMessage(msg.sender, msg.command, msg.args))

        else:
            self.event_queue.put(UnknownMessage(msg.sender, msg.command, msg.args))

    @staticmethod
    def _split_line(line: str) -> _ReceivedAndParsedMessage:
        if not line.startswith(":"):
            sender_is_server = True  # TODO: when does this code run?
            sender = None
            command, *args = line.split(" ")
        else:
            sender, command, *args = line.split(" ")
            sender = sender[1:]
            if "!" in sender:
                # use user_and_host.split('@', 1) to separate user and host
                # TODO: include more stuff about the user than the nick?
                sender, user_and_host = sender.split("!", 1)
                sender_is_server = False
            else:
                # leave sender as is
                sender_is_server = True

        for n, arg in enumerate(args):
            if arg.startswith(":"):
                temp = args[:n]
                temp.append(" ".join(args[n:])[1:])
                args = temp
                break
        return _ReceivedAndParsedMessage(sender, sender_is_server, command, args)

    def _recv_loop(self) -> None:
        while True:
            sock = self._sock
            if sock is None:
                break

            try:
                line = _recv_line(sock, self._recv_buffer)
            except (OSError, ssl.SSLError) as e:
                # socket can be closed while receiving
                if self._sock is None:
                    break
                raise e

            if not line:
                # "Empty messages are silently ignored"
                # https://tools.ietf.org/html/rfc2812#section-2.3.1
                continue
            if line.startswith("PING"):
                self._send_soon(line.replace("PING", "PONG", 1))
                continue

            message = self._split_line(line)
            try:
                self._handle_received_message(message)
            except Exception:
                traceback.print_exc()

    def _send_loop(self) -> None:
        while True:
            bytez, done_event = self._send_queue.get()
            sock = self._sock
            if sock is None:
                # ignore events silently when not connected
                continue

            try:
                sock.sendall(bytez)
            except (OSError, ssl.SSLError):
                if self._sock is not None:
                    # should still be connected
                    traceback.print_exc()
                continue

            if done_event is not None:
                self.event_queue.put(done_event)
                if isinstance(done_event, SelfQuit):
                    self._disconnect()
                    break

    def _connect(self) -> None:
        assert self._sock is None

        try:
            if self._ssl:
                context = ssl.create_default_context()
                self._sock = context.wrap_socket(
                    socket.socket(), server_hostname=self.host
                )
            else:
                self._sock = socket.socket()

            self._sock.connect((self.host, self.port))

            # TODO: what if nick or user are in use? use alternatives?
            self._send_soon("NICK", self.nick)
            self._send_soon("USER", self.username, "0", "*", ":" + self.realname)
        except (OSError, ssl.SSLError) as e:
            if self._sock is not None:
                self._sock.close()
            self._sock = None
            raise e

    def _disconnect(self) -> None:
        # If at any time self._sock is set, it shouldn't be closed yet
        sock = self._sock
        self._sock = None
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)  # stops sending/receiving immediately
            sock.close()

    def join_channel(self, channel: str) -> None:
        self._send_soon("JOIN", channel)

    def part_channel(self, channel: str, reason: str | None = None) -> None:
        if reason is None:
            self._send_soon("PART", channel)
        else:
            # FIXME: the reason thing doesn't seem to work
            self._send_soon("PART", channel, ":" + reason)

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self._send_soon(
            "PRIVMSG",
            nick_or_channel,
            ":" + text,
            done_event=SentPrivmsg(nick_or_channel, text),
        )

    # emits SelfChangedNick event on success
    def change_nick(self, new_nick: str) -> None:
        self._send_soon("NICK", new_nick)

    # part all channels before calling this
    def quit(self) -> None:
        self._send_soon("QUIT", done_event=SelfQuit())
