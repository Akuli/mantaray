# Mantaray

This is an IRC client written in Python with tkinter and ttk.

![Screenshot](screenshot.png)

Supported features:

- SSL
- SASL authentication
- Notifications for new messages
- Multiple channels
- Multiple servers (if you add them manually in the config file, lol)
- Private messages
- `/away` & `/back`
- `/me`
- `/kick`

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

First, run mantaray as shown above.
By default, it connects to `##learnpython` on libera, which is where
most of the discussion about Mantaray development happens.
I am there almost every day at about 6PM to 10PM UTC.

To install tools needed for development, run:

    $ source env/bin/activate
    $ pip install -r requirements-dev.txt

Mantaray's tests use IRC servers that Mantaray connects to for testing.
They are included with Mantaray as Git submodules.
To run tests, you need to download the servers and then actually run the tests:

    $ git submodule init
    $ git submodule update
    $ python3 -m pytest

By default, the tests use [Mantatail](https://github.com/ThePhilgrim/MantaTail)
as their IRC server.
You can change it by setting the `IRC_SERVER` environment variable.
For example, the following command runs the tests with `hircd` as the IRC server:

    $ IRC_SERVER=hircd python3 -m pytest

If you add new tests and they fail because the IRC servers are too old,
you can update them by running `git pull` inside the submodule. For example:

    $ cd tests/MantaTail
    $ git pull origin main
    $ cd ..
    $ git add MantaTail
    $ git commit -m "update mantatail"

To experiment with new features locally, you can start [Mantatail](https://github.com/ThePhilgrim/MantaTail) manually:

    $ cd tests/MantaTail
    $ python3 server.py

Then in another terminal, run Mantaray.
It comes with the correct configuration for connecting to Mantatail.
In fact, there's two, in folders `alice` and `bob`,
because it's often handy to simultaneously run two instances of Mantaray
connected to each other.

    $ python3 -m mantaray --alice

This should connect Mantaray to Mantatail.
You can connect other IRC clients too,
or you can connect another instance of Mantaray with `--bob` instead of `--alice`.
This is essentially what Mantaray's tests do.

I recommend downloading two copies of Mantaray:
one that you develop, and another that you use to talk with people.
You probably want to have different settings on the two copies.
For example, the development copy could have a different nickname,
and join a channel where nobody will get annoyed if you constantly join and leave.
To do this, you can create a new folder for the development settings
and tell Mantaray to use it with `--config-dir`:

    $ mkdir dev-config
    $ python3 -m mantaray --config-dir dev-config

Run `python3 -m mantaray --help` to see
where it stores the configuration by default.
