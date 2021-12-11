# Mantaray

This is an IRC client written in Python with tkinter and ttk.

![Screenshot](screenshot.png)

Supported features:
- SSL
- SASL authentication
- Multiple servers
- Multiple channels
- Private messages
- `/me`
- Notifications for new messages

You can run Mantaray like this:

    $ sudo apt install libnotify-bin   # skip this on MacOS
    $ git clone https://github.com/Akuli/mantaray
    $ cd mantaray
    $ python3 -m venv env
    $ source env/bin/activate
    $ pip install -r requirements.txt
    $ python3 -m mantaray
    
On Windows, run these commands in Command Prompt:

    $ git clone https://github.com/Akuli/mantaray
    $ cd mantaray
    $ py -m venv env
    $ env\Scripts\activate
    $ pip install -r requirements.txt
    $ py -m mantaray


## Developing

    $ source env/bin/activate
    $ pip install -r requirements-dev.txt

Running tests: (use `py` instead of `python3` on Windows)

    $ python3 -m pytest

To experiment with new features locally, you can use [hircd](https://github.com/fboender/hircd).
It is a very simple and fast irc server that runs entirely on your computer.
The tests download it automatically when you run them for the first time.

    $ cd tests/hircd
    $ python3 hircd.py --foreground --verbose --log-stdout

Then in another terminal, run Mantaray.
It comes with the correct configuration for connecting to hircd.
In fact, there's two, in folders `alice` and `bob`,
because it's often handy to simultaneously run two instances of Mantaray
connected to each other.

    $ python3 -m mantaray --config-dir alice --dont-save-config

This should connect Mantaray to hircd.
You can connect other IRC clients too,
or you can connect another instance of Mantaray with `bob` instead of `alice`.
