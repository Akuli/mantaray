from __future__ import annotations
import re
import queue
import traceback
import time
import itertools
import sys
import tkinter
import subprocess
from tkinter import ttk
from typing import Sequence, TYPE_CHECKING, IO

from mantaray import backend, colors, config

if TYPE_CHECKING:
    from mantaray.gui import IrcWidget
    from typing_extensions import Literal


class _UserList:
    def __init__(self, irc_widget: IrcWidget):
        self.treeview = ttk.Treeview(irc_widget, show="tree", selectmode="extended")

    def add_user(self, nick: str) -> None:
        nicks = list(self.get_nicks())
        assert nick not in nicks
        nicks.append(nick)
        nicks.sort(key=str.casefold)
        self.treeview.insert("", nicks.index(nick), nick, text=nick)

    def remove_user(self, nick: str) -> None:
        self.treeview.delete(nick)

    def get_nicks(self) -> tuple[str, ...]:
        return self.treeview.get_children("")

    def set_nicks(self, nicks: list[str]) -> None:
        self.treeview.delete(*self.treeview.get_children(""))
        for nick in sorted(nicks, key=str.casefold):
            self.treeview.insert("", "end", nick, text=nick)


def _show_popup(title: str, text: str) -> None:
    try:
        if sys.platform == "win32":
            print("Sorry, no popups on windows yet :(")  # FIXME
        elif sys.platform == "darwin":
            # https://stackoverflow.com/a/41318195
            command = (
                "on run argv\n"
                "  display notification (item 2 of argv) with title (item 1 of argv)\n"
                "end run\n"
            )
            subprocess.call(["osascript", "-e", command, title, text])
        else:
            subprocess.call(["notify-send", f"[{title}] {text}"])
    except OSError:
        traceback.print_exc()


def _parse_privmsg(
    sender: str,
    message: str,
    self_nick: str,
    all_nicks: Sequence[str],
    *,
    pinged: bool = False,
) -> tuple[str, list[tuple[str, list[str]]]]:
    sent = sender.lower() == self_nick.lower()
    chunks = []

    # /me asdf --> "\x01ACTION asdf\x01"
    if message.startswith("\x01ACTION ") and message.endswith("\x01"):
        if sent:
            chunks.append((sender, ["self-nick"]))
        else:
            chunks.append((sender, ["other-nick"]))
        message = message[7:-1]  # keep the space
        sender = "*"

    for substring, base_tags in colors.parse_text(message):
        for subsubstring, nick_tag in backend.find_nicks(
            substring, self_nick, all_nicks
        ):
            tags = base_tags.copy()
            if nick_tag is not None:
                tags.append(nick_tag)
            tags.append("sent-privmsg" if sent else "received-privmsg")
            chunks.append((subsubstring, tags))

    if pinged:
        chunks = [(text, tags + ["pinged"]) for text, tags in chunks]
    return (sender, chunks)


