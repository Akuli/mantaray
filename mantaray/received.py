"""Handle commands received from the IRC server."""

from __future__ import annotations
import dataclasses
import re
import traceback
from mantaray import backend, views


from base64 import b64encode
from typing import Union


RPL_ENDOFMOTD = "376"
RPL_NAMREPLY = "353"
RPL_ENDOFNAMES = "366"
RPL_LOGGEDIN = "900"
RPL_TOPIC = "332"

# fmt: off
@dataclasses.dataclass
class SelfJoined:
    channel: str
    topic: str
    nicklist: list[str]
@dataclasses.dataclass
class SelfChangedNick:
    old: str
    new: str
@dataclasses.dataclass
class SelfParted:
    channel: str
@dataclasses.dataclass
class UserJoined:
    nick: str
    channel: str
@dataclasses.dataclass
class UserChangedNick:
    old: str
    new: str
@dataclasses.dataclass
class UserParted:
    nick: str
    channel: str
    reason: str | None
@dataclasses.dataclass
class ModeChange:
    channel: str
    setter_nick: str
    mode_flags: str  # e.g. "+o" for opping, "-o" for deopping
    target_nick : str
@dataclasses.dataclass
class Kick:
    kicker: str
    channel: str
    kicked_nick: str
    reason: str | None
@dataclasses.dataclass
class UserQuit:
    nick: str
    reason: str | None
@dataclasses.dataclass
class ReceivedPrivmsg:
    sender: str  # channel or nick (PM)
    recipient: str  # channel or user's nick
    text: str
@dataclasses.dataclass
class TopicChanged:
    who_changed: str
    channel: str
    topic: str
@dataclasses.dataclass
class ServerMessage:
    sender: str | None  # I think this is a hostname. Not sure.
    command: str  # e.g. '482'
    args: list[str]  # e.g. ["Alice", "#foo", "You're not a channel operator"]
    is_error: bool
@dataclasses.dataclass
class UnknownMessage:
    sender: str | None
    command: str
    args: list[str]
# fmt: on

InternalEvent = Union[
    SelfJoined,
    SelfChangedNick,
    SelfParted,
    UserJoined,
    ModeChange,
    Kick,
    UserChangedNick,
    UserParted,
    UserQuit,
    ReceivedPrivmsg,
    TopicChanged,
    ServerMessage,
    UnknownMessage,
]


def _nick_is_relevant_for_view(nick: str, view: views.View) -> bool:
    if isinstance(view, views.ChannelView):
        return isinstance(view, views.ChannelView)
    if isinstance(view, views.PMView):
        return nick == view.nick_of_other_user
    return False


def _handle_privmsg(
    server_view: views.ServerView, sender: str, args: list[str]
) -> None:
    # recipient is server or nick
    recipient, text = args

    if recipient == server_view.core.nick:  # PM
        pm_view = server_view.find_pm(sender)
        if pm_view is None:
            # start of a new PM conversation
            pm_view = views.PMView(server_view, sender)
            server_view.irc_widget.add_view(pm_view)
        pm_view.on_privmsg(sender, text)
        pm_view.add_tag("new_message")
        pm_view.add_notification(text)

    else:
        channel_view = server_view.find_channel(recipient)
        assert channel_view is not None

        pinged = any(
            tag == "self-nick"
            for substring, tag in backend.find_nicks(
                text, server_view.core.nick, [server_view.core.nick]
            )
        )
        channel_view.on_privmsg(sender, text, pinged=pinged)
        channel_view.add_tag("pinged" if pinged else "new_message")
        if pinged or (channel_view.channel_name in server_view.extra_notifications):
            channel_view.add_notification(f"<{sender}> {text}")



def _handle_join(server_view: views.ServerView, sender: str, args: list[str]) -> None:
    [channel] = args
    # When this user joins a channel, wait for RPL_ENDOFNAMES
    if sender != server_view.core.nick:
        _handle_internal_event(server_view, UserJoined(sender, channel))


def _handle_part(server_view: views.ServerView, sender: str, args: list[str]) -> None:
    channel = args[0]
    reason = args[1] if len(args) >= 2 else None
    if sender == server_view.core.nick:
        _handle_internal_event(server_view, SelfParted(channel))
    else:
        _handle_internal_event(server_view, UserParted(sender, channel, reason))


def _handle_nick(server_view: views.ServerView, old_nick: str, args: list[str]) -> None:
    [new_nick] = args
    if old_nick == server_view.core.nick:
        server_view.core.nick = new_nick
        _handle_internal_event(server_view, SelfChangedNick(old_nick, new_nick))
    else:
        _handle_internal_event(server_view, UserChangedNick(old_nick, new_nick))


def _handle_quit(server_view: views.ServerView, sender: str, args: list[str]) -> None:
    reason = args[0] if args else None
    _handle_internal_event(server_view, UserQuit(sender, reason or None))


