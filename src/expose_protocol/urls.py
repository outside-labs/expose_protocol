"""A single URI grammar, strict decoding, and concrete launch planning."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import quote, unquote, urlsplit

from .definitions import Protocol, has_control
from .errors import RequestError

MAX_URL_LENGTH = 8192
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_ENCODED_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_URL_CHARACTERS = re.compile(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%+-]+\Z")
Value = str | int | Path


def _decode(raw: str) -> str:
    if _BAD_ESCAPE.search(raw):
        raise RequestError("malformed percent escape")
    try:
        value = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise RequestError("URL fields must be valid UTF-8") from exc
    if has_control(value) or _ENCODED_ESCAPE.search(value):
        raise RequestError("control characters and double encoding are not allowed")
    return value


def _values(protocol: Protocol, supplied: Mapping[str, str]) -> dict[str, Value]:
    expected = {p.name for p in protocol.parameters}
    if set(supplied) != expected:
        raise RequestError("URL fields must exactly match the declared parameters")
    return {p.name: p.convert(supplied[p.name], protocol.root) for p in protocol.parameters}


def parse_url(protocol: Protocol, url: str) -> dict[str, Value]:
    """Validate an untrusted URL, decode once, and return typed values."""
    if (not isinstance(url, str) or not 1 <= len(url) <= MAX_URL_LENGTH
            or not _URL_CHARACTERS.fullmatch(url)):
        raise RequestError(f"URL must be printable ASCII without spaces (max {MAX_URL_LENGTH})")
    if _BAD_ESCAPE.search(url):
        raise RequestError("malformed percent escape")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise RequestError("invalid URL structure") from exc
    if (parts.scheme != protocol.scheme or parts.netloc != protocol.action
            or parts.path or "#" in url or not re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", url)):
        raise RequestError(f"expected {protocol.scheme}://{protocol.action} with no path or fragment")
    fields: dict[str, str] = {}
    if parts.query:
        tokens = parts.query.split("&")
        if len(tokens) > 32:
            raise RequestError("too many URL fields")
        for token in tokens:
            key, separator, value = token.partition("=")
            if not separator:
                raise RequestError("each URL field requires name=value")
            key = _decode(key)
            if key in fields:
                raise RequestError(f"duplicate URL field: {key}")
            fields[key] = _decode(value)
    return _values(protocol, fields)


def make_url(protocol: Protocol, values: Mapping[str, Value]) -> str:
    """Build a canonical link from validated values, preserving relative paths."""
    raw = {}
    for key, value in values.items():
        if not isinstance(value, (str, Path)) and type(value) is not int:
            raise RequestError("link values must be strings, integers or paths")
        raw[key] = str(value)
    typed = _values(protocol, raw)
    fields = []
    for parameter in protocol.parameters:
        value = str(typed[parameter.name]) if parameter.kind == "integer" else raw[parameter.name]
        fields.append(f"{parameter.name}={quote(value, safe='')}")
    result = f"{protocol.scheme}://{protocol.action}" + ("?" + "&".join(fields) if fields else "")
    parse_url(protocol, result)
    return result


@dataclass(frozen=True)
class Invocation:
    """A validated, fully resolved argv and working directory."""

    protocol: Protocol
    argv: tuple[str, ...]
    cwd: Path

    def to_mapping(self) -> dict[str, object]:
        return {"scheme": self.protocol.scheme, "action": self.protocol.action,
                "mode": self.protocol.mode, "argv": list(self.argv), "cwd": str(self.cwd)}


def prepare(protocol: Protocol, url: str) -> Invocation:
    values = parse_url(protocol, url)
    argv = tuple(str(values[token[1:-1]]) if token.startswith("{") else token
                 for token in protocol.command)
    return Invocation(protocol=protocol, argv=argv, cwd=protocol.root)
