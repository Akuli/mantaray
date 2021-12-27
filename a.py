from __future__ import annotations

import collections
import dataclasses
import itertools
import queue
import re
import socket
import ssl
import sys
import tempfile
import threading
import time
import tkinter
import traceback
from pathlib import Path
from tkinter import ttk
from tkinter.font import Font
from typing import IO, Iterator, Sequence, Union

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


class View:
    def __init__(self, irc_widget, name: str, *, parent_view_id: str = ""):
        self.irc_widget = irc_widget
        self.view_id = irc_widget.view_selector.insert(parent_view_id, "end", text=name)
        self._name = name
        self.notification_count = 0

        self.textwidget = tkinter.Text(
            irc_widget,
            width=1,  # minimum, can stretch bigger
            height=1,  # minimum, can stretch bigger
            font=irc_widget.font,
            state="disabled",
            takefocus=True,
        )
        self.textwidget.bind("<Button-1>", (lambda e: self.textwidget.focus()))

        self.textwidget.tag_configure("underline", underline=True)
        self.textwidget.tag_configure("pinged", foreground="black")
        self.textwidget.tag_configure("error", foreground="black")
        self.textwidget.tag_configure("info", foreground="red")
        self.textwidget.tag_configure("history-selection", background="red")
        self.textwidget.tag_configure("channel", foreground="red")
        self.textwidget.tag_configure("self-nick", foreground="red", underline=True)
        self.textwidget.tag_configure("other-nick", foreground="red", underline=True)

        self.log_file: IO[str] | None = None

    def _update_view_selector(self) -> None:
        if self.notification_count == 0:
            text = self.view_name
        else:
            text = f"{self.view_name} ({self.notification_count})"
        self.irc_widget.view_selector.item(self.view_id, text=text)

    @property
    def view_name(self) -> str:  # e.g. channel name, server host, other nick of PM
        return self._name

    @view_name.setter
    def view_name(self, new_name: str) -> None:
        self._name = new_name
        self._update_view_selector()

    def _window_has_focus(self) -> bool:
        return bool(self.irc_widget.tk.eval("focus"))

    def add_notification(self, popup_text: str) -> None:
        if self.irc_widget.get_current_view() == self and self._window_has_focus():
            return

        self.notification_count += 1
        self._update_view_selector()
        self.irc_widget.event_generate("<<NotificationCountChanged>>")

    def mark_seen(self) -> None:
        self.notification_count = 0
        self._update_view_selector()
        self.irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(self.irc_widget.view_selector.item(self.view_id, "tags"))
        self.irc_widget.view_selector.item(
            self.view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

    def add_tag(self, tag) -> None:
        if self.irc_widget.get_current_view() == self:
            return

        old_tags = set(self.irc_widget.view_selector.item(self.view_id, "tags"))
        if "pinged" in old_tags:  # Adding tag does not unping
            return

        self.irc_widget.view_selector.item(
            self.view_id, tags=list((old_tags - {"new_message", "pinged"}) | {tag})
        )

    def close_log_file(self) -> None:
        if self.log_file is not None:
            print("*** LOGGING ENDS", time.asctime(), file=self.log_file, flush=True)
            self.log_file.close()
            self.log_file = None

    def open_log_file(self) -> None:
        assert self.log_file is None

        # Unlikely to create name conflicts in practice, but it is possible
        name = re.sub("[^A-Za-z0-9-_#]", "_", self.view_name.lower())
        path = self.irc_widget.log_dir / self.server_view.core.host / (name + ".log")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file = path.open("a", encoding="utf-8")
            if sys.platform != "win32":
                path.chmod(0o600)
            print("\n\n*** LOGGING BEGINS", time.asctime(), file=file, flush=True)
            self.log_file = file
        except OSError:
            traceback.print_exc()

    def destroy_widgets(self) -> None:
        self.textwidget.destroy()

    @property
    def server_view(self) -> ServerView:
        parent_id = self.irc_widget.view_selector.parent(self.view_id)
        parent_view = self.irc_widget.views_by_id[parent_id]
        assert isinstance(parent_view, ServerView)
        return parent_view

    def add_message(
        self,
        sender: str,
        *chunks: tuple[str, list[str]],
        pinged: bool = False,
        show_in_gui: bool = True,
    ) -> None:
        if show_in_gui:
            # scroll down all the way if the user hasn't scrolled up manually
            do_the_scroll = self.textwidget.yview()[1] == 1.0

            # nicks are limited to 16 characters at least on freenode
            # len(sender) > 16 is not a problem:
            #    >>> ' ' * (-3)
            #    ''
            padding = " " * (16 - len(sender))

            if sender == "*":
                sender_tags = []
            elif sender == self.server_view.core.nick:
                sender_tags = ["self-nick"]
            else:
                sender_tags = ["other-nick"]

            self.textwidget.config(state="normal")
            start = self.textwidget.index("end - 1 char")
            self.textwidget.insert("end", time.strftime("[%H:%M]") + " " + padding)
            self.textwidget.insert("end", sender, sender_tags)
            self.textwidget.insert("end", " | ")
            flatten = itertools.chain.from_iterable
            if chunks:
                self.textwidget.insert("end", *flatten(chunks))  # type: ignore
            self.textwidget.insert("end", "\n")
            if pinged:
                self.textwidget.tag_add("pinged", start, "end - 1 char")
            self.textwidget.config(state="disabled")

            if do_the_scroll:
                self.textwidget.see("end")

        if self.log_file is not None:
            print(
                time.asctime(),
                sender,
                "".join(text for text, tags in chunks),
                sep="\t",
                file=self.log_file,
                flush=True,
            )

    def on_connectivity_message(self, message: str, *, error: bool = False) -> None:
        self.add_message("", (message, ["error" if error else "info"]))

    def on_self_changed_nick(self, old: str, new: str) -> None:
        # notify about the nick change everywhere, by putting this to base class
        self.add_message(
            "*", ("You are now known as ", []), (new, ["self-nick"]), (".", [])
        )

    def get_relevant_nicks(self) -> Sequence[str]:
        return []

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        self.add_message(
            "*",
            (old, ["other-nick"]),
            (" is now known as ", []),
            (new, ["other-nick"]),
            (".", []),
        )

    def on_relevant_user_quit(self, nick: str, reason: str | None) -> None:
        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"
        self.add_message(
            "*",
            (nick, ["other-nick"]),
            (" quit." + extra, []),
            show_in_gui=self.server_view.should_show_join_leave_message(nick),
        )


class ServerView(View):
    def __init__(self, irc_widget, server_config):
        super().__init__(irc_widget, server_config["host"])
        self.core = IrcCore(server_config)
        self.extra_notifications = set(server_config["extra_notifications"])
        self._join_leave_hiding_config = server_config["join_leave_hiding"]

        self.core.start_threads()
        self.handle_events()

    # Do not log server stuff
    def open_log_file(self) -> None:
        pass

    @property
    def server_view(self) -> ServerView:
        return self

    def should_show_join_leave_message(self, nick: str) -> bool:
        is_exceptional = nick.lower() in (
            n.lower() for n in self._join_leave_hiding_config["exception_nicks"]
        )
        return self._join_leave_hiding_config["show_by_default"] ^ is_exceptional

    def get_subviews(self, *, include_server: bool = False) -> list[View]:
        result: list[View] = []
        if include_server:
            result.append(self)
        for view_id in self.irc_widget.view_selector.get_children(self.view_id):
            result.append(self.irc_widget.views_by_id[view_id])
        return result

    def find_channel(self, name: str) -> ChannelView | None:
        for view in self.get_subviews():
            if (
                isinstance(view, ChannelView)
                and view.channel_name.lower() == name.lower()
            ):
                return view
        return None

    def handle_events(self) -> None:
        """Call this once to start processing events from the core."""
        # this is here so that this will be called again, even if
        # something raises an error this time
        next_call_id = self.irc_widget.after(100, self.handle_events)

        while True:
            try:
                event = self.core.event_queue.get(block=False)
            except queue.Empty:
                break

            if isinstance(event, SelfJoined):
                channel_view = self.find_channel(event.channel)
                if channel_view is None:
                    channel_view = ChannelView(self, event.channel, event.nicklist)
                    self.irc_widget.add_view(channel_view)

                channel_view.show_topic(event.topic)
                if event.channel not in self.core.autojoin:
                    self.core.autojoin.append(event.channel)

            elif isinstance(event, (ServerMessage, UnknownMessage)):
                self.server_view.add_message(
                    event.sender or "???", (" ".join([event.command] + event.args), [])
                )

            elif isinstance(event, ConnectivityMessage):
                for view in self.get_subviews(include_server=True):
                    view.on_connectivity_message(event.message, error=event.is_error)

            elif isinstance(event, TopicChanged):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_topic_changed(event.who_changed, event.topic)

            elif isinstance(event, HostChanged):
                self.view_name = event.new
                for subview in self.get_subviews():
                    subview.close_log_file()
                    subview.open_log_file()

            else:
                # If mypy says 'error: unused "type: ignore" comment', you
                # forgot to check for some class
                print("can't happen")  # type: ignore

    def get_current_config(self):
        channels = [
            view.channel_name
            for view in self.get_subviews()
            if isinstance(view, ChannelView)
        ]
        return {
            "host": self.core.host,
            "port": self.core.port,
            "ssl": self.core.ssl,
            "nick": self.core.nick,
            "username": self.core.username,
            "realname": self.core.realname,
            "password": self.core.password,
            "joined_channels": sorted(
                self.core.autojoin,
                key=(lambda chan: channels.index(chan) if chan in channels else -1),
            ),
            "extra_notifications": list(self.extra_notifications),
            "join_leave_hiding": self._join_leave_hiding_config,
        }


class ChannelView(View):
    def __init__(self, server_view: ServerView, channel_name: str, nicks: list[str]):
        super().__init__(
            server_view.irc_widget, channel_name, parent_view_id=server_view.view_id
        )
        self.open_log_file()

    # Includes the '#' character(s), e.g. '#devuan' or '##learnpython'
    # Same as view_name, but only channels have this attribute, can clarify things a lot
    @property
    def channel_name(self) -> str:
        return self.view_name

    def destroy_widgets(self) -> None:
        super().destroy_widgets()

    def on_join(self, nick: str) -> None:
        self.add_message(
            "*",
            (nick, ["other-nick"]),
            (f" joined {self.channel_name}.", []),
            show_in_gui=self.server_view.should_show_join_leave_message(nick),
        )

    def on_part(self, nick: str, reason: str | None) -> None:
        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"
        self.add_message(
            "*",
            (nick, ["other-nick"]),
            (f" left {self.channel_name}." + extra, []),
            show_in_gui=self.server_view.should_show_join_leave_message(nick),
        )

    def on_kick(
        self, channel: str, kicker: str, kicked_nick: str, reason: str | None
    ) -> None:
        if reason is None:
            reason = ""
        if kicker == self.server_view.core.nick:
            kicker_tag = "self-nick"
        else:
            kicker_tag = "other-nick"
        if kicked_nick == self.server_view.core.nick:
            self.add_message(
                "*",
                (kicker, [kicker_tag]),
                (" has kicked you from ", ["error"]),
                (self.channel_name, ["channel"]),
                (". You can still join by typing ", ["error"]),
                (f"/join {self.channel_name}", ["pinged"]),
                (".", ["error"]),
            )
        else:
            self.add_message(
                "*",
                (kicker, [kicker_tag]),
                (" has kicked ", []),
                (kicked_nick, ["other-nick"]),
                (" from ", []),
                (self.channel_name, ["channel"]),
                (f". (Reason: {reason})", []),
            )

    def get_relevant_nicks(self) -> tuple[str, ...]:
        return ("Alice",)

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        super().on_relevant_user_changed_nick(old, new)

    def on_relevant_user_quit(self, nick: str, reason: str | None) -> None:
        super().on_relevant_user_quit(nick, reason)

    def show_topic(self, topic: str) -> None:
        self.add_message("*", (f"The topic of {self.channel_name} is: {topic}", []))

    def on_topic_changed(self, nick: str, topic: str) -> None:
        if nick == self.server_view.core.nick:
            nick_tag = "self-nick"
        else:
            nick_tag = "other-nick"
        self.add_message(
            "*",
            (nick, [nick_tag]),
            (f" changed the topic of {self.channel_name}: {topic}", []),
        )


class IrcWidget(ttk.PanedWindow):
    def __init__(self, master: tkinter.Misc, file_config, log_dir: Path):
        super().__init__(master, orient="horizontal")
        self.log_dir = log_dir

        self.font = Font(
            family=file_config["font_family"], size=file_config["font_size"]
        )

        self.view_selector = ttk.Treeview(self, show="tree", selectmode="browse")
        self.view_selector.tag_configure("pinged", foreground="#00ff00")
        self.view_selector.tag_configure("new_message", foreground="#ffcc66")
        self.add(self.view_selector, weight=0)  # don't stretch
        self._contextmenu = tkinter.Menu(tearoff=False)

        self._previous_view: View | None = None
        self.view_selector.bind("<<TreeviewSelect>>", self._current_view_changed)

        self._middle_pane = ttk.Frame(self)
        self.add(self._middle_pane, weight=1)  # always stretch

        entryframe = ttk.Frame(self._middle_pane)
        entryframe.pack(side="bottom", fill="x")

        self.entry = tkinter.Entry(
            entryframe,
            font=self.font,
        )
        self.entry.pack(side="left", fill="both", expand=True)

        # {channel_like.name: channel_like}
        self.views_by_id: dict[str, View] = {}
        for server_config in file_config["servers"]:
            self.add_view(ServerView(self, server_config))

    def get_current_view(self) -> View:
        [view_id] = self.view_selector.selection()
        return self.views_by_id[view_id]

    def get_server_views(self) -> list[ServerView]:
        result = []
        for view_id in self.view_selector.get_children(""):
            view = self.views_by_id[view_id]
            assert isinstance(view, ServerView)
            result.append(view)
        return result

    def _current_view_changed(self, event: object) -> None:
        new_view = self.get_current_view()
        if self._previous_view == new_view:
            return

        if (
            self._previous_view is not None
            and self._previous_view.textwidget.winfo_exists()
        ):
            self._previous_view.textwidget.pack_forget()
        new_view.textwidget.pack(
            in_=self._middle_pane, side="top", fill="both", expand=True
        )
        new_view.mark_seen()

        self._previous_view = new_view

    def add_view(self, view: View) -> None:
        assert view.view_id not in self.views_by_id
        self.view_selector.item(view.server_view.view_id, open=True)
        self.views_by_id[view.view_id] = view
        self.view_selector.selection_set(view.view_id)


root_window = tkinter.Tk()
alice = IrcWidget(
    root_window,
    {
        "servers": [
            {
                "host": "localhost",
                "port": 6667,
                "ssl": False,
                "nick": "Alice",
                "username": "Alice",
                "realname": "Alice's real name",
                "joined_channels": ["#autojoin"],
                "password": None,
                "extra_notifications": [],
                "join_leave_hiding": {"show_by_default": True, "exception_nicks": []},
            }
        ],
        "font_family": "monospace",
        "font_size": 10,
    },
    Path(tempfile.mkdtemp(prefix="mantaray-tests-")),
)
alice.pack(fill="both", expand=True)

end = time.monotonic() + 5
while time.monotonic() < end:
    root_window.update()

print(50 * "W", flush=True)
root_window.destroy()
print(50 * "X", flush=True)
