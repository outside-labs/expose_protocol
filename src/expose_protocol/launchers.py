"""Validated execution and confirmation at CLI and macOS boundaries."""

import json
import shlex
import subprocess
import sys
from typing import Callable

from .definitions import Protocol
from .errors import LaunchError, RequestError
from .urls import Invocation, prepare

CONFIRM_SCRIPT = '''on run arguments
    try
        display dialog (item 1 of arguments) with title "Expose Protocol" buttons {"Cancel", "Run"} default button "Cancel" cancel button "Cancel" with icon caution
        return "approved"
    on error number -128
        return "cancelled"
    end try
end run'''

TERMINAL_SCRIPT = '''on run arguments
    tell application "Terminal"
        activate
        do script (item 1 of arguments)
    end tell
end run'''


def require_macos() -> None:
    if sys.platform != "darwin":
        raise LaunchError("this operation requires macOS")


def run_osascript(script: str, arguments: list[str]) -> str:
    require_macos()
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script, "--", *arguments],
                                text=True, capture_output=True, check=False)
    except OSError as exc:
        raise LaunchError(f"cannot run AppleScript: {exc}") from exc
    if result.returncode:
        raise LaunchError(result.stderr.strip() or "AppleScript failed")
    return result.stdout.strip()


def describe(invocation: Invocation) -> str:
    return "Run this configured program?\n\n" + json.dumps(invocation.to_mapping(), indent=2, ensure_ascii=True)


def confirm_gui(invocation: Invocation) -> bool:
    return run_osascript(CONFIRM_SCRIPT, [describe(invocation)]) == "approved"


def confirm_cli(invocation: Invocation) -> bool:
    if not sys.stdin.isatty():
        raise LaunchError("confirmation requires an interactive terminal; use --yes for an explicit local bypass")
    print(describe(invocation))
    try:
        return input("Run? [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def terminal_command(invocation: Invocation) -> str:
    """Quote both shell layers; Terminal runs this in a fresh session."""
    command = f"cd {shlex.quote(str(invocation.cwd))} && exec {shlex.join(invocation.argv)}"
    return "/bin/sh -c " + shlex.quote(command)


def launch(protocol: Protocol, url: str, *, yes: bool = False, gui: bool = False,
           confirm: Callable[[Invocation], bool] | None = None) -> int:
    """Validate, confirm and launch. Cancellation succeeds without side effects.

    A supplied confirmation callback is trusted application code. GUI launches
    always use the built-in confirmation and reject a bypass or callback.
    Direct mode waits and returns the child's status; Terminal returns once the
    new session is submitted, because its interactive child runs independently.
    """
    invocation = prepare(protocol, url)
    if gui and (yes or confirm is not None):
        raise RequestError("GUI URL launches always require the built-in confirmation")
    if gui or protocol.mode == "terminal":
        require_macos()
    approval = confirm_gui if gui else (confirm or confirm_cli)
    if not yes and not approval(invocation):
        return 0
    # Recheck paths after a potentially long confirmation dialog.
    rechecked = prepare(protocol, url)
    if rechecked != invocation:
        raise LaunchError("resolved request changed during confirmation; run it again")
    if protocol.mode == "terminal":
        run_osascript(TERMINAL_SCRIPT, [terminal_command(invocation)])
        return 0
    try:
        status = subprocess.run(list(invocation.argv), cwd=invocation.cwd, check=False).returncode
    except OSError as exc:
        raise LaunchError(f"cannot launch configured program: {exc}") from exc
    return status if status >= 0 else 128 - status
