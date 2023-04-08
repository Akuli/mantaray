import copy
import json
import os
import sys
from pathlib import Path
from tkinter import ttk
from tkinter.font import Font

import pytest

from mantaray.config import ServerSettings, Settings, show_connection_settings_dialog
from mantaray.gui import IrcWidget


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
    settings = Settings(tmp_path)
    settings.load()
    settings.save()
    with (tmp_path / "config.json").open("r") as file:
        assert json.load(file) == {
            "servers": [
                {
                    "host": "irc.libera.chat",
                    "port": 6697,
                    "ssl": True,
                    "nick": "Akuli2",
                    "username": "Akuli2",
                    "realname": "Akuli2",
                    "password": None,
                    "joined_channels": ["##learnpython"],
                    "extra_notifications": [],
                    "audio_notification": False,
                    "join_leave_hiding": {
                        "show_by_default": True,
                        "exception_nicks": [],
                    },
                }
            ],
            "font_family": Font(name="TkFixedFont", exists=True)["family"],
            "font_size": Font(name="TkFixedFont", exists=True)["size"],
        }


def reconnect_with_change(server_view, mocker, key, old, new):
    new_config = vars(server_view.settings)  # FIXME: hack
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
    assert (alice.log_manager.log_dir / "localhost" / "server.log").exists()
    assert (alice.log_manager.log_dir / "localhost" / "#autojoin.log").exists()
    assert (alice.log_manager.log_dir / "127_0_0_1" / "server.log").exists()
    assert (alice.log_manager.log_dir / "127_0_0_1" / "#autojoin.log").exists()


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
    alice.entry.insert(0, "lolwatwut")
    alice.on_enter_pressed()
    wait_until(lambda: "lolwatwut" in alice.text())


def test_reconnect(alice, mocker, monkeypatch, wait_until):
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Reconnect"))
    server_view = alice.get_server_views()[0]
    server_view.show_config_dialog()
    wait_until(lambda: "Connecting to localhost port 6667..." in alice.text())
    wait_until(lambda: alice.text().count("The topic of #autojoin is") == 2)


def test_nothing_changes_if_you_only_click_reconnect(root_window, mocker, monkeypatch):
    settings = Settings(Path("alice"))
    settings.load()
    old_json = copy.deepcopy(settings.get_json())
    settings.save = mocker.Mock()
    monkeypatch.setattr("tkinter.Toplevel.wait_window", lambda w: click(w, "Reconnect"))

    return_value = show_connection_settings_dialog(
        transient_to=root_window,
        connecting_to_new_server=False,
        settings=settings.servers[0],
    )

    assert return_value is True
    assert settings.get_json() == old_json
    settings.save.assert_called_once()


def test_multiple_servers(alice: IrcWidget, bob, mocker, monkeypatch, wait_until):
    def dialog_callback(dialog):
        [content] = dialog.winfo_children()
        content._server_entry.delete(0, "end")
        content._server_entry.insert(0, "localhost")
        content._ssl_var.set(False)
        content._nick_entry.delete(0, "end")
        content._nick_entry.insert(0, "Alice2")
        content._channel_entry.delete(0, "end")
        content._channel_entry.insert(0, "#autojoin")
        click(dialog, "Connect!")

    # right-click view selector in the middle of nowhere
    event = mocker.MagicMock()
    event.y = 1234
    mocker.patch("tkinter.Menu.tk_popup")
    alice.on_view_selector_right_click(event)
    monkeypatch.setattr("tkinter.Toplevel.wait_window", dialog_callback)
    alice.contextmenu.invoke("Connect to a new server...")

    wait_until(lambda: len(alice.get_server_views()) == 2)
    wait_until(
        lambda: alice.get_current_view()
        == alice.get_server_views()[1].find_channel("#autojoin")
    )
    assert len(alice.settings.servers) == 2
    wait_until(lambda: "*\tAlice2 joined #autojoin.\n" in bob.text())

    # Test changing the order of the servers
    old_view_id = alice.get_current_view().view_id
    alice.view_selector.selection_set(alice.get_server_views()[1].view_id)
    alice.settings.save = mocker.Mock()

    assert [s.nick for s in alice.settings.servers] == ["Alice", "Alice2"]
    assert alice.settings.save.call_count == 0

    alice.move_view_up()
    assert alice.settings.save.call_count == 1
    assert [s.nick for s in alice.settings.servers] == ["Alice2", "Alice"]

    alice.move_view_down()
    assert alice.settings.save.call_count == 2
    assert [s.nick for s in alice.settings.servers] == ["Alice", "Alice2"]

    alice.view_selector.selection_set(old_view_id)

    alice.entry.insert(0, "hi")
    alice.on_enter_pressed()
    wait_until(lambda: "\tAlice2\thi\n" in bob.text())

    # right-click the new server view
    x, y, width, height = alice.view_selector.bbox(alice.get_server_views()[1].view_id)
    event.y = y + int(height / 2)
    alice.on_view_selector_right_click(event)

    # Leave the server by clicking that option in the right-click menu
    askyesno = mocker.patch("tkinter.messagebox.askyesno")
    askyesno.return_value = True
    alice.contextmenu.invoke("Leave this server")
    askyesno.assert_called_once()
    assert askyesno.call_args.kwargs["detail"] == (
        "You can reconnect later, but if you decide to do so, Mantaray won't"
        " remember things like the host and port (localhost 6667), your nick"
        " (Alice2) or what channels you joined."
    )
    wait_until(lambda: len(alice.settings.servers) == 1)


