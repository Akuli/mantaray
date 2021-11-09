# irc-client

A fun IRC client written in Python with Tkinter and ttk.

![Screenshot](screenshot.png)

You can run this IRC client like this:

    $ sudo apt install libnotify-bin   # skip this on MacOS
    $ git clone https://github.com/Akuli/irc-client
    $ cd irc-client
    $ python3 -m irc_client

For developing, you may want to create a virtualenv and install some tools into it:

    $ python3 -m venv env
    $ source env/bin/activate
    $ pip install -r requirements-dev.txt
