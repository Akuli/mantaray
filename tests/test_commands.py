import pytest

from mantaray.views import ServerView


# TODO: should test entering channel name case insensitively, but hircd is case sensitive :(
@pytest.mark.parametrize("part_command", ["/part", "/part #lol"])
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


def test_topic_change(alice, wait_until):
    alice.entry.insert("end", "/topic blah blah")
    alice.on_enter_pressed()
    wait_until(
        lambda: "Alice changed the topic of #autojoin: blah blah\n" in alice.text()
    )
    # Bug in hircd: Bob doesn't get a notification about Alice changed topic


def test_me(alice, bob, wait_until):
    alice.entry.insert("end", "/me does something")
    alice.on_enter_pressed()
    wait_until(lambda: "\t*\tAlice does something" in bob.text())


def test_quit(alice, bob, wait_until):
    alice.entry.insert("end", "/quit")
    alice.on_enter_pressed()
    assert alice.get_current_config()["servers"][0]["joined_channels"] == ["#autojoin"]
    wait_until(lambda: "Alice quit.\n" in bob.text())
    wait_until(lambda: not alice.winfo_exists())


def test_invalid_command(alice, wait_until):
    alice.entry.insert("end", "/asdf")
    alice.on_enter_pressed()
    wait_until(lambda: "No command named '/asdf'\n" in alice.text())


def test_command_cant_contain_multiple_slashes(alice, bob, wait_until):
    alice.entry.insert("end", "/home/alice")
    alice.on_enter_pressed()  # sends /home/alice as a message
    wait_until(lambda: "/home/alice" in bob.text())


def test_nickserv_and_memoserv(alice, bob, wait_until):
    # Bob shall pretend he is nickserv, because irc_server doesn't natively support nickserv
    bob.get_server_views()[0].core.change_nick("NickServ")
    wait_until(lambda: "You are now known as NickServ.\n" in bob.text())

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ns identify Alice hunter2")
    alice.on_enter_pressed()
    wait_until(lambda: "identify Alice hunter2\n" in bob.text())
    assert bob.get_current_view().nick_of_other_user == "Alice"

    bob.get_server_views()[0].core.change_nick("MemoServ")
    wait_until(lambda: "You are now known as MemoServ.\n" in bob.text())

    alice.entry.insert("end", "/ms send Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "send Bob hello there\n" in bob.text())


@pytest.mark.parametrize(
    "command, error",
    [
        ("/join", "Usage: /join <channel>"),
        ("/nick", "Usage: /nick <new_nick>"),
        ("/msg", "Usage: /msg <nick> <message>"),
        ("/msg Bob", "Usage: /msg <nick> <message>"),
        ("/quit asdf", "Usage: /quit"),  # no arguments expected is special-cased
        # TODO: tests for kick, once find irc server that support kick
        ("/kick", "Usage: /kick <nick> [<reason>]"),
    ],
)
def test_incorrect_usage(alice, wait_until, command, error):
    alice.entry.insert("end", command)
    alice.on_enter_pressed()
    wait_until(lambda: alice.text().endswith(error + "\n"))
    assert alice.entry.get() == command  # give user chance to correct easily
