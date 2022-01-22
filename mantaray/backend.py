# Originally based on code written by https://github.com/PurpleMyst/
# Most up to date irc docs I am aware of: https://modern.ircdocs.horse/
# TODO: modernize rest of the file to be as the docs say instead of ancient RFCs
from __future__ import annotations
import collections
import dataclasses
import queue
import ssl
import re
import socket
import threading
import traceback
from base64 import b64encode
from typing import Union, Sequence, Iterator

from . import config


_RPL_ENDOFMOTD = "376"
_RPL_NAMREPLY = "353"
_RPL_ENDOFNAMES = "366"
_RPL_LOGGEDIN = "900"
_RPL_TOPIC = "332"

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
class Kick:
    kicker: str
    channel: str
    kicked_nick: str
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
class TopicChanged:
    who_changed: str
    channel: str
    topic: str
@dataclasses.dataclass
class ServerMessage:
    sender: str | None  # I think this is a hostname. Not sure.
    command: str  # e.g. '482'
    args: list[str]  # e.g. ["#foo", "You're not a channel operator"]
    target_channel: str | None
    is_error: bool
@dataclasses.dataclass
class UnknownMessage:
    sender: str | None
    command: str
    args: list[str]
@dataclasses.dataclass
class ConnectivityMessage:
    message: str  # one line
    is_error: bool
@dataclasses.dataclass
class HostChanged:
    old: str
    new: str
# fmt: on

