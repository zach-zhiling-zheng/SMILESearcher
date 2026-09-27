# Resolution methods

Each detected name/SMILES pair is processed independently. A valid existing SMILES string is retained exactly. For a missing or invalid value, the resolver checks the persistent cache before querying enabled providers in order.

The default providers are PubChem, the NCI Chemical Identifier Resolver (CACTUS), and OPSIN. Wikipedia/Wikidata lookup is optional. Returned candidates must pass RDKit parsing before they can fill or replace a table value. Cached and newly resolved structures are written as canonical isomeric SMILES; existing valid input strings are preserved exactly.

The first validated provider result is used. Multiple distinct structures from one provider or an OPSIN parsing warning produce an ambiguous outcome requiring review. If no validated result is available, the original value is retained and the outcome is recorded in the audit.

## Chemical interpretation

RDKit validation establishes that a candidate can be parsed as a molecular structure. It does not verify that the name and structure refer to the same compound, establish experimental identity, or resolve every synonym, abbreviation, hydrate, salt, or stereoisomer. A cached result carries the same identity limitations as its source.

Public services can differ in their interpretation of names. Keep the audit and cache with the processed table so that resolved identities can be reviewed. No accuracy rate is inferred from successful parsing.

Molecular weights use RDKit `Descriptors.MolWt`, which gives average molecular weight for the full SMILES, including every disconnected fragment. Salt form, hydration, and protonation therefore affect the calculated value. The MOFinder comparison helper retains reported weights separately from calculated weights.

## Repeated runs

Cached resolutions reduce repeated requests across tables. Conflicting cached identities remain unresolved for review. Offline runs make no provider requests. A saved checkpoint binds progress to the input content and resolution settings; resume checks these before reusing it. Per-cell audit records distinguish retained values, successful replacements, and unresolved entries.

## Changes from the original workflow

This implementation combines MOF reagent-column handling with organic building-block lookup and explicit column selection. It uses direct service requests, requires RDKit validation, and provides per-cell audits, bounded requests, and resumable output. It does not depend on browser automation.
