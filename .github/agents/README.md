# AI Agent Instructions Directory

This directory contains instructions and guidelines for AI agents (GitHub Copilot, ChatGPT, Claude, etc.) working on this repository.

## Purpose

To ensure consistent, secure development practices when AI agents assist with code, documentation, or automation in this repository.

## Files

- **AI_AGENT_INSTRUCTIONS.md** - Comprehensive security guidelines for AI agents to prevent secret exposure and ensure proper credential management

## For AI Agents

If you are an AI agent working on this repository, you **MUST** read and follow the instructions in `AI_AGENT_INSTRUCTIONS.md` before making any changes.

## For Human Developers

These instructions are specifically designed for AI agents to follow. Human developers should refer to:
- `SECURITY.md` - Security policies
- `GITHUB_ACTIONS.md` - CI/CD setup
- `CONTRIBUTING.md` - Contribution guidelines

## Why This Exists

AI agents need explicit, structured guidance to:
1. Prevent accidental exposure of API tokens and secrets
2. Ensure consistent use of GitHub Secrets and environment variables
3. Maintain security best practices across all automated changes
4. Provide clear patterns for handling sensitive information

These instructions help AI agents make secure contributions without requiring constant human oversight of security practices.
