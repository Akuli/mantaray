from __future__ import annotations

import logging
import subprocess
import sys
import time
import tkinter
import webbrowser
from tkinter import ttk
from typing import IO, TYPE_CHECKING, Any

from playsound import playsound

from mantaray import backend, config, received, textwidget_tags
from mantaray.history import History
from mantaray.right_click_menus import RIGHT_CLICK_BINDINGS, nick_right_click

if TYPE_CHECKING:
    from typing_extensions import Literal

    from mantaray.gui import IrcWidget


class _UserList:
    def __init__(self, server_view: ServerView) -> None:
        self._server_view = server_view
        self.treeview = ttk.Treeview(
            server_view.irc_widget, show="tree", selectmode="extended"
        )
        self.treeview.tag_configure("away", foreground="#95968c")
        for right_click in RIGHT_CLICK_BINDINGS:
            self.treeview.bind(right_click, self._on_right_click)

    def _on_right_click(self, event: tkinter.Event[ttk.Treeview]) -> None:
        nick = self.treeview.identify_row(event.y)
        if nick:
            self.treeview.selection_set(nick)
            nick_right_click(event, self._server_view, nick)

    def add_user(self, nick: str) -> None:
        nicks = list(self.get_nicks())
        assert nick not in nicks
        nicks.append(nick)
        nicks.sort(key=str.casefold)
        self.treeview.insert("", nicks.index(nick), nick, text=nick)

    def remove_user(self, nick: str) -> None:
        self.treeview.delete(nick)

    def change_nick(self, old_nick: str, new_nick: str) -> None:
        # "OldNick (away: foo bar)" --> "NewNick (away: foo bar)"
        old_text = self.treeview.item(old_nick, "text")
        assert old_text.startswith(old_nick)
        new_text = new_nick + old_text[len(old_nick) :]
        tags = self.treeview.item(old_nick, "tags")

        self.remove_user(old_nick)
        self.add_user(new_nick)
        self.treeview.item(new_nick, text=new_text, tags=tags)

    def get_nicks(self) -> tuple[str, ...]:
        return self.treeview.get_children("")

    # Does not preserve away statuses
    def set_nicks(self, nicks: list[str]) -> None:
        self.treeview.delete(*self.treeview.get_children(""))
        for nick in sorted(nicks, key=str.casefold):
            self.treeview.insert("", "end", nick, text=nick)

    def set_away(self, nick: str, is_away: bool, reason: str | None = None) -> None:
        if is_away:
            if reason is None:
                # Away, but for unknown reason.
                #
                # It is possible to query the reason by sending WHOIS, but we would
                # need to do it for every away user on the channel. Bad idea when there
                # are many users.
                text = f"{nick} (away)"
            else:
                text = f"{nick} (away: {reason})"
            self.treeview.item(nick, text=text, tags=["away"])
        else:
            assert reason is None
            self.treeview.item(nick, text=nick, tags=[])


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
        logging.exception("error showing notification popup")


