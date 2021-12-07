"""This file handles commands like /join."""
from __future__ import annotations
import inspect
import re
from typing import Callable, TypeVar
from tkinter import messagebox

from mantaray.views import View, ChannelView, PMView
from mantaray.backend import IrcCore


_CommandT = TypeVar("_CommandT", bound=Callable[..., None])
_commands: dict[str, Callable[..., None]] = {}


def add_command(name: str) -> Callable[[_CommandT], _CommandT]:
    assert re.fullmatch(r"/[a-z]+", name)

    def do_it(func: _CommandT) -> _CommandT:
        _commands[name] = func
        return func

    return do_it


def _send_privmsg(view: View, core: IrcCore, message: str) -> None:
    if isinstance(view, (ChannelView, PMView)):
        core.send_privmsg(view.view_name, message)
    else:
        view.add_message(
            "*",
            (
                (
                    "You can't send messages here. "
                    "Join a channel instead and send messages there."
                ),
                [],
            ),
        )


def escape_message(s: str) -> str:
    if s.startswith("/"):
        return "/" + s
    return s


def handle_command(view: View, core: IrcCore, entry_content: str) -> bool:
    if not entry_content:
        return False

    if re.fullmatch("/[a-z]+( .*)?", entry_content):
        try:
            func = _commands[entry_content.split()[0]]
        except KeyError:
            view.add_message(
                "*", (f"No command named '{entry_content.split()[0]}'", [])
            )
            return False

        view_arg, core_arg, *parameters = inspect.signature(func).parameters.values()
        assert all(param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for param in parameters)
        arg_count_min = [param.default for param in parameters].count(inspect.Parameter.empty)
        arg_count_max = len(parameters) - arg_count_min

        # Last arg can contain spaces
        # Do not pass maxsplit=0 as that means "/lol asdf" --> ["/lol asdf"]
        command_name, *args = entry_content.split(maxsplit=max(arg_count_min, 1))
        if len(args) < arg_count_min or len(args) > arg_count_max:
            usage = command_name
            for param in parameters:
                if param.default == inspect.Parameter.empty:
                    usage += f" <{param.name}>"
                else:
                    usage += f" [<{param.name}>]"
            view.add_message("*", ("Usage: " + usage, []))
            return False

        func(view, core, *args)
        return True

    if entry_content.startswith("//"):
        lines = entry_content[1:].splitlines()
    else:
        lines = entry_content.splitlines()

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


def _add_default_commands() -> None:
    @add_command("/join")
    def join(view: View, core: IrcCore, channel: str) -> None:
        # TODO: plain '/join' for joining the current channel after kick?
        # currently kicks are not handled yet anyway :(
        core.join_channel(channel)

    @add_command("/part")
    def part(view: View, core: IrcCore, channel: str | None = None) -> None:
        if channel is not None:
            core.part_channel(channel)
        elif isinstance(view, ChannelView):
            core.part_channel(view.view_name)
        else:
            view.add_message("*", ("Usage: /part [<channel>]", []))
            view.add_message(
                "*", ("Channel is needed unless you are currently on a channel.", [])
            )

    # TODO: specifying a reason
    @add_command("/quit")
    def quit(view: View, core: IrcCore) -> None:
        core.quit()

    @add_command("/nick")
    def nick(view: View, core: IrcCore, new_nick: str) -> None:
        core.change_nick(new_nick)

    @add_command("/topic")
    def topic(view: View, core: IrcCore, new_topic: str) -> None:
        if isinstance(view, ChannelView):
            core.change_topic(view.view_name, new_topic)
        else:
            view.add_message("*", ("You must be on a channel to change its topic.", []))

    @add_command("/me")
    def me(view: View, core: IrcCore, message: str) -> None:
        _send_privmsg(view, core, "\x01ACTION " + message + "\x01")

    # TODO: /msg <nick>, should open up PMView
    @add_command("/msg")
    def msg(view: View, core: IrcCore, nick: str, message: str) -> None:
        core.send_privmsg(nick, message)

    @add_command("/ns")
    @add_command("/nickserv")
    def msg_nickserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "NickServ", message)

    @add_command("/ms")
    @add_command("/memoserv")
    def msg_memoserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "MemoServ", message)

    @add_command("/kick")
    def kick(view: View, core: IrcCore, nick: str, reason: str | None = None) -> None:
        if isinstance(view, ChannelView):
            core.kick(view.view_name, nick, reason)
        else:
            view.add_message("You can use /kick only on a channel.")

    # TODO: /kick, /ban etc... lots of commands to add


_add_default_commands()