def test_default_settings():
    config = ServerSettings().get_json()
    assert config.pop("nick") == config.pop("username") == config.pop("realname")
    assert config == {
        "host": "irc.libera.chat",
        "joined_channels": ["##learnpython"],
        "password": None,
        "port": 6697,
        "ssl": True,
        "extra_notifications": [],
        "audio_notification": False,
        "join_leave_hiding": {"show_by_default": True, "exception_nicks": []},
    }


@pytest.mark.skipif(
    sys.platform == "win32", reason="fails github actions and I don't know why"
)
@pytest.mark.skipif(
    os.environ["IRC_SERVER"] == "hircd", reason="hircd sends QUIT twice"
)
def test_join_part_quit_messages_disabled(alice, bob, wait_until, monkeypatch):
    bob.entry.insert(0, "/join #lol")
    bob.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in bob.text())

    # Configure Bob to ignore Alice joining/quitting
    def bob_config(settings, connecting_to_new_server, transient_to):
        settings.join_leave_hiding["exception_nicks"].append("aLiCe")
        return True

    monkeypatch.setattr("mantaray.config.show_connection_settings_dialog", bob_config)
    bob.get_server_views()[0].show_config_dialog()
    wait_until(lambda: bob.text().count("The topic of #lol is:") == 2)

    alice.entry.insert(0, "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())
    alice.entry.insert(0, "/part #lol")
    alice.on_enter_pressed()
    wait_until(lambda: not alice.get_server_views()[0].find_channel("#lol"))
    alice.entry.insert(0, "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())
    alice.entry.insert(0, "Hello Bob")
    alice.on_enter_pressed()
    alice.get_server_views()[0].core.quit()
    wait_until(lambda: not alice.winfo_exists())

    wait_until(
        lambda: "Hello Bob" in bob.text()
        and "Alice" not in bob.get_current_view().userlist.get_nicks()
    )
    assert "joined" not in bob.text()
    assert "left" not in bob.text()
    assert "parted" not in bob.text()
    assert "quit" not in bob.text()


def test_nick_change_saved(alice, mocker):
    # Settings update immediately, so that even if the nick is not available,
    # mantaray will try to use that nick later.
    #
    # TODO: This only makes sense if we try other nicks when a nick is in use,
    #       but that isn't implemented yet.

    alice.entry.insert(0, "/nick foo")
    alice.on_enter_pressed()
    assert alice.get_server_views()[0].settings.nick == "foo"

    mocker.patch("mantaray.gui.ask_new_nick", return_value="bar")
    alice.nickbutton.invoke()
    assert alice.get_server_views()[0].settings.nick == "bar"


def test_generate_nickmask(alice, mocker, wait_until):
    server_view = alice.get_server_views()[0]

    if (
        os.environ["IRC_SERVER"] == "hircd"
    ):  # Hircd does not support the WHOIS command. Nickmask will therefore always be None.
        assert server_view.core.get_nickmask() is None
    else:
        assert server_view.core.get_nickmask() == "Alice!AliceUsr@127.0.0.1"

    alice.entry.insert("end", "/nick Foo")
    alice.on_enter_pressed()

    wait_until(lambda: "You are now known as Foo" in alice.text())
    if os.environ["IRC_SERVER"] == "hircd":
        assert server_view.core.get_nickmask() is None
    else:
        assert server_view.core.get_nickmask() == "Foo!AliceUsr@127.0.0.1"

    reconnect_with_change(server_view, mocker, "username", old="AliceUsr", new="FooUsr")

    wait_until(lambda: alice.text().count("The topic of #autojoin is:") == 2)

    if os.environ["IRC_SERVER"] == "hircd":
        assert server_view.core.get_nickmask() is None
    else:
        assert server_view.core.get_nickmask() == "Foo!FooUsr@127.0.0.1"


def test_autojoin_setting(alice, wait_until, monkeypatch):
    assert "#lol" not in alice.get_server_views()[0].settings.joined_channels

    # We set the channel to autojoin when the server says that Alice has joined
    # it, not when Alice types /join. This way the autojoin list doesn't end up
    # containing channels that cannot be joined.
    alice.entry.insert(0, "/join #lol")
    alice.on_enter_pressed()
    assert "#lol" not in alice.get_server_views()[0].settings.joined_channels
    wait_until(lambda: "The topic of #lol is:" in alice.text())
    assert "#lol" in alice.get_server_views()[0].settings.joined_channels

    alice.entry.insert(0, "/part #lol")
    alice.on_enter_pressed()

    # autojoins are removed after a successful part.
    # This way the parting can come from the "/part" command or clicking something in GUI.
    # Also, you should never get into a situation where mantaray is not on #foo, but it
    # would automatically join #foo when restarted.
    assert "#lol" in alice.get_server_views()[0].settings.joined_channels
    wait_until(lambda: alice.get_server_views()[0].find_channel("#lol") is None)
    assert "#lol" not in alice.get_server_views()[0].settings.joined_channels


def test_autojoin_after_connection_error(alice, wait_until, monkeypatch):
    alice.entry.insert(0, "/join #lol")
    alice.on_enter_pressed()
    wait_until(lambda: "The topic of #lol is:" in alice.text())

    server_view = alice.get_server_views()[0]

    # Uncheck "Join when Mantaray starts" for #lol.
    server_view.settings.joined_channels.remove("#lol")

    # Force a reconnect
    monkeypatch.setattr("mantaray.backend.RECONNECT_SECONDS", 1)
    server_view.core._connection_state.close()

    # Both channels should be joined automatically when reconnecting
    wait_until(
        lambda: server_view.find_channel("#lol")
        .get_text()
        .count("The topic of #lol is:")
        == 2
    )
    wait_until(
        lambda: server_view.find_channel("#autojoin")
        .get_text()
        .count("The topic of #autojoin is:")
        == 2
    )

    # But next time mantaray starts we will not join #lol
    assert server_view.settings.joined_channels == ["#autojoin"]
