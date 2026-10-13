from pathlib import Path
import sys
import tempfile
import unittest

from expose_protocol import Parameter, Protocol


class ProtocolTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="expose-protocol-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "README.md").write_text("example", encoding="utf-8")
        self.config = self.root / "profile.toml"
        self.config.write_text(
            'schema = 1\nscheme = "test-editor"\nroot = "."\n'
            f'command = ["{Path(sys.executable).as_posix()}", "{{file}}"]\n'
            '[parameters.file]\ntype = "path"\n', encoding="utf-8")

    def protocol(self, **overrides):
        fields = dict(scheme="test-editor", command=(sys.executable, "{file}"),
                      root=self.root, parameters=(Parameter("file", kind="path"),))
        fields.update(overrides)
        return Protocol(**fields)
