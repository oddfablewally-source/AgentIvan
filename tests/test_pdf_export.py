"""Brick 8 tests: deterministic Build Pack PDF export.

Reuses the Brick 5/6/7 synthetic "Nefarious Rig" fixture and extends it with
the Brick 7 manual PROPOSED/VERIFIED records (multiline text, Unicode,
Markdown-special characters, structured values, absent provenance).

PDF text inspection uses a minimal stdlib-only extractor
(``_extract_pdf_text``) that understands exactly what ReportLab 4.x emits
for these documents: ASCII85+Flate content streams, literal ``(...)`` text,
hex ``<...>`` text drawn with subsetted TrueType fonts, and ToUnicode CMaps
(``beginbfchar``/``beginbfrange``). It is a test-only validation aid -- it
never touches product code paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

import src.app.product.agentivan.pdf as pdf_module
from src.app.product.agentivan import (
    BuildPackPDFExporter,
    PDFDocument,
    PDFExportError,
    export_project_pdf,
)
from src.app.product.agentivan.model import SECTION_SCHEMAS, BuildRecord, InfoStatus
from src.app.product.agentivan.query import BuildPackQuery
from tests.test_buildpack_query import _prepare_brick6


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------


def _prepare_brick8(root: Path) -> dict:
    """Brick 6 fixture plus the Brick 7 manual PROPOSED/VERIFIED records."""
    fixture = _prepare_brick6(root)
    session = fixture["session"]
    pack = session.pack
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
def brick8(tmp_path: Path) -> dict:
    return _prepare_brick8(tmp_path / "brick8")


@pytest.fixture
def exporter() -> BuildPackPDFExporter:
    return BuildPackPDFExporter()


@pytest.fixture
def document(brick8: dict, exporter: BuildPackPDFExporter) -> PDFDocument:
    session = brick8["session"]
    return exporter.export(session.pack, session.reconciliation)


@pytest.fixture
def pdf_text(document: PDFDocument) -> str:
    return _extract_pdf_text(document.data)


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted during PDF export")

    monkeypatch.setattr(socket, "socket", _blocked)
    return True


# ---------------------------------------------------------------------------
# minimal stdlib PDF text extractor (test-only)
# ---------------------------------------------------------------------------


def _pdf_objects(data: bytes) -> dict[int, bytes]:
    objects: dict[int, bytes] = {}
    for match in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", data, re.S):
        objects[int(match.group(1))] = match.group(3)
    return objects


def _pdf_stream(body: bytes) -> bytes | None:
    match = re.search(rb"stream\r?\n", body)
    if not match:
        return None
    return body[match.end() : body.rfind(b"endstream")]


def _decode_stream(body: bytes, raw: bytes) -> bytes:
    filters = re.findall(rb"/(ASCII85Decode|FlateDecode)", body)
    data = raw.strip()
    for name in filters:
        if name == b"ASCII85Decode":
            if data.endswith(b"~>"):
                data = data[:-2]
            data = base64.a85decode(data, adobe=False)
        elif name == b"FlateDecode":
            data = zlib.decompress(data)
    return data


def _parse_cmap(raw: bytes) -> tuple[set[int], dict[int, str]]:
    text = raw.decode("latin-1")
    code_lens: set[int] = set()
    for first, _second in re.findall(
        r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\nendcodespacerange", text
    ):
        code_lens.add(len(first) // 2)
    mapping: dict[int, str] = {}

    def _ustr(hexstr: str) -> str:
        return bytes.fromhex(hexstr).decode("utf-16-be", errors="replace")

    for block in re.findall(r"\d+\s+beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[int(src, 16)] = _ustr(dst)
    for block in re.findall(r"\d+\s+beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
        ):
            base = _ustr(dst)
            for offset, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                mapping[code] = chr(ord(base[0]) + offset) if len(base) == 1 else base
        for lo, hi, array in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block, re.S
        ):
            dsts = re.findall(r"<([0-9A-Fa-f]+)>", array)
            for offset, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                if offset < len(dsts):
                    mapping[code] = _ustr(dsts[offset])
    return code_lens or {1}, mapping


def _decode_literal(
    raw: bytes, mapping: dict[int, str] | None = None
) -> str:
    """Decode a PDF literal string.

    When ``mapping`` (a font's ToUnicode CMap) is given, each byte is a
    subset glyph code and is translated through it; otherwise bytes decode
    as PDFDocEncoding (latin-1 covers everything this project emits).
    """
    out: list[str] = []
    i = 0

    def _emit(code: int) -> None:
        out.append(mapping.get(code, chr(code)) if mapping else chr(code))

    while i < len(raw):
        byte = raw[i : i + 1]
        if byte != b"\\":
            _emit(byte[0])
            i += 1
            continue
        nxt = raw[i + 1 : i + 2]
        if not nxt:
            i += 2
            continue
        char = nxt.decode("latin-1")
        if char in "nrtbf":
            # Whitespace escapes denote real control characters, not glyphs.
            out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}[char])
            i += 2
        elif char in "()\\":
            _emit(ord(char))
            i += 2
        elif char in "01234567":
            digits = ""
            for k in range(1, 4):
                digit = raw[i + k : i + k + 1].decode("latin-1")
                if digit in "01234567":
                    digits += digit
                else:
                    break
            _emit(int(digits, 8))
            i += 1 + len(digits)
        else:
            i += 2
    return "".join(out)


def _extract_pdf_text(data: bytes) -> str:
    """Extract visible text from an AgentIvan-generated PDF (stdlib only).

    Understands the ReportLab 4.x output this project produces: literal and
    hex text-showing operators in ASCII85+Flate content streams, with
    subsetted TrueType text decoded through each font's ToUnicode CMap.
    Raises ``AssertionError`` on structural problems so tests fail loudly
    rather than asserting against an empty string.
    """
    objects = _pdf_objects(data)
    assert objects, "no PDF objects found"

    font_cmaps: dict[int, tuple[set[int], dict[int, str]]] = {}
    for number, body in objects.items():
        match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
        if match:
            raw = _pdf_stream(objects[int(match.group(1))])
            assert raw is not None, "ToUnicode stream missing"
            raw = _decode_stream(objects[int(match.group(1))], raw)
            font_cmaps[number] = _parse_cmap(raw)

    resource_maps: dict[str, tuple[set[int], dict[int, str]]] = {}
    for number, body in objects.items():
        if not re.search(rb"/Type\s*/Page\b", body):
            continue
        font_dict_body = body
        match = re.search(rb"/Font\s+(\d+)\s+0\s+R", body)
        if match:
            font_dict_body = objects[int(match.group(1))]
        for name, font_number in re.findall(
            r"/([\w+.\-]+)\s+(\d+)\s+0\s+R", font_dict_body.decode("latin-1")
        ):
            if int(font_number) in font_cmaps:
                resource_maps[name] = font_cmaps[int(font_number)]

    def _decode_hex(hexstr: str, code_lens: set[int], mapping: dict[int, str]) -> str:
        raw = bytes.fromhex(hexstr)
        out: list[str] = []
        i = 0
        while i < len(raw):
            for length in sorted(code_lens, reverse=True):
                if i + length > len(raw):
                    continue
                code = int.from_bytes(raw[i : i + length], "big")
                if code in mapping:
                    out.append(mapping[code])
                    i += length
                    break
            else:
                out.append("\ufffd")
                i += 1
        return "".join(out)

    texts: list[str] = []
    found_text_op = False
    for number, body in objects.items():
        if re.search(rb"/Type\s*/Page\b", body):
            continue
        raw = _pdf_stream(body)
        if raw is None or b"beginbfchar" in raw or b"beginbfrange" in raw:
            continue
        try:
            raw = _decode_stream(body, raw)
        except Exception:
            continue
        if not re.search(rb"\bTj\b|\bTJ\b", raw):
            continue
        current: tuple[set[int], dict[int, str]] | None = None
        for op in re.finditer(
            rb"/([\w+.\-]+)\s+[\d.]+\s+Tf"
            rb"|<([0-9A-Fa-f]+)>\s*Tj"
            rb"|\(((?:[^()\\]|\\.)*)\)\s*Tj"
            rb"|\[((?:<[^>]*>|\([^)]*\)|-?[\d.]+|\s)*)\]\s*TJ",
            raw,
        ):
            found_text_op = True
            if op.group(1):
                current = resource_maps.get(op.group(1).decode())
            elif op.group(2) is not None:
                if current:
                    texts.append(_decode_hex(op.group(2).decode(), *current) + "\n")
            elif op.group(3) is not None:
                mapping = current[1] if current else None
                texts.append(_decode_literal(op.group(3), mapping) + "\n")
            elif op.group(4) is not None:
                mapping = current[1] if current else None
                code_lens = current[0] if current else {1}
                for part in re.finditer(
                    rb"<([0-9A-Fa-f]+)>|\(((?:[^()\\]|\\.)*)\)", op.group(4)
                ):
                    if part.group(1) is not None:
                        if current:
                            texts.append(
                                _decode_hex(part.group(1).decode(), code_lens, mapping)
                            )
                    else:
                        texts.append(_decode_literal(part.group(2), mapping))
                texts.append("\n")
    assert found_text_op, "no text-showing operators found in PDF"
    return "".join(texts)


def _pdf_page_count(data: bytes) -> int:
    match = re.search(rb"/Count\s+(\d+)\s*/Kids", data)
    assert match, "no /Pages /Count found"
    return int(match.group(1))


# ---------------------------------------------------------------------------
# 1-4: exporter imports, basic export, result type, PDF header
# ---------------------------------------------------------------------------


def test_exporter_imports():
    assert BuildPackPDFExporter is not None
    assert PDFDocument is not None
    assert PDFExportError is not None
    assert export_project_pdf is not None


def test_basic_export_succeeds(brick8, exporter):
    session = brick8["session"]
    document = exporter.export(session.pack, session.reconciliation)
    assert isinstance(document, PDFDocument)


def test_output_is_pdf_result_with_bytes(document):
    assert isinstance(document.data, bytes)
    assert len(document.data) > 0
    assert isinstance(document.page_count, int) and document.page_count >= 1


def test_pdf_signature_header_valid(document):
    assert document.data.startswith(b"%PDF-")
    assert document.data.rstrip().endswith(b"%%EOF")


# ---------------------------------------------------------------------------
# 5-8: identity, section order, populated/empty sections
# ---------------------------------------------------------------------------


def test_project_identity_appears(pdf_text):
    assert "Nefarious Rig" in pdf_text


def test_canonical_section_order(pdf_text):
    order = list(SECTION_SCHEMAS)
    titles = {
        section: section.replace("_", " ").title() for section in order
    }
    positions = {
        section: pdf_text.find(titles[section])
        for section in order
        if titles[section] in pdf_text
    }
    assert len(positions) >= 2
    ordered = sorted(positions, key=lambda s: order.index(s))
    assert list(positions) == ordered


def test_populated_sections_render(pdf_text, brick8):
    pack = brick8["session"].pack
    for section, records in pack.sections.items():
        if records:
            assert section.replace("_", " ").title() in pdf_text


def test_empty_sections_follow_documented_behavior(pdf_text, brick8):
    # Documented behavior (BRICK-8-PDF-EXPORT.md): empty sections are omitted.
    pack = brick8["session"].pack
    empty = [s for s in SECTION_SCHEMAS if not pack.sections.get(s)]
    assert empty, "fixture should leave at least one section empty"
    for section in empty:
        assert section.replace("_", " ").title() not in pdf_text


# ---------------------------------------------------------------------------
# 9-15: all seven statuses verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["KNOWN", "RESEARCHED", "PROPOSED", "MEASURED", "CALCULATED", "VERIFIED", "UNKNOWN"]
)
def test_status_preserved_verbatim(pdf_text, status):
    assert status in pdf_text


def test_statuses_not_translated(pdf_text):
    lowered = pdf_text.lower()
    assert "confirmed" not in lowered
    assert "estimated" not in lowered


# ---------------------------------------------------------------------------
# 16-19: provenance
# ---------------------------------------------------------------------------


def test_provenance_appears(pdf_text):
    assert "Provenance" in pdf_text


def test_source_id_preserved(pdf_text, brick8):
    # The architecture's source identifier is `source_reference` (this is
    # what BuildPackQuery.by_source(source_id) matches against).
    pack = brick8["session"].pack
    expected = None
    for records in pack.sections.values():
        for record in records:
            if record.provenance and record.provenance.source_reference:
                expected = record.provenance.source_reference
                break
    assert expected, "fixture has no source_reference to check"
    assert expected in pdf_text


def test_extraction_id_preserved_where_present(pdf_text, brick8):
    pack = brick8["session"].pack
    expected = None
    for records in pack.sections.values():
        for record in records:
            notes = (record.provenance.notes if record.provenance else "") or ""
            match = re.search(r"extraction_id=([0-9a-f]+)", notes)
            if match:
                expected = match.group(1)
                break
    assert expected, "fixture has no extraction_id to check"
    assert expected in pdf_text


def test_reconciliation_linkage_preserved_where_present(pdf_text, brick8):
    reconciliation = brick8["session"].reconciliation
    accepted = [c for c in reconciliation.candidates if c.state.value == "accepted"]
    assert accepted, "fixture has no accepted candidates to check"
    assert "Reconciliation" in pdf_text
    assert accepted[0].reconciliation_id in pdf_text


def test_absent_provenance_not_fabricated(pdf_text):
    # The manual PROPOSED record has provenance=None: the block belonging
    # to that record must not invent a source. Bound the block by the next
    # top-level section so later records' real provenance cannot leak in.
    start = pdf_text.find("manual-research-tricky")
    assert start != -1
    rest = pdf_text[start:]
    cuts = [
        rest.find(heading)
        for heading in ("Open Issues", "Contradictions", "Change history")
        if rest.find(heading) != -1
    ]
    block = rest[: min(cuts)] if cuts else rest
    assert "Provenance:" not in block
    assert "Source type" not in block


# ---------------------------------------------------------------------------
# 20-23: open issues, UNKNOWN, contradictions
# ---------------------------------------------------------------------------


def test_open_issues_appear(pdf_text, brick8):
    pack = brick8["session"].pack
    assert pack.sections.get("open_issues"), "fixture needs an open issue"
    assert "Open Issues" in pdf_text
    assert pack.sections["open_issues"][0].record_id in pdf_text


def test_unknown_information_visible(pdf_text):
    assert "UNKNOWN" in pdf_text


def test_contradictions_remain_visible(pdf_text, brick8):
    reconciliation = brick8["session"].reconciliation
    assert reconciliation.contradictions, "fixture needs contradictions"
    assert "Contradictions" in pdf_text
    assert "UNRESOLVED" in pdf_text


def test_both_contradiction_sides_appear(pdf_text, brick8):
    reconciliation = brick8["session"].reconciliation
    unresolved = [c for c in reconciliation.contradictions if c.state.value == "UNRESOLVED"]
    assert unresolved, "fixture needs an unresolved contradiction"
    contradiction = unresolved[0]
    assert contradiction.contradiction_id in pdf_text
    assert "Existing side" in pdf_text
    assert "Candidate side" in pdf_text
    if contradiction.existing_record_id:
        assert contradiction.existing_record_id in pdf_text


# ---------------------------------------------------------------------------
# 24-25: revision history, before/after
# ---------------------------------------------------------------------------


def test_revision_history_appears(pdf_text, brick8):
    pack = brick8["session"].pack
    assert pack.change_history, "fixture needs revisions"
    assert "Change history" in pdf_text
    assert "Revision 1" in pdf_text


def test_before_after_information_where_available(pdf_text, brick8):
    # The fixture's revisions carry new_state snapshots (no previous_state);
    # the "After" side must render wherever the canonical data has it.
    pack = brick8["session"].pack
    assert any(r.new_state is not None for r in pack.change_history)
    assert "After" in pdf_text


def test_before_renders_when_previous_state_present(exporter):
    # The "Before" path is exercised with a synthetic revision because the
    # fixture's revisions only carry new_state.
    from src.app.product.agentivan.model import (
        BuildPack,
        ProjectIdentity,
        Revision,
    )

    pack = BuildPack(
        identity=ProjectIdentity(
            name="Before Probe",
            project_id="before-probe-1",
            description="synthetic",
            intent="test",
            status="active",
            created="2026-01-01T00:00:00+00:00",
            modified="2026-01-01T00:00:00+00:00",
            revision=1,
        ),
        change_history=[
            Revision(
                revision=1,
                timestamp="2026-01-02T03:04:05+00:00",
                description="changed the paint color",
                affected_area="design",
                previous_state={"color": "red"},
                new_state={"color": "blue"},
            )
        ],
    )
    text = _extract_pdf_text(exporter.export(pack).data)
    assert "Before" in text
    assert "After" in text
    assert "red" in text
    assert "blue" in text


# ---------------------------------------------------------------------------
# 26-29: structured values, multiline, unicode, special characters
# ---------------------------------------------------------------------------


def test_structured_lists_and_dicts_survive(pdf_text):
    assert "steel tube" in pdf_text
    assert "timber beam" in pdf_text
    assert "500" in pdf_text
    assert "true" in pdf_text  # JSON-style boolean, not Python repr


def test_multiline_text_survives(pdf_text):
    assert "Line one" in pdf_text
    assert "# not a heading" in pdf_text


def test_unicode_survives(pdf_text):
    assert "café \u2603" in pdf_text


def test_special_characters_survive(pdf_text):
    assert "| pipe | cell |" in pdf_text
    assert "`backtick`" in pdf_text
    assert "*star*" in pdf_text


# ---------------------------------------------------------------------------
# 30-31: pagination and byte determinism
# ---------------------------------------------------------------------------


def test_multi_page_document_generation(document):
    assert document.page_count >= 2
    assert _pdf_page_count(document.data) == document.page_count


def test_repeated_identical_input_produces_identical_bytes(brick8, exporter):
    session = brick8["session"]
    first = exporter.export(session.pack, session.reconciliation)
    second = exporter.export(session.pack, session.reconciliation)
    assert first.data == second.data
    assert hashlib.sha256(first.data).hexdigest() == hashlib.sha256(second.data).hexdigest()


def test_determinism_across_processes(brick8, tmp_path):
    project_root = brick8["project"].root
    script = (
        "import sys; sys.path.insert(0, 'src'); sys.path.insert(0, '.');"
        "from src.app.product.agentivan.project import Project;"
        "from src.app.product.agentivan import BuildPackPDFExporter;"
        f"p = Project.load({str(project_root)!r});"
        "pack = p.buildpack_store.load_or_create();"
        "rec = p.reconciliation_store.load_or_empty();"
        "sys.stdout.buffer.write(BuildPackPDFExporter().export(pack, rec).data)"
    )
    first = subprocess.run([sys.executable, "-c", script], capture_output=True, check=True).stdout
    second = subprocess.run([sys.executable, "-c", script], capture_output=True, check=True).stdout
    session = brick8["session"]
    direct = BuildPackPDFExporter().export(session.pack, session.reconciliation).data
    assert first == second == direct


# ---------------------------------------------------------------------------
# 32: read-only guarantee
# ---------------------------------------------------------------------------


def test_export_does_not_mutate_state(brick8, exporter, tmp_path):
    session = brick8["session"]
    project = brick8["project"]
    pack_before = session.pack.to_dict()
    reconciliation_before = session.reconciliation.to_dict()
    extraction_before = None
    if brick8.get("extraction_set") is not None:
        extraction_before = brick8["extraction_set"].to_dict()
    project_files_before = {
        path: path.read_bytes()
        for path in sorted(project.root.rglob("*"))
        if path.is_file() and "__pycache__" not in str(path)
    }
    exporter.export(session.pack, session.reconciliation)
    assert session.pack.to_dict() == pack_before
    assert session.reconciliation.to_dict() == reconciliation_before
    if extraction_before is not None:
        assert brick8["extraction_set"].to_dict() == extraction_before
    project_files_after = {
        path: path.read_bytes()
        for path in sorted(project.root.rglob("*"))
        if path.is_file() and "__pycache__" not in str(path)
    }
    assert project_files_after == project_files_before


# ---------------------------------------------------------------------------
# 33-34: file export
# ---------------------------------------------------------------------------


def test_file_export_writes_expected_bytes(brick8):
    project = brick8["project"]
    session = brick8["session"]
    expected = BuildPackPDFExporter().export(session.pack, session.reconciliation).data
    target = project.export_pdf()
    assert target == project.exports_dir / "build-pack.pdf"
    assert target.read_bytes() == expected


def test_exported_file_can_be_read_back(brick8):
    target = brick8["project"].export_pdf()
    data = target.read_bytes()
    assert data.startswith(b"%PDF-")
    assert _pdf_page_count(data) >= 2
    text = _extract_pdf_text(data)
    assert "Nefarious Rig" in text


def test_relative_destination_follows_exports_convention(brick8):
    project = brick8["project"]
    target = project.export_pdf("reports/summary.pdf")
    assert target == project.exports_dir / "reports" / "summary.pdf"
    assert target.is_file()


# ---------------------------------------------------------------------------
# 35-36: no timestamps, deterministic metadata
# ---------------------------------------------------------------------------


def test_no_current_timestamp_injected(document):
    import datetime

    year = str(datetime.datetime.now().year)
    # Canonical project dates (Created/Modified) are project data and may
    # legitimately appear as text; the export itself must not inject a
    # current-time PDF date. The invariant date is fixed instead.
    assert b"D:" + year.encode() not in document.data
    assert b"D:20000101000000" in document.data  # fixed invariant date


def test_deterministic_metadata_behavior(document):
    data = document.data
    for marker, expected in (
        (b"/Title", b"(Nefarious Rig)"),
        (b"/Creator", b"(AgentIvan)"),
        (b"/Author", b"()"),
        (b"/Subject", b"(AgentIvan Build Pack)"),
    ):
        assert marker in data
        assert expected in data
    assert b"/CreationDate (D:20000101000000" in data
    assert b"/ModDate (D:20000101000000" in data


# ---------------------------------------------------------------------------
# 37-38: relocation, cross-platform destinations
# ---------------------------------------------------------------------------


def test_relocation_does_not_change_output(brick8, tmp_path):
    from src.app.product.agentivan.project import Project

    session = brick8["session"]
    original = BuildPackPDFExporter().export(session.pack, session.reconciliation).data
    relocated_root = tmp_path / "relocated"
    shutil.copytree(brick8["project"].root, relocated_root)
    relocated = Project.load(relocated_root)
    pack = relocated.buildpack_store.load_or_create()
    reconciliation = relocated.reconciliation_store.load_or_empty()
    assert BuildPackPDFExporter().export(pack, reconciliation).data == original


def test_linux_windows_safe_destination_handling(brick8):
    project = brick8["project"]
    target = export_project_pdf(project, Path("nested") / "out.pdf")
    assert target == project.exports_dir / "nested" / "out.pdf"
    assert target.is_file()
    absolute = project.exports_dir / "absolute.pdf"
    assert export_project_pdf(project, absolute) == absolute


# ---------------------------------------------------------------------------
# 39-40: no network, no LLM
# ---------------------------------------------------------------------------


def test_no_network_access(brick8, exporter, no_network):
    session = brick8["session"]
    exporter.export(session.pack, session.reconciliation)  # raises if socket used


def test_no_llm_invocation():
    source = Path(pdf_module.__file__).read_text(encoding="utf-8")
    # The module docstring legitimately says "no LLM"; check the code body.
    parts = source.split('"""')
    body = "".join(parts[2:]) if len(parts) > 2 else source
    lowered = body.lower()
    for token in ("ollama", "pydantic_ai", "openai", "anthropic", "embedding"):
        assert token not in lowered, f"forbidden token in pdf.py: {token}"
    assert "import llm" not in lowered
    assert "llm." not in lowered.replace("no llm", "")


# ---------------------------------------------------------------------------
# 41-42: Brick 7 and canonical data untouched
# ---------------------------------------------------------------------------


def test_no_changes_to_brick7_behavior(brick8):
    from src.app.product.agentivan import BuildPackMarkdownExporter

    session = brick8["session"]
    exporter = BuildPackMarkdownExporter()
    before = exporter.export_text(session.pack, session.reconciliation).text
    BuildPackPDFExporter().export(session.pack, session.reconciliation)
    after = exporter.export_text(session.pack, session.reconciliation).text
    assert before == after


def test_no_changes_to_source_extraction_reconciliation_data(brick8, exporter):
    session = brick8["session"]
    project = brick8["project"]
    sources_before = [record.to_dict() for record in project.sources]
    pack_before = session.pack.to_dict()
    reconciliation_before = session.reconciliation.to_dict()
    exporter.export(session.pack, session.reconciliation)
    assert [record.to_dict() for record in project.sources] == sources_before
    assert session.pack.to_dict() == pack_before
    assert session.reconciliation.to_dict() == reconciliation_before


# ---------------------------------------------------------------------------
# result type, validation, misc contract
# ---------------------------------------------------------------------------


def test_pdfdocument_serialization_round_trip(document):
    restored = PDFDocument.from_dict(document.to_dict())
    assert restored == document
    restored_json = PDFDocument.from_json(document.to_json())
    assert restored_json == document


def test_pdfdocument_rejects_tampered_checksum(document):
    payload = document.to_dict()
    payload["sha256"] = "0" * 64
    with pytest.raises(PDFExportError):
        PDFDocument.from_dict(payload)


def test_export_rejects_invalid_input(exporter):
    with pytest.raises(PDFExportError):
        exporter.export("not-a-pack")
    with pytest.raises(PDFExportError):
        exporter.export(None)


def test_page_numbers_are_deterministic(pdf_text, document):
    assert "Page 1" in pdf_text
    assert f"Page {document.page_count}" in pdf_text


def test_document_title_from_identity(document):
    assert b"/Title (Nefarious Rig)" in document.data


def test_query_layer_still_read_only_after_pdf(brick8, exporter):
    session = brick8["session"]
    query = BuildPackQuery(session.pack, reconciliation=session.reconciliation)
    before = query.what_do_we_know().to_dict()
    exporter.export(session.pack, session.reconciliation)
    assert query.what_do_we_know().to_dict() == before
