from __future__ import annotations
import socket
import collections
import dataclasses
import queue
import ssl
import re
import threading
import traceback
from typing import Union, Sequence, Iterator


# from rfc1459
_RPL_ENDOFMOTD = "376"
_RPL_NAMREPLY = "353"
_RPL_ENDOFNAMES = "366"
_RPL_LOGGEDIN = "900"

# https://tools.ietf.org/html/rfc2812#section-2.3.1
# unlike in the rfc, nicks are limited to 16 characters at least on freenode
# 15 is 16-1 where 1 is the first character
_special = re.escape(r"[]\`_^{|}")
NICK_REGEX = r"[A-Za-z%s][A-Za-z0-9-%s]{0,15}" % (_special, _special)

# https://tools.ietf.org/html/rfc2812#section-1.3
#
# channel names don't need to start with #
#
# at least freenode and spotchat disallow a channel named #
#    <siren.de.SpotChat.org> | toottootttt # Channel # is forbidden: Bad
#                              Channel Name, exposes client bugs
CHANNEL_REGEX = r"[&#+!][^ \x07,]{1,49}"


def find_nicks(
    text: str, self_nick: str, all_nicks: Sequence[str]
) -> Iterator[tuple[str, str | None]]:
    lowercase_nicks = {n.lower() for n in all_nicks}
    assert self_nick.lower() in lowercase_nicks

    previous_end = 0
    for match in re.finditer(NICK_REGEX, text):
        if match.group(0).lower() in lowercase_nicks:
            yield (text[previous_end : match.start()], None)
            if match.group(0).lower() == self_nick.lower():
                yield (match.group(0), "self-nick")
            else:
                yield (match.group(0), "other-nick")
            previous_end = match.end()
    yield (text[previous_end:], None)


# fmt: off
@dataclasses.dataclass
class SelfJoined:
    channel: str
    topic: str
    nicklist: list[str]
@dataclasses.dataclass
class ServerMessage:
    sender: str | None  # I think this is a hostname. Not sure.
    # TODO: figure out meaning of command and args
    command: str
    args: list[str]
@dataclasses.dataclass
class UnknownMessage:
    sender: str | None
    command: str
    args: list[str]
@dataclasses.dataclass
class ConnectivityMessage:
    message: str  # one line
    is_error: bool
# fmt: on

_IrcEvent = Union[
    SelfJoined,
    ServerMessage,
    UnknownMessage,
    ConnectivityMessage,
]

RECONNECT_SECONDS = 10


@dataclasses.dataclass
class _ReceivedAndParsedMessage:
    sender: str | None
    sender_is_server: bool
    command: str
    args: list[str]


@dataclasses.dataclass
class _JoinInProgress:
    topic: str | None
    nicks: list[str]


class IrcCore:

    # each channel in autojoin will be joined after connecting
    def __init__(self, server_config):
        self._apply_config(server_config)
        self._sock = None
        self._send_queue: queue.Queue[tuple[bytes, _IrcEvent | None]] = queue.Queue()
        self._recv_buffer: collections.deque[str] = collections.deque()

        self.event_queue: queue.Queue[_IrcEvent] = queue.Queue()
        self._threads: list[threading.Thread] = []

        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when joining channel
        # the replies are collected here before emitting a self_joined event
        # Topic can also be sent before joining
        # TODO: this in rfc?
        self._joining_in_progress: dict[str, _JoinInProgress] = {}

        self._quit_event = threading.Event()

    def _apply_config(self, server_config) -> None:
        self.host = server_config["host"]
        self.port = server_config["port"]
        self.ssl = server_config["ssl"]
        self.nick = server_config["nick"]
        self.username = server_config["username"]
        self.realname = server_config["realname"]
        self.password = server_config["password"]
        self.autojoin = server_config["joined_channels"].copy()

    def start_threads(self) -> None:
        assert not self._threads
        self._threads.append(threading.Thread(target=self._send_loop))
        self._threads.append(threading.Thread(target=self._connect_and_recv_loop))
        for thread in self._threads:
            thread.start()

    def wait_for_threads_to_stop(self) -> None:
        for thread in self._threads:
            thread.join()

    def _connect_and_recv_loop(self) -> None:
        self.event_queue.put(
            ConnectivityMessage(
                f"Connecting to {self.host} port {self.port}...", is_error=False
            )
        )
        self._connect()

    def _put_to_send_queue(
        self, message: str, *, done_event: _IrcEvent | None = None
    ) -> None:
        self._send_queue.put((message.encode("utf-8") + b"\r\n", done_event))

    def _handle_received_message(self, msg: _ReceivedAndParsedMessage) -> None:
        if msg.command == "JOIN":
            assert msg.sender is not None
            [channel] = msg.args
            return

        if msg.sender_is_server:
            if msg.command == _RPL_NAMREPLY:
                # TODO: wtf are the first 2 args?
                # rfc1459 doesn't mention them, but freenode
                # gives 4-element msg.args lists
                channel, names = msg.args[-2:]

                # TODO: don't ignore @ and + prefixes
                self._joining_in_progress[channel.lower()].nicks.extend(
                    name.lstrip("@+") for name in names.split()
                )
                return  # don't spam server view with nicks

            elif msg.command == _RPL_ENDOFNAMES:
                # joining a channel finished
                channel, human_readable_message = msg.args[-2:]
                join = self._joining_in_progress.pop(channel.lower())
                # join.topic is None, when creating channel on libera
                self.event_queue.put(
                    SelfJoined(channel, join.topic or "(no topic)", join.nicks)
                )

            elif msg.command == _RPL_ENDOFMOTD:
                # TODO: relying on MOTD good?
                for channel in self.autojoin:
                    self.join_channel(channel)

            elif msg.command == "TOPIC":
                channel, topic = msg.args
                self._joining_in_progress[channel.lower()].topic = topic

            self.event_queue.put(ServerMessage(msg.sender, msg.command, msg.args))
            return

        if msg.command == "TOPIC":
            channel, topic = msg.args
            assert msg.sender is not None
            return

        self.event_queue.put(UnknownMessage(msg.sender, msg.command, msg.args))

    @staticmethod
    def _parse_received_message(line: str) -> _ReceivedAndParsedMessage:
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
                self._put_to_send_queue(line.replace("PING", "PONG", 1))
                continue

            message = self._parse_received_message(line)
            try:
                self._handle_received_message(message)
            except Exception:
                traceback.print_exc()

    def _send_loop(self) -> None:
        # Ideally it would be posible to wait until quit_event is set OR queue has something
        while not self._quit_event.is_set():
            try:
                bytez, done_event = self._send_queue.get(timeout=0.1)
            except queue.Empty:
                continue

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

    def _connect(self) -> None:
        assert self._sock is None
        self._sock, _ = socket.socketpair()

        if self.password is not None:
            self._put_to_send_queue("CAP REQ sasl")

        # TODO: what if nick or user are in use? use alternatives?
        self._put_to_send_queue(f"NICK {self.nick}")
        self._put_to_send_queue(f"USER {self.username} 0 * :{self.realname}")

    def join_channel(self, channel: str) -> None:
        self._joining_in_progress[channel.lower()] = _JoinInProgress(None, [])
        self._put_to_send_queue(f"JOIN {channel}")
