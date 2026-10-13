import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from expose_protocol import LaunchError, Parameter, RequestError, launch, make_url, prepare
from expose_protocol.launchers import (CONFIRM_SCRIPT, TERMINAL_SCRIPT, confirm_cli,
                                       run_osascript, terminal_command)
from support import ProtocolTestCase


class LaunchTests(ProtocolTestCase):
    def capture_protocol(self, *, mode="direct", root=None):
        root = root or self.root
        script = root / "capture.py"
        script.write_text("import json, os, pathlib, sys\n"
                          "pathlib.Path('captured.json').write_text(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n")
        return self.protocol(command=(sys.executable, "-I", str(script), "{value}"),
                             parameters=(Parameter("value"),), mode=mode, root=root)

    def test_direct_execution_preserves_hostile_text_as_one_argument(self):
        protocol = self.capture_protocol()
        value = "O'Brien;$(touch SENTINEL)`echo bad` & café + *"
        self.assertEqual(launch(protocol, make_url(protocol, {"value": value}), yes=True), 0)
        captured = json.loads((self.root / "captured.json").read_text())
        self.assertEqual(captured, {"argv": [value], "cwd": str(self.root)})
        self.assertFalse((self.root / "SENTINEL").exists())

    def test_returns_program_exit_status(self):
        protocol = self.protocol(command=(sys.executable, "-I", "-c", "raise SystemExit(7)"), parameters=())
        self.assertEqual(launch(protocol, make_url(protocol, {}), yes=True), 7)

    def test_negative_signal_status_is_converted(self):
        with patch("expose_protocol.launchers.subprocess.run", return_value=subprocess.CompletedProcess([], -15)):
            self.assertEqual(launch(self.protocol(), "test-editor://open?file=README.md", yes=True), 143)

    def test_invalid_request_never_confirms_or_launches(self):
        with patch("expose_protocol.launchers.confirm_cli") as confirm, patch("subprocess.run") as run:
            with self.assertRaises(RequestError):
                launch(self.protocol(), "test-editor://open?file=../bad", yes=True)
            confirm.assert_not_called()
            run.assert_not_called()

    def test_cancelled_request_never_launches(self):
        with patch("subprocess.run") as run:
            invocations = []
            self.assertEqual(launch(self.protocol(), "test-editor://open?file=README.md",
                                    confirm=lambda invocation: invocations.append(invocation) or False), 0)
            self.assertEqual(invocations[0].argv[-1], str(self.root / "README.md"))
            run.assert_not_called()

    def test_changed_symlink_during_confirmation_is_rejected(self):
        (self.root / "other.md").touch()
        link = self.root / "link.md"
        link.symlink_to(self.root / "README.md")
        def confirm(invocation):
            link.unlink()
            link.symlink_to(self.root / "other.md")
            return True
        with patch("subprocess.run") as run, self.assertRaises(LaunchError):
            launch(self.protocol(), "test-editor://open?file=link.md", confirm=confirm)
        run.assert_not_called()

    def test_file_removed_during_confirmation_is_rejected(self):
        def confirm(invocation):
            (self.root / "README.md").unlink()
            return True
        with patch("subprocess.run") as run, self.assertRaises(RequestError):
            launch(self.protocol(), "test-editor://open?file=README.md", confirm=confirm)
        run.assert_not_called()

    def test_gui_cannot_bypass_confirmation_or_supply_callback(self):
        for arguments in ({"yes": True}, {"confirm": lambda _: True}):
            with self.subTest(arguments=arguments), patch("subprocess.run") as run, self.assertRaises(RequestError):
                launch(self.protocol(), "test-editor://open?file=README.md", gui=True, **arguments)
            run.assert_not_called()

    def test_gui_cancellation_never_runs_program(self):
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch(
                "expose_protocol.launchers.run_osascript", return_value="cancelled") as osa, patch("subprocess.run") as run:
            self.assertEqual(launch(self.protocol(), "test-editor://open?file=README.md", gui=True), 0)
            self.assertEqual(osa.call_args.args[0], CONFIRM_SCRIPT)
            self.assertIn(str(self.root / "README.md"), osa.call_args.args[1][0])
            run.assert_not_called()

    def test_terminal_starts_fresh_session_with_quoted_command(self):
        protocol = self.capture_protocol(mode="terminal")
        url = make_url(protocol, {"value": "hello;$(echo bad)"})
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch(
                "expose_protocol.launchers.run_osascript") as osa:
            self.assertEqual(launch(protocol, url, yes=True), 0)
            self.assertEqual(osa.call_args.args, (TERMINAL_SCRIPT, [terminal_command(prepare(protocol, url))]))
        self.assertNotIn("current", TERMINAL_SCRIPT)

    def test_both_terminal_shell_layers_preserve_argv_and_cwd(self):
        root = self.root / "it's $(echo bad) + café"
        root.mkdir()
        protocol = self.capture_protocol(mode="terminal", root=root)
        value = "quote ' ; $(touch SENTINEL) `echo bad` & *"
        invocation = prepare(protocol, make_url(protocol, {"value": value}))
        result = subprocess.run(["/bin/sh", "-c", terminal_command(invocation)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((root / "captured.json").read_text()), {"argv": [value], "cwd": str(root)})
        self.assertFalse((root / "SENTINEL").exists())

    def test_non_macos_terminal_and_gui_fail_before_confirmation(self):
        with patch("expose_protocol.launchers.sys.platform", "linux"), patch("subprocess.run") as run:
            for kwargs, protocol in (({"gui": True}, self.protocol()), ({"yes": True}, self.protocol(mode="terminal"))):
                with self.subTest(kwargs=kwargs), self.assertRaises(LaunchError):
                    launch(protocol, "test-editor://open?file=README.md", **kwargs)
            run.assert_not_called()

    def test_cli_default_no_and_interrupt(self):
        invocation = prepare(self.protocol(), "test-editor://open?file=README.md")
        with contextlib.redirect_stdout(io.StringIO()), patch("sys.stdin.isatty", return_value=True):
            for answer, expected in (("", False), ("no", False), ("yes", True), ("Y", True)):
                with self.subTest(answer=answer), patch("builtins.input", return_value=answer):
                    self.assertEqual(confirm_cli(invocation), expected)
            for exception in (EOFError, KeyboardInterrupt):
                with patch("builtins.input", side_effect=exception):
                    self.assertFalse(confirm_cli(invocation))

    def test_noninteractive_cli_requires_explicit_bypass(self):
        with patch("sys.stdin.isatty", return_value=False), self.assertRaises(LaunchError):
            confirm_cli(prepare(self.protocol(), "test-editor://open?file=README.md"))

    def test_program_and_osascript_failures_are_domain_errors(self):
        with patch("subprocess.run", side_effect=OSError("cannot execute")), self.assertRaises(LaunchError):
            launch(self.protocol(), "test-editor://open?file=README.md", yes=True)
        with patch("expose_protocol.launchers.sys.platform", "darwin"):
            for result in (subprocess.CompletedProcess([], 1, "", "permission denied"),):
                with patch("subprocess.run", return_value=result), self.assertRaises(LaunchError):
                    run_osascript("return 1", [])
            with patch("subprocess.run", side_effect=OSError("missing")), self.assertRaises(LaunchError):
                run_osascript("return 1", [])

    def test_osascript_uses_fixed_source_and_argument_list(self):
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch("subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "approved\n", "")) as run:
            self.assertEqual(run_osascript(CONFIRM_SCRIPT, ["hostile ' text"]), "approved")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-e", CONFIRM_SCRIPT, "--", "hostile ' text"])

    def test_actual_osascript_argument_boundary_on_macos(self):
        if sys.platform != "darwin":
            self.skipTest("macOS only")
        value = "' \" ; $(echo bad) `echo bad` café +"
        self.assertEqual(run_osascript("on run arguments\nreturn item 1 of arguments\nend run", [value]), value)
