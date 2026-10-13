"""Immutable local definitions. Definitions are trusted; URL values are not."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib
import unicodedata
from typing import Any

from .errors import DefinitionError, RequestError

MAX_VALUE_LENGTH = 2048
MAX_DEFINITION_BYTES = 65536
RESERVED_SCHEMES = frozenset({
    "http", "https", "file", "ftp", "ftps", "sftp", "ssh", "mailto",
    "tel", "sms", "facetime", "facetime-audio", "data", "javascript",
    "about", "blob", "ws", "wss", "itms", "itmss", "macappstore",
    "x-apple.systempreferences", "x-apple.findmy", "x-apple.feedbackassistant",
})
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]{0,63}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}\Z")


def has_control(text: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in text)


def validate_scheme(scheme: str) -> str:
    if not isinstance(scheme, str) or not _SCHEME.fullmatch(scheme):
        raise DefinitionError("scheme must be 1–64 lowercase URL-scheme characters")
    if scheme in RESERVED_SCHEMES or scheme.startswith(("x-apple.", "com.apple.")):
        raise DefinitionError(f"reserved scheme: {scheme}")
    return scheme


def _name(name: str, description: str) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise DefinitionError(f"{description} must be a lowercase identifier (at most 32 characters)")


@dataclass(frozen=True)
class Parameter:
    """A required URL field. Path values are confined to the protocol's root."""

    name: str
    kind: str = "string"
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    max_length: int = 1024
    must_exist: bool = True

    def __post_init__(self) -> None:
        _name(self.name, "parameter name")
        if not isinstance(self.kind, str) or self.kind not in {"path", "string", "integer", "choice"}:
            raise DefinitionError("unsupported parameter type")
        if type(self.max_length) is not int or not 1 <= self.max_length <= MAX_VALUE_LENGTH:
            raise DefinitionError(f"{self.name}: max_length must be 1–{MAX_VALUE_LENGTH}")
        if type(self.must_exist) is not bool:
            raise DefinitionError(f"{self.name}: must_exist must be a Boolean")
        if self.kind != "path" and not self.must_exist:
            raise DefinitionError(f"{self.name}: must_exist applies only to paths")
        if not isinstance(self.choices, (tuple, list)):
            raise DefinitionError(f"{self.name}: choices must be a list of strings")
        object.__setattr__(self, "choices", tuple(self.choices))
        if self.kind == "choice":
            if not self.choices or len(self.choices) > 64:
                raise DefinitionError(f"{self.name}: choice requires 1–64 allowed strings")
            for choice in self.choices:
                if (not isinstance(choice, str) or not choice or has_control(choice)
                        or choice.startswith("-") or len(choice) > self.max_length):
                    raise DefinitionError(f"{self.name}: invalid choice")
            if len(set(self.choices)) != len(self.choices):
                raise DefinitionError(f"{self.name}: duplicate choices")
        elif self.choices:
            raise DefinitionError(f"{self.name}: choices applies only to choice parameters")
        if self.kind == "integer":
            if (type(self.minimum) is not int or type(self.maximum) is not int
                    or self.minimum > self.maximum
                    or max(abs(self.minimum), abs(self.maximum)) > 10**18):
                raise DefinitionError(f"{self.name}: integer requires ordered bounds within ±10^18")
        elif self.minimum is not None or self.maximum is not None:
            raise DefinitionError(f"{self.name}: bounds apply only to integers")

    def convert(self, value: str, root: Path) -> str | int | Path:
        if (not isinstance(value, str) or not value or has_control(value)
                or len(value) > self.max_length):
            raise RequestError(f"{self.name}: expected nonempty text without control characters (max {self.max_length})")
        if self.kind == "integer":
            if not re.fullmatch(r"-?[0-9]{1,19}", value):
                raise RequestError(f"{self.name}: expected a decimal integer")
            number = int(value)
            if not self.minimum <= number <= self.maximum:
                raise RequestError(f"{self.name}: expected {self.minimum}–{self.maximum}")
            return number
        if self.kind == "path":
            if "\\" in value or ".." in value.split("/"):
                raise RequestError(f"{self.name}: path traversal and backslashes are not allowed")
            supplied = Path(value)
            try:
                path = (root / supplied).resolve(strict=self.must_exist)
                path.relative_to(root)
                if path.exists():
                    if not path.is_file():
                        raise RequestError(f"{self.name}: expected a regular file")
                elif not path.parent.is_dir():
                    raise RequestError(f"{self.name}: new files require an existing parent directory")
            except (OSError, ValueError, RuntimeError) as exc:
                if isinstance(exc, RequestError):
                    raise
                raise RequestError(f"{self.name}: path must be a file beneath the configured root") from exc
            return path
        if value.startswith("-"):
            raise RequestError(f"{self.name}: option-like values are not allowed")
        if self.kind == "choice" and value not in self.choices:
            raise RequestError(f"{self.name}: expected one of {', '.join(self.choices)}")
        return value

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.kind, "max_length": self.max_length}
        if self.kind == "path":
            result["must_exist"] = self.must_exist
        if self.kind == "integer":
            result.update(minimum=self.minimum, maximum=self.maximum)
        if self.kind == "choice":
            result["choices"] = list(self.choices)
        return result


