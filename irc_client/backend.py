# based on a thing that myst wrote for me
# thanks myst :)   https://github.com/PurpleMyst/
from __future__ import annotations
import collections
import dataclasses
import logging
import queue
import ssl
import re
import socket
import threading
import traceback
from typing import Sequence, Union


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
# fmt: on

IrcEvent = Union[
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
]


class IrcCore:

    # each channel in autojoin will be joined after connecting
    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        username: str,
        realname: str,
        *,
        autojoin: Sequence[str] = ()
    ):
        self.host = host
        self.port = port
        self.nick = nick  # may be changed, see change_nick() below
        self.username = username
        self.realname = realname
        self._autojoin = autojoin
        self._running = True

        self._sock: ssl.SSLSocket | None = None  # see connect()
        self._send_queue: queue.Queue[tuple[bytes, IrcEvent | None]] = queue.Queue()
        self._recv_buffer: collections.deque[str] = collections.deque()

        self.event_queue: queue.Queue[IrcEvent] = queue.Queue()

        # TODO: is automagic RPL_NAMREPLY in an rfc??
        # TODO: what do the rfc's say about huge NAMES replies with more nicks
        #       than maximum reply length?
        #
        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when a
        # client connects
        # the replies are collected here before emitting a self_joined event
        self._names_replys: dict[str, list[str]] = {}  # {channel: [nick1, nick2, ...]}

    def _send_soon(self, *parts: str, done_event: IrcEvent | None = None) -> None:
        self._send_queue.put((" ".join(parts).encode("utf-8") + b"\r\n", done_event))

    def _recv_line(self) -> str:
        if not self._recv_buffer:
            data = bytearray()

            # this accepts both \r\n and \n because b'blah blah\r\n' ends
            # with b'\n'
            while not data.endswith(b"\n"):
                assert self._sock is not None
                chunk = self._sock.recv(4096)
                if chunk:
                    data += chunk
                else:
                    raise RuntimeError("Server closed the connection!")

            lines = data.decode("utf-8", errors="replace").splitlines()
            self._recv_buffer.extend(lines)

        return self._recv_buffer.popleft()

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
            if msg.sender == self.nick:
                self.event_queue.put(SelfQuit())
                self._running = False
            else:
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
                    for channel in self._autojoin:
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
        try:
            # TODO: does quitting work in all cases, even during connecting?
            while self._running:
                line = self._recv_line()
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
        finally:
            assert self._sock is not None
            self._sock.close()
            self._sock = None

    def _send_loop(self) -> None:
        while self._running:
            bytez, done_event = self._send_queue.get()
            assert self._sock is not None
            self._sock.sendall(bytez)
            if done_event is not None:
                self.event_queue.put(done_event)

    # if an exception occurs while connecting, it's raised right away
    # run this in a thread if you don't want blocking
    # this starts the main loop
    # if this fails, you can call this again to try again
    def connect(self) -> None:
        print("connecting")
        assert self._sock is None

        try:
            context = ssl.create_default_context()
            self._sock = context.wrap_socket(socket.socket(), server_hostname=self.host)
            self._sock.connect((self.host, self.port))

            # TODO: what if nick or user are in use? use alternatives?
            self._send_soon("NICK", self.nick)
            self._send_soon("USER", self.username, "0", "*", ":" + self.realname)
            print("connected")
        except OSError as e:
            print("connect failed", e)
            # _recv_loop() knows how to close the
            # socket, but we didn't get to actually run it
            if self._sock is not None:
                self._sock.close()
            self._sock = None
            raise e

        # it didn't fail
        threading.Thread(target=self._recv_loop).start()
        threading.Thread(target=self._send_loop).start()

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
        self._send_soon("QUIT")
        self._running = False
