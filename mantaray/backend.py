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
import queue
import ssl
import re
import socket
import time
from typing import Union, Sequence, Iterator
from concurrent.futures import ThreadPoolExecutor, Future

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


RECONNECT_SECONDS = 5


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


@dataclasses.dataclass
class Quit:
    pass


IrcEvent = Union[
    MessageFromServer,
    MessageFromUser,
    ConnectivityMessage,
    HostChanged,
    SentPrivmsg,
    Quit,
]
_Socket = Union[socket.socket, ssl.SSLSocket]


def _create_connection(host: str, port: int, use_ssl: bool) -> _Socket:
    sock: _Socket

    if use_ssl:
        context = ssl.create_default_context()
        sock = context.wrap_socket(socket.socket(), server_hostname=host)
    else:
        sock = socket.socket()

    try:
        sock.settimeout(15)
        sock.connect((host, port))
    except Exception as e:
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

    # each channel in autojoin will be joined after connecting
    def __init__(self, server_config: config.ServerConfig, *, verbose: bool):
        self._verbose = verbose
        self._apply_config(server_config)

        self._send_queue: collections.deque[
            tuple[bytes, SentPrivmsg | Quit | None]
        ] = collections.deque()
        self._receive_buffer = bytearray()

        # Will contain the capabilities to negotiate with the server
        self.cap_req: list[str] = []
        # "CAP LIST" shows capabilities enabled on the client's connection
        self.cap_list: set[str] = set()
        self.pending_cap_count = 0

        self.event_queue: queue.Queue[IrcEvent] = queue.Queue()

        # Unfortunately there's no such thing as non-blocking connect().
        # Unless you don't invoke getaddrinfo(), which will always block.
        # But then you can't specify a host name to connect to, only an IP.
        #
        # (asyncio manually calls getaddrinfo() in a separate thread, and
        # manages to do it in a way that makes connecting slow on my system)
        self._connect_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"connect-{self.nick}-{hex(id(self))}"
        )

        # Possible states:
        #   Future: currently connecting
        #   socket: connected
        #   float: time.monotonic() disconnected, value indicates when to reconnect
        #   None: quitting
        self._connection_state: Future[
            _Socket
        ] | _Socket | float | None = time.monotonic()

        self._force_quit_time: float | None = None

    def _apply_config(self, server_config: config.ServerConfig) -> None:
        self.host = server_config["host"]
        self.port = server_config["port"]
        self.ssl = server_config["ssl"]
        self.nick = server_config["nick"]
        self.username = server_config["username"]
        self.realname = server_config["realname"]
        self.password = server_config["password"]
        self.autojoin = server_config["joined_channels"].copy()

    # Call this repeatedly from the GUI's event loop.
    #
    # This is the best we can do in tkinter without threading. I
    # tried using threads, and they were difficult to get right.
    def run_one_step(self) -> None:
        if self._connection_state is None:
            # quitting
            return

        elif isinstance(self._connection_state, float):
            if time.monotonic() < self._connection_state:
                return

            # Time to reconnect. Clearing data from previous connections.
            self._send_queue.clear()
            self._receive_buffer.clear()
            self.cap_req.clear()
            self.cap_list.clear()
            self.event_queue.put(
                ConnectivityMessage(
                    f"Connecting to {self.host} port {self.port}...", is_error=False
                )
            )
            self._connection_state = self._connect_pool.submit(
                _create_connection, self.host, self.port, self.ssl
            )

        elif isinstance(self._connection_state, Future):
            if self._connection_state.running():
                return

            try:
                self._connection_state = self._connection_state.result()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Cannot connect (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                self._connection_state = time.monotonic() + RECONNECT_SECONDS
                return

            self._connection_state.setblocking(False)

            if self.password is not None:
                self.cap_req.append("sasl")

            self.cap_req.append("away-notify")

            self.pending_cap_count = len(
                self.cap_req
            )  # To evaluate how many more ACK/NAKs will be received from server
            for capability in self.cap_req:
                self.send(f"CAP REQ {capability}")

            # TODO: what if nick or user are in use? use alternatives?
            self.send(f"NICK {self.nick}")
            self.send(f"USER {self.username} 0 * :{self.realname}")

        else:
            # Connected
            try:
                quitting = self._send_and_receive_as_much_as_possible_without_blocking(
                    self._connection_state
                )
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
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

            # Do not use .splitlines(keepends=True), it splits on \r which is bad (#115)
            split_result = self._receive_buffer.split(b"\n")
            self._receive_buffer = split_result.pop()
            for line in split_result:
                self._handle_received_line(bytes(line) + b"\n")

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
                if done_event is not None:
                    self.event_queue.put(done_event)
                    if isinstance(done_event, Quit):
                        return True
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

        self.event_queue.put(
            self._parse_received_message(line.decode("utf-8", errors="replace"))
        )

    def send(
        self, message: str, *, done_event: SentPrivmsg | Quit | None = None
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

    def apply_config_and_reconnect(self, server_config: config.ServerConfig) -> None:
        if self._connection_state is None:
            # we are trying to reconnect but already quitting???
            return

        assert self.nick == server_config["nick"]
        assert self.autojoin == server_config["joined_channels"]

        old_host = self.host
        self._apply_config(server_config)
        if isinstance(self._connection_state, (socket.socket, ssl.SSLSocket)):
            self._connection_state.close()
        if isinstance(self._connection_state, Future):
            # It's already connecting. We won't use the resulting connection.
            self._connection_state.add_done_callback(_close_socket_when_future_done)
        self._connection_state = time.monotonic()  # reconnect asap

        if old_host != self.host:
            self.event_queue.put(HostChanged(old_host, self.host))

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self.send(
            f"PRIVMSG {nick_or_channel} :{text}",
            done_event=SentPrivmsg(nick_or_channel, text),
        )

    def quit(self, *, wait: bool = False) -> None:
        if (
            isinstance(self._connection_state, (socket.socket, ssl.SSLSocket))
            and self._force_quit_time is None
        ):
            # Attempt a clean quit
            self.send("QUIT", done_event=Quit())
            self._force_quit_time = time.monotonic() + 1
        else:
            self._force_quit_now()

        if wait:
            start = time.monotonic()
            while self._connection_state is not None:
                assert time.monotonic() < start + 10
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
        self.event_queue.put(Quit())
