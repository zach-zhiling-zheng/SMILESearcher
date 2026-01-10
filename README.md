
# SMILES Searching Agent

An agentic searcher for resolving chemical names into SMILES,
designed for MOF / Battery materials datasets cleansing.

---

##  Features

- **Agentic multi-source resolution**
  - Wikipedia / Wikidata
  - PubChem (PUG REST)
  - OPSIN (IUPAC name parsing)
  - ChemSpider (web, Selenium)
  - Google AI Overview fallback (web, Selenium)

- **Two-stage pipeline**
  - **Stage A**: offline fill from persistent JSON cache
  - **Stage B**: online resolution of remaining names with budgets & backoff

- **Robust caching**
  - JSON cache with confidence, provenance, trace, and timestamps
  - Backward-compatible with legacy `name → SMILES` mappings

- **MOF-oriented heuristics**
  - Automatic detection of `linker_<n>`, `modulator_<n>`, `metal_<n>` columns
  - Metal ions / simple inorganic salts filtered to avoid low-value queries

- **Graceful degradation**
  - Works without RDKit (heuristic SMILES validation)
  - Works without Selenium (web resolvers automatically skipped)

---

##  Repository Structure

```text
smiles-agentic-resolver/
  start.bat               # One-click Windows launcher
  app.py                  # Entry point (interactive + CLI)
  pipeline.py             # Stage A / Stage B orchestration
  resolvers.py            # Agentic planner + resolvers
  requirements.txt
  README.md

  utils/
    __init__.py
    cache.py               # JSON cache I/O + schema
    dfio.py                # CSV / DataFrame helpers
    smiles.py              # SMILES validation, filters, sleep policy
````

---

##  Quick Start (Recommended)

###  Install dependencies

```bash
pip install -r requirements.txt
```

> Notes:
>
> * `rdkit-pypi` is strongly recommended but optional.
> * `selenium` is required only for ChemSpider / Google fallback.

---

###  One-click interactive run (Windows)

```bat
start.bat
```

You will be prompted for:

* Input CSV path
* JSON cache path
* Whether to run browser in headless mode

This preserves the original research-script workflow.

---

###  Command-line (non-interactive)

```bash
python app.py --csv mof_extraction.csv --cache name2smiles.json --headless
```

Optional flags:

* `--out <output.csv>`
* `--stage-a-out <A_output.csv>`
* `--requery-not-found`
* `--extra-jitter`

---

##  Input / Output Format

### Input CSV

* Expected columns:

  * `linker_1`, `linker_2`, ...
  * `modulator_1`, ...
  * `metal_1`, ...
* Corresponding `*_SMILES_<n>` columns are auto-created if missing.

### Output CSV

* All resolvable names filled into `*_SMILES_<n>`
* Original column order preserved

### Cache (`.json`)

Each entry stores:

```json
{
  "terephthalic acid": {
    "name_raw": "terephthalic acid",
    "smiles": "O=C(O)c1ccc(cc1)C(=O)O",
    "source": "pubchem",
    "status": "found",
    "confidence": 0.93,
    "trace": [...],
    "updated_at": "2025-01-09T12:34:56Z"
  }
}
```

---

##  Resolution Strategy (High-Level)

1. **Normalize & classify name**

   * SMILES-like
   * IUPAC-like
   * Abbreviation
   * Generic

2. **Generate query variants**

   * Abbreviation expansion
   * Hydration removal
   * Hyphen / punctuation normalization

3. **Agentic planning**

   * Tool order depends on name type
   * Budgets enforced per resolver
   * Early stop on high-confidence hits

4. **Cross-validation**

   * Same SMILES from multiple sources → confidence boost

5. **Persist results**

   * All outcomes (found / not_found / filtered / blocked) cached

---

##  Configuration Notes

* Budgets, sleep/backoff, headers, and resolver switches are passed from `app.py`
  to `pipeline.py` and down to `resolvers.py`.
* Selenium is initialized only if needed.
* Google blocking is detected and propagated to avoid repeated captchas.

---

##  Rate Limiting & Ethics

* Deterministic sleep policy
* Conservative default budgets
* Explicit block detection (Google / ChemSpider)
* Intended for **research-scale** usage, not bulk scraping

---

##  Extensibility

You can:

* Disable individual resolvers globally
* Add new resolvers (e.g., ChEMBL, CIR) inside `resolvers.py`
* Reuse `pipeline.run_pipeline(...)` from other projects

---

##  Troubleshooting

### RDKit install fails

You can safely remove `rdkit-pypi` from `requirements.txt`.
The resolver will fall back to heuristic SMILES validation.

### Selenium errors

Ensure:

* Chrome / Chromium is installed
* `webdriver-manager` can download a compatible driver

---

##  License & Disclaimer

This code is provided for **academic and research use**.
Users are responsible for complying with the terms of service of any third-party
websites queried by the resolvers.

---

##  Contact

If you are extending this pipeline or adapting it for new chemical domains,
please document any new heuristics or resolver behaviors to preserve
reproducibility.

