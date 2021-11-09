# based on a thing that myst wrote for me
# thanks myst :)   https://github.com/PurpleMyst/
from __future__ import annotations
import collections
import dataclasses
import enum
import logging
import queue
import ssl
import re
import socket
import threading
import sys
from typing import Sequence, Any, Union, TYPE_CHECKING, Tuple, List, Optional, NewType

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal


log = logging.getLogger(__name__)


@dataclasses.dataclass
class _Message:
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
    sender: str  # I think this is a hostname. Not sure.
    # TODO: figure out meaning of command and args
    command: str
    args: list[str]
@dataclasses.dataclass
class UnknownMessage:
    sender: str
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

_IrcInternalEvent = enum.Enum(
    "_IrcInternalEvent",
    [
        "got_message",
        "should_join",
        "should_part",
        "should_quit",
        "should_send_privmsg",
        "should_change_nick",
    ],
)


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
        self._linebuffer: collections.deque[str] = collections.deque()

        self._internal_queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.event_queue: queue.Queue[IrcEvent] = queue.Queue()

        # TODO: is automagic RPL_NAMREPLY in an rfc??
        # TODO: what do the rfc's say about huge NAMES replies with more nicks
        #       than maximum reply length?
        #
        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when a
        # client connects
        # the replies are collected here before emitting a self_joined event
        self._names_replys: dict[str, list[str]] = {}  # {channel: [nick1, nick2, ...]}

    def _send(self, *parts: str) -> None:
        data = " ".join(parts).encode("utf-8") + b"\r\n"
        assert self._sock is not None
        self._sock.sendall(data)

    def _recv_line(self) -> str:
        if not self._linebuffer:
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
            self._linebuffer.extend(lines)

        return self._linebuffer.popleft()

    def _add_messages_to_internal_queue(self) -> None:
        # We need to have this function because it would be very complicated to
        # wait on two different queues, one for requests to send stuff and one
        # received messages.
        try:
            while self._running:
                line = self._recv_line()
                if not line:
                    # "Empty messages are silently ignored"
                    # https://tools.ietf.org/html/rfc2812#section-2.3.1
                    continue
                if line.startswith("PING"):
                    self._send(line.replace("PING", "PONG", 1))
                    continue
                self._internal_queue.put(
                    (_IrcInternalEvent.got_message, self._split_line(line))
                )
        finally:
            assert self._sock is not None
            self._sock.close()
            self._sock = None

    @staticmethod
    def _split_line(line: str) -> _Message:
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
        return _Message(sender, sender_is_server, command, args)

    def _mainloop(self) -> None:
        while self._running:
            event, *args = self._internal_queue.get()
            log.debug("got an internal %r event", event)

            if event == _IrcInternalEvent.got_message:
                msg: _Message
                [msg] = args
                if msg.command == "PRIVMSG":
                    recipient, text = msg.args
                    self.event_queue.put(
                        ReceivedPrivmsg(msg.sender, recipient, text))

                elif msg.command == "JOIN":
                    [channel] = msg.args
                    if msg.sender == self.nick:
                        # there are plenty of comments in other
                        # _names_replys code
                        self._names_replys[channel] = []
                    else:
                        self.event_queue.put(UserJoined(msg.sender, channel))

                elif msg.command == "PART":
                    channel = msg.args[0]
                    reason = msg.args[1] if len(msg.args) >= 2 else None
                    if msg.sender == self.nick:
                        self.event_queue.put(SelfParted(channel))
                    else:
                        self.event_queue.put(UserParted(msg.sender, channel, reason))

                elif msg.command == "NICK":
                    old = msg.sender
                    [new] = msg.args
                    if old == self.nick:
                        self.nick = new
                        self.event_queue.put(SelfChangedNick(old, new))
                    else:
                        self.event_queue.put(UserChangedNick(old, new))

                elif msg.command == "QUIT":
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

            elif event == _IrcInternalEvent.should_join:
                [channel] = args
                self._send("JOIN", channel)

            elif event == _IrcInternalEvent.should_part:
                channel, reason = args
                if reason is None:
                    self._send("PART", channel)
                else:
                    # FIXME: the reason thing doesn't seem to work
                    self._send("PART", channel, ":" + reason)

            elif event == _IrcInternalEvent.should_quit:
                assert not args
                self._send("QUIT")
                self._running = False

            elif event == _IrcInternalEvent.should_send_privmsg:
                recipient, text = args
                self._send("PRIVMSG", recipient, ":" + text)
                self.event_queue.put(SentPrivmsg(recipient, text))

            elif event == _IrcInternalEvent.should_change_nick:
                [new_nick] = args
                self._send("NICK", new_nick)

            else:
                raise RuntimeError("Unrecognized internal event!")

            self._internal_queue.task_done()

        self.event_queue.put(SelfQuit())

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
            self._send("NICK", self.nick)
            self._send("USER", self.username, "0", "*", ":" + self.realname)
            print("connected")
        except OSError as e:
            print("connect failed", e)
            # _add_messages_to_internal_queue() knows how to close the
            # socket, but we didn't get to actually run it
            if self._sock is not None:
                self._sock.close()
            self._sock = None
            raise e

        # it didn't fail
        threading.Thread(target=self._add_messages_to_internal_queue).start()
        threading.Thread(target=self._mainloop).start()

    def join_channel(self, channel: str) -> None:
        self._internal_queue.put((_IrcInternalEvent.should_join, channel))

    def part_channel(self, channel: str, reason: str | None = None) -> None:
        self._internal_queue.put((_IrcInternalEvent.should_part, channel, reason))

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self._internal_queue.put(
            (_IrcInternalEvent.should_send_privmsg, nick_or_channel, text)
        )

    # this doesn't change self.nick right away, but .nick is updated
    # when the nick name has actually changed
    # emits a self_changed_nick event on success
    def change_nick(self, new_nick: str) -> None:
        self._internal_queue.put((_IrcInternalEvent.should_change_nick, new_nick))

    # part all channels before calling this
    def quit(self) -> None:
        self._internal_queue.put((_IrcInternalEvent.should_quit,))
