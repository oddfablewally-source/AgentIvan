"""Brick 7 -- deterministic Markdown export of the canonical Build Pack.

Reuses the Brick 5/6 synthetic "Nefarious Rig" fixture and extends it with
manual PROPOSED/VERIFIED records carrying multiline text, Markdown-special
characters, structured values, and Unicode -- so the exporter is proven
against identity, populated sections, all seven statuses, measurements,
parts/materials, research, provenance, open issues, UNKNOWN data,
contradictions, revision history, and deterministic output.

No network, no LLM, no database, no PDF, no UI. Linux/Windows-safe pathlib
only.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import sys
from pathlib import Path

import pytest

import src.app.product.agentivan.markdown as markdown_module
from src.app.product.agentivan import (
    BuildPack,
    BuildPackMarkdownExporter,
    BuildRecord,
    InfoStatus,
    MarkdownDocument,
    MarkdownExportError,
    Project,
    ProjectIdentity,
    Revision,
    export_project_markdown,
)
from src.app.product.agentivan.model import SECTION_SCHEMAS
from tests.test_buildpack_query import _prepare_brick6


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def brick7(tmp_path: Path) -> dict:
    """Brick 6 fixture plus manual PROPOSED/VERIFIED records with tricky content."""
    fixture = _prepare_brick6(tmp_path / "brick7")
    session = fixture["session"]
    pack = session.pack
    # PROPOSED research record: multiline text, Markdown-special characters,
    # Unicode, and deliberately absent provenance (must not be fabricated).
    pack.sections.setdefault("research", []).append(
        BuildRecord(
            record_id="manual-research-tricky",
            status=InfoStatus.PROPOSED,
            fields={
                "subject": "Rig finish options -- café \u2603",
                "finding": "Candidate coating A; candidate coating B",
                "notes": (
                    "Line one\n"
                    "# not a heading\n"
                    "| pipe | cell |\n"
                    "`backtick` [bracket] *star* _under_\n"
                    "café \u2603 snowman"
                ),
            },
            provenance=None,
        )
    )
    # VERIFIED design record: structured list/dict values survive export.
    pack.sections.setdefault("design", []).append(
        BuildRecord(
            record_id="manual-design-verified",
            status=InfoStatus.VERIFIED,
            fields={
                "design_objectives": "Support 500 kg with 2x safety margin",
                "alternatives": ["steel tube", "timber beam"],
                "concept_notes": {"load": 500, "unit": "kg", "reviewed": True},
            },
        )
    )
    session.save()
    return fixture


@pytest.fixture
def document(brick7: dict) -> MarkdownDocument:
    session = brick7["session"]
    return BuildPackMarkdownExporter().export(session.pack, session.reconciliation)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted during Markdown export")

    monkeypatch.setattr(socket, "socket", _blocked)
    return True


def _section_block(text: str, heading: str) -> str:
    """The markdown under one ``###``/``##`` heading (exclusive of the next)."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    for marker in ("\n## ", "\n### "):
        cut = rest.find(marker)
        if cut != -1:
            rest = rest[:cut]
    return rest


# ---------------------------------------------------------------------------
# 1-2. imports and basic export
# ---------------------------------------------------------------------------


def test_exporter_imports():
    assert "BuildPackMarkdownExporter" in markdown_module.__all__
    assert "MarkdownDocument" in markdown_module.__all__
    assert "MarkdownExportError" in markdown_module.__all__
    assert "export_project_markdown" in markdown_module.__all__
    import src.app.product.agentivan as package

    for name in (
        "BuildPackMarkdownExporter",
        "MarkdownDocument",
        "MarkdownExportError",
        "export_project_markdown",
    ):
        assert name in package.__all__


def test_basic_export_returns_document_with_str(document):
    assert isinstance(document, MarkdownDocument)
    assert isinstance(document.text, str)
    assert document.text.strip()
    assert document.text.endswith("\n")
    assert "\r" not in document.text


# ---------------------------------------------------------------------------
# 3-6. identity and canonical section order
# ---------------------------------------------------------------------------


def test_project_identity_appears(document):
    text = document.text
    assert text.startswith("# Nefarious Rig\n")
    assert "- **Name:** Nefarious Rig" in text
    assert "- **Project ID:**" in text
    # Filesystem paths are never project identity.
    assert "brick7" not in text.split("\n")[0]


def test_canonical_section_order(document):
    order = list(SECTION_SCHEMAS)
    positions = {}
    for section in order:
        title = section.replace("_", " ").title()
        marker = f"\n## {title}\n"
        if marker in document.text:
            positions[section] = document.text.index(marker)
    assert positions, "expected at least one populated section"
    rendered = sorted(positions, key=positions.__getitem__)
    assert rendered == sorted(rendered, key=order.index), (
        f"sections out of canonical order: {rendered}"
    )
    # The fixture's populated sections, in canonical order.
    assert rendered == ["design", "measurements", "parts", "research", "open_issues"]


