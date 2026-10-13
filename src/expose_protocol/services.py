"""Small compatibility boundary for macOS Launch Services.

The handler APIs are deprecated by Apple but present on supported macOS. Keep
their ctypes bindings here so a newer backend can replace them independently.
"""

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .definitions import validate_scheme
from .errors import LaunchError
from .launchers import require_macos
from .macos import run_native

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
_UTF8 = 0x08000100


@dataclass(frozen=True)
class DefaultHandler:
    bundle_id: str | None = None
    path: Path | None = None


class _Services:
    def __init__(self):
        try:
            self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.ls = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
            functions = (
                (self.cf.CFStringCreateWithCString, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_void_p),
                (self.cf.CFRelease, [ctypes.c_void_p], None),
                (self.cf.CFStringGetLength, [ctypes.c_void_p], ctypes.c_long),
                (self.cf.CFStringGetMaximumSizeForEncoding, [ctypes.c_long, ctypes.c_uint32], ctypes.c_long),
                (self.cf.CFStringGetCString, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32], ctypes.c_bool),
                (self.cf.CFURLCreateWithString, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
                (self.cf.CFURLCopyFileSystemPath, [ctypes.c_void_p, ctypes.c_int], ctypes.c_void_p),
                (self.ls.LSCopyDefaultHandlerForURLScheme, [ctypes.c_void_p], ctypes.c_void_p),
                (self.ls.LSCopyDefaultApplicationURLForURL, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_void_p),
                (self.ls.LSSetDefaultHandlerForURLScheme, [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int32),
            )
            for function, args, result in functions:
                function.argtypes, function.restype = args, result
        except (OSError, AttributeError) as exc:
            raise LaunchError(f"macOS handler APIs are unavailable: {exc}") from exc

    @contextmanager
    def string(self, value: str):
        reference = self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), _UTF8)
        if not reference:
            raise LaunchError("cannot create a macOS string")
        try:
            yield reference
        finally:
            self.cf.CFRelease(reference)

    def copy_text(self, reference) -> str | None:
        if not reference:
            return None
        try:
            length = self.cf.CFStringGetLength(reference)
            size = self.cf.CFStringGetMaximumSizeForEncoding(length, _UTF8) + 1
            if not 1 <= size <= 65536:
                raise LaunchError("macOS returned an invalid handler string")
            buffer = ctypes.create_string_buffer(size)
            if not self.cf.CFStringGetCString(reference, buffer, size, _UTF8):
                raise LaunchError("cannot decode macOS handler string")
            return buffer.value.decode("utf-8")
        finally:
            self.cf.CFRelease(reference)

    def handler(self, scheme: str) -> DefaultHandler:
        with self.string(scheme) as reference:
            identifier = self.copy_text(self.ls.LSCopyDefaultHandlerForURLScheme(reference))
        path = None
        with self.string(f"{scheme}://open") as reference:
            url = self.cf.CFURLCreateWithString(None, reference, None)
        if not url:
            raise LaunchError("cannot create a macOS URL")
        try:
            application = self.ls.LSCopyDefaultApplicationURLForURL(url, 0xFFFFFFFF, None)
            if application:
                try:
                    text = self.copy_text(self.cf.CFURLCopyFileSystemPath(application, 0))
                    path = Path(text).resolve() if text else None
                finally:
                    self.cf.CFRelease(application)
        finally:
            self.cf.CFRelease(url)
        return DefaultHandler(bundle_id=identifier, path=path)

    def set_handler(self, scheme: str, identifier: str) -> None:
        with self.string(scheme) as name, self.string(identifier) as application:
            status = self.ls.LSSetDefaultHandlerForURLScheme(name, application)
        if status:
            raise LaunchError(f"cannot change default handler (Launch Services status {status})")


@lru_cache(maxsize=1)
def _services() -> _Services:
    return _Services()


def get_handler(scheme: str) -> DefaultHandler:
    require_macos()
    validate_scheme(scheme)
    return _services().handler(scheme)


def set_handler(scheme: str, identifier: str) -> None:
    require_macos()
    validate_scheme(scheme)
    _services().set_handler(scheme, identifier)


def register_app(app: Path) -> None:
    require_macos()
    run_native([LSREGISTER, "-f", str(app)])


def unregister_app(app: Path) -> None:
    require_macos()
    run_native([LSREGISTER, "-u", str(app)])
