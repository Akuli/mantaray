"""Handle commands received from the IRC server."""

from __future__ import annotations
import re
from mantaray import backend, views


def _nick_is_relevant_for_view(nick: str, view: views.View) -> bool:
    if isinstance(view, views.ChannelView):
        return isinstance(view, views.ChannelView)
    if isinstance(view, views.PMView):
        return nick == view.nick_of_other_user
    return False


# Returns True this function should be called again, False if quitting
def handle_event(event: backend._IrcEvent, server_view: views.ServerView) -> None:
    if isinstance(event, backend.SelfJoined):
        channel_view = server_view.find_channel(event.channel)
        if channel_view is None:
            channel_view = views.ChannelView(server_view, event.channel, event.nicklist)
            server_view.irc_widget.add_view(channel_view)
        else:
            # Can exist already, when has been disconnected from server
            channel_view.userlist.set_nicks(event.nicklist)

        channel_view.show_topic(event.topic)
        if event.channel not in server_view.core.autojoin:
            server_view.core.autojoin.append(event.channel)

    elif isinstance(event, backend.SelfParted):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        server_view.irc_widget.remove_view(channel_view)
        if event.channel in server_view.core.autojoin:
            server_view.core.autojoin.remove(event.channel)

    elif isinstance(event, backend.SelfChangedNick):
        if server_view.irc_widget.get_current_view().server_view == server_view:
            server_view.irc_widget.nickbutton.config(text=event.new)

        for view in server_view.get_subviews(include_server=True):
            view.add_message(
                "*",
                ("You are now known as ", []),
                (event.new, ["self-nick"]),
                (".", []),
            )
            if isinstance(view, views.ChannelView):
                view.userlist.remove_user(event.old)
                view.userlist.add_user(event.new)

    elif isinstance(event, backend.SelfQuit):
        server_view.irc_widget.remove_server(server_view)
        return False

    elif isinstance(event, backend.UserJoined):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.on_join(event.nick)

    elif isinstance(event, backend.UserParted):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.on_part(event.nick, event.reason)

    elif isinstance(event, backend.ModeChange):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.on_mode_change(
            event.setter_nick, event.mode_flags, event.target_nick
        )

    elif isinstance(event, backend.Kick):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.on_kick(event.kicker, event.kicked_nick, event.reason)

    elif isinstance(event, backend.UserQuit):
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

    elif isinstance(event, backend.UserChangedNick):
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

    elif isinstance(event, backend.SentPrivmsg):
        channel_view = server_view.find_channel(event.recipient)
        if channel_view is None:
            assert not re.fullmatch(
                backend.CHANNEL_REGEX, event.recipient
            ), event.recipient
            pm_view = server_view.find_pm(event.recipient)
            if pm_view is None:
                # start of a new PM conversation
                pm_view = views.PMView(server_view, event.recipient)
                server_view.irc_widget.add_view(pm_view)
            pm_view.on_privmsg(server_view.core.nick, event.text)
        else:
            channel_view.on_privmsg(server_view.core.nick, event.text)

    elif isinstance(event, backend.ReceivedPrivmsg):
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
                tag == "server_view-nick"
                for substring, tag in backend.find_nicks(
                    event.text, server_view.core.nick, [server_view.core.nick]
                )
            )
            channel_view.on_privmsg(event.sender, event.text, pinged=pinged)
            channel_view.add_tag("pinged" if pinged else "new_message")
            if pinged or (channel_view.channel_name in server_view.extra_notifications):
                channel_view.add_notification(f"<{event.sender}> {event.text}")

    elif isinstance(event, backend.ServerMessage):
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

    elif isinstance(event, backend.UnknownMessage):
        server_view.add_message(
            event.sender or "???", (" ".join([event.command] + event.args), [])
        )

    elif isinstance(event, backend.ConnectivityMessage):
        for view in server_view.get_subviews(include_server=True):
            view.add_message(
                "", (event.message, ["error" if event.is_error else "info"])
            )

    elif isinstance(event, backend.TopicChanged):
        channel_view = server_view.find_channel(event.channel)
        assert channel_view is not None
        channel_view.on_topic_changed(event.who_changed, event.topic)

    elif isinstance(event, backend.HostChanged):
        server_view.view_name = event.new
        for subview in server_view.get_subviews(include_server=True):
            subview.reopen_log_file()

    else:
        # If mypy says 'error: unused "type: ignore" comment', you
        # forgot to check for some class
        print("can't happen")  # type: ignore

    return True
