# Linker example

The [executed notebook](top20_linkers_demo.ipynb) shows SMILES lookup and molecular-weight calculation for 20 frequently reported primary linkers from MOFinder.

| File | Contents |
| --- | --- |
| [top20_linkers_input.csv](top20_linkers_input.csv) | Linker names and synthesis-record frequencies. |
| [top20_linkers_cache.json](top20_linkers_cache.json) | Verified structures from public chemical services. |
| [top20_linkers.csv](top20_linkers.csv) | SMILES and calculated molecular weights in g/mol. |
| [mofinder_linker_weights.py](mofinder_linker_weights.py) | Calculate weights using MOFinder's linker lookup and structure cache. |

From the repository root:

```bash
python -c "import shutil; shutil.copyfile('examples/top20_linkers_cache.json', 'example-cache.json')"
python app.py --csv examples/top20_linkers_input.csv --cache example-cache.json --out results/top20_linkers.csv --offline --molecular-weight
```

Frequencies count positive synthesis records by primary linker. [Source information](top20_linkers_source.json) identifies the MOFinder table.

Install the notebook dependencies with `python -m pip install -e ".[notebook]"` and open `examples/top20_linkers_demo.ipynb` in JupyterLab. The notebook runs offline by default. Set `REFRESH_ONLINE = True` to query the public services again.
