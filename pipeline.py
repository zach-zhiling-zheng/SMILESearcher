"""
Main pipeline (Stage A -> Stage B) for SMILES filling.

This module is the orchestration layer:
- Reads CSV
- Detects linker/modulator/metal_<n> columns and auto-creates *_SMILES_<n>
- Stage A: fills from JSON cache (offline)
- Stage B: resolves remaining names online using resolvers.resolve_smiles_agentic
- Writes filled CSV and updates JSON cache

Repo expectations (simplified structure):
  - utils/cache.py   : load_cache_json, save_cache_json, get_cached_smiles, now_iso
  - utils/dfio.py    : detect_pairs, fill_df_from_cache, collect_missing_names
  - utils/smiles.py  : normalize_name, is_likely_metal_ion_or_simple_salt
  - resolvers.py     : resolve_smiles_agentic (agentic planner)

Important:
- app.py calls pipeline.run_pipeline(...). This file MUST expose run_pipeline.
- Selenium is optional; used only for ChemSpider/Google resolvers when available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# --- utils imports ---
try:
    from utils.cache import load_cache_json, save_cache_json, get_cached_smiles, now_iso
except Exception as e:  # pragma: no cover
    raise ImportError(
        "Missing utils/cache.py with load_cache_json/save_cache_json/get_cached_smiles/now_iso"
    ) from e

try:
    from utils.dfio import detect_pairs, fill_df_from_cache, collect_missing_names
except Exception as e:  # pragma: no cover
    raise ImportError(
        "Missing utils/dfio.py with detect_pairs/fill_df_from_cache/collect_missing_names"
    ) from e

try:
    from utils.smiles import normalize_name, is_likely_metal_ion_or_simple_salt
except Exception as e:  # pragma: no cover
    raise ImportError(
        "Missing utils/smiles.py with normalize_name/is_likely_metal_ion_or_simple_salt"
    ) from e

try:
    import resolvers
except Exception as e:  # pragma: no cover
    raise ImportError("Missing resolvers.py (expected to define resolve_smiles_agentic)") from e


# -------------------------
# Selenium is optional
# -------------------------
def _maybe_init_selenium(headless: bool, page_wait_sec: int):
    """
    Returns (driver, wait) or (None, None) if selenium is unavailable.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception:
        return None, None

    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1400,900")
    chrome_options.add_experimental_option("detach", True)

    service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, page_wait_sec)
    return driver, wait


# -------------------------
# Config
# -------------------------
@dataclass
class PipelineConfig:
    # Output naming
    stage_a_prefix: str = "A_"
    output_prefix: str = "filled_"

    # Run behavior
    requery_not_found: bool = False
    cache_flush_every: int = 20  # save cache checkpoint every N newly resolved names

    # Selenium
    headless: bool = False
    page_wait_sec: int = 12
    enable_selenium: bool = True

    # Budgets (limits) — resolvers may read these from config
    budget_chemspider: int = 40
    budget_google: int = 8
    budget_pubchem: int = 80
    budget_opsin: int = 80

    # Enable/disable particular resolvers (global switches)
    enable_wikipedia: bool = True
    enable_pubchem: bool = True
    enable_opsin: bool = True
    enable_chemspider: bool = True
    enable_google: bool = True

    # Pass-through blob for resolvers (headers/sleep/etc.)
    extra: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_any(config: Optional[Any]) -> "PipelineConfig":
        """
        Accept either:
          - None  -> defaults
          - PipelineConfig -> passthrough
          - dict  -> map fields from the dict produced by app.py
        """
        if config is None:
            return PipelineConfig()
        if isinstance(config, PipelineConfig):
            return config
        if not isinstance(config, dict):
            return PipelineConfig(extra={"raw_config_type": str(type(config))})

        # This maps the dict emitted by the provided app.py:
        # {
        #   "headless": bool,
        #   "page_wait_sec": int,
        #   "budgets": {...limits...},
        #   "sleep": {...},
        #   "google": {...},
        #   "requery_not_found": bool,
        #   "chemspider_api_key": str,
        #   "http_headers": {...},
        # }
        budgets = config.get("budgets", {}) or {}
        return PipelineConfig(
            headless=bool(config.get("headless", False)),
            page_wait_sec=int(config.get("page_wait_sec", 12)),
            requery_not_found=bool(config.get("requery_not_found", False)),
            budget_chemspider=int(budgets.get("chemspider", 40) or 40),
            budget_google=int(budgets.get("google", 8) or 8),
            budget_pubchem=int(budgets.get("pubchem", 80) or 80),
            budget_opsin=int(budgets.get("opsin", 80) or 80),
            extra=dict(config),
        )


