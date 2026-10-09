"""
Focused tests for the AgentIvan source catalog + retrieval layer (Brick 3).

These prove the catalog is a deterministic, stateless view over a
project's source records:

- registration through the existing project intake layer
- lookup by id, count, existence
- deterministic platform-independent listing order
- filtering by kind / label / filename / media type / status / id
- case-insensitive metadata search (plain substring matching only)
- verbatim retrieval of text, files, images, and references
- integrity checking: intact / changed / missing, never auto-repairing
- project relocation keeps catalog behavior working
- duplicate-content identification without automatic deduplication
- explicit removal: record-only vs record+file, never automatic
- rebuild from disk; the catalog persists nothing of its own
- Build Pack is never touched by catalog operations

They do not modify the existing 230-test baseline. No network, no LLM,
no OS-specific shell. Windows-style path inputs are exercised through
the Brick 2 platform-neutral path logic; Windows runtime verification
remains pending (see BRICK-3-SOURCE-CATALOG.md).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.app.product.agentivan import (
    CatalogError,
    Project,
    SourceCatalog,
    SourceIntegrity,
    SourceKind,
    SourceRecord,
    SourceStatus,
    catalog_order_key,
)

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "agentivan_synthetic_project"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _project(tmp_path: Path, name: str = "Catalog Test") -> Project:
    root = tmp_path / "proj"
    return Project.create(name, root)


def _fixture_copy(tmp_path: Path) -> Project:
    dest = tmp_path / "fixture-proj"
    shutil.copytree(FIXTURE_PROJECT, dest)
    return Project.load(dest)


# ---------------------------------------------------------------------------
# catalog creation / registration / basic access
# ---------------------------------------------------------------------------
def test_catalog_is_a_stateless_view_over_live_records(tmp_path):
    project = _project(tmp_path)
    catalog = project.catalog
    assert isinstance(catalog, SourceCatalog)
    assert catalog.count() == 0
    # The same catalog object sees records registered afterwards:
    # it holds no snapshot of its own.
    project.intake_text("hello", label="Greeting")
    assert catalog.count() == 1
    assert project.catalog.count() == 1


def test_catalog_registration_delegates_to_project_intake(tmp_path):
    project = _project(tmp_path)
    catalog = project.catalog
    text_rec = catalog.intake_text("line1\r\nline2", label="Mixed newlines")
    file_src = tmp_path / "data.bin"
    file_src.write_bytes(bytes(range(256)))
    file_rec = catalog.intake_file(file_src, label="Binary blob")
    ref_rec = catalog.intake_reference("https://example.com/x", label="Ref")
    assert catalog.count() == 3
    assert isinstance(text_rec, SourceRecord)
    assert catalog.get(text_rec.source_id).label == "Mixed newlines"
    assert catalog.get(file_rec.source_id).kind is SourceKind.FILE
    assert catalog.get(ref_rec.source_id).kind is SourceKind.REFERENCE


def test_get_unknown_source_id_raises(tmp_path):
    catalog = _project(tmp_path).catalog
    with pytest.raises(CatalogError):
        catalog.get("no-such-id")


def test_exists_and_count(tmp_path):
    project = _project(tmp_path)
    catalog = project.catalog
    assert not catalog.exists("nope")
    rec = project.intake_text("abc", label="A")
    assert catalog.exists(rec.source_id)
    assert catalog.count() == 1


# ---------------------------------------------------------------------------
# deterministic ordering
# ---------------------------------------------------------------------------
def test_listing_order_is_deterministic_not_insertion_order(tmp_path):
    project = _project(tmp_path)
    recs = []
    for label, kind_kwargs in [
        ("zebra note", {"text": "z", "label": "zebra note"}),
        ("Alpha note", {"text": "a", "label": "Alpha note"}),
        ("middle ref", {}),
    ]:
        if kind_kwargs:
            recs.append(project.intake_text(kind_kwargs["text"], label=kind_kwargs["label"]))
        else:
            recs.append(project.intake_reference("https://example.com/m", label=label))
    listed = project.catalog.list()
    keys = [catalog_order_key(r) for r in listed]
    assert keys == sorted(keys)
    # Same project listed again gives the identical order.
    assert [r.source_id for r in project.catalog.list()] == [r.source_id for r in listed]


def test_listing_order_survives_reload_and_relocation(tmp_path):
    project = _fixture_copy(tmp_path)
    before = [r.source_id for r in project.catalog.list()]
    assert len(before) == 5
    # Reload from disk: identical order.
    reloaded = Project.load(project.root)
    assert [r.source_id for r in reloaded.catalog.list()] == before
    # Relocate the whole project directory: identical order.
    moved = tmp_path / "elsewhere" / "renamed"
    shutil.copytree(project.root, moved)
    relocated = Project.load(moved)
    assert [r.source_id for r in relocated.catalog.list()] == before


def test_fixture_listing_is_sorted_by_kind_then_label(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    listed = catalog.list()
    kinds = [r.kind.value for r in listed]
    assert kinds == sorted(kinds)
    labels = [r.label for r in listed if r.kind is SourceKind.TEXT]
    assert labels == sorted(labels, key=str.casefold)


# ---------------------------------------------------------------------------
# filtering
# ---------------------------------------------------------------------------
def test_filter_by_kind(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    assert len(catalog.list(kind=SourceKind.TEXT)) == 3
    assert len(catalog.list(kind=SourceKind.IMAGE)) == 1
    assert len(catalog.list(kind=SourceKind.REFERENCE)) == 1
    assert catalog.list(kind=SourceKind.FILE) == []
    both = catalog.list(kind=[SourceKind.TEXT, SourceKind.IMAGE])
    assert len(both) == 4


def test_filter_by_exact_fields(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    by_label = catalog.search(label="JSON spec")
    assert len(by_label) == 1
    assert by_label[0].original_filename == "spec.json"
    by_filename = catalog.search(original_filename="field-notes.txt")
    assert len(by_filename) == 1
    assert by_filename[0].label == "Field notes (unicode, mixed newlines)"
    by_media = catalog.search(media_type="image/png")
    assert len(by_media) == 1
    assert by_media[0].kind is SourceKind.IMAGE
    by_status = catalog.search(status=SourceStatus.STORED)
    assert len(by_status) == 5
    rec = by_label[0]
    by_id = catalog.search(source_id=rec.source_id)
    assert [r.source_id for r in by_id] == [rec.source_id]


def test_search_is_case_insensitive_substring_over_metadata(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    # Matches label + description of the field notes.
    hits = catalog.search("UNICODE")
    labels = {r.label for r in hits}
    assert "Field notes (unicode, mixed newlines)" in labels
    assert "Rig photo (ünïcode filename)" in labels
    # Matches the stored reference URL.
    ref_hits = catalog.search("nefarious-reference")
    assert len(ref_hits) == 1
    assert ref_hits[0].kind is SourceKind.REFERENCE
    # Matches media type and original filename.
    assert len(catalog.search("image/png")) == 1
    assert len(catalog.search("build-overview")) == 1
    # No match -> empty, not an error.
    assert catalog.search("no-such-phrase-anywhere") == []


def test_search_results_use_deterministic_order(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    hits = catalog.search("e")  # matches several records
    keys = [catalog_order_key(r) for r in hits]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def test_text_retrieval_is_verbatim(tmp_path):
    project = _project(tmp_path)
    original = "héllo wörld\r\nsecond line\rthird\n"
    rec = project.intake_text(original, label="Verbatim")
    retrieved = project.catalog.read(rec.source_id)
    assert retrieved == original
    assert isinstance(retrieved, str)


def test_file_retrieval_returns_exact_bytes(tmp_path):
    project = _project(tmp_path)
    payload = bytes(range(256)) + b"\x00\xff binary \r\n soup"
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    rec = project.intake_file(src, label="Binary")
    retrieved = project.catalog.read(rec.source_id)
    assert isinstance(retrieved, bytes)
    assert retrieved == payload
    assert project.catalog.read_bytes(rec.source_id) == payload


def test_image_retrieval_returns_original_bytes(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    img = catalog.list(kind=SourceKind.IMAGE)[0]
    stored = (project.root / img.relative_path).read_bytes()
    assert catalog.read(img.source_id) == stored
    assert stored[:8] == b"\x89PNG\r\n\x1a\n"  # still a real PNG


def test_reference_retrieval_returns_stored_string_exactly(tmp_path):
    project = _project(tmp_path)
    ref = "https://example.com/some/page?x=1&y=2  "
    rec = project.intake_reference(ref, label="Ref")
    # Trailing whitespace was stripped at intake; the stored value is
    # returned exactly as recorded -- nothing is fetched.
    assert project.catalog.read(rec.source_id) == ref.strip()


def test_stored_path_resolves_inside_project(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    img = catalog.list(kind=SourceKind.IMAGE)[0]
    path = catalog.stored_path(img.source_id)
    assert path.is_absolute()
    assert path.is_file()
    # The resolved path stays inside the project root.
    assert project.root in path.parents or path.parent == project.root
    with pytest.raises(CatalogError):
        catalog.stored_path(catalog.list(kind=SourceKind.REFERENCE)[0].source_id)


def test_read_unknown_id_raises(tmp_path):
    with pytest.raises(CatalogError):
        _project(tmp_path).catalog.read("missing")


# ---------------------------------------------------------------------------
# integrity
# ---------------------------------------------------------------------------
def test_verify_all_intact_after_clean_intake(tmp_path):
    project = _fixture_copy(tmp_path)
    statuses = project.catalog.verify_all()
    assert len(statuses) == 5
    assert {s.state for s in statuses} == {SourceIntegrity.INTACT}
    for status in statuses:
        rec = project.catalog.get(status.source_id)
        if rec.kind is SourceKind.REFERENCE:
            assert status.recorded_sha256 is None
        else:
            assert status.recorded_sha256 == rec.sha256 == status.actual_sha256


def test_verify_detects_changed_file(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    text_rec = catalog.search(label="JSON spec")[0]
    stored = project.root / text_rec.relative_path
    original_bytes = stored.read_bytes()
    stored.write_bytes(original_bytes + b"\n// tampered")
    try:
        status = catalog.verify(text_rec.source_id)
        assert status.state is SourceIntegrity.CHANGED
        assert status.recorded_sha256 == text_rec.sha256
        assert status.actual_sha256 != text_rec.sha256
        # Detection does not repair or delete: the record and the
        # (tampered) file are both still there.
        assert catalog.exists(text_rec.source_id)
        assert stored.is_file()
    finally:
        stored.write_bytes(original_bytes)
    assert catalog.verify(text_rec.source_id).state is SourceIntegrity.INTACT


def test_verify_detects_missing_file(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    img = catalog.list(kind=SourceKind.IMAGE)[0]
    stored = project.root / img.relative_path
    backup = stored.read_bytes()
    stored.unlink()
    try:
        status = catalog.verify(img.source_id)
        assert status.state is SourceIntegrity.MISSING
        assert status.recorded_sha256 == img.sha256
        assert status.actual_sha256 is None
        assert catalog.exists(img.source_id)  # record itself is untouched
    finally:
        stored.write_bytes(backup)
    assert catalog.verify(img.source_id).state is SourceIntegrity.INTACT


def test_verify_reference_is_always_intact(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    ref = catalog.list(kind=SourceKind.REFERENCE)[0]
    status = catalog.verify(ref.source_id)
    assert status.state is SourceIntegrity.INTACT


def test_changed_hash_never_triggers_automatic_removal(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    rec = catalog.search(label="JSON spec")[0]
    stored = project.root / rec.relative_path
    original_bytes = stored.read_bytes()
    stored.write_bytes(b"corrupted")
    try:
        assert catalog.verify(rec.source_id).state is SourceIntegrity.CHANGED
        assert catalog.exists(rec.source_id)
        assert catalog.count() == 5
    finally:
        stored.write_bytes(original_bytes)


# ---------------------------------------------------------------------------
# relocation
# ---------------------------------------------------------------------------
def test_catalog_survives_project_relocation(tmp_path):
    project = _fixture_copy(tmp_path)
    moved = tmp_path / "new-home" / "renamed-project"
    shutil.copytree(project.root, moved)
    relocated = Project.load(moved)
    catalog = relocated.catalog
    assert catalog.count() == 5
    # Enumerate, retrieve, verify, and search all work from the new home.
    for rec in catalog.list():
        if rec.kind is SourceKind.REFERENCE:
            assert catalog.read(rec.source_id) == rec.reference
        elif rec.kind is SourceKind.TEXT:
            raw = catalog.read_bytes(rec.source_id)
            assert catalog.read(rec.source_id) == raw.decode("utf-8")
        else:
            assert catalog.read(rec.source_id) == catalog.read_bytes(rec.source_id)
    assert catalog.verify_all()
    assert {s.state for s in catalog.verify_all()} == {SourceIntegrity.INTACT}
    assert len(catalog.search("unicode")) == 2
    # No absolute development-machine paths leaked into stored paths.
    for rec in catalog.list():
        if rec.relative_path:
            assert not Path(rec.relative_path).is_absolute()
            assert not rec.relative_path.startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# duplicate identification (no automatic deduplication)
# ---------------------------------------------------------------------------
def test_duplicates_identifies_same_content_without_deleting(tmp_path):
    project = _project(tmp_path)
    a = project.intake_text("same\ncontent", label="First copy")
    b = project.intake_text("same\ncontent", label="Second copy")
    c = project.intake_text("same\r\ncontent", label="CRLF variant")
    groups = project.catalog.duplicates()
    assert len(groups) == 1
    assert sorted(groups[0]) == sorted([a.source_id, b.source_id, c.source_id])
    # Identification only: every record is still present.
    assert project.catalog.count() == 3


def test_duplicates_empty_when_all_unique(tmp_path):
    project = _fixture_copy(tmp_path)
    assert project.catalog.duplicates() == []


# ---------------------------------------------------------------------------
# explicit removal
# ---------------------------------------------------------------------------
def test_remove_deletes_record_but_keeps_file_by_default(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    rec = catalog.search(label="JSON spec")[0]
    stored = project.root / rec.relative_path
    removed = catalog.remove(rec.source_id)
    assert removed.source_id == rec.source_id
    assert not catalog.exists(rec.source_id)
    assert catalog.count() == 4
    # Safest minimal behavior: the preserved file is NOT deleted.
    assert stored.is_file()
    project.save()
    reloaded = Project.load(project.root)
    assert not reloaded.catalog.exists(rec.source_id)
    assert (reloaded.root / rec.relative_path).is_file()


def test_remove_with_delete_file_removes_both_explicitly(tmp_path):
    project = _project(tmp_path)
    src = tmp_path / "note.txt"
    src.write_bytes(b"temporary")
    rec = project.intake_file(src, label="Temp")
    stored = project.root / rec.relative_path
    assert stored.is_file()
    project.catalog.remove(rec.source_id, delete_file=True)
    assert not project.catalog.exists(rec.source_id)
    assert not stored.exists()


def test_remove_unknown_id_raises_and_removes_nothing(tmp_path):
    catalog = _fixture_copy(tmp_path).catalog
    with pytest.raises(CatalogError):
        catalog.remove("no-such-id")
    assert catalog.count() == 5


def test_remove_reference_with_delete_file_flag_is_harmless(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    ref = catalog.list(kind=SourceKind.REFERENCE)[0]
    catalog.remove(ref.source_id, delete_file=True)  # no file to delete
    assert not catalog.exists(ref.source_id)
    assert catalog.count() == 4


# ---------------------------------------------------------------------------
# rebuild / no second source of truth
# ---------------------------------------------------------------------------
def test_rebuild_reconstructs_catalog_from_disk(tmp_path):
    project = _project(tmp_path)
    project.intake_text("one", label="One")
    project.intake_reference("https://example.com/1", label="R1")
    project.save()
    rebuilt = project.catalog.rebuild()
    assert isinstance(rebuilt, SourceCatalog)
    assert rebuilt.count() == 2
    assert [r.source_id for r in rebuilt.list()] == [
        r.source_id for r in project.catalog.list()
    ]
    # Rebuild reflects on-disk state, not unsaved in-memory changes.
    project.intake_text("two", label="Two")
    assert rebuilt.count() == 2
    assert project.catalog.count() == 3


def test_catalog_persists_nothing_of_its_own(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    before = {p.relative_to(project.root) for p in project.root.rglob("*")}
    # Exercise every read path.
    catalog.list()
    catalog.search("unicode")
    catalog.duplicates()
    catalog.verify_all()
    for rec in catalog.list():
        catalog.read(rec.source_id)
    after = {p.relative_to(project.root) for p in project.root.rglob("*")}
    assert after == before
    # Only the Brick 2 layout exists: no catalog database, no cache.
    top_level = {p.name for p in project.root.iterdir()}
    assert top_level == {"project.json", "sources", "buildpack", "exports"}


# ---------------------------------------------------------------------------
# original content unchanged / Build Pack separation
# ---------------------------------------------------------------------------
def test_original_content_unchanged_by_catalog_use(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    for rec in catalog.list():
        if rec.kind is SourceKind.REFERENCE:
            continue
        stored = (project.root / rec.relative_path).read_bytes()
        retrieved = catalog.read(rec.source_id)
        if rec.kind is SourceKind.TEXT:
            assert retrieved == stored.decode("utf-8")
        else:
            assert retrieved == stored


def test_read_search_verify_do_not_modify_project_json(tmp_path):
    project = _fixture_copy(tmp_path)
    before = (project.root / "project.json").read_bytes()
    catalog = project.catalog
    catalog.list()
    catalog.search("e")
    catalog.duplicates()
    catalog.verify_all()
    for rec in catalog.list():
        catalog.read(rec.source_id)
    assert (project.root / "project.json").read_bytes() == before


def test_catalog_never_touches_buildpack(tmp_path):
    project = _fixture_copy(tmp_path)
    catalog = project.catalog
    assert list(project.buildpack_dir.iterdir()) == []
    catalog.list()
    catalog.search("unicode")
    catalog.verify_all()
    for rec in catalog.list():
        catalog.read(rec.source_id)
    assert list(project.buildpack_dir.iterdir()) == []
    data = (project.root / "project.json").read_text(encoding="utf-8")
    assert '"buildpack"' not in data
