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
from typing import Iterator, Union

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


@dataclasses.dataclass
class MessageFromUser:
    sender_nick: str
    sender_user_mask: str  # nick!user@host
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


@dataclasses.dataclass
class SentPrivmsg:
    nick_or_channel: str
    text: str
    history_id: int | None


@dataclasses.dataclass
class _Quit:
    pass


IrcEvent = Union[
    MessageFromServer, MessageFromUser, ConnectivityMessage, HostChanged, SentPrivmsg
]
_Socket = Union[socket.socket, ssl.SSLSocket]


def _create_connection(host: str, port: int, use_ssl: bool) -> _Socket:
    sock: _Socket

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

        # Will contain the capabilities to negotiate with the server
        self.cap_req: list[str] = []
        # "CAP LIST" shows capabilities enabled on the client's connection
        self.cap_list: set[str] = set()
        # To evaluate how many more ACK/NAKs will be received from server
        self.pending_cap_count = 0

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
        self._connection_state: Future[
            _Socket
        ] | _Socket | float | None = time.monotonic()

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
            self.cap_req.clear()
            self.cap_list.clear()
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
            if self._connection_state.running():
                return

            try:
                self._connection_state = self._connection_state.result()
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
                self.cap_req.append("sasl")
            self.cap_req.append("away-notify")

            self.pending_cap_count = len(self.cap_req)
            for capability in self.cap_req:
                self.send(f"CAP REQ {capability}")

            self.send(f"NICK {self.settings.nick}")
            self.send(f"USER {self.settings.username} 0 * :{self.settings.realname}")

        else:
            # Connected
            try:
                quitting = self._send_and_receive_as_much_as_possible_without_blocking(
                    self._connection_state
                )
            except (OSError, ssl.SSLError) as e:
                self._events.append(
                    ConnectivityMessage(
                        f"Connection error (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                self._connection_state.close()
                self._connection_state = time.monotonic() + RECONNECT_SECONDS
                return

            if quitting:
                sock = self._connection_state
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

    def _handle_received_line(self, line: bytes) -> None:
        if self._verbose:
            print("Recv:", line)
        # Allow \r\n line endings, or \r in middle of message
        line = line.replace(b"\r\n", b"\n").rstrip(b"\n")

        if not line:
            # "Empty messages are silently ignored"
            # https://tools.ietf.org/html/rfc2812#section-2.3.1
            return

        self._events.append(
            self._parse_received_message(line.decode("utf-8", errors="replace"))
        )

    def send(
        self, message: str, *, done_event: SentPrivmsg | _Quit | None = None
    ) -> None:
        self._send_queue.append((message.encode("utf-8") + b"\r\n", done_event))
        self.run_one_step()

    @staticmethod
    def _parse_received_message(line: str) -> MessageFromServer | MessageFromUser:
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
            return MessageFromUser(
                sender_nick=sender.split("!")[0],
                sender_user_mask=sender,
                command=command,
                args=args,
            )
        else:
            return MessageFromServer(server=sender, command=command, args=args)

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
            self._connection_state.add_done_callback(_close_socket_when_future_done)
        else:
            self._connection_state.close()
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
            self._connection_state.add_done_callback(_close_socket_when_future_done)
        self._connection_state = None
