import pytest


@pytest.mark.parametrize("part_command", ["/part", "/part #lol"])
def test_join_and_part(alice, bob, wait_until, part_command):
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: alice.find_channel("#lol"))

    bob.entry.insert("end", "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: bob.find_channel("#lol"))
    wait_until(
        lambda: (
            "Bob joined #lol.\n"
            in alice.find_channel("#lol").textwidget.get("1.0", "end")
        )
    )
    assert bob.get_current_config()["joined_channels"] == ["#autojoin", "#lol"]

    bob.entry.insert("end", part_command)
    bob.on_enter_pressed()
    wait_until(lambda: not bob.find_channel("#lol"))
    wait_until(
        lambda: (
            "Bob left #lol.\n"
            in alice.find_channel("#lol").textwidget.get("1.0", "end")
        )
    )
    assert bob.get_current_config()["joined_channels"] == ["#autojoin"]


def test_nick_change(alice, bob, wait_until):
    alice.entry.insert("end", "/nick lolwat")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "You are now known as lolwat.\n"
            in alice.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )
    wait_until(
        lambda: (
            "Alice is now known as lolwat.\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )

    alice.entry.insert("end", "/nick LolWat")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "You are now known as LolWat.\n"
            in alice.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )
    wait_until(
        lambda: (
            "lolwat is now known as LolWat.\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )


def test_quit(alice, bob, wait_until):
    # TODO: /quit command
    #    alice.entry.insert("end", "/quit")
    #    alice.on_enter_pressed()
    alice.core.quit()

    wait_until(
        lambda: (
            "Alice quit.\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )
    assert alice.get_current_config()["joined_channels"] == ["#autojoin"]


def test_invalid_command(alice, wait_until):
    alice.entry.insert("end", "/asdf")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "There's no '/asdf' command :("
            in alice.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )


def test_nickserv_and_memoserv(alice, bob, wait_until):
    # Bob shall pretend he is nickserv, because hircd doesn't natively support nickserv
    bob.core.change_nick("NickServ")
    wait_until(
        lambda: (
            "You are now known as NickServ.\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ns identify Alice hunter2")
    alice.on_enter_pressed()
    wait_until(lambda: bob.find_pm("Alice"))
    wait_until(
        lambda: (
            "identify Alice hunter2\n"
            in bob.find_pm("Alice").textwidget.get("1.0", "end")
        )
    )

    bob.core.change_nick("MemoServ")
    wait_until(
        lambda: (
            "You are now known as MemoServ.\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ms send Bob hello there")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "send Bob hello there\n"
            in bob.find_pm("Alice").textwidget.get("1.0", "end")
        )
    )


def test_incorrect_usage(alice, wait_until):
    test_cases = """\
/join --> Usage: /join <channel>
/nick --> Usage: /nick <new nick>
/msg --> Usage: /msg <nick> <message>
/msg Bob --> Usage: /msg <nick> <message>  # TODO: maybe should be supported?
"""
    for line in test_cases.splitlines():
        command, outcome = line.split("#")[0].strip().split(" --> ")
        alice.entry.insert("end", command)
        alice.on_enter_pressed()
        wait_until(
            lambda: (
                alice.find_channel("#autojoin")
                .textwidget.get("end - 1 char - 1 line", "end - 1 char")
                .endswith(outcome + "\n")
            )
        )
