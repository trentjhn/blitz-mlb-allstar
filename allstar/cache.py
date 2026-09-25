"""Storing pages in data/raw/, keeping rejected responses aside, and checking the cache is intact.

Each page is stored exactly as received, under a readable path derived from its URL, and
listed in data/raw/manifest.json with its fetch time, size, and SHA-256.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from allstar import config
from allstar.pages import page_for

MANIFEST_NAME = "manifest.json"
IGNORED_NAMES = {".DS_Store"}
PARTIAL_SUFFIX = ".partial"
# Response headers worth keeping with a quarantined body: they explain a redirect or a block.
KEPT_HEADERS = ("Location", "Retry-After", "Content-Type", "Content-Length")


def manifest_entry(path: str, body: bytes, fetched_at: str) -> dict:
    return {
        "path": path,
        "fetched_at": fetched_at,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def load_manifest(raw_dir: Path) -> dict[str, dict]:
    path = raw_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not all(isinstance(e, dict) for e in manifest.values()):
        raise ValueError(f"{path} is not a manifest: expected an object of URL entries")
    return manifest


def save_manifest(raw_dir: Path, manifest: dict[str, dict]) -> None:
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    write_atomic(raw_dir / MANIFEST_NAME, text.encode())


def stage(path: Path, data: bytes) -> Path:
    """Write data to a synced temp file beside path, for the caller to rename into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=PARTIAL_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a synced temp file and a rename, so no crash leaves half a file behind."""
    tmp = stage(path, data)
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def keep_rejected(
    quarantine_dir: Path,
    url: str,
    reason: str,
    status: int | None,
    headers: dict,
    body: bytes,
    at: str,
) -> Path:
    """Store a refused or rejected response, with why and when, outside the cache.

    Each record gets its own time-stamped name, so a later one never overwrites earlier evidence.
    """
    base = quarantine_dir / f"{page_for(url).path}.{re.sub(r'[^0-9TZ]', '', at)}"
    kept, n = base, 1
    while kept.exists():
        n += 1
        kept = base.with_name(f"{base.name}-{n}")
    wanted = {name.lower() for name in KEPT_HEADERS}
    details = {
        "url": url,
        "reason": reason,
        "status": status,
        "headers": {k: v for k, v in headers.items() if k.lower() in wanted},
        "at": at,
    }
    write_atomic(kept, body)
    write_atomic(kept.with_name(kept.name + ".json"), json.dumps(details, indent=2).encode())
    return kept


def remove_partial_writes(root: Path) -> list[Path]:
    """Delete temp files that an interrupted write_atomic left behind under root."""
    leftovers = [
        p
        for p in root.rglob(f".*{PARTIAL_SUFFIX}")
        if p.is_file() and p.name.startswith(".") and p.name.endswith(PARTIAL_SUFFIX)
    ]
    for path in leftovers:
        path.unlink()
    return leftovers


def check_cache(raw_dir: Path = config.RAW_DIR) -> list[str]:
    """Compare data/raw/ with its manifest. Returns one line per problem; empty means clean."""
    if not (raw_dir / MANIFEST_NAME).exists():
        return [f"{raw_dir / MANIFEST_NAME}: missing"]
    problems = []
    listed = set()
    for url, entry in sorted(load_manifest(raw_dir).items()):
        if not {"path", "fetched_at", "bytes", "sha256"} <= entry.keys():
            problems.append(f"{url}: manifest entry is missing fields")
            continue
        rel = entry["path"]
        listed.add(rel)
        try:
            expected = page_for(url).path
        except ValueError:
            problems.append(f"{url}: no cache rule for this URL")
            continue
        if rel != expected:
            problems.append(f"{url}: manifest path {rel} should be {expected}")
        path = raw_dir / rel
        # The name check catches a case mismatch that a case-insensitive disk would hide.
        if not path.is_file() or path.name not in os.listdir(path.parent):
            problems.append(f"{rel}: missing, or its name differs in case")
            continue
        body = path.read_bytes()
        if len(body) != entry["bytes"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
            problems.append(f"{rel}: contents differ from the manifest")
    for path in sorted(raw_dir.rglob("*")):
        rel = path.relative_to(raw_dir).as_posix()
        is_page = path.is_file() and path.name not in IGNORED_NAMES and rel != MANIFEST_NAME
        if is_page and rel not in listed:
            problems.append(f"{rel}: not in the manifest")
    return problems
