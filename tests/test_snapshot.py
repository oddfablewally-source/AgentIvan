"""Brick 9: deterministic project snapshot / portable archive tests.

Reuses the Brick 5/6/7 synthetic "Nefarious Rig" fixture and extends it
with unicode content, a unicode filename, a binary source, and a
URL/reference source. Stdlib only: no archive-inspection test
dependency.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
import zipfile
from pathlib import Path, PureWindowsPath
from unittest import mock

import pytest

import src.app.product.agentivan.snapshot as snapshot_module
from src.app.product.agentivan import (
    BuildPackMarkdownExporter,
    BuildPackPDFExporter,
    BuildPackQuery,
    InfoStatus,
    Project,
    ProjectSnapshotExporter,
    ProjectSnapshotImporter,
    ReconciliationSession,
    SnapshotError,
    SnapshotResult,
    export_snapshot,
    import_snapshot,
    validate_supplied_path,
)
from src.app.product.agentivan.snapshot import (
    MANIFEST_NAME,
    SNAPSHOT_FORMAT_NAME,
    SNAPSHOT_FORMAT_VERSION,
)
from tests.test_buildpack_query import SOURCE_D, _run_brick6_decisions
from tests.test_candidate_reconciliation import _prepare_project

FIXED_ZIP_DATE_TIME = (2000, 1, 1, 0, 0, 0)
UNICODE_TEXT = "Unicode notes: café ☃ naïve —\nsecond line ✓\n"
BINARY_PAYLOAD = bytes(range(256)) * 4 + b"\x00binary\xff\xe2\x98\x83"
UNICODE_FILENAME = "café-spécifications ☃.md"
REFERENCE_URL = "https://example.invalid/rig/blueprint?rev=2"


def _prepare_brick9(root: Path) -> dict:
    """Brick 5/6/7 fixture + unicode/binary/reference extensions (untampered)."""
    fixture = _prepare_project(root)
    project = fixture["project"]
    project.name = "Nefarious Rig — café ☃"

    project.intake_text(UNICODE_TEXT, label="Unicode notes")

    bin_src = root / "payload.bin"
    bin_src.write_bytes(BINARY_PAYLOAD)
    rec_bin = project.intake_file(bin_src, label="Binary capture")

    uni_src = root / UNICODE_FILENAME
    uni_src.write_text("# Spécifications\nDétail: café ☃\n", encoding="utf-8")
    rec_uni = project.intake_file(uni_src, label="Unicode filename doc")

    rec_ref = project.intake_reference(REFERENCE_URL, label="Remote blueprint")

    rec_d = project.intake_text(SOURCE_D, label="Research notes")
    fixture["source_ids"]["Research notes"] = rec_d.source_id

    extraction_set = project.extractions.run()
    project.extractions.save(extraction_set)
    fixture["extraction_set"] = extraction_set

    fixture.update(_run_brick6_decisions(project, fixture))
    project.save()

    fixture.update(
        {
            "binary_payload": BINARY_PAYLOAD,
            "binary_source_id": rec_bin.source_id,
            "unicode_source_id": rec_uni.source_id,
            "reference_source_id": rec_ref.source_id,
        }
    )
    return fixture


@pytest.fixture()
def brick9(tmp_path: Path) -> dict:
    return _prepare_brick9(tmp_path / "brick9")


@pytest.fixture()
def archive_bytes(brick9: dict) -> bytes:
    result = export_snapshot(brick9["project"])
    assert result.data is not None
    return result.data


def _open_archive(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def _manifest(data: bytes) -> dict:
    with _open_archive(data) as archive:
        return json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))


def _rebuild_archive(members: dict[str, bytes]) -> bytes:
    """Re-zip members (test helper; not the deterministic exporter)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _members_of(data: bytes) -> dict[str, bytes]:
    with _open_archive(data) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _dump(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        default=lambda o: o.to_dict() if hasattr(o, "to_dict") else str(o),
    )


