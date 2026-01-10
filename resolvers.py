# resolvers.py
"""
Agentic SMILES resolver (single-module implementation).

Public API expected by pipeline.py:
    resolve_smiles_agentic(name, i, budgets, google_blocked, driver=None, wait=None, config=None)

Notes:
- This module is self-contained for simplicity, but it reuses shared helpers from utils/smiles.py.
- Selenium (driver/wait) is optional; ChemSpider/Google steps will be skipped if driver is None.
- Budgets are counters passed in by pipeline; limits are read from config when available.

Return signature (DO NOT change order):
    (smiles, source, status, note, google_blocked_new, confidence, trace)
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus

import requests

from utils.smiles import (
    normalize_name,
    canonicalize_smiles,
    smiles_valid,
    looks_like_smiles_token,
    extract_smiles_from_text,
    polite_sleep,
    BLOCK_SIGNALS,
)

# -------------------------
# Config helpers
# -------------------------


def _get_cfg_dict(config: Any) -> Dict[str, Any]:
    """
    pipeline passes either:
      - dict (recommended; app.py builds this)
      - PipelineConfig.extra dict
      - PipelineConfig object (rare)
    """
    if config is None:
        return {}

    if isinstance(config, dict):
        return config

    # Try attribute-style (PipelineConfig)
    out: Dict[str, Any] = {}
    # common fields if present
    for k in [
        "headless",
        "page_wait_sec",
        "requery_not_found",
        "enable_wikipedia",
        "enable_pubchem",
        "enable_opsin",
        "enable_chemspider",
        "enable_google",
        "budget_chemspider",
        "budget_google",
        "budget_pubchem",
        "budget_opsin",
        "extra",
    ]:
        if hasattr(config, k):
            out[k] = getattr(config, k)

    # If PipelineConfig has .extra, merge it last
    if isinstance(out.get("extra"), dict):
        merged = dict(out.get("extra", {}))
        merged.update({k: v for k, v in out.items() if k != "extra"})
        return merged

    return out


def _cfg_get(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    return cfg.get(key, default)


def _cfg_get_nested(cfg: Dict[str, Any], path: Tuple[str, ...], default: Any) -> Any:
    cur: Any = cfg
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _http_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = _cfg_get(cfg, "http_headers", None)
    if isinstance(headers, dict) and headers:
        return {str(k): str(v) for k, v in headers.items()}
    return {
        "User-Agent": "Mozilla/5.0 (SMILES-fallback-resolver; contact: local-script)",
        "Accept": "application/json,text/plain,*/*",
    }


def _budget_limits(cfg: Dict[str, Any]) -> Dict[str, int]:
    # app.py builds config["budgets"] = { chemspider, google, pubchem, opsin } limits
    lim = _cfg_get(cfg, "budgets", None)
    if isinstance(lim, dict) and lim:
        return {
            "chemspider": int(lim.get("chemspider", 40) or 40),
            "google": int(lim.get("google", 8) or 8),
            "pubchem": int(lim.get("pubchem", 80) or 80),
            "opsin": int(lim.get("opsin", 80) or 80),
        }

    # fallback to PipelineConfig-style fields if present in cfg dict
    return {
        "chemspider": int(cfg.get("budget_chemspider", 40) or 40),
        "google": int(cfg.get("budget_google", 8) or 8),
        "pubchem": int(cfg.get("budget_pubchem", 80) or 80),
        "opsin": int(cfg.get("budget_opsin", 80) or 80),
    }


def _sleep_bases(cfg: Dict[str, Any]) -> Dict[str, Any]:
    sl = _cfg_get(cfg, "sleep", None)
    if isinstance(sl, dict) and sl:
        return {
            "wiki": float(sl.get("wiki", 0.6) or 0.6),
            "chemspider": float(sl.get("chemspider", 5.0) or 5.0),
            "google": float(sl.get("google", 10.0) or 10.0),
            "pubchem": float(sl.get("pubchem", 0.8) or 0.8),
            "opsin": float(sl.get("opsin", 0.8) or 0.8),
            "extra_jitter": bool(sl.get("extra_jitter", False)),
        }

    # fallback defaults
    return {
        "wiki": 0.6,
        "chemspider": 5.0,
        "google": 10.0,
        "pubchem": 0.8,
        "opsin": 0.8,
        "extra_jitter": bool(cfg.get("extra_jitter", False)),
    }


def _resolver_enabled(cfg: Dict[str, Any], name: str) -> bool:
    # optional global switches
    key = f"enable_{name}"
    if key in cfg:
        return bool(cfg[key])
    return True


# -------------------------
# Selenium helpers
# -------------------------


def _wait_for_page_stable(driver: Any, wait: Any) -> None:
    if driver is None:
        return
    try:
        if wait is not None:
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            return
    except Exception:
        pass
    try:
        driver.execute_script("return document.readyState")
    except Exception:
        pass


# -------------------------
# Domain: planning & similarity
# -------------------------

_STOPWORDS = {
    "acid",
    "hydrate",
    "anhydrous",
    "solution",
    "aqueous",
    "salt",
    "for",
    "ph",
    "adjustment",
}

ABBREV_MAP = {
    # solvents / modulators
    "dmf": "N,N-dimethylformamide",
    "def": "N,N-diethylformamide",
    "dma": "N,N-dimethylacetamide",
    "dmso": "dimethyl sulfoxide",
    "meoh": "methanol",
    "etoh": "ethanol",
    "acn": "acetonitrile",
    "thf": "tetrahydrofuran",
    "dcm": "dichloromethane",
    # common MOF linkers (extend as needed)
    "bdc": "terephthalic acid",
    "h2bdc": "terephthalic acid",
    "nh2-bdc": "2-aminoterephthalic acid",
    "btc": "benzene-1,3,5-tricarboxylic acid",
    "h3btc": "benzene-1,3,5-tricarboxylic acid",
    "bpdc": "biphenyl-4,4'-dicarboxylic acid",
}


def normalize_semantic_name(s: str) -> str:
    s = normalize_name(s).lower()
    s = re.sub(r"[\(\)\[\]\{\}]", " ", s)
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [t for t in s.split() if t and t not in _STOPWORDS]
    return " ".join(toks)


def name_similarity(a: str, b: str) -> float:
    aa = normalize_semantic_name(a)
    bb = normalize_semantic_name(b)
    if not aa or not bb:
        return 0.0
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def classify_name_for_planning(name: str) -> str:
    s = normalize_name(name)
    if not s:
        return "empty"
    if looks_like_smiles_token(s):
        return "already_smiles"
    low = s.lower()
    if low in ABBREV_MAP:
        return "abbrev"
    if ("," in s) or (re.search(r"\d", s) and (" " in s or "-" in s)):
        return "iupacish"
    return "generic"


def pick_plan(label: str) -> List[str]:
    if label == "already_smiles":
        return ["accept_input"]
    if label == "iupacish":
        return ["opsin", "pubchem", "wikipedia", "chemspider", "google_verify"]
    if label == "abbrev":
        return ["expand", "pubchem", "wikipedia", "opsin", "chemspider", "google_verify"]
    return ["wikipedia", "pubchem", "opsin", "chemspider", "google_verify"]


def generate_query_variants(name: str) -> List[str]:
    raw = normalize_name(name)
    if not raw:
        return []

    variants = [raw]
    low = raw.lower()

    # abbreviation expansion
    if low in ABBREV_MAP:
        variants.append(ABBREV_MAP[low])

    # remove "for pH adjustment"
    v = re.sub(r"\bfor pH adjustment\b", "", raw, flags=re.IGNORECASE).strip()
    if v and v != raw:
        variants.append(v)

    # remove "x H2O" and dot separators
    v2 = re.sub(r"\b(\d+)?\s*h2o\b", "", raw, flags=re.IGNORECASE).strip()
    v2 = v2.replace("·", " ").replace(".", " ")
    v2 = re.sub(r"\s+", " ", v2).strip()
    if v2 and v2 != raw:
        variants.append(v2)

    # hyphen to space
    v3 = raw.replace("-", " ")
    v3 = re.sub(r"\s+", " ", v3).strip()
    if v3 and v3 != raw:
        variants.append(v3)

    # dedup keep order
    seen = set()
    out: List[str] = []
    for x in variants:
        k = x.lower()
        if k not in seen:
            out.append(x)
            seen.add(k)
    return out


# -------------------------
# Models
# -------------------------

@dataclass
class SmilesCandidate:
    smiles: str
    source: str
    status: str
    note: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


# -------------------------
# Wikipedia/Wikidata resolver (requests-only)
# -------------------------


def _wikidata_entity_from_wikipedia_title(title: str, headers: Dict[str, str]) -> Optional[str]:
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "pageprops",
        "ppprop": "wikibase_item",
        "redirects": 1,
    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    for _, p in pages.items():
        qid = p.get("pageprops", {}).get("wikibase_item")
        if qid:
            return qid
    return None


def _wikipedia_search_title(name: str, headers: Dict[str, str]) -> Optional[str]:
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": name,
        "srlimit": 1,
        "srprop": "",
    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    hits = data.get("query", {}).get("search", [])
    if not hits:
        return None
    return hits[0].get("title")


def _wikidata_fetch_smiles(qid: str, headers: Dict[str, str]) -> Tuple[str, str, str]:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    ent = data.get("entities", {}).get(qid, {})
    claims = ent.get("claims", {})

    def get_prop(prop: str) -> Optional[str]:
        vals = claims.get(prop, [])
        for v in vals:
            try:
                mainsnak = v.get("mainsnak", {})
                dv = mainsnak.get("datavalue", {})
                if dv.get("type") == "string":
                    return dv.get("value")
            except Exception:
                pass
        return None

    iso = get_prop("P2017")  # Isomeric SMILES
    can = get_prop("P233")   # Canonical SMILES

    if iso:
        return iso, "found", "Wikidata P2017（Isomeric SMILES）命中；Wikipedia→Wikidata，稳定可追溯。"
    if can:
        return can, "found", "Wikidata P233（Canonical SMILES）命中；Wikipedia→Wikidata，稳定可追溯。"
    return "", "not_found", "Wikidata实体存在，但未提供P233/P2017（SMILES属性缺失）。"


def wikipedia_fetch_smiles(name: str, headers: Dict[str, str]) -> Tuple[str, str, str, str]:
    """
    Returns: smiles, source, status, note
    """
    try:
        title = _wikipedia_search_title(name, headers=headers)
        if not title:
            return "", "wikipedia", "not_found", "Wikipedia搜索无结果或不够明确。"

        qid = _wikidata_entity_from_wikipedia_title(title, headers=headers)
        if not qid:
            return "", "wikipedia", "not_found", f"Wikipedia页面 '{title}' 未找到对应Wikidata实体。"

        smiles, st, note = _wikidata_fetch_smiles(qid, headers=headers)
        if st == "found":
            return smiles, "wikipedia_wikidata", "found", note
        return "", "wikipedia_wikidata", "not_found", note

    except Exception as e:
        return "", "wikipedia", "error", f"Wikipedia/Wikidata检索异常：{repr(e)}"


# -------------------------
# PubChem resolver (requests-only)
# -------------------------


def _pubchem_get_json(url: str, headers: Dict[str, str]) -> Optional[dict]:
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def pubchem_fetch_smiles(name: str, headers: Dict[str, str]) -> Tuple[str, str, str, str, Dict[str, Any], float]:
    """
    Returns: smiles, source, status, note, evidence, confidence_score
    """
    try:
        nm = normalize_name(name)
        if not nm:
            return "", "pubchem", "not_found", "Empty name.", {}, 0.0

        name_q = quote(nm, safe="")
        url_cids = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name_q}/cids/JSON"
        data_cids = _pubchem_get_json(url_cids, headers=headers)
        if not data_cids:
            return "", "pubchem", "not_found", "PubChem name->CID not found (404).", {"query": nm}, 0.0

        cids = (data_cids.get("IdentifierList", {}) or {}).get("CID", []) or []
        if not cids:
            return "", "pubchem", "not_found", "PubChem returned no CID.", {"query": nm}, 0.0

        cids = cids[:5]
        best: Optional[SmilesCandidate] = None

        for cid in cids:
            url_prop = (
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}"
                f"/property/CanonicalSMILES,IsomericSMILES,IUPACName/JSON"
            )
            prop = _pubchem_get_json(url_prop, headers=headers) or {}
            props = (prop.get("PropertyTable", {}) or {}).get("Properties", []) or []
            if not props:
                continue
            p0 = props[0]
            can = (p0.get("CanonicalSMILES") or "").strip()
            iso = (p0.get("IsomericSMILES") or "").strip()
            iupac = (p0.get("IUPACName") or "").strip()

            syns: List[str] = []
            url_syn = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            syn = _pubchem_get_json(url_syn, headers=headers)
            if syn:
                info_list = (syn.get("InformationList", {}) or {}).get("Information", []) or []
                if info_list and isinstance(info_list[0], dict):
                    syns = info_list[0].get("Synonym", []) or []
                syns = syns[:30]

            sim_iupac = name_similarity(nm, iupac) if iupac else 0.0
            sim_syn = max([name_similarity(nm, s) for s in syns], default=0.0)
            sim = max(sim_iupac, sim_syn)

            chosen = iso or can
            if not chosen:
                continue

            chosen_can = canonicalize_smiles(chosen)
            valid = smiles_valid(chosen_can)

            # scoring (same spirit as original script)
            base = 0.82
            score = base + 0.18 * sim + (0.07 if valid else 0.0)
            if len(cids) > 1:
                score -= 0.05
            score = max(0.0, min(1.0, score))

            cand = SmilesCandidate(
                smiles=chosen_can,
                source="pubchem",
                status="found",
                note="PubChem CID resolved; chose best CID via name similarity over IUPAC/synonyms.",
                evidence={
                    "query": nm,
                    "cid": cid,
                    "iupac": iupac,
                    "synonyms_sample": syns[:10],
                    "similarity": sim,
                    "raw_smiles": chosen,
                },
                score=score,
            )

            if best is None or cand.score > best.score:
                best = cand

        if best is None:
            return "", "pubchem", "not_found", "PubChem returned CIDs but no usable SMILES.", {"query": nm, "cids": cids}, 0.0

        return best.smiles, best.source, "found", best.note, best.evidence, best.score

    except Exception as e:
        return "", "pubchem", "error", f"PubChem exception: {repr(e)}", {"query": name}, 0.0


# -------------------------
# OPSIN resolver (requests-only)
# -------------------------


def opsin_fetch_smiles(name: str, headers: Dict[str, str]) -> Tuple[str, str, str, str, Dict[str, Any], float]:
    try:
        nm = normalize_name(name)
        if not nm:
            return "", "opsin", "not_found", "Empty name.", {}, 0.0

        q = quote(nm, safe="")
        url = f"https://opsin.ch.cam.ac.uk/opsin/{q}.json"
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 404:
            return "", "opsin", "not_found", "OPSIN 404 (name not parsed).", {"query": nm}, 0.0
        r.raise_for_status()
        data = r.json()

        sm = (data.get("smiles") or data.get("canonicalSmiles") or data.get("standardSmiles") or "").strip()
        if not sm:
            return "", "opsin", "not_found", "OPSIN JSON has no SMILES field.", {"query": nm}, 0.0

        sm = canonicalize_smiles(sm)
        valid = smiles_valid(sm)
        score = 0.80 + (0.10 if valid else 0.0)
        score = min(1.0, score)

        return sm, "opsin", "found", "OPSIN parsed name to SMILES.", {"query": nm}, score

    except Exception as e:
        return "", "opsin", "error", f"OPSIN exception: {repr(e)}", {"query": name}, 0.0


# -------------------------
# PubChem verification (SMILES -> CID)
# -------------------------


def pubchem_verify_smiles(smiles: str, headers: Dict[str, str]) -> Tuple[bool, Dict[str, Any]]:
    sm = (smiles or "").strip()
    if not sm:
        return False, {"reason": "empty"}

    try:
        q = quote(sm, safe="")
        url_cids = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{q}/cids/JSON"
        data = _pubchem_get_json(url_cids, headers=headers)
        if not data:
            return False, {"reason": "smiles->cid 404"}

        cids = (data.get("IdentifierList", {}) or {}).get("CID", []) or []
        if not cids:
            return False, {"reason": "no cid"}

        cid = cids[0]
        url_prop = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}"
            f"/property/CanonicalSMILES,IsomericSMILES/JSON"
        )
        prop = _pubchem_get_json(url_prop, headers=headers) or {}
        props = (prop.get("PropertyTable", {}) or {}).get("Properties", []) or []
        if not props:
            return True, {"cid": cid, "note": "cid resolved but no properties"}

        p0 = props[0]
        can = (p0.get("CanonicalSMILES") or "").strip()
        iso = (p0.get("IsomericSMILES") or "").strip()
        return True, {"cid": cid, "canonical": can, "isomeric": iso}

    except Exception as e:
        return False, {"reason": f"exception: {repr(e)}"}


# -------------------------
# ChemSpider (selenium)
# -------------------------


def chemspider_fetch_smiles_via_web(name: str, driver: Any, wait: Any) -> Tuple[str, str, str, str]:
    """
    Returns: smiles, source, status, note
    """
    if driver is None:
        return "", "chemspider_web", "skipped", "Selenium driver not provided; skipping ChemSpider web resolver."

    try:
        url = f"https://www.chemspider.com/Search.aspx?q={quote_plus(name)}"
        driver.get(url)
        _wait_for_page_stable(driver, wait)
        body_text = driver.find_element("tag name", "body").text or ""

        smiles, st, note = extract_smiles_from_text(body_text)
        if st == "found":
            return smiles, "chemspider_web", "found", "ChemSpider页面文本命中SMILES；正则抽取+基础检查后接受。"
        if st == "blocked":
            return "", "chemspider_web", "blocked", "ChemSpider疑似触发限流/验证码。"
        return "", "chemspider_web", "not_found", "ChemSpider搜索页未识别到SMILES；可能需进入具体条目页。"
    except Exception as e:
        return "", "chemspider_web", "error", f"ChemSpider检索/解析异常：{repr(e)}"


# -------------------------
# Google (selenium)
# -------------------------


def google_fetch_smiles(name: str, driver: Any, wait: Any, hl: str, num: int) -> Tuple[str, str, str, str]:
    """
    Google AI Overview–oriented SMILES extraction.
    Strategy:
      - Query: "<name> SMILES"
      - Scan entire page text
      - Extract the FIRST long, SMILES-like token
    """
    if driver is None:
        return "", "google", "skipped", "Selenium driver not provided; skipping Google resolver."

    q = f"{name} SMILES"
    url = f"https://www.google.com/search?q={quote_plus(q)}&hl={hl}&num={num}"

    try:
        driver.get(url)
        _wait_for_page_stable(driver, wait)
        text = driver.find_element("tag name", "body").text or ""
        low = text.lower()

        # hard block detection
        if any(sig in low for sig in BLOCK_SIGNALS) and "smiles" not in low:
            return "", "google", "blocked", "Google traffic / captcha detected; AI Overview not accessible."

        tokens = re.split(r"[\s,;]", text)

        # prefer longer tokens to avoid noise
        for tok in tokens:
            s = tok.strip().strip('"\'')
            if len(s) < 25 or len(s) > 500:
                continue
            # reuse charset from utils via looks_like_smiles_token-like logic
            # but require longer and parentheses balance
            if not re.match(r"^[A-Za-z0-9@+\-\[\]\(\)=#\\/%.]+$", s):
                continue
            if any(ch.isspace() for ch in s):
                continue

            if not (
                re.search(r"c\d", s)
                or "C(=O)" in s
                or "c1ccc" in s
                or "N" in s
                or "O" in s
            ):
                continue

            if s.count("(") != s.count(")"):
                continue

            return (
                s,
                "google_ai_overview",
                "found",
                "Extracted first long SMILES-like token from Google AI Overview using shape-based heuristics.",
            )

        return "", "google_ai_overview", "not_found", "No long SMILES-like token detected in Google result."

    except Exception as e:
        return "", "google", "error", f"Google fetch exception: {repr(e)}"


# -------------------------
# Agentic resolver (public)
# -------------------------


def resolve_smiles_agentic(
    name: str,
    i: int,
    budgets: Dict[str, int],
    google_blocked: bool,
    driver: Any = None,
    wait: Any = None,
    config: Optional[Any] = None,
) -> Tuple[str, str, str, str, bool, float, List[Dict[str, Any]]]:
    """
    Agentic planner that tries multiple sources + query variants with budgets.

    budgets:
      - mutable dict of counters: {"chemspider": int, "google": int, "pubchem": int, "opsin": int}
      - limits are in config (if provided)

    Returns:
      (smiles, source, status, note, google_blocked_new, confidence, trace)
    """
    cfg = _get_cfg_dict(config)
    headers = _http_headers(cfg)
    limits = _budget_limits(cfg)
    sleeps = _sleep_bases(cfg)

    google_hl = str(_cfg_get_nested(cfg, ("google", "hl"), "en"))
    google_num = int(_cfg_get_nested(cfg, ("google", "num"), 10))

    name0 = normalize_name(name)
    name_norm = name0.lower()

    trace: List[Dict[str, Any]] = []

    label = classify_name_for_planning(name0)
    plan = pick_plan(label)
    variants = generate_query_variants(name0)
    trace.append({"event": "init", "name": name0, "label": label, "plan": plan, "variants": variants})

    # Quick accept if already SMILES
    if label == "already_smiles":
        sm = canonicalize_smiles(name0)
        ok = smiles_valid(sm)
        st = "found" if ok else "not_found"
        note = "Input appears to be SMILES; accepted directly (canonicalized if possible)."
        return sm if ok else "", "input", st, note, google_blocked, (0.90 if ok else 0.20), trace

    seen_by_smiles: Dict[str, List[str]] = {}

    def consider_candidate(cand: SmilesCandidate) -> SmilesCandidate:
        smk = canonicalize_smiles(cand.smiles)
        srcs = seen_by_smiles.get(smk, [])
        if cand.source not in srcs:
            srcs.append(cand.source)
        seen_by_smiles[smk] = srcs

        if len(srcs) >= 2:
            cand.score = min(1.0, cand.score + 0.12)
            cand.note += f" Cross-validated by sources={srcs}."
        return cand

    best: Optional[SmilesCandidate] = None

    for v in variants:
        v_label = classify_name_for_planning(v)
        trace.append({"event": "variant", "variant": v, "variant_label": v_label})

        for step in plan:
            # global enable switches
            if step == "wikipedia" and not _resolver_enabled(cfg, "wikipedia"):
                trace.append({"step": step, "status": "skipped", "reason": "disabled"})
                continue
            if step == "pubchem" and not _resolver_enabled(cfg, "pubchem"):
                trace.append({"step": step, "status": "skipped", "reason": "disabled"})
                continue
            if step == "opsin" and not _resolver_enabled(cfg, "opsin"):
                trace.append({"step": step, "status": "skipped", "reason": "disabled"})
                continue
            if step == "chemspider" and not _resolver_enabled(cfg, "chemspider"):
                trace.append({"step": step, "status": "skipped", "reason": "disabled"})
                continue
            if step.startswith("google") and not _resolver_enabled(cfg, "google"):
                trace.append({"step": step, "status": "skipped", "reason": "disabled"})
                continue

            # google blocked short-circuit
            if step.startswith("google") and google_blocked:
                trace.append({"step": step, "status": "skipped", "reason": "google_blocked"})
                continue

            if step == "expand":
                trace.append({"step": "expand", "status": "ok", "note": "variants already include abbreviation expansion"})
                continue

            if step == "accept_input":
                continue

            if step == "wikipedia":
                sm, src, st, note = wikipedia_fetch_smiles(v, headers=headers)
                trace.append({"step": "wikipedia", "query": v, "status": st, "note": note})
                polite_sleep(
                    sleeps["wiki"],
                    i,
                    name_norm,
                    extra_jitter=sleeps["extra_jitter"],
                )

                if st == "found" and sm:
                    cand = SmilesCandidate(
                        smiles=canonicalize_smiles(sm),
                        source=src,
                        status="found",
                        note=note,
                        evidence={"query": v},
                        score=min(1.0, 0.90 + (0.05 if smiles_valid(sm) else 0.0)),
                    )
                    cand = consider_candidate(cand)
                    if best is None or cand.score > best.score:
                        best = cand
                    if best.score >= 0.92:
                        return best.smiles, best.source, "found", best.note, google_blocked, best.score, trace
                continue

            if step == "pubchem":
                if budgets.get("pubchem", 0) >= limits["pubchem"]:
                    trace.append({"step": "pubchem", "status": "skipped", "reason": "budget_exhausted"})
                    continue
                budgets["pubchem"] = budgets.get("pubchem", 0) + 1

                sm, src, st, note, evidence, conf = pubchem_fetch_smiles(v, headers=headers)
                trace.append({"step": "pubchem", "query": v, "status": st, "confidence": conf, "evidence": evidence})
                polite_sleep(
                    sleeps["pubchem"],
                    i,
                    name_norm,
                    extra_jitter=sleeps["extra_jitter"],
                )

                if st == "found" and sm:
                    cand = SmilesCandidate(smiles=sm, source=src, status=st, note=note, evidence=evidence, score=conf)
                    cand = consider_candidate(cand)
                    if best is None or cand.score > best.score:
                        best = cand
                    if best.score >= 0.90:
                        return best.smiles, best.source, "found", best.note, google_blocked, best.score, trace
                continue

            if step == "opsin":
                if budgets.get("opsin", 0) >= limits["opsin"]:
                    trace.append({"step": "opsin", "status": "skipped", "reason": "budget_exhausted"})
                    continue
                budgets["opsin"] = budgets.get("opsin", 0) + 1

                sm, src, st, note, evidence, conf = opsin_fetch_smiles(v, headers=headers)
                trace.append({"step": "opsin", "query": v, "status": st, "confidence": conf, "evidence": evidence})
                polite_sleep(
                    sleeps["opsin"],
                    i,
                    name_norm,
                    extra_jitter=sleeps["extra_jitter"],
                )

                if st == "found" and sm:
                    cand = SmilesCandidate(smiles=sm, source=src, status=st, note=note, evidence=evidence, score=conf)
                    cand = consider_candidate(cand)
                    if best is None or cand.score > best.score:
                        best = cand
                    if best.score >= 0.90:
                        return best.smiles, best.source, "found", best.note, google_blocked, best.score, trace
                continue

            if step == "chemspider":
                if budgets.get("chemspider", 0) >= limits["chemspider"]:
                    trace.append({"step": "chemspider", "status": "skipped", "reason": "budget_exhausted"})
                    continue
                budgets["chemspider"] = budgets.get("chemspider", 0) + 1

                attempt = 0
                while attempt < 2:
                    sm, src, st, note = chemspider_fetch_smiles_via_web(v, driver=driver, wait=wait)
                    trace.append({"step": "chemspider", "query": v, "attempt": attempt, "status": st, "note": note})

                    if st == "skipped":
                        # No selenium -> skip
                        break

                    if st == "found" and sm:
                        cand = SmilesCandidate(
                            smiles=canonicalize_smiles(sm),
                            source=src,
                            status="found",
                            note=note,
                            evidence={"query": v},
                            score=min(1.0, 0.72 + (0.08 if smiles_valid(sm) else 0.0)),
                        )
                        cand = consider_candidate(cand)
                        if best is None or cand.score > best.score:
                            best = cand

                        polite_sleep(
                            sleeps["chemspider"],
                            i,
                            name_norm,
                            extra_jitter=sleeps["extra_jitter"],
                        )

                        if best.score >= 0.86:
                            return best.smiles, best.source, "found", best.note, google_blocked, best.score, trace
                        break

                    if st == "blocked":
                        polite_sleep(
                            sleeps["chemspider"],
                            i,
                            name_norm,
                            attempt=attempt,
                            blocked=True,
                            extra_jitter=sleeps["extra_jitter"],
                        )
                        attempt += 1
                        continue

                    polite_sleep(
                        sleeps["chemspider"],
                        i,
                        name_norm,
                        extra_jitter=sleeps["extra_jitter"],
                    )
                    break

                continue

            if step == "google_verify":
                if budgets.get("google", 0) >= limits["google"]:
                    trace.append({"step": "google", "status": "skipped", "reason": "budget_exhausted"})
                    continue
                budgets["google"] = budgets.get("google", 0) + 1

                attempt = 0
                while attempt < 2:
                    sm, src, st, note = google_fetch_smiles(v, driver=driver, wait=wait, hl=google_hl, num=google_num)
                    trace.append({"step": "google", "query": v, "attempt": attempt, "status": st, "note": note})

                    if st == "skipped":
                        # No selenium -> skip google
                        break

                    if st == "blocked":
                        google_blocked = True
                        polite_sleep(
                            sleeps["google"],
                            i,
                            name_norm,
                            attempt=attempt,
                            blocked=True,
                            extra_jitter=sleeps["extra_jitter"],
                        )
                        break

                    if st == "found" and sm:
                        sm_can = canonicalize_smiles(sm)
                        ok, info = pubchem_verify_smiles(sm_can, headers=headers)
                        trace.append({"step": "google_verify_pubchem", "smiles": sm_can, "ok": ok, "info": info})

                        base = 0.50 + (0.08 if smiles_valid(sm_can) else 0.0)
                        if ok:
                            base += 0.18
                            if info.get("canonical"):
                                sm_can = canonicalize_smiles(info["canonical"])

                        cand = SmilesCandidate(
                            smiles=sm_can,
                            source="google_verified" if ok else "google_ai_overview",
                            status="found",
                            note="Google token extracted; verified via PubChem SMILES->CID."
                            if ok
                            else "Google token extracted; PubChem verification failed.",
                            evidence={"query": v, "pubchem_verify": info},
                            score=min(1.0, base),
                        )
                        cand = consider_candidate(cand)
                        if best is None or cand.score > best.score:
                            best = cand

                        polite_sleep(
                            sleeps["google"],
                            i,
                            name_norm,
                            extra_jitter=sleeps["extra_jitter"],
                        )

                        if best.score >= 0.78:
                            return best.smiles, best.source, "found", best.note, google_blocked, best.score, trace
                        break

                    polite_sleep(
                        sleeps["google"],
                        i,
                        name_norm,
                        extra_jitter=sleeps["extra_jitter"],
                    )
                    break

                continue

            trace.append({"step": step, "status": "unknown_step"})

    if best:
        return best.smiles, best.source, best.status, best.note, google_blocked, best.score, trace

    return (
        "",
        "agentic_planner",
        "not_found",
        "All planned tools/variants exhausted without a confident SMILES.",
        google_blocked,
        0.0,
        trace,
    )


__all__ = ["resolve_smiles_agentic"]
