"""Errors at definition, request, and operating-system boundaries."""


class ProtocolError(ValueError):
    """An expected failure suitable for a concise command line error."""


class DefinitionError(ProtocolError):
    """A local protocol definition is invalid."""


class RequestError(ProtocolError):
    """A URL or parameter value violates the protocol definition."""


class LaunchError(ProtocolError):
    """The configured program or macOS integration could not run."""