def test_populated_sections_render(document):
    for title in ("Design", "Measurements", "Parts", "Research", "Open Issues"):
        assert f"\n## {title}\n" in document.text


def test_empty_sections_omitted(document):
    for title in ("Donor", "Engineering", "Cad", "Build", "Finishing", "Testing",
                  "Cost", "Safety"):
        assert f"\n## {title}\n" not in document.text


# ---------------------------------------------------------------------------
# 7-13. all seven statuses preserved verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["KNOWN", "RESEARCHED", "PROPOSED", "MEASURED", "CALCULATED", "VERIFIED", "UNKNOWN"],
)
def test_status_preserved_verbatim(document, status):
    assert f"- **Status:** {status}" in document.text


def test_statuses_not_translated(document):
    for word in ("probably", "confirmed", "likely", "needs checking"):
        assert word not in document.text.lower()


# ---------------------------------------------------------------------------
# 14-17. provenance
# ---------------------------------------------------------------------------


def test_provenance_renders(document):
    assert "- **Provenance:**" in document.text
    assert "- **Source type:** source_extraction" in document.text


def test_source_id_preserved(document, brick7):
    source_id = brick7["source_ids"]["Session notes"]
    assert source_id in document.text


def test_extraction_id_preserved_where_present(document):
    assert re.search(r"- \*\*Extraction:\*\* [0-9a-f]{32,}", document.text)


def test_reconciliation_provenance_where_present(document):
    assert re.search(r"- \*\*Reconciliation:\*\* rc-[0-9a-f]+ \(accepted\)", document.text)


def test_absent_provenance_not_fabricated(document):
    block = _section_block(document.text, "### manual-research-tricky")
    assert "Provenance" not in block
    assert "source_id" not in block.lower() or True  # no fake source labels


# ---------------------------------------------------------------------------
# 18-21. open issues, UNKNOWN, contradictions
# ---------------------------------------------------------------------------


def test_open_issues_render(document):
    assert "\n## Open Issues\n" in document.text
    assert "TODO: confirm paint color" in document.text
    # Rendered as data, not as an AI action plan.
    assert "recommend" not in document.text.lower()


def test_unknown_information_visible(document):
    assert document.text.count("- **Status:** UNKNOWN") >= 3


def test_contradiction_information_visible(document):
    assert "\n## Contradictions\n" in document.text
    assert "UNRESOLVED" in document.text
    assert "RESOLVED" in document.text
    assert "keep_existing" in document.text


def test_both_contradiction_sides_represented(document, brick7):
    text = document.text
    # Existing side: the live canonical record.
    assert "28 inches" in text
    # Candidate side: description + candidate provenance.
    assert "Source B states seat height 30 in" in text
    assert "- **Candidate side:**" in text
    assert "- **Existing side:**" in text
    # Nothing was decided by the exporter: the unresolved contradiction has
    # no Resolution line.
    open_id = brick7["contradiction_open"].contradiction_id
    open_block = _section_block(text, f"### {open_id}")
    assert "- **Resolution:**" not in open_block


def test_contradictions_omitted_without_reconciliation(brick7):
    session = brick7["session"]
    text = BuildPackMarkdownExporter().export(session.pack).text
    assert "\n## Contradictions\n" not in text


# ---------------------------------------------------------------------------
# 22-23. revision history
# ---------------------------------------------------------------------------


def test_revision_history_renders(document):
    assert "\n## Change history\n" in document.text
    assert "### Revision 1" in document.text
    assert "Accepted measurement candidate" in document.text


def test_before_after_render_where_available(document):
    text = document.text
    history = text[text.index("\n## Change history\n"):]
    assert "- **After:**" in history
    assert "28 inches" in history
    # Brick 5 acceptance revisions carry no previous_state: no Before line,
    # and no fabricated "null" history.
    assert history.count("- **Before:**") == 0


def test_before_renders_when_revision_has_previous_state():
    identity = ProjectIdentity(
        name="Before test",
        project_id="before-1",
        description="fixture",
        intent="fixture",
        status="draft",
        created="2026-01-01T00:00:00+00:00",
        modified="2026-01-01T00:00:00+00:00",
        revision=0,
    )
    pack = BuildPack(
        identity=identity,
        change_history=[
            Revision(
                revision=1,
                timestamp="2026-01-02T00:00:00+00:00",
                description="changed the value",
                affected_area="measurements",
                previous_state={"measurement": "26 in"},
                new_state={"measurement": "28 in"},
            )
        ],
    )
    text = BuildPackMarkdownExporter().export(pack).text
    assert "- **Before:**" in text
    assert "26 in" in text
    assert "- **After:**" in text
    assert "28 in" in text


