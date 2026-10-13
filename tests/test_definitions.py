from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

from expose_protocol import DefinitionError, Parameter, load_protocol
from expose_protocol.definitions import protocol_from_mapping
from support import ProtocolTestCase


class DefinitionTests(ProtocolTestCase):
    def test_relative_root_is_relative_to_definition(self):
        self.assertEqual(load_protocol(self.config).root, self.root)

    def test_immutable_normalized_definition(self):
        protocol = self.protocol(command=[sys.executable, "{file}"], parameters=[Parameter("file", kind="path")])
        self.assertIsInstance(protocol.command, tuple)
        self.assertIsInstance(protocol.parameters, tuple)
        self.assertTrue(Path(protocol.command[0]).is_absolute())
        with self.assertRaises(FrozenInstanceError):
            protocol.scheme = "other"

    def test_snapshot_roundtrip(self):
        protocol = self.protocol()
        self.assertEqual(protocol_from_mapping(protocol.to_mapping(), base=self.root), protocol)

    def test_scheme_grammar_and_reserved_names(self):
        for scheme in ("", "Vim", "1vim", "vim/name", "vim space", "v" * 65,
                       "http", "https", "file", "javascript", "x-apple.anything", None):
            with self.subTest(scheme=scheme), self.assertRaises(DefinitionError):
                self.protocol(scheme=scheme)
        self.assertEqual(self.protocol(scheme="my-editor.v2+local").scheme, "my-editor.v2+local")

    def test_unknown_fields_and_schema(self):
        valid = self.protocol().to_mapping()
        for changes in ({"shell": True}, {"schema": 2}, {"schema": True},
                        {"root": []}, {"parameters": []}, {"mode": []}, {"scheme": []}):
            with self.subTest(changes=changes), self.assertRaises(DefinitionError):
                protocol_from_mapping(valid | changes, base=self.root)

    def test_required_definition_fields(self):
        for key in ("scheme", "command", "root", "schema"):
            mapping = self.protocol().to_mapping()
            del mapping[key]
            with self.subTest(key=key), self.assertRaises(DefinitionError):
                protocol_from_mapping(mapping, base=self.root)

    def test_missing_invalid_and_oversized_toml(self):
        with self.assertRaises(DefinitionError):
            load_protocol(self.root / "missing.toml")
        for contents in (b"invalid=", b"\xff", b" " * 65537):
            self.config.write_bytes(contents)
            with self.subTest(contents=contents[:10]), self.assertRaises(DefinitionError):
                load_protocol(self.config)

    def test_missing_root_and_executable(self):
        for changes in ({"root": self.root / "missing"}, {"root": self.root / "README.md"},
                        {"command": ("vim", "{file}")},
                        {"command": (str(self.root / "missing"), "{file}")},
                        {"command": (str(self.root / "README.md"), "{file}")}):
            with self.subTest(changes=changes), self.assertRaises(DefinitionError):
                self.protocol(**changes)

    def test_argv_and_placeholder_constraints(self):
        for command in ((), "vim", (sys.executable, "--file={file}"),
                        (sys.executable, "{unknown}"), (sys.executable, "text\n"),
                        (sys.executable,), ("{file}",), (sys.executable, None)):
            with self.subTest(command=command), self.assertRaises(DefinitionError):
                self.protocol(command=command)

    def test_duplicate_parameters_and_invalid_action(self):
        parameter = Parameter("file", kind="path")
        with self.assertRaises(DefinitionError):
            self.protocol(parameters=(parameter, parameter))
        for action in ("open/path", "open:3", "Open", None):
            with self.subTest(action=action), self.assertRaises(DefinitionError):
                self.protocol(action=action)

    def test_parameter_declaration_constraints(self):
        for fields in ({"name": "--file"}, {"kind": "code"}, {"kind": []},
                       {"max_length": True}, {"max_length": 0}, {"max_length": 2049},
                       {"must_exist": "yes"}, {"must_exist": False},
                       {"kind": "integer"}, {"kind": "integer", "minimum": 10, "maximum": 1},
                       {"kind": "integer", "minimum": False, "maximum": 1},
                       {"kind": "integer", "minimum": 0, "maximum": 10**19},
                       {"kind": "choice"}, {"kind": "choice", "choices": ("a", "a")},
                       {"kind": "choice", "choices": ("-c",)},
                       {"kind": "choice", "choices": ("a\n",)},
                       {"choices": ("a",)}, {"choices": "a"}, {"minimum": 1}):
            with self.subTest(fields=fields), self.assertRaises(DefinitionError):
                Parameter(**({"name": "value"} | fields))

    def test_unknown_parameter_fields(self):
        valid = self.protocol().to_mapping()
        for declaration in ({"type": "path", "required": False}, {"choices": ["a"]}, "path"):
            with self.subTest(declaration=declaration), self.assertRaises(DefinitionError):
                protocol_from_mapping(valid | {"parameters": {"file": declaration}}, base=self.root)
