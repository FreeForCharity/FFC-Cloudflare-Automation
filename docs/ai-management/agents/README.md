# Custom Claude Code Agent Definitions

This directory contains custom agent definitions for Claude Code. Each markdown file
defines a specialized agent that can be invoked from a Claude Code session.

## What Are Custom Agents?

Claude Code supports custom agents defined as markdown files in a repository's
`.claude/agents/` directory. Each agent file contains specialized instructions that
focus the AI on a specific task -- DNS auditing, site health checks, code review, etc.

When a user invokes a custom agent, Claude Code loads the agent's instructions and
operates within that specialized context.

## Available Agents

| Agent | File | Purpose |
|-------|------|---------|
| dns-audit | `dns-audit.md` | Audit Cloudflare DNS records for FFC charity domains |
| site-health | `site-health.md` | Validate GitHub Pages deployments for FFC websites |
| cross-repo-sync | `cross-repo-sync.md` | Check AI config status across all FFC repositories |
| pr-reviewer | `pr-reviewer.md` | Review PRs with FFC-specific awareness |
| onboarding | `onboarding.md` | Guide setup of a new charity website in the FFC system |
| copilot-review-cycle | `copilot-review-cycle.md` | Automate Copilot code review feedback loop until clean |

## How These Are Deployed

The sync script (`scripts/Sync-AIConfigs.ps1`) copies these agent files into each
target repository's `.claude/agents/` directory. This ensures every FFC repo has
access to the same set of specialized agents.

## Creating a New Agent

1. Create a new `.md` file in this directory.
2. Start with a clear `# Agent Name` heading.
3. Define the agent's purpose, context, and expected behavior.
4. Include specific checks, expected values, and output formats.
5. Add the agent to the table above.
6. Run the sync script to deploy to target repos.

## File Format

Each agent file should follow this structure:

```markdown
# Agent Name

## Purpose
What this agent does and when to use it.

## Context
Background information the agent needs.

## Instructions
Step-by-step behavior the agent should follow.

## Expected Output
What the agent should produce (tables, reports, etc.).
```
