import os

import pytest


def test_basic(alice, bob, wait_until):
    alice.entry.insert(0, "Hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello there\n" in bob.text())


def test_textwidget_tags(alice, bob, wait_until):
    alice.entry.insert(
        "end",
        "\x0311,4cyan on red\x0f \x02bold\x0f \x1funderline\x0f \x0311,4\x02\x1feverything\x0f nothing",
    )
    alice.on_enter_pressed()
    wait_until(lambda: "cyan on red" in bob.text())

    def tags(search_string):
        index = bob.get_current_view().textwidget.search(search_string, "1.0")
        return set(bob.get_current_view().textwidget.tag_names(index))

    assert tags("cyan on red") == {
        "text",
        "received-privmsg",
        "foreground-11",
        "background-4",
    }
    assert tags("bold") == {"text", "received-privmsg"}  # bolding not supported
    assert tags("underline") == {"text", "received-privmsg", "underline"}
    assert tags("everything") == {
        "text",
        "received-privmsg",
        "foreground-11",
        "background-4",
        "underline",
    }
    assert tags("nothing") == {"text", "received-privmsg"}
    assert "cyan on red bold underline everything nothing" in bob.text()


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") == "true",
    reason="sometimes fails on github actions, don't know why (#197)",
)
def test_nick_autocompletion(alice, bob):
    alice.entry.insert(0, "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "
    assert alice.entry.index("insert") == len("i think Bob ")


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") == "true",
    reason="sometimes fails on github actions, don't know why (#197)",
)
def test_nick_autocompletion_after_entering_message(alice, bob):
    alice.entry.insert(0, "bhello there")
    alice.entry.icursor(1)
    alice.autocomplete()
    assert alice.entry.get() == "Bob: hello there"
    assert alice.entry.index("insert") == len("Bob: ")


def test_escaped_slash(alice, bob, wait_until):
    alice.entry.insert(0, "//home/alice/codes")
    alice.on_enter_pressed()
    wait_until(lambda: "\tAlice\t/home/alice/codes\n" in bob.text())


def test_enter_press_with_no_text(alice, bob, wait_until):
    alice.on_enter_pressed()
    assert "Alice" not in bob.text()


def test_multiline_sending(alice, bob, wait_until, mocker):
    mock = mocker.patch("tkinter.messagebox.askyesno")
    mock.return_value = True
    alice.entry.insert("end", "one\ntwo\nthree\nfour")
    alice.on_enter_pressed()
    assert not alice.entry.get()
    mock.assert_called_once()

    wait_until(lambda: "four" in bob.text())
    i = bob.text().index
    assert i("one") < i("two") < i("three") < i("four")


def test_multiline_not_sending(alice, bob, wait_until, mocker):
    mock = mocker.patch("tkinter.messagebox.askyesno")
    mock.return_value = False
    alice.entry.insert("end", "one\ntwo\nthree\nfour")
    alice.on_enter_pressed()
    mock.assert_called_once()
    assert alice.entry.get() == "one\ntwo\nthree\nfour"


def test_slash_r_character(alice, bob, wait_until):
    alice.entry.insert("end", "hello \rlol\r world")
    alice.on_enter_pressed()
    wait_until(lambda: "hello \rlol\r world" in bob.text())


