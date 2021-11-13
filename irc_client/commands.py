"""This file handles commands like /join."""
from __future__ import annotations
from typing import Callable, TypeVar
from irc_client.gui import View, ChannelView, PMView
from irc_client.backend import IrcCore

_CommandT = TypeVar("_CommandT", bound=Callable[[View, IrcCore, str], None])
_commands: dict[str, Callable[[View, IrcCore, str], None]] = {}


def add_command(name: str) -> Callable[[_CommandT], _CommandT]:
    assert name.startswith("/")

    def do_it(func: _CommandT) -> _CommandT:
        _commands[name] = func
        return func

    return do_it


def _add_default_commands() -> None:
    @add_command("/join")
    def join(view: View, core: IrcCore, channel: str) -> None:
        # TODO: plain '/join' for joining the current channel after kick?
        # currently kicks are not handled yet anyway :(
        if channel:
            core.join_channel(channel)
        else:
            view.add_message("*", "Usage: /join <channel>")

    @add_command("/part")
    def part(view: View, core: IrcCore, channel: str) -> None:
        if channel:
            core.part_channel(channel)
        elif isinstance(view, ChannelView):
            core.part_channel(view.name)
        else:
            view.add_message("*", "Usage: /part [<channel>]")
            view.add_message(
                "*", "Channel is needed unless you are currently on a channel."
            )

    @add_command("/nick")
    def nick(view: View, core: IrcCore, new_nick: str) -> None:
        if new_nick:
            core.change_nick(new_nick)
        else:
            view.add_message("*", "Usage: /nick <new nick>")
        return None

    @add_command("/msg")
    def msg(view: View, core: IrcCore, params: str) -> None:
        try:
            nick, message = params.split(maxsplit=1)
        except ValueError:
            view.add_message("*", "Usage: /msg <nick> <message>")
        else:
            core.send_privmsg(nick, message)

    @add_command("/ns")
    @add_command("/nickserv")
    def msg_nickserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "NickServ " + message)

    @add_command("/ms")
    @add_command("/memoserv")
    def msg_memoserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "MemoServ " + message)

    # TODO: /me, /kick, /ban etc... lots of commands to add


_add_default_commands()


def handle_command(view: View, core: IrcCore, entry_content: str) -> None:
    if not entry_content:
        return None

    # TODO: disallow slashes in command
    if entry_content.startswith("/") and not entry_content.startswith("//"):
        try:
            command, args = entry_content.split(maxsplit=1)
        except ValueError:
            command = entry_content.strip()
            args = ""

        if command in _commands:
            _commands[command](view, core, args)
        else:
            view.add_message("*", f"There's no '{command}' command :(")
    else:
        if entry_content.startswith("//"):
            message = entry_content.replace("//", "/", 1)
        else:
            message = entry_content

        if isinstance(view, ChannelView):
            core.send_privmsg(view.name, message)
        elif isinstance(view, PMView):
            core.send_privmsg(view.nick, message)
        else:
            view.add_message(
                "*",
                (
                    "You can't send messages directly to the server. "
                    "Join a channel instead and send messages there."
                ),
            )