def _handle_mode(server_view: views.ServerView, sender: str, args: list[str]) -> None:
    channel, mode_flags, nick = args
    _handle_internal_event(server_view, ModeChange(channel, sender, mode_flags, nick))


def _handle_kick(server_view: views.ServerView, sender: str, args: list[str]) -> None:
    kicker = sender
    channel, kicked_nick, reason = args
    _handle_internal_event(
        server_view, Kick(kicker, channel, kicked_nick, reason or None)
    )


def _handle_cap(server_view: views.ServerView, args: list[str]) -> None:
    subcommand = args[1]
    if subcommand == "ACK":
        acknowledged = set(args[-1].split())
        if "sasl" in acknowledged:
            server_view.core.put_to_send_queue("AUTHENTICATE PLAIN")
    elif subcommand == "NAK":
        rejected = set(args[-1].split())
        if "sasl" in rejected:
            # TODO: this good?
            raise ValueError("The server does not support SASL.")


def _handle_authenticate(server_view: views.ServerView) -> None:
    query = f"\0{server_view.core.username}\0{server_view.core.password}"
    b64_query = b64encode(query.encode("utf-8")).decode("utf-8")
    for i in range(0, len(b64_query), 400):
        server_view.core.put_to_send_queue("AUTHENTICATE " + b64_query[i : i + 400])


def _handle_namreply(server_view: views.ServerView, args: list[str]) -> None:
    # TODO: wtf are the first 2 args?
    # rfc1459 doesn't mention them, but freenode
    # gives 4-element msg.args lists
    channel, names = args[-2:]

    # TODO: the prefixes have meanings
    # https://modern.ircdocs.horse/#channel-membership-prefixes
    server_view.core.joining_in_progress[channel.lower()].nicks.extend(
        name.lstrip("~&@%+") for name in names.split()
    )


def _handle_endofnames(server_view: views.ServerView, args: list[str]) -> None:
    # joining a channel finished
    channel, human_readable_message = args[-2:]
    join = server_view.core.joining_in_progress.pop(channel.lower())

    channel_view = server_view.find_channel(channel)
    if channel_view is None:
        channel_view = views.ChannelView(server_view, channel, join.nicks)
        server_view.irc_widget.add_view(channel_view)
    else:
        # Can exist already, when has been disconnected from server
        channel_view.userlist.set_nicks(join.nicks)

    topic = join.topic or "(no topic)"
    channel_view.add_message(
        "*", (f"The topic of {channel_view.channel_name} is: {topic}", [])
    )

    if channel not in server_view.core.autojoin:
        server_view.core.autojoin.append(channel)


def _handle_endofmotd(server_view: views.ServerView) -> None:
    # TODO: relying on MOTD good?
    for channel in server_view.core.autojoin:
        server_view.core.join_channel(channel)


def _handle_numeric_rpl_topic(server_view: views.ServerView, args: list[str]) -> None:
    channel, topic = args[1:]
    server_view.core.joining_in_progress[channel.lower()].topic = topic


def _handle_literally_topic(
    server_view: views.ServerView, sender: str, args: list[str]
) -> None:
    channel, topic = args
    _handle_internal_event(server_view, TopicChanged(sender, channel, topic))


def _handle_unknown_message(
    server_view: views.ServerView,
    sender: str | None,
    sender_is_server: bool,
    command: str,
    args: list[str],
) -> None:
    if sender_is_server:
        _handle_internal_event(
            server_view,
            ServerMessage(
                sender,
                command,
                args,
                # Errors seem to always be 4xx, 5xx or 7xx.
                # Not all 6xx responses are errors, e.g. RPL_STARTTLS = 670
                is_error=command.startswith(("4", "5", "7")),
            ),
        )
    else:
        _handle_internal_event(server_view, UnknownMessage(sender, command, args))


