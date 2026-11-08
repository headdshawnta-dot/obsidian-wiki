"""Tests for the pre-ingest one-source/one-page guard."""

import json
import subprocess
import sys
from pathlib import Path

from obsidian_wiki.cache import compute_hash
from obsidian_wiki.ingest_guard import check_paths, check_source


def _page(vault: Path, rel: str, title: str, sources: list[str] | None = None) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"title: {title}", "category: references"]
    if sources:
        lines.append("sources:")
        lines.extend(f"- {item}" for item in sources)
    lines.extend(["---", "", "# Body", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _manifest(vault: Path, sources: dict) -> None:
    (vault / ".manifest.json").write_text(
        json.dumps({"version": 1, "sources": sources}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_creates_when_no_page_matches(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")

    result = check_source(vault, source)
    assert result["decision"] == "create"
    assert result["proposed_page_name"] == "doc.md"


def test_updates_the_single_matching_page(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/doc.md", "doc")

    result = check_source(vault, source)
    assert result["decision"] == "update"
    assert result["page"] == "references/doc.md"


def test_blocks_base_page_and_suffix_mirror(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/doc.md", "doc")
    _page(vault, "references/doc.pdf.md", "doc.pdf")

    result = check_source(vault, source)
    assert result["decision"] == "blocked"
    assert {item["page"] for item in result["candidates"]} == {
        "references/doc.md",
        "references/doc.pdf.md",
    }


def test_blocks_three_way_duplicate(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/doc.md", "doc")
    _page(vault, "references/doc.pdf.md", "doc.pdf")
    _page(vault, "entities/doc.md", "doc")

    result = check_source(vault, source)
    assert result["decision"] == "blocked"
    assert len(result["candidates"]) == 3


def test_does_not_treat_a_concept_page_source_mention_as_the_page(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "concepts/product-line.md", "产品线", [str(source)])

    result = check_source(vault, source)
    assert result["decision"] == "create"


def test_manifest_page_key_is_strong_identity(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/another-name.md", "Another name")
    _manifest(vault, {
        str(source): {
            "content_hash": f"sha256:{compute_hash(source)}",
            "page_key": "references/another-name.md",
        }
    })

    result = check_source(vault, source)
    assert result["decision"] == "skip"
    assert result["page"] == "references/another-name.md"


def test_same_basename_in_another_directory_is_not_the_same_source(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/another-name.md", "Another name")
    _manifest(vault, {
        "/other/project/doc.pdf": {
            "content_hash": "sha256:" + "0" * 64,
            "page_key": "references/another-name.md",
        }
    })

    result = check_source(vault, source)
    assert result["decision"] == "create"


def test_missing_manifest_page_blocks_instead_of_silently_recreating(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _manifest(vault, {
        str(source): {
            "content_hash": f"sha256:{compute_hash(source)}",
            "page_key": "references/gone.md",
        }
    })

    result = check_source(vault, source)
    assert result["decision"] == "blocked"
    assert result["missing_manifest_pages"] == ["references/gone.md"]


def test_checks_paths_aggregates_blocked_state(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/doc.md", "doc")
    _page(vault, "references/doc.pdf.md", "doc.pdf")

    result = check_paths(vault, [source])
    assert result["ok"] is False
    assert result["blocked"] == 1


def test_skill_requires_the_guard_before_writing():
    skill = (Path(__file__).resolve().parents[1] / ".skills/wiki-ingest/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Step 0.5: Identity Guard" in skill
    assert "obsidian-wiki ingest-guard" in skill
    assert "One source, one primary page" in skill


def test_batch_dispatch_does_not_skip_the_guard():
    skill = (Path(__file__).resolve().parents[1] / ".skills/wiki-ingest/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Step 0.5 onward" in skill
    assert "wiki-ingest Step 1 onward" not in skill


def test_cli_exits_two_when_blocked(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf bytes")
    _page(vault, "references/doc.md", "doc")
    _page(vault, "references/doc.pdf.md", "doc.pdf")

    proc = subprocess.run(
        [sys.executable, "-m", "obsidian_wiki.cli", "ingest-guard",
         str(vault), str(source)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["ok"] is False
    assert data["results"][0]["decision"] == "blocked"
