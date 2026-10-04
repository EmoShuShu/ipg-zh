# ipg-zh pilot

This repository currently contains only the approved P0-P2 local vertical
slice for Chapter 2, a golden sample for section 2.5, and small Appendix A/B
fixtures. It is deliberately not a full-IPG parser or migration.

The authoritative English source is the immutable WPN PDF snapshot. The
legacy `AIPG_2025.md` file is a migration input only and remains byte-for-byte
unchanged. Generated candidate artifacts are visibly marked and must not be
published.

Validation profile is selected by the command line:

```powershell
ipg-pilot validate --profile candidate
ipg-pilot validate --profile release
```

The manifest never selects a validation profile. Any future publishing entry
point must invoke `--profile release` explicitly.

