def test_basic(alice, bob, wait_until):
    alice.entry.insert(0, "Hello there")
    alice.on_enter_pressed()
    wait_until(lambda: "Hello there\n" in bob.text())
