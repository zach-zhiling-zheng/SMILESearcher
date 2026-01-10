"""
Utility package exports.

This file keeps imports ergonomic across the repo, e.g.:

    from utils import load_cache_json, save_cache_json, normalize_name, smiles_valid
"""

from .cache import (
    CacheDict,
    CacheRecord,
    normalize_name as normalize_cache_name,
    now_iso,
    load_cache_json,
    save_cache_json,
    get_cached_smiles,
    upsert_cache_record,
)

from .smiles import (
    normalize_name,
    is_likely_metal_ion_or_simple_salt,
    canonicalize_smiles,
    smiles_valid,
    looks_like_smiles_token,
    extract_smiles_from_text,
    polite_sleep,
    METAL_SYMBOLS,
    INORGANIC_KEYWORDS,
    SMILES_CHARSET,
    SMILES_PATTERNS,
    BLOCK_SIGNALS,
)

__all__ = [
    # cache.py
    "CacheDict",
    "CacheRecord",
    "normalize_cache_name",
    "now_iso",
    "load_cache_json",
    "save_cache_json",
    "get_cached_smiles",
    "upsert_cache_record",
    # smiles.py
    "normalize_name",
    "is_likely_metal_ion_or_simple_salt",
    "canonicalize_smiles",
    "smiles_valid",
    "looks_like_smiles_token",
    "extract_smiles_from_text",
    "polite_sleep",
    "METAL_SYMBOLS",
    "INORGANIC_KEYWORDS",
    "SMILES_CHARSET",
    "SMILES_PATTERNS",
    "BLOCK_SIGNALS",
]