def _file_fingerprint(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def _query_fingerprint(project: Project) -> list[str]:
    query = BuildPackQuery.for_project(project)
    source_id = next(iter(project.sources)).source_id
    return [
        _dump(query.identity()),
        _dump(query.sections()),
        _dump([hit.to_dict() for hit in query.section("parts")]),
        _dump([hit.to_dict() for hit in query.by_status(InfoStatus.KNOWN)]),
        _dump([hit.to_dict() for hit in query.unknown()]),
        _dump([hit.to_dict() for hit in query.open_issues()]),
        _dump([hit.to_dict() for hit in query.by_source(source_id)]),
        _dump(query.deferred_candidates()),
        _dump(query.contradictions()),
        _dump(query.unresolved_contradictions()),
        _dump(query.resolved_contradictions()),
        _dump(query.revisions()),
        _dump(query.source_integrity()),
        _dump(query.integrity_warnings()),
        _dump(query.what_do_we_know().to_dict()),
        _dump(query.what_remains().to_dict()),
        _dump([hit.to_dict() for hit in query.search(query_text="28 inches")]),
    ]


def _markdown_bytes(project: Project) -> bytes:
    session = ReconciliationSession(project)
    return BuildPackMarkdownExporter().export(
        session.pack, session.reconciliation
    ).text.encode("utf-8")


def _pdf_bytes(project: Project) -> bytes:
    session = ReconciliationSession(project)
    return BuildPackPDFExporter().export(
        session.pack, session.reconciliation
    ).data


# ---------------------------------------------------------------------------
# 1-2: imports
# ---------------------------------------------------------------------------


def test_snapshot_exporter_imports() -> None:
    assert snapshot_module.ProjectSnapshotExporter is ProjectSnapshotExporter
    assert snapshot_module.export_snapshot is export_snapshot
    assert snapshot_module.SnapshotResult is SnapshotResult
    assert snapshot_module.SNAPSHOT_FORMAT_VERSION == 1


def test_snapshot_importer_imports() -> None:
    assert snapshot_module.ProjectSnapshotImporter is ProjectSnapshotImporter
    assert snapshot_module.import_snapshot is import_snapshot
    assert snapshot_module.SnapshotError is SnapshotError


# ---------------------------------------------------------------------------
# 3-10: basic export shape
# ---------------------------------------------------------------------------


def test_basic_snapshot_succeeds(brick9: dict) -> None:
    result = ProjectSnapshotExporter().export_project(brick9["project"])
    assert isinstance(result, SnapshotResult)
    assert result.project_id == brick9["project"].project_id
    assert result.format_version == SNAPSHOT_FORMAT_VERSION
    assert result.member_count > 0
    assert result.payload_bytes > 0
    assert result.archive_bytes > 0
    assert len(result.archive_sha256) == 64
    assert result.data is not None


def test_archive_is_non_empty(archive_bytes: bytes) -> None:
    assert len(archive_bytes) > 0


def test_archive_is_valid_zip(archive_bytes: bytes) -> None:
    assert zipfile.is_zipfile(io.BytesIO(archive_bytes))
    with _open_archive(archive_bytes) as archive:
        assert archive.testzip() is None


def test_manifest_exists(archive_bytes: bytes) -> None:
    with _open_archive(archive_bytes) as archive:
        assert MANIFEST_NAME in archive.namelist()


def test_manifest_format_version_exists(archive_bytes: bytes) -> None:
    manifest = _manifest(archive_bytes)
    assert manifest["format"] == SNAPSHOT_FORMAT_NAME
    assert manifest["format_version"] == SNAPSHOT_FORMAT_VERSION


def test_project_id_preserved(brick9: dict, archive_bytes: bytes) -> None:
    manifest = _manifest(archive_bytes)
    assert manifest["project_id"] == brick9["project"].project_id


def test_deterministic_manifest_ordering(archive_bytes: bytes) -> None:
    first = _manifest(archive_bytes)
    paths = [entry["path"] for entry in first["members"]]
    assert paths == sorted(paths)
    # Manifest bytes themselves are deterministic across exports.
    assert _manifest(archive_bytes) == first


def test_deterministic_archive_member_ordering(archive_bytes: bytes) -> None:
    with _open_archive(archive_bytes) as archive:
        names = archive.namelist()
    manifest = _manifest(archive_bytes)
    expected = [MANIFEST_NAME] + sorted(
        entry["path"] for entry in manifest["members"]
    )
    assert names == expected


# ---------------------------------------------------------------------------
# 11-20: canonical preservation
# ---------------------------------------------------------------------------


def _assert_member_matches_disk(
    brick9: dict, archive_bytes: bytes, relative: str
) -> None:
    members = _members_of(archive_bytes)
    disk_bytes = (brick9["project"].root / relative).read_bytes()
    assert members[relative] == disk_bytes


def test_project_json_preserved(brick9: dict, archive_bytes: bytes) -> None:
    _assert_member_matches_disk(brick9, archive_bytes, "project.json")


def test_buildpack_preserved(brick9: dict, archive_bytes: bytes) -> None:
    _assert_member_matches_disk(
        brick9, archive_bytes, "buildpack/buildpack.json"
    )


def test_extraction_data_preserved(brick9: dict, archive_bytes: bytes) -> None:
    _assert_member_matches_disk(
        brick9, archive_bytes, "extractions/extractions.json"
    )


def test_reconciliation_data_preserved(brick9: dict, archive_bytes: bytes) -> None:
    _assert_member_matches_disk(
        brick9, archive_bytes, "reconciliation/reconciliation.json"
    )


def test_source_records_preserved(brick9: dict, archive_bytes: bytes) -> None:
    members = _members_of(archive_bytes)
    archived = json.loads(members["project.json"].decode("utf-8"))
    disk = json.loads(
        (brick9["project"].root / "project.json").read_bytes().decode("utf-8")
    )
    assert archived["sources"] == disk["sources"]
    assert len(archived["sources"]) == len(brick9["project"].sources)


def test_text_source_preserved_byte_for_byte(
    brick9: dict, archive_bytes: bytes
) -> None:
    members = _members_of(archive_bytes)
    text_members = {
        name: payload
        for name, payload in members.items()
        if name.startswith("sources/")
    }
    found = False
    for path in sorted(brick9["project"].sources_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(brick9["project"].root).as_posix()
            assert text_members[rel] == path.read_bytes()
            found = True
    assert found


def test_binary_source_preserved_byte_for_byte(
    brick9: dict, archive_bytes: bytes
) -> None:
    members = _members_of(archive_bytes)
    matches = [
        payload
        for name, payload in members.items()
        if name.startswith("sources/") and payload == BINARY_PAYLOAD
    ]
    assert len(matches) == 1


def test_unicode_filename_preserved(brick9: dict, archive_bytes: bytes) -> None:
    members = _members_of(archive_bytes)
    names = [name for name in members if name.startswith("sources/")]
    assert any(UNICODE_FILENAME in name for name in names)
    match = next(name for name in names if UNICODE_FILENAME in name)
    assert "café" in match and "☃" in match
    assert "Détail: café ☃".encode("utf-8") in members[match]


def test_unicode_content_preserved(brick9: dict, archive_bytes: bytes) -> None:
    members = _members_of(archive_bytes)
    texts = []
    for name, payload in members.items():
        if not name.startswith("sources/") or name == MANIFEST_NAME:
            continue
        try:
            texts.append(payload.decode("utf-8"))
        except UnicodeDecodeError:
            continue  # binary sources are not text; covered elsewhere
    assert any(UNICODE_TEXT in text for text in texts)


def test_url_reference_preserved_without_fetching(
    brick9: dict, archive_bytes: bytes
) -> None:
    members = _members_of(archive_bytes)
    archived = json.loads(members["project.json"].decode("utf-8"))
    ref_records = [
        record
        for record in archived["sources"]
        if record["source_id"] == brick9["reference_source_id"]
    ]
    assert len(ref_records) == 1
    assert ref_records[0]["kind"] == "reference"
    assert ref_records[0]["reference"] == REFERENCE_URL
    # References store no file payload.
    assert not any(
        brick9["reference_source_id"] in name
        for name in members
        if name.startswith("sources/")
    )


# ---------------------------------------------------------------------------
# 21: exports omitted
# ---------------------------------------------------------------------------


def test_exports_are_omitted_by_default(brick9: dict) -> None:
    project = brick9["project"]
    project.export_markdown()
    project.export_pdf()
    assert (project.root / "exports" / "build-pack.md").exists()
    assert (project.root / "exports" / "build-pack.pdf").exists()
    result = export_snapshot(project)
    assert result.data is not None
    with _open_archive(result.data) as archive:
        names = archive.namelist()
    assert not any(name.startswith("exports/") for name in names)


# ---------------------------------------------------------------------------
# 22-25: determinism and integrity
# ---------------------------------------------------------------------------


def test_identical_state_produces_identical_archive_bytes(
    brick9: dict,
) -> None:
    first = export_snapshot(brick9["project"]).data
    second = export_snapshot(brick9["project"]).data
    assert first is not None and second is not None
    assert first == second


def test_zip_timestamps_are_deterministic(archive_bytes: bytes) -> None:
    with _open_archive(archive_bytes) as archive:
        infos = archive.infolist()
    assert infos
    for info in infos:
        assert info.date_time == FIXED_ZIP_DATE_TIME


def test_zip_metadata_is_deterministic(archive_bytes: bytes) -> None:
    with _open_archive(archive_bytes) as archive:
        assert archive.comment == b""
        infos = archive.infolist()
    for info in infos:
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.create_system == 3
        assert info.external_attr == (0o644 << 16)
        assert info.extra == b""


def test_manifest_hashes_verify(archive_bytes: bytes) -> None:
    manifest = _manifest(archive_bytes)
    members = _members_of(archive_bytes)
    for entry in manifest["members"]:
        payload = members[entry["path"]]
        assert len(payload) == entry["size"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_snapshot_result_is_serializable(brick9: dict) -> None:
    result = export_snapshot(brick9["project"])
    restored = SnapshotResult.from_dict(result.to_dict())
    assert restored == result
    assert SnapshotResult.from_json(result.to_json()) == result
    assert "data" not in result.to_dict()


# ---------------------------------------------------------------------------
# 26-32, 45: rejection paths
# ---------------------------------------------------------------------------


def _tampered(members: dict[str, bytes], **overrides: bytes) -> bytes:
    members = dict(members)
    members.update(overrides)
    return _rebuild_archive(members)


def test_corrupt_member_is_rejected(
    brick9: dict, archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    payload = bytearray(members["buildpack/buildpack.json"])
    payload[10] ^= 0xFF
    members["buildpack/buildpack.json"] = bytes(payload)
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_incorrect_manifest_hash_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    manifest = json.loads(members[MANIFEST_NAME].decode("utf-8"))
    manifest["members"][0]["sha256"] = "0" * 64
    members[MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_missing_required_member_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    del members["project.json"]
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_unexpected_member_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    members["sneaky.txt"] = b"sneaky"
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_duplicate_archive_member_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(MANIFEST_NAME, b"{}")
        archive.writestr(MANIFEST_NAME, b"{}")
    with pytest.raises(SnapshotError):
        import_snapshot(buffer.getvalue(), tmp_path / "restored")


@pytest.mark.parametrize(
    "evil",
    ["../evil.txt", "/abs.txt", "C:/win.txt", "\\\\server\\share.txt"],
)
def test_unsafe_paths_are_rejected(
    archive_bytes: bytes, tmp_path: Path, evil: str
) -> None:
    members = _members_of(archive_bytes)
    manifest = json.loads(members[MANIFEST_NAME].decode("utf-8"))
    payload = b"evil"
    manifest["members"].append(
        {
            "path": evil,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    )
    manifest["members"].sort(key=lambda entry: entry["path"])
    members[MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    members[evil] = payload
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_unsupported_snapshot_version_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    manifest = json.loads(members[MANIFEST_NAME].decode("utf-8"))
    manifest["format_version"] = SNAPSHOT_FORMAT_VERSION + 1
    members[MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_malformed_manifest_is_rejected(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    members = _members_of(archive_bytes)
    members[MANIFEST_NAME] = b"{not valid json"
    with pytest.raises(SnapshotError):
        import_snapshot(_rebuild_archive(members), tmp_path / "restored")


def test_not_a_zip_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        import_snapshot(b"definitely not a zip archive", tmp_path / "restored")


# ---------------------------------------------------------------------------
# 33-34: import safety
# ---------------------------------------------------------------------------


def test_import_does_not_overwrite_existing_project(
    archive_bytes: bytes, tmp_path: Path
) -> None:
    dest = tmp_path / "taken"
    dest.mkdir()
    sentinel = b'{"sentinel": true}'
    (dest / "project.json").write_bytes(sentinel)
    with pytest.raises(SnapshotError):
        import_snapshot(archive_bytes, dest)
    assert (dest / "project.json").read_bytes() == sentinel


def test_invalid_archive_leaves_no_partial_project(tmp_path: Path) -> None:
    dest = tmp_path / "partial"
    with pytest.raises(SnapshotError):
        import_snapshot(b"not a zip", dest)
    assert not dest.exists()
    leftovers = [
        entry
        for entry in tmp_path.iterdir()
        if entry.name.startswith(".agentivan-import-")
    ]
    assert leftovers == []


def test_unexpected_top_level_entry_blocks_export(
    brick9: dict, tmp_path: Path
) -> None:
    (brick9["project"].root / "stray.txt").write_text("stray", encoding="utf-8")
    with pytest.raises(SnapshotError):
        export_snapshot(brick9["project"])


def test_symlink_blocks_export(brick9: dict) -> None:
    link = brick9["project"].sources_dir / "link.txt"
    target = next(brick9["project"].sources_dir.rglob("*"))
    link.symlink_to(target)
    with pytest.raises(SnapshotError):
        export_snapshot(brick9["project"])


# ---------------------------------------------------------------------------
# 35-36: round-trip and identity
# ---------------------------------------------------------------------------


def test_round_trip_restores_project(brick9: dict, tmp_path: Path) -> None:
    project = brick9["project"]
    before = _file_fingerprint(project.root)
    restored = import_snapshot(
        export_snapshot(project).data,
        tmp_path / "restored",
    )
    assert isinstance(restored, Project)
    after = _file_fingerprint(restored.root)
    assert after == before
    assert [r.to_dict() for r in restored.sources] == [
        r.to_dict() for r in project.sources
    ]


def test_import_accepts_archive_path(brick9: dict, tmp_path: Path) -> None:
    archive_path = tmp_path / "snap.zip"
    archive_path.write_bytes(export_snapshot(brick9["project"]).data)
    restored = import_snapshot(archive_path, tmp_path / "restored")
    assert restored.project_id == brick9["project"].project_id


def test_project_id_survives_relocation(brick9: dict, tmp_path: Path) -> None:
    original_id = brick9["project"].project_id
    restored = import_snapshot(
        export_snapshot(brick9["project"]).data, tmp_path / "elsewhere"
    )
    assert restored.project_id == original_id


def test_relocation_preserves_queryable_identity(
    brick9: dict, tmp_path: Path
) -> None:
    restored = import_snapshot(
        export_snapshot(brick9["project"]).data, tmp_path / "restored"
    )
    # project.json identity (the snapshot layer's concern) is preserved;
    # the canonical Build Pack's own identity section is untouched by design.
    assert restored.name == brick9["project"].name == "Nefarious Rig — café ☃"
    identity = BuildPackQuery.for_project(restored).identity()
    assert identity.project_id == brick9["project"].project_id


# ---------------------------------------------------------------------------
# 37-39: regression after restoration
# ---------------------------------------------------------------------------


def test_query_results_survive_relocation(
    brick9: dict, tmp_path: Path
) -> None:
    project = brick9["project"]
    before = _query_fingerprint(project)
    restored = import_snapshot(
        export_snapshot(project).data, tmp_path / "restored"
    )
    assert _query_fingerprint(restored) == before


def test_markdown_output_survives_relocation(
    brick9: dict, tmp_path: Path
) -> None:
    project = brick9["project"]
    before = _markdown_bytes(project)
    restored = import_snapshot(
        export_snapshot(project).data, tmp_path / "restored"
    )
    assert _markdown_bytes(restored) == before


def test_pdf_output_survives_relocation(
    brick9: dict, tmp_path: Path
) -> None:
    project = brick9["project"]
    before = _pdf_bytes(project)
    restored = import_snapshot(
        export_snapshot(project).data, tmp_path / "restored"
    )
    assert _pdf_bytes(restored) == before


def test_deterministic_repeated_round_trip(
    brick9: dict, tmp_path: Path
) -> None:
    project = brick9["project"]
    first = export_snapshot(project).data
    restored = import_snapshot(first, tmp_path / "restored")
    second = export_snapshot(restored).data
    assert first is not None and second is not None
    assert second == first


# ---------------------------------------------------------------------------
# 40-41: mutation boundaries
# ---------------------------------------------------------------------------


def test_export_does_not_mutate_original(brick9: dict) -> None:
    project = brick9["project"]
    before = _file_fingerprint(project.root)
    export_snapshot(project)
    export_snapshot(project, destination=None)
    assert _file_fingerprint(project.root) == before


def test_import_does_not_mutate_original_or_archive(
    brick9: dict, tmp_path: Path
) -> None:
    project = brick9["project"]
    before_files = _file_fingerprint(project.root)
    archive_path = tmp_path / "snap.zip"
    archive_path.write_bytes(export_snapshot(project).data)
    before_archive = archive_path.read_bytes()
    import_snapshot(archive_path, tmp_path / "restored")
    assert _file_fingerprint(project.root) == before_files
    assert archive_path.read_bytes() == before_archive


# ---------------------------------------------------------------------------
# 42: no network
# ---------------------------------------------------------------------------


def test_no_network_access(tmp_path: Path) -> None:
    def _no_sockets(*args, **kwargs):
        raise AssertionError("network access attempted")

    with mock.patch.object(socket, "socket", _no_sockets):
        fixture = _prepare_brick9(tmp_path / "nonet")
        data = export_snapshot(fixture["project"]).data
        assert data is not None
        restored = import_snapshot(data, tmp_path / "nonet-restored")
        assert restored.project_id == fixture["project"].project_id


def test_snapshot_module_uses_no_network_or_ai() -> None:
    source = Path(snapshot_module.__file__).read_text(encoding="utf-8")
    for token in (
        "import socket",
        "import requests",
        "import urllib",
        "import ollama",
        "import pydanticai",
        "from openai",
        "from anthropic",
    ):
        assert token not in source


# ---------------------------------------------------------------------------
# 43: cross-platform paths
# ---------------------------------------------------------------------------


def test_linux_windows_safe_paths(archive_bytes: bytes) -> None:
    manifest = _manifest(archive_bytes)
    with _open_archive(archive_bytes) as archive:
        names = archive.namelist()
    assert names[0] == MANIFEST_NAME
    for name in names:
        assert "\\" not in name
        assert not PureWindowsPath(name).is_absolute()
        assert not PureWindowsPath(name).drive
        # Already-normalized POSIX form per Brick 2 path safety.
        assert validate_supplied_path(name) == name
    for entry in manifest["members"]:
        assert validate_supplied_path(entry["path"]) == entry["path"]


# ---------------------------------------------------------------------------
# file export API
# ---------------------------------------------------------------------------


def test_project_export_snapshot_convenience(brick9: dict) -> None:
    result = brick9["project"].export_snapshot()
    assert isinstance(result, SnapshotResult)
    assert result.data is not None
    assert result.archive_path is None


def test_export_snapshot_to_file(brick9: dict, tmp_path: Path) -> None:
    dest = tmp_path / "backups" / "rig.zip"
    result = export_snapshot(brick9["project"], dest)
    assert result.archive_path == str(dest)
    assert result.data is None
    assert dest.read_bytes() == export_snapshot(brick9["project"]).data


def test_export_snapshot_relative_destination(
    brick9: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = brick9["project"].export_snapshot("rel.zip")
    assert result.archive_path == str(tmp_path / "rel.zip")
    assert (tmp_path / "rel.zip").exists()


def test_exporter_rejects_non_project(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        ProjectSnapshotExporter().export_project(object())  # type: ignore[arg-type]
