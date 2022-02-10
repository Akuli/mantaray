"""Handle commands received from the IRC server."""

from __future__ import annotations

import re
import traceback
from base64 import b64encode

from mantaray import backend, views

RPL_ENDOFMOTD = "376"
RPL_NAMREPLY = "353"
RPL_ENDOFNAMES = "366"
RPL_WHOREPLY = "352"
RPL_LOGGEDIN = "900"
RPL_TOPIC = "332"


def _get_views_relevant_for_nick(server_view: views.ServerView, nick: str) -> list[views.ChannelView | views.PMView]:
    result: list[views.ChannelView | views.PMView] = []
    for view in server_view.get_subviews():
        if isinstance(view, views.ChannelView) and nick in view.userlist.get_nicks():
            result.append(view)

    pm_view = server_view.find_pm(nick)
    if pm_view is not None:
        result.append(pm_view)

    return result


def _handle_privmsg(server_view: views.ServerView, sender: str, args: list[str]) -> None:
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
            for substring, tag in backend.find_nicks(text, server_view.core.nick, [server_view.core.nick])
        )
        channel_view.on_privmsg(sender, text, pinged=pinged)
        channel_view.add_tag("pinged" if pinged else "new_message")
        if pinged or (channel_view.channel_name in server_view.extra_notifications):
            channel_view.add_notification(f"<{sender}> {text}")