@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd",
    reason="hircd doesn't support case insensitive nicks",
)
def test_private_messages(alice, bob, wait_until):
    # TODO: some button in gui to start private messaging?

    alice.entry.insert(0, "/msg Bob hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "hello there" in alice.text())
    wait_until(lambda: "hello there" in bob.text())
    assert alice.get_current_view().nick_of_other_user == "Bob"
    assert bob.get_current_view().nick_of_other_user == "Alice"

    alice.entry.insert(0, "/msg BOB nicks are case insensitive")
    alice.on_enter_pressed()
    wait_until(lambda: "nicks are case insensitive" in alice.text())
    wait_until(lambda: "nicks are case insensitive" in bob.text())
    assert alice.get_current_view().nick_of_other_user == "Bob"
    assert bob.get_current_view().nick_of_other_user == "Alice"

    bob.entry.insert(0, "Hey Alice")
    bob.on_enter_pressed()
    wait_until(lambda: "Hey Alice" in alice.text())
    wait_until(lambda: "Hey Alice" in bob.text())


@pytest.mark.xfail(
    os.environ["IRC_SERVER"] == "mantatail",
    reason="https://github.com/ThePhilgrim/MantaTail/issues/125",
    strict=True,
)
def test_private_messages_nick_changing_bug(alice, bob, wait_until):
    bob.entry.insert(0, "/msg Alice hello")
    bob.on_enter_pressed()
    wait_until(lambda: "hello" in alice.text())

    bob.entry.insert(0, "/part #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: alice.get_current_view().view_name != "#autojoin")

    bob.entry.insert(0, "/nick Bob2")
    bob.on_enter_pressed()
    wait_until(lambda: bob.nickbutton["text"] == "Bob2")

    bob.entry.insert(0, "/msg Alice hello2")
    bob.on_enter_pressed()
    wait_until(lambda: "hello2" in alice.text())

    assert [v.view_name for v in alice.get_server_views()[0].get_subviews()] == [
        "#autojoin",
        "Bob",
        "Bob2",
    ]

    bob.entry.insert(0, "/join #autojoin")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #autojoin is" in bob.text())
    bob.entry.insert(0, "/nick Bob")
    bob.on_enter_pressed()

    wait_until(lambda: alice.get_server_views()[0].find_pm("Bob2") is None)
    assert [v.view_name for v in alice.get_server_views()[0].get_subviews()] == [
        "#autojoin",
        "Bob",
    ]

    bob.entry.insert(0, "/nick bob")
    bob.on_enter_pressed()
    wait_until(lambda: "Bob is now known as bob" in alice.text())
    assert [v.view_name for v in alice.get_server_views()[0].get_subviews()] == [
        "#autojoin",
        "bob",
    ]


def test_urls(alice, bob, wait_until):
    alice.entry.insert(0, "please use https://www.google.com...")
    alice.on_enter_pressed()
    alice.entry.insert(0, "log in to https://github.com/, or do not contribute lol?")
    alice.on_enter_pressed()
    alice.entry.insert(
        0, "why do you ask me (ever heard of https://stackoverflow.com/)?"
    )
    alice.on_enter_pressed()
    alice.entry.insert(
        0, "why do you ask me (ever heard of https://stackoverflow.com/?)"
    )
    alice.on_enter_pressed()
    alice.entry.insert(
        0, "this is lol https://en.wikipedia.org/wiki/Whitespace_(programming_language)"
    )
    alice.on_enter_pressed()
    alice.entry.insert(0, "google.com is not a valid URL, it's just a hostname")
    alice.on_enter_pressed()
    alice.entry.insert(0, "last message")
    alice.on_enter_pressed()

    wait_until(lambda: "last message" in bob.text())
    textwidget = bob.get_current_view().textwidget

    # i hate how badly tkinter exposes tag_ranges()
    fucking_flat_tuple = textwidget.tag_ranges("url")
    urls = [
        textwidget.get(start, end)
        for start, end in zip(fucking_flat_tuple[0::2], fucking_flat_tuple[1::2])
    ]
    assert urls == [
        "https://www.google.com",
        "https://github.com/",
        "https://stackoverflow.com/",
        "https://stackoverflow.com/",
        "https://en.wikipedia.org/wiki/Whitespace_(programming_language)",
    ]


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
    alice.next_message_to_entry()
    assert not alice.entry.get()

    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.previous_message_to_entry()
    assert alice.entry.get() == "first message"
    alice.next_message_to_entry()
    assert not alice.entry.get()

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
    assert not alice.entry.get()

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
