"""
Entry point for the SMILES agentic resolver.

Goals:
- Keep "double-click start.bat -> interactive prompts" behavior.
- Also support optional CLI args for non-interactive runs.

Expected:
- pipeline.py provides: run_pipeline(csv_path, cache_path, output_path, stage_a_output_path, config)
  (Name can be adjusted, but keep app.py minimal.)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Optional

# Your pipeline module should implement the main workflow.
# e.g., def run_pipeline(csv_path, cache_path, output_path, stage_a_output_path, config): ...
import pipeline


def prompt(msg: str, default: Optional[str] = None) -> str:
    if default is None:
        s = input(f"{msg}: ").strip()
        return s
    s = input(f"{msg} [DEFAULT: {default}]: ").strip()
    return s if s else default


@dataclass
class AppConfig:
    # Selenium
    headless: bool = False
    page_wait_sec: int = 12

    # Budgets per run (avoid bans)
    chemspider_query_budget: int = 40
    google_query_budget: int = 8
    pubchem_query_budget: int = 80
    opsin_query_budget: int = 80

    # Sleep configs (deterministic)
    wiki_sleep_base: float = 0.6
    chemspider_sleep_base: float = 5.0
    google_sleep_base: float = 10.0
    pubchem_sleep_base: float = 0.8
    opsin_sleep_base: float = 0.8

    extra_jitter: bool = False  # deterministic by default

    # Google params
    google_hl: str = "en"
    google_num: int = 10

    # Cache/query behavior
    requery_not_found: bool = False

    # Headers / keys
    chemspider_api_key: str = ""  # optional; can be env-injected
    user_agent: str = "Mozilla/5.0 (SMILES-fallback-resolver; contact: local-script)"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=True)

    # If provided, app runs non-interactively (no prompts required).
    p.add_argument("--csv", dest="csv_path", default=None, help="Input CSV path.")
    p.add_argument("--cache", dest="cache_json", default=None, help="JSON cache path.")
    p.add_argument("--out", dest="output_path", default=None, help="Output CSV path.")
    p.add_argument("--stage-a-out", dest="stage_a_output_path", default=None, help="Stage-A output CSV path.")

    p.add_argument("--headless", action="store_true", help="Run browser in headless mode.")
    p.add_argument("--no-headless", action="store_true", help="Force non-headless browser mode.")

    p.add_argument("--requery-not-found", action="store_true", help="Re-query cached not_found/filtered/blocked names.")
    p.add_argument("--extra-jitter", action="store_true", help="Enable deterministic jitter in sleep policy.")

    p.add_argument("--google-hl", default="en", help="Google UI language, e.g. en.")
    p.add_argument("--google-num", type=int, default=10, help="Google results per page.")

    return p


def interactive_inputs() -> tuple[str, str, str, str, AppConfig]:
    print("=" * 60)
    print(" SMILES Agentic Resolver – Interactive Launch ")
    print("=" * 60)

    csv_path = prompt("Enter input CSV path", "mof_extraction_1_2_3_4_5_6.csv")
    if not os.path.exists(csv_path):
        print(f"[Warning] CSV file not found: {csv_path}")
        input("Press Enter to exit...")
        raise SystemExit(1)

    output_path = "filled_" + os.path.basename(csv_path)
    stage_a_output_path = "A_" + os.path.basename(csv_path)

    cache_json = prompt("Enter JSON cache path", "name2smiles_1222.json")

    headless_input = prompt("Run browser in headless mode? (y/n)", "n").lower()
    headless = headless_input.startswith("y")

    cfg = AppConfig(
        headless=headless,
        chemspider_api_key=os.getenv("CHEMSPIDER_API_KEY", "").strip(),
    )
    return csv_path, cache_json, output_path, stage_a_output_path, cfg


def cli_inputs(args: argparse.Namespace) -> tuple[str, str, str, str, AppConfig]:
    # csv path required for CLI mode
    if not args.csv_path:
        raise SystemExit("Missing --csv. Either provide CLI args or run interactively with no args.")

    csv_path = args.csv_path
    if not os.path.exists(csv_path):
        raise SystemExit(f"CSV file not found: {csv_path}")

    cache_json = args.cache_json or "name2smiles_1222.json"
    output_path = args.output_path or ("filled_" + os.path.basename(csv_path))
    stage_a_output_path = args.stage_a_output_path or ("A_" + os.path.basename(csv_path))

    # headless resolution
    if args.no_headless:
        headless = False
    elif args.headless:
        headless = True
    else:
        headless = False  # default matches your original script's default prompt "n"

    cfg = AppConfig(
        headless=headless,
        requery_not_found=bool(args.requery_not_found),
        extra_jitter=bool(args.extra_jitter),
        google_hl=str(args.google_hl),
        google_num=int(args.google_num),
        chemspider_api_key=os.getenv("CHEMSPIDER_API_KEY", "").strip(),
    )
    return csv_path, cache_json, output_path, stage_a_output_path, cfg


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # If any CLI parameter is provided (csv/cache/out/stage-a-out/headless flags),
    # we consider it CLI mode; otherwise interactive.
    cli_mode = any(
        [
            args.csv_path,
            args.cache_json,
            args.output_path,
            args.stage_a_output_path,
            args.headless,
            args.no_headless,
            args.requery_not_found,
            args.extra_jitter,
        ]
    )

    if cli_mode:
        csv_path, cache_json, output_path, stage_a_output_path, cfg = cli_inputs(args)
    else:
        csv_path, cache_json, output_path, stage_a_output_path, cfg = interactive_inputs()

    # Build config dict for downstream modules (keeps pipeline/resolvers decoupled from AppConfig class).
    config = {
        "headless": cfg.headless,
        "page_wait_sec": cfg.page_wait_sec,
        "budgets": {
            "chemspider": cfg.chemspider_query_budget,
            "google": cfg.google_query_budget,
            "pubchem": cfg.pubchem_query_budget,
            "opsin": cfg.opsin_query_budget,
        },
        "sleep": {
            "wiki": cfg.wiki_sleep_base,
            "chemspider": cfg.chemspider_sleep_base,
            "google": cfg.google_sleep_base,
            "pubchem": cfg.pubchem_sleep_base,
            "opsin": cfg.opsin_sleep_base,
            "extra_jitter": cfg.extra_jitter,
        },
        "google": {
            "hl": cfg.google_hl,
            "num": cfg.google_num,
        },
        "requery_not_found": cfg.requery_not_found,
        "chemspider_api_key": cfg.chemspider_api_key,
        "http_headers": {
            "User-Agent": cfg.user_agent,
            "Accept": "application/json,text/plain,*/*",
        },
    }

    pipeline.run_pipeline(
        csv_path=csv_path,
        cache_json=cache_json,
        output_path=output_path,
        stage_a_output_path=stage_a_output_path,
        config=config,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        raise SystemExit(130)
    except Exception as e:
        # Keep behavior consistent with a double-click workflow:
        # show error then wait for Enter so the console doesn't vanish.
        print(f"\n[Warning] Fatal error: {repr(e)}")
        if sys.stdin.isatty():
            try:
                input("Press Enter to exit...")
            except Exception:
                pass
        raise
