"""
SMILES + name utilities used across the resolver.

Contents:
- normalize_name
- metal/inorganic heuristic filter
- SMILES extraction patterns + block detection
- SMILES validation + optional RDKit canonicalization
- deterministic sleep/backoff policy
"""

from __future__ import annotations

import re
import time
from typing import Any, List, Tuple, Optional

# Optional RDKit for robust SMILES validation/canonicalization.
# If absent, we fall back to heuristic validation.
try:
    from rdkit import Chem  # type: ignore
except Exception:
    Chem = None  # type: ignore


# =========================
# 1) Basic name normalization
# =========================
_WS = re.compile(r"\s+")


def normalize_name(x: Any) -> str:
    """
    Minimal normalization used for name keys and planning:
    - cast to str
    - strip
    - collapse whitespace
    """
    if x is None:
        return ""
    s = str(x).strip()
    s = _WS.sub(" ", s)
    return s


# =========================
# 2) Metal / inorganic filtering
# =========================
METAL_SYMBOLS = [
    "Li", "Na", "K", "Rb", "Cs",
    "Be", "Mg", "Ca", "Sr", "Ba",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Al", "Ga", "In", "Sn", "Tl", "Pb", "Bi",
]

INORGANIC_KEYWORDS = [
    "chloride", "bromide", "iodide", "fluoride",
    "nitrate", "nitrite", "sulfate", "sulphate", "phosphate", "carbonate",
    "hydroxide", "oxide", "peroxide",
    "cyanide", "thiocyanate", "scn",
    "hcl", "hf", "hno3", "h2so4", "naoh", "koh",
    "so4", "po4",
    "for ph adjustment",
]

# Metal symbol boundary match: "Fe" matches "Fe" but not "Fex" or "cafe"
METAL_PATTERNS = [
    re.compile(rf"(?<![A-Za-z]){re.escape(sym)}(?![a-z])", re.IGNORECASE)
    for sym in METAL_SYMBOLS
]

INORG_PAT = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in INORGANIC_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

FORMULAISH = re.compile(r"^[A-Za-z0-9\(\)\[\]\+\-·\.\s]+$")


def is_likely_metal_ion_or_simple_salt(name: str) -> bool:
    """
    Heuristic filter:
    - short inorganic salts/metal ions are low-value targets for SMILES resolution
    - catches common salts, acids/bases, formula-ish tokens
    """
    s = normalize_name(name)
    if not s:
        return True

    # A simple "shortish" heuristic: short names or 1-2 tokens
    shortish = (len(s) < 12) or (len(s.split()) <= 2 and len(s) < 18)

    # direct inorganic keywords
    if INORG_PAT.search(s):
        return True

    if shortish:
        # metal symbol boundary match
        for pat in METAL_PATTERNS:
            if pat.search(s):
                return True

        # formula-ish + contains digits + uppercase suggests a salt/formula token
        if len(s) <= 10 and FORMULAISH.match(s):
            if re.search(r"\d", s) and re.search(r"[A-Z]", s):
                return True

    return False


# =========================
# 3) SMILES patterns & extraction
# =========================
SMILES_CHARSET = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#\\/%.]+$")

SMILES_PATTERNS = [
    re.compile(r"(?:Canonical)\s+SMILES\s*[:\-]?\s*([A-Za-z0-9@+\-\[\]\(\)=#\\/%.]+)", re.IGNORECASE),
    re.compile(r"(?:Isomeric)\s+SMILES\s*[:\-]?\s*([A-Za-z0-9@+\-\[\]\(\)=#\\/%.]+)", re.IGNORECASE),
    re.compile(r"\bSMILES\b\s*[:\-]?\s*([A-Za-z0-9@+\-\[\]\(\)=#\\/%.]+)", re.IGNORECASE),
]

BLOCK_SIGNALS = [
    "unusual traffic",
    "detected unusual traffic",
    "prove you are not a robot",
    "our systems have detected",
    "captcha",
    "sorry",
]


def canonicalize_smiles(sm: str) -> str:
    """
    Canonicalize SMILES if RDKit is available; otherwise return stripped input.
    """
    sm = (sm or "").strip()
    if not sm:
        return ""
    if Chem is None:
        return sm
    try:
        mol = Chem.MolFromSmiles(sm)
        if mol is None:
            return sm
        return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        return sm


