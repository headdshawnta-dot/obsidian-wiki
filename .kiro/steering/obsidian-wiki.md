---
inclusion: always
---

# Obsidian Wiki — Agent Context

This project is a **skill-based framework** for building and maintaining an Obsidian knowledge base.

## Quick Orientation

1. Resolve config via `AGENTS.md`: honor an inline `@name` vault override first, then `.env`, then the global config (`~/.config/obsidian-wiki/config`, XDG-style; legacy `~/.obsidian-wiki/config` still honored). This gives `OBSIDIAN_VAULT_PATH` — where the wiki lives.
2. Read `.manifest.json` at the vault root to see what's already been ingested.
3. Skills are in `.skills/` (also at `.kiro/skills/`). Each subfolder has a `SKILL.md`.

## When to Use Skills

| User says something like… | Read this skill |
|---|---|
| "set up my wiki" / "initialize" | `wiki-setup` |
| "ingest" / "add this to the wiki" / "process these docs" / "process this export" / "ingest this data" | `wiki-ingest` |
| "ingest a conversation export or transcript" | `wiki-ingest` |
| "search an agent's available history for a topic" | `wiki-agent` |
| "what's the status" / "show the delta" | `wiki-status` |
| "what do I know about X" | `wiki-query` |
| "use my vault as context" / "context pack for X" / "bounded context" | `wiki-context-pack` |
| "audit" / "lint" / "find broken links" | `wiki-lint` |
| "rebuild" / "archive" / "restore" | `wiki-rebuild` |
| "link my pages" / "cross-reference" | `cross-linker` |
| "fix my tags" | `tag-taxonomy` |
| "update wiki" / "sync to wiki" | `wiki-update` |
| "export wiki" / "export graph" | `wiki-export` |

## Core Rules

- **Compile, don't retrieve** — update existing pages, don't append or duplicate.
- **Track everything** — update `.manifest.json`, `index.md`, and `log.md` after every operation.
- **Connect with `[[wikilinks]]`** — every page should link to related pages.
- **Frontmatter required** — every page needs `title`, `category`, `tags`, `sources`, `created`, `updated`.

For full context, read `AGENTS.md` at the repo root.
