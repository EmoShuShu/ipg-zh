# P2 correction review summary

Scope: local P0-P2 vertical slice only. No full-document parser or migration,
GitHub integration, remote, Actions workflow, or Release configuration is
included.

## Golden sample and migration

- Legacy lines 1707-1842 have 136 unique dispositions: 52 layout lines, 16
  quote separators, 1 bilingual title, 5 structural labels, 17 official
  English blocks, 17 Chinese translations, 14 publication-annotation source
  blocks and 14 annotation translations. There are no deferred or unresolved
  units in the range.
- Section 2.5 has 12 publication annotations and 14 inner bilingual blocks.
  Every annotation is positioned `after` the legacy-adjacent official block.
  Two annotations share the final remedy anchor and render by persistent order
  110 then 120. Lines 1795-1801 remain two English and two Chinese paragraphs.
- All 24 targets in the previous missing-translation report are now mapped.
  The outcome is 24 successful mappings, 0 evidenced true gaps and 0 manual
  unresolved items. The Chapter 2 introduction records all six raw units
  653/655, 665/667 and 673/675.
- Outside 2.5, 86 publication-annotation groups (338 content raw units) are
  explicitly deferred because they are outside the approved golden sample.
  Official Chapter 2 body blocks in the slice were migrated; the deferral is
  not used to hide missing official translations.

## Publication annotations and OmegaT

- Publication annotations contain stable ID, anchor, display position,
  persistent order, optional `appliesTo`, and paragraph/list groups containing
  bilingual blocks.
- OmegaT exports 128 units: controlled display codes once each, titles,
  official body blocks, and 14 separate publication-annotation blocks.
- The writeback demonstration previews exactly one controlled display change,
  records pre/post hashes, and applies only the unchanged isolated candidate.
- Translation notes are versioned at
  `review/translation-notes/zh-r0001.json`; review state is independently
  versioned at `review/status/zh-r0001.json`. Neither store appears in YAML or
  published output.

## Validation and identity evidence

- Candidate validation succeeds with complete findings. The actual pilot is
  intentionally not release-ready: 125 units are unreviewed, one review action
  is stale, and annotation licensing/attribution is pending.
- The clean release fixture contains a complete ledger and passes with every
  release gate at zero. Tests separately prove release failure for a missing
  ledger, unreviewed entries, stale entries, orphan entries, duplicate records,
  and source/target hash mismatches.
- Parsing emits temporary extraction IDs. Reconciliation then uses the registry
  (93 current block evidence records), English hashes, component context and
  explicit overrides. Synthetic version tests cover insertion, deletion with
  permanent retirement, reorder, title change, renumber override, and
  ambiguous evidence.
- Translation-note tests prove that changing only the target makes a note
  stale and that missing TMX time metadata requires an explicit parameter.

## Reproducibility

- 52 tests pass.
- Two clean candidate builds are byte-identical.
- `pilot/output/IPG.md` SHA-256:
  `b068586e8a92045a470fd1f58256ca841f32eaf8ee5724d0f30e92a85856fc9a`.
- `pilot/output/rules.json` SHA-256:
  `5291c14cabff332a716f759cf4f695d27a52e0ffa8a3244cdff725bd1453a3c3`.
- The rules hash is external only, in `SHA256SUMS` and the build report.

## Human review still required

1. Review or explicitly dispose the 125 currently unreviewed OmegaT units and
   re-review the one stale action before any release validation can pass.
2. Resolve licensing and attribution for the migrated AIPG annotations before
   formal publication.
3. The 86 deferred annotation groups require anchor decisions only if and when
   a later full-document phase is approved; they are not a P2 blocker.

Stop condition: P2 correction package complete. Do not enter P3 without new
approval.
