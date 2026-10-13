import json
from pathlib import Path
import subprocess
import sys

from expose_protocol import build_app
from support import ProtocolTestCase

WRAPPER = '''on run arguments
    set bridge to load script (POSIX file (item 1 of arguments))
    tell bridge to dispatchURL(item 2 of arguments, item 3 of arguments)
end run'''


class BridgeTests(ProtocolTestCase):
    def test_actual_compiled_shim_preserves_one_untrusted_url_argument(self):
        if sys.platform != "darwin":
            self.skipTest("macOS only")
        app = build_app(self.protocol(), self.root / "Bridge Café's App.app")
        resources = app / "Contents/Resources"
        # A private synthetic recorder replaces only the test bundle's helper.
        # No registration, prompt, editor or user-selected program is invoked.
        (resources / "launcher.py").write_text(
            "import json, pathlib, sys\n"
            "pathlib.Path(__file__).with_name('captured.json').write_text(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        script = resources / "Scripts/main.scpt"
        marker = self.root / "SENTINEL"
        urls = ("test-editor://open?file=O%27Brien%20%C3%A9.md",
                f"test-editor://open?file=';touch {marker};' $(touch {marker}) `touch {marker}`",
                "test-editor://open?file=bad\nvalue\twith\"quotes")
        for url in urls:
            with self.subTest(url=url):
                result = subprocess.run(["/usr/bin/osascript", "-e", WRAPPER, "--", str(script), url, str(app) + "/"],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((resources / "captured.json").read_text()), [url])
                self.assertFalse(marker.exists())
