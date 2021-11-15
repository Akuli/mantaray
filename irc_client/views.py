from __future__ import annotations
import re
import queue
import traceback
import time
import tkinter
from tkinter import ttk
from typing import Sequence, TYPE_CHECKING, IO

from irc_client import backend, colors, config

if TYPE_CHECKING:
    from irc_client.gui import IrcWidget


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


class View:
    def __init__(self, irc_widget: IrcWidget, *, parent_view_id: str = ""):
        self.irc_widget = irc_widget
        self.view_id = irc_widget.view_selector.insert(parent_view_id, "end")

        # width and height are minimums, can stretch bigger
        self.textwidget = tkinter.Text(
            irc_widget, width=1, height=1, state="disabled", takefocus=True
        )
        self.textwidget.bind("<Button-1>", (lambda e: self.textwidget.focus()))
        colors.config_tags(self.textwidget)

        self.log_file: IO[str] | None = None

    def destroy_view(self) -> None:
        self.textwidget.destroy()
        if self.log_file is not None:
            print("*** LOGGING ENDS", time.asctime(), file=self.log_file, flush=True)
            self.log_file.close()

    @property
    def server_view(self) -> ServerView:
        parent_id = self.irc_widget.view_selector.parent(self.view_id)
        parent_view = self.irc_widget.views_by_id[parent_id]
        assert isinstance(parent_view, ServerView)
        return parent_view

    def add_message(
        self,
        sender: str,
        message: str,
        *,
        nicks_to_highlight: Sequence[str] = (),
        pinged: bool = False,
    ) -> None:
        # scroll down all the way if the user hasn't scrolled up manually
        do_the_scroll = self.textwidget.yview()[1] == 1.0

        # nicks are limited to 16 characters at least on freenode
        # len(sender) > 16 is not a problem:
        #    >>> ' ' * (-3)
        #    ''
        padding = " " * (16 - len(sender))

        self.textwidget.config(state="normal")
        self.textwidget.insert("end", time.strftime("[%H:%M]") + " " + padding)
        colors.add_text(self.textwidget, colors.color_nick(sender))
        self.textwidget.insert("end", " | ")
        colors.add_text(
            self.textwidget, message, known_nicks=nicks_to_highlight, pinged=pinged
        )
        self.textwidget.insert("end", "\n")
        self.textwidget.config(state="disabled")

        if self.log_file is not None:
            print(time.asctime(), sender, message, sep="\t", file=self.log_file, flush=True)

        if do_the_scroll:
            self.textwidget.see("end")

    def on_connectivity_message(self, message: str, *, error: bool = False) -> None:
        if error:
            self.add_message("", colors.ERROR_PREFIX + message)
        else:
            self.add_message("", colors.INFO_PREFIX + message)

    def on_self_changed_nick(self, old: str, new: str) -> None:
        # notify about the nick change everywhere, by putting this to base class
        self.add_message("*", f"You are now known as {colors.color_nick(new)}.")

    def get_relevant_nicks(self) -> Sequence[str]:
        return []

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        self.add_message(
            "*", f"{colors.color_nick(old)} is now known as {colors.color_nick(new)}."
        )

    def on_relevant_user_quit(self, nick: str, reason: str | None) -> None:
        msg = f"{colors.color_nick(nick)} quit."
        if reason is not None:
            msg += f" ({reason})"
        self.add_message("*", msg)


