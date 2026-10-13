import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from expose_protocol.cli import main
from support import ProtocolTestCase


class CLITests(ProtocolTestCase):
    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try:
                main(list(args))
            except SystemExit as exc:
                return exc.code, output.getvalue(), error.getvalue()
        return 0, output.getvalue(), error.getvalue()

    def test_help_never_reads_config(self):
        with patch("expose_protocol.cli.load_protocol") as load:
            for arguments in (("--help",), ("inspect", "--help"), ("link", "--help")):
                self.assertEqual(self.invoke(*arguments)[0], 0)
            load.assert_not_called()

    def test_validate_and_inspect_do_not_execute(self):
        with patch("subprocess.run") as run:
            code, output, _ = self.invoke("validate", "--config", str(self.config))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["scheme"], "test-editor")
            code, output, _ = self.invoke("inspect", "--config", str(self.config), "test-editor://open?file=README.md")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["argv"][-1], str(self.root / "README.md"))
            run.assert_not_called()

    def test_markdown_link_escapes_label(self):
        code, output, _ = self.invoke("link", "--config", str(self.config), "file=README.md", "--markdown", "Edit [README]")
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "[Edit \\[README\\]](<test-editor://open?file=README.md>)")

    def test_invalid_link_fields_and_labels(self):
        for tokens in (("file=README.md", "file=README.md"), ("file",),
                       ("file=README.md", "--markdown", "x\n"), ("command=sh",)):
            with self.subTest(tokens=tokens):
                code, output, error = self.invoke("link", "--config", str(self.config), *tokens)
                self.assertEqual(code, 2)
                self.assertFalse(output)
                self.assertIn("expose-protocol:", error)

    def test_bad_request_is_concise_usage_error(self):
        code, output, error = self.invoke("inspect", "--config", str(self.config), "test-editor://open?file=../outside")
        self.assertEqual(code, 2)
        self.assertFalse(output)
        self.assertNotIn("Traceback", error)

    def test_dry_run_never_confirms_or_launches(self):
        with patch("expose_protocol.cli.launch") as launch:
            code, output, error = self.invoke("run", "--config", str(self.config),
                                               "test-editor://open?file=README.md", "--dry-run")
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(output)["cwd"], str(self.root))
            launch.assert_not_called()

    def test_run_propagates_exit_code_and_yes(self):
        with patch("expose_protocol.cli.launch", return_value=7) as launch:
            self.assertEqual(self.invoke("run", "--config", str(self.config),
                                          "test-editor://open?file=README.md", "--yes")[0], 7)
            self.assertTrue(launch.call_args.kwargs["yes"])

    def test_install_passes_explicit_update_and_takeover(self):
        with patch("expose_protocol.cli.install", return_value=self.root / "App.app") as install:
            code, _, error = self.invoke("install", "--config", str(self.config), "--replace", "--takeover",
                                           "--apps-dir", str(self.root))
            self.assertEqual(code, 0, error)
            self.assertEqual(install.call_args.kwargs, {"directory": str(self.root), "replace": True, "takeover": True})

    def test_uninstall_does_not_require_current_definition(self):
        with patch("expose_protocol.cli.uninstall", return_value=self.root / "App.app") as remove, patch(
                "expose_protocol.cli.load_protocol") as load:
            self.assertEqual(self.invoke("uninstall", "test-editor")[0], 0)
            remove.assert_called_once_with("test-editor", directory=None)
            load.assert_not_called()

    def test_module_launcher_from_outside_checkout(self):
        env = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        result = subprocess.run([sys.executable, "-m", "expose_protocol", "link", "--config", str(self.config),
                                 "file=README.md"], cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "test-editor://open?file=README.md")
