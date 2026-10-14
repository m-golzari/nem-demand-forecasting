"""Small provenance helpers: hashing, UTC timestamps, JSON manifests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    """Current UTC time, second precision, ISO 8601 with ``Z``."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_new(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to a path that must not already exist (manifests are never overwritten)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def write_json_atomic(path: Path, payload: dict[str, Any], *, exclusive: bool = True) -> None:
    """Write JSON via a temporary file and ``os.replace`` so readers never see a partial file.

    With ``exclusive=True`` an existing target is never replaced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise FileExistsError(f"{path} already exists; manifests are never overwritten")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if exclusive and path.exists():
        tmp.unlink()
        raise FileExistsError(f"{path} already exists; manifests are never overwritten")
    tmp.replace(path)
