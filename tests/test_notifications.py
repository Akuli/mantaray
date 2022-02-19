import os
import re
import pytest

from mantaray import views


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd sends QUIT twice"
)
def test_notification_when_mentioned(alice, bob, wait_until, monkeypatch):
    monkeypatch.setattr(bob.get_current_view(), "_window_has_focus", (lambda: False))

    alice.entry.insert(0, "hey bob")  # bob vs Bob shouldn't matter
    alice.on_enter_pressed()
    alice.entry.insert(0, "this unrelated message shouldn't cause notifications")
    alice.on_enter_pressed()
    wait_until(lambda: "unrelated" in bob.text())

    assert re.fullmatch(
        r"\[\d\d:\d\d\]\tAlice\they bob\n",
        bob.get_current_view().textwidget.get("pinged.first", "pinged.last"),
    )
    views._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")

    hey_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 6 chars")
    bob_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 2 chars")
    assert set(hey_tags) == {"text", "privmsg", "pinged"}
    assert set(bob_tags) == {"text", "privmsg", "pinged", "self-nick"}


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd sends QUIT twice"
)
@pytest.mark.parametrize("window_focused", [True, False])
def test_extra_notifications(alice, bob, wait_until, monkeypatch, window_focused):
    alice.get_server_views()[0].core.send("JOIN #bobnotify")
    bob.get_server_views()[0].core.send("JOIN #bobnotify")
    wait_until(lambda: alice.get_current_view().channel_name == "#bobnotify")
    wait_until(lambda: bob.get_current_view().channel_name == "#bobnotify")

    monkeypatch.setattr(
        bob.get_current_view(), "_window_has_focus", (lambda: window_focused)
    )

    assert (
        bob.view_selector.item(bob.get_current_view().view_id, "text") == "#bobnotify"
    )
    alice.entry.insert(0, "this should cause notification")
    alice.on_enter_pressed()
    wait_until(lambda: "this should cause notification" in bob.text())

    if window_focused:
        assert (
            bob.view_selector.item(bob.get_current_view().view_id, "text")
            == "#bobnotify"
        )
        assert views._show_popup.call_count == 0
    else:
        assert (
            bob.view_selector.item(bob.get_current_view().view_id, "text")
            == "#bobnotify (1)"
        )
        views._show_popup.assert_called_once_with(
            "#bobnotify", "<Alice> this should cause notification"
        )
    assert not bob.get_current_view().textwidget.tag_ranges("pinged")


def test_new_message_tags(alice, bob, wait_until, switch_view):
    alice_autojoin = alice.get_current_view()
    alice.get_server_views()[0].core.send("JOIN #lol")
    wait_until(lambda: "The topic of #lol is" in alice.text())
    assert not alice.view_selector.item(alice.get_current_view().view_id, "tags")

    bob.entry.insert(0, "blah blah")
    bob.on_enter_pressed()
    wait_until(lambda: "blah blah" in alice_autojoin.textwidget.get(1.0, "end"))
    assert alice.view_selector.item(alice_autojoin.view_id, "tags") == ("new_message",)

    bob.entry.insert(0, "hey alice")
    bob.on_enter_pressed()
    wait_until(lambda: "hey alice" in alice_autojoin.textwidget.get(1.0, "end"))
    assert alice.view_selector.item(alice_autojoin.view_id, "tags") == ("pinged",)

    bob.entry.insert(0, "blah blah 2")
    bob.on_enter_pressed()
    wait_until(lambda: "blah blah 2" in alice_autojoin.textwidget.get(1.0, "end"))
    assert alice.view_selector.item(alice_autojoin.view_id, "tags") == ("pinged",)

    switch_view(alice, "#autojoin")
    assert not alice.view_selector.item(alice_autojoin.view_id, "tags")
