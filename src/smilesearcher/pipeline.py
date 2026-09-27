"""Fill MOF and building-block CSV structures with an auditable, resumable search.

Modified for Zheng Lab, 2026-09-27. Based on StarLiu714/SMILESearcher
and the building-block name-resolution workflow; distributed under GPL-3.0.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from .cache import atomic_write, get_cached_smiles, load_cache_json, now_iso, save_cache_json
from .chemistry import canonicalize_smiles, is_missing, molecular_weight, name_key, normalize_name
from .models import Resolution
from .resolvers import Resolver


DEFAULTS = {
    "name_column": None, "smiles_column": None, "offline": False,
    "providers": ["pubchem", "cactus", "opsin"], "timeout": 20.0,
    "delay": 0.3, "retries": 2, "max_queries": 1000,
    "checkpoint_every": 50, "resume": False, "requery_not_found": False,
    "molecular_weight": False,
}
AUDIT_COLUMNS = ["row", "name_column", "smiles_column", "name", "original_smiles",
                 "smiles", "status", "source", "note"]


def detect_pairs(df, name_column=None, smiles_column=None):
    """Recognize MOF numbered fields or a single building-block/name table."""
    if bool(name_column) != bool(smiles_column):
        raise ValueError("Provide both name_column and smiles_column.")
    if name_column:
        if name_column not in df:
            raise ValueError(f"Name column {name_column!r} is absent.")
        if name_column == smiles_column:
            raise ValueError("Name and SMILES columns must be different.")
        pairs = [(name_column, smiles_column)]
    else:
        pairs = []
        names = {c.casefold(): c for c in df.columns}
        for column in df.columns:
            match = re.fullmatch(r"(linker|modulator|metal)_(\d+)", column, re.I)
            if match:
                prefix, number = match.groups()
                target = f"{prefix.lower()}_SMILES_{number}"
                adjacent = f"{column}_smiles".casefold()
                target = names.get(target.casefold(), names.get(adjacent, target))
                pairs.append((column, target))
        if not pairs:
            for candidate in ("canonical_name", "name", "chemical_name"):
                if candidate in names:
                    pairs = [(names[candidate], names.get("smiles", "smiles"))]
                    break
    if not pairs:
        raise ValueError("No name columns detected; provide --name-column and --smiles-column.")
    for _, target in pairs:
        if target not in df:
            df[target] = ""
    return pairs


def _csv_bytes(frame):
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def run_pipeline(*, csv_path, cache_json, output_path=None, stage_a_output_path=None,
                 config=None, resolver=None):
    """Resolve unique missing names; preserve input rows and valid structures."""
    supplied = dict(config or {})
    unknown = set(supplied) - set(DEFAULTS) - {"headless"}
    if unknown:
        raise ValueError(f"Unknown options: {', '.join(sorted(unknown))}")
    cfg = {**DEFAULTS, **supplied}
    if isinstance(cfg["providers"], str):
        cfg["providers"] = [x.strip() for x in cfg["providers"].split(",") if x.strip()]
    for key in ("retries", "max_queries", "checkpoint_every"):
        if not isinstance(cfg[key], int) or isinstance(cfg[key], bool) or cfg[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer.")
    if not math.isfinite(cfg["timeout"]) or cfg["timeout"] <= 0 or not math.isfinite(cfg["delay"]) or not 0 <= cfg["delay"] <= 60:
        raise ValueError("timeout must be finite and positive; delay must be between 0 and 60 seconds.")
    source = Path(csv_path).resolve()
    output = Path(output_path or source.with_name("filled_" + source.name)).resolve()
    cache_path = Path(cache_json).resolve()
    audit_path = Path(str(output) + ".audit.csv")
    summary_path = Path(str(output) + ".summary.json")
    checkpoint_path = Path(str(output) + ".checkpoint.json")
    checkpoint_csv = Path(str(output) + ".checkpoint.csv")
    paths = [source, output, cache_path, audit_path, summary_path, checkpoint_path, checkpoint_csv]
    if stage_a_output_path:
        paths.append(Path(stage_a_output_path).resolve())
    if len(set(paths)) != len(paths):
        raise ValueError("Input, cache, and output paths must be distinct.")
    raw = source.read_bytes()
    header = next(csv.reader(io.StringIO(raw.decode("utf-8-sig"))), [])
    if not header or any(not column.strip() for column in header) or len(header) != len({c.casefold() for c in header}):
        raise ValueError("Input CSV must have nonempty, unique column headers.")
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    pairs = detect_pairs(frame, cfg["name_column"], cfg["smiles_column"])
    original = frame.copy(deep=True)
    cache = load_cache_json(cache_path, on_conflict="ambiguous")
    input_hash = _hash(raw)
    identity = {k: v for k, v in cfg.items() if k not in {"resume", "checkpoint_every", "headless"}}
    config_hash = _hash(json.dumps(identity, sort_keys=True).encode("utf-8"))
    audit, start_row = [], 0
    if cfg["resume"]:
        if not checkpoint_path.exists():
            raise ValueError("No checkpoint found for this output path.")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("input_sha256") != input_hash or checkpoint.get("config_sha256") != config_hash:
            raise ValueError("Checkpoint input or configuration does not match this run.")
        if checkpoint.get("columns") != list(frame.columns) or len(checkpoint.get("data", [])) != len(frame):
            raise ValueError("Checkpoint table does not match this input.")
        frame = pd.DataFrame(checkpoint["data"], columns=checkpoint["columns"])
        audit = checkpoint["audit"]
        start_row = checkpoint["next_row"]
        if not isinstance(start_row, int) or not 0 <= start_row <= len(frame):
            raise ValueError("Checkpoint row is invalid.")

    if stage_a_output_path:
        stage_a = original.copy(deep=True)
        for name_col, target in pairs:
            for row in stage_a.index:
                if not canonicalize_smiles(stage_a.at[row, target]):
                    value = get_cached_smiles(cache, stage_a.at[row, name_col])
                    if value:
                        stage_a.at[row, target] = value
        atomic_write(stage_a_output_path, _csv_bytes(stage_a))

    def checkpoint(next_row):
        save_cache_json(cache_path, cache)
        atomic_write(checkpoint_csv, _csv_bytes(frame))
        # JSON is the authoritative snapshot, so interruption between writes is recoverable.
        save_cache_json(checkpoint_path, {"schema": 1, "input_sha256": input_hash,
                        "config_sha256": config_hash, "columns": list(frame.columns),
                        "data": frame.to_dict(orient="records"), "audit": audit, "next_row": next_row})

    owns_resolver = resolver is None and not cfg["offline"]
    if owns_resolver:
        resolver = Resolver(providers=cfg["providers"], timeout=cfg["timeout"],
                            retries=cfg["retries"], delay=cfg["delay"],
                            budgets={p: cfg["max_queries"] for p in cfg["providers"]})
    seen = {}
    next_row = start_row
    try:
        for row in range(start_row, len(frame)):
            row_audit = []
            for name_col, target in pairs:
                name = normalize_name(original.at[row, name_col])
                previous = original.at[row, target]
                if is_missing(name) and is_missing(previous):
                    continue
                if canonicalize_smiles(previous):
                    result = Resolution(name=name, smiles=previous, source="input", status="preserved")
                elif is_missing(name):
                    result = Resolution(name=name, status="missing_name", source="input",
                                        note="Existing value is not a valid SMILES." if previous else "")
                else:
                    key = name_key(name)
                    cached = get_cached_smiles(cache, name)
                    old = cache.get(key, {})
                    if cached:
                        result = Resolution(name=name, smiles=cached, source=old.get("source", "cache"),
                                            status="cached", note="Validated cache hit.")
                    elif key in seen:
                        result = seen[key]
                    elif old.get("status") in {"not_found", "ambiguous", "filtered"} and not cfg["requery_not_found"]:
                        result = Resolution(name=name, status=old["status"], source=old.get("source", "cache"),
                                            note="Cached outcome; use --requery-not-found to retry.")
                    elif cfg["offline"]:
                        result = Resolution(name=name, status="unresolved", source="cache", note="Offline cache miss.")
                    else:
                        result = resolver.resolve(name)
                        if result.status == "found":
                            validated = canonicalize_smiles(result.smiles)
                            if validated:
                                result.smiles = validated
                            else:
                                result = Resolution(name=name, status="error", source=result.source,
                                                    note="Provider returned an invalid SMILES.", trace=result.trace)
                        seen[key] = result
                        record = result.to_dict()
                        record["updated_at"] = now_iso()
                        cache[key] = record
                    if result.status in {"found", "cached"} and canonicalize_smiles(result.smiles):
                        frame.at[row, target] = result.smiles
                row_audit.append({"row": row + 1, "name_column": name_col, "smiles_column": target,
                                  "name": name, "original_smiles": previous, "smiles": frame.at[row, target],
                                  "status": result.status, "source": result.source, "note": result.note})
            audit.extend(row_audit)
            next_row = row + 1
            if cfg["checkpoint_every"] and next_row % cfg["checkpoint_every"] == 0:
                checkpoint(next_row)
        checkpoint(next_row)
    except BaseException:
        checkpoint(next_row)
        raise
    finally:
        if owns_resolver:
            resolver.close()
    weight_columns = {}
    if cfg["molecular_weight"]:
        for _, target in pairs:
            match = re.fullmatch(r"(linker|modulator|metal)_SMILES_(\d+)", target, re.I)
            weight_col = (f"{match[1].lower()}_MW_{match[2]}" if match else
                          "molecular_weight_g_mol" if target.casefold() == "smiles" else target + "_mw_g_mol")
            frame[weight_col] = [format(weight, ".8g") if (weight := molecular_weight(value)) is not None else ""
                                 for value in frame[target]]
            weight_columns[target] = weight_col
    atomic_write(output, _csv_bytes(frame))
    atomic_write(audit_path, _csv_bytes(pd.DataFrame(audit, columns=AUDIT_COLUMNS)))
    stats = {"rows": len(frame), "column_pairs": pairs, "input_sha256": input_hash,
             "status_counts": dict(Counter(a["status"] for a in audit)),
             "cells_filled": sum(a["status"] in {"found", "cached"} for a in audit),
             "request_counts": dict(getattr(resolver, "request_counts", {})),
             "offline": cfg["offline"], "completed": True,
             "molecular_weight_columns": weight_columns, "molecular_weight_unit": "g/mol" if weight_columns else None}
    save_cache_json(summary_path, stats)
    return stats