def _handle_received_message(
    server_view: views.ServerView, msg: backend.ReceivedLine
) -> None:
    if msg.command == "PRIVMSG":
        assert msg.sender is not None
        _handle_privmsg(server_view, msg.sender, msg.args)

    elif msg.command == "JOIN":
        assert msg.sender is not None
        _handle_join(server_view, msg.sender, msg.args)

    elif msg.command == "PART":
        assert msg.sender is not None
        _handle_part(server_view, msg.sender, msg.args)

    elif msg.command == "NICK":
        assert msg.sender is not None
        _handle_nick(server_view, msg.sender, msg.args)

    elif msg.command == "QUIT":
        assert msg.sender is not None
        _handle_quit(server_view, msg.sender, msg.args)

    elif msg.command == "MODE":
        assert msg.sender is not None
        _handle_mode(server_view, msg.sender, msg.args)

    elif msg.command == "KICK":
        assert msg.sender is not None
        _handle_kick(server_view, msg.sender, msg.args)

    elif msg.command == "CAP":
        _handle_cap(server_view, msg.args)

    elif msg.command == "AUTHENTICATE":
        _handle_authenticate(server_view)

    elif msg.command == RPL_LOGGEDIN:
        server_view.core.put_to_send_queue("CAP END")

    elif msg.command == RPL_NAMREPLY:
        _handle_namreply(server_view, msg.args)

    elif msg.command == RPL_ENDOFNAMES:
        _handle_endofnames(server_view, msg.args)

    elif msg.command == RPL_ENDOFMOTD:
        _handle_endofmotd(server_view)

    elif msg.command == RPL_TOPIC:
        _handle_numeric_rpl_topic(server_view, msg.args)

    elif msg.command == "TOPIC" and not msg.sender_is_server:
        assert msg.sender is not None
        _handle_literally_topic(server_view, msg.sender, msg.args)

    else:
        _handle_unknown_message(
            server_view, msg.sender, msg.sender_is_server, msg.command, msg.args
        )


def _handle_internal_event(server_view: views.ServerView, event: InternalEvent) -> None:
    if isinstance(event, SelfJoined):
        channel_view = server_view.find_channel(event.channel)
        if channel_view is None:
            channel_view = views.ChannelView(server_view, event.channel, event.nicklist)
            server_view.irc_widget.add_view(channel_view)
        else:
            # Can exist already, when has been disconnected from server
            channel_view.userlist.set_nicks(event.nicklist)

        channel_view.add_message(
            "*", (f"The topic of {channel_view.channel_name} is: {event.topic}", [])
        )
        if event.channel not in server_view.core.autojoin:
            server_view.core.autojoin.append(event.channel)

    elif isinstance(event, SelfParted):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        server_view.irc_widget.remove_view(channel_view)
        if event.channel in server_view.core.autojoin:
            server_view.core.autojoin.remove(event.channel)

    elif isinstance(event, SelfChangedNick):
        if server_view.irc_widget.get_current_view().server_view == server_view:
            server_view.irc_widget.nickbutton.config(text=event.new)

        for view in server_view.get_subviews(include_server=True):
            view.add_message(
                "*",
                ("You are now known as ", []),
                (event.new, ["server_view.core-nick"]),
                (".", []),
            )
            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(event.old)
                view.userlist.add_user(event.new)

    elif isinstance(event, UserJoined):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None

        channel_view.userlist.add_user(event.nick)
        channel_view.add_message(
            "*",
            (event.nick, ["other-nick"]),
            (f" joined {channel_view.channel_name}.", []),
            show_in_gui=channel_view.server_view.should_show_join_leave_message(
                event.nick
            ),
        )

    elif isinstance(event, UserParted):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.userlist.remove_user(event.nick)

        if event.reason is None:
            extra = ""
        else:
            extra = " (" + event.reason + ")"

        channel_view.add_message(
            "*",
            (event.nick, ["other-nick"]),
            (f" left {channel_view.channel_name}." + extra, []),
            show_in_gui=channel_view.server_view.should_show_join_leave_message(
                event.nick
            ),
        )

    elif isinstance(event, ModeChange):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None

        if event.mode_flags == "+o":
            message = "gives channel operator permissions to"
        elif event.mode_flags == "-o":
            message = "removes channel operator permissions from"
        else:
            message = f"sets mode {event.mode_flags} on"

        if event.target_nick == channel_view.server_view.core.nick:
            target_tag = "server_view.core-nick"
        else:
            target_tag = "other-nick"

        if event.setter_nick == channel_view.server_view.core.nick:
            setter_tag = "server_view.core-nick"
        else:
            setter_tag = "other-nick"

        channel_view.add_message(
            "*",
            (event.setter_nick, [setter_tag]),
            (f" {message} ", []),
            (event.target_nick, [target_tag]),
            (".", []),
        )

    elif isinstance(event, Kick):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None

        channel_view.userlist.remove_user(event.kicked_nick)
        if event.kicker == channel_view.server_view.core.nick:
            kicker_tag = "server_view.core-nick"
        else:
            kicker_tag = "other-nick"
        if event.kicked_nick == channel_view.server_view.core.nick:
            channel_view.add_message(
                "*",
                (event.kicker, [kicker_tag]),
                (" has kicked you from ", ["error"]),
                (channel_view.channel_name, ["channel"]),
                (
                    f". (Reason: {event.reason or ''}) You can still join by typing ",
                    ["error"],
                ),
                (f"/join {channel_view.channel_name}", ["pinged"]),
                (".", ["error"]),
            )
        else:
            channel_view.add_message(
                "*",
                (event.kicker, [kicker_tag]),
                (" has kicked ", []),
                (event.kicked_nick, ["other-nick"]),
                (" from ", []),
                (channel_view.channel_name, ["channel"]),
                (f". (Reason: {event.reason or ''})", []),
            )

    elif isinstance(event, UserQuit):
        # This isn't perfect, other person's QUIT not received if not both joined on the same channel
        for view in server_view.get_subviews(include_server=True):
            if not _nick_is_relevant_for_view(event.nick, view):
                continue

            if event.reason is None:
                extra = ""
            else:
                extra = " (" + event.reason + ")"

            view.add_message(
                "*",
                (event.nick, ["other-nick"]),
                (" quit." + extra, []),
                show_in_gui=view.server_view.should_show_join_leave_message(event.nick),
            )
            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(event.nick)

    elif isinstance(event, UserChangedNick):
        for view in server_view.get_subviews(include_server=True):
            if not _nick_is_relevant_for_view(event.old, view):
                continue

            view.add_message(
                "*",
                (event.old, ["other-nick"]),
                (" is now known as ", []),
                (event.new, ["other-nick"]),
                (".", []),
            )
            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(event.old)
                view.userlist.add_user(event.new)
            if isinstance(view, views.PMView):
                view.set_nick_of_other_user(event.new)

    elif isinstance(event, ReceivedPrivmsg):
        # sender and recipient are channels or nicks
        if event.recipient == server_view.core.nick:  # PM
            pm_view = server_view.find_pm(event.sender)
            if pm_view is None:
                # start of a new PM conversation
                pm_view = views.PMView(server_view, event.sender)
                server_view.irc_widget.add_view(pm_view)
            pm_view.on_privmsg(event.sender, event.text)
            pm_view.add_tag("new_message")
            pm_view.add_notification(event.text)

        else:
            channel_view = server_view.find_channel(event.recipient)
            assert channel_view is not None

            pinged = any(
                tag == "self-nick"
                for substring, tag in backend.find_nicks(
                    event.text, server_view.core.nick, [server_view.core.nick]
                )
            )
            channel_view.on_privmsg(event.sender, event.text, pinged=pinged)
            channel_view.add_tag("pinged" if pinged else "new_message")
            if pinged or (channel_view.channel_name in server_view.extra_notifications):
                channel_view.add_notification(f"<{event.sender}> {event.text}")

    elif isinstance(event, ServerMessage):
        if event.is_error:
            view = server_view.irc_widget.get_current_view()
        else:
            view = server_view
        view.add_message(
            event.sender or "???",
            (
                " ".join([event.command] + event.args),
                ["error"] if event.is_error else [],
            ),
        )

    elif isinstance(event, UnknownMessage):
        server_view.add_message(
            event.sender or "???", (" ".join([event.command] + event.args), [])
        )

    elif isinstance(event, TopicChanged):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None

        if event.who_changed == channel_view.server_view.core.nick:
            nick_tag = "server_view.core-nick"
        else:
            nick_tag = "other-nick"
        channel_view.add_message(
            "*",
            (event.who_changed, [nick_tag]),
            (f" changed the topic of {channel_view.channel_name}: {event.topic}", []),
        )

    else:
        # If mypy says 'error: unused "type: ignore" comment', you
        # forgot to check for some class
        print("can't happen")  # type: ignore