def _handle_join(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    # When this user joins a channel, wait for RPL_ENDOFNAMES
    if nick == server_view.core.nick:
        return

    [channel] = args
    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    channel_view.userlist.add_user(nick)

    channel_view.add_message(
        "*",
        (nick, ["other-nick"]),
        (f" joined {channel_view.channel_name}.", []),
        show_in_gui=channel_view.server_view.should_show_join_leave_message(nick),
    )


def _handle_part(server_view: views.ServerView, parting_nick: str, args: list[str]) -> None:
    channel = args[0]
    reason = args[1] if len(args) >= 2 else None

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if parting_nick == server_view.core.nick:
        server_view.irc_widget.remove_view(channel_view)
        if channel in server_view.core.autojoin:
            server_view.core.autojoin.remove(channel)

    else:
        channel_view.userlist.remove_user(parting_nick)

        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"

        channel_view.add_message(
            "*",
            (parting_nick, ["other-nick"]),
            (f" left {channel_view.channel_name}." + extra, []),
            show_in_gui=channel_view.server_view.should_show_join_leave_message(parting_nick),
        )


def _handle_nick(server_view: views.ServerView, old_nick: str, args: list[str]) -> None:
    [new_nick] = args
    if old_nick == server_view.core.nick:
        server_view.core.nick = new_nick
        if server_view.irc_widget.get_current_view().server_view == server_view:
            server_view.irc_widget.nickbutton.config(text=new_nick)

        for view in server_view.get_subviews(include_server=True):
            view.add_message("*", ("You are now known as ", []), (new_nick, ["self-nick"]), (".", []))
            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(old_nick)
                view.userlist.add_user(new_nick)
    else:
        for view in _get_views_relevant_for_nick(server_view, old_nick):
            view.add_message(
                "*", (old_nick, ["other-nick"]), (" is now known as ", []), (new_nick, ["other-nick"]), (".", [])
            )

            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(old_nick)
                view.userlist.add_user(new_nick)

            if isinstance(view, views.PMView):
                # Someone else might have had this nick before
                old_view = server_view.find_pm(new_nick)
                if old_view is not None and old_view != view:
                    server_view.irc_widget.remove_view(old_view)

                view.view_name = new_nick
                view.reopen_log_file()


def _handle_quit(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    if args and args[0]:
        reason_string = " (" + args[0] + ")"
    else:
        reason_string = ""

    # This isn't perfect, other person's QUIT not received if not both joined on the same channel
    for view in _get_views_relevant_for_nick(server_view, nick):
        view.add_message(
            "*",
            (nick, ["other-nick"]),
            (" quit." + reason_string, []),
            show_in_gui=view.server_view.should_show_join_leave_message(nick),
        )
        if isinstance(view, views.ChannelView):
            view.userlist.remove_user(nick)


def _handle_away(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    for view in _get_views_relevant_for_nick(server_view, nick):
        if not args:
            view.add_message("*", (nick, ["other-nick"]), (" is no longer away.", ["info"]))
        else:
            view.add_message("*", (nick, ["other-nick"]), (f' is away. ({" ".join(args)})', ["info"]))


def _handle_mode(server_view: views.ServerView, setter_nick: str, args: list[str]) -> None:
    channel, mode_flags, target_nick = args

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if mode_flags == "+o":
        message = "gives channel operator permissions to"
    elif mode_flags == "-o":
        message = "removes channel operator permissions from"
    else:
        message = f"sets mode {mode_flags} on"

    if target_nick == channel_view.server_view.core.nick:
        target_tag = "self-nick"
    else:
        target_tag = "other-nick"

    if setter_nick == channel_view.server_view.core.nick:
        setter_tag = "self-nick"
    else:
        setter_tag = "other-nick"

    channel_view.add_message(
        "*", (setter_nick, [setter_tag]), (f" {message} ", []), (target_nick, [target_tag]), (".", [])
    )


def _handle_kick(server_view: views.ServerView, kicker: str, args: list[str]) -> None:
    channel, kicked_nick, reason = args

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    channel_view.userlist.remove_user(kicked_nick)
    if kicker == channel_view.server_view.core.nick:
        kicker_tag = "self-nick"
    else:
        kicker_tag = "other-nick"

    if kicked_nick == channel_view.server_view.core.nick:
        channel_view.add_message(
            "*",
            (kicker, [kicker_tag]),
            (" has kicked you from ", ["error"]),
            (channel_view.channel_name, ["channel"]),
            (f". (Reason: {reason or ''}) You can still join by typing ", ["error"]),
            (f"/join {channel_view.channel_name}", ["pinged"]),
            (".", ["error"]),
        )
    else:
        channel_view.add_message(
            "*",
            (kicker, [kicker_tag]),
            (" has kicked ", []),
            (kicked_nick, ["other-nick"]),
            (" from ", []),
            (channel_view.channel_name, ["channel"]),
            (f". (Reason: {reason or ''})", []),
        )


def _handle_cap(server_view: views.ServerView, args: list[str]) -> None:
    subcommand = args[1]
    if subcommand == "ACK":
        acknowledged = set(args[-1].split())
        if "sasl" in acknowledged:
            server_view.core.send("AUTHENTICATE PLAIN")
        if "away-notify" in acknowledged:
            server_view.core.cap_list.add("away-notify")
    elif subcommand == "NAK":
        rejected = set(args[-1].split())
        if "sasl" in rejected:
            # TODO: this good?
            raise ValueError("The server does not support SASL.")
        if "away-notify" in rejected:
            raise ValueError("The server does not support away-notify.")

    server_view.core.send("CAP END")


def _handle_authenticate(server_view: views.ServerView) -> None:
    query = f"\0{server_view.core.username}\0{server_view.core.password}"
    b64_query = b64encode(query.encode("utf-8")).decode("utf-8")
    for i in range(0, len(b64_query), 400):
        server_view.core.send("AUTHENTICATE " + b64_query[i : i + 400])


class _JoinInProgress:
    def __init__(self) -> None:
        self.topic: str | None = None
        self.nicks: list[str] = []


_joins_in_progress: dict[tuple[views.ServerView, str], _JoinInProgress] = {}


def _handle_numeric_rpl_topic(server_view: views.ServerView, args: list[str]) -> None:
    channel, topic = args[1:]
    join = _joins_in_progress.setdefault((server_view, channel), _JoinInProgress())
    join.topic = topic


def _handle_namreply(server_view: views.ServerView, args: list[str]) -> None:
    # TODO: wtf are the first 2 args?
    # rfc1459 doesn't mention them, but freenode
    # gives 4-element msg.args lists
    channel, names = args[-2:]

    # TODO: the prefixes have meanings
    # TODO: get the prefixes actually used from RPL_ISUPPORT
    # https://modern.ircdocs.horse/#channel-membership-prefixes
    join = _joins_in_progress.setdefault((server_view, channel), _JoinInProgress())
    join.nicks.extend(name.lstrip("~&@%+") for name in names.split())


def _handle_endofnames(server_view: views.ServerView, args: list[str]) -> None:
    # joining a channel finished
    channel, human_readable_message = args[-2:]
    join = _joins_in_progress.pop((server_view, channel))

    channel_view = server_view.find_channel(channel)
    if channel_view is None:
        channel_view = views.ChannelView(server_view, channel, join.nicks)
        server_view.irc_widget.add_view(channel_view)
    else:
        # Can exist already, when has been disconnected from server
        channel_view.userlist.set_nicks(join.nicks)

    topic = join.topic or "(no topic)"
    channel_view.add_message("*", (f"The topic of {channel_view.channel_name} is: {topic}", []))

    if channel not in server_view.core.autojoin:
        server_view.core.autojoin.append(channel)


def _handle_endofmotd(server_view: views.ServerView) -> None:
    # TODO: relying on MOTD good?
    for channel in server_view.core.autojoin:
        server_view.core.send(f"JOIN {channel}")
        if "away-notify" in server_view.core.cap_list:
            server_view.core.send_who(channel)


def _handle_whoreply(server_view: views.ServerView, sender: str, command: str, args: list[str]) -> None:
    pass


def _handle_literally_topic(server_view: views.ServerView, who_changed: str, args: list[str]) -> None:
    channel, topic = args
    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if who_changed == channel_view.server_view.core.nick:
        nick_tag = "self-nick"
    else:
        nick_tag = "other-nick"

    channel_view.add_message(
        "*", (who_changed, [nick_tag]), (f" changed the topic of {channel_view.channel_name}: {topic}", [])
    )


def _handle_unknown_message(
    server_view: views.ServerView, sender: str | None, sender_is_server: bool, command: str, args: list[str]
) -> None:
    if sender_is_server:
        # Errors seem to always be 4xx, 5xx or 7xx.
        # Not all 6xx responses are errors, e.g. RPL_STARTTLS = 670
        is_error = command.startswith(("4", "5", "7"))

        if is_error:
            view = server_view.irc_widget.get_current_view()
        else:
            view = server_view
        view.add_message(sender or "???", (" ".join([command] + args), ["error"] if is_error else []))

    else:
        server_view.add_message(sender or "???", (" ".join([command] + args), []))


def _handle_received_message(server_view: views.ServerView, msg: backend.ReceivedLine) -> None:
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

    # TODO: figure out what MODE with 2 args is
    elif msg.command == "MODE" and len(msg.args) != 2:
        assert msg.sender is not None
        _handle_mode(server_view, msg.sender, msg.args)

    elif msg.command == "KICK":
        assert msg.sender is not None
        _handle_kick(server_view, msg.sender, msg.args)

    elif msg.command == "AWAY":
        assert msg.sender is not None
        _handle_away(server_view, msg.sender, msg.args)

    elif msg.command == "CAP":
        _handle_cap(server_view, msg.args)

    elif msg.command == "AUTHENTICATE":
        _handle_authenticate(server_view)

    elif msg.command == RPL_NAMREPLY:
        _handle_namreply(server_view, msg.args)

    elif msg.command == RPL_ENDOFNAMES:
        _handle_endofnames(server_view, msg.args)

    elif msg.command == RPL_ENDOFMOTD:
        _handle_endofmotd(server_view)

    elif msg.command == RPL_TOPIC:
        _handle_numeric_rpl_topic(server_view, msg.args)

    elif msg.command == RPL_WHOREPLY:
        assert msg.sender is not None
        _handle_whoreply(server_view, msg.sender, msg.command, msg.args)

    elif msg.command == "TOPIC" and not msg.sender_is_server:
        assert msg.sender is not None
        _handle_literally_topic(server_view, msg.sender, msg.args)

    else:
        _handle_unknown_message(server_view, msg.sender, msg.sender_is_server, msg.command, msg.args)


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
            view.add_message("", (event.message, ["error" if event.is_error else "info"]))
        return True

    if isinstance(event, backend.HostChanged):
        server_view.view_name = event.new
        for subview in server_view.get_subviews(include_server=True):
            subview.reopen_log_file()
        return True

    if isinstance(event, backend.SentPrivmsg):
        channel_view = server_view.find_channel(event.nick_or_channel)
        if channel_view is None:
            assert not re.fullmatch(backend.CHANNEL_REGEX, event.nick_or_channel), event.nick_or_channel
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
