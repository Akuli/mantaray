import re

from mantaray import gui


def test_basic(alice, bob, wait_until):
    alice.entry.insert(0, "Hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello there\n" in bob.text())


def test_colors(alice, bob, wait_until):
    alice.entry.insert(
        "end",
        "\x0311,4cyan on red\x0f \x02bold\x0f \x1funderline\x0f \x0311,4\x02\x1feverything\x0f nothing",
    )
    alice.on_enter_pressed()
    wait_until(lambda: "cyan on red" in bob.text())

    def tags(search_string):
        index = bob.get_current_view().textwidget.search(search_string, "1.0")
        return set(bob.get_current_view().textwidget.tag_names(index))

    assert tags("cyan on red") == {"received-privmsg", "foreground-11", "background-4"}
    assert tags("bold") == {"received-privmsg"}  # bolding not supported
    assert tags("underline") == {"received-privmsg", "underline"}
    assert tags("everything") == {
        "received-privmsg",
        "foreground-11",
        "background-4",
        "underline",
    }
    assert tags("nothing") == {"received-privmsg"}
    assert "cyan on red bold underline everything nothing" in bob.text()


def test_nick_autocompletion(alice, bob):
    alice.entry.insert(0, "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "
    assert alice.entry.index("insert") == len("i think Bob ")


def test_nick_autocompletion_after_entering_message(alice, bob):
    alice.entry.insert(0, "bhello there")
    alice.entry.icursor(1)
    alice.autocomplete()
    assert alice.entry.get() == "Bob: hello there"
    assert alice.entry.index("insert") == len("Bob: ")


def test_escaped_slash(alice, bob, wait_until):
    alice.entry.insert(0, "//home/alice/codes")
    alice.on_enter_pressed()
    wait_until(lambda: " /home/alice/codes\n" in bob.text())


def test_enter_press_with_no_text(alice, bob, wait_until):
    alice.on_enter_pressed()
    assert "Alice" not in bob.text()


def test_private_messages(alice, bob, wait_until):
    # TODO: some button in gui to start private messaging?
    # TODO: "/msg bob asdf" with lowercase bob causes two bugs:
    #   - hircd doesn't send message
    #   - this client thinks that Bob and bob are two different nicks

    alice.entry.insert(0, "/msg Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "hello there" in alice.text())
    wait_until(lambda: "hello there" in bob.text())
    assert alice.get_current_view().other_nick == "Bob"
    assert bob.get_current_view().other_nick == "Alice"

    bob.entry.insert(0, "Hey Alice")
    bob.on_enter_pressed()
    wait_until(lambda: "Hey Alice" in alice.text())
    wait_until(lambda: "Hey Alice" in bob.text())


def test_notification_when_mentioned(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.entry.insert(0, "hey bob")  # bob vs Bob shouldn't matter
    alice.on_enter_pressed()
    alice.entry.insert(0, "this unrelated message shouldn't cause notifications")
    alice.on_enter_pressed()
    wait_until(lambda: "unrelated" in bob.text())

    assert re.fullmatch(
        r"\[\d\d:\d\d\]            Alice \| hey bob\n",
        bob.get_current_view().textwidget.get("pinged.first", "pinged.last"),
    )
    gui._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")

    hey_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 6 chars")
    bob_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 2 chars")
    assert set(hey_tags) == {"received-privmsg", "pinged"}
    assert set(bob_tags) == {"received-privmsg", "pinged", "self-nick"}


def test_extra_notifications(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.get_server_views()[0].core.join_channel("#bobnotify")
    bob.get_server_views()[0].core.join_channel("#bobnotify")
    wait_until(lambda: alice.get_current_view().channel_name == "#bobnotify")
    wait_until(lambda: bob.get_current_view().channel_name == "#bobnotify")

    alice.entry.insert(0, "this should cause notification")
    alice.on_enter_pressed()
    wait_until(lambda: "this should cause notification" in bob.text())
    gui._show_popup.assert_called_once_with(
        "#bobnotify", "<Alice> this should cause notification"
    )
    assert not bob.get_current_view().textwidget.tag_ranges("pinged")


def test_history(alice, bob, wait_until):
    # Alice presses first arrow up, then arrow down
    assert not alice.entry.get()
    alice.previous_message_to_entry()
    assert not alice.entry.get()
    alice.next_message_to_entry()
    assert not alice.entry.get()

    # Bob presses first arrow down, then arrow up
    assert not alice.entry.get()
    alice.next_message_to_entry()
    assert not alice.entry.get()
    alice.previous_message_to_entry()
    assert not alice.entry.get()

    alice.entry.insert(0, "first message")
    alice.on_enter_pressed()
    wait_until(lambda: "first message" in alice.text())

    assert not alice.entry.get()
    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.next_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.next_message_to_entry()

    alice.entry.delete(0, "end")
    alice.entry.insert(0, "second message")
    alice.on_enter_pressed()
    wait_until(lambda: "second message" in alice.text())

    assert not alice.entry.get()
    alice.previous_message_to_entry()
    assert alice.entry.get() == "second message"
    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.next_message_to_entry()
    assert alice.entry.get() == "second message"
    alice.next_message_to_entry()
    assert alice.entry.get() == "second message"

    alice.entry.delete(0, "end")
    alice.entry.insert(0, "//escaped message")
    alice.on_enter_pressed()
    wait_until(lambda: "escaped message" in alice.text())

    assert not alice.entry.get()
    alice.previous_message_to_entry()
    assert alice.entry.get() == "//escaped message"
    alice.previous_message_to_entry()
    assert alice.entry.get() == "second message"
    alice.next_message_to_entry()
    assert alice.entry.get() == "//escaped message"
