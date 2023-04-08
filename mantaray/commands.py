"""This file handles commands like /join."""
from __future__ import annotations
import inspect
import re
from typing import Callable
from tkinter import messagebox

from mantaray.views import View, ChannelView, PMView
from mantaray.backend import IrcCore


def _send_privmsg(
    view: View, core: IrcCore, message: str, *, history_id: int | None = None
) -> None:
    if isinstance(view, ChannelView):
        core.send_privmsg(view.channel_name, message, history_id=history_id)
    elif isinstance(view, PMView):
        core.send_privmsg(view.nick_of_other_user, message, history_id=history_id)
    else:
        view.add_message(
            "You can't send messages here. Join a channel instead and send messages there.",
            tag="error",
            history_id=history_id,
        )


def handle_command(view: View, core: IrcCore, entry_text: str, history_id: int) -> None:
    if not entry_text:
        return

    if re.fullmatch("/[A-Za-z]+( .*)?", entry_text):
        try:
            func = _commands[entry_text.split()[0].lower()]
        except KeyError:
            view.add_message(
                f"No command named '{entry_text.split()[0]}'",
                tag="error",
                history_id=history_id,
            )
            return

        view_arg, core_arg, *params = inspect.signature(func).parameters.values()
        assert all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
        required_params = [p for p in params if p.default == inspect.Parameter.empty]

        # Last arg can contain spaces
        # Do not pass maxsplit=0 as that means "/lol asdf" --> ["/lol asdf"]
        command_name, *args = entry_text.rstrip().split(maxsplit=max(len(params), 1))
        if len(args) < len(required_params) or len(args) > len(params):
            usage = command_name
            for p in params:
                if p in required_params:
                    usage += f" <{p.name}>"
                else:
                    usage += f" [<{p.name}>]"
            view.add_message("Usage: " + usage, tag="error", history_id=history_id)
        else:
            func(view, core, *args)

        return

    if entry_text.startswith("//"):
        entry_text = entry_text[1:]

    lines = [line for line in entry_text.split("\n") if line]
    if len(lines) > 3:
        # TODO: add button that pastebins?
        result = messagebox.askyesno(
            "Send multiple lines",
            "Do you really want to send many lines of text as separate messages?",
            detail=(
                f"You are about to send the {len(lines)} lines of text."
                f" It will be sent as {len(lines)} separate messages, one line per message."
                " Sending many messages like this is usually considered bad style,"
                " and it's often better to use a pastebin site instead."
                " Are you sure you want to do it?"
            ),
        )
        if not result:
            return

    for line in lines:
        _send_privmsg(view, core, line, history_id=history_id)


def _define_commands() -> dict[str, Callable[..., None]]:
    # Channel is required, and not assumed to be the current channel view.
    # So when you have been kicked, you will have to type the current channel
    # name manually to rejoin, which is good because it might give you time
    # to calm down a bit before you continue ranting.
    def join(view: View, core: IrcCore, channel: str) -> None:
        core.send(f"JOIN {channel}")

    def part(view: View, core: IrcCore, channel: str | None = None) -> None:
        if channel is None:
            if isinstance(view, ChannelView):
                channel = view.channel_name
            else:
                view.add_message("Usage: /part [<channel>]")
                view.add_message(
                    "Channel is needed unless you are currently on a channel.",
                    tag="error",
                )
                return

        core.send(f"PART {channel}")
        # User wants to GTFO from the channel for whatever reason, so don't
        # join them back automatically when mantaray restarts
        if channel in view.server_view.settings.joined_channels:
            view.server_view.settings.joined_channels.remove(channel)

    # TODO: add /quit, make sure it quits all servers.
    # Do not support specifying a reason, because when talking about these commands, I
    # often type "/quit is a command" without thinking about it much.

    def nick(view: View, core: IrcCore, new_nick: str) -> None:
        core.send(f"NICK :{new_nick}")
        view.server_view.settings.nick = new_nick

    def topic(view: View, core: IrcCore, new_topic: str) -> None:
        if isinstance(view, ChannelView):
            core.send(f"TOPIC {view.channel_name} :{new_topic}")
        else:
            view.add_message(
                "You must be on a channel to change its topic.", tag="error"
            )

    def me(view: View, core: IrcCore, message: str) -> None:
        _send_privmsg(view, core, "\x01ACTION " + message + "\x01")

    # TODO: /msg <nick>, should open up PMView
    def msg(view: View, core: IrcCore, nick: str, message: str) -> None:
        core.send_privmsg(nick, message)

    def msg_nickserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "NickServ", message)

    def msg_memoserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "MemoServ", message)

    def msg_chanserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "ChanServ", message)

    def op(view: View, core: IrcCore, nick: str) -> None:
        if isinstance(view, ChannelView):
            core.send(f"MODE {view.channel_name} +o :{nick}")
        else:
            view.add_message("You can use /op only on a channel.", tag="error")

    def deop(view: View, core: IrcCore, nick: str) -> None:
        if isinstance(view, ChannelView):
            core.send(f"MODE {view.channel_name} -o :{nick}")
        else:
            view.add_message("You can use /deop only on a channel.", tag="error")

    def kick(view: View, core: IrcCore, nick: str, reason: str | None = None) -> None:
        if isinstance(view, ChannelView):
            if reason is None:
                core.send(f"KICK {view.channel_name} {nick}")
            else:
                core.send(f"KICK {view.channel_name} {nick} :{reason}")
        else:
            view.add_message("You can use /kick only on a channel.", tag="error")

    def away(view: View, core: IrcCore, away_message: str) -> None:
        core.send(f"AWAY :{away_message}")

    def back(view: View, core: IrcCore) -> None:
        core.send("AWAY")

    return {
        "/join": join,
        "/part": part,
        "/nick": nick,
        "/topic": topic,
        "/me": me,
        "/msg": msg,
        "/ns": msg_nickserv,
        "/nickserv": msg_nickserv,
        "/ms": msg_memoserv,
        "/memoserv": msg_memoserv,
        "/cs": msg_chanserv,
        "/chanserv": msg_chanserv,
        "/op": op,
        "/deop": deop,
        "/kick": kick,
        "/away": away,
        "/back": back,
    }


_commands = _define_commands()
