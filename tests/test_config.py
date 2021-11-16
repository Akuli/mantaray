from irc_client.config import load_from_file


def test_old_config_format(tmp_path):
    (tmp_path / "config.json").write_text(
        """
        {
          "servers": [
            {
              "host": "irc.libera.chat",
              "port": 6697,
              "nick": "Akuli2",
              "username": "Akuli2",
              "realname": "Akuli2",
              "joined_channels": [
                "##learnpython"
              ]
            }
          ]
        }
        """
    )
    assert load_from_file(tmp_path) == {
        "servers": [
            {
                "host": "irc.libera.chat",
                "port": 6697,
                "ssl": True,  # added
                "nick": "Akuli2",
                "username": "Akuli2",
                "realname": "Akuli2",
                "password": None,  # added
                "joined_channels": ["##learnpython"],
                "extra_notifications": [],  # added
            }
        ]
    }


def reconnect_with_change(server_view, mocker, key, old, new):
    new_config = server_view.get_current_config().copy()
    assert new_config[key] == old
    new_config[key] = new
    mocker.patch("irc_client.config.show_server_config_dialog").return_value = new_config
    server_view.show_config_dialog()


def test_changing_host(alice, mocker, wait_until):
    server_view = alice.get_server_views()[0]
    reconnect_with_change(server_view, mocker, "host", old="localhost", new="127.0.0.1")
    wait_until(lambda: alice.text().count("The topic of #autojoin is:") == 2)

    assert alice.view_selector.item(server_view.view_id, "text") == "127.0.0.1"
    assert (alice.log_dir / "localhost" / "#autojoin.log").exists()
    assert (alice.log_dir / "127.0.0.1" / "#autojoin.log").exists()


def test_changing_autojoins(alice, mocker, wait_until):
    alice.entry.insert("end", "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())

    server_view = alice.get_server_views()[0]
    reconnect_with_change(server_view, mocker, "joined_channels", old=["#autojoin", "#lol"], new=["#autojoin"])
    wait_until(lambda: alice.text().count("The topic of #autojoin is:") == 2)
    assert server_view.find_channel("#lol") is None


def test_changing_nick_in_setting_dialog(alice, mocker, wait_until):
    server_view = alice.get_server_views()[0]
    reconnect_with_change(server_view, mocker, "nick", old="Alice", new="Anna")
    wait_until(lambda: alice.text().count("The topic of #autojoin is:") == 2)
    assert alice.nickbutton["text"] == "Anna"