_IrcEvent = Union[
    SelfJoined,
    SelfChangedNick,
    SelfParted,
    SelfQuit,
    UserJoined,
    Kick,
    UserChangedNick,
    UserParted,
    UserQuit,
    SentPrivmsg,
    ReceivedPrivmsg,
    TopicChanged,
    ServerMessage,
    UnknownMessage,
    ConnectivityMessage,
    HostChanged,
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


def _recv_line(
    sock: socket.socket | ssl.SSLSocket, buffer: collections.deque[bytes]
) -> bytes:
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

        # Do not use .splitlines(keepends=True), it splits on \r which is bad (#115)
        buffer.extend(bytes(data)[:-1].split(b"\n"))

    return buffer.popleft()


class IrcCore:

    # each channel in autojoin will be joined after connecting
    def __init__(self, server_config: config.ServerConfig, *, verbose: bool):
        self._verbose = verbose
        self._apply_config(server_config)
        self._send_queue: queue.Queue[tuple[bytes, _IrcEvent | None]] = queue.Queue()
        self._recv_buffer: collections.deque[bytes] = collections.deque()

        # During connecting, sock is None and connected is False.
        # If connected is True, sock shouldn't be None.
        self._sock: socket.socket | ssl.SSLSocket | None = None
        self._connected = False

        self.event_queue: queue.Queue[_IrcEvent] = queue.Queue()
        self._threads: list[threading.Thread] = []

        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when joining channel
        # the replies are collected here before emitting a self_joined event
        # Topic can also be sent before joining
        # TODO: this in rfc?
        self._joining_in_progress: dict[str, _JoinInProgress] = {}

        self._quit_event = threading.Event()

    def _apply_config(self, server_config: config.ServerConfig) -> None:
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
        self._threads.append(
            threading.Thread(
                target=self._send_loop, name=f"send-loop-{hex(id(self))}-{self.nick}"
            )
        )
        self._threads.append(
            threading.Thread(
                target=self._connect_and_recv_loop,
                name=f"connect-and-recv-{hex(id(self))}-{self.nick}",
            )
        )
        for thread in self._threads:
            thread.start()

    def wait_for_threads_to_stop(self) -> None:
        for thread in self._threads:
            thread.join()

    def _connect_and_recv_loop(self) -> None:
        while not self._quit_event.is_set():
            try:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Connecting to {self.host} port {self.port}...", is_error=False
                    )
                )
                self._connect()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Cannot connect (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                self._quit_event.wait(timeout=RECONNECT_SECONDS)
                continue

            try:
                # If this succeeds, it stops when connection is closed
                self._recv_loop()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(f"Error while receiving: {e}", is_error=True)
                )
                # get ready to connect again
                self._disconnect()

    def _put_to_send_queue(
        self, message: str, *, done_event: _IrcEvent | None = None
    ) -> None:
        self._send_queue.put((message.encode("utf-8") + b"\r\n", done_event))

    def _handle_received_message(self, msg: _ReceivedAndParsedMessage) -> None:
        if msg.command == "PRIVMSG":
            assert msg.sender is not None
            recipient, text = msg.args
            self.event_queue.put(ReceivedPrivmsg(msg.sender, recipient, text))
            return

        if msg.command == "JOIN":
            assert msg.sender is not None
            [channel] = msg.args
            if msg.sender != self.nick:
                self.event_queue.put(UserJoined(msg.sender, channel))
            return

        if msg.command == "PART":
            assert msg.sender is not None
            channel = msg.args[0]
            reason = msg.args[1] if len(msg.args) >= 2 else None
            if msg.sender == self.nick:
                self.event_queue.put(SelfParted(channel))
            else:
                self.event_queue.put(UserParted(msg.sender, channel, reason))
            return

        if msg.command == "NICK":
            assert msg.sender is not None
            old = msg.sender
            [new] = msg.args
            if old == self.nick:
                self.nick = new
                self.event_queue.put(SelfChangedNick(old, new))
            else:
                self.event_queue.put(UserChangedNick(old, new))
            return

        if msg.command == "QUIT":
            assert msg.sender is not None
            reason = msg.args[0] if msg.args else None
            self.event_queue.put(UserQuit(msg.sender, reason or None))
            return

        if msg.command == "KICK":
            assert msg.sender is not None
            kicker = msg.sender
            channel, kicked_nick, reason = msg.args
            self.event_queue.put(Kick(kicker, channel, kicked_nick, reason or None))
            return

        if msg.sender_is_server:
            if msg.command == "CAP":
                subcommand = msg.args[1]

                if subcommand == "ACK":
                    acknowledged = set(msg.args[-1].split())

                    if "sasl" in acknowledged:
                        self._put_to_send_queue("AUTHENTICATE PLAIN")
                elif subcommand == "NAK":
                    rejected = set(msg.args[-1].split())
                    if "sasl" in rejected:
                        # TODO: this good?
                        raise ValueError("The server does not support SASL.")

            elif msg.command == "AUTHENTICATE":
                query = f"\0{self.username}\0{self.password}"
                b64_query = b64encode(query.encode("utf-8")).decode("utf-8")
                for i in range(0, len(b64_query), 400):
                    self._put_to_send_queue("AUTHENTICATE " + b64_query[i : i + 400])

            elif msg.command == _RPL_LOGGEDIN:
                self._put_to_send_queue("CAP END")

            elif msg.command == _RPL_NAMREPLY:
                # TODO: wtf are the first 2 args?
                # rfc1459 doesn't mention them, but freenode
                # gives 4-element msg.args lists
                channel, names = msg.args[-2:]

                # TODO: the prefixes have meanings
                # https://modern.ircdocs.horse/#channel-membership-prefixes
                self._joining_in_progress[channel.lower()].nicks.extend(
                    name.lstrip("~&@%+") for name in names.split()
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

            elif msg.command == _RPL_TOPIC:
                channel, topic = msg.args[1:]
                self._joining_in_progress[channel.lower()].topic = topic

            target_channel = None
            # TODO: remove startswith, its because https://github.com/ThePhilgrim/MantaTail/issues/83
            if msg.command == "482" and msg.args[0].startswith("#"):
                target_channel, *args = msg.args
            else:
                args = msg.args
            self.event_queue.put(
                ServerMessage(
                    msg.sender,
                    msg.command,
                    args,
                    target_channel,
                    # Errors seem to always be 4xx, 5xx or 7xx.
                    # Not all 6xx responses are errors, e.g. RPL_STARTTLS = 670
                    is_error=msg.command.startswith(("4", "5", "7")),
                )
            )
            return

        if msg.command == "TOPIC":
            channel, topic = msg.args
            assert msg.sender is not None
            self.event_queue.put(TopicChanged(msg.sender, channel, topic))
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
        assert self._connected
        sock = self._sock
        assert sock is not None

        while True:
            # Stop this thread if we disconnect or reconnect
            if self._sock is not sock or not self._connected:
                break

            try:
                line_bytes = _recv_line(sock, self._recv_buffer)
            except (OSError, ssl.SSLError) as e:
                # socket can be closed while receiving
                if self._sock is not sock or not self._connected:
                    break
                raise e

            if self._verbose:
                print("Recv:", line_bytes)

            line_bytes = line_bytes.rstrip(b"\n")
            # Allow \r\n line endings, or \r in middle of message
            if line_bytes.endswith(b"\r"):
                line_bytes = line_bytes[:-1]

            line = line_bytes.decode("utf-8", errors="replace")
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
            if sock is None or not self._connected:
                # ignore events silently when not connected
                continue

            if self._verbose:
                print("Send:", bytez)

            try:
                sock.sendall(bytez)
            except (OSError, ssl.SSLError):
                if self._connected:
                    traceback.print_exc()
                continue

            if done_event is not None:
                self.event_queue.put(done_event)
                if isinstance(done_event, SelfQuit):
                    self._quit_event.set()
                    self._disconnect()  # stop recv loop

    def _connect(self) -> None:
        assert self._sock is None and not self._connected

        try:
            if self.ssl:
                context = ssl.create_default_context()
                self._sock = context.wrap_socket(
                    socket.socket(), server_hostname=self.host
                )
            else:
                self._sock = socket.socket()
            self._sock.connect((self.host, self.port))
            self._connected = True
        except (OSError, ssl.SSLError) as e:
            if self._sock is not None:
                self._sock.close()
            self._sock = None
            raise e

        if self.password is not None:
            self._put_to_send_queue("CAP REQ sasl")

        # TODO: what if nick or user are in use? use alternatives?
        self._put_to_send_queue(f"NICK {self.nick}")
        self._put_to_send_queue(f"USER {self.username} 0 * :{self.realname}")

    def _disconnect(self) -> None:
        sock = self._sock
        self._sock = None
        self._connected = False

        if sock is not None:
            self.event_queue.put(ConnectivityMessage("Disconnected.", is_error=False))
            try:
                sock.shutdown(socket.SHUT_RDWR)  # stops sending/receiving immediately
            except OSError:
                # sometimes happens on macos, but .close() seems to stop sending/receiving on macos
                pass
            sock.close()

    def apply_config_and_reconnect(self, server_config: config.ServerConfig) -> None:
        assert self.nick == server_config["nick"]
        assert self.autojoin == server_config["joined_channels"]

        old_host = self.host
        self._apply_config(server_config)
        self._disconnect()  # will cause the main loop to reconnect

        if old_host != self.host:
            self.event_queue.put(HostChanged(old_host, self.host))

    def join_channel(self, channel: str) -> None:
        self._joining_in_progress[channel.lower()] = _JoinInProgress(None, [])
        self._put_to_send_queue(f"JOIN {channel}")

    def part_channel(self, channel: str, reason: str | None = None) -> None:
        if reason is None:
            self._put_to_send_queue(f"PART {channel}")
        else:
            # FIXME: the reason thing doesn't seem to work?
            self._put_to_send_queue(f"PART {channel} :{reason}")

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self._put_to_send_queue(
            f"PRIVMSG {nick_or_channel} :{text}",
            done_event=SentPrivmsg(nick_or_channel, text),
        )

    def kick(self, channel: str, kicked_nick: str, reason: str | None = None) -> None:
        if reason is None:
            self._put_to_send_queue(f"KICK {channel} {kicked_nick}")
        else:
            self._put_to_send_queue(f"KICK {channel} {kicked_nick} :{reason}")

    # emits SelfChangedNick event on success

    def change_nick(self, new_nick: str) -> None:
        self._put_to_send_queue(f"NICK {new_nick}")

    def change_topic(self, channel: str, new_topic: str) -> None:
        self._put_to_send_queue(f"TOPIC {channel} {new_topic}")

    def quit(self) -> None:
        sock = self._sock
        if sock is not None and self._connected:
            sock.settimeout(1)  # Do not freeze forever if sending is slow
            self._put_to_send_queue("QUIT", done_event=SelfQuit())
        else:
            self._disconnect()
            self._quit_event.set()
            self.event_queue.put(SelfQuit())
