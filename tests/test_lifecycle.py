import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import uuid

from expose_protocol import DefinitionError, LaunchError, install, uninstall
from expose_protocol.lifecycle import _same_app
from expose_protocol.macos import bundle_identifier, app_scheme
from expose_protocol.services import DefaultHandler, get_handler, register_app, unregister_app, LSREGISTER
from support import ProtocolTestCase
import test_macos


class LifecycleTests(ProtocolTestCase):
    fake_native = test_macos.BundleTests.fake_native

    def setUp(self):
        super().setUp()
        self.apps = self.root / "Applications"
        self.apps.mkdir()
        self.app = self.apps / "test-editor.app"
        self.receipt = self.apps / "test-editor.json"
        self.handler = DefaultHandler()
        self.events = []
        self.addCleanup(patch.stopall)
        patch("expose_protocol.launchers.sys.platform", "darwin").start()
        patch("expose_protocol.macos.run_native", side_effect=self.fake_native).start()
        patch("expose_protocol.lifecycle.run_native", return_value="").start()
        patch("expose_protocol.lifecycle.get_handler", side_effect=lambda _: self.handler).start()
        patch("expose_protocol.lifecycle.register_app", side_effect=self.register).start()
        patch("expose_protocol.lifecycle.unregister_app", side_effect=self.unregister).start()
        patch("expose_protocol.lifecycle.set_handler", side_effect=self.select).start()

    def register(self, app):
        self.events.append(("register", app))

    def unregister(self, app):
        self.events.append(("unregister", app))
        if self.handler.path == app:
            self.handler = DefaultHandler()

    def select(self, scheme, identifier):
        self.events.append(("select", identifier))
        path = self.app if identifier == bundle_identifier(scheme) else self.root / "Foreign.app"
        self.handler = DefaultHandler(identifier, path)

    def test_install_and_removal_restore_previous_handler(self):
        self.handler = DefaultHandler("org.example.foreign", self.root / "Foreign.app")
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps)
        self.assertFalse(self.app.exists())
        self.assertEqual(install(self.protocol(), directory=self.apps, takeover=True), self.app)
        self.assertEqual(app_scheme(self.app), "test-editor")
        self.assertEqual(json.loads(self.receipt.read_text())["previous_handler"], "org.example.foreign")
        self.assertEqual(uninstall("test-editor", directory=self.apps), self.app)
        self.assertFalse(self.app.exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.handler.bundle_id, "org.example.foreign")
        self.assertFalse(list(self.apps.glob(".*lock")))

    def test_update_is_explicit_and_preserves_original_default(self):
        self.handler = DefaultHandler("org.example.foreign", self.root / "Foreign.app")
        install(self.protocol(), directory=self.apps, takeover=True)
        original = self.receipt.read_text()
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps)
        self.assertEqual(self.receipt.read_text(), original)
        install(self.protocol(action="edit"), directory=self.apps, replace=True)
        self.assertEqual(json.loads(self.receipt.read_text())["previous_handler"], "org.example.foreign")
        self.assertFalse(list(self.apps.glob(".expose-protocol-*")))

    def test_failed_update_restores_app_receipt_and_handler(self):
        install(self.protocol(), directory=self.apps)
        original = self.receipt.read_bytes()
        calls = 0
        def register(app):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise LaunchError("registration failed")
            self.register(app)
        with patch("expose_protocol.lifecycle.register_app", side_effect=register), self.assertRaises(LaunchError):
            install(self.protocol(action="edit"), directory=self.apps, replace=True)
        self.assertEqual(self.receipt.read_bytes(), original)
        self.assertEqual(app_scheme(self.app), "test-editor")
        manifest = json.loads((self.app / "Contents/Resources/manifest.json").read_text())
        self.assertEqual(manifest["protocol"]["action"], "open")
        self.assertEqual(self.handler, DefaultHandler(bundle_identifier("test-editor"), self.app))
        self.assertFalse(list(self.apps.glob(".expose-protocol-*")))

    def test_delayed_routing_is_checked_before_installation_succeeds(self):
        expected = DefaultHandler(bundle_identifier("test-editor"), self.app)
        with patch("expose_protocol.lifecycle.get_handler", side_effect=[DefaultHandler(),
                DefaultHandler(expected.bundle_id), expected]), patch("expose_protocol.lifecycle.time.sleep") as sleep:
            self.assertEqual(install(self.protocol(), directory=self.apps), self.app)
            sleep.assert_called_once_with(0.05)

    def test_selection_failure_restores_previous_files(self):
        install(self.protocol(), directory=self.apps)
        original = self.receipt.read_bytes()
        with patch("expose_protocol.lifecycle.get_handler", side_effect=[self.handler] + [DefaultHandler()] * 7), patch(
                "expose_protocol.lifecycle.time.sleep"), self.assertRaises(LaunchError) as error:
            install(self.protocol(action="edit"), directory=self.apps, replace=True)
        self.assertIn("returned handler", str(error.exception))
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_filesystem_identity_accepts_an_alias_for_the_same_app(self):
        install(self.protocol(), directory=self.apps)
        alias = self.root / "Alias.app"
        alias.symlink_to(self.app, target_is_directory=True)
        self.assertTrue(_same_app(alias, self.app))
        self.assertFalse(_same_app(self.root / "Other.app", self.app))

    def test_failed_new_install_removes_generated_files_and_restores_default(self):
        self.handler = DefaultHandler("org.example.foreign", self.root / "Foreign.app")
        count = 0
        def select(scheme, identifier):
            nonlocal count
            count += 1
            if count == 1:
                raise LaunchError("selection failed")
            self.select(scheme, identifier)
        with patch("expose_protocol.lifecycle.set_handler", side_effect=select), self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps, takeover=True)
        self.assertFalse(self.app.exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.handler.bundle_id, "org.example.foreign")

    def test_recovery_failure_retains_backup_and_reports_path(self):
        install(self.protocol(), directory=self.apps)
        original_rmtree = shutil.rmtree
        def remove(path, *args, **kwargs):
            if Path(path) == self.app:
                raise OSError("removal denied")
            return original_rmtree(path, *args, **kwargs)
        with patch("expose_protocol.lifecycle.register_app", side_effect=LaunchError("registration failed")), patch(
                "expose_protocol.lifecycle.shutil.rmtree", side_effect=remove), self.assertRaises(LaunchError) as failure:
            install(self.protocol(action="edit"), directory=self.apps, replace=True)
        stages = list(self.apps.glob(".expose-protocol-*"))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / "previous.app").is_dir())
        self.assertIn(str(stages[0]), str(failure.exception))

    def test_signature_failure_and_foreign_application_are_preserved(self):
        self.app.mkdir()
        sentinel = self.app / "sentinel"
        sentinel.write_text("keep")
        for action in (lambda: install(self.protocol(), directory=self.apps, replace=True),
                       lambda: uninstall("test-editor", directory=self.apps)):
            with self.assertRaises(DefinitionError):
                action()
            self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse(self.events)
        shutil.rmtree(self.app)
        install(self.protocol(), directory=self.apps)
        self.events.clear()
        with patch("expose_protocol.lifecycle.run_native", side_effect=LaunchError("invalid signature")), self.assertRaises(LaunchError):
            uninstall("test-editor", directory=self.apps)
        self.assertTrue(self.app.is_dir())
        self.assertFalse(self.events)

    def test_symlinked_directory_application_and_receipt_rejected(self):
        link = self.root / "Linked Applications"
        link.symlink_to(self.apps, target_is_directory=True)
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=link)
        self.app.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(DefinitionError):
            install(self.protocol(), directory=self.apps, replace=True)
        self.app.unlink()
        self.receipt.symlink_to(self.root / "README.md")
        with self.assertRaises(DefinitionError):
            install(self.protocol(), directory=self.apps)
        self.assertEqual((self.root / "README.md").read_text(), "example")

    def test_lock_or_orphan_receipt_prevents_changes(self):
        lock = self.apps / ".test-editor.lock"
        lock.touch()
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps)
        lock.unlink()
        self.receipt.write_text("orphan")
        with self.assertRaises(DefinitionError):
            install(self.protocol(), directory=self.apps)
        self.assertEqual(self.receipt.read_text(), "orphan")
        self.assertFalse(self.events)

    def test_same_bundle_id_at_another_path_is_a_collision(self):
        self.handler = DefaultHandler(bundle_identifier("test-editor"), self.root / "Other.app")
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps)
        self.assertFalse(self.app.exists())

    def test_fallback_application_without_preference_is_a_collision(self):
        self.handler = DefaultHandler(path=self.root / "Foreign.app")
        with self.assertRaises(LaunchError):
            install(self.protocol(), directory=self.apps)
        self.assertFalse(self.app.exists())

    def test_mismatched_receipt_is_preserved(self):
        install(self.protocol(), directory=self.apps)
        metadata = json.loads(self.receipt.read_text())
        for changes in ({"scheme": "other"}, {"format": True}, {"previous_handler": "bad\n"}, {"extra": 1}):
            self.receipt.write_text(json.dumps(metadata | changes))
            with self.subTest(changes=changes), self.assertRaises(DefinitionError):
                uninstall("test-editor", directory=self.apps)
            self.assertTrue(self.app.is_dir())

    def test_removal_failure_before_deletion_restores_registration(self):
        self.handler = DefaultHandler("org.example.foreign", self.root / "Foreign.app")
        install(self.protocol(), directory=self.apps, takeover=True)
        original = self.handler
        calls = 0
        def select(scheme, identifier):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise LaunchError("restore failed")
            self.select(scheme, identifier)
        with patch("expose_protocol.lifecycle.set_handler", side_effect=select), self.assertRaises(LaunchError):
            uninstall("test-editor", directory=self.apps)
        self.assertTrue(self.app.is_dir())
        self.assertEqual(self.handler, original)

    def test_unregistration_failure_never_deletes_app(self):
        install(self.protocol(), directory=self.apps)
        with patch("expose_protocol.lifecycle.unregister_app", side_effect=LaunchError("failed")), self.assertRaises(LaunchError):
            uninstall("test-editor", directory=self.apps)
        self.assertTrue(self.app.is_dir())
        self.assertTrue(self.receipt.is_file())

    def test_removal_does_not_change_another_current_default(self):
        install(self.protocol(), directory=self.apps)
        self.handler = DefaultHandler("org.example.foreign", self.root / "Foreign.app")
        self.events.clear()
        uninstall("test-editor", directory=self.apps)
        self.assertEqual(self.handler.bundle_id, "org.example.foreign")
        self.assertFalse(any(event[0] == "select" for event in self.events))

    def test_removal_works_after_profile_root_is_deleted(self):
        working = self.root / "Working"
        working.mkdir()
        (working / "README.md").touch()
        install(self.protocol(root=working), directory=self.apps)
        shutil.rmtree(working)
        uninstall("test-editor", directory=self.apps)
        self.assertFalse(self.app.exists())

    def test_native_registration_arguments_are_scoped_to_one_app(self):
        with patch("expose_protocol.services.run_native") as native:
            register_app(self.app)
            self.assertEqual(native.call_args.args[0], [LSREGISTER, "-f", str(self.app)])
            unregister_app(self.app)
            self.assertEqual(native.call_args.args[0], [LSREGISTER, "-u", str(self.app)])


