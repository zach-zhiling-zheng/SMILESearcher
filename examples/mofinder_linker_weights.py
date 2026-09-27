"""Calculate linker molecular weights from MOFinder names and cached structures.

The reported lookup is kept unchanged; output is a separate review table.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smilesearcher import run_pipeline
from smilesearcher.cache import atomic_write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mofinder-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("results/linker_weights.csv"))
    parser.add_argument("--online", action="store_true", help="Query providers for names missing from the cache.")
    args = parser.parse_args(argv)
    data = args.mofinder_root.resolve() / "data"
    source = data / "organic_linker_info/linker_molecular_weights.csv"
    source_cache = data / "name_SMILES_mappers/name2smiles_1222.json"
    output = args.out.resolve()
    prepared = Path(str(output) + ".input.csv")
    cache = Path(str(output) + ".cache.json")
    if any(p == protected for p in (output, prepared, cache) for protected in (source, source_cache)):
        parser.error("Output paths must be separate from MOFinder source files.")
    frame = pd.read_csv(source, header=None, names=["linker_name", "reported_mw_g_mol"],
                        dtype=str, keep_default_na=False, encoding="utf-8-sig")
    frame["smiles"] = ""
    atomic_write(prepared, frame.to_csv(index=False).encode("utf-8-sig"))
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_cache, cache)
    stats = run_pipeline(csv_path=prepared, cache_json=cache, output_path=output,
                         config={"name_column": "linker_name", "smiles_column": "smiles",
                                 "molecular_weight": True, "offline": not args.online})
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
