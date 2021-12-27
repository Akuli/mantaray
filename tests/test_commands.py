import pytest

from mantaray.views import ServerView


@pytest.mark.parametrize("part_command", ["/part", "/part #lol"])
def test_join_and_part(alice, bob, wait_until, part_command):
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())

    bob.entry.insert("end", "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in bob.text())
    wait_until(lambda: "Bob joined #lol.\n" in alice.text())
    assert bob.get_current_config()["servers"][0]["joined_channels"] == [
        "#autojoin",
        "#lol",
    ]

    bob.move_view_up()
    assert bob.get_current_config()["servers"][0]["joined_channels"] == [
        "#lol",
        "#autojoin",
    ]

    bob.entry.insert("end", part_command)
    bob.on_enter_pressed()
    wait_until(lambda: not bob.get_server_views()[0].find_channel("#lol"))
    wait_until(lambda: "Bob left #lol.\n" in alice.text())
    assert bob.get_current_config()["servers"][0]["joined_channels"] == ["#autojoin"]


def test_part_last_channel(alice, bob, wait_until):
    alice.entry.insert("end", "/part #autojoin")
    alice.on_enter_pressed()
    wait_until(lambda: isinstance(alice.get_current_view(), ServerView))
