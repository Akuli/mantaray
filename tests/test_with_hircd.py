def test_basic(alice, bob, wait_until):
    alice.entry.insert("end", "Hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello there" in bob.channel_likes["#autojoin"].textwidget.get("1.0", "end"))


def test_nick_autocompletion(alice, bob):
    alice.entry.insert("end", "i think b")
    alice.autocomplete()
    # space at the end is important, so alice can easily finish the sentence
    assert alice.entry.get() == "i think Bob "  
