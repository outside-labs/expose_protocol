# expose_protocol

Expose locally configured programs through validated macOS URL schemes.
Python 3.14; no runtime dependencies.

## Install

```sh
python -m pip install expose-protocol
expose-protocol --help
```

The examples below use `uv` from a source checkout. After installing from PyPI,
invoke `expose-protocol` directly in place of `uv run expose-protocol`.

## Releases

Publish a GitHub release with a tag matching the package version, such as
`v0.1.0`. `.github/workflows/release.yaml` tests and verifies the distributions
before publishing to PyPI through the `pypi` environment using Trusted Publishing.
The workflow can also be run manually against an existing release tag.

## Usage

Choose a protocol name, a program and its typed arguments in a local profile,
build/install its URL app, then use the generated links in Markdown or your CLI.
The [examples](examples/README.md) include Vim, nano and a fixed Python script;
the [API reference](docs/API.md) covers definitions, validation and launch rules.

A local TOML definition selects a fixed executable, argv template, file root,
action and typed URL fields. For example:

```toml
schema = 1
scheme = "vim"
action = "open"
command = ["/usr/bin/vim", "--", "{file}"]
root = "."
mode = "terminal"

[parameters.file]
type = "path"
```

Save this as `vim.toml` beside an existing `README.md`, then:

```sh
uv sync --locked
uv run expose-protocol validate --config vim.toml
uv run expose-protocol link --config vim.toml file=README.md --markdown 'Edit README'
uv run expose-protocol inspect --config vim.toml 'vim://open?file=README.md'
```

Canonical URLs use `scheme://action?name=value` with UTF-8 percent encoding.
`link` handles encoding. Shell quotes are literal filename characters in URLs.
Values decode once; malformed input, duplicates, undeclared fields, controls,
traversal and paths outside the root are rejected before a launch is planned.
All declared fields are required. Paths select existing regular files;
`must_exist = false` allows a new file beneath an existing parent.

Strings reject leading option markers and have a bounded `max_length` (default
1024). `integer` parameters require `minimum` and `maximum`; `choice` parameters
require a `choices` array. Placeholders occupy an entire argv token. The
executable is a fixed absolute path, and relative roots resolve beside the
definition. Definitions are trusted local configuration, and validation does
not sandbox the chosen program or secure a hostile local filesystem.

Launch from the CLI:

```sh
uv run expose-protocol run --config vim.toml 'vim://open?file=README.md' --dry-run
uv run expose-protocol run --config vim.toml 'vim://open?file=README.md'
```

`run` shows the resolved argv and working directory and asks for confirmation.
`--yes` explicitly bypasses the CLI prompt. GUI URL launches always ask for
confirmation. Invalid requests and cancelled prompts launch nothing.
Direct mode runs an argv list without a shell and returns the child's exit
status. Terminal mode opens a fresh macOS Terminal window with shell-quoted
arguments; its exit status reports submission of the session. Terminal's
configured shell must accept POSIX shell quoting. macOS may request permission
to automate Terminal on the first Terminal launch.

Build a URL application on macOS:

```sh
uv run expose-protocol build --config vim.toml --output './Vim Links.app'
```

The output path must be new and its parent must exist. The app receives a local
ad-hoc signature and contains a validated profile snapshot and its helper code.
It pins the current Python interpreter, which must remain installed at that
path. Editing the original TOML file takes effect after a rebuild. Building
does not register the app; installation and registration are separate commands.
The bundle uses an isolated Python helper and passes each incoming URL as one
quoted argument. It requests Terminal automation permission only for Terminal
profiles. The generated app is for local use; signing for distribution and
notarization are separate release concerns.

Install, update or remove a handler:

```sh
uv run expose-protocol install --config vim.toml
uv run expose-protocol install --config vim.toml --replace
uv run expose-protocol uninstall vim
```

Installation builds into `~/Applications/Expose Protocol`, registers the app
and selects it for the custom scheme. `--apps-dir` chooses another owned
directory; use a persistent Applications location, since macOS can decline
routing from temporary storage. An existing different handler requires explicit `--takeover`;
installation records its bundle identifier for restoration on removal.
Updates require `--replace`, verify the existing app and receipt, and preserve
the previous app until registration succeeds. Failed updates restore the old
app and default when possible. If recovery itself fails, the error identifies
the retained backup directory. Uninstall verifies the managed identity and
signature, unregisters, restores the recorded default if applicable, and then
removes the app. It also works when the original profile root has been deleted.

Apple's handler-selection APIs are deprecated; the current compatibility
backend is isolated and tested on macOS. A duplicate copy of a generated app
can affect macOS routing; installation checks both the selected bundle ID and
application path. Registration failures report an error and attempt rollback.
The CLI refuses symlinked install paths and unrelated apps. An interrupted
operation can leave a scheme lock or a backup; inspect the reported path before
removing a stale lock. Personal URL handlers are not installed by the tests.

Development:

```sh
uv run python -m unittest discover -s tests -v
uv build
python scripts/check_distribution.py
```

CI verifies core behavior and distributions on Linux and macOS. Native macOS
checks compile/sign real apps and exercise the compiled AppleScript bridge
with a synthetic recorder. Synthetic installation/update/removal is enabled
only on disposable CI runners with `EXPOSE_PROTOCOL_NATIVE_LIFECYCLE=1`.
Distribution checks build a wheel/source archive and exercise a freshly
installed wheel from outside the checkout. Source development is independent
of package publication.

[Roadmap](https://github.com/outside-labs/expose_protocol/issues/1)
and [Project](https://github.com/orgs/outside-labs/projects/8) track development.
