from irc_client import gui


def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello there\n" in bob.text())


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
    assert alice.get_current_view().nick == "Bob"
    assert bob.get_current_view().nick == "Alice"

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

    assert (
        bob.get_current_view().textwidget.get("pinged.first", "pinged.last")
        == "hey bob"
    )
    gui._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")

    # "hey bob" should highlight "bob" with extra tags e.g. {'bold', 'foreground-3', 'pinged'}
    hey_tags = bob.get_current_view().textwidget.tag_names("pinged.first + 1 char")
    bob_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 1 char")
    assert hey_tags == ("pinged",)
    assert "pinged" in bob_tags
    assert "bold" in bob_tags
    assert any(t.startswith("foreground") for t in bob_tags)


def test_extra_notifications(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.get_server_views()[0].core.join_channel("#bobnotify")
    bob.get_server_views()[0].core.join_channel("#bobnotify")
    wait_until(lambda: alice.get_current_view().name == "#bobnotify")
    wait_until(lambda: bob.get_current_view().name == "#bobnotify")

    alice.entry.insert("end", "this should cause notification")
    alice.on_enter_pressed()
    wait_until(lambda: "this should cause notification" in bob.text())
    gui._show_popup.assert_called_once_with(
        "#bobnotify", "<Alice> this should cause notification"
    )
    assert not bob.get_current_view().textwidget.tag_ranges("pinged")
