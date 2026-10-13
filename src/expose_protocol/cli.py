"""The command line boundary; library functions remain independently usable."""

import argparse
import json
import sys

from .definitions import has_control, load_protocol
from .errors import LaunchError, ProtocolError, RequestError
from .launchers import launch
from .lifecycle import install, uninstall
from .macos import build_app
from .urls import make_url, prepare


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="expose-protocol", description="Expose local programs through validated macOS URL schemes.")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("validate", "Validate a local TOML definition."),
                            ("inspect", "Print a resolved invocation without launching."),
                            ("link", "Build a percent-encoded URL or Markdown link."),
                            ("run", "Confirm and launch a validated URL."),
                            ("build", "Compile a new macOS URL application."),
                            ("install", "Build, install and register a per-user URL application.")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", required=True, help="Explicit trusted TOML definition.")
        if name == "build":
            command.add_argument("--output", required=True, help="New .app path; parent must exist.")
        if name == "install":
            command.add_argument("--apps-dir", help="Owned application directory (default: ~/Applications/Expose Protocol).")
            command.add_argument("--replace", action="store_true", help="Explicitly update an existing managed app.")
            command.add_argument("--takeover", action="store_true", help="Explicitly replace a different current scheme handler.")
        if name in {"inspect", "run"}:
            command.add_argument("url")
        if name == "run":
            command.add_argument("--dry-run", action="store_true", help="Print the resolved invocation without launching.")
            command.add_argument("--yes", action="store_true", help="Explicitly bypass CLI confirmation.")
        if name == "link":
            command.add_argument("values", nargs="*", metavar="NAME=VALUE")
            command.add_argument("--markdown", metavar="LABEL")
    remove = commands.add_parser("uninstall", help="Unregister and remove a managed URL application.")
    remove.add_argument("scheme")
    remove.add_argument("--apps-dir", help="Application directory used for installation.")
    return root


def _link_values(tokens: list[str]) -> dict[str, str]:
    values = {}
    for token in tokens:
        name, separator, value = token.partition("=")
        if not separator or name in values:
            raise RequestError("link fields must be unique name=value arguments")
        values[name] = value
    return values


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.command == "uninstall":
            print(uninstall(args.scheme, directory=args.apps_dir))
            return
        protocol = load_protocol(args.config)
        if args.command == "validate":
            print(json.dumps(protocol.to_mapping(), indent=2, ensure_ascii=True))
        elif args.command == "inspect":
            print(json.dumps(prepare(protocol, args.url).to_mapping(), indent=2, ensure_ascii=True))
        elif args.command == "run":
            if args.dry_run:
                print(json.dumps(prepare(protocol, args.url).to_mapping(), indent=2, ensure_ascii=True))
            else:
                raise SystemExit(launch(protocol, args.url, yes=args.yes))
        elif args.command == "build":
            print(build_app(protocol, args.output))
        elif args.command == "install":
            print(install(protocol, directory=args.apps_dir, replace=args.replace, takeover=args.takeover))
        elif args.command == "link":
            url = make_url(protocol, _link_values(args.values))
            if args.markdown is not None:
                if not args.markdown or has_control(args.markdown):
                    raise RequestError("Markdown label must be nonempty text without control characters")
                label = args.markdown.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                print(f"[{label}](<{url}>)")
            else:
                print(url)
    except ProtocolError as exc:
        print(f"expose-protocol: {exc}", file=sys.stderr)
        raise SystemExit(1 if isinstance(exc, LaunchError) else 2) from None
