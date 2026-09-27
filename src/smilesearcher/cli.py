"""Command-line interface. Modified for Zheng Lab, 2026-09-27 (GPL-3.0)."""

import argparse
import json
import sys

from .cache import get_cached_smiles, load_cache_json, now_iso, save_cache_json
from .chemistry import molecular_weight, name_key
from .models import Resolution
from .pipeline import DEFAULTS, run_pipeline
from .resolvers import Resolver


def parser():
    p = argparse.ArgumentParser(description="Resolve chemical names to validated SMILES.")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--csv", help="MOF, building-block, or custom CSV table.")
    modes.add_argument("--name", help="Resolve one chemical name; print a JSON record.")
    p.add_argument("--cache", default="name2smiles.json", help="Persistent JSON cache.")
    p.add_argument("--out", help="Output CSV; default is filled_<input>.csv.")
    p.add_argument("--stage-a-out", help="Optional CSV filled only from the starting cache.")
    p.add_argument("--name-column", help="Explicit chemical-name column.")
    p.add_argument("--smiles-column", help="Explicit SMILES column.")
    p.add_argument("--offline", action="store_true", help="Use the cache without network requests.")
    p.add_argument("--molecular-weight", action="store_true", help="Calculate average molecular weight from valid SMILES (g/mol).")
    p.add_argument("--providers", default=",".join(DEFAULTS["providers"]), help="Ordered, comma-separated providers.")
    p.add_argument("--timeout", type=float, default=20, help="Timeout per HTTP request in seconds.")
    p.add_argument("--delay", type=float, default=0.3, help="Minimum delay between provider requests.")
    p.add_argument("--retries", type=int, default=2, help="Retries for temporary HTTP/network failures.")
    p.add_argument("--max-queries", type=int, default=1000, help="Maximum HTTP attempts per provider, including retries.")
    p.add_argument("--checkpoint-every", type=int, default=50, help="Checkpoint every N rows; 0 saves only at interruption/completion.")
    p.add_argument("--resume", action="store_true", help="Resume the matching output checkpoint.")
    p.add_argument("--requery-not-found", action="store_true", help="Retry cached not-found or ambiguous outcomes.")
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not args.csv and not args.name:
        if argv is None and len(sys.argv) == 1 and sys.stdin.isatty():
            args.csv = input("Input CSV path: ").strip().strip('"')
            args.cache = input("Cache path [name2smiles.json]: ").strip().strip('"') or args.cache
        else:
            p.error("Provide --csv or --name.")
    config = {key: getattr(args, key) for key in DEFAULTS}
    config["providers"] = [x.strip() for x in args.providers.split(",") if x.strip()]
    try:
        if args.csv:
            stats = run_pipeline(csv_path=args.csv, cache_json=args.cache, output_path=args.out,
                                 stage_a_output_path=args.stage_a_out, config=config)
            print(json.dumps(stats, indent=2))
            return 0
        if args.resume or args.out or args.stage_a_out or args.name_column or args.smiles_column:
            p.error("CSV output, column and resume options require --csv.")
        cache = load_cache_json(args.cache, on_conflict="ambiguous")
        old = cache.get(name_key(args.name), {})
        value = get_cached_smiles(cache, args.name)
        if value:
            result = Resolution(name=args.name, smiles=value, source=old.get("source", "cache"), status="found")
        elif old.get("status") in {"not_found", "ambiguous", "filtered"} and not args.requery_not_found:
            result = Resolution(name=args.name, source=old.get("source", "cache"), status=old["status"],
                                note="Cached outcome; use --requery-not-found to retry.")
        elif args.offline:
            result = Resolution(name=args.name, status="not_found", source="cache", note="Offline cache miss.")
        else:
            with Resolver(providers=config["providers"], timeout=args.timeout, delay=args.delay,
                          retries=args.retries, budgets={n: args.max_queries for n in config["providers"]}) as resolver:
                result = resolver.resolve(args.name)
            record = result.to_dict()
            record["updated_at"] = now_iso()
            cache[name_key(args.name)] = record
            save_cache_json(args.cache, cache)
        payload = result.to_dict()
        if args.molecular_weight:
            payload["molecular_weight_g_mol"] = molecular_weight(result.smiles)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.status == "found" else 2
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; completed rows were checkpointed.", file=sys.stderr)
        return 130
