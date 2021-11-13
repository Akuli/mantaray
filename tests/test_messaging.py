from irc_client import gui


def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "Hello there\n"
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
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
            in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )


def test_enter_press_with_no_text(alice, bob, wait_until):
    alice.on_enter_pressed()
    assert "Alice" not in bob.find_channel("#autojoin").textwidget.get("1.0", "end")


def test_private_messages(alice, bob, wait_until):
    # TODO: some button in gui to start private messaging?
    # TODO: "/msg bob asdf" with lowercase bob causes two bugs:
    #   - hircd doesn't send message
    #   - this client thinks that Bob and bob are two different nicks

    alice.entry.insert("end", "/msg Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: alice.find_pm("Bob"))
    wait_until(lambda: bob.find_pm("Alice"))

    assert alice.get_current_view() == alice.find_pm("Bob")
    assert bob.get_current_view() == bob.find_pm("Alice")
    assert "hello there" in alice.find_pm("Bob").textwidget.get("1.0", "end")
    assert "hello there" in bob.find_pm("Alice").textwidget.get("1.0", "end")

    bob.entry.insert("end", "Hey Alice")
    bob.on_enter_pressed()
    wait_until(lambda: "Hey Alice" in alice.find_pm("Bob").textwidget.get("1.0", "end"))
    wait_until(lambda: "Hey Alice" in bob.find_pm("Alice").textwidget.get("1.0", "end"))


def test_notification_when_mentioned(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.entry.insert("end", "hey bob")  # bob vs Bob shouldn't matter
    alice.on_enter_pressed()
    alice.entry.insert("end", "this unrelated message shouldn't cause notifications")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "unrelated" in bob.find_channel("#autojoin").textwidget.get("1.0", "end")
        )
    )
    gui._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")


def test_extra_notifications(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob, "_window_has_focus", (lambda: False))

    alice.core.join_channel("#bobnotify")
    bob.core.join_channel("#bobnotify")
    wait_until(lambda: alice.find_channel("#bobnotify"))
    wait_until(lambda: bob.find_channel("#bobnotify"))

    alice.entry.insert("end", "this should cause notification")
    alice.on_enter_pressed()
    wait_until(
        lambda: (
            "this should cause notification"
            in bob.find_channel("#bobnotify").textwidget.get("1.0", "end")
        )
    )
    gui._show_popup.assert_called_once_with(
        "#bobnotify", "<Alice> this should cause notification"
    )
