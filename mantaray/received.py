"""Handle commands received from the IRC server."""

from __future__ import annotations

import sys
import re
from base64 import b64encode
from datetime import datetime

from mantaray import backend, textwidget_tags, views, logs, state

if sys.version_info >= (3, 11):
    from typing import assert_never
else:
    from typing import NoReturn

    def assert_never(value: object) -> NoReturn:
        raise AssertionError(f"this should never happen: {value}")

# Most of these are from https://modern.ircdocs.horse/
RPL_WELCOME = "001"
RPL_UNAWAY = "305"
RPL_NOWAWAY = "306"
RPL_WHOISCERTFP = "276"
RPL_WHOISREGNICK = "307"
RPL_WHOISUSER = "311"
RPL_WHOISSERVER = "312"
RPL_WHOISOPERATOR = "313"
RPL_WHOISIDLE = "317"
RPL_WHOISCHANNELS = "319"
RPL_WHOISSPECIAL = "320"
RPL_WHOISACCOUNT = "330"
RPL_WHOISACTUALLY = "338"
RPL_WHOISHOST = "378"
RPL_WHOISMODES = "379"
RPL_WHOISSECURE = "671"
RPL_ENDOFWHOIS = "318"
RPL_ENDOFMOTD = "376"
RPL_AWAY = "301"
RPL_NAMREPLY = "353"
RPL_ENDOFNAMES = "366"
RPL_WHOREPLY = "352"
RPL_ENDOFWHO = "315"
RPL_SASLSUCCESS = "903"
RPL_LOGGEDIN = "900"
RPL_TOPIC = "332"

ERR_SASLFAIL = "904"

WHOIS_REPLY_CODES = {
    RPL_WHOISCERTFP,
    RPL_WHOISREGNICK,
    RPL_WHOISUSER,
    RPL_WHOISSERVER,
    RPL_WHOISOPERATOR,
    RPL_WHOISIDLE,
    RPL_WHOISCHANNELS,
    RPL_WHOISSPECIAL,
    RPL_WHOISACCOUNT,
    RPL_WHOISACTUALLY,
    RPL_WHOISHOST,
    RPL_WHOISMODES,
    RPL_WHOISSECURE,
    RPL_ENDOFWHOIS,
}


def _get_views_relevant_for_nick(
    server_view: views.ServerView, nick: str
) -> list[views.ChannelView | views.PMView]:
    result: list[views.ChannelView | views.PMView] = []
    for view in server_view.get_subviews():
        if isinstance(view, views.ChannelView) and nick in view.userlist.get_nicks():
            result.append(view)

    pm_view = server_view.find_pm(nick)
    if pm_view is not None:
        result.append(pm_view)

    return result


def _make_view_state(view: views.View) -> state.ViewState:
    view_type = (
        "server"
        if isinstance(view, views.ServerView)
        else "channel"
        if isinstance(view, views.ChannelView)
        else "pm"
    )
    parent_id = None if isinstance(view, views.ServerView) else view.server_view.view_id
    selector_tags = list(view.irc_widget.view_selector.item(view.view_id, "tags"))
    metadata: dict[str, str] = {}
    if isinstance(view, views.ChannelView):
        metadata["channel_name"] = view.channel_name
    elif isinstance(view, views.PMView):
        metadata["other_nick"] = view.nick_of_other_user

    return state.ViewState(
        view_id=view.view_id,
        view_type=view_type,
        name=view.view_name,
        parent_id=parent_id,
        notification_count=view.notification_count,
        selector_tags=selector_tags,
        metadata=metadata,
    )


def _make_server_state(server_view: views.ServerView) -> state.ServerState:
    joined_channels = [view.channel_name for view in server_view.get_subviews() if isinstance(view, views.ChannelView)]
    server_state = state.ServerState(
        server_id=server_view.view_id,
        name=server_view.view_name,
        host=server_view.settings.host,
        nick=server_view.settings.nick,
        connected=not isinstance(server_view.core._connection_state, float),
        joined_channels=joined_channels,
    )
    for view in server_view.get_subviews():
        server_state.add_view(view.view_id)
    return server_state


