#!/usr/bin/env python3
"""Offline check of vendored upstream bytes against the reviewed lock manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def verify(root, lock):
    root = Path(root).resolve()
    data = json.loads(Path(lock).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("files"), list) or not data["files"]:
        raise ValueError("invalid or empty upstream lock manifest")
    seen = set()
    for record in data["files"]:
        rel = Path(record["path"])
        if rel.is_absolute() or ".." in rel.parts or record["path"] in seen:
            raise ValueError(f"invalid/duplicate upstream path: {rel}")
        seen.add(record["path"])
        target = root / rel
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError(f"upstream path escapes root or is a symlink: {rel}")
        if not target.is_file():
            raise ValueError(f"missing upstream file: {rel}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != record["sha256"]:
            raise ValueError(f"upstream checksum mismatch: {rel}")
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    lock = args.lock or args.root / ".agents/quality/upstream-lock.json"
    try:
        count = verify(args.root, lock)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {count} upstream files match reviewed lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
