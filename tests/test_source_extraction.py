"""
Focused tests for the AgentIvan source ingestion + structured extraction
layer (Brick 4).

These prove the extraction layer is a conservative, read-only,
provenance-preserving view over raw sources:

- deterministic text chunking with predictable Markdown boundaries
- conservative measurement identification with original expressions kept
- explicit status markers recognized; ambiguous prose never promoted
- JSON fields extracted with paths; URLs/references never fetched
- provenance (source_id, filename, heading, chunk, offsets) on items
- serializable, loadable, repeatable results with content-derived ids
- read-only: sources, hashes, project.json, and the Build Pack untouched
- honest handling of images/binary; failures recorded, not swallowed
- relocation-safe, unicode-safe, platform-neutral storage
- LLM extractor exists only as an unimplemented extension point

They do not modify the existing 264-test baseline. No network, no LLM,
no model calls, no OS-specific shell. Windows runtime verification
remains pending (see BRICK-4-SOURCE-INGESTION.md).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.app.product.agentivan import (
    BuildPack,
    CandidateCategory,
    DeterministicExtractor,
    ExtractedItem,
    ExtractionError,
    ExtractionMethod,
    ExtractionNotAvailableError,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionSet,
    ExtractionStore,
    Extractor,
    InfoStatus,
    LLMExtractor,
    Project,
    ProjectIdentity,
    ReviewState,
    SourceKind,
    SourceLocation,
    TextChunk,
    chunk_json,
    chunk_text,
    get_extractor,
    make_extraction_id,
)
from src.app.product.agentivan.extraction import _strip_timestamps

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "agentivan_synthetic_project"

MARKDOWN_DOC = """# Nefarious Rig — Build Notes

Project: Nefarious Rig

## Measurements

Seat height measured at 28 inches.
Rail width 42 mm.
I think the seat should be 30 inches.
Seat height is 28 inches.
Weight 12.5 kg [MEASURED]
Gap 5 m.
Everything looks verified.

## Parts

Part: steel bracket
Material: 6061 aluminum

TODO: confirm donor year
UNKNOWN: fastener size

