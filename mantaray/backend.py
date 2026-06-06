"""Handles socket connections, sending and receiving.

This file does not depend on the GUI in any way. For example, you could make an
IRC bot using this file, without having to modify it at all.
"""

# Originally based on code written by https://github.com/PurpleMyst/
# Most up to date irc docs I am aware of: https://modern.ircdocs.horse/
# TODO: modernize rest of the file to be as the docs say instead of ancient RFCs
from __future__ import annotations

import collections
import dataclasses
import re
import socket
import ssl
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Iterator, Union, cast, Any

import certifi

from . import config

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
    text: str, self_nick: str, all_nicks: list[str]
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


RECONNECT_SECONDS = 5

IDLE_BEFORE_PING_SECONDS = 60
PING_TIMEOUT_SECONDS = 30


@dataclasses.dataclass
class MessageFromServer:
    server: str
    command: str
    args: list[str]
    is_error: bool


@dataclasses.dataclass
class MessageFromUser:
    sender_nick: str
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


@dataclasses.dataclass  # TODO: split into channel and DM variants
class SentPrivmsg:
    nick_or_channel: str
    text: str
    history_id: int | None


@dataclasses.dataclass
class ReceivePM:
    sender_nick: str
    text: str


@dataclasses.dataclass
class ChannelMessage:
    channel: str
    sender_nick: str
    text: str


@dataclasses.dataclass
class IJoinedChannel:
    channel: str
    nicks: list[str]  # All users that are currently on the channel
    topic: str | None


@dataclasses.dataclass
class Away:
    nick: str
    reason: str | None  # None means unknown reason


@dataclasses.dataclass
class Back:  # no longer away
    nick: str


IrcEvent = Union[
    MessageFromServer,
    MessageFromUser,
    ConnectivityMessage,
    HostChanged,
    SentPrivmsg,
    ReceivePM,
    ChannelMessage,
    IJoinedChannel,
    Away,
    Back,
]
_Socket = Union[socket.socket, ssl.SSLSocket]


@dataclasses.dataclass
class _Quit:
    pass


def _create_connection(host: str, port: int, use_ssl: bool) -> _Socket:
    if use_ssl:
        context = ssl.create_default_context(cafile=certifi.where())
        sock = context.wrap_socket(socket.socket(), server_hostname=host)
    else:
        sock = socket.socket()

    try:
        sock.settimeout(15)
        sock.connect((host, port))
    except (OSError, ssl.SSLError) as e:
        sock.close()
        raise e

    return sock


def _close_socket_when_future_done(future: Future[_Socket]) -> None:
    try:
        sock = future.result()
    except Exception:
        pass
    else:
        sock.close()


def _flush_and_close_socket(sock: _Socket) -> None:
    sock.settimeout(1)
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


# Slightly more type safe than just cast().
# TODO: avoid this entirely?
def _cast_future(future: Future[Any]) -> Future[_Socket]:
    return cast(Future[_Socket], future)


class _JoinInProgress:
    def __init__(self) -> None:
        self.topic: str | None = None
        self.nicks: list[str] = []


# These can't be global variables because Python's match statement works weirdly.
# It treats RPL_NAMREPLY as a local variable and _Codes.RPL_NAMREPLY as a constant.
class _Codes:
    RPL_NAMREPLY = "353"
    RPL_TOPIC = "332"
    RPL_ENDOFWHO = "315"
    RPL_ENDOFNAMES = "366"
    RPL_SASLSUCCESS = "903"

    # Please note that all ERR_ codes are listed again below
    ERR_STARTTLS = "691"
    ERR_INVALIDMODEPARAM = "696"
    ERR_NOPRIVS = "723"
    ERR_NICKLOCKED = "902"
    ERR_SASLFAIL = "904"
    ERR_SASLTOOLONG = "905"
    ERR_SASLABORTED = "906"
    ERR_SASLALREADY = "907"


# Detecting whether a code is an error is weirdly inconsistent.
# See: https://modern.ircdocs.horse/
def _is_error_code(command: str) -> bool:
    return (
        command.startswith(("4", "5"))
        or command in (
            _Codes.ERR_STARTTLS,
            _Codes.ERR_INVALIDMODEPARAM,
            _Codes.ERR_NOPRIVS,
            _Codes.ERR_NICKLOCKED,
            _Codes.ERR_SASLFAIL,
            _Codes.ERR_SASLTOOLONG,
            _Codes.ERR_SASLABORTED,
            _Codes.ERR_SASLALREADY,
        )
    )


