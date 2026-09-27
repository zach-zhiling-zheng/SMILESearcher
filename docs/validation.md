# Validation

Checks performed on 2026-09-27:

| Check | Result |
| --- | --- |
| Offline regression suite | All 77 tests passed. |
| Offline demonstration tables | Both three-row examples passed, including cache fills, exact preservation of valid strings, and replacement of invalid strings. No network requests. |
| Local building-block table with an empty cache | All 2,971 rows, their order, and all 16 columns remained unchanged. The audit recorded 2,153 valid existing structures and 818 unresolved entries. No network requests; the source file was unchanged. |
| MOFinder molecular-weight helper | Retained all 591 reported linker entries and calculated weights for 263 using the local cache. No network requests; both source files and reported weights were unchanged. |
| Public-service checks | All nine lookups matched the expected canonical structures: three compounds tested independently with PubChem, CACTUS, and OPSIN. |

The public test compounds were ethanol, terephthalic acid, and `(R)-2-aminopropanoic acid`. The chiral compound retained the expected stereochemistry. [Recorded responses](validation/provider_smoke.json) include the returned structures; request counters are cumulative within each provider's three-lookups session.

Offline regression tests cover structure validation, stereochemistry, cache compatibility, response handling, retries, request budgets, column mapping, preservation of input values, audits, and checkpoint resume. Run them with:

```bash
python -m unittest discover -s tests -v
```

These checks establish the tested behavior and service connectivity, not an accuracy estimate for arbitrary chemical names. The local table check tested preservation with lookups disabled. Unresolved, ambiguous, or incorrectly named compounds still require chemical review.
