from __future__ import annotations
from typing import TYPE_CHECKING
import tkinter,sys

from functools import partial
from tkinter import messagebox, ttk
from typing import Any

if TYPE_CHECKING:
    from mantaray.views  import ServerView, ChannelView, PMView
    from mantaray.gui import IrcWidget
    from typing_extensions import TypeAlias


if sys.platform == "darwin":
    RIGHT_CLICK_BINDINGS = ["<Button-2>", "<Control-Button-1>"]
else:
    RIGHT_CLICK_BINDINGS = ["<Button-3>"]


# The same menu widget is reused, so that we don't need to
# worry about destroying it when we're done.
_global_menu: tkinter.Menu | None = None


def _clear_and_get_menu() -> tkinter.Menu:
    global _global_menu
    if _global_menu is None:
        _global_menu = tkinter.Menu(tearoff=False)
    _global_menu.delete(0, "end")
    return _global_menu


if TYPE_CHECKING:
    # Compatible with any tkinter event. Use this when you don't care about
    # what type of widget the event came from.
    _AnyEvent: TypeAlias = tkinter.Event[tkinter.Misc]


def _show_menu(menu: tkinter.Menu, event: _AnyEvent) -> None:
        menu.tk_popup(event.x_root + 5, event.y_root)


def server_right_click(event: _AnyEvent, irc_widget: IrcWidget, view: ServerView | None) -> None:
    menu = _clear_and_get_menu()

    if view is not None:
        assert view.irc_widget == irc_widget
        menu.add_command(
            label="Server settings...", command=view.show_config_dialog
        )
        menu.add_command(
            label="Leave this server",
            command=partial(irc_widget.leave_server, view),
            # To leave the last server, you need to close window instead
            state=("disabled" if len(irc_widget.get_server_views()) == 1 else "normal"),
        )

    menu.add_command(
        label="Connect to a new server...", command=irc_widget.add_server
    )
    _show_menu(menu, event)


def channel_view_right_click(event: _AnyEvent, view: ChannelView) -> None:
    def toggle_autojoin(*junk: object) -> None:
        if view.channel_name in view.server_view.settings.joined_channels:
            view.server_view.settings.joined_channels.remove(view.channel_name)
        else:
            view.server_view.settings.joined_channels.append(view.channel_name)
            view.irc_widget.sort_settings_according_to_gui()
        view.server_view.settings.save()

    def toggle_extra_notifications(*junk: object) -> None:
        view.server_view.settings.extra_notifications ^= {view.channel_name}
        view.server_view.settings.save()

    autojoin_var = tkinter.BooleanVar(
        value=(view.channel_name in view.server_view.settings.joined_channels)
    )
    extra_notif_var = tkinter.BooleanVar(
        value=(view.channel_name in view.server_view.settings.extra_notifications)
    )

    autojoin_var.trace_add("write", toggle_autojoin)
    extra_notif_var.trace_add("write", toggle_extra_notifications)

    menu = _clear_and_get_menu()
    menu._garbage_collection_is_lol = (autojoin_var, extra_notif_var)  # type: ignore
    menu.add_checkbutton(
        label="Join when Mantaray starts", variable=autojoin_var
    )
    menu.add_checkbutton(
        label="Show notifications for all messages", variable=extra_notif_var
    )
    menu.add_command(
        label="Part this channel",
        command=(lambda: view.server_view.core.send(f"PART {view.channel_name}")),
    )
    _show_menu(menu, event)


def pm_view_right_click(event: _AnyEvent, view: PMView) -> None:
    menu = _clear_and_get_menu()
    menu.add_command(
        label="Close", command=(lambda: view.irc_widget.remove_view(view))
    )
    _show_menu(menu, event)


def nick_right_click(event: _AnyEvent, server_view: ServerView, nick: str) -> None:
    menu = _clear_and_get_menu()
    menu.add_command(
        label=f"Send private message to {nick}",
        command=(lambda: server_view.find_or_open_pm(nick, select=True)),
    )
    _show_menu(menu, event)
