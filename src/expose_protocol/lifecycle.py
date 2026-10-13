"""Per-user installation with managed identities and recoverable updates."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile

from .definitions import Protocol, has_control, validate_scheme
from .errors import DefinitionError, LaunchError, ProtocolError
from .launchers import require_macos
from .macos import BUNDLE_FORMAT, app_scheme, build_app, bundle_identifier, run_native
from .services import get_handler, register_app, set_handler, unregister_app


def application_directory(directory: str | Path | None = None, *, create: bool = False) -> Path:
    path = Path(directory) if directory is not None else Path.home() / "Applications/Expose Protocol"
    path = path.expanduser().absolute()
    if has_control(str(path)) or any(part.is_symlink() for part in (path, *path.parents)):
        raise LaunchError("application directory must not contain symlinks or control characters")
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            raise LaunchError("application directory must exist and belong to the current user")
        return path.resolve(strict=True)
    except OSError as exc:
        raise LaunchError(f"cannot use application directory: {exc}") from exc


@contextmanager
def _lock(directory: Path, scheme: str):
    path = directory / f".{scheme}.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise LaunchError("another operation holds this scheme's lock; check for an interrupted operation") from exc
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def _managed(app: Path, receipt: Path, scheme: str) -> dict:
    if app.is_symlink() or receipt.is_symlink():
        raise DefinitionError("managed app and receipt must not be symlinks")
    if app_scheme(app) != scheme:
        raise DefinitionError("managed application scheme does not match its path")
    try:
        if receipt.stat().st_size > 4096:
            raise DefinitionError("installation receipt is too large")
        data = json.loads(receipt.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {"format", "scheme", "bundle_id", "previous_handler"}
                or type(data["format"]) is not int or data["format"] != BUNDLE_FORMAT
                or data["scheme"] != scheme or data["bundle_id"] != bundle_identifier(scheme)):
            raise DefinitionError("installation receipt does not match the application")
        previous = data["previous_handler"]
        if previous is not None and (not isinstance(previous, str) or not previous
                                     or len(previous) > 255 or has_control(previous)
                                     or previous == data["bundle_id"]):
            raise DefinitionError("invalid previous handler in installation receipt")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DefinitionError(f"cannot read installation receipt: {exc}") from exc
    run_native(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    return data


def install(protocol: Protocol, *, directory: str | Path | None = None,
            replace: bool = False, takeover: bool = False) -> Path:
    """Build and register an app; replacement and foreign takeover are explicit."""
    require_macos()
    directory = application_directory(directory, create=True)
    scheme = protocol.scheme
    app, receipt = directory / f"{scheme}.app", directory / f"{scheme}.json"
    identifier = bundle_identifier(scheme)
    with _lock(directory, scheme):
        exists = app.exists() or app.is_symlink()
        if exists and not replace:
            raise LaunchError("application is already installed; use --replace to update it")
        if receipt.is_symlink() or (not exists and receipt.exists()):
            raise DefinitionError("orphaned or symlinked installation receipt; inspect it before proceeding")
        old_receipt = _managed(app, receipt, scheme) if exists else None
        original = get_handler(scheme)
        foreign = (original.bundle_id is not None or original.path is not None) and (original.bundle_id != identifier or original.path != app)
        if foreign and not takeover:
            raise LaunchError(f"scheme already has a handler ({original.bundle_id or original.path}); use --takeover to explicitly replace it")
        previous = original.bundle_id if foreign else (old_receipt["previous_handler"] if old_receipt else original.bundle_id)
        if previous == identifier:
            previous = None
        staging = Path(tempfile.mkdtemp(prefix=f".expose-protocol-{scheme}-", dir=directory))
        old_app, old_metadata = staging / "previous.app", staging / "previous.json"
        deployed = False
        registration_attempted = False
        cleanup = True
        try:
            new_app = build_app(protocol, staging / f"{scheme}.app")
            new_receipt = staging / "receipt.json"
            new_receipt.write_text(json.dumps({"format": BUNDLE_FORMAT, "scheme": scheme,
                                               "bundle_id": identifier, "previous_handler": previous}) + "\n", encoding="utf-8")
            new_receipt.chmod(0o600)
            if exists:
                app.rename(old_app)
                receipt.rename(old_metadata)
            if app.exists() or app.is_symlink() or receipt.exists() or receipt.is_symlink():
                raise LaunchError("installation paths changed during build; nothing was replaced")
            new_app.rename(app)
            deployed = True
            new_receipt.rename(receipt)
            registration_attempted = True
            register_app(app)
            set_handler(scheme, identifier)
            selected = get_handler(scheme)
            if selected.bundle_id != identifier or selected.path != app:
                raise LaunchError("macOS did not select this application; check for another copy of the same handler")
        except (OSError, ProtocolError) as exc:
            failures = []
            def recover(operation):
                try:
                    operation()
                except (OSError, ProtocolError) as error:
                    failures.append(str(error))
            if registration_attempted:
                recover(lambda: unregister_app(app))
            if deployed:
                recover(lambda: shutil.rmtree(app))
                if receipt.exists() and not receipt.is_symlink():
                    recover(receipt.unlink)
            if old_app.exists():
                recover(lambda: old_app.rename(app))
            if old_metadata.exists():
                recover(lambda: old_metadata.rename(receipt))
            if registration_attempted and exists and app.exists():
                recover(lambda: register_app(app))
            if registration_attempted and original.bundle_id is not None:
                recover(lambda: set_handler(scheme, original.bundle_id))
            if failures:
                cleanup = False
                raise LaunchError(f"installation failed: {exc}; recovery needs attention at {staging}: {'; '.join(failures)}") from exc
            raise LaunchError(f"installation failed; previous state restored: {exc}") from exc
        finally:
            if cleanup:
                shutil.rmtree(staging)
        return app


def uninstall(scheme: str, *, directory: str | Path | None = None) -> Path:
    """Unregister and remove only a recognized, signed managed application."""
    require_macos()
    validate_scheme(scheme)
    directory = application_directory(directory)
    app, receipt = directory / f"{scheme}.app", directory / f"{scheme}.json"
    with _lock(directory, scheme):
        metadata = _managed(app, receipt, scheme)
        original = get_handler(scheme)
        unregister_app(app)
        try:
            if original.bundle_id == metadata["bundle_id"] and metadata["previous_handler"] is not None:
                set_handler(scheme, metadata["previous_handler"])
            shutil.rmtree(app)
            receipt.unlink()
        except (OSError, ProtocolError) as exc:
            # Registration can be restored only while the intact app remains.
            try:
                _managed(app, receipt, scheme)
                register_app(app)
                if original.bundle_id is not None:
                    set_handler(scheme, original.bundle_id)
            except (OSError, ProtocolError) as recovery:
                raise LaunchError(f"removal incomplete at {app}: {exc}; recovery failed: {recovery}") from exc
            raise LaunchError(f"removal failed; registration restored: {exc}") from exc
        return app