class ServerView(View):
    def __init__(self, irc_widget: IrcWidget, server_config: config.ServerConfig):
        super().__init__(irc_widget)
        irc_widget.view_selector.item(self.view_id, text=server_config["host"])
        self.core = backend.IrcCore(server_config)
        self.extra_notifications = set(server_config["extra_notifications"])

        self.core.start()
        self.handle_events()

    def open_log_file(self, name: str) -> IO[str] | None:
        name = re.sub(r"[^A-Za-z0-9-_#]", "", name)
        (self.irc_widget.log_dir / self.core.host).mkdir(parents=True, exist_ok=True)
        try:
            file = (self.irc_widget.log_dir / self.core.host / (name + ".log")).open(
                "a", encoding="utf-8"
            )
        except OSError:
            traceback.print_exc()
            return None
        else:
            print("*** LOGGING BEGINS", time.asctime(), file=file, flush=True)
            return file

    @property
    def server_view(self) -> ServerView:
        return self

    def get_subviews(self, *, include_server: bool = False) -> list[View]:
        result: list[View] = []
        if include_server:
            result.append(self)
        for view_id in self.irc_widget.view_selector.get_children(self.view_id):
            result.append(self.irc_widget.views_by_id[view_id])
        return result

    def find_channel(self, name: str) -> ChannelView | None:
        for view in self.get_subviews():
            if isinstance(view, ChannelView) and view.channel_name == name:
                return view
        return None

    def find_pm(self, nick: str) -> PMView | None:
        for view in self.get_subviews():
            # TODO: case insensitive
            if isinstance(view, PMView) and view.nick == nick:
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
                    assert not re.fullmatch(backend.CHANNEL_REGEX, event.recipient)
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
                    self.irc_widget.new_message_notify(pm_view, event.text)

                else:
                    channel_view = self.find_channel(event.recipient)
                    assert channel_view is not None

                    pinged = bool(backend.find_nicks(event.text, [self.core.nick]))
                    channel_view.on_privmsg(event.sender, event.text, pinged=pinged)
                    if pinged or (
                        channel_view.channel_name in self.extra_notifications
                    ):
                        self.irc_widget.new_message_notify(
                            channel_view, f"<{event.sender}> {event.text}"
                        )

            # TODO: do something to unknown messages!! maybe log in backend?
            elif isinstance(event, (backend.ServerMessage, backend.UnknownMessage)):
                self.server_view.add_message(
                    event.sender or "???", " ".join(event.args)
                )

            elif isinstance(event, backend.ConnectivityMessage):
                for view in self.get_subviews(include_server=True):
                    view.on_connectivity_message(event.message, error=event.is_error)

            elif isinstance(event, backend.TopicChanged):
                channel_view = self.find_channel(event.channel)
                assert channel_view is not None
                channel_view.on_topic_changed(event.who_changed, event.topic)

            else:
                # If mypy says 'error: unused "type: ignore" comment', you
                # forgot to check for some class
                print("can't happen")  # type: ignore

    def get_current_config(self) -> config.ServerConfig:
        return {
            "host": self.core.host,
            "port": self.core.port,
            "ssl": self.core.ssl,
            "nick": self.core.nick,
            "username": self.core.username,
            "realname": self.core.realname,
            "joined_channels": self.core.autojoin.copy(),
            "extra_notifications": list(self.extra_notifications),
        }


class ChannelView(View):
    userlist: _UserList  # no idea why this is needed to avoid mypy error

    def __init__(self, server_view: ServerView, name: str, nicks: list[str]):
        super().__init__(server_view.irc_widget, parent_view_id=server_view.view_id)
        self.irc_widget.view_selector.item(
            self.view_id, text=name, image=server_view.irc_widget.channel_image
        )
        self.userlist = _UserList(server_view.irc_widget)
        self.userlist.set_nicks(nicks)

        self.log_file = self.server_view.open_log_file(name)

    def destroy_view(self) -> None:
        super().destroy_view()
        self.userlist.treeview.destroy()

    @property
    def channel_name(self) -> str:
        return self.irc_widget.view_selector.item(self.view_id, "text")

    def on_privmsg(self, sender: str, message: str, pinged: bool = False) -> None:
        self.add_message(
            sender, message, nicks_to_highlight=self.userlist.get_nicks(), pinged=pinged
        )

    def on_join(self, nick: str) -> None:
        self.userlist.add_user(nick)
        self.add_message("*", f"{colors.color_nick(nick)} joined {self.channel_name}.")

    def on_part(self, nick: str, reason: str | None) -> None:
        self.userlist.remove_user(nick)
        msg = f"{colors.color_nick(nick)} left {self.channel_name}."
        if reason is not None:
            msg += f" ({reason})"
        self.add_message("*", msg)

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
        self.add_message("*", f"The topic of {self.channel_name} is: {topic}")

    def on_topic_changed(self, nick: str, topic: str) -> None:
        self.add_message(
            "*",
            f"{colors.color_nick(nick)} changed the topic of {self.channel_name}: {topic}",
        )


# PM = private messages, also known as DM = direct messages
class PMView(View):
    def __init__(self, server_view: ServerView, nick: str):
        super().__init__(server_view.irc_widget, parent_view_id=server_view.view_id)
        self.irc_widget.view_selector.item(
            self.view_id, text=nick, image=self.irc_widget.pm_image
        )

        # FIXME: reopen log file when nick changes
        self.log_file = self.server_view.open_log_file(nick)

    @property
    def nick(self) -> str:
        return self.irc_widget.view_selector.item(self.view_id, "text")

    def on_privmsg(self, sender: str, message: str) -> None:
        self.add_message(sender, message)

    # quit isn't perfect: no way to notice a person quitting if not on a same
    # channel with the user
    def get_relevant_nicks(self) -> list[str]:
        return [self.nick]

    def on_relevant_user_changed_nick(self, old: str, new: str) -> None:
        super().on_relevant_user_changed_nick(old, new)
        self.irc_widget.view_selector.item(self.view_id, text=new)