def _add_privmsg_to_view(
    view: views.ChannelView | views.PMView,
    sender: str,
    text: str,
    *,
    pinged: bool = False,
    history_id: int | None = None,
    notification: bool = False,
    timestamp: datetime | None = None,
) -> None:
    if timestamp is not None:
        assert timestamp.tzinfo is not None

    # /me asdf --> "\x01ACTION asdf\x01"
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        slash_me = True
        text = text[8:-1]
    else:
        slash_me = False

    if isinstance(view, views.ChannelView):
        all_nicks = list(view.userlist.get_nicks())
        if view.server_view.settings.nick not in all_nicks:
            # Possible, if user is kicked
            all_nicks.append(view.server_view.settings.nick)
    else:
        all_nicks = [view.nick_of_other_user, view.server_view.settings.nick]

    parts = []
    for substring, base_tags in textwidget_tags.parse_text(text):
        for subsubstring, nick_tag in backend.find_nicks(
            substring, view.server_view.settings.nick, all_nicks
        ):
            tags = base_tags.copy()
            if nick_tag is not None:
                tags.append(nick_tag)
            parts.append(views.MessagePart(subsubstring, tags=tags))

    if sender == view.server_view.settings.nick:
        sender_tag = "self-nick"
    else:
        sender_tag = "other-nick"

    if slash_me:
        _render_message(
            view,
            [views.MessagePart(sender, tags=[sender_tag]), views.MessagePart(" ")]
            + parts,
            sender=None,
            tag="privmsg",
            pinged=pinged,
            history_id=history_id,
            timestamp=timestamp,
        )
    else:
        _render_message(
            view,
            parts,
            sender=sender,
            tag="privmsg",
            pinged=pinged,
            history_id=history_id,
            timestamp=timestamp,
        )

    if notification:
        if slash_me:
            view.add_notification(f"{sender} {text}")
        else:
            if isinstance(view, views.ChannelView):
                view.add_notification(
                    f"<{sender}> {text}", biberao_mode=(sender == "biberao")
                )
            else:
                view.add_notification(text)


# Also used for messages loaded from logs.
def add_received_privmsg_to_view(
    view: views.ChannelView | views.PMView,
    sender: str,
    text: str,
    *,
    already_seen: bool = False,
    timestamp: datetime | None = None,
) -> None:
    if timestamp is not None:
        assert timestamp.tzinfo is not None

    if isinstance(view, views.PMView):
        _add_privmsg_to_view(view, sender, text, notification=(not already_seen), timestamp=timestamp)
        if not already_seen:
            view.add_view_selector_tag("new_message")
    else:
        pinged = any(
            tag == "self-nick"
            for substring, tag in backend.find_nicks(
                text, view.server_view.settings.nick, [view.server_view.settings.nick]
            )
        )
        _add_privmsg_to_view(
            view,
            sender,
            text,
            pinged=pinged,
            notification=(
                (not already_seen)
                and (
                    pinged
                    or view.channel_name in view.server_view.settings.extra_notifications
                )
            ),
            timestamp=timestamp,
        )
        view.add_view_selector_tag("pinged" if pinged else "new_message")


def _make_message_state(
    message: str | list[views.MessagePart],
    sender: str | None = None,
    *,
    tag: str = "info",
    pinged: bool = False,
    history_id: int | None = None,
    timestamp: datetime | None = None,
) -> state.MessageState:
    if timestamp is None:
        timestamp = datetime.now().astimezone()
    assert timestamp.tzinfo is not None

    if isinstance(message, str):
        message = [views.MessagePart(message)]

    return state.MessageState(
        sender=sender,
        parts=[state.MessagePartState(part.text, tags=part.tags) for part in message],
        tag=tag,
        pinged=pinged,
        history_id=history_id,
        timestamp=timestamp,
    )


