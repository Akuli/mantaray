from __future__ import annotations
import time
import tkinter
from tkinter import ttk
from typing import Sequence, TYPE_CHECKING

from irc_client import colors

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
    def __init__(self, irc_widget: IrcWidget):
        self.irc_widget = irc_widget
        self.view_id = irc_widget.view_selector.insert("", "end")

        # width and height are minimums, can stretch bigger
        self.textwidget = tkinter.Text(
            irc_widget, width=1, height=1, state="disabled", takefocus=True
        )
        self.textwidget.bind("<Button-1>", (lambda e: self.textwidget.focus()))
        colors.config_tags(self.textwidget)

    def destroy_widgets(self) -> None:
        self.textwidget.destroy()

    def add_message(
        self,
        sender: str,
        message: str,
        *,
        nicks_to_highlight: Sequence[str] = (),
        pinged: bool = False,
    ) -> None:
        """Add a message to self.textwidget."""
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
    def __init__(self, irc_widget: IrcWidget, hostname: str):
        super().__init__(irc_widget)
        irc_widget.view_selector.item(self.view_id, text=hostname)


class ChannelView(View):
    def __init__(self, irc_widget: IrcWidget, name: str, nicks: list[str]):
        super().__init__(irc_widget)
        self.irc_widget.view_selector.item(
            self.view_id, text=name, image=irc_widget.channel_image
        )
        self.userlist = _UserList(irc_widget)
        self.userlist.set_nicks(nicks)

    def destroy_widgets(self) -> None:
        super().destroy_widgets()
        self.userlist.treeview.destroy()

    @property
    def name(self) -> str:
        return self.irc_widget.view_selector.item(self.view_id, "text")

    def on_privmsg(self, sender: str, message: str, pinged: bool = False) -> None:
        self.add_message(
            sender, message, nicks_to_highlight=self.userlist.get_nicks(), pinged=pinged
        )

    def on_join(self, nick: str) -> None:
        self.userlist.add_user(nick)
        self.add_message("*", f"{colors.color_nick(nick)} joined {self.name}.")

    def on_part(self, nick: str, reason: str | None) -> None:
        self.userlist.remove_user(nick)
        msg = f"{colors.color_nick(nick)} left {self.name}."
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
        self.add_message("*", f"The topic of {self.name} is: {topic}")

    def on_topic_changed(self, nick: str, topic: str) -> None:
        self.add_message(
            "*", f"{colors.color_nick(nick)} changed the topic of {self.name}: {topic}"
        )


# PM = private messages, also known as DM = direct messages
class PMView(View):
    def __init__(self, irc_widget: IrcWidget, nick: str):
        super().__init__(irc_widget)
        self.irc_widget.view_selector.item(
            self.view_id, text=nick, image=irc_widget.pm_image
        )

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
