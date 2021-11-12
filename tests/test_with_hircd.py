def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "Hello there" in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )


def test_nick_autocompletion(alice, bob):
    alice.entry.insert("end", "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "


def test_join_and_part(alice, bob, wait_until):
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "#lol" in alice.channel_likes)

    bob.entry.insert("end", "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "#lol" in bob.channel_likes)
    wait_until(
        lambda: (
            "Bob joined #lol."
            in alice.channel_likes["#lol"].textwidget.get("1.0", "end")
        )
    )
    assert bob.core.get_current_config()["joined_channels"] == ["#autojoin", "#lol"]

    bob.entry.insert("end", "/part #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "#lol" not in bob.channel_likes)
    wait_until(
        lambda: (
            "Bob left #lol." in alice.channel_likes["#lol"].textwidget.get("1.0", "end")
        )
    )
    assert bob.core.get_current_config()["joined_channels"] == ["#autojoin"]