class View:
    def __init__(self, irc_widget: IrcWidget, name: str, *, parent_view_id: str = ""):
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
        colors.config_tags(self.textwidget)

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
        _show_popup(self.view_name, popup_text)

    def mark_seen(self) -> None:
        self.notification_count = 0
        self._update_view_selector()
        self.irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(self.irc_widget.view_selector.item(self.view_id, "tags"))
        self.irc_widget.view_selector.item(
            self.view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

    def add_tag(self, tag: Literal["new_message", "pinged"]) -> None:
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
    core: backend.IrcCore  # no idea why mypy need this

    def __init__(self, irc_widget: IrcWidget, server_config: config.ServerConfig):
        super().__init__(irc_widget, server_config["host"])
        self.core = backend.IrcCore(server_config)
        self.extra_notifications = set(server_config["extra_notifications"])
        self._join_leave_hiding_config = server_config["join_leave_hiding"]

        self.core.start()
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
            if isinstance(view, ChannelView) and view.view_name.lower() == name.lower():
                return view
        return None

    def find_pm(self, nick: str) -> PMView | None:
        for view in self.get_subviews():
            # TODO: case insensitive
            if isinstance(view, PMView) and view.view_name == nick:
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

            if isinstance(event, backend.SelfJoined):
                channel_view = self.find_channel(event.channel)
                if channel_view is None:
                    channel_view = ChannelView(self, event.channel, event.nicklist)
                    self.irc_widget.add_view(channel_view)
                else:
                    # Can exist already, when has been disconnected from server
                    channel_view.userlist.set_nicks(event.nicklist)

                channel_view.show_topic(event.topic)
                if event.channel not in self.core.autojoin:
                    self.core.autojoin.append(event.channel)

            elif isinstance(event, backend.SelfParted):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                self.irc_widget.remove_view(channel_view)
                if event.channel in self.core.autojoin:
                    self.core.autojoin.remove(event.channel)

            elif isinstance(event, backend.SelfChangedNick):
                if self.irc_widget.get_current_view().server_view == self:
                    self.irc_widget.nickbutton.config(text=event.new)
                for view in self.get_subviews(include_server=True):
                    view.on_self_changed_nick(event.old, event.new)

            elif isinstance(event, backend.SelfQuit):
                self.irc_widget.after_cancel(next_call_id)
                self.irc_widget.remove_server(self)
                return

            elif isinstance(event, backend.UserJoined):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_join(event.nick)

            elif isinstance(event, backend.UserParted):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_part(event.nick, event.reason)

            elif isinstance(event, backend.UserQuit):
                for view in self.get_subviews(include_server=True):
                    if event.nick in view.get_relevant_nicks():
                        view.on_relevant_user_quit(event.nick, event.reason)

            elif isinstance(event, backend.UserChangedNick):
                for view in self.get_subviews(include_server=True):
                    if event.old in view.get_relevant_nicks():
                        view.on_relevant_user_changed_nick(event.old, event.new)

            elif isinstance(event, backend.SentPrivmsg):
                channel_view = self.find_channel(event.recipient)
                if channel_view is None:
                    assert not re.fullmatch(
                        backend.CHANNEL_REGEX, event.recipient
                    ), event.recipient
                    pm_view = self.find_pm(event.recipient)
                    if pm_view is None:
                        # start of a new PM conversation
                        pm_view = PMView(self, event.recipient)
                        self.irc_widget.add_view(pm_view)
                    pm_view.on_privmsg(self.core.nick, event.text)
                else:
                    channel_view.on_privmsg(self.core.nick, event.text)

            elif isinstance(event, backend.ReceivedPrivmsg):
                # sender and recipient are channels or nicks
                if event.recipient == self.core.nick:  # PM
                    pm_view = self.find_pm(event.sender)
                    if pm_view is None:
                        # start of a new PM conversation
                        pm_view = PMView(self, event.sender)
                        self.irc_widget.add_view(pm_view)
                    pm_view.on_privmsg(event.sender, event.text)
                    pm_view.add_tag("new_message")
                    pm_view.add_notification(event.text)

                else:
                    channel_view = self.find_channel(event.recipient)
                    assert channel_view is not None

                    pinged = any(
                        tag == "self-nick"
                        for substring, tag in backend.find_nicks(
                            event.text, self.core.nick, [self.core.nick]
                        )
                    )
                    channel_view.on_privmsg(event.sender, event.text, pinged=pinged)
                    channel_view.add_tag("pinged" if pinged else "new_message")
                    if pinged or (channel_view.view_name in self.extra_notifications):
                        channel_view.add_notification(f"<{event.sender}> {event.text}")

            elif isinstance(event, (backend.ServerMessage, backend.UnknownMessage)):
                self.server_view.add_message(
                    event.sender or "???", (" ".join([event.command] + event.args), [])
                )

            elif isinstance(event, backend.ConnectivityMessage):
                for view in self.get_subviews(include_server=True):
                    view.on_connectivity_message(event.message, error=event.is_error)

            elif isinstance(event, backend.TopicChanged):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_topic_changed(event.who_changed, event.topic)

            elif isinstance(event, backend.HostChanged):
                self.view_name = event.new
                for subview in self.get_subviews():
                    subview.close_log_file()
                    subview.open_log_file()

            else:
                # If mypy says 'error: unused "type: ignore" comment', you
                # forgot to check for some class
                print("can't happen")  # type: ignore

    def get_current_config(self) -> config.ServerConfig:
        channels = [
            view.view_name
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

    def show_config_dialog(self) -> None:
        new_config = config.show_connection_settings_dialog(
            transient_to=self.irc_widget.winfo_toplevel(),
            initial_config=self.get_current_config(),
        )
        if new_config is not None:
            self._join_leave_hiding_config = new_config["join_leave_hiding"]
            self.core.apply_config_and_reconnect(new_config)
            # TODO: autojoin setting would be better in right-click
            for subview in self.get_subviews():
                if (
                    isinstance(subview, ChannelView)
                    and subview.view_name not in self.core.autojoin
                ):
                    self.irc_widget.remove_view(subview)


class ChannelView(View):
    userlist: _UserList  # no idea why this is needed to avoid mypy error

    def __init__(self, server_view: ServerView, channel_name: str, nicks: list[str]):
        super().__init__(
            server_view.irc_widget, channel_name, parent_view_id=server_view.view_id
        )
        self.irc_widget.view_selector.item(
            self.view_id, image=server_view.irc_widget.channel_image
        )
        self.userlist = _UserList(server_view.irc_widget)
        self.userlist.set_nicks(nicks)
        self.open_log_file()

    def destroy_widgets(self) -> None:
        super().destroy_widgets()
        self.userlist.treeview.destroy()

    def on_privmsg(self, sender: str, message: str, pinged: bool = False) -> None:
        sender, chunks = _parse_privmsg(
            sender, message, self.server_view.core.nick, self.userlist.get_nicks()
        )
        self.add_message(sender, *chunks, pinged=pinged)

    def on_join(self, nick: str) -> None:
        self.userlist.add_user(nick)
        self.add_message(
            "*",
            (nick, ["other-nick"]),
            (f" joined {self.view_name}.", []),
            show_in_gui=self.server_view.should_show_join_leave_message(nick),
        )

    def on_part(self, nick: str, reason: str | None) -> None:
        self.userlist.remove_user(nick)
        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"
        self.add_message(
            "*",
            (nick, ["other-nick"]),
            (f" left {self.view_name}." + extra, []),
            show_in_gui=self.server_view.should_show_join_leave_message(nick),
        )

    def on_self_changed_nick(self, old: str, new: str) -> None:
        super().on_self_changed_nick(old, new)
        self.userlist.remove_user(old)
        self.userlist.add_user(new)

    def get_relevant_nicks(self) -> tuple[str, ...]:
        return self.userlist.get_nicks()

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        super().on_relevant_user_changed_nick(old, new)
        self.userlist.remove_user(old)
        self.userlist.add_user(new)

    def on_relevant_user_quit(self, nick: str, reason: str | None) -> None:
        super().on_relevant_user_quit(nick, reason)
        self.userlist.remove_user(nick)

    def show_topic(self, topic: str) -> None:
        self.add_message("*", (f"The topic of {self.view_name} is: {topic}", []))

    def on_topic_changed(self, nick: str, topic: str) -> None:
        if nick == self.server_view.core.nick:
            nick_tag = "self-nick"
        else:
            nick_tag = "other-nick"
        self.add_message(
            "*",
            (nick, [nick_tag]),
            (f" changed the topic of {self.view_name}: {topic}", []),
        )


# PM = private messages, also known as DM = direct messages
class PMView(View):
    def __init__(self, server_view: ServerView, nick: str):
        super().__init__(
            server_view.irc_widget, nick, parent_view_id=server_view.view_id
        )
        self.irc_widget.view_selector.item(
            self.view_id, image=server_view.irc_widget.pm_image
        )
        self.open_log_file()

    def on_privmsg(self, sender: str, message: str) -> None:
        sender, chunks = _parse_privmsg(
            sender,
            message,
            self.server_view.core.nick,
            [self.server_view.core.nick, self.view_name],
        )
        self.add_message(sender, *chunks)

    # quit isn't perfect: no way to notice a person quitting if not on a same
    # channel with the user
    def get_relevant_nicks(self) -> list[str]:
        return [self.view_name]

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        super().on_relevant_user_changed_nick(old, new)
        self.view_name = new

        self.close_log_file()
        self.open_log_file()
