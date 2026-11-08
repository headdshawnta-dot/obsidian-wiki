"""Pre-ingest identity guard: one source file, one wiki page.

The 2026-07 public batch created ``X.pdf.md`` pages beside existing ``X.md``
pages because it named pages after the source filename and never checked the
extension-stripped identity. This module makes that check deterministic and
runs before ``wiki-ingest`` writes anything.

It is read-only: it answers *which existing page(s) correspond to each source*
and refuses to let an ingest proceed when more than one page matches.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from obsidian_wiki import cache
from obsidian_wiki.batch import (
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    TEXT_EXTENSIONS,
)
from obsidian_wiki.vault import iter_md, split_frontmatter

#: Extensions a source document can carry before it becomes a wiki page.
SOURCE_EXTENSIONS = frozenset(
    PDF_EXTENSIONS | OFFICE_EXTENSIONS | IMAGE_EXTENSIONS | TEXT_EXTENSIONS
)

#: Root bookkeeping pages are not ingest targets.
SYSTEM_PAGES = frozenset({"index.md", "log.md", "hot.md", "_insights.md", "AGENTS.md"})


class GuardError(ValueError):
    """A source cannot be checked because its identity is ambiguous."""


def _normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def canonical_stem(name: str) -> str:
    """Strip a known source extension and compare in a filename-safe form.

    ``X.pdf`` and ``X.pdf.md`` both collapse to ``x``; ``X`` stays ``x``.
    Unicode is NFKC-normalized so a decomposed accent matches its composed
    spelling.
    """
    path = Path(name)
    suffix = path.suffix.lower()
    if suffix in SOURCE_EXTENSIONS:
        name = path.stem
    return _normalize_name(name)


def _clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value.replace("'''", "'")


def _frontmatter_title(frontmatter: str) -> str:
    """Read ``title:`` from the small YAML subset used by wiki pages."""
    lines = frontmatter.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^title\s*:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in {">", ">-", "|", "|-", "|+", ">+"} or not value:
            collected: list[str] = []
            for continuation in lines[index + 1 :]:
                if continuation and not continuation.startswith((" ", "\t")):
                    break
                collected.append(continuation.strip())
            return " ".join(part for part in collected if part).strip()
        return _clean_scalar(value)
    return ""


def _page_identity(path: Path, vault: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, _ = split_frontmatter(text)
    title = _frontmatter_title(frontmatter)
    return {
        "page": path.relative_to(vault).as_posix(),
        "stem": path.stem,
        "title": title,
        "stem_key": canonical_stem(path.stem),
        "title_key": canonical_stem(title) if title else "",
    }


def _source_entries(vault: Path) -> dict[str, dict]:
    """Source-key -> entry for both manifest shapes."""
    raw = cache._load_raw(vault)
    return {
        key: entry
        for key, entry in cache._iter_entries(raw.get("sources", {}))
        if key
    }


def _manifest_matches_source(key: str, source: Path, vault: Path) -> bool:
    """Whether a manifest key names *source* (portable or absolute forms)."""
    try:
        if cache._same_source(key, source, vault):
            return True
    except (OSError, ValueError):
        pass
    return key == str(source)


def _source_hash_matches(entry: dict, source: Path) -> bool:
    expected = cache._strip_algo(entry.get("content_hash") or entry.get("sha256"))
    if not expected or not source.exists():
        return False
    return cache.compute_hash(source) == expected


def check_source(vault: Path, source: Path) -> dict[str, Any]:
    """Return the pre-ingest decision for one source file."""
    source = source.resolve()
    if not source.exists():
        return {
            "source": str(source),
            "source_key": str(source),
            "decision": "invalid",
            "reason": "source does not exist",
            "candidates": [],
        }

    source_key = cache.stored_key(source, vault) or str(source)
    source_stem = canonical_stem(source.name)
    source_name_key = _normalize_name(source.name)
    source_entries = _source_entries(vault)
    manifest_pages: set[str] = set()
    manifest_page_keys: set[str] = set()
    manifest_entry: dict | None = None
    for key, entry in source_entries.items():
        if _manifest_matches_source(key, source, vault):
            manifest_entry = entry
            page_key = entry.get("page_key")
            if isinstance(page_key, str):
                manifest_page_keys.add(page_key)
            for field in ("page_key", "pages_produced", "pages_created", "pages_updated"):
                value = entry.get(field)
                if isinstance(value, str):
                    manifest_pages.add(value)
                elif isinstance(value, list):
                    manifest_pages.update(str(item) for item in value if item)

    candidates: list[dict[str, Any]] = []
    for path in iter_md(vault):
        rel = path.relative_to(vault)
        if path.name in SYSTEM_PAGES or rel.parts and rel.parts[0] in {"_meta", "_raw", "_staging", "_archives"}:
            continue
        page = _page_identity(path, vault)
        signals: list[str] = []
        if page["stem_key"] == source_stem:
            signals.append("stem")
        elif page["stem_key"] == source_name_key:
            signals.append("source-filename")
        if page["title_key"] and page["title_key"] in {source_stem, source_name_key}:
            signals.append("title")
        if page["page"] in manifest_page_keys:
            signals.append("manifest-page-key")
        if signals:
            candidates.append({**page, "signals": sorted(set(signals))})

    if len(candidates) > 1:
        return {
            "source": str(source),
            "source_key": source_key,
            "canonical_stem": source_stem,
            "decision": "blocked",
            "reason": "more than one existing page matches this source",
            "candidates": candidates,
        }

    if len(candidates) == 1:
        page = candidates[0]
        unchanged = bool(manifest_entry and _source_hash_matches(manifest_entry, source))
        return {
            "source": str(source),
            "source_key": source_key,
            "canonical_stem": source_stem,
            "decision": "skip" if unchanged else "update",
            "reason": "one existing page matches; update it instead of creating another",
            "page": page["page"],
            "candidates": candidates,
        }

    missing_manifest_pages = sorted(
        page for page in manifest_pages
        if not (vault / page).exists()
    )
    if missing_manifest_pages:
        return {
            "source": str(source),
            "source_key": source_key,
            "canonical_stem": source_stem,
            "decision": "blocked",
            "reason": "manifest names pages that no longer exist; repair the record before ingest",
            "missing_manifest_pages": missing_manifest_pages,
            "candidates": [],
        }

    return {
        "source": str(source),
        "source_key": source_key,
        "canonical_stem": source_stem,
        "decision": "create",
        "reason": "no existing page matches; create one page",
        "proposed_page_name": source.name[: -len(Path(source.name).suffix)] + ".md"
        if Path(source.name).suffix in SOURCE_EXTENSIONS
        else source.name + ".md",
        "candidates": [],
    }


def _expand_sources(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        path = path.expanduser()
        if path.is_dir():
            expanded.extend(
                child for child in sorted(path.rglob("*"))
                if child.is_file() and child.suffix.lower() in SOURCE_EXTENSIONS
                and not any(part in {".git", "node_modules", "__pycache__"} for part in child.parts)
            )
        else:
            expanded.append(path)
    return expanded


def check_paths(vault: Path, paths: list[Path]) -> dict[str, Any]:
    results = [check_source(vault, source) for source in _expand_sources(paths)]
    blocked = [item for item in results if item["decision"] in {"blocked", "invalid"}]
    return {
        "ok": not blocked,
        "checked": len(results),
        "blocked": len(blocked),
        "results": results,
    }


def result_json(result: dict[str, Any], *, pretty: bool) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2 if pretty else None)