def smiles_valid(sm: str) -> bool:
    """
    Validate SMILES:
    - with RDKit: MolFromSmiles != None
    - without RDKit: charset + minimal structural heuristics
    """
    sm = (sm or "").strip()
    if not sm:
        return False

    if Chem is not None:
        try:
            return Chem.MolFromSmiles(sm) is not None
        except Exception:
            return False

    # Heuristic fallback (best-effort)
    if not SMILES_CHARSET.match(sm):
        return False
    if any(ch.isspace() for ch in sm):
        return False
    if sm.count("(") != sm.count(")"):
        return False
    # must include at least one typical organic atom symbol
    if not re.search(r"[BCNOSPIFbcnospif]", sm):
        return False
    return True


def looks_like_smiles_token(s: str) -> bool:
    """
    Lightweight check used for 'already_smiles' classification.
    """
    s = (s or "").strip()
    if len(s) < 5:
        return False
    if not SMILES_CHARSET.match(s):
        return False
    if not re.search(r"[BCNOSPIFbcnospif]", s):
        return False
    return True


def extract_smiles_from_text(page_text: str) -> Tuple[str, str, str]:
    """
    Extract SMILES from arbitrary page text using regex patterns.

    Returns:
      smiles, status, note

    status in: found / not_found / blocked
    """
    text = page_text or ""
    low = text.lower()

    # If a block/captcha page and no SMILES appears, stop early.
    if any(sig in low for sig in BLOCK_SIGNALS) and ("smiles" not in low):
        return "", "blocked", "页面疑似触发反爬/验证码（traffic/captcha），未继续解析。"

    candidates: List[str] = []
    for pat in SMILES_PATTERNS:
        for m in pat.finditer(text):
            s = (m.group(1) or "").strip().strip('"\'')
            s = re.sub(r"[,\.;:]+$", "", s)
            if not s:
                continue
            if len(s) < 2 or len(s) > 500:
                continue
            if any(ch.isspace() for ch in s):
                continue
            if not SMILES_CHARSET.match(s):
                continue
            # avoid ultra-short / non-organic noise
            if sum(ch.isalpha() for ch in s) < 2:
                continue
            if not re.search(r"[BCNOSPIFbcnospif]", s):
                continue
            candidates.append(s)

    if not candidates:
        return "", "not_found", "未识别到明确SMILES；可能页面未直接展示或命名歧义。"

    return (
        candidates[0],
        "found",
        "从页面文本识别到SMILES（优先Canonical/Isomeric标签），并通过基础合法性检查后接受。",
    )


# =========================
# 4) Sleep / backoff policy
# =========================
def polite_sleep(
    base: float,
    i: int,
    name_norm: str,
    *,
    attempt: int = 0,
    blocked: bool = False,
    extra_jitter: bool = False,
    max_sleep: float = 60.0,
) -> None:
    """
    Deterministic sleep controller with mild backoff.

    Parameters:
      base: base sleep seconds for the tool
      i: running index (1..N), used to add gradual pacing
      name_norm: name normalized (used only if extra_jitter=True)
      attempt: retry attempt index (0..)
      blocked: whether we saw an anti-bot signal
      extra_jitter: if True, add deterministic jitter based on name hash
      max_sleep: cap the sleep
    """
    if base <= 0:
        return

    t = float(base)

    # mild pacing every 5 items
    t += (i // 5) * 0.5

    # optional deterministic jitter
    if extra_jitter and name_norm:
        h = sum(ord(c) for c in name_norm)
        t += (h % 10) * 0.2

    # exponential-ish backoff on retries or blocks
    if blocked or attempt > 0:
        t *= (1.8 ** max(attempt, 1))

    if t > max_sleep:
        t = max_sleep

    time.sleep(t)


__all__ = [
    "normalize_name",
    "METAL_SYMBOLS",
    "INORGANIC_KEYWORDS",
    "is_likely_metal_ion_or_simple_salt",
    "SMILES_CHARSET",
    "SMILES_PATTERNS",
    "BLOCK_SIGNALS",
    "canonicalize_smiles",
    "smiles_valid",
    "looks_like_smiles_token",
    "extract_smiles_from_text",
    "polite_sleep",
]
