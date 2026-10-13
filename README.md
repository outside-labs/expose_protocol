# expose_protocol

Expose locally configured programs through validated macOS URL schemes.
Python 3.14; no runtime dependencies.

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

Development:

```sh
uv run python -m unittest discover -s tests -v
uv build
```

[Roadmap](https://github.com/outside-labs/expose_protocol/issues/1)
and [Project](https://github.com/orgs/outside-labs/projects/8) track development.