def _render_message(
    view: views.View,
    message: str | list[views.MessagePart],
    sender: str | None = None,
    *,
    tag: str = "info",
    pinged: bool = False,
    history_id: int | None = None,
    timestamp: datetime | None = None,
) -> None:
    message_state = _make_message_state(
        message,
        sender,
        tag=tag,
        pinged=pinged,
        history_id=history_id,
        timestamp=timestamp,
    )
    view.add_message_state(message_state)


# privmsg can be a message to a channel or a PM (actual Private Message directly to the user)
def _handle_privmsg(
    server_view: views.ServerView, sender: str, args: list[str]
) -> None:
    # recipient is server or nick
    recipient, text = args

    if recipient == server_view.settings.nick:  # actual PM
        pm_view = server_view.find_or_open_pm(sender)
        add_received_privmsg_to_view(pm_view, sender, text)
    else:
        channel_view = server_view.find_channel(recipient)
        assert channel_view is not None
        add_received_privmsg_to_view(channel_view, sender, text)


def _handle_join(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    # When this user joins a channel, wait for RPL_ENDOFNAMES
    if nick == server_view.settings.nick:
        return

    [channel] = args
    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    channel_view.userlist.add_user(nick)
    server_state = _get_server_state(server_view)
    if server_state is not None:
        _add_channel_user(server_state, channel_view.channel_name, nick)

    # TODO: Add hidden join/leave messages to log? Would cause trouble when
    #       parsing the log, because join/leave messages coming from the log
    #       might need hiding based on user's preferences.
    if channel_view.server_view.should_show_join_leave_message(nick):
        _render_message(
            channel_view,
            [
                views.MessagePart(nick, tags=["other-nick"]),
                views.MessagePart(" joined "),
                views.MessagePart(channel_view.channel_name, tags=["channel"]),
                views.MessagePart("."),
            ],
        )


def _get_server_state(server_view: views.ServerView) -> state.ServerState | None:
    return server_view.irc_widget.app_state.servers.get(server_view.view_id)


def _sync_channel_userlist(
    server_state: state.ServerState, channel: str, nicks: list[str]
) -> None:
    if nicks:
        server_state.userlist[channel] = sorted(nicks, key=str.casefold)
    else:
        server_state.userlist.pop(channel, None)


def _add_channel_user(
    server_state: state.ServerState, channel: str, nick: str
) -> None:
    userlist = server_state.userlist.setdefault(channel, [])
    if nick not in userlist:
        userlist.append(nick)
        userlist.sort(key=str.casefold)


def _remove_channel_user(
    server_state: state.ServerState, channel: str, nick: str
) -> None:
    userlist = server_state.userlist.get(channel)
    if userlist is None:
        return
    if nick in userlist:
        userlist.remove(nick)
    if not userlist:
        server_state.userlist.pop(channel, None)


def _rename_nick_in_userlists(
    server_state: state.ServerState, old_nick: str, new_nick: str
) -> None:
    for channel, nicks in list(server_state.userlist.items()):
        if old_nick in nicks:
            nicks[:] = [new_nick if nick == old_nick else nick for nick in nicks]
            nicks.sort(key=str.casefold)


def _handle_part(
    server_view: views.ServerView, parting_nick: str, args: list[str]
) -> None:
    channel = args[0]
    reason = args[1] if len(args) >= 2 else None

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if parting_nick == server_view.settings.nick:
        server_view.irc_widget.remove_view(channel_view)
        if channel_view.channel_name in server_view.settings.joined_channels:
            server_view.settings.joined_channels.remove(channel_view.channel_name)
        server_state = _get_server_state(server_view)
        if server_state:
            if channel_view.channel_name in server_state.joined_channels:
                server_state.joined_channels.remove(channel_view.channel_name)
            _sync_channel_userlist(server_state, channel_view.channel_name, [])

    else:
        channel_view.userlist.remove_user(parting_nick)
        server_state = _get_server_state(server_view)
        if server_state:
            _remove_channel_user(server_state, channel_view.channel_name, parting_nick)

        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"

        if channel_view.server_view.should_show_join_leave_message(parting_nick):
            _render_message(
                channel_view,
                [
                    views.MessagePart(parting_nick, tags=["other-nick"]),
                    views.MessagePart(" left "),
                    views.MessagePart(channel_view.channel_name, tags=["channel"]),
                    views.MessagePart("."),
                    views.MessagePart(extra),
                ],
            )


def _handle_nick(server_view: views.ServerView, old_nick: str, args: list[str]) -> None:
    new_nick = args[0]
    if old_nick == server_view.settings.nick:
        # Refactoring note: The nick stored in settings will be used to interpret
        # events coming from the backend. If you don't want to save the nick to
        # settings as soon as it changes, you need to separately keep track of the
        # nick that is currently being used.
        server_view.settings.nick = new_nick
        server_view.settings.save()
        server_view.irc_widget.update_nick_button()

        server_state = _get_server_state(server_view)
        if server_state is not None:
            server_state.nick = new_nick

        for view in server_view.get_subviews(include_server=True):
            _render_message(
                view,
                [
                    views.MessagePart("You are now known as "),
                    views.MessagePart(new_nick, tags=["self-nick"]),
                    views.MessagePart("."),
                ],
            )
            if isinstance(view, views.ChannelView):
                view.userlist.change_nick(old_nick, new_nick)

    else:
        server_state = _get_server_state(server_view)
        if server_state is not None:
            _rename_nick_in_userlists(server_state, old_nick, new_nick)

        for view in _get_views_relevant_for_nick(server_view, old_nick):
            _render_message(
                view,
                [
                    views.MessagePart(old_nick, tags=["other-nick"]),
                    views.MessagePart(" is now known as "),
                    views.MessagePart(new_nick, tags=["other-nick"]),
                    views.MessagePart("."),
                ],
            )

            if isinstance(view, views.ChannelView):
                view.userlist.change_nick(old_nick, new_nick)

            if isinstance(view, views.PMView):
                # Someone else might have had this nick before
                old_view = server_view.find_pm(new_nick)
                if old_view is not None and old_view != view:
                    server_view.irc_widget.remove_view(old_view)

                logs.stop_logging(view)
                view.view_name = new_nick
                view.view_state.metadata["other_nick"] = new_nick
                logs.start_logging(view)


def _handle_quit(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    if args and args[0]:
        reason_string = " (" + args[0] + ")"
    else:
        reason_string = ""

    server_state = _get_server_state(server_view)
    for view in _get_views_relevant_for_nick(server_view, nick):
        if view.server_view.should_show_join_leave_message(nick):
            _render_message(view, [views.MessagePart(" quit." + reason_string)])
        if isinstance(view, views.ChannelView):
            view.userlist.remove_user(nick)
            if server_state is not None:
                _remove_channel_user(server_state, view.channel_name, nick)


def _handle_away(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    for view in _get_views_relevant_for_nick(server_view, nick):
        if isinstance(view, views.ChannelView):
            if args and args[0]:
                view.userlist.set_away(nick, is_away=True, reason=args[0])
            else:
                view.userlist.set_away(nick, is_away=False)


def _handle_ping(server_view: views.ServerView, args: list[str]) -> None:
    [send_this_unchanged] = args
    server_view.core.send(f"PONG :{send_this_unchanged}")


def _handle_mode(
    server_view: views.ServerView, setter_nick: str, args: list[str]
) -> None:
    channel, mode_flags, target_nick = args

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if mode_flags == "+o":
        message = "gives channel operator permissions to"
    elif mode_flags == "-o":
        message = "removes channel operator permissions from"
    else:
        message = f"sets mode {mode_flags} on"

    if target_nick == channel_view.server_view.settings.nick:
        target_tag = "self-nick"
    else:
        target_tag = "other-nick"

    if setter_nick == channel_view.server_view.settings.nick:
        setter_tag = "self-nick"
    else:
        setter_tag = "other-nick"

    _render_message(
        channel_view,
        [
            views.MessagePart(setter_nick, tags=[setter_tag]),
            views.MessagePart(f" {message} "),
            views.MessagePart(target_nick, tags=[target_tag]),
            views.MessagePart("."),
        ],
    )


def _handle_kick(server_view: views.ServerView, kicker: str, args: list[str]) -> None:
    channel, kicked_nick, reason = args

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    channel_view.userlist.remove_user(kicked_nick)
    server_state = _get_server_state(server_view)
    if server_state is not None:
        _remove_channel_user(server_state, channel_view.channel_name, kicked_nick)

    if kicker == channel_view.server_view.settings.nick:
        kicker_tag = "self-nick"
    else:
        kicker_tag = "other-nick"

    if kicked_nick == channel_view.server_view.settings.nick:
        _render_message(
            channel_view,
            [
                views.MessagePart(kicker, tags=[kicker_tag]),
                views.MessagePart(" has kicked you from "),
                # TODO: Make channel tag clickable?
                views.MessagePart(channel_view.channel_name, tags=["channel"]),
                views.MessagePart(
                    f". (Reason: {reason}) You can still join by typing "
                ),
                # TODO: new tag instead of abusing the "pinged" tag for this
                views.MessagePart(
                    f"/join {channel_view.channel_name}", tags=["pinged"]
                ),
                views.MessagePart("."),
            ],
            tag="error",
        )
    else:
        _render_message(
            channel_view,
            [
                views.MessagePart(kicker, tags=[kicker_tag]),
                views.MessagePart(" has kicked "),
                views.MessagePart(kicked_nick, tags=["other-nick"]),
                views.MessagePart(" from "),
                # TODO: Make channel tag clickable?
                views.MessagePart(channel_view.channel_name, tags=["channel"]),
                views.MessagePart(f". (Reason: {reason})"),
            ],
        )


def _handle_cap(server_view: views.ServerView, args: list[str]) -> None:
    subcommand = args[1]
    if subcommand == "ACK":
        acknowledged = args[-1].split()
        server_view.core.pending_cap_count -= len(acknowledged)

        if "sasl" in acknowledged:
            server_view.core.send("AUTHENTICATE PLAIN")

        for capability in acknowledged:
            server_view.core.cap_list.add(capability)

    elif subcommand == "NAK":
        rejected = args[-1].split()
        server_view.core.pending_cap_count -= len(rejected)
        if "sasl" in rejected:
            # TODO: this good?
            raise ValueError("The server does not support SASL.")

    else:
        server_view.core.send("CAP END")
        raise ValueError("Invalid CAP response. Aborting Capability Negotiation.")

    # If we use SASL, we can't send CAP END until all SASL stuff is done.
    # If "sasl" is in cap_list, Mantaray sends CAP END after the server
    # has replied with RPL_SASLSUCCESS or ERR_SASLFAIL
    if (
        server_view.core.pending_cap_count == 0
        and "sasl" not in server_view.core.cap_list
    ):
        server_view.core.send("CAP END")


def _handle_authenticate(server_view: views.ServerView) -> None:
    query = f"\0{server_view.settings.username}\0{server_view.settings.password}"
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


def _handle_whois_reply(
    server_view: views.ServerView, msg: backend.MessageFromServer
) -> None:
    nick = msg.args[1]

    if msg.command == RPL_WHOISACCOUNT:
        # msg.args=["Alice", "foo", "bar", "is logged in as"] --> "foo is logged in as bar"
        assert len(msg.args) == 4
        text = f"{msg.command} {nick} {msg.args[3]} {msg.args[2]}"
    else:
        text = f"{msg.command} {nick} {' '.join(msg.args[2:])}"

    if nick == server_view.settings.nick:
        # This is a reply to running WHOIS on the current user.
        if msg.command == RPL_WHOISUSER:
            server_view.core.set_nickmask(user=msg.args[2], host=msg.args[3])
        _render_message(server_view, text)
    else:
        _render_message(server_view.find_or_open_pm(nick, select_existing=True), text)


# This can be part of a WHOIS response or it can appear separately.
def _handle_other_user_away_reply(
    server_view: views.ServerView, args: list[str]
) -> None:
    nick, reason = args[1:]
    for view in _get_views_relevant_for_nick(server_view, nick):
        if isinstance(view, views.PMView):
            _render_message(view, f"{nick} is marked as being away: {reason}")
        else:
            view.userlist.set_away(nick, is_away=True, reason=reason)


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

    server_state = _get_server_state(server_view)
    if server_state is not None:
        _sync_channel_userlist(server_state, channel, join.nicks)


# While waiting for a response to a WHO, don't send another WHO.
# This prevents the server from deciding to disconnect because it's
# being asked to send a lot of data quickly.
#
# TODO: clear this when reconnecting
_pending_who_sends: dict[views.ServerView, list[str]] = {}


def _handle_endofnames(server_view: views.ServerView, args: list[str]) -> None:
    # joining a channel finished
    channel, human_readable_message = args[-2:]
    join = _joins_in_progress.pop((server_view, channel))

    channel_view = server_view.find_channel(channel)
    if channel_view is None:
        channel_view = views.ChannelView(server_view, channel, join.nicks)
        server_view.irc_widget.add_view(channel_view)
        logs.read_old_logs(channel_view)
        logs.start_logging(channel_view)
    else:
        # Can exist already, when has been disconnected from server
        channel_view.userlist.set_nicks(join.nicks)

    server_state = _get_server_state(server_view)
    if server_state is not None:
        _sync_channel_userlist(server_state, channel_view.channel_name, list(channel_view.userlist.get_nicks()))

    if "away-notify" in server_view.core.cap_list:
        if server_view in _pending_who_sends:
            # WHO sending is currently in progress, queue the next one
            _pending_who_sends[server_view].append(channel)
        else:
            _pending_who_sends[server_view] = []
            server_view.core.send(f"WHO {channel}")

    topic = join.topic or "(no topic)"
    _render_message(
        channel_view,
        [
            views.MessagePart("The topic of "),
            views.MessagePart(channel_view.channel_name, tags=["channel"]),
            views.MessagePart(" is: "),
            views.MessagePart(topic, tags=["topic"]),
        ],
    )

    if (
        channel == server_view.last_slash_join_channel
        and channel not in server_view.settings.joined_channels
    ):
        server_view.settings.joined_channels.append(channel)
        server_state = _get_server_state(server_view)
        if server_state and channel not in server_state.joined_channels:
            server_state.joined_channels.append(channel)
        server_view.last_slash_join_channel = None


def _handle_endofmotd(server_view: views.ServerView) -> None:
    server_view.core.send(f"WHOIS {server_view.settings.nick}")

    channel_views = [
        v for v in server_view.get_subviews() if isinstance(v, views.ChannelView)
    ]
    if channel_views:
        # Reconnect after connectivity error, join whatever channels are open
        for view in server_view.get_subviews():
            if isinstance(view, views.ChannelView):
                server_view.core.send(f"JOIN {view.channel_name}")
    else:
        # Mantaray just started, connect according to settings
        for channel in server_view.settings.joined_channels:
            server_view.core.send(f"JOIN {channel}")


def _handle_whoreply(server_view: views.ServerView, args: list[str]) -> None:
    assert len(args) == 8
    nick = args[5]
    away_status = args[6][0]
    view = server_view.find_channel(args[1])

    assert view is not None
    assert away_status.lower() == "g" or away_status.lower() == "h"

    if away_status.lower() == "g":
        # The WHO reply contains info about whether the user is away or not, but
        # not the reason/message why they are away
        view.userlist.set_away(nick, is_away=True, reason=None)


def _handle_endofwho(server_view: views.ServerView) -> None:
    if _pending_who_sends[server_view]:
        channel = _pending_who_sends[server_view].pop()
        server_view.core.send(f"WHO {channel}")
    else:
        del _pending_who_sends[server_view]


def _handle_literally_topic(
    server_view: views.ServerView, who_changed: str, args: list[str]
) -> None:
    channel, topic = args
    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    if who_changed == channel_view.server_view.settings.nick:
        nick_tag = "self-nick"
    else:
        nick_tag = "other-nick"

    _render_message(
        channel_view,
        [
            views.MessagePart(who_changed, tags=[nick_tag]),
            views.MessagePart(" changed the topic of "),
            views.MessagePart(channel_view.channel_name, tags=["channel"]),
            views.MessagePart(": "),
            views.MessagePart(topic, tags=["topic"]),
        ],
    )


def _handle_unknown_message(
    server_view: views.ServerView,
    msg: backend.MessageFromServer | backend.MessageFromUser,
) -> None:
    sender = (
        msg.server if isinstance(msg, backend.MessageFromServer) else msg.sender_nick
    )
    text = " ".join([msg.command] + msg.args)

    # Errors seem to always be 4xx, 5xx or 7xx.
    # Not all 6xx responses are errors, e.g. RPL_STARTTLS = 670
    if isinstance(msg, backend.MessageFromServer) and msg.command.startswith(
        ("4", "5", "7")
    ):
        for view in server_view.get_subviews(include_server=True):
            _render_message(view, text, sender, tag="error")
    else:
        _render_message(server_view, text, sender)


def _handle_received_message(
    server_view: views.ServerView,
    msg: backend.MessageFromServer | backend.MessageFromUser,
) -> None:
    if msg.command == "PRIVMSG":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_privmsg(server_view, msg.sender_nick, msg.args)

    elif msg.command == "JOIN":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_join(server_view, msg.sender_nick, msg.args)

    elif msg.command == "PART":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_part(server_view, msg.sender_nick, msg.args)

    elif msg.command == "NICK":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_nick(server_view, msg.sender_nick, msg.args)

    elif msg.command == "QUIT":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_quit(server_view, msg.sender_nick, msg.args)

    elif msg.command == "PING":
        _handle_ping(server_view, msg.args)

    # TODO: figure out what MODE with 2 or 4 args is
    elif msg.command == "MODE" and len(msg.args) == 3:
        assert isinstance(msg, backend.MessageFromUser)
        _handle_mode(server_view, msg.sender_nick, msg.args)

    elif msg.command == "KICK":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_kick(server_view, msg.sender_nick, msg.args)

    elif msg.command == "AWAY":
        assert isinstance(msg, backend.MessageFromUser)
        _handle_away(server_view, msg.sender_nick, msg.args)

    elif msg.command == "CAP":
        _handle_cap(server_view, msg.args)

    elif msg.command == "AUTHENTICATE":
        _handle_authenticate(server_view)

    elif msg.command == RPL_WELCOME and msg.args[0] != server_view.settings.nick:
        # Use whatever nickname the server tells us to use.
        # Needed e.g. when nick is in use and you changed nick during connecting.
        _handle_nick(server_view, server_view.settings.nick, msg.args)

    elif msg.command == RPL_SASLSUCCESS or msg.command == ERR_SASLFAIL:
        assert isinstance(msg, backend.MessageFromServer)
        _render_message(server_view, f"{msg.command} {' '.join(msg.args)}", msg.server)
        server_view.core.send("CAP END")

    elif msg.command == RPL_NAMREPLY:
        _handle_namreply(server_view, msg.args)

    elif msg.command == RPL_ENDOFNAMES:
        _handle_endofnames(server_view, msg.args)

    elif msg.command == RPL_ENDOFMOTD:
        _handle_endofmotd(server_view)

    elif msg.command == RPL_TOPIC:
        _handle_numeric_rpl_topic(server_view, msg.args)

    elif msg.command in WHOIS_REPLY_CODES:
        assert isinstance(msg, backend.MessageFromServer)
        _handle_whois_reply(server_view, msg)

    elif msg.command == RPL_AWAY:
        _handle_other_user_away_reply(server_view, msg.args)

    elif msg.command == RPL_WHOREPLY:
        _handle_whoreply(server_view, msg.args)

    elif msg.command == RPL_ENDOFWHO:
        _handle_endofwho(server_view)

    elif msg.command == RPL_UNAWAY:
        back_notification = msg.args[1]
        for user_view in server_view.get_subviews(include_server=True):
            _render_message(user_view, back_notification)
            if isinstance(user_view, views.ChannelView):
                user_view.userlist.set_away(server_view.settings.nick, False)

        server_view.core.is_away = False
        server_state = _get_server_state(server_view)
        if server_state is not None:
            server_state.away_status = None
        server_view.irc_widget.update_nick_button()

    elif msg.command == RPL_NOWAWAY:
        away_notification = msg.args[1]
        for user_view in server_view.get_subviews(include_server=True):
            _render_message(user_view, away_notification)
            if isinstance(user_view, views.ChannelView):
                user_view.userlist.set_away(
                    server_view.settings.nick,
                    is_away=True,
                    reason=server_view.last_away_status,
                )

        server_view.core.is_away = True
        server_state = _get_server_state(server_view)
        if server_state is not None:
            server_state.away_status = server_view.last_away_status
        server_view.irc_widget.update_nick_button()

    elif msg.command == "TOPIC" and isinstance(msg, backend.MessageFromUser):
        _handle_literally_topic(server_view, msg.sender_nick, msg.args)

    else:
        _handle_unknown_message(server_view, msg)


def handle_event(event: backend.IrcEvent, server_view: views.ServerView) -> None:
    if isinstance(event, (backend.MessageFromServer, backend.MessageFromUser)):
        _handle_received_message(server_view, event)

    elif isinstance(event, backend.ConnectivityMessage):
        for view in server_view.get_subviews(include_server=True):
            _render_message(
                view,
                event.message,
                tag=("error" if event.is_error else "info"),
            )

        # When reconnecting, the user is marked as not being away.
        # This can affect the nick button because it shows whether the user is away.
        server_view.irc_widget.update_nick_button()

    elif isinstance(event, backend.HostChanged):
        server_state = _get_server_state(server_view)
        if server_state is not None:
            server_state.host = event.new
            server_state.name = event.new
        for subview in server_view.get_subviews(include_server=True):
            logs.stop_logging(subview)
        server_view.view_name = event.new
        for subview in server_view.get_subviews(include_server=True):
            logs.start_logging(subview)

    elif isinstance(event, backend.SentPrivmsg):
        channel_view = server_view.find_channel(event.nick_or_channel)
        if channel_view is None:
            assert not re.fullmatch(backend.CHANNEL_REGEX, event.nick_or_channel), (
                event.nick_or_channel
            )
            pm_view = server_view.find_or_open_pm(event.nick_or_channel)

            # /msg NickServ identify <password>   --> hide password
            text = event.text
            if (
                pm_view.nick_of_other_user.lower() == "nickserv"
                and text.lower().startswith("identify ")
            ):
                text = text[:9] + "********"

            _add_privmsg_to_view(
                pm_view, server_view.settings.nick, text, history_id=event.history_id
            )
        else:
            _add_privmsg_to_view(
                channel_view,
                server_view.settings.nick,
                event.text,
                history_id=event.history_id,
            )

    else:
        assert_never(event)
