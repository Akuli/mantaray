import re
import pytest

from mantaray import views


def test_notification_when_mentioned(alice, bob, wait_until, mocker, monkeypatch):
    monkeypatch.setattr(bob.get_current_view(), "_window_has_focus", (lambda: False))

    alice.entry.insert(0, "hey bob")  # bob vs Bob shouldn't matter
    alice.on_enter_pressed()
    alice.entry.insert(0, "this unrelated message shouldn't cause notifications")
    alice.on_enter_pressed()
    wait_until(lambda: "unrelated" in bob.text())

    assert re.fullmatch(
        r"\[\d\d:\d\d\]            Alice \| hey bob\n",
        bob.get_current_view().textwidget.get("pinged.first", "pinged.last"),
    )
    views._show_popup.assert_called_once_with("#autojoin", "<Alice> hey bob")

    hey_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 6 chars")
    bob_tags = bob.get_current_view().textwidget.tag_names("pinged.last - 2 chars")
    assert set(hey_tags) == {"received-privmsg", "pinged"}
    assert set(bob_tags) == {"received-privmsg", "pinged", "self-nick"}


@pytest.mark.parametrize("window_focused", [True, False])
def test_extra_notifications(alice, bob, wait_until, mocker, monkeypatch, window_focused):
    alice.get_server_views()[0].core.join_channel("#bobnotify")
    bob.get_server_views()[0].core.join_channel("#bobnotify")
    wait_until(lambda: alice.get_current_view().view_name == "#bobnotify")
    wait_until(lambda: bob.get_current_view().view_name == "#bobnotify")

    monkeypatch.setattr(bob.get_current_view(), "_window_has_focus", (lambda: window_focused))

    assert bob.view_selector.item(bob.get_current_view().view_id, "text") == "#bobnotify"
    alice.entry.insert(0, "this should cause notification")
    alice.on_enter_pressed()
    wait_until(lambda: "this should cause notification" in bob.text())

    if window_focused:
        assert bob.view_selector.item(bob.get_current_view().view_id, "text") == "#bobnotify"
        assert views._show_popup.call_count == 0
    else:
        assert bob.view_selector.item(bob.get_current_view().view_id, "text") == "#bobnotify (1)"
        views._show_popup.assert_called_once_with(
            "#bobnotify", "<Alice> this should cause notification"
        )
    assert not bob.get_current_view().textwidget.tag_ranges("pinged")