# -------------------------
# Public API (expected by app.py)
# -------------------------
def run_pipeline(
    *,
    csv_path: str,
    cache_json: str,
    output_path: Optional[str] = None,
    stage_a_output_path: Optional[str] = None,
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Execute the full pipeline.

    Parameters:
      csv_path: input CSV
      cache_json: JSON cache path
      output_path: output CSV path (defaults to filled_<input>.csv)
      stage_a_output_path: Stage-A CSV path (defaults to A_<input>.csv)
      config: dict (from app.py) or PipelineConfig

    Returns:
      stats dict
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    cfg = PipelineConfig.from_any(config)

    base = os.path.basename(csv_path)
    if output_path is None:
        output_path = cfg.output_prefix + base
    if stage_a_output_path is None:
        stage_a_output_path = cfg.stage_a_prefix + base

    # -------------------------
    # 1) Load CSV + detect pairs
    # -------------------------
    df = pd.read_csv(csv_path)
    pairs = detect_pairs(df)

    print("Detected / created column pairs:")
    for a, b in pairs:
        print(f"  - {a}  ->  {b}")

    # -------------------------
    # 2) Load cache
    # -------------------------
    cache = load_cache_json(cache_json)
    print(f"\n📦 Loaded JSON cache: {len(cache)} entries from {cache_json}")

    # -------------------------
    # 3) Stage A: fill from cache (offline)
    # -------------------------
    filled_a = fill_df_from_cache(df, pairs, cache)
    print(f"✅ Stage A: filled {filled_a} cells from JSON cache before any online lookup.")

    df.to_csv(stage_a_output_path, index=False, encoding="utf-8-sig")
    print(f"💾 Stage A CSV saved: {stage_a_output_path}")

    # Reload for consistency with original script
    df = pd.read_csv(stage_a_output_path)

    unique_names = collect_missing_names(df, pairs)
    print(f"\n🔍 Remaining unique names to resolve after Stage A: {len(unique_names)}")

    # -------------------------
    # 4) Filter inorganic/metal ions (write filtered status to cache)
    # -------------------------
    candidates: List[str] = []
    filtered_out = 0

    for nm in unique_names:
        nm0 = normalize_name(nm)
        if not nm0:
            continue
        nm_norm = nm0.lower()

        # Already found in cache
        rec = cache.get(nm_norm)
        if isinstance(rec, dict) and (rec.get("smiles") or "").strip():
            continue

        if is_likely_metal_ion_or_simple_salt(nm0):
            filtered_out += 1
            # record filtered if not already present
            if nm_norm not in cache or not isinstance(cache.get(nm_norm), dict) or not cache[nm_norm].get("status"):
                cache[nm_norm] = {
                    "name_raw": nm0,
                    "smiles": "",
                    "source": "heuristic",
                    "status": "filtered",
                    "note": "名称较短且包含金属符号/无机盐关键词（短名+边界匹配判定），为减少低价值查询跳过。",
                    "confidence": 0.0,
                    "trace": [],
                    "updated_at": now_iso(),
                }
            continue

        candidates.append(nm0)

    print(f"🧪 Stage B candidates: {len(candidates)}; filtered out: {filtered_out}")

    # Persist cache after filtering to avoid losing work
    save_cache_json(cache_json, cache)

    # -------------------------
    # 5) Stage B: resolve remaining names (online)
    # -------------------------
    budgets_used = {"chemspider": 0, "google": 0, "pubchem": 0, "opsin": 0}
    google_blocked = False

    driver = None
    wait = None

    need_selenium = cfg.enable_selenium and (cfg.enable_chemspider or cfg.enable_google)
    if need_selenium:
        driver, wait = _maybe_init_selenium(cfg.headless, cfg.page_wait_sec)
        if driver is None:
            print("⚠️ Selenium not available; ChemSpider/Google resolvers may be skipped by resolvers.py.")

    resolved_count = 0
    skipped_cache_found = 0
    skipped_cache_terminal = 0

    try:
        for i, name in enumerate(candidates, 1):
            name0 = normalize_name(name)
            if not name0:
                continue
            name_norm = name0.lower()

            # Cache skip logic
            if name_norm in cache and isinstance(cache[name_norm], dict):
                st = (cache[name_norm].get("status") or "").strip()
                sm = (cache[name_norm].get("smiles") or "").strip()
                if sm:
                    conf = float(cache[name_norm].get("confidence", 0.0) or 0.0)
                    print(f"[{i}/{len(candidates)}] Cache hit (found): {name0} -> {sm} (conf={conf:.2f})")
                    skipped_cache_found += 1
                    continue
                if st in {"not_found", "filtered", "blocked"} and not cfg.requery_not_found:
                    print(f"[{i}/{len(candidates)}] Cache hit ({st}, skip): {name0}")
                    skipped_cache_terminal += 1
                    continue

            print(f"[{i}/{len(candidates)}] Resolving (agentic planner): {name0}")

            # Preferred call signature: resolvers.resolve_smiles_agentic(..., driver=..., wait=..., config=...)
            # Fallback to legacy signature if resolvers not yet refactored.
            try:
                smiles, source, status, note, google_blocked, confidence, trace = resolvers.resolve_smiles_agentic(
                    name=name0,
                    i=i,
                    budgets=budgets_used,
                    google_blocked=google_blocked,
                    driver=driver,
                    wait=wait,
                    config=(cfg.extra if isinstance(cfg.extra, dict) else cfg),
                )
            except TypeError:
                smiles, source, status, note, google_blocked, confidence, trace = resolvers.resolve_smiles_agentic(
                    name0, i, budgets_used, google_blocked
                )

            cache[name_norm] = {
                "name_raw": name0,
                "smiles": (smiles or "").strip(),
                "source": source,
                "status": status,
                "note": note,
                "confidence": float(confidence or 0.0),
                "trace": trace if isinstance(trace, list) else [],
                "updated_at": now_iso(),
            }

            if smiles:
                print(f"✅ {name0} → {smiles}  [{source}] (conf={confidence:.2f})")
            else:
                print(f"⚠️ {name0} → (no SMILES) [{status}] [{source}] (conf={confidence:.2f})")

            resolved_count += 1

            # Periodic cache flush for durability
            if cfg.cache_flush_every > 0 and (resolved_count % cfg.cache_flush_every == 0):
                save_cache_json(cache_json, cache)
                print(f"🧾 JSON cache checkpoint saved ({resolved_count} resolved so far): {cache_json}")

        # Final cache save
        save_cache_json(cache_json, cache)
        print(f"\n🧾 JSON cache saved: {cache_json}")

        # -------------------------
        # 6) Final fill pass + write output CSV
        # -------------------------
        filled_b = fill_df_from_cache(df, pairs, cache)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ Final fill pass filled {filled_b} additional cells. Output saved: {output_path}")

        return {
            "csv_path": csv_path,
            "stage_a_output_path": stage_a_output_path,
            "output_path": output_path,
            "cache_json": cache_json,
            "pairs": pairs,
            "unique_names_after_stage_a": len(unique_names),
            "stage_b_candidates": len(candidates),
            "filtered_out": filtered_out,
            "filled_stage_a": filled_a,
            "filled_final_pass": filled_b,
            "resolved_count": resolved_count,
            "skipped_cache_found": skipped_cache_found,
            "skipped_cache_terminal": skipped_cache_terminal,
            "budgets_used": budgets_used,
            "google_blocked": google_blocked,
        }

    finally:
        # Always close selenium driver if created
        try:
            if driver is not None:
                driver.quit()
        except Exception:
            pass


# Backward-compatible alias (optional)
def run(
    csv_path: str,
    cache_json_path: str,
    output_path: Optional[str] = None,
    stage_a_output_path: Optional[str] = None,
    config: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Alias for older code paths; app.py uses run_pipeline().
    """
    return run_pipeline(
        csv_path=csv_path,
        cache_json=cache_json_path,
        output_path=output_path,
        stage_a_output_path=stage_a_output_path,
        config=config,
    )
