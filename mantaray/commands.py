"""This file handles commands like /join."""

from __future__ import annotations

import inspect
import re
from datetime import datetime
from tkinter import messagebox
from typing import Callable

from mantaray import state
from mantaray.views import ChannelView, MessagePart, PMView, View


def _format_usage(command_name: str, func: Callable[..., None]) -> str:
    # First parameter is the view, the rest are from the user.
    params = list(inspect.signature(func).parameters.values())[1:]
    usage = command_name
    for p in params:
        if p.default == inspect.Parameter.empty:
            usage += f" <{p.name}>"
        else:
            usage += f" [<{p.name}>]"
    return usage


def _message_to_parts(message: str | list[MessagePart]) -> list[state.MessagePartState]:
    if isinstance(message, str):
        return [state.MessagePartState(message)]
    return [state.MessagePartState(part.text, tags=part.tags) for part in message]


def _render_message_action(
    view: View,
    message: str | list[MessagePart],
    sender: str | None = None,
    *,
    tag: str = "info",
    pinged: bool = False,
    history_id: int | None = None,
    timestamp: datetime | None = None,
) -> state.RenderMessageAction:
    if timestamp is None:
        timestamp = datetime.now().astimezone()

    return state.RenderMessageAction(
        view_id=view.view_id,
        message=_message_to_parts(message),
        sender=sender,
        tag=tag,
        pinged=pinged,
        history_id=history_id,
        timestamp=timestamp,
    )