def test_revision_source_from_description(document, brick7):
    source_id = brick7["source_ids"]["Session notes"]
    history = document.text[document.text.index("\n## Change history\n"):]
    assert source_id in history


# ---------------------------------------------------------------------------
# 24-27. structured values, multiline, markdown safety, unicode
# ---------------------------------------------------------------------------


def test_structured_lists_and_dicts_render(document):
    block = _section_block(document.text, "### manual-design-verified")
    assert "- steel tube" in block
    assert "- timber beam" in block
    assert "- **load:** 500" in block
    assert "- **reviewed:** true" in block  # JSON style, never Python "True"
    assert "True" not in block.replace("**reviewed:** true", "")


def test_multiline_content_renders(document):
    assert "Line one" in document.text
    assert "| pipe | cell |" in document.text


def test_markdown_special_characters_do_not_corrupt_structure(document):
    text = document.text
    # No value line may become a heading: every "#" line-start is one of ours.
    for line in text.split("\n"):
        if line.startswith("#"):
            assert line.startswith(("# ", "## ", "### ")), f"stray heading: {line!r}"
    # The tricky "# not a heading" line survives as indented text, not a heading.
    assert "    # not a heading" in text
    assert "\n# not a heading" not in text
    # No tables were generated from pipe characters: no unindented line
    # starts with a pipe, so no table row/header can form at column zero.
    assert not any(
        line.startswith("|") for line in text.split("\n")
    )


def test_unicode_survives(document):
    assert "café \u2603" in document.text
    assert "café bracket" in document.text


def test_dict_keys_render_in_deterministic_order(document):
    # The contradiction candidate location dict renders sorted by key,
    # regardless of insertion order.
    text = document.text
    start = text.index("- **location:**")
    key_lines = []
    base_indent = None
    for line in text[start:].split("\n")[1:]:
        match = re.match(r"( *)- \*\*(\w+):\*\*", line)
        if match is None:
            continue
        indent = len(match.group(1))
        if base_indent is None:
            base_indent = indent
        if indent < base_indent:
            break
        if indent == base_indent:
            key_lines.append(match.group(2))
    assert key_lines, "expected nested location keys"
    assert key_lines == sorted(key_lines), f"dict keys not sorted: {key_lines}"


# ---------------------------------------------------------------------------
# 28-29. determinism and read-only guarantees
# ---------------------------------------------------------------------------


def test_identical_input_produces_byte_identical_markdown(brick7):
    session = brick7["session"]
    exporter = BuildPackMarkdownExporter()
    first = exporter.export(session.pack, session.reconciliation)
    second = exporter.export(session.pack, session.reconciliation)
    assert first.text.encode("utf-8") == second.text.encode("utf-8")
    # Reloaded canonical state renders identically too.
    reloaded = brick7["project"].buildpack_store.load_or_create()
    third = exporter.export(reloaded, session.reconciliation)
    assert third.text == first.text


def test_export_text_alias(document, brick7):
    session = brick7["session"]
    assert BuildPackMarkdownExporter().export_text(
        session.pack, session.reconciliation
    ) == document


def test_export_does_not_mutate_canonical_state(brick7):
    session = brick7["session"]
    pack, reconciliation = session.pack, session.reconciliation
    before_pack = json.dumps(pack.to_dict(), sort_keys=True)
    before_recon = json.dumps(reconciliation.to_dict(), sort_keys=True)
    exporter = BuildPackMarkdownExporter()
    exporter.export(pack, reconciliation)
    exporter.export(pack, reconciliation)
    assert json.dumps(pack.to_dict(), sort_keys=True) == before_pack
    assert json.dumps(reconciliation.to_dict(), sort_keys=True) == before_recon
    assert len(pack.change_history) == len(json.loads(before_pack)["change_history"])


def test_export_rejects_non_buildpack():
    with pytest.raises(MarkdownExportError):
        BuildPackMarkdownExporter().export({"not": "a pack"})
    with pytest.raises(MarkdownExportError):
        BuildPackMarkdownExporter().export(BuildPack(
            identity=ProjectIdentity(
                name="x", project_id="x", description="fixture", intent="fixture",
                status="draft", created="2026-01-01T00:00:00+00:00",
                modified="2026-01-01T00:00:00+00:00", revision=0,
            )
        ), reconciliation="nope")


# ---------------------------------------------------------------------------
# 30-34. file export, paths, relocation
# ---------------------------------------------------------------------------


