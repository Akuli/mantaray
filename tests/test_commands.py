import os
import re

import pytest

from mantaray.views import ChannelView, PMView, ServerView

# https://stackoverflow.com/a/30575822
params = ["/part", "/part #lol"]
if os.environ["IRC_SERVER"] == "hircd":
    params.append(
        pytest.param(
            "/part #LOL", marks=pytest.mark.xfail(reason="hircd is case-sensitive")
        )
    )
else:
    params.append("/part #LOL")


# TODO: should test entering channel name case insensitively, but hircd is case sensitive :(
@pytest.mark.parametrize("part_command", params)
def test_join_and_part(alice, bob, wait_until, part_command):
    alice.entry.insert(0, "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())

    bob.entry.insert(0, "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in bob.text())
    wait_until(lambda: "Bob joined #lol.\n" in alice.text())
    assert bob.settings.servers[0].joined_channels == ["#autojoin", "#lol"]

    bob.move_view_up()
    assert bob.settings.servers[0].joined_channels == ["#lol", "#autojoin"]

    bob.entry.insert(0, part_command)
    bob.on_enter_pressed()
    wait_until(lambda: not bob.get_server_views()[0].find_channel("#lol"))
    wait_until(lambda: "Bob left #lol.\n" in alice.text())
    assert bob.settings.servers[0].joined_channels == ["#autojoin"]


def test_part_last_channel(alice, bob, wait_until):
    alice.entry.insert(0, "/part #autojoin")
    alice.on_enter_pressed()
    wait_until(lambda: isinstance(alice.get_current_view(), ServerView))


def test_nick_change(alice, bob, wait_until):
    alice.entry.insert(0, "/nick lolwat")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as lolwat.\n" in alice.text())
    wait_until(lambda: "Alice is now known as lolwat.\n" in bob.text())

    alice.entry.insert(0, "/nick LolWat")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as LolWat.\n" in alice.text())
    wait_until(lambda: "lolwat is now known as LolWat.\n" in bob.text())


def test_extra_spaces_ignored(alice, wait_until):
    alice.entry.insert(0, "/nick lolwat     ")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as lolwat.\n" in alice.text())

    alice.entry.insert(0, "/nick    lolwat2")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as lolwat2.\n" in alice.text())


@pytest.mark.xfail(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd is buggy", strict=True
)
def test_topic_change(alice, bob, wait_until):
    alice.entry.insert(0, "/topic blah blah")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice changed the topic of #autojoin: blah blah\n" in alice.text()
    )
    wait_until(
        lambda: "Alice changed the topic of #autojoin: blah blah\n" in bob.text()
    )

    bob.entry.insert(0, "/part #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "blah blah" not in bob.text())

    bob.entry.insert(0, "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #autojoin is: blah blah\n" in bob.text())

    bob.entry.insert(0, "/topic blah blah")
    bob.on_enter_pressed()
    wait_until(lambda: "482 Bob #autojoin You're not channel operator" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support KICK"
)
def test_kick(alice, bob, wait_until, switch_view):
    alice.entry.insert(0, "/kick bob")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice has kicked Bob from #autojoin. (Reason: Bob)" in alice.text()
    )
    wait_until(
        lambda: (
            "Alice has kicked you from #autojoin. (Reason: Bob) You can still join by typing /join #autojoin."
            in bob.text()
        )
    )

    bob.entry.insert(0, "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: bob.text().count("The topic of #autojoin is") == 2)

    alice.entry.insert(0, "/kick bob insane trolling")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "Alice has kicked Bob from #autojoin. (Reason: insane trolling)"
            in alice.text()
        )
    )
    wait_until(
        lambda: (
            "Alice has kicked you from #autojoin. (Reason: insane trolling) You can still join by typing /join #autojoin."
            in bob.text()
        )
    )

    bob.entry.insert(0, "just trying to talk here...")
    bob.on_enter_pressed()
    for view in bob.views_by_id.values():
        wait_until(lambda: "#autojoin You're not on that channel" in view.get_text())

    switch_view(alice, alice.get_server_views()[0])
    alice.entry.insert(0, "/kick bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /kick only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")


