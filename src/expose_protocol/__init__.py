"""Expose locally configured programs through validated URL schemes."""

from .definitions import Parameter, Protocol, load_protocol
from .errors import DefinitionError, LaunchError, ProtocolError, RequestError
from .launchers import launch
from .urls import Invocation, make_url, parse_url, prepare

__all__ = [
    "Parameter", "Protocol", "Invocation", "load_protocol", "make_url", "parse_url",
    "prepare", "launch", "DefinitionError", "RequestError", "LaunchError", "ProtocolError", "main",
]


def main() -> None:
    from .cli import main as cli_main
    cli_main()
