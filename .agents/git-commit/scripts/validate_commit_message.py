#!/usr/bin/env python3
"""Create and validate Git commit message files"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

CONTENT_ERROR = 1
INFRASTRUCTURE_ERROR = 3

MAX_LINE_LENGTH = 68
TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
SIGNED_OFF_BY_PATTERN = re.compile(r"^\s*Signed-off-by\s*:", re.IGNORECASE)
AUTHOR_ATTRIBUTION_PATTERN = re.compile(
    r"^\s*(?:authors?|authored-by|co-authors?|co-authored-by)\s*:",
    re.IGNORECASE,
)

TITLE_PATTERN = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9-]*)"
    r"(?:\((?P<scope>[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*)\))?"
    r"(?P<breaking>!)?: (?P<subject>\S(?:.*\S)?)$"
)

STANDARD_TYPES = frozenset(
    {
        "Build",
        "Chore",
        "CI",
        "Docs",
        "Feat",
        "Fix",
        "Perf",
        "Refactor",
        "Revert",
        "Style",
        "Test",
    }
)


def fail(message: str, exit_code: int = CONTENT_ERROR) -> None:
    label = "Validator error"
    if exit_code == CONTENT_ERROR:
        label = "Invalid commit message"

    print(f"{label}: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def resolve_message_file(message_path: Path) -> Path:
    try:
        resolved_path = message_path.resolve(strict=True)
    except OSError as error:
        fail(
            f"cannot resolve file {message_path}: {error}",
            INFRASTRUCTURE_ERROR,
        )

    if not resolved_path.is_file():
        fail(f"path is not a regular file: {message_path}")
    return resolved_path


def read_lines(message_path: Path) -> list[str]:
    try:
        with message_path.open("r", encoding="utf-8", newline=None) as stream:
            message = stream.read()
    except UnicodeDecodeError:
        fail("file must contain valid UTF-8 text")
    except OSError as error:
        fail(
            f"cannot read file {message_path}: {error}",
            INFRASTRUCTURE_ERROR,
        )

    if "\x00" in message:
        fail("file must not contain NUL characters")
    elif message.endswith("\n"):
        message = message[:-1]
    elif not message:
        fail("file is empty")

    lines = message.split("\n")
    if lines[-1] == "":
        fail("file must not end with a blank line")
    return lines


def validate_message(message_path: Path, allowed_types: frozenset[str]) -> None:
    resolved_path = resolve_message_file(message_path)
    lines = read_lines(resolved_path)

    title_match = TITLE_PATTERN.fullmatch(lines[0])
    if title_match is None:
        fail("title does not match '<Type>(<Scope>)!: <Subject>'")

    message_type = title_match.group("type")
    if message_type not in allowed_types:
        fail(
            f"type is not allowed: `{message_type}`; use --allow-type for a "
            "repository-defined type"
        )

    for line_number, line in enumerate(lines, start=1):
        if line != line.rstrip():
            fail(f"line {line_number} has trailing whitespace")
        elif SIGNED_OFF_BY_PATTERN.match(line):
            fail("omit `Signed-off-by`; Git must generate it")
        elif AUTHOR_ATTRIBUTION_PATTERN.match(line):
            fail("omit author and co-author trailers; Git records authorship")
        elif line and len(line) > MAX_LINE_LENGTH:
            fail(
                f"line {line_number} is {len(line)} characters; "
                f"maximum is {MAX_LINE_LENGTH}"
            )

    if len(lines) == 1:
        return
    elif lines[1] != "":
        fail("line 2 must be empty when authored content follows the title")
    elif len(lines) == 2 or lines[2] == "":
        fail("authored content must start after exactly one empty line")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a Git commit message file.",
    )
    parser.add_argument(
        "--allow-type",
        action="append",
        default=[],
        metavar="TYPE",
        help="allow a repository-defined Conventional Commit type",
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="create a private message file in the platform temporary directory",
    )
    parser.add_argument("message_file", nargs="?", type=Path)
    arguments = parser.parse_args()

    if arguments.create:
        if arguments.message_file is not None:
            parser.error("--create does not accept a message file")
        if arguments.allow_type:
            parser.error("--create does not accept --allow-type")
    elif arguments.message_file is None:
        parser.error("message_file is required unless --create is used")

    for allowed_type in arguments.allow_type:
        if not TYPE_PATTERN.fullmatch(allowed_type):
            parser.error(f"invalid --allow-type value: {allowed_type}")
        if not allowed_type[0].isupper():
            parser.error(f"--allow-type must be title-cased: {allowed_type}")
    return arguments


def create_message_file() -> Path:
    try:
        descriptor, message_path = tempfile.mkstemp(
            prefix="git-commit-message-",
            suffix=".txt",
            text=True,
        )
        os.close(descriptor)
    except OSError as error:
        fail(f"cannot create temporary message file: {error}", INFRASTRUCTURE_ERROR)
    return Path(message_path)


def main() -> None:
    arguments = parse_arguments()
    if arguments.create:
        print(create_message_file())
        return

    allowed_types = STANDARD_TYPES.union(arguments.allow_type)
    validate_message(arguments.message_file, allowed_types)
    print(f"Valid commit message: {arguments.message_file}")


if __name__ == "__main__":
    main()
