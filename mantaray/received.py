"""Handle commands received from the IRC server."""

from __future__ import annotations

import sys
import re
from base64 import b64encode
from datetime import datetime

from mantaray import backend, textwidget_tags, views, logs

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
        view.add_message(
            [views.MessagePart(sender, tags=[sender_tag]), views.MessagePart(" ")]
            + parts,
            pinged=pinged,
            history_id=history_id,
            timestamp=timestamp,
        )
    else:
        view.add_message(
            parts,
            sender,
            sender_tag=sender_tag,
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


def _handle_received_pm(server_view: views.ServerView, event: backend.ReceivePM) -> None:
    pm_view = server_view.find_or_open_pm(event.sender_nick)
    add_received_privmsg_to_view(pm_view, event.sender_nick, event.text)


def _handle_channel_message(server_view: views.ServerView, event: backend.ChannelMessage) -> None:
    channel_view = server_view.find_channel(event.channel)
    assert channel_view is not None
    add_received_privmsg_to_view(channel_view, event.sender_nick, event.text)


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

    else:
        channel_view.userlist.remove_user(parting_nick)

        if reason is None:
            extra = ""
        else:
            extra = " (" + reason + ")"

        if channel_view.server_view.should_show_join_leave_message(parting_nick):
            channel_view.add_message(
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

        for view in server_view.get_subviews(include_server=True):
            view.add_message(
                [
                    views.MessagePart("You are now known as "),
                    views.MessagePart(new_nick, tags=["self-nick"]),
                    views.MessagePart("."),
                ]
            )
            if isinstance(view, views.ChannelView):
                view.userlist.change_nick(old_nick, new_nick)

    else:
        for view in _get_views_relevant_for_nick(server_view, old_nick):
            view.add_message(
                [
                    views.MessagePart(old_nick, tags=["other-nick"]),
                    views.MessagePart(" is now known as "),
                    views.MessagePart(new_nick, tags=["other-nick"]),
                    views.MessagePart("."),
                ]
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
                logs.start_logging(view)


def _handle_quit(server_view: views.ServerView, nick: str, args: list[str]) -> None:
    if args and args[0]:
        reason_string = " (" + args[0] + ")"
    else:
        reason_string = ""

    # This isn't perfect, other person's QUIT not received if not both joined on the same channel
    for view in _get_views_relevant_for_nick(server_view, nick):
        if view.server_view.should_show_join_leave_message(nick):
            view.add_message(
                [
                    views.MessagePart(nick, tags=["other-nick"]),
                    views.MessagePart(" quit." + reason_string),
                ],
            )
        if isinstance(view, views.ChannelView):
            view.userlist.remove_user(nick)


def _handle_away(server_view: views.ServerView, event: backend.Away) -> None:
    for view in _get_views_relevant_for_nick(server_view, event.nick):
        if isinstance(view, views.ChannelView):
            view.userlist.set_away(event.nick, is_away=True, reason=event.reason)


def _handle_back(server_view: views.ServerView, event: backend.Back) -> None:
    for view in _get_views_relevant_for_nick(server_view, event.nick):
        if isinstance(view, views.ChannelView):
            view.userlist.set_away(event.nick, is_away=False)


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

    channel_view.add_message(
        [
            views.MessagePart(setter_nick, tags=[setter_tag]),
            views.MessagePart(f" {message} "),
            views.MessagePart(target_nick, tags=[target_tag]),
            views.MessagePart("."),
        ]
    )


def _handle_kick(server_view: views.ServerView, kicker: str, args: list[str]) -> None:
    channel, kicked_nick, reason = args

    channel_view = server_view.find_channel(channel)
    assert channel_view is not None

    channel_view.userlist.remove_user(kicked_nick)
    if kicker == channel_view.server_view.settings.nick:
        kicker_tag = "self-nick"
    else:
        kicker_tag = "other-nick"

    if kicked_nick == channel_view.server_view.settings.nick:
        channel_view.add_message(
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
        channel_view.add_message(
            [
                views.MessagePart(kicker, tags=[kicker_tag]),
                views.MessagePart(" has kicked "),
                views.MessagePart(kicked_nick, tags=["other-nick"]),
                views.MessagePart(" from "),
                # TODO: Make channel tag clickable?
                views.MessagePart(channel_view.channel_name, tags=["channel"]),
                views.MessagePart(f". (Reason: {reason})"),
            ]
        )


def _handle_authenticate(server_view: views.ServerView) -> None:
    query = f"\0{server_view.settings.username}\0{server_view.settings.password}"
    b64_query = b64encode(query.encode("utf-8")).decode("utf-8")
    for i in range(0, len(b64_query), 400):
        server_view.core.send("AUTHENTICATE " + b64_query[i : i + 400])


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
        server_view.add_message(text)
    else:
        server_view.find_or_open_pm(nick, select_existing=True).add_message(text)


# This can be part of a WHOIS response or it can appear separately.
def _handle_other_user_away_reply(
    server_view: views.ServerView, args: list[str]
) -> None:
    nick, reason = args[1:]
    for view in _get_views_relevant_for_nick(server_view, nick):
        if isinstance(view, views.PMView):
            view.add_message(f"{nick} is marked as being away: {reason}")
        else:
            view.userlist.set_away(nick, is_away=True, reason=reason)


def _handle_i_joined_channel(server_view: views.ServerView, event: backend.IJoinedChannel) -> None:
    channel_view = server_view.find_channel(event.channel)
    if channel_view is None:
        channel_view = views.ChannelView(server_view, event.channel, event.nicks)
        server_view.irc_widget.add_view(channel_view)
        logs.read_old_logs(channel_view)
        logs.start_logging(channel_view)
    else:
        # Can exist already, when has been disconnected from server
        channel_view.userlist.set_nicks(event.nicks)

    topic = event.topic or "(no topic)"
    channel_view.add_message(
        [
            views.MessagePart("The topic of "),
            views.MessagePart(channel_view.channel_name, tags=["channel"]),
            views.MessagePart(" is: "),
            views.MessagePart(topic, tags=["topic"]),
        ]
    )

    if (
        event.channel == server_view.last_slash_join_channel
        and event.channel not in server_view.settings.joined_channels
    ):
        server_view.settings.joined_channels.append(event.channel)
        server_view.last_slash_join_channel = None


def _handle_other_user_joined_channel(server_view: views.ServerView, event: backend.OtherUserJoinedChannel) -> None:
    channel_view = server_view.find_channel(event.channel)
    assert channel_view is not None

    channel_view.userlist.add_user(event.nick)
    # TODO: Add hidden join/leave messages to log? Would cause trouble when
    #       parsing the log, because join/leave messages coming from the log
    #       might need hiding based on user's preferences.
    if channel_view.server_view.should_show_join_leave_message(event.nick):
        channel_view.add_message(
            [
                views.MessagePart(event.nick, tags=["other-nick"]),
                views.MessagePart(" joined "),
                views.MessagePart(channel_view.channel_name, tags=["channel"]),
                views.MessagePart("."),
            ],
        )


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

    channel_view.add_message(
        [
            views.MessagePart(who_changed, tags=[nick_tag]),
            views.MessagePart(" changed the topic of "),
            views.MessagePart(channel_view.channel_name, tags=["channel"]),
            views.MessagePart(": "),
            views.MessagePart(topic, tags=["topic"]),
        ]
    )


def _handle_unknown_message(
    server_view: views.ServerView,
    msg: backend.MessageFromServer | backend.MessageFromUser,
) -> None:
    sender = (
        msg.server if isinstance(msg, backend.MessageFromServer) else msg.sender_nick
    )
    text = " ".join([msg.command] + msg.args)

    if isinstance(msg, backend.MessageFromServer) and msg.is_error:
        for view in server_view.get_subviews(include_server=True):
            view.add_message(text, sender, tag="error")
    else:
        server_view.add_message(text, sender)


def _handle_received_message(
    server_view: views.ServerView,
    msg: backend.MessageFromServer | backend.MessageFromUser,
) -> None:
    if msg.command == "PART":
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

    elif msg.command == "AUTHENTICATE":
        _handle_authenticate(server_view)

    elif msg.command == RPL_WELCOME and msg.args[0] != server_view.settings.nick:
        # Use whatever nickname the server tells us to use.
        # Needed e.g. when nick is in use and you changed nick during connecting.
        _handle_nick(server_view, server_view.settings.nick, msg.args)

    elif msg.command == RPL_ENDOFMOTD:
        _handle_endofmotd(server_view)

    elif msg.command in WHOIS_REPLY_CODES:
        assert isinstance(msg, backend.MessageFromServer)
        _handle_whois_reply(server_view, msg)

    elif msg.command == RPL_AWAY:
        _handle_other_user_away_reply(server_view, msg.args)

    elif msg.command == RPL_WHOREPLY:
        _handle_whoreply(server_view, msg.args)

    elif msg.command == RPL_UNAWAY:
        back_notification = msg.args[1]
        for user_view in server_view.get_subviews(include_server=True):
            user_view.add_message(back_notification)
            if isinstance(user_view, views.ChannelView):
                user_view.userlist.set_away(server_view.settings.nick, False)

        server_view.core.is_away = False
        server_view.irc_widget.update_nick_button()

    elif msg.command == RPL_NOWAWAY:
        away_notification = msg.args[1]
        for user_view in server_view.get_subviews(include_server=True):
            user_view.add_message(away_notification)
            if isinstance(user_view, views.ChannelView):
                user_view.userlist.set_away(
                    server_view.settings.nick,
                    is_away=True,
                    reason=server_view.last_away_status,
                )

        server_view.core.is_away = True
        server_view.irc_widget.update_nick_button()

    elif msg.command == "TOPIC" and isinstance(msg, backend.MessageFromUser):
        _handle_literally_topic(server_view, msg.sender_nick, msg.args)

    else:
        _handle_unknown_message(server_view, msg)


def handle_event(event: backend.IrcEvent, server_view: views.ServerView) -> None:
    match event:
        case backend.ReceivePM():
            _handle_received_pm(server_view, event)
            return
        case backend.ChannelMessage():
            _handle_channel_message(server_view, event)
            return
        case backend.IJoinedChannel():
            _handle_i_joined_channel(server_view, event)
            return
        case backend.OtherUserJoinedChannel():
            _handle_other_user_joined_channel(server_view, event)
            return
        case backend.Away():
            _handle_away(server_view, event)
            return
        case backend.Back():
            _handle_back(server_view, event)
            return

    if isinstance(event, (backend.MessageFromServer, backend.MessageFromUser)):
        _handle_received_message(server_view, event)

    elif isinstance(event, backend.ConnectivityMessage):
        for view in server_view.get_subviews(include_server=True):
            view.add_message(event.message, tag=("error" if event.is_error else "info"))

        # When reconnecting, the user is marked as not being away.
        # This can affect the nick button because it shows whether the user is away.
        server_view.irc_widget.update_nick_button()

    elif isinstance(event, backend.HostChanged):
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
