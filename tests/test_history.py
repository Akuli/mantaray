def test_basic(alice):
    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    assert alice.entry.get() == ""

    alice.entry.insert(0, "two")
    alice.on_enter_pressed()
    assert alice.entry.get() == ""

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "two"
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    alice.get_current_view().history.next()
    assert alice.entry.get() == ""
    alice.get_current_view().history.next()
    assert alice.entry.get() == ""


def test_preserving_last_message(alice):
    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    alice.entry.insert(0, "two")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"


def test_switching_views(alice):
    alice.entry.insert(0, "hello #autojoin")

    alice.view_selector.selection_set(alice.get_server_views()[0].view_id)
    alice.update()
    assert not alice.entry.get()
    alice.entry.insert(0, "this is for the server view")

    alice.view_selector.selection_set(alice.get_server_views()[0].find_channel("#autojoin").view_id)
    alice.update()
    assert alice.entry.get() == "hello #autojoin"


def test_escaped_message(alice):
    alice.entry.insert(0, "//hello")
    alice.on_enter_pressed()

    assert alice.entry.get() == ""
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "//hello"


def test_command(alice):
    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    alice.entry.insert(0, "/me does a thing")
    alice.on_enter_pressed()
    alice.entry.insert(0, "two")
    alice.on_enter_pressed()

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "two"
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "/me does a thing"
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    alice.get_current_view().history.next()
    assert alice.entry.get() == "/me does a thing"
    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    alice.get_current_view().history.next()
    assert alice.entry.get() == ""
