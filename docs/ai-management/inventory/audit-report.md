# AI Configuration Audit Report

> **Historical — superseded.** This is a **point-in-time snapshot** of AI-configuration presence
> across FFC repositories, generated `2026-02-16` by the retired `scripts/Audit-AIConfigs.ps1` from
> the wound-down `FFC-IN-AI-Management` repo. It is **not maintained** and does not reflect the
> current fleet (repos have since been added, renamed, and adopted `.claude/` config). There is no
> live successor inventory for AI-config presence today; this snapshot is retained as a
> clearly-labeled archive for provenance only. Do not treat its rows as current. See
> [`../README.md`](../README.md) for the migration status table and the `inventory/` disposition.

Generated: 2026-02-16 14:47:30

## Summary

| Org                                | Repo                                 | Type             | CLAUDE.md | AGENTS.md | GEMINI.md | copilot-instructions | .claude/settings | .claude/rules | .claude/agents | mcp-config | AI_AGENT_INSTRUCTIONS |
| ---------------------------------- | ------------------------------------ | ---------------- | --------- | --------- | --------- | -------------------- | ---------------- | ------------- | -------------- | ---------- | --------------------- |
| FreeForCharity                     | FFC-IN-AI-Management                 | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-aariasblueelephant.org        | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-Cloudflare-Automation            | powershell-infra | -         | -         | -         | -                    | -                | -             | -              | -          | Yes                   |
| FreeForCharity                     | FFC-IN-Single_Page_Template_Jekell   | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-IN-Single_Page_Template_HTML     | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC_Single_Page_Template             | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-SRRN.net                      | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-thecrookedhouse.net           | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-sporting2Impact.org           | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-technomonasteries.org         | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-nittanypost245.org            | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-slopestohope.org              | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-legioninthewoods.org          | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-IN-ffcadmin.org                  | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-freedomrisingusa.org          | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | freeforcharity-web                   | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-AllTypeTowing.com             | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-bintobetter.org               | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-PAGboosters.org               | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-IN-Zeffy-Management              | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-Static-Site-Capture-Tools        | powershell-infra | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-IN-Antigravity-Static-site-agent | powershell-infra | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-EX-SRRN-Static-site-conversion   | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-Technology-Directory             | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FreeForCharity.org                   | base             | -         | -         | -         | Yes                  | -                | -             | -              | -          | -                     |
| FreeForCharity                     | TechnologyMonastery.org              | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-MicrosoftBot                     | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| FreeForCharity                     | FFC-Discussions                      | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |
| koenig-childhood-cancer-foundation | KCCF-web                             | base             | -         | -         | -         | -                    | -                | -             | -              | -          | -                     |

## Legend

- **Yes**: File or directory is present in the repo
- **-**: Not present

---

_Report generated by `scripts/Audit-AIConfigs.ps1`_
