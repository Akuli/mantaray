import os
import pytest

from mantaray.views import ServerView


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
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())

    bob.entry.insert("end", "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in bob.text())
    wait_until(lambda: "Bob joined #lol.\n" in alice.text())
    assert bob.get_current_config()["servers"][0]["joined_channels"] == [
        "#autojoin",
        "#lol",
    ]

    bob.move_view_up()
    assert bob.get_current_config()["servers"][0]["joined_channels"] == [
        "#lol",
        "#autojoin",
    ]

    bob.entry.insert("end", part_command)
    bob.on_enter_pressed()
    wait_until(lambda: not bob.get_server_views()[0].find_channel("#lol"))
    wait_until(lambda: "Bob left #lol.\n" in alice.text())
    assert bob.get_current_config()["servers"][0]["joined_channels"] == ["#autojoin"]


def test_part_last_channel(alice, bob, wait_until):
    alice.entry.insert("end", "/part #autojoin")
    alice.on_enter_pressed()
    wait_until(lambda: isinstance(alice.get_current_view(), ServerView))


def test_nick_change(alice, bob, wait_until):
    alice.entry.insert("end", "/nick lolwat")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as lolwat.\n" in alice.text())
    wait_until(lambda: "Alice is now known as lolwat.\n" in bob.text())

    alice.entry.insert("end", "/nick LolWat")
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
    alice.entry.insert("end", "/topic blah blah")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice changed the topic of #autojoin: blah blah\n" in alice.text()
    )
    wait_until(
        lambda: "Alice changed the topic of #autojoin: blah blah\n" in bob.text()
    )

    bob.entry.insert("end", "/part #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "blah blah" not in bob.text())

    bob.entry.insert("end", "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #autojoin is: blah blah\n" in bob.text())

    bob.entry.insert(0, "/topic blah blah")
    bob.on_enter_pressed()
    wait_until(lambda: "482 Bob #autojoin You're not channel operator" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support KICK"
)
def test_kick(alice, bob, wait_until):
    alice.entry.insert("end", "/kick bob")
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

    bob.entry.insert("end", "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: bob.text().count("The topic of #autojoin is") == 2)

    alice.entry.insert("end", "/kick bob insane trolling")
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

    alice.view_selector.selection_set(alice.get_server_views()[0].view_id)
    alice.update()

    alice.entry.insert("end", "/kick bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /kick only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")


def test_me(alice, bob, wait_until):
    alice.entry.insert("end", "/me does something")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice does something" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support modes"
)
def test_op_deop(alice, bob, wait_until):
    alice.entry.insert("end", "/op bob")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice gives channel operator permissions to Bob" in alice.text()
    )
    wait_until(lambda: "Alice gives channel operator permissions to Bob" in bob.text())

    alice.entry.insert("end", "/deop bob")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice removes channel operator permissions from Bob" in alice.text()
    )
    wait_until(
        lambda: "Alice removes channel operator permissions from Bob" in bob.text()
    )

    alice.entry.insert("end", "/op nonexistent")
    alice.on_enter_pressed()
    wait_until(lambda: "401 Alice nonexistent No such nick/channel" in alice.text())

    # TODO: modes other than +o and -o are displayed differently.
    # Should test them when available in mantatail

    alice.view_selector.selection_set(alice.get_server_views()[0].view_id)
    alice.update()

    alice.entry.insert("end", "/op bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /op only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")

    alice.entry.insert("end", "/deop bob")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("You can use /deop only on a channel.\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")


def switch_to_channel_view(user, channel_name):
    view = user.get_server_views()[0].find_channel(channel_name)
    user.view_selector.selection_set(view.view_id)
    user.update()
    assert f"The topic of {channel_name} is" in user.text()


def test_quit(alice, bob, wait_until):
    # Bob joins a second channel, but Alice only joins #autojoin.
    # Alice's quit message should only appear on #autojoin
    bob.entry.insert(0, "/join #bob")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #bob is" in bob.text())
    switch_to_channel_view(bob, "#autojoin")

    alice.entry.insert("end", "/quit")
    alice.on_enter_pressed()
    assert alice.get_current_config()["servers"][0]["joined_channels"] == ["#autojoin"]

    wait_until(lambda: "Alice quit." in bob.text())
    wait_until(lambda: not alice.winfo_exists())

    assert "Alice quit." in bob.text()
    switch_to_channel_view(bob, "#bob")
    assert "Alice quit." not in bob.text()


