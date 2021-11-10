import functools
import tkinter
from getpass import getuser

from . import gui, config


def update_title(
    root: tkinter.Tk, irc_widget: gui.IrcWidget, junk_event: object = None
) -> None:
    title = "IRC: %s" % irc_widget.core.host
    number = irc_widget.not_seen_count()
    if number != 0:
        title = "(%d) %s" % (number, title)
    root.title(title)


# TODO: current_channel_like_notify and mark_seen()
def main() -> None:
    # tkinter must have one global root window, but server configging creates dialog
    # solution: hide root window temporarily
    root = tkinter.Tk()
    root.withdraw()

    file_config = config.load_from_file()
    if file_config is None:
        server_config = config.show_server_config_dialog(
            transient_to=None,
            initial_config={
                "host": "irc.libera.chat",
                "port": 6697,
                "nick": getuser(),
                "username": getuser(),
                "realname": getuser(),
                "joined_channels": ["##learnpython"],
            },
        )
        if server_config is None:
            return
    else:
        # TODO: support multiple servers
        [server_config] = file_config["servers"]

    irc_widget = gui.IrcWidget(root, server_config, root.destroy)
    irc_widget.pack(fill="both", expand=True)
    root.bind("<FocusIn>", (lambda junk_event: irc_widget.focus_the_entry()))
    root.protocol("WM_DELETE_WINDOW", irc_widget.part_all_channels_and_quit)

    update_the_title = functools.partial(update_title, root, irc_widget)
    update_the_title()
    irc_widget.bind("<<NotSeenCountChanged>>", update_the_title)

    irc_widget.handle_events()  # doesn't block
    root.deiconify()  # unhide
    root.mainloop()

    new_config = irc_widget.core.get_current_config(irc_widget.get_joined_channels())
    config.save_to_file({"servers": [new_config]})


if __name__ == "__main__":
    main()
