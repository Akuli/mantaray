from __future__ import annotations

import argparse
import functools
import sys
import time
import tkinter
import traceback
from functools import partial
from pathlib import Path
from typing import Callable

try:
    import platformdirs
    import sv_ttk  # type: ignore

    from mantaray import config, gui
except ImportError:
    traceback.print_exc()
    print()
    print("You need to create a venv and install the dependencies into it with pip.")
    print("If you already created it, you probably forgot to active it.")
    print("See README.md for instructions.")
    sys.exit(1)


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    number = sum(v.notification_count for v in irc_widget.views_by_id.values())
    root.title("Mantaray" if number == 0 else f"({number}) Mantaray")


def is_parent_widget(parent: tkinter.Misc | str, child: tkinter.Misc) -> bool:
    w: tkinter.Misc | None = child
    while w is not None:
        if str(w) == str(parent):
            return True
        w = w.master
    return False


def main() -> None:
    default_config_dir = platformdirs.user_config_path("mantaray", "Akuli")

    parser = argparse.ArgumentParser()

    config_dir_group = parser.add_mutually_exclusive_group()
    config_dir_group.add_argument(
        "--config-dir",
        type=Path,
        default=default_config_dir,
        help=(
            "path to folder containing config.json and logs folder"
            + f" (default: {default_config_dir})"
        ),
    )
    config_dir_group.add_argument(
        "--alice",
        action="store_true",
        help="equivalent to '--config-dir alice --dont-save-config', useful for developing mantaray",
    )
    config_dir_group.add_argument(
        "--bob",
        action="store_true",
        help="equivalent to '--config-dir bob --dont-save-config', useful for developing mantaray",
    )

    parser.add_argument(
        "--dont-save-config",
        action="store_true",
        help="do not write to config.json in the config dir",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print everything sent and received, useful for development and understanding IRC",
    )

    args = parser.parse_args()

    if args.alice:
        args.config_dir = Path("alice")
    if args.bob:
        args.config_dir = Path("bob")

    if args.config_dir != default_config_dir and not args.config_dir.is_dir():
        parser.error("the specified --config-dir must exist and be a directory")

    # tkinter must have one global root window, but server configging creates dialog
    # solution: hide root window temporarily
    root = tkinter.Tk()
    sv_ttk.use_dark_theme()
    root.withdraw()

    settings = config.Settings(
        config_dir=args.config_dir,
        read_only=(args.alice or args.bob or args.dont_save_config),
    )
    if settings.read_only:
        print("Settings (read-only):", args.config_dir / "config.json")
    else:
        print("Settings:", args.config_dir / "config.json")
    print("Logs:", args.config_dir / "logs")

    try:
        settings.load()
    except FileNotFoundError:
        server_settings = config.ServerSettings()
        user_clicked_connect = config.show_connection_settings_dialog(
            settings=server_settings, transient_to=None, connecting_to_new_server=True
        )
        if not user_clicked_connect:
            return
        settings.add_server(server_settings)
        settings.save()

    if settings.theme != "dark":
        sv_ttk.set_theme(settings.theme)  # can be the default theme

    last_root_focus = 0.0

    def on_any_widget_focused(event: tkinter.Event[tkinter.Misc]) -> None:
        nonlocal last_root_focus

        if event.widget == root:
            last_root_focus = time.monotonic()
            irc_widget.get_current_view().mark_seen()

        if time.monotonic() - last_root_focus < 0.05 and not is_parent_widget(
            event.widget, irc_widget.entry
        ):
            # User just clicked into the mantaray window, and the focus is going to
            # somewhere else than the text entry. Let's focus the entry instead. If
            # you actually want to focus something else, you can click it twice.
            #
            # I tried other ways to do this before resorting to time. They worked most
            # of the time but not reliably. You should probably not touch this code.
            irc_widget.entry.focus()

    def quit_all_servers() -> None:
        for server_view in irc_widget.get_server_views():
            server_view.core.quit()

    irc_widget = gui.IrcWidget(
        root,
        settings,
        args.config_dir / "logs",
        verbose=args.verbose,
        after_quitting_all_servers=root.destroy,
    )
    irc_widget.pack(fill="both", expand=True)

    def add_binding(binding: str, callback: Callable[[], None]) -> None:
        def actual_callback(event: object) -> str:
            callback()
            return "break"

        if sys.platform == "darwin":
            binding = binding.format(ControlOrCommand="Command")
        else:
            binding = binding.format(ControlOrCommand="Control")

        # Must be bound on entry, otherwise Ctrl+PageUp runs PageUp code
        root.bind(binding, actual_callback)
        irc_widget.entry.bind(binding, actual_callback)

    # Don't bind to alt+n, some windows users use it for entering characters as "alt codes"
    add_binding("<{ControlOrCommand}-plus>", irc_widget.bigger_font_size)
    add_binding("<{ControlOrCommand}-minus>", irc_widget.smaller_font_size)
    add_binding("<{ControlOrCommand}-Shift-Prior>", irc_widget.move_view_up)
    add_binding("<{ControlOrCommand}-Shift-Next>", irc_widget.move_view_down)
    add_binding("<{ControlOrCommand}-Prior>", irc_widget.select_previous_view)
    add_binding("<{ControlOrCommand}-Next>", irc_widget.select_next_view)
    for n in range(10):
        add_binding(
            "<{ControlOrCommand}-Key-%d>" % n, partial(irc_widget.select_by_number, n)
        )

    root.bind("<FocusIn>", on_any_widget_focused)
    root.protocol("WM_DELETE_WINDOW", quit_all_servers)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotificationCountChanged>>", update_the_title)

    root.geometry("800x500")  # Good enough for me, let me know if you don't like this
    root.deiconify()  # unhide
    root.mainloop()


if __name__ == "__main__":
    main()
