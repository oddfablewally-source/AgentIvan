"""Brick 10: ProjectService unified operations facade tests.

The facade must coordinate the existing AgentIvan layers -- never
reimplement them. These tests prove delegation: the service routes to
the Brick 2-9 implementations, returns their own result types, preserves
their errors, keeps reads read-only, persists mutations through the
existing mechanisms, and adds no network, LLM, UI, cache, threads, or
persistence of its own.

Reuses the Brick 5/6/7/9 "Nefarious Rig" fixture (extended with unicode,
binary, and reference sources). Stdlib only.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import shutil
import socket
import threading
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from src.app.product.agentivan import (
    ACCEPT_INCOMING,
    KEEP_EXISTING,
    AgentIvanWorkspace,
    BuildPack,
    BuildPackQuery,
    BuildRecord,
    CatalogError,
    ExtractionSet,
    InfoStatus,
    IntakeError,
    Project,
    ProjectError,
    ProjectIdentity,
    ProjectService,
    ProjectServiceError,
    ReconciliationError,
    ReconciliationSession,
    ReconciliationState,
    ResolutionStatus,
    SnapshotError,
    SnapshotResult,
    SourceKind,
    SourceRecord,
    WorkspaceError,
    diff_packs,
    export_project_markdown,
    export_project_pdf,
    export_snapshot,
    import_snapshot,
)
import src.app.product.agentivan.service as service_module
from src.app.product.agentivan.reconciliation import _CATEGORY_SECTION_FIELD
from tests.test_candidate_reconciliation import SOURCE_A, SOURCE_B
from tests.test_snapshot import (
    MANIFEST_NAME,
    SNAPSHOT_FORMAT_VERSION,
    _members_of,
    _prepare_brick9,
    _rebuild_archive,
)

UNICODE_PROBE = "naïve résumé — café ☃\nsecond line ✓\n"


def _service_fixture(root: Path) -> dict:
    """Full Nefarious Rig project hosted inside a real workspace.

    Builds the Brick 9 fixture (Brick 5/6/7 state + unicode/binary/
    reference extensions + scripted decisions) at a scratch location,
    then relocates the project directory under the workspace -- a move
    the architecture explicitly supports, since identity lives in
    project.json, never in the path.
    """
    workspace = AgentIvanWorkspace(root / "ws")
    fixture = _prepare_brick9(root / "build")
    project = fixture["project"]
    target = workspace.project_root(project.project_id)
    shutil.move(str(project.root), str(target))
    project.root = target
    project.save()
    service = ProjectService(workspace, project_id=project.project_id)
    fixture.update({"workspace": workspace, "service": service})
    return fixture


@pytest.fixture
def rig(tmp_path: Path) -> dict:
    return _service_fixture(tmp_path)


def _pending(service: ProjectService, minimum: int = 1) -> list:
    candidates = service.pending_candidates()
    assert len(candidates) >= minimum, "fixture needs pending candidates"
    return candidates


def _acceptable(service: ProjectService) -> object:
    """First pending candidate the lower layer allows accepting.

    Brick 5 deliberately refuses PROJECT_IDENTITY and GENERAL
    candidates (no safe section mapping); the facade must preserve
    that, so decision tests pick a candidate the layer accepts.
    """
    for candidate in _pending(service):
        if candidate.category in _CATEGORY_SECTION_FIELD:
            return candidate
    raise AssertionError("fixture needs an acceptable pending candidate")


def _canonical_hashes(project: Project) -> dict:
    """Hashes of canonical state; regenerable exports/ excluded."""
    result = {"project.json": hashlib.sha256(
        project.project_json_path.read_bytes()
    ).hexdigest()}
    for sub in ("sources", "extractions", "buildpack", "reconciliation"):
        entries = []
        directory = project.root / sub
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    entries.append(
                        (
                            path.relative_to(project.root).as_posix(),
                            hashlib.sha256(path.read_bytes()).hexdigest(),
                        )
                    )
        result[sub] = entries
    return result


# ---------------------------------------------------------------------------
# 1-2: imports and construction
# ---------------------------------------------------------------------------


def test_service_imports() -> None:
    assert service_module.ProjectService is ProjectService
    assert service_module.ProjectServiceError is ProjectServiceError
    assert "ProjectService" in service_module.__all__
    assert "ProjectServiceError" in service_module.__all__


def test_service_construction_forms(rig: dict) -> None:
    workspace = rig["workspace"]
    project_id = rig["service"].project_id

    by_id = ProjectService(workspace, project_id=project_id)
    assert by_id.project_id == project_id
    assert isinstance(by_id.project, Project)

    by_object = ProjectService(workspace, project=rig["project"])
    assert by_object.project_id == project_id
    assert by_object.project is rig["project"]  # never a copy

    workspace.set_active_project(project_id)
    by_active = ProjectService(workspace)
    assert by_active.project_id == project_id


def test_service_construction_without_project_fails(tmp_path: Path) -> None:
    workspace = AgentIvanWorkspace(tmp_path / "ws")
    with pytest.raises(ProjectServiceError):
        ProjectService(workspace)
    with pytest.raises(ProjectServiceError):
        ProjectService(workspace, project=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3-4: project lifecycle
# ---------------------------------------------------------------------------


def test_create_project(rig: dict) -> None:
    service = rig["service"]
    workspace = rig["workspace"]
    old_id = service.project_id

    project = service.create_project("Second Rig")

    assert project.project_id != old_id
    assert service.project_id == project.project_id
    assert workspace.active_project_id == project.project_id
    assert (workspace.project_root(project.project_id) / "project.json").is_file()


def test_create_classmethod_bootstraps_fresh_workspace(tmp_path: Path) -> None:
    workspace = AgentIvanWorkspace(tmp_path / "ws")
    service = ProjectService.create(workspace, "Fresh Rig")
    assert isinstance(service.project, Project)
    assert service.project_id == service.project.project_id
    assert workspace.active_project_id == service.project_id


def test_open_project_sets_active_and_rebinds(rig: dict) -> None:
    service = rig["service"]
    workspace = rig["workspace"]
    rig_id = service.project_id
    other = service.create_project("Other Rig")

    reopened = service.open_project(rig_id)

    assert reopened.project_id == rig_id
    assert service.project_id == rig_id
    assert workspace.active_project_id == rig_id
    assert other.project_id != rig_id


def test_load_project_does_not_change_active(rig: dict) -> None:
    service = rig["service"]
    workspace = rig["workspace"]
    rig_id = service.project_id
    other = service.create_project("Other Rig")

    loaded = service.load_project(rig_id)

    assert loaded.project_id == rig_id
    assert service.project_id == rig_id
    assert workspace.active_project_id == other.project_id  # untouched
    assert service.active_project_id == other.project_id  # exposed, not copied


# ---------------------------------------------------------------------------
# 5-6: identity and Build Pack reads
# ---------------------------------------------------------------------------


def test_get_project_identity(rig: dict) -> None:
    service = rig["service"]
    identity = service.get_project_identity()
    assert isinstance(identity, ProjectIdentity)
    assert identity.project_id == service.project_id
    assert identity == BuildPackQuery.for_project(service.project).identity()
    # identity is the canonical project_id from project.json, not a path
    assert identity.project_id == rig["project"].project_id


def test_get_build_pack(rig: dict) -> None:
    service = rig["service"]
    pack = service.get_build_pack()
    assert isinstance(pack, BuildPack)
    assert pack.sections.keys() == service.project.buildpack_store.load().sections.keys()


# ---------------------------------------------------------------------------
# 7-9: source intake delegation (Brick 2)
# ---------------------------------------------------------------------------


def test_add_text_delegates_to_project_intake(rig: dict) -> None:
    service = rig["service"]
    real = Project.intake_text
    calls: list = []

    def spy(self, text, **kwargs):
        calls.append((text, kwargs))
        return real(self, text, **kwargs)

    with mock.patch.object(Project, "intake_text", spy):
        record = service.add_text("service probe text", label="probe")

    assert len(calls) == 1  # exactly one intake: no duplication
    assert calls[0][0] == "service probe text"
    assert calls[0][1]["label"] == "probe"
    assert isinstance(record, SourceRecord)
    # persisted through the existing save path, visible on reload
    assert Project.load(service.project.root).get_source(record.source_id).label == "probe"


def test_add_file_delegates_and_preserves_bytes(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    payload = bytes(range(256)) + "café ☃".encode("utf-8")
    source_file = tmp_path / "capture.bin"
    source_file.write_bytes(payload)

    record = service.add_file(source_file, label="binary probe")

    assert record.kind is SourceKind.FILE
    stored = service.project.catalog.read_bytes(record.source_id)
    assert stored == payload


def test_add_reference_delegates_and_never_fetches(rig: dict) -> None:
    service = rig["service"]
    record = service.add_reference("https://example.invalid/rig/plan", label="plan ref")
    assert record.kind is SourceKind.REFERENCE
    assert record.reference == "https://example.invalid/rig/plan"
    assert service.get_source(record.source_id).reference == record.reference


# ---------------------------------------------------------------------------
# 10-14: source inspection delegation (Brick 3)
# ---------------------------------------------------------------------------


def test_list_sources(rig: dict) -> None:
    service = rig["service"]
    expected = service.project.catalog.list()
    assert [r.source_id for r in service.list_sources()] == [
        r.source_id for r in expected
    ]
    texts = service.list_sources(kind=SourceKind.TEXT)
    assert texts == service.project.catalog.list(kind=SourceKind.TEXT)
    assert all(r.kind is SourceKind.TEXT for r in texts)


def test_get_source(rig: dict) -> None:
    service = rig["service"]
    source_id = rig["source_ids"]["Session notes"]
    assert service.get_source(source_id) == service.project.catalog.get(source_id)
    with pytest.raises(CatalogError):
        service.get_source("does-not-exist")


def test_search_sources(rig: dict) -> None:
    service = rig["service"]
    assert service.search_sources("café") == service.project.catalog.search("café")
    assert any(
        r.source_id == rig["unicode_source_id"] for r in service.search_sources("café")
    )


def test_verify_source(rig: dict) -> None:
    service = rig["service"]
    source_id = rig["source_ids"]["Session notes"]
    status = service.verify_source(source_id)
    assert status == service.project.catalog.verify(source_id)
    assert status.state.value == "intact"


def test_verify_all_sources(rig: dict) -> None:
    service = rig["service"]
    assert service.verify_all_sources() == service.project.catalog.verify_all()
    assert all(s.state.value == "intact" for s in service.verify_all_sources())


# ---------------------------------------------------------------------------
# 15: extraction delegation (Brick 4)
# ---------------------------------------------------------------------------


def test_extract_delegates_runs_and_persists(rig: dict) -> None:
    service = rig["service"]
    extraction_set = service.extract()
    assert isinstance(extraction_set, ExtractionSet)
    assert extraction_set == service.project.extractions.load()
    assert extraction_set == service.get_extraction_set()
    assert len(extraction_set.all_items()) > 0


def test_extract_uses_deterministic_extractor_only(rig: dict) -> None:
    service = rig["service"]
    assert "extractor" not in inspect.signature(service.extract).parameters
    names = {result.extractor_name for result in service.extract().results}
    assert names == {"deterministic"}


def test_no_automatic_extraction_or_side_effects(tmp_path: Path) -> None:
    workspace = AgentIvanWorkspace(tmp_path / "ws")
    service = ProjectService.create(workspace, "Quiet Rig")
    service.add_text("plain notes", label="notes")
    project = service.project
    assert not project.extractions.exists()
    assert not project.buildpack_store.exists()
    assert not project.reconciliation_store.exists()
    assert service.pending_candidates() == []


# ---------------------------------------------------------------------------
# 16-21: reconciliation delegation (Brick 5)
# ---------------------------------------------------------------------------


def test_import_candidates_explicit_and_idempotent(tmp_path: Path) -> None:
    workspace = AgentIvanWorkspace(tmp_path / "ws")
    service = ProjectService.create(workspace, "Import Rig")
    service.add_text(SOURCE_A, label="Session notes")
    service.add_text(SOURCE_B, label="Follow-up notes")
    service.extract()

    first = service.import_candidates()
    assert len(first) > 0
    assert {c.reconciliation_id for c in service.pending_candidates()} == {
        c.reconciliation_id for c in first
    }
    assert service.import_candidates() == []  # idempotent, per lower layer
    assert len(service.pending_candidates()) == len(first)


def test_accept_candidate_delegates(rig: dict) -> None:
    service = rig["service"]
    candidate = _acceptable(service)
    real_accept = ReconciliationSession.accept
    calls: list = []

    def spy(self, reconciliation_id, *, note=None):
        calls.append((reconciliation_id, note))
        return real_accept(self, reconciliation_id, note=note)

    with mock.patch.object(ReconciliationSession, "accept", spy):
        record = service.accept_candidate(candidate.reconciliation_id, note="svc accept")

    assert calls == [(candidate.reconciliation_id, "svc accept")]
    decided = service.get_candidate(candidate.reconciliation_id)
    assert decided.state in (ReconciliationState.ACCEPTED, ReconciliationState.DUPLICATE)
    if decided.state is ReconciliationState.ACCEPTED:
        assert isinstance(record, BuildRecord)
        assert service.get_build_pack() is not None
    else:
        assert record is None  # exact duplicate: lower-layer semantics kept


def test_reject_candidate_delegates(rig: dict) -> None:
    service = rig["service"]
    candidate = _pending(service)[0]
    records_before = len(service.get_build_pack().change_history)

    rejected = service.reject_candidate(candidate.reconciliation_id, reason="out of scope")

    assert rejected.state is ReconciliationState.REJECTED
    assert rejected.decision_note == "out of scope"
    assert service.get_candidate(candidate.reconciliation_id).state is ReconciliationState.REJECTED
    assert len(service.get_build_pack().change_history) == records_before


def test_defer_candidate_delegates(rig: dict) -> None:
    service = rig["service"]
    candidate = _pending(service)[0]

    deferred = service.defer_candidate(candidate.reconciliation_id, reason="later")

    assert deferred.state is ReconciliationState.DEFERRED
    assert service.get_candidate(candidate.reconciliation_id).state is ReconciliationState.DEFERRED


def test_flag_contradiction_delegates(rig: dict) -> None:
    service = rig["service"]
    candidate = _pending(service)[0]

    contradiction = service.flag_contradiction(
        candidate.reconciliation_id, description="service flagged conflict"
    )

    assert contradiction.state is ResolutionStatus.UNRESOLVED
    assert service.get_contradiction(contradiction.contradiction_id).description == (
        "service flagged conflict"
    )
    assert service.get_candidate(candidate.reconciliation_id).state is (
        ReconciliationState.CONTRADICTED
    )


def test_resolve_contradiction_delegates(rig: dict) -> None:
    service = rig["service"]
    candidate = _pending(service)[0]
    contradiction = service.flag_contradiction(
        candidate.reconciliation_id, description="resolve me"
    )
    pack_before = service.get_build_pack().to_dict()

    resolved = service.resolve_contradiction(
        contradiction.contradiction_id, resolution=KEEP_EXISTING, note="stands"
    )

    assert resolved.state is ResolutionStatus.RESOLVED
    assert resolved.resolution == KEEP_EXISTING
    # keep_existing leaves the Build Pack untouched
    assert service.get_build_pack().to_dict() == pack_before


def test_fixture_unresolved_contradiction_visible(rig: dict) -> None:
    service = rig["service"]
    open_id = rig["contradiction_open"].contradiction_id
    contradiction = service.get_contradiction(open_id)
    assert contradiction.state is ResolutionStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# 22-25: query delegation (Brick 6)
# ---------------------------------------------------------------------------


def test_query_delegates(rig: dict) -> None:
    service = rig["service"]
    query = BuildPackQuery.for_project(service.project)
    assert [h.to_dict() for h in service.query(text="28 inches")] == [
        h.to_dict() for h in query.records(text="28 inches")
    ]
    assert service.query(section="measurements") == query.records(section="measurements")


def test_search_delegates(rig: dict) -> None:
    service = rig["service"]
    query = BuildPackQuery.for_project(service.project)
    assert [h.to_dict() for h in service.search("bracket")] == [
        h.to_dict() for h in query.search("bracket")
    ]


def test_what_do_we_know_delegates(rig: dict) -> None:
    service = rig["service"]
    report = service.what_do_we_know()
    expected = BuildPackQuery.for_project(service.project).what_do_we_know()
    assert report.to_dict() == expected.to_dict()
    assert set(report.to_dict()) == {s.value for s in InfoStatus}


def test_what_remains_delegates(rig: dict) -> None:
    service = rig["service"]
    assert service.what_remains().to_dict() == (
        BuildPackQuery.for_project(service.project).what_remains().to_dict()
    )


def test_get_changes_and_diff_delegates(rig: dict) -> None:
    service = rig["service"]
    before = service.get_build_pack()
    candidate = _acceptable(service)
    service.accept_candidate(candidate.reconciliation_id, note="diff probe")
    after = service.get_build_pack()

    expected = diff_packs(before, after)
    assert service.get_changes(before, after).to_dict() == expected.to_dict()
    assert service.diff_build_packs(before, after).to_dict() == expected.to_dict()
    assert not service.get_changes(before, after).is_empty()
    assert service.get_changes(after, after).is_empty()


# ---------------------------------------------------------------------------
# 26-27: export delegation (Brick 7 / Brick 8)
# ---------------------------------------------------------------------------


def test_export_markdown_delegates(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    via_service = tmp_path / "service.md"
    via_direct = tmp_path / "direct.md"
    service.export_markdown(via_service)
    export_project_markdown(service.project, via_direct)
    assert via_service.read_bytes() == via_direct.read_bytes()
    # Brick 7 regression: canonical content renders through the facade
    text = via_service.read_text(encoding="utf-8")
    assert "Nefarious Rig" in text
    assert "28 inches" in text


def test_export_pdf_delegates(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    via_service = tmp_path / "service.pdf"
    via_direct = tmp_path / "direct.pdf"
    service.export_pdf(via_service)
    export_project_pdf(service.project, via_direct)
    assert via_service.read_bytes() == via_direct.read_bytes()
    # Brick 8 regression: a real PDF document comes out of the facade
    assert via_service.read_bytes().startswith(b"%PDF")
    assert via_service.stat().st_size > 2000


# ---------------------------------------------------------------------------
# 28-29: snapshot delegation (Brick 9)
# ---------------------------------------------------------------------------


def test_export_snapshot_delegates(rig: dict) -> None:
    service = rig["service"]
    result = service.export_snapshot()
    assert isinstance(result, SnapshotResult)
    assert result.data == export_snapshot(service.project).data
    assert result.project_id == service.project_id


def test_snapshot_manifest_contract(rig: dict) -> None:
    data = rig["service"].export_snapshot().data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
    assert names[0] == MANIFEST_NAME
    manifest = json.loads(
        zipfile.ZipFile(io.BytesIO(data)).read(MANIFEST_NAME).decode("utf-8")
    )
    assert manifest["format_version"] == SNAPSHOT_FORMAT_VERSION
    assert manifest["project_id"] == rig["service"].project_id


def test_import_snapshot_roundtrip(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    archive = service.export_snapshot().data
    restored = ProjectService.import_snapshot(archive, tmp_path / "restored")
    assert isinstance(restored, Project)
    assert restored.project_id == service.project_id
    relocated = ProjectService(rig["workspace"], project=restored)
    assert relocated.what_do_we_know().to_dict() == service.what_do_we_know().to_dict()
    assert [r.source_id for r in relocated.list_sources()] == [
        r.source_id for r in service.list_sources()
    ]


def test_import_snapshot_uses_brick9_importer(rig: dict, tmp_path: Path) -> None:
    archive = rig["service"].export_snapshot().data
    with mock.patch(
        "src.app.product.agentivan.service.import_snapshot", wraps=import_snapshot
    ) as spy:
        restored = ProjectService.import_snapshot(archive, tmp_path / "restored")
    spy.assert_called_once()
    assert restored.project_id == rig["service"].project_id


def test_import_snapshot_rejects_invalid_archive(rig: dict, tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        ProjectService.import_snapshot(b"not a zip", tmp_path / "restored")
    members = _members_of(rig["service"].export_snapshot().data)
    payload = bytearray(members[MANIFEST_NAME])
    payload[10] ^= 0xFF
    members[MANIFEST_NAME] = bytes(payload)
    with pytest.raises(SnapshotError):
        ProjectService.import_snapshot(_rebuild_archive(members), tmp_path / "tampered")


def test_import_snapshot_rejects_unsafe_paths(rig: dict, tmp_path: Path) -> None:
    members = _members_of(rig["service"].export_snapshot().data)
    members["../evil.txt"] = b"escape"
    with pytest.raises(SnapshotError):
        ProjectService.import_snapshot(_rebuild_archive(members), tmp_path / "evil")


def test_import_snapshot_rejects_existing_destination(rig: dict, tmp_path: Path) -> None:
    archive = rig["service"].export_snapshot().data
    dest = tmp_path / "taken"
    ProjectService.import_snapshot(archive, dest)
    with pytest.raises(SnapshotError):
        ProjectService.import_snapshot(archive, dest)


# ---------------------------------------------------------------------------
# 30-31: read-only and mutation boundaries
# ---------------------------------------------------------------------------


def test_read_operations_do_not_mutate_state(rig: dict) -> None:
    service = rig["service"]
    before = _canonical_hashes(service.project)
    source_id = rig["source_ids"]["Session notes"]

    service.get_project_identity()
    service.get_build_pack()
    service.buildpack_query()
    service.get_source(source_id)
    service.list_sources()
    service.list_sources(kind=SourceKind.TEXT)
    service.search_sources("notes")
    service.verify_source(source_id)
    service.verify_all_sources()
    service.get_extraction_set()
    service.pending_candidates()
    service.get_candidate(service.pending_candidates()[0].reconciliation_id)
    service.get_contradiction(rig["contradiction_open"].contradiction_id)
    service.query(text="inches")
    service.search("bracket")
    service.what_do_we_know()
    service.what_remains()
    query = service.buildpack_query()
    query.sections()
    query.revisions()
    query.contradictions()
    query.deferred_candidates()

    assert _canonical_hashes(service.project) == before


def test_exports_do_not_mutate_canonical_state(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    before = _canonical_hashes(service.project)
    service.export_markdown(tmp_path / "a.md")
    service.export_pdf(tmp_path / "a.pdf")
    service.export_snapshot(tmp_path / "a.zip")
    service.export_snapshot()
    assert _canonical_hashes(service.project) == before


def test_mutations_persist_through_existing_mechanism(rig: dict) -> None:
    service = rig["service"]
    root = service.project.root
    record = service.add_text("persisted probe", label="probe")
    candidate = _acceptable(service)
    service.accept_candidate(candidate.reconciliation_id, note="persist me")

    reloaded = Project.load(root)
    assert reloaded.get_source(record.source_id).label == "probe"
    assert len(reloaded.buildpack_store.load().change_history) > 0
    assert reloaded.reconciliation_store.load().get_candidate(
        candidate.reconciliation_id
    ).state in (ReconciliationState.ACCEPTED, ReconciliationState.DUPLICATE)


# ---------------------------------------------------------------------------
# 32-34: identity, duplication, isolation
# ---------------------------------------------------------------------------


def test_service_does_not_duplicate_project_identity(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    assert service.project_id == service.project.project_id
    assert service.get_project_identity().project_id == service.project_id
    archive = service.export_snapshot().data
    restored = ProjectService.import_snapshot(archive, tmp_path / "idem")
    assert restored.project_id == service.project_id
    seen = {service.project_id}
    for name in ("Other", "Third"):
        created = ProjectService.create(rig["workspace"], name)
        assert created.project_id not in seen  # fresh identity every time
        seen.add(created.project_id)


def test_service_does_not_create_duplicate_source_records(rig: dict) -> None:
    service = rig["service"]
    before = len(service.list_sources())
    record = service.add_text("no duplicates here", label="once")
    after = service.list_sources()
    assert len(after) == before + 1  # exactly one record added
    assert [r.source_id for r in after].count(record.source_id) == 1
    assert len({r.source_id for r in after}) == len(after)


def test_project_scoped_isolation(rig: dict) -> None:
    service_a = rig["service"]
    workspace = rig["workspace"]
    service_b = ProjectService.create(workspace, "Second Rig")
    assert service_b.project_id != service_a.project_id

    hash_before = hashlib.sha256(
        service_a.project.project_json_path.read_bytes()
    ).hexdigest()
    count_before = len(service_a.list_sources())

    service_b.add_text("belongs to B only", label="b-note")

    assert len(service_a.list_sources()) == count_before
    assert (
        hashlib.sha256(service_a.project.project_json_path.read_bytes()).hexdigest()
        == hash_before
    )
    assert len(service_b.list_sources()) == 1
    # rebinding one service never disturbs the other
    service_a.load_project(service_a.project_id)
    assert service_a.project_id != service_b.project_id
    # creating B made B the workspace's active project; A is unaffected
    assert workspace.active_project_id == service_b.project_id


# ---------------------------------------------------------------------------
# 35: lower-layer errors stay identifiable
# ---------------------------------------------------------------------------


def test_lower_layer_errors_remain_identifiable(rig: dict) -> None:
    service = rig["service"]
    with pytest.raises(CatalogError) as excinfo:
        service.get_source("missing")
    assert type(excinfo.value) is CatalogError
    assert not isinstance(excinfo.value, ProjectServiceError)

    with pytest.raises(ReconciliationError) as excinfo2:
        service.accept_candidate("rc-missing")
    assert type(excinfo2.value) is ReconciliationError

    with pytest.raises(WorkspaceError) as excinfo3:
        service.load_project("0" * 32)
    assert type(excinfo3.value) is WorkspaceError

    with pytest.raises(IntakeError):
        service.add_text(None, label="empty")  # Brick 2 validation, unwrapped


# ---------------------------------------------------------------------------
# 36-37: determinism
# ---------------------------------------------------------------------------


def test_deterministic_repeated_reads(rig: dict) -> None:
    service = rig["service"]
    assert service.what_do_we_know().to_dict() == service.what_do_we_know().to_dict()
    assert service.what_remains().to_dict() == service.what_remains().to_dict()
    first = [r.to_dict() for r in service.list_sources()]
    assert [r.to_dict() for r in service.list_sources()] == first
    assert [h.to_dict() for h in service.query(text="inches")] == [
        h.to_dict() for h in service.query(text="inches")
    ]


def test_deterministic_output_delegation(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    first = service.export_snapshot()
    second = service.export_snapshot()
    assert first.data == second.data
    assert first.archive_sha256 == second.archive_sha256

    first_md = tmp_path / "first.md"
    second_md = tmp_path / "second.md"
    service.export_markdown(first_md)
    service.export_markdown(second_md)
    assert first_md.read_bytes() == second_md.read_bytes()


# ---------------------------------------------------------------------------
# 38-39: relocation and unicode
# ---------------------------------------------------------------------------


def test_relocation_still_works(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    archive = service.export_snapshot().data
    restored = ProjectService.import_snapshot(archive, tmp_path / "elsewhere")
    relocated = ProjectService(rig["workspace"], project=restored)
    assert relocated.project_id == service.project_id
    assert relocated.get_project_identity().project_id == service.project_id
    assert relocated.what_do_we_know().to_dict() == service.what_do_we_know().to_dict()
    assert relocated.verify_all_sources() == service.verify_all_sources()


def test_unicode_project_and_source_handling(rig: dict) -> None:
    service = rig["service"]
    assert "café ☃" in service.project.name
    assert service.get_project_identity().project_id == service.project_id
    record = service.add_text(UNICODE_PROBE, label="tëst ☃")
    assert service.get_source(record.source_id).label == "tëst ☃"
    assert any(
        r.source_id == rig["unicode_source_id"] for r in service.search_sources("café")
    )
    # catalog search covers documented metadata (label), not raw content
    assert any(r.source_id == record.source_id for r in service.search_sources("tëst"))


# ---------------------------------------------------------------------------
# 40-44: no network, no LLM, no semantics, no new persistence, no threads
# ---------------------------------------------------------------------------


def test_no_network(rig: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    service = rig["service"]
    service.add_reference("https://example.invalid/no-fetch", label="offline ref")
    service.list_sources()
    service.search_sources("notes")
    service.verify_all_sources()
    service.what_do_we_know()
    service.what_remains()
    service.export_markdown(tmp_path / "off.md")
    service.export_pdf(tmp_path / "off.pdf")
    service.export_snapshot()


def test_no_llm_or_semantic_search(rig: dict) -> None:
    service = rig["service"]
    # extraction has no model hook: narrow signature, deterministic engine
    assert "extractor" not in inspect.signature(service.extract).parameters
    assert "llm" not in inspect.signature(service.extract).parameters
    names = {result.extractor_name for result in service.extract().results}
    assert names == {"deterministic"}
    # search is exact/substring only: a near-miss typo matches nothing
    assert service.search("seat hieght") == []
    assert service.search("seat height") == BuildPackQuery.for_project(
        service.project
    ).search("seat height")


def test_no_new_persistence_mechanism(tmp_path: Path) -> None:
    workspace = AgentIvanWorkspace(tmp_path / "ws")
    service = ProjectService.create(workspace, "Clean Rig")
    service.add_text(SOURCE_A, label="a")
    service.extract()
    service.import_candidates()
    service.accept_candidate(_acceptable(service).reconciliation_id)
    service.export_markdown()
    service.export_pdf()
    service.export_snapshot(tmp_path / "s.zip")

    allowed = {
        "project.json",
        "sources",
        "extractions",
        "buildpack",
        "reconciliation",
        "exports",
    }
    actual = {entry.name for entry in service.project.root.iterdir()}
    assert actual <= allowed, actual
    assert not any(entry.name.startswith(".") for entry in service.project.root.iterdir())
    assert [
        path
        for path in service.project.root.rglob("*")
        if path.suffix in {".db", ".sqlite", ".sqlite3"}
    ] == []


def test_no_background_threads(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    before = {thread.ident for thread in threading.enumerate()}
    service.add_text("thread probe", label="t")
    service.extract()
    service.what_do_we_know()
    service.export_markdown(tmp_path / "t.md")
    service.export_snapshot()
    after = {thread.ident for thread in threading.enumerate()}
    assert after - before == set()


def test_service_module_has_no_banned_imports() -> None:
    source = Path(service_module.__file__).read_text(encoding="utf-8")
    for banned in ("import threading", "import asyncio", "import socket", "import urllib"):
        assert banned not in source


# ---------------------------------------------------------------------------
# 45-47: Brick 7/8/9 regressions already covered above; explicit pins
# ---------------------------------------------------------------------------


def test_brick7_markdown_regression_pinned(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    out = tmp_path / "pack.md"
    service.export_markdown(out)
    assert out.read_bytes() == export_project_markdown(service.project, tmp_path / "d.md").read_bytes()


def test_brick8_pdf_regression_pinned(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    out = tmp_path / "pack.pdf"
    service.export_pdf(out)
    assert out.read_bytes() == export_project_pdf(service.project, tmp_path / "d.pdf").read_bytes()


def test_brick9_snapshot_regression_pinned(rig: dict, tmp_path: Path) -> None:
    service = rig["service"]
    before = service.what_do_we_know().to_dict()
    archive = service.export_snapshot().data
    restored = ProjectService.import_snapshot(archive, tmp_path / "v9")
    assert ProjectService(rig["workspace"], project=restored).what_do_we_know().to_dict() == before
