# ipg-zh

This repository currently contains the approved work through P4: the complete
2024-09-23 official English IPG structure, conservatively migrated legacy
Chinese text, and the bilingual AIPG publication annotations found in
`AIPG_2025.md`. It is a review candidate, not a publishable release.

The authoritative English source is the immutable WPN PDF snapshot. The legacy
`AIPG_2025.md` file is a migration input only and remains byte-for-byte
unchanged. Candidate artifacts are visibly marked and must not be published.

## P4 full legacy migration

P4 rebuilds the seven-file candidate source directly from the immutable PDF
and legacy snapshots, the stable-ID registry, and reviewed mapping overrides.
It does not use an ignored P3 output as an input.

```powershell
ipg-p4 migrate
ipg-p4 validate
ipg-p4 build
ipg-p4 review
# or run the four steps in order
ipg-p4 all
```

The committed source is under
`src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/`. Generated raw
ledgers, parser diagnostics, reports, and the visibly marked preview are kept
under ignored `outputs/p4/`. Formal `dist/` remains untouched.

Candidate validation succeeds with all 4,085 legacy raw units uniquely
disposed and no unresolved mappings, duplicate consumption, orphan annotation,
or deferred annotation. Release validation intentionally fails: 12 Appendix B
entries have no legacy Chinese, the full review ledger does not yet exist,
publication-annotation licensing/attribution is pending, and the manifest is
`publishable: false`.

P4 deliberately does not create a full OmegaT project. That review workflow is
reserved for the next approved phase.

## Historical P0-P2 pilot workflow

```powershell
ipg-pilot parse
ipg-pilot reconcile
ipg-pilot migrate
ipg-pilot omegat-export
ipg-pilot omegat-preview
ipg-pilot omegat-apply --expected-change-count 1
ipg-pilot translation-notes
ipg-pilot review-status
ipg-pilot terms
ipg-pilot validate --profile candidate
ipg-pilot build --profile candidate
ipg-pilot p2-pack
```

These commands reproduce the historical 2.5 vertical slice. `parse` emits
temporary extraction IDs. `reconcile` is a separate identity
step using the prior registry, English evidence, structural context and
explicit overrides before any stable ID reaches source YAML. Generated parser,
migration, report and OmegaT work files live under ignored `outputs/`. The
committed candidate reading artifact lives under `pilot/output/`; `dist/` is
reserved for a future build that has passed `--profile release`.

The three editorial data sets have separate stores and lifecycles:

- published AIPG annotations are part of release YAML and rendered output;
- translator rationale lives in `review/translation-notes/<revision>.json`;
- review state lives in `review/status/<revision>.json`, derived from explicit
  actions in `review/actions/<revision>.yaml`.

Publication annotations may contain multiple bilingual paragraph or list
blocks. Each inner block is a separate OmegaT unit. Repeated component labels
and penalty names are exported once per controlled display code.

## Validation and tests

The validation profile is selected only on the command line:

```powershell
ipg-pilot validate --profile candidate
ipg-pilot validate --profile release
```

The manifest never selects a validation profile. Any future publishing entry
point must invoke `--profile release` explicitly. Candidate validation reports
missing translations, unresolved mappings and review problems without blocking
structurally valid work. Release validation additionally requires exactly one
current review record per OmegaT unit, no unreviewed or stale records, no orphan
records, and matching source and target hashes. Annotation licensing and
attribution remain an independent release gate.

Run all local regression tests with:

```powershell
python -m pytest -q
```

## P3 full official-English parser

P3 uses only the pinned official PDF. It writes the complete reconciled source,
coverage reports, and a visibly non-publishable candidate under ignored
`outputs/p3/`; it does not migrate additional legacy Chinese or annotations.

```powershell
ipg-p3 parse
ipg-p3 reconcile
ipg-p3 build
ipg-p3 review
# or run the four steps in order
ipg-p3 all
```

The committed compact review is `docs/review/p3-full-official-parser.md`. P3 is
kept as the frozen official-structure baseline used by P4.
