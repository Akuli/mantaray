def test_join_and_part(alice, bob, wait_until):
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "#lol" in alice.channel_likes)

    bob.entry.insert("end", "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "#lol" in bob.channel_likes)
    wait_until(
        lambda: (
            "Bob joined #lol.\n"
            in alice.channel_likes["#lol"].textwidget.get("1.0", "end")
        )
    )
    assert bob.core.get_current_config()["joined_channels"] == ["#autojoin", "#lol"]

    bob.entry.insert("end", "/part #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "#lol" not in bob.channel_likes)
    wait_until(
        lambda: (
            "Bob left #lol.\n"
            in alice.channel_likes["#lol"].textwidget.get("1.0", "end")
        )
    )
    assert bob.core.get_current_config()["joined_channels"] == ["#autojoin"]


def test_nick_change(alice, bob, wait_until):
    alice.entry.insert("end", "/nick lolwat")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "You are now known as lolwat.\n"
            in alice.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )
    wait_until(
        lambda: (
            "Alice is now known as lolwat.\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )

    alice.entry.insert("end", "/nick LolWat")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "You are now known as LolWat.\n"
            in alice.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )
    wait_until(
        lambda: (
            "lolwat is now known as LolWat.\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )


def test_quit(alice, bob, wait_until):
    # TODO: /quit command
    #    alice.entry.insert("end", "/quit")
    #    alice.on_enter_pressed()
    alice.core.quit()

    wait_until(
        lambda: "Alice quit.\n"
        in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
    )
    assert alice.core.get_current_config()["joined_channels"] == ["#autojoin"]


def test_invalid_command(alice, wait_until):
    alice.entry.insert("end", "/asdf")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "There's no '/asdf' command :("
            in alice.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )


def test_nickserv_and_memoserv(alice, bob, wait_until):
    # Bob shall pretend he is nickserv, because hircd doesn't natively support nickserv
    bob.core.change_nick("NickServ")
    wait_until(
        lambda: (
            "You are now known as NickServ.\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ns identify Alice hunter2")
    alice.on_enter_pressed()
    wait_until(lambda: "Alice" in bob.channel_likes)
    wait_until(
        lambda: (
            "identify Alice hunter2\n"
            in bob.channel_likes["Alice"].textwidget.get("1.0", "end")
        )
    )

    bob.core.change_nick("MemoServ")
    wait_until(
        lambda: (
            "You are now known as MemoServ.\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )

    # FIXME: show password with *** in Alice's client?
    alice.entry.insert("end", "/ms send Bob hello there")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "send Bob hello there\n"
            in bob.channel_likes["Alice"].textwidget.get("1.0", "end")
        )
    )


def test_incorrect_usage(alice, wait_until):
    test_cases = """\
/join --> Usage: /join <channel>
/part --> Usage: /part <channel>
/nick --> Usage: /nick <new_nick>
/msg --> Usage: /msg <nick> <message>
/msg Bob --> Usage: /msg <nick> <message>  # TODO: maybe should be supported?
"""
    for line in test_cases.splitlines():
        command, outcome = line.split("#")[0].strip().split(" --> ")
        alice.entry.insert("end", command)
        alice.on_enter_pressed()
        wait_until(
            lambda: (
                alice.channel_likes["#autojoin"]
                .textwidget.get("end - 1 char - 1 line", "end - 1 char")
                .endswith(outcome + "\n")
            )
        )
