# Example note

Use `vim.toml` or `nano.toml` in this directory to create a link to this file.

```sh
expose-protocol link --config examples/vim.toml file=README.md --markdown 'Edit example'
expose-protocol run --config examples/vim.toml 'vim://open?file=README.md' --dry-run
```

Copy a profile beside your own notes and set its root and executable for that
location. The root is relative to the profile, so `.` here selects this examples
directory. The nano example permits a new file with an existing parent.

For Python, replace the executable in `python.toml` with the absolute path
printed by `python -c 'import sys; print(sys.executable)'`. Keep `repeat.py`
beside the profile. Then:

```sh
expose-protocol link --config examples/python.toml count=2 message=hello
```

This produces `python://run?count=2&message=hello`. The script and interpreter
options are fixed in local configuration; the link selects a bounded count and
an allowed message. Installation enables clicks in Markdown viewers or command
line programs that support external URL schemes. macOS and the viewer may show
their own confirmation in addition to the program confirmation.
