# Protocol definitions and Python API

The distribution is `expose-protocol`, imported as `expose_protocol`. Python
3.14 and the standard library are sufficient. The console command and
`python -m expose_protocol` provide the same CLI.

## Define and plan

```python
from pathlib import Path
from expose_protocol import Parameter, Protocol, make_url, parse_url, prepare

protocol = Protocol(
    scheme="vim",
    command=("/usr/bin/vim", "--", "{file}"),
    root=Path("notes"),
    parameters=(Parameter("file", kind="path"),),
    action="open",
    mode="terminal",
)
url = make_url(protocol, {"file": "README.md"})
values = parse_url(protocol, url)  # {"file": an absolute Path}
invocation = prepare(protocol, url)
print(invocation.argv, invocation.cwd)
```

`Protocol`, `Parameter` and `Invocation` are frozen dataclasses. Protocols
normalize command/parameter sequences to tuples, resolve the executable and
root, and reject invalid definitions immediately. Executables must be absolute,
existing executable files; roots must be existing directories. Configuration
is explicit: `load_protocol(path)` reads one trusted TOML file and resolves
relative roots beside that file. It never discovers profiles from URL data.

`schema = 1` is required in TOML. Top-level fields are `scheme`, `command`,
`root`, optional `action` (default `open`), optional `mode` (default `direct`),
and the `parameters` table. Unknown fields and versions fail. Schemes use 1–64
lowercase URL-scheme characters, starting with a letter; browser/file/system
names are reserved. Actions and field names are lowercase identifiers of at
most 32 characters. At most 32 fields and 64 command tokens are supported.

Every declared parameter is required and must appear in the command template.
Only whole tokens such as `{file}` substitute values; a placeholder can be
repeated. The executable cannot be a placeholder. Fixed arguments may contain
ordinary punctuation, but braces are reserved for placeholders.

| Parameter kind | Declaration | Value and restrictions |
| --- | --- | --- |
| `path` | `type = "path"`, optional `must_exist = false` | Returns an absolute `Path` beneath root. Existing targets must be regular files. New files require an existing parent. Rejects `..`, backslashes, and escaping symlinks. |
| `string` | `type = "string"` | Returns nonempty text; rejects controls, invisible formatting characters, line separators and leading `-`. |
| `integer` | `type = "integer"`, `minimum`, `maximum` | Returns an `int`; decimal digits with an optional minus, 1–19 digits, within explicit bounds no larger than ±10^18. |
| `choice` | `type = "choice"`, `choices = [...]` | Returns one of 1–64 distinct allowed strings; choices cannot be empty, control characters or option-like values. |

Each field has `max_length`, default 1024, constrained to 1–2048. Choices and
integer bounds are accepted only for their corresponding kinds. `must_exist`
is a Boolean and applies to paths. Paths are resolved, so internal symlinks
select the resolved target, while outside targets fail.

## URI contract

URLs have one grammar: `scheme://action?name=value&other=value`. Scheme matching
is case insensitive; action and names are case sensitive. Credential/port
authorities, URL paths and fragments are rejected. The parser requires printable
ASCII URI characters; Unicode and spaces use UTF-8 percent encoding. Literal
`+` is a plus, while canonical links encode spaces as `%20` and plus as `%2B`.
The illustrated `vim://file='README.md'` format is not this grammar; single and
double quotes are filename data rather than shell quoting.

Decode exactly once. Reject malformed escapes, invalid UTF-8, controls, residual
`%HH` sequences, duplicates even under encoded names, missing/empty/unknown
fields, and URLs over 8192 characters. The residual-escape restriction also
excludes literal filenames containing `%HH`. `make_url` validates input and
generates deterministic field order from the definition. `prepare` converts
typed fields to concrete argv tokens and an absolute working directory, with
no process or filesystem writes.

## Launch and macOS apps

