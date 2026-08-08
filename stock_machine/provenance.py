"""Immutable raw storage. Every API response is saved verbatim inside a
provenance envelope; existing files are never overwritten — new content gets a
new version file keyed by its content hash."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RAW_DIR


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def save_raw(
    provider: str,
    path_parts: list[str],
    payload: Any,
    source_url: str,
    request_parameters: dict | None = None,
) -> Path:
    """Persist one raw record. Returns the path of the stored (or pre-existing
    identical) version."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    content_hash = _sha256(body)
    target_dir = RAW_DIR / provider
    for part in path_parts:
        target_dir = target_dir / part
    target_dir.mkdir(parents=True, exist_ok=True)

    short = content_hash.split(":")[1][:16]
    existing = list(target_dir.glob(f"*__{short}.json"))
    if existing:
        return existing[0]

    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    envelope = {
        "provider": provider,
        "retrieved_at": retrieved_at,
        "source_url": source_url,
        "source_url_hash": _sha256(source_url.encode()),
        "content_hash": content_hash,
        "request_parameters": request_parameters or {},
        "original_payload": payload,
    }
    stamp = retrieved_at.replace(":", "").replace("-", "").replace("+0000", "Z")
    path = target_dir / f"{stamp}__{short}.json"
    path.write_text(json.dumps(envelope, indent=1))
    return path


def load_raw(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def latest_raw(provider: str, path_parts: list[str]) -> Path | None:
    """Most recently retrieved version of a raw record, or None."""
    d = RAW_DIR / provider
    for part in path_parts:
        d = d / part
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    return files[-1] if files else None
