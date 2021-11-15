"""This file handles commands like /join."""
from __future__ import annotations
import re
from typing import Callable, TypeVar
from irc_client.views import View, ChannelView, PMView
from irc_client.backend import IrcCore


_CommandT = TypeVar("_CommandT", bound=Callable[..., None])
_commands: dict[str, tuple[str, Callable[..., None]]] = {}


def add_command(usage: str) -> Callable[[_CommandT], _CommandT]:
    assert re.fullmatch(r"/[a-z]+( <[a-z_]+>)*( \[<[a-z_]+>\])*", usage)

    def do_it(func: _CommandT) -> _CommandT:
        _commands[usage.split()[0]] = (usage, func)
        return func

    return do_it


def handle_command(view: View, core: IrcCore, entry_content: str) -> None:
    if not entry_content:
        return

    if re.fullmatch("/[a-z]+( .*)?", entry_content):
        try:
            usage, func = _commands[entry_content.split()[0]]
        except KeyError:
            view.add_message("*", f"No command named '{entry_content.split()[0]}'")
            return

        # Last arg can contain spaces
        # Do not pass maxsplit=0 as that means "/lol asdf" --> ["/lol asdf"]
        args = entry_content.split(maxsplit=max(usage.count(" "), 1))[1:]
        if len(args) < usage.count(" <") or len(args) > usage.count(" "):
            view.add_message("*", "Usage: " + usage)
        else:
            func(
                view,
                core,
                **{
                    name.strip("[<>]"): arg
                    for name, arg in zip(usage.split()[1:], args)
                },
            )
        return

    if entry_content.startswith("//"):
        message = entry_content[1:]
    else:
        message = entry_content

    if isinstance(view, ChannelView):
        core.send_privmsg(view.channel_name, message)
    elif isinstance(view, PMView):
        core.send_privmsg(view.nick, message)
    else:
        view.add_message(
            "*",
            (
                "You can't send messages here. "
                "Join a channel instead and send messages there."
            ),
        )


def _add_default_commands() -> None:
    @add_command("/join <channel>")
    def join(view: View, core: IrcCore, channel: str) -> None:
        # TODO: plain '/join' for joining the current channel after kick?
        # currently kicks are not handled yet anyway :(
        core.join_channel(channel)

    @add_command("/part [<channel>]")
    def part(view: View, core: IrcCore, channel: str | None = None) -> None:
        if channel is not None:
            core.part_channel(channel)
        elif isinstance(view, ChannelView):
            core.part_channel(view.channel_name)
        else:
            view.add_message("*", "Usage: /part [<channel>]")
            view.add_message(
                "*", "Channel is needed unless you are currently on a channel."
            )

    # TODO: specifying a reason
    @add_command("/quit")
    def quit(view: View, core: IrcCore) -> None:
        core.quit()

    @add_command("/nick <new_nick>")
    def nick(view: View, core: IrcCore, new_nick: str) -> None:
        core.change_nick(new_nick)

    @add_command("/topic <new_topic>")
    def topic(view: View, core: IrcCore, new_topic: str) -> None:
        if isinstance(view, ChannelView):
            core.change_topic(view.channel_name, new_topic)
        else:
            view.add_message("*", "You must be on a channel to change its topic.")

    # TODO: /msg <nick>, should open up PMView
    @add_command("/msg <nick> <message>")
    def msg(view: View, core: IrcCore, nick: str, message: str) -> None:
        core.send_privmsg(nick, message)

    @add_command("/ns <message>")
    @add_command("/nickserv <message>")
    def msg_nickserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "NickServ", message)

    @add_command("/ms <message>")
    @add_command("/memoserv <message>")
    def msg_memoserv(view: View, core: IrcCore, message: str) -> None:
        return msg(view, core, "MemoServ", message)

    # TODO: /me, /kick, /ban etc... lots of commands to add


_add_default_commands()
