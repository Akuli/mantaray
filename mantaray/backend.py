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
import select
import socket
import sys
import threading
import time
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


# Special bytes to be put to loop notify socketpair
_QUIT_THE_SERVER = b"q"
_RECONNECT = b"r"
_BYTES_ADDED_TO_SEND_QUEUE = b"s"


class IrcCore:

    # each channel in autojoin will be joined after connecting
    def __init__(self, server_config: config.ServerConfig, *, verbose: bool):
        self._verbose = verbose
        self._apply_config(server_config)

        self._send_queue: collections.deque[
            tuple[bytes, SentPrivmsg | Quit | None]
        ] = collections.deque()

        # Sending to _loop_notify_send tells the select() loop to do something special
        self._loop_notify_send, self._loop_notify_recv = socket.socketpair()
        self._loop_notify_recv.setblocking(False)

        self._send_and_recv_loop_running = False

        self.event_queue: queue.Queue[IrcEvent] = queue.Queue()
        self._thread: threading.Thread | None = None

        self.nickmask: str | None = None

    def _apply_config(self, server_config: config.ServerConfig) -> None:
        self.host = server_config["host"]
        self.port = server_config["port"]
        self.ssl = server_config["ssl"]
        self.nick = server_config["nick"]
        self.username = server_config["username"]
        self.realname = server_config["realname"]
        self.password = server_config["password"]
        self.autojoin = server_config["joined_channels"].copy()

    def start_thread(self) -> None:
        assert self._thread is None
        self._thread = threading.Thread(
            name=f"core-thread-{hex(id(self))}-{self.nick}", target=self._connect_loop
        )
        self._thread.start()

    def _notify_the_select_loop(self, message: bytes) -> None:
        try:
            self._loop_notify_send.send(message)
        except OSError as e:
            # Expected to fail if we already quit, and socket is closed
            if self._loop_notify_send.fileno() != -1:
                raise e

    def _connect_loop(self) -> None:
        while True:
            # send queue contents were for previous connection
            self._send_queue.clear()

            try:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Connecting to {self.host} port {self.port}...", is_error=False
                    )
                )
                sock = self._connect()
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(
                        f"Cannot connect (reconnecting in {RECONNECT_SECONDS}sec): {e}",
                        is_error=True,
                    )
                )
                end = time.monotonic() + RECONNECT_SECONDS
                while True:
                    timeout = end - time.monotonic()
                    if timeout < 0:
                        break

                    can_receive = select.select(
                        [self._loop_notify_recv], [], [], timeout
                    )[0]
                    if self._loop_notify_recv in can_receive:
                        byte = self._loop_notify_recv.recv(1)
                        if byte == _QUIT_THE_SERVER:
                            self.event_queue.put(Quit())
                            self._loop_notify_send.close()
                            self._loop_notify_recv.close()
                            return
                        if byte == _RECONNECT:
                            break
                continue

            if self.password is not None:
                self.send("CAP REQ sasl")
            # TODO: what if nick or user are in use? use alternatives?
            self.send(f"NICK {self.nick}")
            self.send(f"USER {self.username} 0 * :{self.realname}")

            self._send_and_recv_loop_running = True
            try:
                quitting = self._send_and_recv_loop(sock)
            except (OSError, ssl.SSLError) as e:
                self.event_queue.put(
                    ConnectivityMessage(f"Connection error: {e}", is_error=True)
                )
                quitting = False
            self._send_and_recv_loop_running = False

            sock.settimeout(1)
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ssl.SSLError):
                pass
            sock.close()
            self.event_queue.put(ConnectivityMessage("Disconnected.", is_error=True))

            if quitting:
                self.event_queue.put(Quit())
                self._loop_notify_send.close()
                self._loop_notify_recv.close()
                return

    def _connect(self) -> socket.socket | ssl.SSLSocket:
        sock: socket.socket | ssl.SSLSocket

        if self.ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(socket.socket(), server_hostname=self.host)
        else:
            sock = socket.socket()

        try:
            # Unfortunately there's no such thing as non-blocking connect().
            # Unless you don't invoke getaddrinfo(), which will always block.
            # But then you can't specify a host name to connect to, only an IP.
            #
            # (asyncio manually calls getaddrinfo() in a separate thread, and
            # manages to do it in a way that makes connecting slow on my system)
            sock.settimeout(15)
            sock.connect((self.host, self.port))
        except Exception as e:
            sock.close()
            raise e

        return sock

    # I used to have separate send and receive threads, but I decided to use select() instead.
    # It's more code, but avoiding race conditions is easier with less threads.
    def _send_and_recv_loop(self, sock: socket.socket | ssl.SSLSocket) -> bool:
        sock.setblocking(False)
        recv_buffer = bytearray()

        while True:
            wanna_recv = set()
            wanna_send = set()

            while True:
                try:
                    byte = self._loop_notify_recv.recv(1)
                except BlockingIOError:
                    wanna_recv.add(self._loop_notify_recv)
                    break
                else:
                    if byte == _QUIT_THE_SERVER:
                        return True
                    elif byte == _RECONNECT:
                        return False
                    elif byte == _BYTES_ADDED_TO_SEND_QUEUE:
                        # The purpose of this byte is to wake up the select() below.
                        pass
                    else:
                        raise ValueError(byte)

            while True:
                try:
                    received = sock.recv(4096)
                except (ssl.SSLWantReadError, BlockingIOError):
                    wanna_recv.add(sock)
                    break
                except ssl.SSLWantWriteError:
                    wanna_send.add(sock)
                    break
                else:
                    if not received:
                        raise OSError("Server closed the connection!")
                    recv_buffer += received

                    # Do not use .splitlines(keepends=True), it splits on \r which is bad (#115)
                    split_result = recv_buffer.split(b"\n")
                    recv_buffer = split_result.pop()
                    for line in split_result:
                        self._handle_received_line(bytes(line) + b"\n")

            while self._send_queue:
                data, done_event = self._send_queue[0]
                try:
                    n = sock.send(data)
                except ssl.SSLWantReadError:
                    wanna_recv.add(sock)
                    break
                except (ssl.SSLWantWriteError, BlockingIOError):
                    wanna_send.add(sock)
                    break
                else:
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

            select.select(wanna_recv, wanna_send, [])

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
        self._notify_the_select_loop(_BYTES_ADDED_TO_SEND_QUEUE)

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
        assert self.nick == server_config["nick"]
        assert self.autojoin == server_config["joined_channels"]

        old_host = self.host
        self._apply_config(server_config)
        self._notify_the_select_loop(_RECONNECT)

        if old_host != self.host:
            self.event_queue.put(HostChanged(old_host, self.host))

    def send_privmsg(self, nick_or_channel: str, text: str) -> None:
        self.send(
            f"PRIVMSG {nick_or_channel} :{text}",
            done_event=SentPrivmsg(nick_or_channel, text),
        )

    def quit(self, *, wait: bool = False) -> None:
        if self._send_and_recv_loop_running:
            # Attempt a clean quit
            self.send("QUIT", done_event=Quit())
            timer = threading.Timer(
                1, (lambda: self._notify_the_select_loop(_QUIT_THE_SERVER))
            )
            timer.daemon = True
            timer.start()
        else:
            self._notify_the_select_loop(_QUIT_THE_SERVER)

        if self._thread is not None and wait:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                # TODO: hopefully this disgusting debug prints can remove some day
                assert self._thread.ident is not None
                stack_trace = traceback.format_stack(
                    sys._current_frames()[self._thread.ident]
                )
                raise RuntimeError("thread doesn't stop\n" + "".join(stack_trace))
