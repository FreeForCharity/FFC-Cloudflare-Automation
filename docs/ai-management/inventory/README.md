# Inventory snapshot (historical archive)

> **Historical — superseded.** Everything in this directory is a **point-in-time snapshot** of
> AI-configuration presence across FFC repositories, generated `2026-02-16` by the retired
> `scripts/Audit-AIConfigs.ps1` from the wound-down `FFC-IN-AI-Management` repo. It is **not
> maintained** and no live successor inventory for AI-config presence exists today. The snapshot is
> retained as a clearly-labeled archive for provenance only — do not treat its contents as current.
> See [`../README.md`](../README.md) for the migration status table.

## Contents

| File              | What                                                                   |
| ----------------- | ---------------------------------------------------------------------- |
| `audit-report.md` | Human-readable presence matrix (CLAUDE.md / AGENTS.md / copilot / …)   |
| `repos.json`      | Machine-readable form of the same audit (`lastAudit` timestamp inside) |

`repos.json` is left **byte-for-byte** as generated (a data artifact carries no prose banner); this
README is the archive label for it. Both files describe the org as it stood on the `lastAudit` date,
not the present fleet — repositories have since been added, renamed, and adopted `.claude/` config
that this snapshot predates.