@dataclass(frozen=True)
class Protocol:
    """A fixed executable and argv template with explicitly declared fields."""

    scheme: str
    command: tuple[str, ...]
    root: Path
    parameters: tuple[Parameter, ...] = ()
    action: str = "open"
    mode: str = "direct"

    def __post_init__(self) -> None:
        validate_scheme(self.scheme)
        _name(self.action, "action")
        if not isinstance(self.mode, str) or self.mode not in {"direct", "terminal"}:
            raise DefinitionError("mode must be direct or terminal")
        if not isinstance(self.command, (tuple, list)) or not 1 <= len(self.command) <= 64:
            raise DefinitionError("command must be a list of 1–64 argv tokens")
        if any(not isinstance(t, str) or not t or has_control(t) or len(t) > 4096 for t in self.command):
            raise DefinitionError("command tokens must be nonempty text without control characters")
        object.__setattr__(self, "command", tuple(self.command))
        executable = Path(self.command[0])
        if not executable.is_absolute() or "{" in self.command[0] or "}" in self.command[0]:
            raise DefinitionError("command[0] must be a fixed absolute executable path")
        try:
            executable = executable.resolve(strict=True)
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise DefinitionError("command[0] must be an executable file")
            root = Path(self.root).resolve(strict=True)
            if not root.is_dir() or has_control(str(root)):
                raise DefinitionError("root must be an existing directory without control characters")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            if isinstance(exc, DefinitionError):
                raise
            raise DefinitionError("executable and root must exist") from exc
        object.__setattr__(self, "command", (str(executable), *self.command[1:]))
        object.__setattr__(self, "root", root)
        if not isinstance(self.parameters, (list, tuple)) or len(self.parameters) > 32:
            raise DefinitionError("parameters must be a list of at most 32 Parameter objects")
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if any(not isinstance(p, Parameter) for p in self.parameters):
            raise DefinitionError("parameters must contain Parameter objects")
        names = {p.name for p in self.parameters}
        if len(names) != len(self.parameters):
            raise DefinitionError("duplicate parameter names")
        used = set()
        for token in self.command[1:]:
            placeholder = _PLACEHOLDER.fullmatch(token)
            if placeholder:
                used.add(placeholder[1])
            elif "{" in token or "}" in token:
                raise DefinitionError("placeholders must occupy a whole argv token")
        if used != names:
            raise DefinitionError("command placeholders must match the declared parameters")

    def to_mapping(self) -> dict[str, Any]:
        """Return a versioned snapshot with pinned absolute paths."""
        return {"schema": 1, "scheme": self.scheme, "action": self.action,
                "command": list(self.command), "root": str(self.root), "mode": self.mode,
                "parameters": {p.name: p.to_mapping() for p in self.parameters}}


def protocol_from_mapping(data: dict[str, Any], *, base: Path) -> Protocol:
    """Validate a versioned mapping; relative roots are relative to base."""
    allowed = {"schema", "scheme", "action", "command", "root", "mode", "parameters"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise DefinitionError("unknown definition fields")
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise DefinitionError("schema must be 1")
    if not {"scheme", "command", "root"} <= data.keys():
        raise DefinitionError("definition requires scheme, command and root")
    if not isinstance(data["root"], str) or not data["root"] or has_control(data["root"]):
        raise DefinitionError("root must be a nonempty path string")
    raw_parameters = data.get("parameters", {})
    if not isinstance(raw_parameters, dict):
        raise DefinitionError("parameters must be a table")
    parameters = []
    keys = {"type", "choices", "minimum", "maximum", "max_length", "must_exist"}
    for name, declaration in raw_parameters.items():
        if not isinstance(declaration, dict) or set(declaration) - keys or "type" not in declaration:
            raise DefinitionError(f"{name}: invalid parameter declaration")
        options = dict(declaration)
        options["kind"] = options.pop("type")
        parameters.append(Parameter(name=name, **options))
    return Protocol(scheme=data["scheme"], command=data["command"],
                    root=base / Path(data["root"]).expanduser(), parameters=parameters,
                    action=data.get("action", "open"), mode=data.get("mode", "direct"))


def load_protocol(path: str | Path) -> Protocol:
    """Load one explicitly selected trusted TOML file; never search for config."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_DEFINITION_BYTES:
            raise DefinitionError("definition is too large")
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise DefinitionError(f"cannot read TOML definition: {exc}") from exc
    return protocol_from_mapping(data, base=path.resolve().parent)