def test_invalid_command(alice, wait_until):
    alice.entry.insert("end", "/asdf")
    alice.on_enter_pressed()
    wait_until(lambda: "No command named '/asdf'\n" in alice.text())

    alice.entry.delete(0, "end")
    alice.entry.insert("end", "/AsDf")
    alice.on_enter_pressed()
    wait_until(lambda: "No command named '/AsDf'\n" in alice.text())


def test_case_insensitive(alice, bob, wait_until):
    alice.entry.insert("end", "/ME says foo")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice says foo" in bob.text())

    alice.entry.insert("end", "/mE says bar")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice says bar" in bob.text())


def test_command_cant_contain_multiple_slashes(alice, bob, wait_until):
    alice.entry.insert("end", "/home/alice")
    alice.on_enter_pressed()  # sends /home/alice as a message
    wait_until(lambda: "/home/alice" in bob.text())


def test_nickserv_and_memoserv(alice, bob, wait_until):
    # Bob shall pretend he is nickserv, because hircd and mantatail don't have nickserv
    bob.get_server_views()[0].core.send("NICK NickServ")
    wait_until(lambda: "You are now known as NickServ.\n" in bob.text())

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ns identify Alice hunter2")
    alice.on_enter_pressed()
    wait_until(lambda: "identify Alice hunter2\n" in bob.text())
    assert bob.get_current_view().nick_of_other_user == "Alice"

    bob.get_server_views()[0].core.send("NICK MemoServ")
    wait_until(lambda: "You are now known as MemoServ.\n" in bob.text())

    alice.entry.insert("end", "/ms send Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "send Bob hello there\n" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd doesn't support away notifications",
)
def test_away_status(alice, bob, wait_until):
    alice.entry.insert("end", "/away foo bar baz")
    alice.on_enter_pressed()

    # Server view
    wait_until(
        lambda: "You have been marked as being away\n"
        in alice.get_server_views()[0].get_text()
    )

    # Channel view
    wait_until(lambda: "You have been marked as being away\n" in alice.text())

    assert "away" in alice.get_current_view().userlist.treeview.item("Alice")["tags"]

    wait_until(
        lambda: "away" in bob.get_current_view().userlist.treeview.item("Alice")["tags"]
    )

    alice.entry.insert(0, "/nick Alice2")
    alice.on_enter_pressed()
    wait_until(lambda: "You are now known as Alice2" in alice.text())
    wait_until(lambda: "Alice is now known as Alice2" in bob.text())
    assert "away" in alice.get_current_view().userlist.treeview.item("Alice2")["tags"]
    assert "away" in bob.get_current_view().userlist.treeview.item("Alice2")["tags"]

    alice.entry.insert("end", "/back")
    alice.on_enter_pressed()
    wait_until(lambda: "You are no longer marked as being away\n" in alice.text())
    assert (
        "away" not in alice.get_current_view().userlist.treeview.item("Alice2")["tags"]
    )
    wait_until(
        lambda: "away"
        not in bob.get_current_view().userlist.treeview.item("Alice2")["tags"]
    )


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd doesn't support away-notify"
)
@pytest.mark.parametrize("sharing_channels", [True, False])
def test_who_on_join(alice, bob, wait_until, sharing_channels):
    if not sharing_channels:
        alice.entry.insert("end", "/part #autojoin")
        alice.on_enter_pressed()
        wait_until(lambda: "topic" not in alice.text())

    bob.entry.insert("end", "/join #foo")
    bob.on_enter_pressed()
    bob.entry.insert("end", "/away foo bar baz")
    bob.on_enter_pressed()

    alice.entry.insert("end", "/join #foo")
    alice.on_enter_pressed()

    wait_until(lambda: "topic" in alice.text())

    wait_until(
        lambda: "away" in alice.get_current_view().userlist.treeview.item("Bob")["tags"]
    )


@pytest.mark.parametrize(
    "command, error",
    [
        ("/join", "Usage: /join <channel>"),
        ("/nick", "Usage: /nick <new_nick>"),
        ("/msg", "Usage: /msg <nick> <message>"),
        ("/msg Bob", "Usage: /msg <nick> <message>"),
        ("/quit asdf", "Usage: /quit"),  # no arguments expected is special-cased
        ("/kick", "Usage: /kick <nick> [<reason>]"),
        ("/away", "Usage: /away <away_message>"),
    ],
)
def test_incorrect_usage(alice, wait_until, command, error):
    alice.entry.insert("end", command)
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith(error + "\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 5 chars")


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd doesn't support KICK and unknown commands fail tests",
)
def test_error_response(alice, wait_until):
    alice.entry.insert("end", "/kick xyz")
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith("401 Alice xyz No such nick/channel\n"))
    assert "error" in alice.get_current_view().textwidget.tag_names("end - 10 chars")
