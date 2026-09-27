# SPDX-License-Identifier: GPL-3.0-only
# Modified 2026-09-27 by Zheng Lab; adapted from StarLiu714/SMILESearcher
# and the local building-block name-resolution workflow.
"""Conservative name handling and RDKit structure validation."""

import math
import re

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors


_MISSING = {"", "nan", "none", "null", "n/a", "not reported", "not stated", "unknown"}
_FORMULA_PREFIX = "@formula:"
_FORMULA_TOKEN = re.compile(r"(?:[A-Z][a-z]?\d*)+(?:[+-]\d*)?")
_ELEMENTS = {Chem.GetPeriodicTable().GetElementSymbol(number) for number in range(1, 119)}


def is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    # Element/formula labels such as Na and NO are not missing-value flags.
    return str(value).strip().casefold() in _MISSING


def normalize_name(value) -> str:
    if is_missing(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def name_key(value) -> str:
    """Casefold names while keeping case-sensitive formula keys distinct."""
    text = normalize_name(value)
    if text.startswith(_FORMULA_PREFIX):
        return text
    if _FORMULA_TOKEN.fullmatch(text):
        symbols = re.findall(r"[A-Z][a-z]?", text)
        if all(symbol in _ELEMENTS for symbol in symbols):
            return _FORMULA_PREFIX + text
    return text.casefold()


def canonicalize_smiles(value) -> str | None:
    """Return canonical isomeric SMILES; retain salts, isotopes and charges."""
    if is_missing(value):
        return None
    text = str(value).strip()
    # RDKit otherwise accepts a trailing molecule name or a CXSMILES suffix.
    if any(character.isspace() for character in text):
        return None
    try:
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(text)
        if molecule is None or molecule.GetNumAtoms() == 0:
            return None
        return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
    except (ValueError, RuntimeError):
        return None


def molecular_weight(smiles) -> float | None:
    """Return average molecular weight in g/mol for all encoded fragments.

    RDKit uses average atomic weights for unlabeled atoms and the specified
    isotope masses for labeled atoms. Unspecified atoms have no defined mass.
    """
    canonical = canonicalize_smiles(smiles)
    if canonical is None:
        return None
    try:
        with rdBase.BlockLogs():
            molecule = Chem.MolFromSmiles(canonical)
        if molecule is None or any(atom.GetAtomicNum() == 0 for atom in molecule.GetAtoms()):
            return None
        weight = float(Descriptors.MolWt(molecule))
        return weight if math.isfinite(weight) and weight > 0 else None
    except (ValueError, RuntimeError):
        return None
