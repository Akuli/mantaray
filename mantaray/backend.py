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
from typing import Union, Sequence, Iterator

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
class _JoinInProgress:
    topic: str | None
    nicks: list[str]


@dataclasses.dataclass
class ReceivedLine:
    sender: str | None
    sender_is_server: bool
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


IrcEvent = Union[ReceivedLine, ConnectivityMessage, HostChanged, SentPrivmsg, Quit]


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
        self._send_queue: queue.Queue[
            tuple[bytes, SentPrivmsg | Quit | None]
        ] = queue.Queue()
        self._recv_buffer: collections.deque[bytes] = collections.deque()

        # During connecting, sock is None and connected is False.
        # If connected is True, sock shouldn't be None.
        self._sock: socket.socket | ssl.SSLSocket | None = None
        self._connected = False

        self.event_queue: queue.Queue[IrcEvent] = queue.Queue()
        self._threads: list[threading.Thread] = []

        # servers seem to send RPL_NAMREPLY followed by RPL_ENDOFNAMES when joining channel
        # the replies are collected here before emitting a self_joined event
        # Topic can also be sent before joining
        # TODO: this in rfc?
        self.joining_in_progress: dict[str, _JoinInProgress] = {}

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

    def send(
        self, message: str, *, done_event: SentPrivmsg | Quit | None = None
    ) -> None:
        self._send_queue.put((message.encode("utf-8") + b"\r\n", done_event))

    @staticmethod
    def _parse_received_message(line: str) -> ReceivedLine:
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
        return ReceivedLine(sender, sender_is_server, command, args)

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
                print("Recv:", line_bytes + b"\n")

            # Allow \r\n line endings, or \r in middle of message
            if line_bytes.endswith(b"\r"):
                line_bytes = line_bytes[:-1]

            line = line_bytes.decode("utf-8", errors="replace")
            if not line:
                # "Empty messages are silently ignored"
                # https://tools.ietf.org/html/rfc2812#section-2.3.1
                continue
            # TODO: should be handled in received.py like everything else
            if line.startswith("PING"):
                self.send(line.replace("PING", "PONG", 1))
                continue

            self.event_queue.put(self._parse_received_message(line))

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
                if isinstance(done_event, Quit):
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
            self.send("CAP REQ sasl")

        # TODO: what if nick or user are in use? use alternatives?
        self.send(f"NICK {self.nick}")
        self.send(f"USER {self.username} 0 * :{self.realname}")

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
        self.joining_in_progress[channel.lower()] = _JoinInProgress(None, [])
        self.send(f"JOIN {channel}")

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self.send(
            f"PRIVMSG {nick_or_channel} :{text}",
            done_event=SentPrivmsg(nick_or_channel, text),
        )

    def quit(self) -> None:
        sock = self._sock
        if sock is not None and self._connected:
            sock.settimeout(1)  # Do not freeze forever if sending is slow
            self.send("QUIT", done_event=Quit())
        else:
            # TODO: duplicate code in done_event handling
            self._disconnect()
            self._quit_event.set()
            self.event_queue.put(Quit())
