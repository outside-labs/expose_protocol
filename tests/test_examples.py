import json
from pathlib import Path
import subprocess
import sys

from expose_protocol import RequestError, load_protocol, make_url, prepare
from support import ProtocolTestCase

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class ExampleTests(ProtocolTestCase):
    def test_python_profile_has_fixed_script_and_bounded_parameters(self):
        profile = self.root / "python.toml"
        profile.write_text((EXAMPLES / "python.toml").read_text().replace(
            "/absolute/path/to/python3.14", str(Path(sys.executable).resolve())), encoding="utf-8")
        (self.root / "repeat.py").write_bytes((EXAMPLES / "repeat.py").read_bytes())
        protocol = load_protocol(profile)
        url = make_url(protocol, {"count": 2, "message": "hello"})
        self.assertEqual(url, "python://run?count=2&message=hello")
        invocation = prepare(protocol, url)
        result = subprocess.run(list(invocation.argv), cwd=invocation.cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "hello\nhello\n")
        for values in ({"count": 6, "message": "hello"}, {"count": 1, "message": "eval"},
                       {"count": 1, "message": "hello", "code": "print(1)"}):
            with self.subTest(values=values), self.assertRaises(RequestError):
                make_url(protocol, values)

    def test_editor_examples_select_confined_files(self):
        if sys.platform != "darwin":
            self.skipTest("bundled macOS editors only")
        for name in ("vim", "nano"):
            with self.subTest(name=name):
                protocol = load_protocol(EXAMPLES / f"{name}.toml")
                invocation = prepare(protocol, make_url(protocol, {"file": "README.md"}))
                self.assertEqual(invocation.argv[-1], str(EXAMPLES / "README.md"))
                self.assertEqual(invocation.protocol.mode, "terminal")
