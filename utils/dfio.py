"""
DataFrame <-> cache interaction utilities.
Responsible ONLY for:
  - detecting name/SMILES column pairs
  - filling SMILES from cache
  - collecting unresolved names

No network, no resolver logic here.
"""

from typing import List, Tuple
import pandas as pd
import re


# ------------------------------------------------------------
# Column pair detection
# ------------------------------------------------------------

_PAIR_PATTERN = re.compile(r"^(linker|modulator|metal)_(\d+)$", re.IGNORECASE)


def detect_pairs(df: pd.DataFrame) -> List[Tuple[str, str]]:
    """
    Detect name ↔ SMILES column pairs ONLY for:
      - linker_<n>
      - modulator_<n>
      - metal_<n>

    If a corresponding *_SMILES_<n> column does not exist,
    it will be auto-created and filled with NA.

    Returns:
        List of (name_col, smiles_col)
    """
    pairs: List[Tuple[str, str]] = []

    for col in df.columns:
        if "SMILES" in col:
            continue

        m = _PAIR_PATTERN.match(col)
        if not m:
            continue

        prefix, idx = m.group(1).lower(), m.group(2)
        smiles_col = f"{prefix}_SMILES_{idx}"

        if smiles_col not in df.columns:
            df[smiles_col] = pd.NA
            print(f"🆕 Auto-created column: {smiles_col}")

        pairs.append((col, smiles_col))

    return pairs


# ------------------------------------------------------------
# Cache -> DataFrame filling
# ------------------------------------------------------------

def fill_df_from_cache(
    df: pd.DataFrame,
    pairs: List[Tuple[str, str]],
    cache: dict,
) -> int:
    """
    Fill df[smiles_col] from cache where:
      - name is present
      - SMILES cell is empty / NA

    Cache format:
        cache[name_norm] = {
            "smiles": "...",
            ...
        }

    Returns:
        Number of cells filled
    """
    filled = 0

    for name_col, smiles_col in pairs:
        name_present = (
            df[name_col].notna()
            & (df[name_col].astype(str).str.strip() != "")
        )

        smiles_missing = (
            df[smiles_col].isna()
            | (df[smiles_col].astype(str).str.strip() == "")
        )

        mask = name_present & smiles_missing
        if not mask.any():
            continue

        for idx in df.index[mask]:
            name_val = str(df.at[idx, name_col]).strip()
            if not name_val:
                continue

            rec = cache.get(name_val.lower())
            if not rec:
                continue

            sm = (rec.get("smiles") or "").strip()
            if not sm:
                continue

            df.at[idx, smiles_col] = sm
            filled += 1

    return filled


# ------------------------------------------------------------
# Collect unresolved names
# ------------------------------------------------------------

def collect_missing_names(
    df: pd.DataFrame,
    pairs: List[Tuple[str, str]],
) -> List[str]:
    """
    After filling from cache, collect all remaining names
    whose SMILES cells are still empty.

    Returns:
        Sorted list of unique names (original casing preserved)
    """
    candidates: List[str] = []

    for name_col, smiles_col in pairs:
        name_present = (
            df[name_col].notna()
            & (df[name_col].astype(str).str.strip() != "")
        )

        smiles_missing = (
            df[smiles_col].isna()
            | (df[smiles_col].astype(str).str.strip() == "")
        )

        mask = name_present & smiles_missing
        if not mask.any():
            continue

        values = df.loc[mask, name_col].astype(str).tolist()
        candidates.extend(v.strip() for v in values if v.strip())

    # Deduplicate case-insensitively but keep original strings
    seen = set()
    unique: List[str] = []

    for name in candidates:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(name)

    return sorted(unique, key=lambda x: x.lower())
