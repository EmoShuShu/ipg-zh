# P2 vertical-slice review pack

This package is a candidate-only review index. It completes P0-P2 and is the mandatory stop point before any full-IPG parser or migration work.

## Contract and pilot sources

- Architecture plan: `docs/plans/2026-10-04-ipg-zh-architecture.html`
- Manifest: `src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/manifest.yaml`
- Pilot YAML: `chapter-02.yaml`, `appendix-a.yaml`, and `appendix-b.yaml` beside the manifest
- Central display values: `src/ipg/display-values.yaml`
- Stable ID registry: `src/ipg/id-registry.yaml`
- Applied and lifecycle override examples: `src/ipg/mapping-overrides.yaml`

## Parsing, provenance, migration, and findings

- Official parsed JSON: `work/parsed/official-pilot.json`
- Golden fixture: `tests/fixtures/pilot/golden-official-pilot.json`
- Golden comparison: `reports/golden-fixture-diff.json` (0 differences)
- PDF provenance summary: `reports/pdf-provenance-summary.json`
- Raw units and unique disposition ledger: `work/migration/raw-units.json`, `work/migration/raw-unit-ledger.json`
- Migration report: `reports/migration-report.json`
  - 4,085/4,085 raw units disposed exactly once
  - 0 duplicate consumption
  - 24 missing legacy mappings and 24 missing translations
  - 523 additional in-scope raw units deliberately deferred and counted as unresolved for release
  - lines 3246/3248 retained as fixed regression observations

## Validation and deterministic outputs

- Candidate validation: `reports/validation-candidate.json` (valid, reviewable, not publishable)
- Actual release validation: `reports/validation-release.json` (expected failure)
- Cleared 2.5 release-path fixture: `reports/release-clean-fixture-report.json` (valid; no artifact published)
- Candidate reading output: `dist/IPG.md`
- Native JSON output: `dist/rules.json`
- External hashes only: `dist/build-report.json`, `dist/SHA256SUMS`
- Two-run comparison: `reports/determinism-report.json`

## OmegaT and independent review data

- OmegaT project: `omegat/ipg-pilot/`
- Source/target PO: `omegat/ipg-pilot/source/ipg-pilot.po`, `omegat/ipg-pilot/target/ipg-pilot.po`
- Mapping and export hashes: `omegat/ipg-pilot/omegat/ipg-pilot.mapping.json`
- Preview report: `reports/omegat-writeback-preview.json`
- Isolated candidate: `work/omegat-candidate/`
- Minimal applied difference: `reports/p2-review-pack/omegat-writeback.diff`
- TMX note input and separate store: `omegat/ipg-pilot/tm/translation-notes.tmx`, `review/translation-notes.json`
- Review actions, ledger, and status: `review/review-actions.yaml`, `review/review-ledger.json`, `reports/review-status.json`
- Shared glossary and advisory audit: `terminology/ipg-glossary.txt`, `reports/terminology-audit.json`

The OmegaT export contains 120 units: 95 official body blocks, 9 titles, 4 publication annotations, and 12 centralized display values. Each display code occurs exactly once. Translation notes and review states are absent from content YAML, `IPG.md`, and `rules.json`.

## Verification and handoff

- Full test result: `reports/test-report.json` (36 passed)
- Local Git summary: `reports/p2-review-pack/git-commit-summary.md`
- Human decisions before P3: `reports/p2-review-pack/human-judgment-required.md`

No GitHub remote, Actions workflow, PR automation, or Release configuration exists. Do not begin P3 until this package is approved.

