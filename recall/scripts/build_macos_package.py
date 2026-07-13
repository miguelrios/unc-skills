#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import stat
import tarfile
from pathlib import Path


EPOCH = 0


def files(source_root: Path) -> dict[str, bytes]:
    selected: dict[str, bytes] = {}
    mappings = {
        source_root / "client" / "mac.py": "lib/client/mac.py",
        source_root / "client" / "cli.py": "lib/client/cli.py",
        source_root / "client" / "__init__.py": "lib/client/__init__.py",
        source_root / "collector" / "collector.py": "lib/collector/collector.py",
        source_root / "collector" / "__init__.py": "lib/collector/__init__.py",
        source_root / "client" / "macos" / "recall-brain": "bin/recall-brain",
        source_root / "client" / "macos" / "install.sh": "install.sh",
        source_root / "client" / "macos" / "uninstall.sh": "uninstall.sh",
    }
    for source, destination in mappings.items():
        selected[destination] = source.read_bytes()
    manifest = {
        "format": "recall-macos-v1",
        "version": 1,
        "files": [
            {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in sorted(selected.items())
        ],
    }
    selected["MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return selected


def build(source_root: Path, output: Path) -> None:
    payloads = files(source_root)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
        root = tarfile.TarInfo("recall-brain-macos")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.mtime = EPOCH
        archive.addfile(root)
        directories = sorted({str(Path(path).parent) for path in payloads if str(Path(path).parent) != "."})
        for directory in directories:
            info = tarfile.TarInfo("recall-brain-macos/" + directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = EPOCH
            archive.addfile(info)
        for path, data in sorted(payloads.items()):
            info = tarfile.TarInfo("recall-brain-macos/" + path)
            info.size = len(data)
            info.mode = 0o755 if path in {"bin/recall-brain", "install.sh", "uninstall.sh"} else 0o644
            info.mtime = EPOCH
            archive.addfile(info, io.BytesIO(data))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=EPOCH) as compressed:
            compressed.write(raw.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build byte-reproducible Recall Brain macOS bundle")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_root.resolve(), args.output)
    print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