class MessagePart:
    def __init__(self, text: str, *, tags: list[str] = []):
        self.text = text
        self.tags = tags.copy()


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
            font=irc_widget.settings.font,
            state="disabled",
            takefocus=True,
            tabs=(150, "right", 160, "left"),
        )
        # TODO: a vertical line you can drag, like in hexchat
        self.textwidget.tag_config("text", lmargin2=160)
        textwidget_tags.config_tags(
            self.textwidget, self._on_link_leftclick, self._on_link_rightclick
        )

        self.history = History(self.textwidget)

        self.log_file: IO[str] | None = None
        self.reopen_log_file()

    def _on_link_leftclick(
        self, event: tkinter.Event[tkinter.Text], tag: str, text: str
    ) -> None:
        if tag == "url":
            webbrowser.open(text)
        elif tag == "other-nick":
            self.server_view.find_or_open_pm(text, select_existing=True)
        else:
            raise NotImplementedError(tag)

    def _on_link_rightclick(
        self, event: tkinter.Event[tkinter.Text], tag: str, text: str
    ) -> None:
        if tag == "other-nick":
            nick_right_click(event, self.server_view, text)

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
        if self.server_view.settings.audio_notification:
            try:
                playsound("mantaray/audio/notify.mp3", False)
            except Exception:
                logging.exception("can't play notify sound")

        _show_popup(self.view_name, popup_text)

    def mark_seen(self) -> None:
        self.notification_count = 0
        self._update_view_selector()
        self.irc_widget.event_generate("<<NotificationCountChanged>>")

        old_tags = set(self.irc_widget.view_selector.item(self.view_id, "tags"))
        self.irc_widget.view_selector.item(
            self.view_id, tags=list(old_tags - {"new_message", "pinged"})
        )

    def add_view_selector_tag(self, tag: Literal["new_message", "pinged"]) -> None:
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

    # for tests
    def get_text(self) -> str:
        return self.textwidget.get("1.0", "end")

    @property
    def server_view(self) -> ServerView:
        parent_id = self.irc_widget.view_selector.parent(self.view_id)
        parent_view = self.irc_widget.views_by_id[parent_id]
        assert isinstance(parent_view, ServerView)
        return parent_view

    def add_message(
        self,
        message: str | list[MessagePart],
        sender: str = "*",
        *,
        sender_tag: str | None = None,
        tag: Literal["info", "error", "privmsg"] = "info",
        show_in_gui: bool = True,
        pinged: bool = False,
        history_id: int | None = None,
    ) -> None:
        if isinstance(message, str):
            message = [MessagePart(message)]

        if show_in_gui:
            # scroll down all the way if the user hasn't scrolled up manually
            do_the_scroll = self.textwidget.yview()[1] == 1.0

            if history_id is not None:
                # Without gravity, the mark stays at the end as text is inserted
                self.textwidget.mark_set(f"history-start-{history_id}", "end - 1 char")
                self.textwidget.mark_gravity(f"history-start-{history_id}", "left")

            self.textwidget.config(state="normal")
            start = self.textwidget.index("end - 1 char")
            self.textwidget.insert("end", time.strftime("[%H:%M]"))
            self.textwidget.insert("end", "\t")
            self.textwidget.insert(
                "end", sender, [] if sender_tag is None else [sender_tag]
            )
            self.textwidget.insert("end", "\t")

            if message:
                insert_args: list[Any] = []
                for part in message:
                    insert_args.append(part.text)
                    insert_args.append(part.tags + ["text", tag])
                self.textwidget.insert("end", *insert_args)

            self.textwidget.insert("end", "\n")
            if pinged:
                self.textwidget.tag_add("pinged", start, "end - 1 char")
            self.textwidget.config(state="disabled")

            if history_id is not None:
                self.textwidget.mark_set(f"history-end-{history_id}", "end - 1 char")
                self.textwidget.mark_gravity(f"history-end-{history_id}", "left")

            textwidget_tags.find_and_tag_urls(self.textwidget, start, "end")

            if do_the_scroll:
                self.textwidget.see("end")

        if self.log_file is not None:
            print(
                time.asctime(),
                sender,
                "".join(part.text for part in message),
                sep="\t",
                file=self.log_file,
                flush=True,
            )


class ServerView(View):
    # help mypy with some weird errors...
    core: backend.IrcCore

    def __init__(self, irc_widget: IrcWidget, settings: config.ServerSettings):
        super().__init__(irc_widget, settings.host)
        self.settings = settings
        self.core = backend.IrcCore(settings, verbose=irc_widget.verbose)

        # A bit weird, but "/join #foo" causes mantaray to join #foo automatically
        # when started only if joining a channel named #foo succeeds. This only
        # applies to the /join command, because if you use the GUI to join a channel,
        # you likely want to also use the GUI to configure automatic joining.
        self.last_slash_join_channel: str | None = None

        # Similarly, we display the resulting status of "/away <status>" when the
        # server tells us that it successfully marked the user as away.
        self.last_away_status: str | None = None

    def _run_core(self) -> None:
        self.core.run_one_step()

        if self.core.quitting_finished():
            self.irc_widget.remove_server(self)
            return

        for event in self.core.get_events():
            try:
                received.handle_event(event, self)
            except Exception:
                logging.exception(f"error while handling event: {event}")

        self.irc_widget.after(50, self._run_core)

    def start_running(self) -> None:
        self._run_core()

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
            n.lower() for n in self.settings.join_leave_hiding["exception_nicks"]
        )
        return self.settings.join_leave_hiding["show_by_default"] ^ is_exceptional

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

    def find_or_open_pm(self, nick: str, *, select_existing: bool = False) -> PMView:
        existing_view = self.find_pm(nick)
        if existing_view is not None:
            if select_existing:
                self.irc_widget.view_selector.selection_set(existing_view.view_id)
            return existing_view

        new_view = PMView(self, nick)
        self.irc_widget.add_view(new_view)  # selects the view
        return new_view

    def show_config_dialog(self) -> None:
        user_clicked_reconnect = config.show_connection_settings_dialog(
            transient_to=self.irc_widget.winfo_toplevel(),
            settings=self.settings,
            connecting_to_new_server=False,
        )
        if user_clicked_reconnect:
            self.core.reconnect()


class ChannelView(View):
    def __init__(self, server_view: ServerView, channel_name: str, nicks: list[str]):
        super().__init__(
            server_view.irc_widget, channel_name, parent_view_id=server_view.view_id
        )
        self.irc_widget.view_selector.item(
            self.view_id, image=server_view.irc_widget.channel_image
        )
        self.userlist = _UserList(server_view)
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

    def get_log_name(self) -> str:
        return self.nick_of_other_user
