"""Persistent name-to-structure cache. Modified for Zheng Lab, 2026-09-27.

Adapted from StarLiu714/SMILESearcher under GPL-3.0; see LICENSE.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .chemistry import canonicalize_smiles, name_key, normalize_name


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path, data):
    """Replace a complete file only after its temporary copy is flushed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_cache_json(path, cache):
    atomic_write(path, json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True,
                                 allow_nan=False).encode("utf-8"))


def load_cache_json(path, *, on_conflict="error"):
    """Read legacy string values or rich records without hiding corrupt files."""
    if on_conflict not in {"error", "ambiguous"}:
        raise ValueError("on_conflict must be error or ambiguous.")
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid cache JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("Cache must be a JSON object keyed by chemical name.")
    result = {}
    for name, value in data.items():
        if name == "_schema":
            continue
        key = name_key(name)
        if not key:
            continue
        if isinstance(value, str):
            record = {"name": normalize_name(name), "smiles": value,
                      "source": "legacy_cache", "status": "found" if value else "not_found"}
        elif isinstance(value, dict):
            record = dict(value)
            record.setdefault("name", record.get("name_raw", normalize_name(name)))
            record["smiles"] = record.get("smiles") or record.get("SMILES") or ""
            record.setdefault("source", "cache")
            record.setdefault("status", "found" if record["smiles"] else "not_found")
        else:
            raise ValueError(f"Unsupported cache entry for {name!r}.")
        if record["status"] == "found" and not canonicalize_smiles(record["smiles"]):
            record["status"] = "invalid"
            record["note"] = "Cached structure failed RDKit validation."
        previous = result.get(key)
        if previous and previous.get("source") == "cache_conflict":
            previous["trace"].append({"name": name, "smiles": record.get("smiles", ""),
                                      "source": record.get("source", "cache")})
            continue
        if previous and get_cached_smiles({key: previous}, name) and get_cached_smiles({key: record}, name):
            if canonicalize_smiles(previous["smiles"]) != canonicalize_smiles(record["smiles"]):
                if on_conflict == "error":
                    raise ValueError(f"Conflicting cache structures for normalized name {key!r}.")
                result[key] = {"name": normalize_name(name), "smiles": "", "status": "ambiguous",
                               "source": "cache_conflict", "note": "Conflicting cached structures require review.",
                               "trace": [{"name": r.get("name", name), "smiles": r["smiles"],
                                          "source": r.get("source", "cache")} for r in (previous, record)]}
                continue
        if previous and previous.get("status") == "found" and record.get("status") != "found":
            continue
        result[key] = record
    return result


def get_cached_smiles(cache, name):
    record = cache.get(name_key(name), {})
    if isinstance(record, str):
        return canonicalize_smiles(record) or ""
    if record.get("status", "found") != "found":
        return ""
    return canonicalize_smiles(record.get("smiles", record.get("SMILES", ""))) or ""