# Returns True this function should be called again, False if quitting
def handle_event(event: backend.IrcEvent, server_view: views.ServerView) -> bool:
    if isinstance(event, backend.ReceivedLine):
        try:
            _handle_received_message(server_view, event)
        except Exception:
            traceback.print_exc()
        return True

    if isinstance(event, backend.ConnectivityMessage):
        for view in server_view.get_subviews(include_server=True):
            view.add_message(
                "", (event.message, ["error" if event.is_error else "info"])
            )
        return True

    if isinstance(event, backend.HostChanged):
        server_view.view_name = event.new
        for subview in server_view.get_subviews(include_server=True):
            subview.reopen_log_file()
        return True

    if isinstance(event, backend.SentPrivmsg):
        channel_view = server_view.find_channel(event.nick_or_channel)
        if channel_view is None:
            assert not re.fullmatch(
                backend.CHANNEL_REGEX, event.nick_or_channel
            ), event.nick_or_channel
            pm_view = server_view.find_pm(event.nick_or_channel)
            if pm_view is None:
                # start of a new PM conversation
                pm_view = views.PMView(server_view, event.nick_or_channel)
                server_view.irc_widget.add_view(pm_view)
            pm_view.on_privmsg(server_view.core.nick, event.text)
        else:
            channel_view.on_privmsg(server_view.core.nick, event.text)
        return True

    if isinstance(event, backend.Quit):
        return False

    # If mypy says 'error: unused "type: ignore" comment', you
    # forgot to check for some class
    print("can't happen")  # type: ignore
