from __future__ import annotations
import queue
import traceback
import time
import sys
import tkinter
import subprocess
from playsound import playsound  # type: ignore
from tkinter import ttk
from typing import Any, Sequence, TYPE_CHECKING, IO

from mantaray import backend, textwidget_tags, config, received

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

    for substring, base_tags in textwidget_tags.parse_text(message):
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


def _add_tags_to_urls(textwidget: tkinter.Text, start: str, end: str) -> None:
    search_start = start
    while True:
        match_start = textwidget.search(
            r"\mhttps?://[a-z0-9:]", search_start, end, nocase=True, regexp=True
        )
        if not match_start:  # empty string means not found
            break

        url = textwidget.get(match_start, f"{match_start} lineend")

        url = url.split(" ")[0]
        url = url.split("'")[0]
        url = url.split('"')[0]
        url = url.split("`")[0]

        # URL, and URL. URL? URL! (also URL). (also URL.)
        url = url.rstrip(".,?!")
        if "(" not in url:  # urls can contain spaces (e.g. wikipedia)
            url = url.rstrip(")")
        url = url.rstrip(".,?!")

        match_end = f"{match_start} + {len(url)} chars"
        textwidget.tag_add("url", match_start, match_end)
        search_start = f"{match_end} + 1 char"


class View:
    def __init__(self, irc_widget: IrcWidget, name: str, *, parent_view_id: str = ""):
        self.irc_widget = irc_widget
        self.view_id = irc_widget.view_selector.insert(parent_view_id, "end", text=name)
        self._name = name
        self.notification_count = 0

        self.textwidget = tkinter.Text(
            irc_widget.textwidget_container,
            width=1,  # minimum, can stretch bigger
            height=1,  # minimum, can stretch bigger
            font=irc_widget.font,
            state="disabled",
            takefocus=True,
            tabs=(150, "right", 160, "left"),
        )
        # TODO: a vertical line you can drag, like in hexchat
        self.textwidget.tag_config("text", lmargin2=160)
        self.textwidget.bind("<Button-1>", (lambda e: self.textwidget.focus()))
        textwidget_tags.config_tags(self.textwidget)

        self.log_file: IO[str] | None = None
        self.reopen_log_file()

    def get_log_name(self) -> str:
        raise NotImplementedError

    def close_log_file(self) -> None:
        if self.log_file is not None:
            self.irc_widget.log_manager.close_log_file(self.log_file)

    def reopen_log_file(self) -> None:
        self.close_log_file()
        self.log_file = self.irc_widget.log_manager.open_log_file(
            self.server_view.view_name, self.get_log_name()
        )

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
        if self.server_view.audio_notification:
            try:
                playsound("mantaray/audio/notify.mp3", False)
            except Exception:
                traceback.print_exc()

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

            if sender == "*":
                sender_tags = []
            elif sender == self.server_view.core.nick:
                sender_tags = ["self-nick"]
            else:
                sender_tags = ["other-nick"]

            self.textwidget.config(state="normal")
            start = self.textwidget.index("end - 1 char")
            self.textwidget.insert("end", time.strftime("[%H:%M]"))
            self.textwidget.insert("end", "\t")
            self.textwidget.insert("end", sender, sender_tags)
            self.textwidget.insert("end", "\t")

            if chunks:
                insert_args: list[Any] = []
                for text, tags in chunks:
                    insert_args.append(text)
                    insert_args.append(tags + ["text"])
                self.textwidget.insert("end", *insert_args)

            self.textwidget.insert("end", "\n")
            if pinged:
                self.textwidget.tag_add("pinged", start, "end - 1 char")
            self.textwidget.config(state="disabled")

            _add_tags_to_urls(self.textwidget, start, "end")

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


class ServerView(View):
    core: backend.IrcCore  # no idea why mypy need this

    def __init__(
        self, irc_widget: IrcWidget, server_config: config.ServerConfig, verbose: bool
    ):
        super().__init__(irc_widget, server_config["host"])
        self.core = backend.IrcCore(server_config, verbose=verbose)
        self.extra_notifications = set(server_config["extra_notifications"])
        self.audio_notification = server_config["audio_notification"]
        self._join_leave_hiding_config = server_config["join_leave_hiding"]

        self.core.start_threads()

    def get_log_name(self) -> str:
        # Log to file named logs/foobar/server.log.
        #
        # Not a problem if someone is nicknamed "server", because ServerView
        # opens its log file first.
        return "server"

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

    def find_pm(self, nick: str) -> PMView | None:
        for view in self.get_subviews():
            if (
                isinstance(view, PMView)
                and view.nick_of_other_user.lower() == nick.lower()
            ):
                return view
        return None

    def handle_events(self) -> None:
        """Call this once to start processing events from the core.

        Do NOT call it before the view has been added to the IRC widget.
        """
        # this is here so that this will be called again, even if
        # something raises an error this time
        next_call_id = self.irc_widget.after(100, self.handle_events)

        while True:
            try:
                event = self.core.event_queue.get(block=False)
            except queue.Empty:
                break

            should_keep_going = received.handle_event(event, self)
            if not should_keep_going:
                self.irc_widget.after_cancel(next_call_id)
                self.irc_widget.remove_server(self)
                break

    def get_current_config(self) -> config.ServerConfig:
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
            "audio_notification": self.audio_notification,
        }

    def show_config_dialog(self) -> None:
        new_config = config.show_connection_settings_dialog(
            transient_to=self.irc_widget.winfo_toplevel(),
            initial_config=self.get_current_config(),
        )
        if new_config is not None:
            self._join_leave_hiding_config = new_config["join_leave_hiding"]
            self.core.apply_config_and_reconnect(new_config)
            self.audio_notification = new_config["audio_notification"]
            # TODO: autojoin setting would be better in right-click
            for subview in self.get_subviews():
                if (
                    isinstance(subview, ChannelView)
                    and subview.channel_name not in self.core.autojoin
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

    # Includes the '#' character(s), e.g. '#devuan' or '##learnpython'
    # Same as view_name, but only channels have this attribute, can clarify things a lot
    @property
    def channel_name(self) -> str:
        return self.view_name

    def get_log_name(self) -> str:
        return self.channel_name

    def destroy_widgets(self) -> None:
        super().destroy_widgets()
        self.userlist.treeview.destroy()

    def on_privmsg(self, sender: str, message: str, pinged: bool = False) -> None:
        sender, chunks = _parse_privmsg(
            sender, message, self.server_view.core.nick, self.userlist.get_nicks()
        )
        self.add_message(sender, *chunks, pinged=pinged)


# PM = private messages, also known as DM = direct messages
class PMView(View):
    def __init__(self, server_view: ServerView, nick: str):
        super().__init__(
            server_view.irc_widget, nick, parent_view_id=server_view.view_id
        )
        self.irc_widget.view_selector.item(
            self.view_id, image=server_view.irc_widget.pm_image
        )

    # Same as view_name, but only PM views have this attribute
    # Do not set view_name directly, if you want log file name to update too
    @property
    def nick_of_other_user(self) -> str:
        return self.view_name

    def set_nick_of_other_user(self, new_nick: str) -> None:
        self.view_name = new_nick
        self.reopen_log_file()

    def get_log_name(self) -> str:
        return self.nick_of_other_user

    def on_privmsg(self, sender: str, message: str) -> None:
        sender, chunks = _parse_privmsg(
            sender,
            message,
            self.server_view.core.nick,
            [self.server_view.core.nick, self.nick_of_other_user],
        )
        self.add_message(sender, *chunks)
