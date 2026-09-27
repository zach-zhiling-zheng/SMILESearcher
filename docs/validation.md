# Validation

Checks performed on 2026-09-27:

| Check | Result |
| --- | --- |
| Offline regression suite | All 77 tests passed. |
| Linker demonstration | Twenty linker structures matched across PubChem, OPSIN, and CACTUS; the offline demo reproduced all SMILES and molecular weights. |
| MOFinder molecular-weight helper | Retained all 591 reported linker entries and calculated weights for 263 using the local cache. No network requests; both source files and reported weights were unchanged. |
| Public-service checks | All nine lookups matched the expected canonical structures: three compounds tested independently with PubChem, CACTUS, and OPSIN. |

The public test compounds were ethanol, terephthalic acid, and `(R)-2-aminopropanoic acid`. The chiral compound retained the expected stereochemistry. [Recorded responses](validation/provider_smoke.json) include the returned structures; request counters are cumulative within each provider's three-lookups session.

Offline regression tests cover structure validation, stereochemistry, cache compatibility, response handling, retries, request budgets, column mapping, preservation of input values, audits, and checkpoint resume. Run them with:

```bash
python -m unittest discover -s tests -v
```

These checks establish the tested behavior and service connectivity, not an accuracy estimate for arbitrary chemical names. Unresolved, ambiguous, or incorrectly named compounds still require chemical review.