https://example.com/oddfable/manual
"""

JSON_DOC = json.dumps(
    {
        "project": "Nefarious",
        "seat_height_in": 28,
        "tags": ["a", "b"],
        "nested": {"x": 1},
    }
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _project(tmp_path: Path, name: str = "Extraction Test") -> Project:
    return Project.create(name, tmp_path / "proj")


def _rich_project(tmp_path: Path) -> Project:
    """Project exercising every Brick 4 source kind."""
    project = _project(tmp_path)
    project.intake_text(
        MARKDOWN_DOC,
        label="Build notes",
        filename="notes/build.md",
        media_type="text/markdown; charset=utf-8",
    )
    project.intake_text(
        JSON_DOC,
        label="Spec",
        filename="spec.json",
        media_type="application/json",
    )
    project.intake_reference("https://example.com/manual", label="Manual")
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(range(256)))
    project.intake_file(blob, label="Binary blob")
    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    project.intake_file(png, label="Photo")
    project.save()
    return project


def _fixture_copy(tmp_path: Path) -> Project:
    dest = tmp_path / "fixture-proj"
    shutil.copytree(FIXTURE_PROJECT, dest)
    return Project.load(dest)


def _items_by_text(extraction_set: ExtractionSet) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for item in extraction_set.all_items():
        grouped.setdefault(item.text, []).append(item)
    return grouped


def _source_hashes(project: Project) -> dict[str, str]:
    hashes = {}
    for record in project.sources:
        if record.relative_path:
            data = project.catalog.read_bytes(record.source_id)
            hashes[record.source_id] = hashlib.sha256(data).hexdigest()
    return hashes


# ---------------------------------------------------------------------------
# 1-2. chunking
# ---------------------------------------------------------------------------
def test_chunking_splits_text_into_predictable_chunks():
    chunks = chunk_text("src1", MARKDOWN_DOC)
    assert chunks
    # Chunk ids are deterministic and zero-padded.
    assert [c.chunk_id for c in chunks] == [
        f"src1:chunk:{i:04d}" for i in range(len(chunks))
    ]
    # Offsets always slice the original text exactly.
    for chunk in chunks:
        assert chunk.text == MARKDOWN_DOC[chunk.char_start : chunk.char_end]
    # Chunks are ordered and non-overlapping.
    spans = [(c.char_start, c.char_end) for c in chunks]
    assert spans == sorted(spans)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start


def test_chunking_is_deterministic_across_runs():
    first = [c.to_dict() for c in chunk_text("src1", MARKDOWN_DOC)]
    second = [c.to_dict() for c in chunk_text("src1", MARKDOWN_DOC)]
    assert first == second


def test_markdown_headings_create_predictable_boundaries():
    chunks = chunk_text("src1", MARKDOWN_DOC)
    headings = [c.text for c in chunks if c.heading and c.text.startswith("#")]
    assert headings == [
        "# Nefarious Rig — Build Notes",
        "## Measurements",
        "## Parts",
    ]
    # The heading line is its own chunk and attributes following chunks.
    measurements = next(c for c in chunks if c.text == "## Measurements")
    assert measurements.heading == "Measurements"
    following = chunks[chunks.index(measurements) + 1]
    assert following.heading == "Measurements"
    assert "28 inches" in following.text
    # A "#hashtag" line without a space is not a heading.
    chunks = chunk_text("src1", "#hashtag is just text\n\nnext")
    assert all(c.heading is None for c in chunks)


def test_chunking_handles_mixed_line_endings_without_normalizing():
    text = "line one\r\nline two\rline three\nline four"
    chunks = chunk_text("src1", text)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == text
    assert text[chunk.char_start : chunk.char_end] == text


def test_chunking_empty_text_yields_no_chunks():
    assert chunk_text("src1", "") == []
    assert chunk_text("src1", "  \n\r\n \t\n") == []


# ---------------------------------------------------------------------------
# 3-6. measurements and status markers
# ---------------------------------------------------------------------------
def _measurement_items(project: Project) -> list:
    extraction_set = project.extractions.run(DeterministicExtractor())
    return [
        item
        for item in extraction_set.all_items()
        if item.category is CandidateCategory.MEASUREMENT
    ]


def test_explicit_measurements_identified(tmp_path):
    project = _rich_project(tmp_path)
    expressions = sorted(
        item.text for item in _measurement_items(project)
    )
    assert expressions == [
        "12.5 kg",
        "28 inches",
        "28 inches",
        "30 inches",
        "42 mm",
        "5 m",
    ]


def test_original_measurement_expression_preserved(tmp_path):
    project = _rich_project(tmp_path)
    by_text = _items_by_text(project.extractions.run(DeterministicExtractor()))
    item = next(
        i
        for i in by_text["28 inches"]
        if i.status is InfoStatus.MEASURED
    )
    # Original expression verbatim; normalized form kept separately.
    assert item.text == "28 inches"
    assert item.normalized == {"value": 28.0, "unit": "in"}
    item_mm = by_text["42 mm"][0]
    assert item_mm.text == "42 mm"
    assert item_mm.normalized == {"value": 42.0, "unit": "mm"}


def test_explicit_status_markers_recognized(tmp_path):
    project = _rich_project(tmp_path)
    by_text = _items_by_text(project.extractions.run(DeterministicExtractor()))
    marker = next(
        i
        for i in by_text["Weight 12.5 kg [MEASURED]"]
        if i.category is CandidateCategory.GENERAL
    )
    assert marker.status is InfoStatus.MEASURED
    unknown = by_text["UNKNOWN: fastener size"][0]
    assert unknown.status is InfoStatus.UNKNOWN
    assert unknown.category is CandidateCategory.GENERAL


def test_ambiguous_statements_not_falsely_promoted(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    by_text = _items_by_text(extraction_set)
    # "Seat height is 28 inches." states a number without establishing a
    # status: it must not become VERIFIED (or anything else).
    plain = next(
        i for i in by_text["28 inches"] if i.status is None
    )
    assert plain.category is CandidateCategory.MEASUREMENT
    assert plain.status is None
    # "I think the seat should be 30 inches." is a proposal, not a fact.
    proposed = by_text["30 inches"][0]
    assert proposed.status is InfoStatus.PROPOSED
    assert proposed.status is not InfoStatus.MEASURED
    # "Everything looks verified." sounds confident but carries no
    # explicit marker: VERIFIED must never be inferred from phrasing.
    assert not [
        i for i in extraction_set.all_items() if i.status is InfoStatus.VERIFIED
    ]


def test_measured_keyword_gives_measured_not_verified(tmp_path):
    project = _rich_project(tmp_path)
    by_text = _items_by_text(project.extractions.run(DeterministicExtractor()))
    measured = next(
        i
        for i in by_text["28 inches"]
        if i.status is InfoStatus.MEASURED
    )
    assert measured.text == "28 inches"


def test_short_ambiguous_units_get_reduced_confidence(tmp_path):
    project = _rich_project(tmp_path)
    by_text = _items_by_text(project.extractions.run(DeterministicExtractor()))
    assert by_text["5 m"][0].confidence == 0.8
    assert by_text["42 mm"][0].confidence == 1.0
    assert by_text["28 inches"][0].confidence == 1.0


def test_confidence_and_status_stay_separate_concepts(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    # High confidence does not imply a strong status ...
    plain = next(
        i
        for i in extraction_set.all_items()
        if i.text == "42 mm"
    )
    assert plain.confidence == 1.0
    assert plain.status is None
    # ... and a status never comes from confidence alone.
    for item in extraction_set.all_items():
        if item.status is not None:
            assert item.status in set(InfoStatus)


# ---------------------------------------------------------------------------
# labeled declarations, todos, urls
# ---------------------------------------------------------------------------
def test_labeled_declarations_categorized(tmp_path):
    project = _rich_project(tmp_path)
    by_text = _items_by_text(project.extractions.run(DeterministicExtractor()))
    assert by_text["Project: Nefarious Rig"][0].category is (
        CandidateCategory.PROJECT_IDENTITY
    )
    assert by_text["Part: steel bracket"][0].category is CandidateCategory.PART
    assert by_text["Material: 6061 aluminum"][0].category is (
        CandidateCategory.MATERIAL
    )
    todo = by_text["TODO: confirm donor year"][0]
    assert todo.category is CandidateCategory.OPEN_ISSUE
    assert todo.status is None


def test_urls_identified_without_fetching(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    by_text = _items_by_text(extraction_set)
    url = by_text["https://example.com/oddfable/manual"][0]
    assert url.category is CandidateCategory.RESEARCH
    assert url.status is None
    # The reference source is recorded as a candidate, never fetched.
    ref_record = next(
        r for r in project.sources if r.kind is SourceKind.REFERENCE
    )
    result = extraction_set.for_source(ref_record.source_id)
    assert result.outcome is ExtractionOutcome.SUCCESS
    assert len(result.items) == 1
    ref_item = result.items[0]
    assert ref_item.text == "https://example.com/manual"
    assert ref_item.category is CandidateCategory.RESEARCH
    assert "not fetched" in (result.note or "").lower()


# ---------------------------------------------------------------------------
# 7. JSON extraction
# ---------------------------------------------------------------------------
def test_json_fields_extracted_deterministically(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    json_record = next(
        r
        for r in project.sources
        if r.original_filename == "spec.json"
    )
    result = extraction_set.for_source(json_record.source_id)
    assert result.outcome is ExtractionOutcome.SUCCESS
    paths = sorted(i.location.json_path for i in result.items)
    assert paths == [
        "$.nested.x",
        "$.project",
        "$.seat_height_in",
        "$.tags[0]",
        "$.tags[1]",
    ]
    by_path = {i.location.json_path: i for i in result.items}
    assert by_path["$.project"].text == "$.project = Nefarious"
    assert by_path["$.project"].normalized == "Nefarious"
    assert by_path["$.seat_height_in"].normalized == 28
    assert by_path["$.tags[1]"].normalized == "b"
    assert by_path["$.nested.x"].normalized == 1
    # JSON leaves are candidates, never Build Pack truth: no status.
    assert all(i.status is None for i in result.items)
    assert all(
        i.category is CandidateCategory.GENERAL for i in result.items
    )
    # No character offsets for JSON: the path is the provenance.
    assert all(i.location.char_start is None for i in result.items)


def test_chunk_json_paths_and_order():
    chunks = chunk_json("s", {"b": 1, "a": {"z": [True, None]}})
    assert [c.json_path for c in chunks] == ["$.a.z[0]", "$.a.z[1]", "$.b"]
    assert [c.chunk_id for c in chunks] == [
        "s:json:0000",
        "s:json:0001",
        "s:json:0002",
    ]
    assert chunks[0].text == "true"
    assert chunks[1].text == "null"


# ---------------------------------------------------------------------------
# 9. provenance
# ---------------------------------------------------------------------------
def test_provenance_survives_extraction(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    md_record = next(
        r
        for r in project.sources
        if r.original_filename == "notes/build.md"
    )
    source_text = project.catalog.read(md_record.source_id)
    assert isinstance(source_text, str)
    checked = 0
    for item in extraction_set.all_items():
        loc = item.location
        assert loc.source_id == item.source_id
        assert loc.source_filename
        if item.source_id == md_record.source_id and loc.char_start is not None:
            # Offsets slice the original source text exactly.
            assert source_text[loc.char_start : loc.char_end] == item.text
            assert loc.chunk_id
            checked += 1
    assert checked > 0
    # Items under a Markdown section carry the heading.
    by_text = _items_by_text(extraction_set)
    assert by_text["42 mm"][0].location.heading == "Measurements"
    assert by_text["Part: steel bracket"][0].location.heading == "Parts"


def test_provenance_never_fabricated_for_references(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    ref_record = next(
        r for r in project.sources if r.kind is SourceKind.REFERENCE
    )
    item = extraction_set.for_source(ref_record.source_id).items[0]
    assert item.location.chunk_id is None
    assert item.location.char_start is None
    assert item.location.char_end is None
    assert item.location.json_path is None


# ---------------------------------------------------------------------------
# 10-12. serialization, storage, repeatability
# ---------------------------------------------------------------------------
def test_extraction_results_serialize_and_round_trip(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    clone = ExtractionSet.from_json(extraction_set.to_json())
    assert clone.to_dict() == extraction_set.to_dict()
    # Timestamps are metadata: excluded from equivalence comparison.
    assert clone.equivalent(extraction_set)


def test_extraction_results_save_and_load(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    store = project.extractions
    assert isinstance(store, ExtractionStore)
    path = store.save(extraction_set)
    assert path == project.root / "extractions" / "extractions.json"
    assert store.exists()
    assert store.load().to_dict() == extraction_set.to_dict()


def test_rerun_on_unchanged_material_is_equivalent(tmp_path):
    project = _rich_project(tmp_path)
    first = project.extractions.run(DeterministicExtractor())
    second = project.extractions.run(DeterministicExtractor())
    assert first.equivalent(second)
    assert [i.extraction_id for i in first.all_items()] == [
        i.extraction_id for i in second.all_items()
    ]
    # Raw dicts may differ only in run timestamps.
    assert _strip_timestamps(first.to_dict()) == _strip_timestamps(
        second.to_dict()
    )


def test_extraction_ids_are_content_derived_not_random(tmp_path):
    project = _rich_project(tmp_path)
    first_ids = [i.extraction_id for i in project.extractions.run().all_items()]
    project2 = Project.load(project.root)
    second_ids = [i.extraction_id for i in project2.extractions.run().all_items()]
    assert first_ids == second_ids
    assert len(set(first_ids)) == len(first_ids)


def test_clear_removes_cache_without_touching_sources(tmp_path):
    project = _rich_project(tmp_path)
    store = project.extractions
    store.save(store.run())
    assert store.exists()
    before = _source_hashes(project)
    store.clear()
    assert not store.exists()
    assert _source_hashes(project) == before
    # The cache is regenerable from the original sources.
    assert store.run().all_items()


def test_review_state_defaults_to_unreviewed(tmp_path):
    project = _rich_project(tmp_path)
    for item in project.extractions.run().all_items():
        assert item.review_state is ReviewState.UNREVIEWED


# ---------------------------------------------------------------------------
# 13-14. read-only extraction
# ---------------------------------------------------------------------------
def test_source_material_unchanged_by_extraction(tmp_path):
    project = _rich_project(tmp_path)
    before = _source_hashes(project)
    project.extractions.run(DeterministicExtractor())
    project.extractions.save(project.extractions.run())
    assert _source_hashes(project) == before


def test_source_hashes_and_project_json_unchanged(tmp_path):
    project = _rich_project(tmp_path)
    project_json_before = project.project_json_path.read_bytes()
    hashes_before = [r.sha256 for r in project.sources]
    project.extractions.run(DeterministicExtractor())
    assert project.project_json_path.read_bytes() == project_json_before
    assert [r.sha256 for r in project.sources] == hashes_before


# ---------------------------------------------------------------------------
# 15. honest handling of images and binary
# ---------------------------------------------------------------------------
def test_image_and_binary_handled_honestly(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    image_record = next(r for r in project.sources if r.kind is SourceKind.IMAGE)
    image_result = extraction_set.for_source(image_record.source_id)
    assert image_result.outcome is ExtractionOutcome.NO_EXTRACTION_AVAILABLE
    assert image_result.items == []
    assert "ocr" in (image_result.note or "").lower()
    binary_record = next(
        r
        for r in project.sources
        if r.kind is SourceKind.FILE
        and r.original_filename == "blob.bin"
    )
    binary_result = extraction_set.for_source(binary_record.source_id)
    assert binary_result.outcome is ExtractionOutcome.NO_EXTRACTION_AVAILABLE
    assert binary_result.items == []
    # Nothing was fabricated to fill the gap.
    assert all(
        r.outcome is ExtractionOutcome.SUCCESS or not r.items
        for r in extraction_set.results
    )


# ---------------------------------------------------------------------------
# 16. failures are represented, never swallowed
# ---------------------------------------------------------------------------
def test_extraction_failure_represented_not_swallowed(tmp_path):
    project = _rich_project(tmp_path)
    victim = next(
        r for r in project.sources if r.original_filename == "notes/build.md"
    )
    # Simulate a missing preserved file.
    project.catalog.stored_path(victim.source_id).unlink()
    extraction_set = project.extractions.run(DeterministicExtractor())
    failed = extraction_set.for_source(victim.source_id)
    assert failed.outcome is ExtractionOutcome.ERROR
    assert failed.error
    assert failed.items == []
    # Unrelated sources were still processed.
    others = [
        r for r in extraction_set.results if r.source_id != victim.source_id
    ]
    assert others
    assert all(r.outcome is not ExtractionOutcome.ERROR for r in others)
    assert any(r.items for r in others)


def test_error_outcome_requires_an_error_message():
    loc = SourceLocation(source_id="s")
    with pytest.raises(ExtractionError):
        ExtractionResult(
            source_id="s",
            outcome=ExtractionOutcome.ERROR,
            extracted_at="2026-01-01T00:00:00+00:00",
            extractor_name="deterministic",
        )
    with pytest.raises(ExtractionError):
        ExtractionResult(
            source_id="s",
            outcome=ExtractionOutcome.SUCCESS,
            error="spurious",
            extracted_at="2026-01-01T00:00:00+00:00",
            extractor_name="deterministic",
        )
    assert loc.source_id == "s"


# ---------------------------------------------------------------------------
# 17. Build Pack protection
# ---------------------------------------------------------------------------
def test_build_pack_untouched_by_extraction(tmp_path):
    project = _rich_project(tmp_path)
    pack = BuildPack(
        identity=ProjectIdentity(
            name="Nefarious",
            project_id="x",
            description="d",
            intent="i",
            status="active",
            created="2026-01-01T00:00:00+00:00",
            modified="2026-01-01T00:00:00+00:00",
            revision=0,
        )
    )
    before = pack.to_json()
    buildpack_files_before = sorted(p.name for p in project.buildpack_dir.iterdir())
    project.extractions.run(DeterministicExtractor())
    project.extractions.save(project.extractions.run())
    assert pack.to_json() == before
    assert sorted(p.name for p in project.buildpack_dir.iterdir()) == (
        buildpack_files_before
    )


# ---------------------------------------------------------------------------
# 18-20. relocation, unicode, platform neutrality
# ---------------------------------------------------------------------------
def test_relocation_keeps_extraction_working(tmp_path):
    project = _rich_project(tmp_path)
    original = project.extractions.run(DeterministicExtractor())
    moved_root = tmp_path / "elsewhere" / "moved-proj"
    shutil.copytree(project.root, moved_root)
    moved = Project.load(moved_root)
    assert moved.project_id == project.project_id
    rerun = moved.extractions.run(DeterministicExtractor())
    assert rerun.equivalent(original)
    # Stored results relocate too: no absolute paths inside.
    moved.extractions.save(rerun)
    payload = moved.extractions.extractions_path.read_text(encoding="utf-8")
    assert str(project.root) not in payload
    assert str(moved_root) not in payload


def test_unicode_survives_extraction(tmp_path):
    project = _fixture_copy(tmp_path)
    extraction_set = project.extractions.run(DeterministicExtractor())
    texts = [i.text for i in extraction_set.all_items()]
    # Unicode in JSON leaves is extracted verbatim.
    assert any("café ☕" in t for t in texts)
    # Unicode in plain-text lines survives chunking verbatim even when
    # the line carries nothing the deterministic extractor recognizes:
    # chunking never normalizes or mangles text.
    notes = next(
        r for r in project.sources if r.original_filename == "field-notes.txt"
    )
    chunks = chunk_text(
        notes.source_id, project.catalog.read(notes.source_id)
    )
    assert any("日本語" in c.text for c in chunks)
    assert any("naïve" in c.text for c in chunks)
    # Round-trips through JSON exactly.
    clone = ExtractionSet.from_json(extraction_set.to_json())
    assert [i.text for i in clone.all_items()] == texts


def test_storage_is_platform_neutral_and_relocatable(tmp_path):
    project = _rich_project(tmp_path)
    store = project.extractions
    extraction_set = store.run()
    path = store.save(extraction_set)
    # Built with pathlib only: no hard-coded separators or drive letters.
    assert path == project.root / "extractions" / "extractions.json"
    payload = path.read_text(encoding="utf-8")
    data = json.loads(payload)
    assert data["format"] == "agentivan-extractions"
    assert str(project.root) not in payload
    # Results sorted deterministically by source_id.
    ids = [r["source_id"] for r in data["results"]]
    assert ids == sorted(ids)


def test_mixed_newline_offsets_stay_valid(tmp_path):
    project = _fixture_copy(tmp_path)
    notes = next(
        r for r in project.sources if r.original_filename == "field-notes.txt"
    )
    source_text = project.catalog.read(notes.source_id)
    assert "\r" in source_text  # the fixture really has mixed endings
    extraction_set = project.extractions.run(DeterministicExtractor())
    result = extraction_set.for_source(notes.source_id)
    assert result.outcome is ExtractionOutcome.SUCCESS
    for item in result.items:
        loc = item.location
        if loc.char_start is not None:
            assert source_text[loc.char_start : loc.char_end] == item.text


# ---------------------------------------------------------------------------
# 17. LLM extension point (not implemented)
# ---------------------------------------------------------------------------
def test_llm_extractor_is_an_unimplemented_extension_point(tmp_path):
    project = _rich_project(tmp_path)
    extractor = get_extractor("llm")
    assert isinstance(extractor, LLMExtractor)
    assert isinstance(extractor, Extractor)
    record = project.catalog.list()[0]
    with pytest.raises(ExtractionNotAvailableError):
        extractor.extract_source(project.catalog, record)
    with pytest.raises(ExtractionError):
        get_extractor("no-such-extractor")
    assert get_extractor("deterministic").name == "deterministic"


def test_extractor_interface_common_shape():
    assert issubclass(DeterministicExtractor, Extractor)
    assert issubclass(LLMExtractor, Extractor)
    for cls in (DeterministicExtractor, LLMExtractor):
        assert isinstance(cls.name, str)
        assert cls.version >= 1


# ---------------------------------------------------------------------------
# model validation
# ---------------------------------------------------------------------------
def test_extracted_item_rejects_bad_values():
    loc = SourceLocation(source_id="s")
    good = dict(
        extraction_id="id1",
        source_id="s",
        location=loc,
        text="28 inches",
        category=CandidateCategory.MEASUREMENT,
        confidence=1.0,
        method=ExtractionMethod.DETERMINISTIC,
        created_at="2026-01-01T00:00:00+00:00",
    )
    ExtractedItem(**good)
    with pytest.raises(ExtractionError):
        ExtractedItem(**{**good, "confidence": 1.5})
    with pytest.raises(ExtractionError):
        ExtractedItem(**{**good, "category": "nope"})
    with pytest.raises(ExtractionError):
        ExtractedItem(**{**good, "status": "VERIFIED-ish"})
    # status=None is valid: the source established nothing.
    item = ExtractedItem(**{**good, "status": None})
    assert item.status is None
    item = ExtractedItem(**{**good, "status": "MEASURED"})
    assert item.status is InfoStatus.MEASURED
    # Manual items are supported for future human review workflows.
    manual = ExtractedItem(
        **{**good, "method": ExtractionMethod.MANUAL, "extraction_id": "id2"}
    )
    assert manual.method is ExtractionMethod.MANUAL


def test_make_extraction_id_is_deterministic():
    kwargs = dict(
        extractor_name="deterministic",
        extractor_version=1,
        source_id="s",
        chunk_id="s:chunk:0000",
        category=CandidateCategory.MEASUREMENT,
        status=InfoStatus.MEASURED,
        confidence=1.0,
        method=ExtractionMethod.DETERMINISTIC,
        text="28 inches",
        normalized={"value": 28.0, "unit": "in"},
        char_start=10,
        char_end=19,
        json_path=None,
    )
    assert make_extraction_id(**kwargs) == make_extraction_id(**kwargs)
    assert make_extraction_id(**kwargs) != make_extraction_id(
        **{**kwargs, "text": "29 inches"}
    )


def test_text_chunk_rejects_bad_offsets():
    with pytest.raises(ExtractionError):
        TextChunk(
            chunk_id="c",
            source_id="s",
            heading=None,
            text="x",
            char_start=5,
            char_end=2,
        )


def test_extraction_set_rejects_duplicate_source_results():
    loc_kwargs = dict(
        extracted_at="2026-01-01T00:00:00+00:00", extractor_name="deterministic"
    )
    r = ExtractionResult(source_id="s", outcome=ExtractionOutcome.SUCCESS, **loc_kwargs)
    with pytest.raises(ExtractionError):
        ExtractionSet(project_id="p", results=[r, r])


def test_store_run_defaults_to_deterministic_extractor(tmp_path):
    project = _rich_project(tmp_path)
    extraction_set = project.extractions.run()
    assert extraction_set.results
    assert all(
        r.extractor_name == "deterministic" for r in extraction_set.results
    )