```python
from expose_protocol import launch, build_app, install, uninstall

status = launch(protocol, url)  # interactive CLI confirmation
# launch(protocol, url, yes=True) is an explicit trusted caller's CLI bypass.
app = build_app(protocol, "Vim Links.app")
# Explicit local installation/update/removal:
# install(protocol)
# install(protocol, replace=True, takeover=True)
# uninstall("vim")
```

`launch(protocol, url, *, yes=False, gui=False, confirm=None)` validates before
confirmation and rechecks the resolved command afterward. A changed target
fails rather than executing a different displayed command. CLI confirmation
requires an interactive input stream unless the caller chooses `yes=True`.
An application may provide a trusted `confirm(invocation) -> bool` callback.
GUI mode rejects bypasses/callbacks and always uses the built-in dialog.
Cancellation returns 0 without launching.

Direct mode uses `subprocess.run(argv, cwd=...)` without a shell and returns the
program's exit status; signals become 128 plus their signal number. Terminal
mode requires macOS and submits a command to a new Terminal window. Each token
and the outer `/bin/sh -c` command are quoted. Its return value reports session
submission, independently of the interactive program's later result. Terminal
must use a shell supporting POSIX quoting.

`build_app` stages a new `.app`, compiles its shim, uses `plistlib` for metadata,
copies this library/profile snapshot, and signs/verifies the final resources.
Output must be new, with an existing parent. The helper uses an absolute pinned
Python interpreter with `-I`; it always loads its own manifest. The interpreter,
root, executable and fixed scripts must remain available. Rebuild/update after
changing a definition or moving/upgrading the interpreter. The app's local
ad-hoc signature provides integrity checks; distributed signing/notarization
are separate concerns.

`inspect_app(path)` verifies identity and returns the currently valid Protocol.
Lifecycle identity checks can recognize a signed app even after its external
runtime paths disappear, allowing removal of a broken installation.

`install(protocol, *, directory=None, replace=False, takeover=False)` builds,
registers and selects a per-user handler in `~/Applications/Expose Protocol`.
The alternate directory must be owned by the user and have no symlink path
components. Foreign handlers require `takeover=True`; updates require
`replace=True`. App/receipt identities and signatures are checked, and the
previous app is retained until verified routing selects the intended app.
Routing verification includes a bounded wait for Launch Services. On failure,
restore files/defaults; if recovery fails, retain the backup and report its path.

`uninstall(scheme, *, directory=None)` recognizes the managed app and receipt,
unregisters, restores a recorded earlier default if this app was still selected,
and removes its files. It preserves unrelated applications. Failed removal may
require attention to the reported path, especially after partial filesystem
deletion. `get_handler(scheme)` returns `DefaultHandler(bundle_id, path)` without
changing routing. Apple's deprecated Launch Services handler APIs are isolated
in the compatibility backend and can fail on a future macOS version.

## Errors and trust

`DefinitionError`, `RequestError` and `LaunchError` inherit `ProtocolError`.
CLI usage/definition/request failures exit 2; expected platform/launch failures
exit 1. Unexpected programming exceptions retain their traceback. Help, link,
validate, inspect and dry run never launch. Application failures are presented
by the shim; a cancelled confirmation is successful and quiet.

Local definitions, confirmation callbacks, installed helper code and selected
executables are trusted. URL fields are untrusted. Validation constrains input
and argv structure; the chosen program can still interpret input as its own
language or run plugins. A shell/interpreter/profile that accepts code strings
requires its author's explicit design and consent. The supplied Python example
pins a script and bounded inputs. Editor startup files and selected scripts
remain part of the program's behavior. Files may change after checks, so this
package does not sandbox programs or secure a hostile local filesystem.

Pretty Notebook's notebook identity and allowed note actions belong to a
consumer adapter. [APP-01](https://github.com/outside-labs/pretty-notebook/issues/57)
remains that separate adoption gate. Legacy handlers are unsupported by this
URI contract; link producers must generate the canonical validated format.