class IrcCore:
    def __init__(self, settings: config.ServerSettings, *, verbose: bool):
        self.settings = settings
        self._verbose = verbose

        # This is where we are actually connected to. When the settings
        # change, we reconnect shortly after and that's when this updates.
        self.host = settings.host

        self._send_queue: collections.deque[
            tuple[bytes, SentPrivmsg | _Quit | None]
        ] = collections.deque()
        self._receive_buffer = bytearray()

        self._joins_in_progress: dict[str, _JoinInProgress] = {}

        # Will contain the capabilities to negotiate with the server
        self._cap_req: list[str] = []
        # "CAP LIST" shows capabilities enabled on the client's connection
        self._cap_list: set[str] = set()
        # To evaluate how many more ACK/NAKs will be received from server
        self._pending_cap_count = 0

        # Keep track of whether the current user is away or not.
        # User lists do that for all users on a channel, but that's not enough if
        # the user does not join any channels and only chats with private messages.
        self.is_away = False

        # While waiting for a response to a WHO, don't send another WHO.
        # This prevents the server from deciding to disconnect because it's
        # being asked to send a lot of data quickly.
        #
        # None means we're not waiting for any WHO to complete.
        #
        # TODO: clear this when reconnecting
        self._pending_who_sends: list[str] | None = None

        self._events: list[IrcEvent] = []

        # Unfortunately there's no such thing as non-blocking connect().
        # Unless you don't invoke getaddrinfo(), which will always block.
        # But then you can't specify a host name to connect to, only an IP.
        #
        # (asyncio calls getaddrinfo() in a separate thread, and manages
        # to do it in a way that makes connecting slow on my system)
        self._connect_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"connect-{self.host}-{hex(id(self))}"
        )

        # Possible states:
        #   Future: currently connecting
        #   socket: connected
        #   float: disconnected, value indicates when to reconnect
        #   None: quitting
        self._connection_state: Future[_Socket] | _Socket | float | None = (
            time.monotonic()
        )

        self._force_quit_time: float | None = None

        self._ping_sent = False
        self._last_receive_time = time.monotonic()

        self._nickmask: str | None = None

    def get_events(self) -> list[IrcEvent]:
        result = self._events.copy()
        self._events.clear()
        return result

    def get_nickmask(self) -> str | None:
        if self._nickmask is None:
            return None
        return self.settings.nick + self._nickmask

    def set_nickmask(self, user: str, host: str) -> None:
        self._nickmask = f"!{user}@{host}"

    # Call this repeatedly from the GUI's event loop.
    #
    # This is the best we can do in tkinter without threading. I
    # tried using threads, and they were difficult to get right.
    def run_one_step(self) -> None:
        if self._connection_state is None:
            # quitting finished
            return

        elif isinstance(self._connection_state, float):
            if time.monotonic() < self._connection_state:
                return

            # Time to reconnect. Clearing data from previous connections.
            self._send_queue.clear()
            self._receive_buffer.clear()
            self._cap_req.clear()
            self._cap_list.clear()
            # TODO: should we reset _pending_cap_count?
            self.is_away = False
            self._nickmask = None

            if self.host != self.settings.host:
                self._events.append(HostChanged(old=self.host, new=self.settings.host))
                self.host = self.settings.host

            self._events.append(
                ConnectivityMessage(
                    f"Connecting to {self.host} port {self.settings.port}...",
                    is_error=False,
                )
            )
            self._connection_state = self._connect_pool.submit(
                _create_connection, self.host, self.settings.port, self.settings.ssl
            )

        elif isinstance(self._connection_state, Future):
            future = _cast_future(self._connection_state)
            if future.running():
                return

            try:
                self._connection_state = future.result()
            except (OSError, ssl.SSLError) as e:
                self._events.append(
                    ConnectivityMessage(
                        f"Cannot connect (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                self._connection_state = time.monotonic() + RECONNECT_SECONDS
                return

            self._ping_sent = False
            self._last_receive_time = time.monotonic()

            self._connection_state.setblocking(False)

            if self.settings.password is not None:
                self._cap_req.append("sasl")
            self._cap_req.append("away-notify")

            self._pending_cap_count = len(self._cap_req)
            for capability in self._cap_req:
                self.send(f"CAP REQ {capability}")

            self.send(f"NICK {self.settings.nick}")
            self.send(f"USER {self.settings.username} 0 * :{self.settings.realname}")

        else:
            # Connected
            sock = cast(_Socket, self._connection_state)  # TODO: make this more type-safe
            try:
                quitting = self._send_and_receive_as_much_as_possible_without_blocking(sock)
            except (OSError, ssl.SSLError) as e:
                self._events.append(
                    ConnectivityMessage(
                        f"Connection error (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                sock.close()
                self._connection_state = time.monotonic() + RECONNECT_SECONDS
                return

            if quitting:
                self._connection_state = None
                sock.setblocking(True)
                self._connect_pool.submit(_flush_and_close_socket, sock)
                return

    def _send_and_receive_as_much_as_possible_without_blocking(
        self, sock: _Socket
    ) -> bool:
        while True:
            try:
                received = sock.recv(4096)
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                break

            if not received:
                raise OSError("Server closed the connection!")

            self._receive_buffer += received
            self._ping_sent = False
            self._last_receive_time = time.monotonic()

            # Do not use .splitlines(keepends=True), it splits on \r which is bad (#115)
            split_result = self._receive_buffer.split(b"\n")
            self._receive_buffer = split_result.pop()
            for line in split_result:
                self._handle_received_line(bytes(line) + b"\n")

        time_since_receive = time.monotonic() - self._last_receive_time
        if time_since_receive > IDLE_BEFORE_PING_SECONDS and not self._ping_sent:
            # ping_sent must be set before sending, because .send() ends up calling this method
            self._ping_sent = True
            # The PONG will show up for the user in server view
            self.send("PING :mantaray")
        if time_since_receive > IDLE_BEFORE_PING_SECONDS + PING_TIMEOUT_SECONDS:
            raise OSError(
                f"Server did not respond to ping in {PING_TIMEOUT_SECONDS} seconds."
            )

        while self._send_queue:
            data, done_event = self._send_queue[0]
            try:
                n = sock.send(data)
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                break

            if self._verbose:
                print("Send:", data[:n])
            if n == len(data):
                self._send_queue.popleft()
                if isinstance(done_event, _Quit):
                    return True
                if done_event is not None:
                    self._events.append(done_event)
            else:
                self._send_queue[0] = (data[n:], done_event)

        return False

    def _handle_received_line(self, line_bytes: bytes) -> None:
        if self._verbose:
            print("Recv:", line_bytes)
        # Allow \r\n line endings, or \r in middle of message
        line_bytes = line_bytes.replace(b"\r\n", b"\n").rstrip(b"\n")

        if not line_bytes:
            # "Empty messages are silently ignored"
            # https://tools.ietf.org/html/rfc2812#section-2.3.1
            return

        line = line_bytes.decode("utf-8", errors="replace")

        if not line.startswith(":"):
            # Server sends PING this way, for example
            sender = "???"
            command, *args = line.split(" ")
        else:
            # Most messages are like this.
            sender, command, *args = line.split(" ")
            sender = sender[1:]

        for n, arg in enumerate(args):
            if arg.startswith(":"):
                temp = args[:n]
                temp.append(" ".join(args[n:])[1:])
                args = temp
                break

        if sender is not None and "!" in sender:
            sender_nick = sender.split("!")[0]   # sender is nick!user@host
            self._handle_message_from_user(sender_nick, command, args)
        else:
            self._handle_message_from_server(sender, command, args)

    def _handle_message_from_user(self, sender_nick: str, command: str, args: list[str]) -> None:
        match (command, args):
            case ("PRIVMSG", [recipient, text]):
                if recipient == self.settings.nick:
                    # message from some other user to this user
                    self._events.append(ReceivePM(sender_nick=sender_nick, text=text))
                else:
                    # someone sent a message to a channel
                    # TODO(refactor): check if channel is in the active channels
                    self._events.append(ChannelMessage(channel=recipient, sender_nick=sender_nick, text=text))

            # According to https://modern.ircdocs.horse/ marking someone as
            # back can be done with no parameters or empty parameter.
            case ("AWAY", []) | ("AWAY", [""]):
                self._events.append(Back(sender_nick))
            case ("AWAY", [reason]):
                self._events.append(Away(sender_nick, reason=reason))

            case _:
                self._events.append(MessageFromUser(sender_nick, command, args))

    def _handle_message_from_server(self, sender: str, command: str, args: list[str]) -> None:
        match (command, args):
            # TODO: wtf are the first 2 args?
            # rfc1459 doesn't mention them, but freenode
            # gives 4-element msg.args lists
            case (_Codes.RPL_NAMREPLY, [_, _, channel, names]):
                # TODO: the prefixes have meanings
                # TODO: get the prefixes actually used from RPL_ISUPPORT
                # https://modern.ircdocs.horse/#channel-membership-prefixes
                join = self._joins_in_progress.setdefault(channel, _JoinInProgress())
                join.nicks.extend(name.lstrip("~&@%+") for name in names.split())

            case (_Codes.RPL_TOPIC, [_, channel, topic]):
                join = self._joins_in_progress.setdefault(channel, _JoinInProgress())
                join.topic = topic

            case (_Codes.RPL_ENDOFWHO, _):
                if self._pending_who_sends:
                    channel = self._pending_who_sends.pop()
                    self.send(f"WHO {channel}")
                else:
                    self._pending_who_sends = None

            case (_Codes.RPL_ENDOFNAMES, [_, channel, _]):
                # joining a channel finished
                join = self._joins_in_progress.pop(channel)

                # We already know the nicks of people on the channel, but we
                # don't know which ones are marked as being away.
                if "away-notify" in self._cap_list:
                    # The server supports tracking which users are away. Let's
                    # start that tracking by asking who is away now. The server
                    # will later notify us when someone is marked as away or
                    # back.
                    if self._pending_who_sends is None:
                        # no WHO sending is currently going on
                        self._pending_who_sends = []
                        self.send(f"WHO {channel}")
                    else:
                        # WHO sending is currently in progress, queue the next one
                        self._pending_who_sends.append(channel)

                self._events.append(IJoinedChannel(channel, join.nicks, join.topic))

            case ("CAP", args):
                match args:
                    case [_, "ACK", caps]:
                        acknowledged = caps.split()
                        self._pending_cap_count -= len(acknowledged)
                        if "sasl" in acknowledged:
                            self.send("AUTHENTICATE PLAIN")
                        self._cap_list.update(acknowledged)
                    case [_, "NAK", caps]:
                        rejected = caps.split()
                        self._pending_cap_count -= len(rejected)
                        if "sasl" in rejected:
                            # TODO: this good?
                            raise ValueError("The server does not support SASL.")
                    case _:
                        self.send("CAP END")
                        # TODO: this good?
                        raise ValueError("Invalid CAP response. Aborting Capability Negotiation.")

                # If we use SASL, we can't send CAP END until all SASL stuff is done.
                # If "sasl" is in _cap_list, Mantaray sends CAP END after the server
                # has replied with RPL_SASLSUCCESS or ERR_SASLFAIL
                if (
                    self._pending_cap_count == 0
                    and "sasl" not in self._cap_list
                ):
                    self.send("CAP END")

            case _:
                if command == _Codes.RPL_SASLSUCCESS or command == _Codes.ERR_SASLFAIL:
                    # We want to show this in UI and send CAP END
                    self.send("CAP END")
                self._events.append(MessageFromServer(sender, command, args, is_error=_is_error_code(command)))

    def send(
        self, message: str, *, done_event: SentPrivmsg | _Quit | None = None
    ) -> None:
        self._send_queue.append((message.encode("utf-8") + b"\r\n", done_event))
        self.run_one_step()

    # Reconnecting is needed e.g. after changing settings.
    def reconnect(self) -> None:
        if self._connection_state is None:
            # we are trying to reconnect but already quitting???
            return

        if isinstance(self._connection_state, float):
            # A reconnect is already scheduled, that can be ignored
            pass
        elif isinstance(self._connection_state, Future):
            # It's already connecting. We won't use that connection.
            future = _cast_future(self._connection_state)
            future.add_done_callback(_close_socket_when_future_done)
        else:
            sock = cast(_Socket, self._connection_state)  # TODO: make this more type-safe
            sock.close()
        self._connection_state = time.monotonic()  # reconnect asap

    def send_privmsg(
        self, nick_or_channel: str, text: str, *, history_id: int | None = None
    ) -> None:
        self.send(
            f"PRIVMSG {nick_or_channel} :{text}",
            done_event=SentPrivmsg(nick_or_channel, text, history_id),
        )

    def quit(self, *, wait: bool = False) -> None:
        if (
            isinstance(self._connection_state, (socket.socket, ssl.SSLSocket))
            and self._force_quit_time is None
        ):
            # Attempt a clean quit
            self.send("QUIT", done_event=_Quit())
            self._force_quit_time = time.monotonic() + 1
        else:
            self._force_quit_now()

        if wait:
            start = time.monotonic()
            while self._connection_state is not None:
                assert time.monotonic() < start + 10
                self.run_one_step()
                time.sleep(0.01)

    def quitting_finished(self) -> bool:
        return self._connection_state is None

    def _force_quit_now(self) -> None:
        if isinstance(self._connection_state, (socket.socket, ssl.SSLSocket)):
            self._connection_state.close()
        if isinstance(self._connection_state, Future):
            # It's already connecting. We won't use the resulting connection.
            future = _cast_future(self._connection_state)
            future.add_done_callback(_close_socket_when_future_done)
        self._connection_state = None
