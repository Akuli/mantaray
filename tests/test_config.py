from irc_client.config import load_from_file


def test_old_config_format_ssl(tmp_path, monkeypatch):
    monkeypatch.setattr("irc_client.config._config_json_path", tmp_path / "config.json")
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
    assert load_from_file() == {
        "servers": [
            {
                "host": "irc.libera.chat",
                "port": 6697,
                "ssl": True,  # added
                "nick": "Akuli2",
                "username": "Akuli2",
                "realname": "Akuli2",
                "joined_channels": ["##learnpython"],
                "extra_notifications": [],  # added
            }
        ]
    }
