"""Generate self-contained URL applets with a pinned Python interpreter."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from .definitions import MAX_DEFINITION_BYTES, Protocol, has_control, protocol_from_mapping
from .errors import DefinitionError, LaunchError, ProtocolError, RequestError
from .launchers import launch, require_macos

BUNDLE_FORMAT = 1
APP_HELPER = '''"""Entrypoint for this generated URL application."""
from pathlib import Path
import sys

resources = Path(__file__).resolve().parent
sys.path.insert(0, str(resources))
from expose_protocol.macos import bundle_main

bundle_main(resources / "manifest.json", sys.argv[1:])
'''


def bundle_identifier(scheme: str) -> str:
    digest = hashlib.sha256(scheme.encode("ascii")).hexdigest()[:24]
    return f"net.outside-labs.expose-protocol.{digest}"


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def app_script(python: Path) -> str:
    """Only the interpreter is a source literal. URLs remain quoted data."""
    return f'''property pythonPath : {_applescript_string(str(python))}

on run
    return
end run

on open location incomingURL
    my dispatchURL(incomingURL, POSIX path of (path to me))
end open location

on dispatchURL(incomingURL, bundlePath)
    set helperPath to bundlePath & "Contents/Resources/launcher.py"
    set commandText to (quoted form of pythonPath) & " -I " & (quoted form of helperPath) & " " & (quoted form of incomingURL)
    try
        do shell script commandText
    on error errorMessage
        display alert "Expose Protocol" message errorMessage as warning
    end try
end dispatchURL
'''


def run_native(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise LaunchError(f"cannot run macOS tool: {exc}") from exc
    if result.returncode:
        raise LaunchError(result.stderr.strip() or f"{Path(argv[0]).name} failed")
    return result.stdout.strip()


def load_manifest(path: Path) -> tuple[Protocol, Path]:
    """Load a generated snapshot and validate it again before each launch."""
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_DEFINITION_BYTES:
            raise DefinitionError("bundle manifest is too large")
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {"format", "python", "protocol"}
                or type(data["format"]) is not int or data["format"] != BUNDLE_FORMAT):
            raise DefinitionError("unsupported bundle manifest")
        python = data["python"]
        if not isinstance(python, str) or has_control(python) or not Path(python).is_absolute():
            raise DefinitionError("bundle interpreter must be an absolute path")
        interpreter = Path(python).resolve(strict=True)
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise DefinitionError("bundle interpreter must be an executable file")
        return protocol_from_mapping(data["protocol"], base=path.parent), interpreter
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DefinitionError(f"cannot read bundle manifest: {exc}") from exc


def bundle_main(manifest: Path, arguments: list[str]) -> None:
    """Internal GUI boundary. The URL cannot choose another definition."""
    try:
        if len(arguments) != 1:
            raise RequestError("URL application expects exactly one URL")
        protocol, interpreter = load_manifest(manifest)
        if Path(sys.executable).resolve() != interpreter:
            raise LaunchError("bundle interpreter changed; rebuild the application")
        raise SystemExit(launch(protocol, arguments[0], gui=True))
    except ProtocolError as exc:
        print(f"expose-protocol: {exc}", file=sys.stderr)
        raise SystemExit(1 if isinstance(exc, LaunchError) else 2) from None


def inspect_app(path: str | Path) -> Protocol:
    """Recognize a generated app by its layout, snapshot and matching identity."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or path.suffix != ".app":
        raise DefinitionError("expected a generated application directory without symlinks")
    if any(child.is_symlink() for child in path.rglob("*")):
        raise DefinitionError("generated application must not contain symlinks")
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if not isinstance(info, dict):
            raise DefinitionError("application plist must be a dictionary")
        protocol, _ = load_manifest(path / "Contents/Resources/manifest.json")
        identifier = bundle_identifier(protocol.scheme)
        expected_types = [{"CFBundleTypeRole": "Shell", "CFBundleURLName": identifier,
                           "CFBundleURLSchemes": [protocol.scheme]}]
        if (type(info.get("ExposeProtocolSchema")) is not int
                or info.get("ExposeProtocolSchema") != BUNDLE_FORMAT
                or info.get("CFBundleIdentifier") != identifier
                or info.get("CFBundleURLTypes") != expected_types
                or info.get("CFBundleExecutable") != "applet"
                or not (path / "Contents/MacOS/applet").is_file()
                or (path / "Contents/Resources/launcher.py").read_text(encoding="utf-8") != APP_HELPER):
            raise DefinitionError("application identity or helper does not match its manifest")
        return protocol
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        if isinstance(exc, DefinitionError):
            raise
        raise DefinitionError(f"cannot inspect generated application: {exc}") from exc


def build_app(protocol: Protocol, output: str | Path) -> Path:
    """Compile, snapshot, sign and publish a new app without replacing output.

    The bundle copies this package and pins the current interpreter. It does
    not register a URL scheme or install anything into Applications.
    """
    require_macos()
    output = Path(output)
    if output.is_symlink() or output.exists():
        raise LaunchError("application output already exists; choose a new output path")
    if output.suffix != ".app" or has_control(output.name):
        raise LaunchError("output must have a .app name without control characters")
    try:
        output = output.parent.resolve(strict=True) / output.name
        python = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix=".expose-protocol-build-", dir=output.parent) as staging:
            staging = Path(staging)
            script = staging / "handler.applescript"
            script.write_text(app_script(python), encoding="utf-8")
            app = staging / output.name
            run_native(["/usr/bin/osacompile", "-o", str(app), str(script)])
            contents = app / "Contents"
            resources = contents / "Resources"
            with (contents / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            for key in list(info):
                if key.startswith("NS") and key.endswith("UsageDescription"):
                    del info[key]
            identifier = bundle_identifier(protocol.scheme)
            info.update(CFBundleIdentifier=identifier, CFBundleName=f"Expose Protocol ({protocol.scheme})",
                        CFBundleShortVersionString="0.1.0", CFBundleVersion="1",
                        CFBundleURLTypes=[{"CFBundleTypeRole": "Shell", "CFBundleURLName": identifier,
                                           "CFBundleURLSchemes": [protocol.scheme]}],
                        ExposeProtocolSchema=BUNDLE_FORMAT, OSAAppletShowStartupScreen=False)
            if protocol.mode == "terminal":
                info["NSAppleEventsUsageDescription"] = "Open the configured program in a new Terminal window after confirmation."
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(info, stream)
            resources.mkdir(exist_ok=True)
            manifest = {"format": BUNDLE_FORMAT, "python": str(python), "protocol": protocol.to_mapping()}
            manifest_path = resources / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            manifest_path.chmod(0o600)
            (resources / "launcher.py").write_text(APP_HELPER, encoding="utf-8")
            shutil.copytree(Path(__file__).parent, resources / "expose_protocol",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            inspect_app(app)
            run_native(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app)])
            run_native(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
            if output.exists() or output.is_symlink():
                raise LaunchError("application output appeared during build; nothing was replaced")
            app.rename(output)
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException) as exc:
        if isinstance(exc, ProtocolError):
            raise
        raise LaunchError(f"cannot build application: {exc}") from exc
    return output
