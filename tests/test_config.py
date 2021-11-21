from tkinter import ttk
from tkinter.font import Font

from mantaray.config import load_from_file, show_connection_settings_dialog


def test_old_config_format(tmp_path, root_window):
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
        ],
        "font_family": Font(name="TkFixedFont", exists=True)["family"],  # added
        "font_size": Font(name="TkFixedFont", exists=True)["size"],  # added
    }


def reconnect_with_change(server_view, mocker, key, old, new):
    new_config = server_view.get_current_config().copy()
    assert new_config[key] == old
    new_config[key] = new
    mocker.patch(
        "mantaray.config.show_connection_settings_dialog"
    ).return_value = new_config
    server_view.show_config_dialog()


def test_changing_host(alice, mocker, wait_until):
    server_view = alice.get_server_views()[0]
    reconnect_with_change(server_view, mocker, "host", old="localhost", new="127.0.0.1")
    wait_until(lambda: alice.text().count("The topic of #autojoin is:") == 2)

    assert alice.view_selector.item(server_view.view_id, "text") == "127.0.0.1"
    assert (alice.log_dir / "localhost" / "#autojoin.log").exists()
    assert (alice.log_dir / "127.0.0.1" / "#autojoin.log").exists()


def click(window, button_text):
    widgets = [window]
    while True:
        w = widgets.pop()
        if isinstance(w, ttk.Button) and w["text"] == button_text:
            w.invoke()
            return
        widgets.extend(w.winfo_children())


def test_cancel(alice, mocker, monkeypatch, wait_until):
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Cancel"))
    server_view = alice.get_server_views()[0]
    server_view.show_config_dialog()

    # Ensure nothing happened
    alice.entry.insert("end", "lolwatwut")
    alice.on_enter_pressed()
    wait_until(lambda: "lolwatwut" in alice.text())
    assert "Disconnected" not in alice.text()


def test_reconnect(alice, mocker, monkeypatch, wait_until):
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Reconnect"))
    server_view = alice.get_server_views()[0]
    server_view.show_config_dialog()
    wait_until(lambda: "Disconnected" in alice.text())


def test_nothing_changes_if_you_only_click_reconnect(root_window, monkeypatch):
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Reconnect"))
    sample_config = {
        "host": "example.com",
        "port": 1234,
        "ssl": False,
        "nick": "AzureDiamond",
        "password": "hunter2",
        "username": "azure69",
        "realname": "xd lol",
        "joined_channels": ["#lol", "#wut"],
        "extra_notifications": ["#wut"],
    }
    assert (
        show_connection_settings_dialog(
            transient_to=root_window, initial_config=sample_config
        )
        == sample_config
    )


def test_default_settings(root_window, monkeypatch):
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Connect!"))
    config = show_connection_settings_dialog(
        transient_to=root_window, initial_config=None
    )
    assert config.pop("nick") == config.pop("username") == config.pop("realname")
    assert config == {
        "extra_notifications": [],
        "host": "irc.libera.chat",
        "joined_channels": ["##learnpython"],
        "password": None,
        "port": 6697,
        "ssl": True,
    }
