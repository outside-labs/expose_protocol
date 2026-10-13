"""A fixed, bounded script for the example python://run protocol."""

import sys


def main(arguments: list[str]) -> None:
    if len(arguments) != 2:
        raise SystemExit("expected count and message")
    count = int(arguments[0])
    message = arguments[1]
    if not 1 <= count <= 5 or message not in {"hello", "goodbye"}:
        raise SystemExit("unsupported count or message")
    for _ in range(count):
        print(message)


if __name__ == "__main__":
    main(sys.argv[1:])