def handle_command(view: View, entry_text: str, history_id: int) -> list[state.UserAction]:
    if not entry_text:
        return []

    if re.fullmatch(r"/([A-Za-z]+|\?)( .*)?", entry_text):
        try:
            func = _commands[entry_text.split()[0].lower()][0]
        except KeyError:
            view.add_message(
                f"No command named '{entry_text.split()[0]}'",
                tag="error",
                history_id=history_id,
            )
            return []

        params = list(inspect.signature(func).parameters.values())[1:]
        assert all(p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
        required_params = [p for p in params if p.default == inspect.Parameter.empty]

        # Last arg can contain spaces
        # Do not pass maxsplit=0 as that means "/lol asdf" --> ["/lol asdf"]
        command_name, *args = entry_text.rstrip().split(maxsplit=max(len(params), 1))
        if len(args) < len(required_params) or len(args) > len(params):
            return [
                _render_message_action(
                    view,
                    [
                        MessagePart("Usage: "),
                        # TODO: Add a dedicated command-syntax tag instead of reusing "pinged".
                        MessagePart(_format_usage(command_name, func), tags=["pinged"]),
                    ],
                    tag="error",
                    history_id=history_id,
                )
            ]
        else:
            result = func(view, *args)
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return [result]

        return []

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
        view.irc_widget.entry.focus()
        if not result:
            return []

    actions: list[state.UserAction] = []
    for line in lines:
        actions.append(
            state.SendMessageAction(view_id=view.view_id, text=line, history_id=history_id)
        )
    return actions


def _define_commands() -> dict[str, tuple[Callable[..., list[state.UserAction] | state.UserAction | None], str]]:
    # Channel is required, and not assumed to be the current channel view.
    # So when you have been kicked, you will have to type the current channel
    # name manually to rejoin, which is good because it might give you time
    # to calm down a bit before you continue ranting.
    def join(view: View, channel: str) -> state.ExecuteCommandAction:
        view.server_view.last_slash_join_channel = channel
        return state.ExecuteCommandAction(
            view_id=view.view_id,
            command=f"JOIN {channel}",
        )

    def part(view: View, channel: str | None = None) -> list[state.UserAction] | state.ExecuteCommandAction:
        if channel is not None:
            return state.ExecuteCommandAction(view_id=view.view_id, command=f"PART {channel}")
        elif isinstance(view, ChannelView):
            return state.ExecuteCommandAction(
                view_id=view.view_id, command=f"PART {view.channel_name}"
            )
        else:
            return [
                _render_message_action(view, "Usage: /part [<channel>]") ,
                _render_message_action(
                    view,
                    "Channel is needed unless you are currently on a channel.",
                    tag="error",
                ),
            ]

    # TODO: add /quit, make sure it quits all servers.
    # Do not support specifying a reason, because when talking about these commands, I
    # often type "/quit is a command" without thinking about it much.

    def nick(view: View, new_nick: str) -> state.ExecuteCommandAction:
        return state.ExecuteCommandAction(view_id=view.view_id, command=f"NICK :{new_nick}")

    def topic(view: View, new_topic: str) -> list[state.UserAction] | state.ExecuteCommandAction:
        if isinstance(view, ChannelView):
            return state.ExecuteCommandAction(
                view_id=view.view_id,
                command=f"TOPIC {view.channel_name} :{new_topic}",
            )
        return _render_message_action(
            view,
            "You must be on a channel to change its topic.",
            tag="error",
        )

    def me(view: View, message: str) -> state.SendMessageAction:
        return state.SendMessageAction(
            view_id=view.view_id,
            text="\x01ACTION " + message + "\x01",
            history_id=None,
        )

    # TODO: /msg <nick>, should open up PMView
    def msg(view: View, nick: str, message: str) -> state.SendMessageAction:
        pm_view = view.server_view.find_or_open_pm(nick, select_existing=True)
        return state.SendMessageAction(
            view_id=pm_view.view_id,
            text=message,
        )

    def msg_nickserv(view: View, message: str) -> list[state.UserAction]:
        pm_view = view.server_view.find_or_open_pm("NickServ", select_existing=True)

        masked = message.split(" ", 1)
        if len(masked) == 2:
            visible, _ = masked
            masked_text = f"{visible} ********"
        else:
            masked_text = message

        return [
            _render_message_action(
                pm_view,
                masked_text,
                tag="info",
            ),
            state.SendMessageAction(
                view_id=pm_view.view_id,
                text=message,
            ),
        ]

    def msg_memoserv(view: View, message: str) -> state.SendMessageAction:
        return msg(view, "MemoServ", message)

    def msg_chanserv(view: View, message: str) -> state.SendMessageAction:
        return msg(view, "ChanServ", message)

    def whois(view: View, nick: str) -> state.ExecuteCommandAction:
        return state.ExecuteCommandAction(view_id=view.view_id, command=f"WHOIS {nick}")

    def op(view: View, nick: str) -> list[state.UserAction] | state.ExecuteCommandAction:
        if isinstance(view, ChannelView):
            return state.ExecuteCommandAction(
                view_id=view.view_id,
                command=f"MODE {view.channel_name} +o :{nick}",
            )
        return _render_message_action(
            view,
            "You can use /op only on a channel.",
            tag="error",
        )

    def deop(view: View, nick: str) -> list[state.UserAction] | state.ExecuteCommandAction:
        if isinstance(view, ChannelView):
            return state.ExecuteCommandAction(
                view_id=view.view_id,
                command=f"MODE {view.channel_name} -o :{nick}",
            )
        return _render_message_action(
            view,
            "You can use /deop only on a channel.",
            tag="error",
        )

    def kick(view: View, nick: str, reason: str | None = None) -> list[state.UserAction] | state.ExecuteCommandAction:
        if isinstance(view, ChannelView):
            if reason is None:
                return state.ExecuteCommandAction(
                    view_id=view.view_id, command=f"KICK {view.channel_name} {nick}"
                )
            return state.ExecuteCommandAction(
                view_id=view.view_id,
                command=f"KICK {view.channel_name} {nick} :{reason}",
            )
        return _render_message_action(
            view,
            "You can use /kick only on a channel.",
            tag="error",
        )

    def away(view: View, away_message: str) -> state.ExecuteCommandAction:
        view.server_view.last_away_status = away_message
        return state.ExecuteCommandAction(
            view_id=view.view_id,
            command=f"AWAY :{away_message}",
        )

    def back(view: View) -> state.ExecuteCommandAction:
        return state.ExecuteCommandAction(view_id=view.view_id, command="AWAY")

    def raw(view: View, command: str) -> state.ExecuteCommandAction:
        return state.ExecuteCommandAction(view_id=view.view_id, command=command)

    def help(view: View, command: str | None = None) -> list[state.UserAction]:
        actions: list[state.UserAction] = []
        if command is None:
            actions.append(
                _render_message_action(
                    view,
                    [MessagePart("Available commands:", tags=["pinged", "underline"])],
                )
            )
            keys = sorted(_commands.keys())
        else:
            key = command.lower()
            if not key.startswith("/"):
                key = "/" + key
            if key not in _commands:
                return [
                    _render_message_action(
                        view,
                        f"No command named '{command}'",
                        tag="error",
                    )
                ]
            keys = [key]

        for command_name in sorted(keys):
            func, description = _commands[command_name]
            actions.append(
                _render_message_action(
                    view,
                    [
                        # TODO: Add a dedicated command-syntax tag instead of reusing "topic".
                        MessagePart(_format_usage(command_name, func), tags=["topic"]),
                        MessagePart(" - " + description),
                    ],
                )
            )

        if command is None:
            actions.append(
                _render_message_action(
                    view,
                    "Feel free to ask questions by creating an issue on GitHub: https://github.com/Akuli/mantaray",
                )
            )
        return actions

    return {
        "/join": (join, "Join a channel"),
        "/part": (part, "Leave a channel"),
        "/nick": (nick, "Change your nickname"),
        "/topic": (topic, "Change the channel topic"),
        "/me": (me, "Send an action message"),
        "/msg": (msg, "Send a message"),
        "/ns": (msg_nickserv, "Send a message to NickServ"),
        "/nickserv": (msg_nickserv, "Send a message to NickServ"),
        "/ms": (msg_memoserv, "Send a message to MemoServ"),
        "/memoserv": (msg_memoserv, "Send a message to MemoServ"),
        "/cs": (msg_chanserv, "Send a message to ChanServ"),
        "/chanserv": (msg_chanserv, "Send a message to ChanServ"),
        "/whois": (whois, "Show whois information"),
        "/op": (op, "Give operator permissions to a user"),
        "/deop": (deop, "Remove operator permissions from a user"),
        "/kick": (kick, "Kick a user from the channel"),
        "/away": (away, "Mark yourself as being away"),
        "/back": (back, "Return from away"),
        "/raw": (raw, "Send a raw IRC command"),
        "/help": (help, "Show available commands"),
        "/?": (help, "Show available commands"),
    }


_commands = _define_commands()
