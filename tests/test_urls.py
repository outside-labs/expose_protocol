from pathlib import Path
import sys
from urllib.parse import quote

from expose_protocol import Parameter, RequestError, make_url, parse_url, prepare
from support import ProtocolTestCase


class URLTests(ProtocolTestCase):
    def test_relative_and_absolute_file_paths(self):
        protocol = self.protocol()
        for value in ("README.md", str(self.root / "README.md")):
            url = make_url(protocol, {"file": value})
            self.assertEqual(parse_url(protocol, url)["file"], self.root / "README.md")
            self.assertEqual(prepare(protocol, url).argv[-1], str(self.root / "README.md"))

    def test_utf8_spaces_plus_and_shell_punctuation_roundtrip(self):
        name = "café + O'Brien;$(echo bad)`name`.md"
        (self.root / name).touch()
        url = make_url(self.protocol(), {"file": name})
        self.assertIn("%20", url)
        self.assertIn("%2B", url)
        self.assertIn("%C3%A9", url)
        self.assertEqual(parse_url(self.protocol(), url)["file"], self.root / name)

    def test_plus_is_not_a_space(self):
        (self.root / "a+b.md").touch()
        self.assertEqual(parse_url(self.protocol(), "test-editor://open?file=a+b.md")["file"], self.root / "a+b.md")

    def test_rejects_bad_structure(self):
        for url in ("test-editor:open?file=README.md", "other://open?file=README.md",
                    "test-editor://run?file=README.md", "test-editor://user@open?file=README.md",
                    "test-editor://open:3?file=README.md", "test-editor://open/?file=README.md",
                    "test-editor://open?file=README.md#", "test-editor://open?file=README.md#x",
                    "test-editor://[bad?file=README.md", "test-editor://file='README.md'"):
            with self.subTest(url=url), self.assertRaises(RequestError):
                parse_url(self.protocol(), url)

    def test_rejects_whitespace_and_raw_unicode_before_urlsplit(self):
        for value in ("\n", "\t", " ", "é", "\x7f", "\u202e", "\\"):
            with self.subTest(value=value), self.assertRaises(RequestError):
                parse_url(self.protocol(), "test-editor://open?file=" + value + "README.md")

    def test_rejects_hostile_encodings(self):
        for value in ("%", "%2", "%GG", "%FF", "%C0%AF", "%ED%A0%80", "%00", "%0A",
                      "%1B%5B2J", "%E2%80%AE", "%252e%252e%252fREADME.md", "%2500", "%2520"):
            with self.subTest(value=value), self.assertRaises(RequestError):
                parse_url(self.protocol(), "test-editor://open?file=" + value)

    def test_missing_duplicate_unknown_empty_fields(self):
        for query in ("", "file", "file=", "file=README.md&file=README.md",
                      "file=README.md&%66ile=README.md", "file=README.md&command=sh",
                      "file=README.md&", "=README.md", "File=README.md"):
            with self.subTest(query=query), self.assertRaises(RequestError):
                parse_url(self.protocol(), "test-editor://open?" + query)

    def test_bounded_url_and_field_count(self):
        for query in ("file=" + "a" * 8200, "&".join(f"a{i}=b" for i in range(33))):
            with self.subTest(query=query[:20]), self.assertRaises(RequestError):
                parse_url(self.protocol(), "test-editor://open?" + query)

    def test_traversal_absolute_escape_missing_and_directory_rejected(self):
        for value in ("../README.md", "sub/../README.md", "/etc/passwd", "missing.md", ".", "a\\b"):
            with self.subTest(value=value), self.assertRaises(RequestError):
                parse_url(self.protocol(), "test-editor://open?file=" + quote(value, safe=""))

    def test_symlink_escape_and_internal_symlink(self):
        outside = self.root.parent / (self.root.name + "-outside")
        outside.write_text("outside")
        self.addCleanup(outside.unlink)
        (self.root / "escape.md").symlink_to(outside)
        (self.root / "inside.md").symlink_to(self.root / "README.md")
        with self.assertRaises(RequestError):
            parse_url(self.protocol(), "test-editor://open?file=escape.md")
        self.assertEqual(parse_url(self.protocol(), "test-editor://open?file=inside.md")["file"], self.root / "README.md")

    def test_new_file_requires_confined_existing_parent(self):
        protocol = self.protocol(parameters=(Parameter("file", kind="path", must_exist=False),))
        self.assertEqual(parse_url(protocol, "test-editor://open?file=new.md")["file"], self.root / "new.md")
        for value in ("missing/new.md", "../new.md", str(self.root.parent / "new.md")):
            with self.subTest(value=value), self.assertRaises(RequestError):
                make_url(protocol, {"file": value})

    def test_typed_values_and_stable_argument_order(self):
        protocol = self.protocol(command=(sys.executable, "{count}", "{color}", "{text}"), parameters=(
            Parameter("count", kind="integer", minimum=-2, maximum=10),
            Parameter("color", kind="choice", choices=("red", "blue")), Parameter("text", max_length=20)))
        url = make_url(protocol, {"text": "hello", "color": "blue", "count": "003"})
        self.assertEqual(url, "test-editor://open?count=3&color=blue&text=hello")
        self.assertEqual(parse_url(protocol, url), {"count": 3, "color": "blue", "text": "hello"})
        self.assertEqual(prepare(protocol, url).argv[1:], ("3", "blue", "hello"))
        for fields in ({"count": "+1"}, {"count": "1.0"}, {"count": "11"}, {"color": "green"},
                       {"text": "-c"}, {"text": "a" * 21}, {"text": "\n"}, {"count": True}):
            with self.subTest(fields=fields), self.assertRaises(RequestError):
                make_url(protocol, {"count": 1, "color": "red", "text": "hello"} | fields)

    def test_zero_parameter_protocol_and_repeated_placeholder(self):
        protocol = self.protocol(command=(sys.executable, "--version"), parameters=())
        self.assertEqual(make_url(protocol, {}), "test-editor://open")
        self.assertEqual(parse_url(protocol, "test-editor://open"), {})
        with self.assertRaises(RequestError):
            parse_url(protocol, "test-editor://open?anything=a")
        repeated = self.protocol(command=(sys.executable, "{file}", "{file}"))
        self.assertEqual(prepare(repeated, "test-editor://open?file=README.md").argv[1:2],
                         prepare(repeated, "test-editor://open?file=README.md").argv[2:])

    def test_url_scheme_case_is_insensitive(self):
        self.assertEqual(parse_url(self.protocol(), "TEST-EDITOR://open?file=README.md")["file"], self.root / "README.md")
