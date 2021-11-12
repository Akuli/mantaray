def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "Hello there\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )


def test_nick_autocompletion(alice, bob):
    alice.entry.insert("end", "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "


def test_escaped_slash(alice, bob, wait_until):
    alice.entry.insert("end", "//home/alice/codes")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            " /home/alice/codes\n"
            in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")
        )
    )


def test_enter_press_with_no_text(alice, bob, wait_until):
    alice.on_enter_pressed()
    assert "Alice" not in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end")


def test_private_messages(alice, bob, wait_until):
    # TODO: some button in gui to start private messaging?
    # TODO: "/msg bob asdf" with lowercase bob causes two bugs:
    #   - hircd doesn't send message
    #   - this client thinks that Bob and bob are two different nicks

    alice.entry.insert("end", "/msg Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: bob._current_channel_like.name == "Alice")
    wait_until(lambda: alice._current_channel_like.name == "Bob")
    wait_until(
        lambda: "hello there" in alice.channel_likes["Bob"].textwidget.get("1.0", "end")
    )
    wait_until(
        lambda: "hello there" in bob.channel_likes["Alice"].textwidget.get("1.0", "end")
    )

    bob.entry.insert("end", "Hey Alice")
    bob.on_enter_pressed()
    wait_until(
        lambda: "Hey Alice" in alice.channel_likes["Bob"].textwidget.get("1.0", "end")
    )
    wait_until(
        lambda: "Hey Alice" in bob.channel_likes["Alice"].textwidget.get("1.0", "end")
    )
