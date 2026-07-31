#!/usr/bin/env python3
"""Patch the `image:` block of a gitops chart values file.

Plain-text regex edits rather than a full YAML parse/dump round-trip, so the
human-authored comments already in these files (explaining the placeholder
image, the promotion flow, etc.) survive untouched - a YAML library would
silently drop them on re-serialization. Only `patch_gitops_values.py` itself
should ever write these files, so the format it depends on (top-level
`image:` block with `repository:`/`tag:` children, once each) is safe to
assume stable.

Subcommands:
  bump      dev.yaml    - set an explicit repository+tag, optionally drop the
                          placeholder livenessProbe/readinessProbe overrides
  promote   staging/prod.yaml - copy image.repository/image.tag from one
                          values file into another, touching nothing else
  read-tag  staging/prod.yaml - print just the current image.tag from a file
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# [ \t]* rather than \s* throughout - \s also matches newlines, which would
# let a greedy trailing \s*$ swallow the blank line after this field (and
# then subn's replacement, which has no trailing newline, would delete it
# for good). Confining every pattern to a single line keeps the edit
# exactly line-scoped.
_FIELD_RE = {
    "repository": re.compile(r"^([ \t]*repository:[ \t]*)(\S*)[ \t]*$", re.MULTILINE),
    "tag": re.compile(r'^([ \t]*tag:[ \t]*)"?([^"\n]*?)"?[ \t]*$', re.MULTILINE),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _get_field(text: str, field: str) -> str:
    match = _FIELD_RE[field].search(text)
    if not match:
        raise SystemExit(f"could not find '{field}:' in the image block")
    return match.group(2)


def _set_field(text: str, field: str, value: str) -> str:
    quoted = f'"{value}"' if field == "tag" else value
    pattern = _FIELD_RE[field]
    new_text, count = pattern.subn(lambda m: f"{m.group(1)}{quoted}", text, count=1)
    if count == 0:
        raise SystemExit(f"could not find '{field}:' in the image block to replace")
    return new_text


def _remove_block(text: str, key: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}:\n(?:^[ \t]+.*\n?)*")
    return pattern.sub("", text, count=1)


def cmd_bump(args: argparse.Namespace) -> None:
    path = Path(args.file)
    text = _read(path)
    text = _set_field(text, "repository", args.repository)
    text = _set_field(text, "tag", args.tag)
    if args.reset_probes:
        text = _remove_block(text, "livenessProbe")
        text = _remove_block(text, "readinessProbe")
        # the blank lines that used to separate these blocks from their
        # neighbors are now adjacent to each other - collapse to one.
        text = re.sub(r"\n{3,}", "\n\n", text)
    path.write_text(text, encoding="utf-8")
    print(f"bumped {path} -> {args.repository}:{args.tag}")


def cmd_promote(args: argparse.Namespace) -> None:
    source_text = _read(Path(args.source))
    repository = _get_field(source_text, "repository")
    tag = _get_field(source_text, "tag")

    target_path = Path(args.target)
    target_text = _read(target_path)
    target_text = _set_field(target_text, "repository", repository)
    target_text = _set_field(target_text, "tag", tag)
    if args.reset_probes:
        target_text = _remove_block(target_text, "livenessProbe")
        target_text = _remove_block(target_text, "readinessProbe")
        target_text = re.sub(r"\n{3,}", "\n\n", target_text)
    target_path.write_text(target_text, encoding="utf-8")
    print(f"promoted {repository}:{tag} from {args.source} -> {args.target}")


def cmd_read_tag(args: argparse.Namespace) -> None:
    text = _read(Path(args.file))
    print(_get_field(text, "tag"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bump = subparsers.add_parser("bump", help="set an explicit repository+tag")
    bump.add_argument("--file", required=True)
    bump.add_argument("--repository", required=True)
    bump.add_argument("--tag", required=True)
    bump.add_argument("--reset-probes", action="store_true")
    bump.set_defaults(func=cmd_bump)

    promote = subparsers.add_parser("promote", help="copy image.repository/tag between files")
    promote.add_argument("--source", required=True)
    promote.add_argument("--target", required=True)
    promote.add_argument("--reset-probes", action="store_true")
    promote.set_defaults(func=cmd_promote)

    read_tag = subparsers.add_parser("read-tag", help="print the current image.tag")
    read_tag.add_argument("--file", required=True)
    read_tag.set_defaults(func=cmd_read_tag)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
