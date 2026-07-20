# Custom Agents Guide

Custom agents are specialized AI personas defined as markdown files. They provide focused behavior, tool access, and domain knowledge for specific tasks within FFC projects.

## What Are Custom Agents?

A custom agent is a markdown file placed in `.claude/agents/` within a repository. Each file defines:

- A **role** (what the agent does)
- **Tools** the agent should use
- **Instructions** for behavior, tone, and output format
- **Context** about the domain it operates in

When invoked, the agent overrides the default Claude Code behavior with the specialized persona defined in the file.

## Where Do They Live?

```
.claude/
  agents/
    dns-audit.md
    site-health.md
    pr-reviewer.md
    onboarding.md
    cross-repo-sync.md
```

## How to Invoke a Custom Agent

In Claude Code, use the agent picker or invoke directly:

```
/agent dns-audit
```

Or reference the agent when starting a session:

```bash
claude --agent dns-audit
```

The agent file is loaded and its instructions replace the default system prompt additions for that session.

## FFC Custom Agents

### dns-audit

**File**: `.claude/agents/dns-audit.md`

**Purpose**: Audit DNS records for an FFC-managed domain using the Cloudflare MCP server.

**Capabilities**:
- List all DNS records for a zone
- Compare records against expected values (GitHub Pages CNAME, MX records for M365)
- Flag misconfigurations or missing records
- Generate a summary report

**Typical invocation**:
```
/agent dns-audit
> Audit DNS for legioninthewoods.org
```

---

### site-health

**File**: `.claude/agents/site-health.md`

**Purpose**: Check the health and status of an FFC charity website.

**Capabilities**:
- Load the site in Playwright and check for errors
- Verify SSL certificate validity
- Check page load performance
- Validate that key pages return 200 status
- Screenshot the homepage for visual review

**Typical invocation**:
```
/agent site-health
> Check health of slopestohope.org
```

---

### cross-repo-sync

**File**: `.claude/agents/cross-repo-sync.md`

**Purpose**: Coordinate changes that span multiple FFC repositories.

**Capabilities**:
- Read inventory from repos.json
- Plan multi-repo changes
- Create consistent PRs across repos using the GitHub MCP
- Track sync status

**Typical invocation**:
```
/agent cross-repo-sync
> Update the footer copyright year across all EX repos
```

---

### pr-reviewer

**File**: `.claude/agents/pr-reviewer.md`

**Purpose**: Review pull requests with FFC-specific standards in mind.

**Capabilities**:
- Analyze PR diffs for security issues (exposed tokens, missing .env entries)
- Check adherence to FFC coding conventions
- Verify commit message format (Conventional Commits)
- Suggest improvements

**Typical invocation**:
```
/agent pr-reviewer
> Review PR #42 on FFC-EX-legioninthewoods.org
```

---

### onboarding

**File**: `.claude/agents/onboarding.md`

**Purpose**: Help new FFC volunteers understand the project structure and get started.

**Capabilities**:
- Explain the FFC mission and tech stack
- Walk through repo structure
- Guide setup of development environment
- Point to relevant documentation

**Typical invocation**:
```
/agent onboarding
> I'm new to FFC. Help me get started with the legioninthewoods.org site.
```

## How to Create a New Agent

1. Create a markdown file in `.claude/agents/` with a descriptive name (e.g., `my-agent.md`).

2. Structure the file with these sections:

```markdown
# Agent Name

## Role
Describe what this agent does and its expertise.

## Tools
List the MCP servers or tools this agent should use:
- Cloudflare MCP (for DNS operations)
- Playwright (for browser testing)
- GitHub MCP (for repo operations)

## Instructions
Step-by-step behavioral instructions:
1. Always start by...
2. When asked to...
3. Never...

## Context
Background information the agent needs:
- FFC manages domains via Cloudflare
- Sites are hosted on GitHub Pages or WPMUDEV
- Email is through M365

## Output Format
Describe the expected output format (e.g., markdown table, JSON, summary report).
```

3. Test the agent locally:
```bash
claude --agent my-agent
```

4. Commit to the repo and create a PR.

## How Agents Get Synced to Repos

Custom agents are managed as templates in this repository (`FFC-IN-AI-Management`):

1. Agent templates live in `templates/base/.claude/agents/` (for agents that go to every repo) or `templates/overlays/{type}/.claude/agents/` (for type-specific agents).

2. The `Sync-AIConfigs.ps1` script assembles and deploys agent files to target repos along with all other AI configuration files.

3. Template variables (`{{REPO_NAME}}`, `{{DOMAIN_NAME}}`) are replaced with repo-specific values during sync.

4. Changes are delivered via pull request, never pushed directly to main.

To add a new agent to the fleet:

1. Create the agent markdown file in the appropriate template directory.
2. Run `.\scripts\Sync-AIConfigs.ps1 -All -DryRun` to preview.
3. Run `.\scripts\Sync-AIConfigs.ps1 -All` to deploy via PRs.
