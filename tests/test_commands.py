from mantaray.views import ServerView


def test_part_last_channel(alice, bob, wait_until):
    alice.entry.insert("end", "/part #autojoin")
    alice.on_enter_pressed()
    wait_until(lambda: isinstance(alice.get_current_view(), ServerView))