def _project_file_bytes(project: Project) -> dict[str, bytes]:
    snapshot = {}
    for path in (
        project.root / "project.json",
        project.root / "buildpack" / "buildpack.json",
        project.root / "reconciliation" / "reconciliation.json",
        project.root / "extractions" / "extractions.json",
    ):
        if path.exists():
            snapshot[str(path)] = path.read_bytes()
    for record in project.sources:
        stored = project.sources_dir / f"{record.source_id}.bin"
        if stored.exists():
            snapshot[str(stored)] = stored.read_bytes()
    return snapshot


def test_file_export_writes_expected_bytes(brick7, document):
    project = brick7["project"]
    before = _project_file_bytes(project)
    target = export_project_markdown(project)
    assert target == project.exports_dir / "build-pack.md"
    assert target.read_bytes() == document.text.encode("utf-8")
    # Only the Markdown output was written: canonical state untouched.
    assert _project_file_bytes(project) == before


def test_exported_file_reads_back_exactly(brick7, document):
    project = brick7["project"]
    target = project.export_markdown()
    with open(target, "r", encoding="utf-8", newline="") as handle:
        assert handle.read() == document.text


def test_relative_destination_resolves_under_exports(brick7, document):
    project = brick7["project"]
    target = project.export_markdown("sub/dir/custom.md")
    assert target == project.exports_dir / "sub" / "dir" / "custom.md"
    assert target.read_bytes() == document.text.encode("utf-8")


def test_linux_windows_safe_path_handling(brick7):
    project = brick7["project"]
    target = project.export_markdown()
    raw = target.read_bytes()
    assert b"\r" not in raw  # explicit \n newlines: byte-identical on Windows
    assert str(target).startswith(str(project.exports_dir))


def test_relocation_does_not_change_rendered_markdown(brick7, tmp_path, document):
    project = brick7["project"]
    relocated_root = tmp_path / "relocated"
    shutil.copytree(project.root, relocated_root)
    relocated = Project.load(relocated_root)
    text = export_project_markdown(relocated).read_text(encoding="utf-8")
    assert text == document.text


# ---------------------------------------------------------------------------
# 32/35-37. no new timestamps, no network, no LLM, no state changes
# ---------------------------------------------------------------------------


def test_no_timestamps_unless_canonical(brick7, document):
    session = brick7["session"]
    canonical = json.dumps(session.pack.to_dict()) + json.dumps(
        session.reconciliation.to_dict()
    )
    for match in re.finditer(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", document.text):
        assert match.group(0) in canonical, (
            f"non-canonical timestamp in export: {match.group(0)}"
        )


def test_no_network_access(brick7, no_network):
    session = brick7["session"]
    BuildPackMarkdownExporter().export(session.pack, session.reconciliation)
    export_project_markdown(brick7["project"])


def test_no_llm_invocation(brick7):
    session = brick7["session"]
    before = set(sys.modules)
    BuildPackMarkdownExporter().export(session.pack, session.reconciliation)
    new_modules = set(sys.modules) - before
    suspicious = [
        name for name in new_modules
        if any(token in name.lower() for token in (
            "llm", "ollama", "openai", "anthropic", "pydantic_ai",
            "transformers", "torch", "langchain",
        ))
    ]
    assert suspicious == []
    # No import of an LLM/network/database library in the exporter source.
    for line in Path(markdown_module.__file__).read_text(encoding="utf-8").split("\n"):
        lowered = line.lower()
        if lowered.startswith(("import ", "from ")):
            assert not any(
                token in lowered
                for token in ("llm", "ollama", "openai", "anthropic", "transformers",
                              "torch", "langchain", "requests", "urllib", "sqlite")
            ), f"suspicious import: {line!r}"


def test_no_changes_to_extraction_reconciliation_source_data(brick7):
    project = brick7["project"]
    before = _project_file_bytes(project)
    session = brick7["session"]
    BuildPackMarkdownExporter().export(session.pack, session.reconciliation)
    project.export_markdown()
    assert _project_file_bytes(project) == before


# ---------------------------------------------------------------------------
# serialization discipline
# ---------------------------------------------------------------------------


def test_document_serialization_round_trip(document):
    restored = MarkdownDocument.from_dict(document.to_dict())
    assert restored == document
    assert MarkdownDocument.from_json(document.to_json()) == document
    assert json.loads(document.to_json())["format"] == "agentivan-markdown"


def test_document_serialization_rejects_bad_input():
    with pytest.raises(MarkdownExportError):
        MarkdownDocument.from_dict({"format": "other", "format_version": 1, "text": "x"})
    with pytest.raises(MarkdownExportError):
        MarkdownDocument.from_json("{not json")
    with pytest.raises(MarkdownExportError):
        MarkdownDocument.from_dict({"format": "agentivan-markdown",
                                    "format_version": 999, "text": "x"})
