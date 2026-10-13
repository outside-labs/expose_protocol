import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from unittest.mock import patch

from expose_protocol import DefinitionError, LaunchError, build_app, inspect_app
from expose_protocol.macos import APP_HELPER, bundle_main, load_manifest
from support import ProtocolTestCase


class BundleTests(ProtocolTestCase):
    def fake_native(self, argv):
        if argv[0] == "/usr/bin/osacompile":
            app = Path(argv[2])
            (app / "Contents/MacOS").mkdir(parents=True)
            (app / "Contents/Resources").mkdir()
            (app / "Contents/MacOS/applet").touch()
            with (app / "Contents/Info.plist").open("wb") as stream:
                plistlib.dump({"CFBundleExecutable": "applet", "NSCameraUsageDescription": "unused",
                              "NSAppleEventsUsageDescription": "unused", "NSContactsUsageDescription": "unused"}, stream)
        return ""

    def fake_build(self, *, protocol=None, name="Test.app"):
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch(
                "expose_protocol.macos.run_native", side_effect=self.fake_native):
            return build_app(protocol or self.protocol(), self.root / name)

    def test_snapshot_library_and_minimal_permissions(self):
        app = self.fake_build()
        self.assertEqual(inspect_app(app), self.protocol())
        resources = app / "Contents/Resources"
        self.assertEqual((resources / "launcher.py").read_text(), APP_HELPER)
        self.assertTrue((resources / "expose_protocol/urls.py").is_file())
        self.assertFalse(any(resources.rglob("*.pyc")))
        manifest = json.loads((resources / "manifest.json").read_text())
        self.assertEqual(manifest["python"], str(Path(sys.executable).resolve()))
        self.assertEqual(manifest["protocol"], self.protocol().to_mapping())
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        self.assertFalse(any(key.endswith("UsageDescription") for key in info))

    def test_terminal_permission_and_shell_url_role(self):
        app = self.fake_build(protocol=self.protocol(mode="terminal"))
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        self.assertEqual([key for key in info if key.endswith("UsageDescription")], ["NSAppleEventsUsageDescription"])
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleTypeRole"], "Shell")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["test-editor"])

    def test_build_refuses_existing_symlink_invalid_name_and_non_macos(self):
        existing = self.root / "Existing.app"
        existing.mkdir()
        sentinel = existing / "sentinel"
        sentinel.write_text("keep")
        link = self.root / "Link.app"
        link.symlink_to(existing, target_is_directory=True)
        dangling = self.root / "Dangling.app"
        dangling.symlink_to(self.root / "missing")
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch("subprocess.run") as run:
            for output in (existing, link, dangling, self.root / "bad.txt", self.root / "bad\n.app", self.root / "missing/X.app"):
                with self.subTest(output=output), self.assertRaises(LaunchError):
                    build_app(self.protocol(), output)
            run.assert_not_called()
        self.assertEqual(sentinel.read_text(), "keep")
        with patch("expose_protocol.launchers.sys.platform", "linux"), self.assertRaises(LaunchError):
            build_app(self.protocol(), self.root / "Other.app")

    def test_compiler_or_signer_failure_leaves_no_output(self):
        for failing_tool in ("/usr/bin/osacompile", "/usr/bin/codesign"):
            def native(argv):
                if argv[0] == failing_tool:
                    raise LaunchError("tool failed")
                return self.fake_native(argv)
            with self.subTest(failing_tool=failing_tool), patch("expose_protocol.launchers.sys.platform", "darwin"), patch(
                    "expose_protocol.macos.run_native", side_effect=native), self.assertRaises(LaunchError):
                build_app(self.protocol(), self.root / "Failure.app")
            self.assertFalse((self.root / "Failure.app").exists())
            self.assertFalse(list(self.root.glob(".expose-protocol-build-*")))

    def test_output_created_during_build_is_preserved(self):
        target = self.root / "Concurrent.app"
        def native(argv):
            self.fake_native(argv)
            if "--verify" in argv:
                target.mkdir()
                (target / "sentinel").write_text("keep")
            return ""
        with patch("expose_protocol.launchers.sys.platform", "darwin"), patch(
                "expose_protocol.macos.run_native", side_effect=native), self.assertRaises(LaunchError):
            build_app(self.protocol(), target)
        self.assertEqual((target / "sentinel").read_text(), "keep")

    def test_inspection_rejects_identity_helper_and_symlink_changes(self):
        app = self.fake_build()
        plist = app / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        for key, value in (("CFBundleIdentifier", "unrelated.app"), ("ExposeProtocolSchema", True),
                           ("CFBundleURLTypes", []), ("CFBundleExecutable", "other")):
            plist.write_bytes(plistlib.dumps(info | {key: value}))
            with self.subTest(key=key), self.assertRaises(DefinitionError):
                inspect_app(app)
        plist.write_bytes(plistlib.dumps(info))
        helper = app / "Contents/Resources/launcher.py"
        helper.write_text("print('different')")
        with self.assertRaises(DefinitionError):
            inspect_app(app)
        helper.write_text(APP_HELPER)
        helper.unlink()
        helper.symlink_to(self.root / "README.md")
        with self.assertRaises(DefinitionError):
            inspect_app(app)

    def test_manifest_validation(self):
        app = self.fake_build()
        path = app / "Contents/Resources/manifest.json"
        valid = json.loads(path.read_text())
        for invalid in ({}, valid | {"format": 2}, valid | {"format": True}, valid | {"extra": 1},
                        valid | {"python": "python"}, valid | {"python": str(self.root / "missing")},
                        valid | {"protocol": {"schema": 1}}):
            path.write_text(json.dumps(invalid))
            with self.subTest(invalid=invalid), self.assertRaises(DefinitionError):
                load_manifest(path)

    def test_helper_requires_one_url_and_builtin_gui_confirmation(self):
        app = self.fake_build()
        manifest = app / "Contents/Resources/manifest.json"
        with patch("expose_protocol.macos.launch", return_value=0) as launch:
            with self.assertRaises(SystemExit) as exit:
                bundle_main(manifest, ["test-editor://open?file=README.md"])
            self.assertEqual(exit.exception.code, 0)
            self.assertEqual(launch.call_args.kwargs, {"gui": True})
        for arguments in ([], ["one", "two"]):
            with patch("expose_protocol.macos.launch") as launch, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                bundle_main(manifest, arguments)
            self.assertEqual(exit.exception.code, 2)
            launch.assert_not_called()

    def test_bundled_helper_is_isolated_and_rejects_invalid_url_before_gui(self):
        app = self.fake_build()
        poison = self.root / "poison"
        poison.mkdir()
        (poison / "expose_protocol.py").write_text("raise RuntimeError('PYTHONPATH used')")
        helper = app / "Contents/Resources/launcher.py"
        result = subprocess.run([sys.executable, "-I", str(helper), "test-editor://open?file=../outside"],
                                cwd=self.root, env=os.environ | {"PYTHONPATH": str(poison)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("path traversal", result.stderr)
        self.assertNotIn("PYTHONPATH used", result.stderr)

    def test_real_app_compiles_and_signature_verifies_on_macos(self):
        if sys.platform != "darwin":
            self.skipTest("macOS only")
        app = build_app(self.protocol(), self.root / "Real ' Café + App.app")
        self.assertEqual(inspect_app(app), self.protocol())
        result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.root.glob(".expose-protocol-build-*")))
