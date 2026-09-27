# Examples

These small, invented tables demonstrate name lookup; they are not experimental synthesis records.

| File | Contents |
| --- | --- |
| [mof.csv](mof.csv) | MOF-style linker, modulator, and metal columns. |
| [building_blocks.csv](building_blocks.csv) | Organic building blocks with `canonical_name` and `smiles` columns. |
| [cache.json](cache.json) | A legacy-format name-to-SMILES cache for offline runs. |
| [mofinder_linker_weights.py](mofinder_linker_weights.py) | Compare MOFinder linker weights with weights calculated from validated SMILES. |

From the repository root:

```bash
python -c "import shutil; shutil.copyfile('examples/cache.json', 'example-cache.json')"
python app.py --csv examples/mof.csv --cache example-cache.json --out results/mof.csv --offline
python app.py --csv examples/building_blocks.csv --cache example-cache.json --out results/building_blocks.csv --offline
```

Existing valid SMILES should remain unchanged. The intentionally invalid `invalid_smiles` entries demonstrate replacement from a validated cache value. Inspect the accompanying audit CSVs for each cell's outcome.
