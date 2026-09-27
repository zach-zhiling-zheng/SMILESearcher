# SMILESearcher

Resolve MOF linker and modulator names to validated SMILES and molecular weights. SMILESearcher combines persistent lookup caches, public chemical services, and RDKit validation in one command-line workflow.

- Detect MOF linker and modulator columns, or select columns explicitly.
- Preserve valid existing SMILES; record each lookup and replacement in an audit table.
- Resolve through PubChem, CACTUS, and OPSIN, with optional Wikipedia lookup.
- Run entirely from a local cache with `--offline`, or resume interrupted batches.

## Install

Requires Python 3.10 or later.

```bash
git clone https://github.com/zach-zhiling-zheng/SMILESearcher.git
cd SMILESearcher
python -m pip install -e .
```

RDKit, pandas, and requests are installed as dependencies. A browser and API keys are not required.

## Quick start

```bash
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv
```

Run the demo with 20 frequently reported primary linkers from MOFinder:

```bash
python -c "import shutil; shutil.copyfile('examples/top20_linkers_cache.json', 'example-cache.json')"
python app.py --csv examples/top20_linkers_input.csv --cache example-cache.json --out results/top20_linkers.csv --offline --molecular-weight
```

See the [linker names, counts, SMILES, and molecular weights](examples/top20_linkers.csv) and the [executed notebook](examples/top20_linkers_demo.ipynb). To run it interactively, install `python -m pip install -e ".[notebook]"` and open it in JupyterLab.

## Input columns

| Input | Name column | SMILES column |
| --- | --- | --- |
| MOF linker | `linker_1`, `linker_2`, ... | `linker_SMILES_1`, `linker_SMILES_2`, ... |
| MOF modulator | `modulator_1`, `modulator_2`, ... | `modulator_SMILES_1`, `modulator_SMILES_2`, ... |
| Other tables | Choose with `--name-column` | Choose with `--smiles-column` |

Valid existing strings are preserved exactly. An invalid string is replaced only when a validated resolution is available; otherwise it remains in the output and is flagged in the audit. RDKit parsing checks structure validity, not whether a structure is the correct interpretation of an ambiguous chemical name.

See [usage](docs/usage.md), [resolution methods](docs/methods.md), [validation](docs/validation.md), and the [example files](examples/README.md).

## Molecular weights

Add `--molecular-weight` to calculate average molecular weights in g/mol from validated structures. To compare calculated values with MOFinder's linker molecular-weight table and name-to-SMILES cache:

```bash
python examples/mofinder_linker_weights.py --mofinder-root ../MOFinder --out results/linker_weights.csv
```

The helper runs offline by default and writes a separate table of calculated weights. Reported weights remain unchanged. When running from MOFinder's `SMILESearcher/` submodule, use `--mofinder-root ..`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Acknowledgments and license

Modified from [StarLiu714/SMILESearcher](https://github.com/StarLiu714/SMILESearcher), originally developed by Xinxin Liu, with batch lookup for organic building blocks. The original history and [GNU GPL v3 license](LICENSE) are retained. See [acknowledgments](docs/acknowledgments.md) for the upstream version and changes.