def test_me(alice, bob, wait_until):
    alice.entry.insert(0, "/me does something")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice does something" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support WHOIS"
)
def test_whois(alice, bob, wait_until):
    alice.entry.insert(0, "/whois bob")
    alice.on_enter_pressed()
    wait_until(lambda: "End of /WHOIS list." in alice.text())

    assert isinstance(alice.get_current_view(), PMView)
    assert alice.get_current_view().nick_of_other_user == "Bob"
    assert re.sub(r".*\t", "", alice.text()).splitlines() == [
        # mantatail sends only 311 and 318, although we support many more whois responses
        "311 Bob BobUsr 127.0.0.1 * Bob's real name",
        "318 Bob End of /WHOIS list.",
    ]


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support modes"
)
def test_op_deop(alice, bob, wait_until, switch_view):
    alice.entry.insert(0, "/op bob")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice gives channel operator permissions to Bob" in alice.text()
    )
    wait_until(lambda: "Alice gives channel operator permissions to Bob" in bob.text())

    alice.entry.insert(0, "/deop bob")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice removes channel operator permissions from Bob" in alice.text()
    )
    wait_until(
        lambda: "Alice removes channel operator permissions from Bob" in bob.text()
    )

    alice.entry.insert(0, "/op nonexistent")
    alice.on_enter_pressed()
    wait_until(lambda: "401 Alice nonexistent No such nick/channel" in alice.text())

    # TODO: modes other than +o and -o are displayed differently.
    # Should test them when available in mantatail

    switch_view(alice, alice.get_server_views()[0])

    alice.entry.insert(0, "/op bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /op only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")

    alice.entry.insert(0, "/deop bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /deop only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")


def test_invalid_command(alice, wait_until):
    alice.entry.insert(0, "/asdf")
    alice.on_enter_pressed()
    wait_until(lambda: "No command named '/asdf'\n" in alice.text())

    alice.entry.delete(0, "end")
    alice.entry.insert(0, "/AsDf")
    alice.on_enter_pressed()
    wait_until(lambda: "No command named '/AsDf'\n" in alice.text())


def test_case_insensitive(alice, bob, wait_until):
    alice.entry.insert(0, "/ME says foo")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice says foo" in bob.text())

    alice.entry.insert(0, "/mE says bar")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice says bar" in bob.text())


def test_command_cant_contain_multiple_slashes(alice, bob, wait_until):
    alice.entry.insert(0, "/home/alice")
    alice.on_enter_pressed()  # sends /home/alice as a message
    wait_until(lambda: "/home/alice" in bob.text())


def test_nickserv(alice, bob, wait_until):
    # Bob shall pretend he is nickserv, because hircd and mantatail don't have nickserv
    bob.get_server_views()[0].core.send("NICK NickServ")
    wait_until(lambda: "You are now known as NickServ.\n" in bob.text())

    # Password is shown with *** in Alice's client, nickserv commands are case insensitive
    alice.entry.insert(0, "/ns IdEnTiFy Alice hunter2")
    alice.on_enter_pressed()
    wait_until(lambda: "IdEnTiFy ********\n" in alice.text())
    assert "hunter2" not in alice.text()

    text = (alice.log_manager.log_dir / "localhost" / "nickserv.log").read_text("utf-8")
    assert "IdEnTiFy ********" in text
    assert "hunter2" not in text

    # It does not actually send the stars though, lol. So Bob sees the password
    wait_until(lambda: "IdEnTiFy Alice hunter2\n" in bob.text())
    assert bob.get_current_view().nick_of_other_user == "Alice"


