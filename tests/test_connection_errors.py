def test_server_dies(alice, hircd):
    hircd.stop()
    alice.mainloop()
