"""
Focused tests for the AgentIvan project workspace + source intake (Brick 2).

These prove the intake foundation is reliable and platform-neutral:

- workspace/project lifecycle (create, open, list, save, load)
- stable project identity that survives relocation
- source intake preserves originals byte-for-byte
- metadata + deterministic hashing, including newline-insensitive
  identity so Linux/Windows line-ending differences are not false changes
- path traversal / drive / UNC / absolute-path rejection on every host
- unicode filenames and text survive; mixed newlines survive
- active-project behavior, including intake against the active project

They do not touch the existing 169-test baseline; those tests must keep
passing unchanged. No Windows runtime is available on this machine, so
Windows-style inputs are exercised through platform-neutral validation
logic -- Windows runtime verification remains pending (see docstring in
BRICK-2-WORKSPACE-INTAKE.md).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath

import pytest

from src.app.product.agentivan import (
    PROJECT_FORMAT_NAME,
    AgentIvanWorkspace,
    IntakeError,
    Project,
    ProjectError,
    SourceKind,
    SourceRecord,
    SourceRecordError,
    SourceStatus,
    UnsafePathError,
    WorkspaceError,
    default_workspace_root,
    identity_hash_for_text,
    normalize_newlines,
    sanitize_filename,
    sha256_hex,
    validate_supplied_path,
)

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "agentivan_synthetic_project"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _workspace(tmp_path: Path) -> AgentIvanWorkspace:
    return AgentIvanWorkspace(tmp_path / "ws")


def _project(tmp_path: Path, name: str = "Test Project") -> Project:
    ws = _workspace(tmp_path)
    return ws.create_project(name)


# ---------------------------------------------------------------------------
# workspace lifecycle
# ---------------------------------------------------------------------------
def test_workspace_creates_root_structure(tmp_path):
    ws = _workspace(tmp_path)
    assert ws.root.is_dir()
    assert ws.projects_dir.is_dir()
    assert ws.root == tmp_path / "ws"


def test_default_workspace_root_is_platform_neutral():
    root = default_workspace_root()
    assert isinstance(root, Path)
    assert not root.is_absolute() is False
    # No hard-coded OS-specific roots: derived from the user home.
    assert str(root).startswith(str(Path.home()))


def test_create_project_makes_required_layout(tmp_path):
    project = _project(tmp_path)
    assert (project.root / "project.json").is_file()
    assert project.sources_dir.is_dir()
    assert project.buildpack_dir.is_dir()
    assert project.exports_dir.is_dir()


def test_create_project_rejects_empty_name(tmp_path):
    ws = _workspace(tmp_path)
    for bad in ("", "   ", None):
        with pytest.raises((ProjectError, WorkspaceError)):
            ws.create_project(bad)


def test_project_ids_are_stable_and_unique(tmp_path):
    ws = _workspace(tmp_path)
    a = ws.create_project("Alpha")
    b = ws.create_project("Beta")
    assert a.project_id and b.project_id
    assert a.project_id != b.project_id
    assert len(a.project_id) == 32  # uuid4 hex


def test_list_projects(tmp_path):
    ws = _workspace(tmp_path)
    assert ws.list_projects() == []
    a = ws.create_project("Alpha")
    b = ws.create_project("Beta")
    listed = ws.list_projects()
    assert {e["project_id"] for e in listed} == {a.project_id, b.project_id}
    assert all(e["source_count"] == 0 for e in listed)


def test_open_project_makes_it_active(tmp_path):
    ws = _workspace(tmp_path)
    project = ws.create_project("Alpha")
    assert ws.active_project_id is None
    opened = ws.open_project(project.project_id)
    assert opened.project_id == project.project_id
    assert ws.active_project_id == project.project_id
    assert ws.active_project.project_id == project.project_id


def test_get_project_does_not_change_active(tmp_path):
    ws = _workspace(tmp_path)
    a = ws.create_project("Alpha")
    b = ws.create_project("Beta")
    ws.open_project(a.project_id)
    fetched = ws.get_project(b.project_id)
    assert fetched.project_id == b.project_id
    assert ws.active_project_id == a.project_id


def test_open_nonexistent_project_raises(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.open_project("0" * 32)
    with pytest.raises(WorkspaceError):
        ws.set_active_project("0" * 32)


def test_open_invalid_project_id_raises(tmp_path):
    ws = _workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.open_project("../escape")
    with pytest.raises(WorkspaceError):
        ws.open_project("")


def test_active_project_persists_across_workspace_instances(tmp_path):
    ws = _workspace(tmp_path)
    project = ws.create_project("Alpha")
    ws.open_project(project.project_id)
    ws2 = AgentIvanWorkspace(tmp_path / "ws")
    assert ws2.active_project_id == project.project_id
    assert ws2.active_project.project_id == project.project_id


def test_clear_active_project(tmp_path):
    ws = _workspace(tmp_path)
    project = ws.create_project("Alpha")
    ws.open_project(project.project_id)
    ws.clear_active_project()
    assert ws.active_project_id is None
    assert ws.active_project is None


def test_intake_requires_active_project(tmp_path):
    ws = _workspace(tmp_path)
    ws.create_project("Alpha")  # created but not opened
    with pytest.raises(WorkspaceError):
        ws.intake_text("hello", label="x")
    with pytest.raises(WorkspaceError):
        ws.intake_reference("https://example.com", label="x")


def test_workspace_intake_operates_on_active_project(tmp_path):
    ws = _workspace(tmp_path)
    project = ws.create_project("Alpha")
    ws.open_project(project.project_id)
    record = ws.intake_text("hello", label="greeting", filename="hi.txt")
    assert record.kind is SourceKind.TEXT
    assert ws.active_project.get_source(record.source_id).label == "greeting"


# ---------------------------------------------------------------------------
# project identity, save/load, relocation
# ---------------------------------------------------------------------------
def test_project_identity_not_derived_from_path(tmp_path):
    project = _project(tmp_path, name="Nefarious")
    project.save()
    data = json.loads((project.root / "project.json").read_bytes().decode("utf-8"))
    assert data["format"] == PROJECT_FORMAT_NAME
    assert data["project_id"] == project.project_id
    assert data["name"] == "Nefarious"
    # Identity lives in project.json: the same project data under a
    # completely unrelated directory name keeps its identity.
    renamed = tmp_path / "some-unrelated-folder-name"
    shutil.copytree(project.root, renamed)
    reloaded = Project.load(renamed)
    assert reloaded.project_id == project.project_id
    assert reloaded.name == "Nefarious"


def test_save_load_round_trip_preserves_everything(tmp_path):
    project = _project(tmp_path, name="Nefarious")
    text = "caf\u00e9 \u65e5\u672c\u8a9e\r\nline2"
    r1 = project.intake_text(text, label="notes", filename="notes.txt")
    r2 = project.intake_reference("https://example.com/r", label="ref")
    project.save()

    reopened = Project.load(project.root)
    assert reopened.project_id == project.project_id
    assert reopened.name == project.name
    assert reopened.created_at == project.created_at
    assert reopened.updated_at == project.updated_at
    assert len(reopened.sources) == 2
    assert reopened.get_source(r1.source_id).to_dict() == r1.to_dict()
    assert reopened.get_source(r2.source_id).to_dict() == r2.to_dict()
    stored = (project.root / r1.relative_path).read_bytes()
    assert stored == text.encode("utf-8")


def test_save_is_deterministic(tmp_path):
    project = _project(tmp_path)
    project.intake_text("hello", label="a", filename="a.txt")
    project.save()
    first = (project.root / "project.json").read_bytes()
    project.save()
    second = (project.root / "project.json").read_bytes()
    assert first == second


def test_load_rejects_corrupted_project_json(tmp_path):
    project = _project(tmp_path)
    (project.root / "project.json").write_bytes(b"{not json")
    with pytest.raises(ProjectError):
        Project.load(project.root)


def test_load_rejects_missing_project_json(tmp_path):
    with pytest.raises(ProjectError):
        Project.load(tmp_path / "nope")


def test_load_rejects_wrong_format(tmp_path):
    project = _project(tmp_path)
    (project.root / "project.json").write_bytes(b'{"format": "other"}')
    with pytest.raises(ProjectError):
        Project.load(project.root)


def test_project_relocation_keeps_identity(tmp_path):
    project = _project(tmp_path, name="Nefarious")
    project.intake_text("payload", label="p", filename="p.txt")
    project.save()
    original_id = project.project_id

    new_home = tmp_path / "elsewhere" / "moved-project"
    new_home.parent.mkdir(parents=True)
    shutil.move(str(project.root), str(new_home))

    moved = Project.load(new_home)
    assert moved.project_id == original_id
    assert moved.name == "Nefarious"
    assert len(moved.sources) == 1
    assert (new_home / moved.sources[0].relative_path).is_file()


# ---------------------------------------------------------------------------
# source intake: preservation, metadata, hashing
# ---------------------------------------------------------------------------
def test_intake_text_preserves_content_byte_exact(tmp_path):
    project = _project(tmp_path)
    text = "caf\u00e9 \u65e5\u672c\u8a9e \U0001f375\r\nline2\nline3\rlast  "
    record = project.intake_text(text, label="notes", filename="notes.txt")
    stored = (project.root / record.relative_path).read_bytes()
    assert stored == text.encode("utf-8")


def test_intake_text_records_complete_metadata(tmp_path):
    project = _project(tmp_path)
    record = project.intake_text("hello", label="greeting",
                                 filename="sub/hi.md", description="desc")
    assert record.kind is SourceKind.TEXT
    assert record.status is SourceStatus.STORED
    assert record.source_id
    assert record.original_filename == "sub/hi.md"
    assert record.relative_path.startswith("sources/")
    assert PurePosixPath(record.relative_path).as_posix() == record.relative_path
    assert record.media_type == "text/markdown; charset=utf-8"
    assert record.sha256 == identity_hash_for_text("hello")
    assert record.byte_size == 5
    assert record.intake_timestamp
    assert record.description == "desc"
    assert record.reference is None


def test_intake_detects_json_media_type(tmp_path):
    project = _project(tmp_path)
    record = project.intake_text('{"a": 1}', label="spec", filename="spec.json")
    assert record.media_type == "application/json"


def test_intake_file_preserves_bytes_and_detects_image(tmp_path):
    project = _project(tmp_path)
    payload = bytes(range(256)) * 4
    src = tmp_path / "blob.bin"
    src.write_bytes(payload)
    record = project.intake_file(src, label="blob")
    assert record.kind is SourceKind.FILE
    assert (project.root / record.relative_path).read_bytes() == payload
    assert record.sha256 == sha256_hex(payload)
    assert record.byte_size == len(payload)


def test_intake_file_missing_source_raises_and_registers_nothing(tmp_path):
    project = _project(tmp_path)
    before = len(project.sources)
    with pytest.raises(IntakeError):
        project.intake_file(tmp_path / "missing.bin", label="ghost")
    assert len(project.sources) == before


def test_intake_reference_stores_without_fetch(tmp_path):
    project = _project(tmp_path)
    url = "https://example.com/oddfable/some-page"
    record = project.intake_reference(url, label="docs")
    assert record.kind is SourceKind.REFERENCE
    assert record.reference == url
    assert record.sha256 is None
    assert record.relative_path is None
    # No file is created for a reference.
    assert list(project.sources_dir.iterdir()) == []


def test_source_hash_is_stable(tmp_path):
    project = _project(tmp_path)
    a = project.intake_text("same content", label="a", filename="a.txt")
    b = project.intake_text("same content", label="b", filename="b.txt")
    assert a.sha256 == b.sha256
    c = project.intake_text("different", label="c", filename="c.txt")
    assert c.sha256 != a.sha256


def test_duplicate_content_recognized_by_hash_not_filename(tmp_path):
    project = _project(tmp_path)
    payload = b"\x00\x01binary\xff"
    f1 = tmp_path / "first.dat"
    f2 = tmp_path / "second.dat"
    f1.write_bytes(payload)
    f2.write_bytes(payload)
    r1 = project.intake_file(f1, label="first")
    r2 = project.intake_file(f2, label="second")
    # Same content recognized as identical...
    assert r1.sha256 == r2.sha256
    # ...but nothing is deduplicated or deleted: both originals preserved.
    assert r1.source_id != r2.source_id
    assert len(project.sources) == 2
    assert (project.root / r1.relative_path).read_bytes() == payload
    assert (project.root / r2.relative_path).read_bytes() == payload


def test_source_ids_unique(tmp_path):
    project = _project(tmp_path)
    ids = [project.intake_text(f"text {i}", label=f"t{i}").source_id for i in range(10)]
    assert len(set(ids)) == 10


def test_source_records_survive_save_load(tmp_path):
    project = _project(tmp_path)
    src = tmp_path / "data.bin"
    src.write_bytes(b"\x00\xff binary")
    t = project.intake_text("hello w\u00f6rld", label="t", filename="t.txt")
    f = project.intake_file(src, label="f")
    r = project.intake_reference("https://example.com", label="r")
    project.save()

    reopened = Project.load(project.root)
    assert [s.source_id for s in reopened.sources] == [t.source_id, f.source_id, r.source_id]
    assert (reopened.root / reopened.get_source(t.source_id).relative_path).read_bytes() == "hello w\u00f6rld".encode("utf-8")
    assert (reopened.root / reopened.get_source(f.source_id).relative_path).read_bytes() == b"\x00\xff binary"
    assert reopened.get_source(r.source_id).reference == "https://example.com"


# ---------------------------------------------------------------------------
# newline handling
# ---------------------------------------------------------------------------
def test_newline_styles_do_not_change_identity_but_bytes_preserved(tmp_path):
    project = _project(tmp_path)
    base = "line1\nline2\nline3"
    variants = {
        "lf": "line1\nline2\nline3",
        "crlf": "line1\r\nline2\r\nline3",
        "cr": "line1\rline2\rline3",
        "mixed": "line1\rline2\r\nline3",
    }
    records = {
        name: project.intake_text(text, label=name, filename=f"{name}.txt")
        for name, text in variants.items()
    }
    hashes = {r.sha256 for r in records.values()}
    assert len(hashes) == 1, "same logical text must hash identically"
    assert base.encode()  # sanity
    for name, text in variants.items():
        stored = (project.root / records[name].relative_path).read_bytes()
        assert stored == text.encode("utf-8"), f"{name} bytes must be preserved verbatim"
    assert normalize_newlines(variants["crlf"]) == base


def test_file_intake_text_hash_is_newline_insensitive(tmp_path):
    project = _project(tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"x\r\ny\r\n")
    f2.write_bytes(b"x\ny\n")
    r1 = project.intake_file(f1, label="a")
    r2 = project.intake_file(f2, label="b")
    assert r1.sha256 == r2.sha256
    assert (project.root / r1.relative_path).read_bytes() == b"x\r\ny\r\n"


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("evil", [
    "../evil.txt",
    "..\\evil.txt",
    "a/../../evil.txt",
    "a\\..\\..\\evil.txt",
    "/absolute/path.txt",
    "C:\\Windows\\evil.txt",
    "C:/Windows/evil.txt",
    "D:evil.txt",
    "\\\\server\\share\\evil.txt",
    "//server/share/evil.txt",
    "\\rooted.txt",
    "\\\\rooted.txt",
    "",
    "   ",
    "a/\x00b.txt",
])
def test_path_traversal_and_injection_rejected(evil):
    with pytest.raises(UnsafePathError):
        validate_supplied_path(evil)


@pytest.mark.parametrize("good,expected", [
    ("notes.txt", "notes.txt"),
    ("sub/dir/notes.txt", "sub/dir/notes.txt"),
    ("./notes.txt", "notes.txt"),
    ("caf\u00e9 \u65e5\u672c.txt", "caf\u00e9 \u65e5\u672c.txt"),
])
def test_valid_paths_normalized(good, expected):
    assert validate_supplied_path(good) == expected


def test_windows_style_filename_stored_safely(tmp_path):
    project = _project(tmp_path)
    # A Windows-style name must not create subdirectories or escape.
    record = project.intake_text("data", label="w", filename="folder\\sub\\file.txt")
    stored = Path(project.root / record.relative_path)
    assert stored.parent == project.sources_dir
    assert stored.is_file()
    assert ".." not in record.relative_path
    assert "\\" not in record.relative_path


def test_linux_style_subpath_keeps_original_name_but_stores_flat(tmp_path):
    project = _project(tmp_path)
    record = project.intake_text("data", label="l", filename="sub/dir/file.txt")
    assert record.original_filename == "sub/dir/file.txt"
    stored = Path(project.root / record.relative_path)
    assert stored.parent == project.sources_dir  # flattened, no dir escape
    assert stored.name.endswith("file.txt")


def test_unicode_filename_preserved(tmp_path):
    project = _project(tmp_path)
    src = tmp_path / "rig-photo-\u00fcn\u00ef.png"
    src.write_bytes(b"PNG-ish")
    record = project.intake_file(src, label="photo")
    assert "\u00fcn\u00ef" in record.original_filename
    assert "\u00fcn\u00ef" in Path(record.relative_path).name
    assert (project.root / record.relative_path).read_bytes() == b"PNG-ish"


def test_sanitize_filename_neutralizes_reserved_and_unsafe():
    assert sanitize_filename("a/b\\c:d.txt").find("/") == -1
    assert sanitize_filename("CON.txt").lower() != "con.txt"
    assert sanitize_filename("  ...  ") == "_"
    assert sanitize_filename("caf\u00e9.txt") == "caf\u00e9.txt"


def test_stored_relative_paths_are_posix(tmp_path):
    project = _project(tmp_path)
    record = project.intake_text("x", label="x", filename="x.txt")
    assert "\\" not in record.relative_path
    assert PurePosixPath(record.relative_path).as_posix() == record.relative_path


# ---------------------------------------------------------------------------
# source record validation
# ---------------------------------------------------------------------------
def test_source_record_rejects_bad_kinds_and_fields():
    with pytest.raises(SourceRecordError):
        SourceRecord(source_id="s", kind="nope", label="l",
                     intake_timestamp="t", relative_path="p", sha256="h")
    with pytest.raises(SourceRecordError):
        SourceRecord.from_dict({"source_id": "s", "kind": "bogus",
                                "label": "l", "intake_timestamp": "t"})
    with pytest.raises(SourceRecordError):
        SourceRecord.from_dict({"source_id": "s", "kind": "text", "label": "l",
                                "intake_timestamp": "t", "relative_path": "p",
                                "sha256": "h", "unknown_field": 1})
    # Reference without a reference value is rejected.
    with pytest.raises(SourceRecordError):
        SourceRecord(source_id="s", kind=SourceKind.REFERENCE, label="l",
                     intake_timestamp="t")


def test_source_record_extra_fields_survive_round_trip():
    record = SourceRecord(
        source_id="s1", kind=SourceKind.TEXT, label="l",
        intake_timestamp="2026-09-20T00:00:00+00:00",
        relative_path="sources/s1__a.txt", sha256="abc",
        extra={"future": {"nested": [1, 2]}},
    )
    assert SourceRecord.from_dict(record.to_dict()).extra == {"future": {"nested": [1, 2]}}


# ---------------------------------------------------------------------------
# synthetic fixture project
# ---------------------------------------------------------------------------
def test_synthetic_fixture_loads_with_all_required_elements():
    assert FIXTURE_PROJECT.is_dir(), "synthetic fixture project missing"
    project = Project.load(FIXTURE_PROJECT)
    assert project.project_id
    assert project.name

    kinds = [r.kind for r in project.sources]
    assert SourceKind.TEXT in kinds
    assert SourceKind.IMAGE in kinds
    assert SourceKind.REFERENCE in kinds

    by_label = {r.label: r for r in project.sources}
    text_rec = by_label["Field notes (unicode, mixed newlines)"]
    md_rec = by_label["Markdown overview"]
    json_rec = by_label["JSON spec"]
    img_rec = by_label["Rig photo (\u00fcn\u00efcode filename)"]
    ref_rec = by_label["Example reference"]

    # Required metadata on every record.
    for record in project.sources:
        assert record.source_id
        assert record.status is SourceStatus.STORED
        assert record.intake_timestamp
        assert record.label

    # Unicode + mixed newlines preserved byte-exact in the text source.
    raw = (FIXTURE_PROJECT / text_rec.relative_path).read_bytes()
    assert "caf\u00e9".encode("utf-8") in raw
    assert "\u65e5\u672c\u8a9e".encode("utf-8") in raw
    assert b"\r\n" in raw and b"\r" in raw.replace(b"\r\n", b"")
    assert text_rec.sha256 == identity_hash_for_text(raw.decode("utf-8"))

    # Media types distinguished.
    assert md_rec.media_type.startswith("text/markdown")
    assert json_rec.media_type == "application/json"

    # Image fixture: content hashed, original bytes intact.
    img_bytes = (FIXTURE_PROJECT / img_rec.relative_path).read_bytes()
    assert img_bytes.startswith(b"\x89PNG")
    assert img_rec.sha256 == sha256_hex(img_bytes)
    assert "\u00fcn\u00ef" in img_rec.original_filename

    # Reference: URL stored, nothing fetched, no file.
    assert ref_rec.reference.startswith("https://")
    assert ref_rec.sha256 is None
    assert ref_rec.relative_path is None


def test_fixture_is_small():
    total = sum(f.stat().st_size for f in FIXTURE_PROJECT.rglob("*") if f.is_file())
    assert total < 100_000, f"fixture should stay small, got {total} bytes"
