import tkinter


def get_history_selection(irc_widget):
    try:
        return irc_widget.get_current_view().textwidget.get(
            "history-selection.first", "history-selection.last"
        )
    except tkinter.TclError as e:
        print("**", e)
        # nothing tagged with history-selection
        return None


def test_basic(alice, wait_until):
    assert get_history_selection(alice) is None

    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    wait_until(lambda: "one" in alice.text())

    assert alice.entry.get() == ""
    assert get_history_selection(alice) is None

    alice.entry.insert(0, "two")
    alice.on_enter_pressed()
    wait_until(lambda: "two" in alice.text())

    assert alice.entry.get() == ""
    assert get_history_selection(alice) is None

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice).endswith("\ttwo\n")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice).endswith("\ttwo\n")

    alice.get_current_view().history.next()
    assert alice.entry.get() == ""
    assert get_history_selection(alice) is None

    alice.get_current_view().history.next()
    assert alice.entry.get() == ""


def test_preserving_last_message(alice, wait_until):
    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    wait_until(lambda: "one" in alice.text())
    alice.entry.insert(0, "two")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.entry.insert("end", "asdf asdf")
    assert alice.entry.get() == "oneasdf asdf"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "oneasdf asdf"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice) is None

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice) is None


def test_switching_views(alice, switch_view):
    alice.entry.insert(0, "hello #autojoin")

    switch_view(alice, alice.get_server_views()[0])
    assert not alice.entry.get()
    alice.entry.insert(0, "this is for the server view")

    switch_view(alice, "#autojoin")
    assert alice.entry.get() == "hello #autojoin"


def test_escaped_message(alice, wait_until):
    alice.entry.insert(0, "//hello")
    alice.on_enter_pressed()
    wait_until(lambda: "/hello" in alice.text())

    assert alice.entry.get() == ""
    alice.get_current_view().history.previous()
    assert alice.entry.get() == "//hello"
    assert get_history_selection(alice).endswith("\t/hello\n")


def test_command(alice, wait_until):
    alice.entry.insert(0, "one")
    alice.on_enter_pressed()
    alice.entry.insert(0, "/me does a thing")
    alice.on_enter_pressed()
    alice.entry.insert(0, "two")
    alice.on_enter_pressed()
    wait_until(lambda: "two" in alice.text())

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice).endswith("\ttwo\n")

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "/me does a thing"
    assert get_history_selection(alice) is None

    alice.get_current_view().history.previous()
    assert alice.entry.get() == "one"
    assert get_history_selection(alice).endswith("\tone\n")

    alice.get_current_view().history.next()
    assert alice.entry.get() == "/me does a thing"
    assert get_history_selection(alice) is None

    alice.get_current_view().history.next()
    assert alice.entry.get() == "two"
    assert get_history_selection(alice).endswith("\ttwo\n")

    alice.get_current_view().history.next()
    assert alice.entry.get() == ""
    assert get_history_selection(alice) is None
