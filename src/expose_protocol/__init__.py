"""Expose locally configured programs through validated URL schemes."""

from .definitions import Parameter, Protocol, load_protocol
from .errors import DefinitionError, LaunchError, ProtocolError, RequestError
from .launchers import launch
from .macos import build_app, inspect_app
from .lifecycle import install, uninstall
from .services import DefaultHandler, get_handler
from .urls import Invocation, make_url, parse_url, prepare

__all__ = [
    "Parameter", "Protocol", "Invocation", "load_protocol", "make_url", "parse_url",
    "prepare", "launch", "build_app", "inspect_app", "install", "uninstall", "DefaultHandler",
    "get_handler", "DefinitionError", "RequestError",
    "LaunchError", "ProtocolError", "main",
]


def main() -> None:
    from .cli import main as cli_main
    cli_main()