class ServiceReadTests(ProtocolTestCase):
    def test_current_handler_native_read_only_query(self):
        if sys.platform != "darwin":
            self.skipTest("macOS only")
        scheme = "expose-test-" + uuid.uuid4().hex
        self.assertEqual(get_handler(scheme), DefaultHandler())

    def test_read_only_query_rejects_non_macos_and_reserved_schemes(self):
        with patch("expose_protocol.launchers.sys.platform", "linux"), self.assertRaises(LaunchError):
            get_handler("test-editor")
        with patch("expose_protocol.launchers.sys.platform", "darwin"), self.assertRaises(DefinitionError):
            get_handler("https")

    def test_native_lifecycle_on_disposable_macos_runner(self):
        if sys.platform != "darwin" or os.environ.get("EXPOSE_PROTOCOL_NATIVE_LIFECYCLE") != "1":
            self.skipTest("explicit opt-in on disposable macOS runner only")
        scheme = "expose-test-" + uuid.uuid4().hex
        protocol = self.protocol(scheme=scheme)
        directory = self.root / "Applications"
        app = install(protocol, directory=directory)
        try:
            self.assertEqual(get_handler(scheme), DefaultHandler(bundle_identifier(scheme), app))
            install(self.protocol(scheme=scheme, action="edit"), directory=directory, replace=True)
        finally:
            uninstall(scheme, directory=directory)
        self.assertFalse(app.exists())
        self.assertIsNone(get_handler(scheme).path)
