"""This file handles commands like /join."""
from __future__ import annotations
import inspect
import re
from typing import Callable
from tkinter import messagebox

from mantaray.views import View, ChannelView, PMView
from mantaray.backend import IrcCore


def _send_privmsg(view: View, core: IrcCore, message: str) -> None:
    if isinstance(view, ChannelView):
        core.send_privmsg(view.channel_name, message)
    elif isinstance(view, PMView):
        core.send_privmsg(view.nick_of_other_user, message)
    else:
        view.add_message(
            "*", (("You can't send messages here. " "Join a channel instead and send messages there."), [])
        )


def escape_message(s: str) -> str:
    if s.startswith("/"):
        return "/" + s
    return s


def handle_command(view: View, core: IrcCore, entry_content: str) -> bool:
    if not entry_content:
        return False

    if re.fullmatch("/[A-Za-z]+( .*)?", entry_content):
        try:
            func = _commands[entry_content.split()[0].lower()]
        except KeyError:
            view.add_message("*", (f"No command named '{entry_content.split()[0]}'", []))
            return False

        view_arg, core_arg, *params = inspect.signature(func).parameters.values()
        assert all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
        required_params = [p for p in params if p.default == inspect.Parameter.empty]

        # Last arg can contain spaces
        # Do not pass maxsplit=0 as that means "/lol asdf" --> ["/lol asdf"]
        command_name, *args = entry_content.split(maxsplit=max(len(params), 1))
        if len(args) < len(required_params) or len(args) > len(params):
            usage = command_name
            for p in params:
                if p in required_params:
                    usage += f" <{p.name}>"
                else:
                    usage += f" [<{p.name}>]"
            view.add_message("*", ("Usage: " + usage, []))
            return False

        func(view, core, *args)
        return True

    if entry_content.startswith("//"):
        entry_content = entry_content[1:]

    lines = [line for line in entry_content.split("\n") if line]
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
            return False

    for line in lines:
        _send_privmsg(view, core, line)
    return True


def _define_commands() -> dict[str, Callable[..., None]]:
    def join(view: View, core: IrcCore, channel: str) -> None:
        # TODO: plain '/join' for joining the current channel after kick?
        core.join_channel(channel)
        if "away-notify" in view.server_view.cap_list:
            who(view, core, channel)

    def part(view: View, core: IrcCore, channel: str | None = None) -> None:
        if channel is not None:
            core.send(f"PART {channel}")
        elif isinstance(view, ChannelView):
            core.send(f"PART {view.channel_name}")
        else:
            view.add_message("*", ("Usage: /part [<channel>]", []))
            view.add_message("*", ("Channel is needed unless you are currently on a channel.", []))

    # Doesn't support specifying a reason, because when talking about these commands, I
    # often type "/quit is a command" without thinking about it much.
    def quit(view: View, core: IrcCore) -> None:
        core.quit()

    def nick(view: View, core: IrcCore, new_nick: str) -> None:
        core.send(f"NICK :{new_nick}")

    def topic(view: View, core: IrcCore, new_topic: str) -> None:
        if isinstance(view, ChannelView):
            core.send(f"TOPIC {view.channel_name} :{new_topic}")
        else:
            view.add_message("*", ("You must be on a channel to change its topic.", []))

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
            view.add_message("You can use /op only on a channel.")

    def deop(view: View, core: IrcCore, nick: str) -> None:
        if isinstance(view, ChannelView):
            core.send(f"MODE {view.channel_name} -o :{nick}")
        else:
            view.add_message("You can use /deop only on a channel.")

    def kick(view: View, core: IrcCore, nick: str, reason: str | None = None) -> None:
        if isinstance(view, ChannelView):
            if reason is None:
                core.send(f"KICK {view.channel_name} {nick}")
            else:
                core.send(f"KICK {view.channel_name} {nick} :{reason}")
        else:
            view.add_message("You can use /kick only on a channel.")

    def away(view: View, core: IrcCore, away_message: str | None = None) -> None:
        if away_message is None:
            core.send("AWAY :")
        else:
            core.send(f"AWAY {away_message}")
        # TODO: Make own nick gray

    def who(view: View, core: IrcCore, nick_or_channel: str) -> None:
        core.send_who(nick_or_channel)

    return {
        "/join": join,
        "/part": part,
        "/quit": quit,
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
        "/who": who,
    }


_commands = _define_commands()
