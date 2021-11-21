import re

from mantaray import gui


def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
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

    assert tags("cyan on red") == {"foreground-11", "background-4"}
    assert tags("bold") == set()  # bolding not supported
    assert tags("underline") == {"underline"}
    assert tags("everything") == {"foreground-11", "background-4", "underline"}
    assert tags("nothing") == set()
    assert "cyan on red bold underline everything nothing" in bob.text()


def test_nick_autocompletion(alice, bob):
    alice.entry.insert("end", "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "


def test_escaped_slash(alice, bob, wait_until):
    alice.entry.insert("end", "//home/alice/codes")
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

    alice.entry.insert("end", "/msg Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "hello there" in alice.text())
    wait_until(lambda: "hello there" in bob.text())
    assert alice.get_current_view().other_nick == "Bob"
    assert bob.get_current_view().other_nick == "Alice"

    bob.entry.insert("end", "Hey Alice")
    bob.on_enter_pressed()
    wait_until(lambda: "Hey Alice" in alice.text())
    wait_until(lambda: "Hey Alice" in bob.text())


def test_notification_when_mentioned(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.entry.insert("end", "hey bob")  # bob vs Bob shouldn't matter
    alice.on_enter_pressed()
    alice.entry.insert("end", "this unrelated message shouldn't cause notifications")
    alice.on_enter_pressed()
    wait_until(lambda: "unrelated" in bob.text())

    assert re.fullmatch(
        r"\[\d\d:\d\d\]            Alice \| hey bob\n",
        bob.get_current_view().textwidget.get("pinged.first", "pinged.last"),
    )
    gui._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")

    hey_tags = bob.get_current_view().textwidget.tag_names("pinged.first + 1 char")
    bob_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 2 chars")
    assert set(hey_tags) == {"pinged"}
    assert set(bob_tags) == {"pinged", "self-nick"}


def test_extra_notifications(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.get_server_views()[0].core.join_channel("#bobnotify")
    bob.get_server_views()[0].core.join_channel("#bobnotify")
    wait_until(lambda: alice.get_current_view().channel_name == "#bobnotify")
    wait_until(lambda: bob.get_current_view().channel_name == "#bobnotify")

    alice.entry.insert("end", "this should cause notification")
    alice.on_enter_pressed()
    wait_until(lambda: "this should cause notification" in bob.text())
    gui._show_popup.assert_called_once_with(
        "#bobnotify", "<Alice> this should cause notification"
    )
    assert not bob.get_current_view().textwidget.tag_ranges("pinged")