def test_memoserv(alice, bob, wait_until):
    bob.get_server_views()[0].core.send("NICK MemoServ")
    wait_until(lambda: "You are now known as MemoServ.\n" in bob.text())

    alice.entry.insert(0, "/ms send Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "send Bob hello there\n" in bob.text())


def userlist(irc_widget):
    view = irc_widget.get_current_view()
    if not isinstance(view, ChannelView):
        return []
    return [
        view.userlist.treeview.item(nick, "text") for nick in view.userlist.get_nicks()
    ]


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd doesn't support away notifications",
)
def test_away_status(alice, bob, wait_until):
    assert alice.nickbutton["text"] == "Alice"
    assert str(alice.nickbutton["style"]) == ""

    alice.entry.insert(0, "/away foo bar baz")
    alice.on_enter_pressed()
    wait_until(lambda: "away" in str(userlist(alice)) and "away" in str(userlist(bob)))

    # Server view (Alice only)
    wait_until(
        lambda: (
            "You have been marked as being away\n"
            in alice.get_server_views()[0].get_text()
        )
    )

    # Channel view (Alice and Bob)
    assert "You have been marked as being away\n" in alice.text()
    assert userlist(alice) == ["Alice (away: foo bar baz)", "Bob"]
    assert userlist(bob) == ["Alice (away: foo bar baz)", "Bob"]
    assert alice.nickbutton["text"] == "Alice (away)"
    assert str(alice.nickbutton["style"]) == "Away.TButton"

    # When joining a channel that already has people marked as away, we know who is
    # away but we don't know their away reasons yet.
    bob.entry.insert(0, "/part #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: bob.get_server_views()[0].find_channel("#autojoin") is None)
    bob.entry.insert(0, "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "away" in str(userlist(bob)))
    assert userlist(alice) == ["Alice (away: foo bar baz)", "Bob"]
    assert userlist(bob) == ["Alice (away)", "Bob"]  # unknown away reason

    # The server tells Bob why Alice is away when Bob messages Alice.
    bob.entry.insert(0, "/msg Alice hi")
    bob.on_enter_pressed()
    wait_until(lambda: "Alice is marked as being away: foo bar baz" in bob.text())

    # Now that the server told the away reason to Bob, it's also shown in his user list
    alice.remove_view(alice.get_current_view())
    bob.remove_view(bob.get_current_view())
    alice.update()  # Handle selected view changed events
    assert userlist(bob) == ["Alice (away: foo bar baz)", "Bob"]

    # Nick changes preserve away status
    alice.entry.insert(0, "/nick Alice2")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as Alice2" in alice.text())
    wait_until(lambda: "Alice is now known as Alice2" in bob.text())
    assert userlist(alice) == ["Alice2 (away: foo bar baz)", "Bob"]
    assert userlist(bob) == ["Alice2 (away: foo bar baz)", "Bob"]
    assert alice.nickbutton["text"] == "Alice2 (away)"
    assert str(alice.nickbutton["style"]) == "Away.TButton"

    alice.entry.insert(0, "/back")
    alice.on_enter_pressed()
    wait_until(lambda: "Alice2" in userlist(alice) and "Alice2" in userlist(bob))
    assert userlist(alice) == ["Alice2", "Bob"]
    assert userlist(bob) == ["Alice2", "Bob"]
    assert "You are no longer marked as being away\n" in alice.text()
    assert alice.nickbutton["text"] == "Alice2"
    assert str(alice.nickbutton["style"]) == ""


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support away-notify"
)
@pytest.mark.parametrize("sharing_channels", [True, False])
def test_who_on_join(alice, bob, wait_until, sharing_channels):
    if not sharing_channels:
        alice.entry.insert(0, "/part #autojoin")
        alice.on_enter_pressed()
        wait_until(lambda: "topic" not in alice.text())

    bob.entry.insert(0, "/join #foo")
    bob.on_enter_pressed()
    bob.entry.insert(0, "/away foo bar baz")
    bob.on_enter_pressed()

    alice.entry.insert(0, "/join #foo")
    alice.on_enter_pressed()

    wait_until(lambda: "topic" in alice.text())

    wait_until(
        lambda: "away" in alice.get_current_view().userlist.treeview.item("Bob", "tags")
    )


@pytest.mark.parametrize(
    "command, error",
    [
        ("/join", "Usage: /join <channel>"),
        ("/nick", "Usage: /nick <new_nick>"),
        ("/msg", "Usage: /msg <nick> <message>"),
        ("/msg Bob", "Usage: /msg <nick> <message>"),
        ("/kick", "Usage: /kick <nick> [<reason>]"),
        ("/away", "Usage: /away <away_message>"),
        ("/back asdf", "Usage: /back"),
    ],
)
def test_incorrect_usage(alice, wait_until, command, error):
    alice.entry.insert(0, command)
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith(error + "\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 5 chars")


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd doesn't support KICK and unknown commands fail tests",
)
def test_error_response(alice, wait_until):
    alice.entry.insert(0, "/kick xyz")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("401 Alice xyz No such nick/channel\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")
