"""
JSON cache I/O utilities for name -> SMILES resolution.

Design goals:
- Backward compatible with legacy JSON mapping formats:
    { "name": "SMILES", ... }
    { "name": { "smiles": "...", "source": "...", ... }, ... }
- Canonical internal format in-memory:
    cache[name_norm] = {
        "name_raw": str,
        "smiles": str,
        "source": str,
        "status": str,      # found / not_found / filtered / blocked / error
        "note": str,
        "confidence": float,
        "trace": list,
        "updated_at": str,  # ISO8601 UTC with Z
    }
- Deterministic save order, atomic write.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, List


CacheRecord = Dict[str, Any]
CacheDict = Dict[str, CacheRecord]  # keyed by name_norm (lowercased)


def normalize_name(x: Any) -> str:
    """
    Minimal normalization for cache keys:
    - cast to str, strip
    - collapse whitespace
    """
    if x is None:
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def now_iso() -> str:
    """UTC ISO-8601 timestamp with trailing 'Z'."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _to_record(name_raw: str, value: Any) -> CacheRecord:
    """
    Convert a cache value to a unified record dict.

    Supports:
      - "SMILES" string
      - {"smiles": "...", "source": "...", ...} dict
    """
    name_raw = normalize_name(name_raw)

    if isinstance(value, str):
        sm = value.strip()
        return {
            "name_raw": name_raw,
            "smiles": sm,
            "source": "legacy_json",
            "status": "found" if sm else "not_found",
            "note": "Loaded from legacy json mapping (string value).",
            "confidence": 0.0,
            "trace": [],
            "updated_at": "",
        }

    if isinstance(value, dict):
        sm = (value.get("smiles") or value.get("SMILES") or "").strip()
        trace_val = value.get("trace", [])
        trace = trace_val if isinstance(trace_val, list) else []

        conf_val = value.get("confidence", 0.0)
        try:
            conf = float(conf_val or 0.0)
        except Exception:
            conf = 0.0

        status = (value.get("status") or "").strip()
        if not status:
            status = "found" if sm else "not_found"

        return {
            "name_raw": normalize_name(value.get("name_raw", name_raw)),
            "smiles": sm,
            "source": str(value.get("source", "") or ""),
            "status": status,
            "note": str(value.get("note", "") or ""),
            "confidence": conf,
            "trace": trace,
            "updated_at": str(value.get("updated_at", "") or ""),
        }

    return {
        "name_raw": name_raw,
        "smiles": "",
        "source": "legacy_json",
        "status": "error",
        "note": f"Unsupported cache value type: {type(value)}",
        "confidence": 0.0,
        "trace": [],
        "updated_at": "",
    }


def load_cache_json(path: str) -> CacheDict:
    """
    Load JSON cache from disk.

    Returns:
      cache dict keyed by normalized lower-cased name:
        cache[name_norm] = CacheRecord
    """
    cache: CacheDict = {}
    if not path or not os.path.exists(path):
        return cache

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # Keep silent-ish here; caller can log if desired.
        return cache

    if not isinstance(data, dict):
        return cache

    # Optional schema key support (ignored if present)
    if "_schema" in data and isinstance(data["_schema"], dict):
        data = {k: v for k, v in data.items() if k != "_schema"}

    for k, v in data.items():
        name_raw = normalize_name(k)
        if not name_raw:
            continue
        name_norm = name_raw.lower()
        cache[name_norm] = _to_record(name_raw, v)

    return cache


def save_cache_json(
    path: str,
    cache: CacheDict,
    *,
    include_schema: bool = False,
    schema_version: int = 1,
) -> None:
    """
    Save unified record dict back to JSON, deterministically ordered.

    Atomic write strategy:
      write to <path>.tmp then os.replace to final path.
    """
    if not path:
        raise ValueError("cache path is empty")

    out: Dict[str, Any] = {}

    if include_schema:
        out["_schema"] = {
            "name": "smiles_resolver_cache",
            "version": int(schema_version),
            "updated_at": now_iso(),
        }

    for k in sorted(cache.keys(), key=lambda s: s.lower()):
        out[k] = cache[k]

    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_cached_smiles(cache: CacheDict, name_value: Any) -> str:
    """Return cached SMILES for a given raw name (after normalization)."""
    nm = normalize_name(name_value)
    if not nm:
        return ""
    rec = cache.get(nm.lower())
    if not rec:
        return ""
    return (rec.get("smiles") or "").strip()


def upsert_cache_record(
    cache: CacheDict,
    *,
    name_raw: str,
    smiles: str = "",
    source: str = "",
    status: str = "",
    note: str = "",
    confidence: float = 0.0,
    trace: Optional[List[Dict[str, Any]]] = None,
    updated_at: Optional[str] = None,
) -> CacheRecord:
    """
    Convenience helper to create/update a cache record in the unified format.
    Returns the record stored in cache.
    """
    nr = normalize_name(name_raw)
    if not nr:
        raise ValueError("name_raw is empty after normalization")

    sm = (smiles or "").strip()
    st = (status or "").strip()
    if not st:
        st = "found" if sm else "not_found"

    try:
        conf = float(confidence or 0.0)
    except Exception:
        conf = 0.0

    rec: CacheRecord = {
        "name_raw": nr,
        "smiles": sm,
        "source": str(source or ""),
        "status": st,
        "note": str(note or ""),
        "confidence": conf,
        "trace": trace if isinstance(trace, list) else [],
        "updated_at": updated_at or now_iso(),
    }

    cache[nr.lower()] = rec
    return rec


__all__ = [
    "CacheRecord",
    "CacheDict",
    "normalize_name",
    "now_iso",
    "load_cache_json",
    "save_cache_json",
    "get_cached_smiles",
    "upsert_cache_record",
]
