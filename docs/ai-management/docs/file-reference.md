# AI Configuration File Reference

This document maps every AI configuration file to the tools that consume it, explaining what each file does and where it is auto-loaded.

## File-to-Tool Matrix

| File | Claude Code | GitHub Copilot | Gemini | Other Agents |
|------|-------------|----------------|--------|--------------|
| `CLAUDE.md` | Auto-loaded (root + parents + child dirs) | -- | -- | -- |
| `AGENTS.md` | Read if referenced | Read if referenced | Read if referenced | Universal instructions |
| `GEMINI.md` | -- | -- | Auto-loaded | -- |
| `.github/copilot-instructions.md` | -- | Auto-loaded | -- | -- |
| `.claude/settings.json` | Auto-loaded (permissions, MCP config) | -- | -- | -- |
| `.claude/rules/*.md` | Auto-loaded by file path match | -- | -- | -- |
| `.claude/agents/*.md` | Loaded on demand (custom agents) | -- | -- | -- |
| `.copilot/mcp-config.json` | -- | Copilot Agent (MCP server reference) | -- | -- |
| `.github/agents/AI_AGENT_INSTRUCTIONS.md` | -- | Copilot Agent | -- | Other tools can reference |

## Detailed File Descriptions

### CLAUDE.md

- **Location**: Repository root (also checked in parent directories and child directories)
- **Consumer**: Claude Code
- **Behavior**: Automatically loaded every time Claude Code opens a project. The file closest to the working directory takes precedence, but parent `CLAUDE.md` files are also loaded. This is the primary way to give Claude Code project-specific context.
- **Content**: Project description, conventions, architecture notes, do/don't rules.

### AGENTS.md

- **Location**: Repository root
- **Consumer**: All AI tools (universal)
- **Behavior**: Not auto-loaded by any specific tool, but serves as a universal instruction file that any AI agent can reference. Useful for instructions that should apply regardless of which AI tool is being used.
- **Content**: Cross-tool instructions, project rules, architecture overview.

### GEMINI.md

- **Location**: Repository root
- **Consumer**: Google Gemini
- **Behavior**: Auto-loaded by Gemini-based tools when they detect the file in the repository.
- **Content**: Similar to CLAUDE.md but tailored for Gemini's behavior and capabilities.

### .github/copilot-instructions.md

- **Location**: `.github/copilot-instructions.md`
- **Consumer**: GitHub Copilot
- **Behavior**: Auto-loaded by GitHub Copilot when it detects the file. This is GitHub's official mechanism for customizing Copilot behavior per repository.
- **Content**: Coding style preferences, project conventions, technology stack notes.

### .claude/settings.json

- **Location**: `.claude/settings.json`
- **Consumer**: Claude Code
- **Behavior**: Auto-loaded to configure Claude Code permissions (allow/deny lists for tools), MCP server connections, and environment variables.
- **Content**: JSON configuration for permissions and MCP servers.

### .claude/rules/*.md

- **Location**: `.claude/rules/` directory
- **Consumer**: Claude Code
- **Behavior**: Each rule file is auto-loaded based on file path glob patterns. For example, a rule file scoped to `*.ps1` will be loaded when Claude Code is working with PowerShell files. This allows fine-grained, context-sensitive instructions.
- **Content**: Markdown files with specific rules for file types or directories.

### .claude/agents/*.md

- **Location**: `.claude/agents/` directory
- **Consumer**: Claude Code (custom agents)
- **Behavior**: These define custom agent personas that can be invoked on demand. They are not auto-loaded but are available when the user runs a custom agent command.
- **Content**: Agent definition including role, tools, and behavioral instructions.

### .copilot/mcp-config.json

- **Location**: `.copilot/mcp-config.json`
- **Consumer**: GitHub Copilot Agent
- **Behavior**: Configures MCP (Model Context Protocol) servers that Copilot Agent can connect to for extended tool access (e.g., Cloudflare, Sentry).
- **Content**: JSON with MCP server URLs, authentication, and toolset configuration.

### .github/agents/AI_AGENT_INSTRUCTIONS.md

- **Location**: `.github/agents/AI_AGENT_INSTRUCTIONS.md`
- **Consumer**: GitHub Copilot Agent (primary), other tools (secondary)
- **Behavior**: Loaded by Copilot Agent for agentic workflows. Other tools may also reference this file for structured agent instructions.
- **Content**: Detailed agent instructions, available tools, workflow definitions.
