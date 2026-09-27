# Usage

Run the following commands from the repository root after `python -m pip install -e .`.

## Resolve a table

```bash
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv
```

MOF `linker_N` and `modulator_N` columns are detected automatically. Missing SMILES columns are created. For another schema, select a pair explicitly:

```bash
python app.py --csv compounds.csv --name-column compound --smiles-column structure --cache name_to_smiles.json --out resolved.csv
```

To resolve one name and print its result as JSON:

```bash
python app.py --name "terephthalic acid" --cache name_to_smiles.json
```

A successful single-name lookup exits with code 0; an unresolved name exits with code 2.

## Calculate molecular weights

```bash
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv --molecular-weight
```

Weights are calculated with RDKit from validated SMILES and reported in g/mol. MOF fields use `linker_MW_N`, `modulator_MW_N`, and `metal_MW_N`; a `smiles` column uses `molecular_weight_g_mol`. Other explicit SMILES columns receive a matching `_mw_g_mol` suffix. Single-name results include `molecular_weight_g_mol` when requested.

To read the MOFinder linker table and its name-to-SMILES cache:

```bash
python examples/mofinder_linker_weights.py --mofinder-root ../MOFinder --out results/linker_weights.csv
```

The helper runs offline by default and keeps `reported_mw_g_mol` alongside calculated weights in a separate output table. It does not overwrite MOFinder's source values. From inside the MOFinder `SMILESearcher/` submodule, use `--mofinder-root ..`.

## Control requests

```bash
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv --providers pubchem,cactus,opsin --timeout 20 --delay 1 --retries 2 --max-queries 100
```

| Option | Effect |
| --- | --- |
| `--offline` | Use local values and cache entries without making network requests. |
| `--providers` | Choose the ordered providers; Wikipedia is opt-in. |
| `--timeout` | Set the request timeout in seconds. |
| `--delay` | Set the delay between provider requests in seconds. |
| `--retries` | Set the retry limit for transient request failures. |
| `--max-queries` | Limit HTTP requests per provider, including retry attempts. |
| `--requery-not-found` | Retry names previously cached without a match. |

Online resolution sends chemical names to the selected public services. Offline mode is suitable for tables that should remain local.

## Resume a batch

```bash
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv --checkpoint-every 25
python app.py --csv compounds.csv --cache name_to_smiles.json --out resolved.csv --checkpoint-every 25 --resume
```

Resume with the same input and resolution settings. The checkpoint verifies the input hash and configuration before continuing. Cache, output, and checkpoint files are written atomically.

## Read the outputs

- **Output CSV:** input rows with resolved SMILES columns; valid existing SMILES are preserved.
- **Audit CSV (`resolved.csv.audit.csv`):** per-cell resolution, source, and replacement information, including unresolved invalid values.
- **Run summary (`resolved.csv.summary.json`):** processing and request counts.
- **Cache JSON:** reusable resolution records. Legacy `{ "name": "SMILES" }` dictionaries are accepted as well as richer records.

Review unresolved names and ambiguous identities before using the results in chemical analyses. The [methods](methods.md) describe what validation establishes.
