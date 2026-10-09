"""
Focused tests for the AgentIvan candidate reconciliation + Build Pack
assembly layer (Brick 5).

These prove the controlled transition from Brick 4 extracted candidates
to the Brick 1 canonical Build Pack:

- candidates are reviewed explicitly; nothing is applied automatically
- acceptance maps to the correct Build Pack section with status,
  provenance, measurement detail, and revision history preserved
- rejection/deferment keep candidates recorded with their reasons
- exact duplicates are identified, not duplicated
- contradictions are preserved (existing values never silently
  replaced) and can be explicitly resolved both ways
- project identity can never be silently changed
- persistence round-trips for both the pack and the decisions
- identical inputs produce equivalent results (deterministic assembly)
- raw sources and extraction records are never modified
- relocation keeps working; unicode survives; no network; no LLM

They do not modify the existing 305-test baseline. No network, no LLM,
no model calls, no OS-specific shell. Windows runtime verification
remains pending (see BRICK-5-RECONCILIATION.md).
"""

from __future__ import annotations

import hashlib
import shutil
import socket
import sys
from pathlib import Path
from unittest import mock

import pytest

from src.app.product.agentivan import (
    ACCEPT_INCOMING,
    CATEGORY_SECTION_MAP,
    KEEP_EXISTING,
    BuildPack,
    BuildPackError,
    BuildPackStore,
    BuildRecord,
    CandidateCategory,
    InfoStatus,
    Project,
    ProjectIdentity,
    Provenance,
    ReconciledCandidate,
    Reconciliation,
    ReconciliationContradiction,
    ReconciliationError,
    ReconciliationSession,
    ReconciliationState,
    ReconciliationStore,
    ResolutionStatus,
    Revision,
    build_record_fields,
    candidate_provenance,
    packs_equivalent,
    utc_now_iso,
)
import src.app.product.agentivan.reconciliation as reconciliation_module

# ---------------------------------------------------------------------------
# Synthetic reconciliation fixture (brief section 24)
#
# A small synthetic project ("Nefarious Rig") with three text sources.
# The scripted decision flow below produces: one accepted candidate,
# one rejected, one deferred, one duplicate, one unresolved
# contradiction, one resolved contradiction, multiple sources supporting
# the same information, several information statuses, and provenance
# through the complete chain. Synthetic data only.
# ---------------------------------------------------------------------------

SOURCE_A = (
    "# Session notes\n"
    "\n"
    "Seat height measured at 28 inches.\n"
    "Part: steel bracket\n"
    "TODO: confirm paint color\n"
    "Rail width 42 mm.\n"
    "A quiet afternoon in the shop.\n"
)

SOURCE_B = (
    "# Follow-up notes\n"
    "\n"
    "Seat height measured at 30 inches.\n"
    "Seat height measured at 28 inches.\n"
    "Project: Nefarious Rig\n"
)

SOURCE_C = (
    "# Old notes\n"
    "\n"
    "Seat height was 26 inches. [MEASURED]\n"
)


def _prepare_project(root: Path) -> dict:
    """Intake sources, run extraction, snapshot evidence.

    Returns the project plus everything the decision flow needs.
    Nothing here makes any reconciliation decision.
    """
    project = Project.create("Nefarious Rig", root / "recon")
    rec_a = project.intake_text(SOURCE_A, label="Session notes")
    rec_b = project.intake_text(SOURCE_B, label="Follow-up notes")
    rec_c = project.intake_text(SOURCE_C, label="Old notes")

    extraction_set = project.extractions.run()
    project.extractions.save(extraction_set)
    extraction_bytes = project.extractions.extractions_path.read_bytes()

    source_hashes = {}
    for path in sorted(project.sources_dir.rglob("*")):
        if path.is_file():
            source_hashes[path.name] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()

    # A pre-existing canonical record, so tests can prove unrelated
    # accepts leave existing Build Pack data intact.
    session = ReconciliationSession(project)
    pre_existing = BuildRecord(
        record_id="pre-existing-part-1",
        status=InfoStatus.KNOWN,
        fields={"name": "Pre-existing mounting plate"},
        provenance=Provenance(
            source_type="manual", source_text="shop notebook"
        ),
    )
    session.pack.sections.setdefault("parts", []).append(pre_existing)
    session.save()

    return {
        "project": project,
        "extraction_set": extraction_set,
        "extraction_bytes": extraction_bytes,
        "source_hashes": source_hashes,
        "source_ids": {
            "Session notes": rec_a.source_id,
            "Follow-up notes": rec_b.source_id,
            "Old notes": rec_c.source_id,
        },
    }


def _pick(session: ReconciliationSession, fixture: dict, **kwargs) -> ReconciledCandidate:
    """Find one candidate by category, text fragment, and source label."""
    category = kwargs["category"]
    fragment = kwargs["fragment"]
    source_id = fixture["source_ids"][kwargs["label"]]
    matches = [
        candidate
        for candidate in session.reconciliation.candidates
        if candidate.category is category
        and fragment in candidate.text
        and candidate.source_id == source_id
    ]
    assert len(matches) == 1, (
        f"expected one match for {kwargs}, got {len(matches)}"
    )
    return matches[0]


def _run_decisions(project: Project, fixture: dict) -> dict:
    """Apply the scripted, explicit decision flow. Returns key ids."""
    session = ReconciliationSession(project)
    session.import_candidates(fixture["extraction_set"])

    c_28 = _pick(
        session,
        fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="28 inches",
        label="Session notes",
    )
    c_bracket = _pick(
        session,
        fixture,
        category=CandidateCategory.PART,
        fragment="steel bracket",
        label="Session notes",
    )
    c_paint = _pick(
        session,
        fixture,
        category=CandidateCategory.OPEN_ISSUE,
        fragment="paint color",
        label="Session notes",
    )
    c_42 = _pick(
        session,
        fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="42 mm",
        label="Session notes",
    )
    c_30 = _pick(
        session,
        fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="30 inches",
        label="Follow-up notes",
    )
    c_dup = _pick(
        session,
        fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="28 inches",
        label="Follow-up notes",
    )
    c_26 = _pick(
        session,
        fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="26 inches",
        label="Old notes",
    )
    c_identity = _pick(
        session,
        fixture,
        category=CandidateCategory.PROJECT_IDENTITY,
        fragment="Nefarious Rig",
        label="Follow-up notes",
    )

    # 1. accepted candidate (MEASURED status preserved)
    record_28 = session.accept(
        c_28.reconciliation_id, note="reviewed: primary measurement"
    )
    assert record_28 is not None

    # 2. accepted candidate with no extraction status (stays UNKNOWN)
    record_bracket = session.accept(c_bracket.reconciliation_id)
    assert record_bracket is not None

    # 3. rejected candidate (reason preserved)
    session.reject(c_paint.reconciliation_id, reason="cosmetic, out of scope")

    # 4. deferred candidate (reason preserved)
    session.defer(c_42.reconciliation_id, reason="unclear which rail")

    # 5. contradiction, left unresolved
    contradiction_open = session.flag_contradiction(
        c_30.reconciliation_id,
        existing_record_id=record_28.record_id,
        description=(
            "Source B states seat height 30 in; "
            "the pack records 28 in (MEASURED)"
        ),
    )

    # 6. contradiction, resolved by keeping the existing information
    contradiction_resolved = session.flag_contradiction(
        c_26.reconciliation_id,
        existing_record_id=record_28.record_id,
        description=(
            "Source C states seat height 26 in; "
            "the pack records 28 in (MEASURED)"
        ),
    )
    session.resolve_contradiction(
        contradiction_resolved.contradiction_id,
        resolution=KEEP_EXISTING,
        note="newer measurement stands",
    )

    # 7. duplicate: same information, different source -> no new record
    session.accept(c_dup.reconciliation_id)

    # 8. project identity candidate is left for human review; it must
    # never be accepted automatically (proven in its own test).

    session.save()
    return {
        "session": session,
        "record_28": record_28,
        "record_bracket": record_bracket,
        "c_28": c_28,
        "c_bracket": c_bracket,
        "c_paint": c_paint,
        "c_42": c_42,
        "c_30": c_30,
        "c_dup": c_dup,
        "c_26": c_26,
        "c_identity": c_identity,
        "contradiction_open": contradiction_open,
        "contradiction_resolved": contradiction_resolved,
    }


@pytest.fixture()
def decided(tmp_path: Path) -> dict:
    """A fully decided scenario: prepare, decide, save."""
    fixture = _prepare_project(tmp_path / "p1")
    ids = _run_decisions(fixture["project"], fixture)
    return {"fixture": fixture, **ids}


# ---------------------------------------------------------------------------
# 1-5: review, accept, section mapping, status, provenance
# ---------------------------------------------------------------------------


def test_candidate_can_be_reviewed(decided: dict) -> None:
    session = decided["session"]
    candidate = decided["c_28"]
    reviewed = session.get_candidate(candidate.reconciliation_id)
    assert reviewed.state is ReconciliationState.ACCEPTED
    assert reviewed.category is CandidateCategory.MEASUREMENT
    assert reviewed.text == "28 inches"
    assert reviewed.source_id == decided["fixture"]["source_ids"][
        "Session notes"
    ]
    assert reviewed.extraction_id
    assert reviewed.section == "measurements"
    assert reviewed.decided_at is not None
    # The identity candidate was never touched: still awaiting review.
    assert (
        decided["c_identity"].state is ReconciliationState.CANDIDATE
    )


def test_candidate_can_be_explicitly_accepted(decided: dict) -> None:
    candidate = decided["c_28"]
    assert candidate.state is ReconciliationState.ACCEPTED
    assert candidate.build_record_id == decided["record_28"].record_id
    assert candidate.decision_note == "reviewed: primary measurement"
    assert candidate.decided_at is not None
    # Accepting twice is refused: decisions are explicit, not repeatable
    # by accident.
    with pytest.raises(ReconciliationError):
        decided["session"].accept(candidate.reconciliation_id)


def test_accepted_candidate_reaches_correct_section(decided: dict) -> None:
    pack = decided["session"].pack
    assert decided["record_28"] in pack.sections["measurements"]
    assert decided["record_bracket"] in pack.sections["parts"]
    # Pre-existing data was already there and is untouched by placement.
    assert any(
        r.record_id == "pre-existing-part-1" for r in pack.sections["parts"]
    )


def test_candidate_status_is_preserved(decided: dict) -> None:
    # MEASURED is accepted as MEASURED: never promoted.
    assert decided["record_28"].status is InfoStatus.MEASURED
    # A candidate the source gave no status becomes UNKNOWN, the honest
    # conservative representation -- never an invented KNOWN.
    assert decided["record_bracket"].status is InfoStatus.UNKNOWN
    assert decided["c_bracket"].status is None


def test_candidate_provenance_is_preserved(decided: dict) -> None:
    candidate = decided["c_28"]
    record = decided["record_28"]
    provenance = record.provenance
    assert provenance is not None
    assert provenance.source_type == "source_extraction"
    assert candidate.source_id in (provenance.source_reference or "")
    assert provenance.source_text == candidate.text == "28 inches"
    assert candidate.extraction_id in (provenance.notes or "")
    assert candidate.reconciliation_id in (provenance.notes or "")
    assert "source_extraction" not in (provenance.source_text or "")


# ---------------------------------------------------------------------------
# 6-7: rejection and deferment stay recorded
# ---------------------------------------------------------------------------


def test_rejected_candidates_remain_recorded(decided: dict) -> None:
    candidate = decided["c_paint"]
    assert candidate.state is ReconciliationState.REJECTED
    assert candidate.decision_note == "cosmetic, out of scope"
    assert candidate.decided_at is not None
    # Still present in reconciliation history; nothing was deleted.
    ids = [
        c.reconciliation_id
        for c in decided["session"].reconciliation.candidates
    ]
    assert candidate.reconciliation_id in ids
    # Rejection created no Build Pack record.
    assert "open_issues" not in decided["session"].pack.sections


def test_deferred_candidates_remain_recorded(decided: dict) -> None:
    candidate = decided["c_42"]
    assert candidate.state is ReconciliationState.DEFERRED
    assert candidate.decision_note == "unclear which rail"
    assert candidate.decided_at is not None
    # Deferred information was not forced into the pack.
    measurements = decided["session"].pack.sections["measurements"]
    assert all("42 mm" != r.fields.get("measurement") for r in measurements)


# ---------------------------------------------------------------------------
# 8: duplicates
# ---------------------------------------------------------------------------


def test_duplicate_information_is_not_duplicated(decided: dict) -> None:
    candidate = decided["c_dup"]
    assert candidate.state is ReconciliationState.DUPLICATE
    assert (
        candidate.duplicate_of_record_id == decided["record_28"].record_id
    )
    measurements = decided["session"].pack.sections["measurements"]
    assert len(measurements) == 1
    # The duplicate's source remains part of project history.
    assert candidate.source_id == decided["fixture"]["source_ids"][
        "Follow-up notes"
    ]


def test_duplicate_detection_is_conservative(decided: dict) -> None:
    session = decided["session"]
    # "30 inches" differs from the accepted "28 inches": not a duplicate.
    assert session.find_duplicate(decided["c_30"]) is None
    # Unmapped categories can never match a record.
    general = next(
        c
        for c in session.reconciliation.candidates
        if c.category is CandidateCategory.GENERAL
    )
    assert session.find_duplicate(general) is None


# ---------------------------------------------------------------------------
# 9-11: contradictions
# ---------------------------------------------------------------------------


def test_contradictory_information_is_not_silently_replaced(
    decided: dict,
) -> None:
    record = decided["record_28"]
    # The existing canonical value is untouched by flagging.
    assert record.fields["measurement"] == "28 inches"
    assert record.status is InfoStatus.MEASURED
    candidate = decided["c_30"]
    assert candidate.state is ReconciliationState.CONTRADICTED
    assert (
        candidate.contradiction_id
        == decided["contradiction_open"].contradiction_id
    )
    contradiction = decided["contradiction_open"]
    assert contradiction.state is ResolutionStatus.UNRESOLVED
    assert contradiction.existing_record_id == record.record_id


def test_contradiction_provenance_is_preserved(decided: dict) -> None:
    contradiction = decided["contradiction_open"]
    candidate = decided["c_30"]
    # Existing side keeps its provenance.
    assert contradiction.existing_provenance is not None
    assert (
        contradiction.existing_provenance.get("source_text") == "28 inches"
    )
    # Candidate side keeps its provenance: extraction id, source, chunk
    # location -- recoverable without guessing.
    candidate_side = contradiction.candidate_provenance
    assert candidate_side["extraction_id"] == candidate.extraction_id
    assert (
        candidate_side["source_id"]
        == decided["fixture"]["source_ids"]["Follow-up notes"]
    )
    assert candidate_side["location"]["source_id"] == candidate.source_id


def test_contradiction_can_be_resolved_keep_existing(
    decided: dict,
) -> None:
    contradiction = decided["contradiction_resolved"]
    assert contradiction.state is ResolutionStatus.RESOLVED
    assert contradiction.resolution == KEEP_EXISTING
    assert contradiction.resolution_note == "newer measurement stands"
    assert contradiction.resolved_at is not None
    # The Build Pack was not touched by keeping the existing value.
    record = decided["record_28"]
    assert record.fields["measurement"] == "28 inches"
    assert record.status is InfoStatus.MEASURED
    # The candidate keeps its contradicted history.
    assert decided["c_26"].state is ReconciliationState.CONTRADICTED
    # Resolving twice is refused.
    with pytest.raises(ReconciliationError):
        decided["session"].resolve_contradiction(
            contradiction.contradiction_id, resolution=KEEP_EXISTING
        )


def test_contradiction_can_be_resolved_accept_incoming(
    tmp_path: Path,
) -> None:
    project = Project.create("Nefarious Rig", tmp_path / "mini")
    project.intake_text(
        "Seat height measured at 28 inches.", label="First"
    )
    project.intake_text(
        "Seat height measured at 30 inches.", label="Second"
    )
    extraction_set = project.extractions.run()
    session = ReconciliationSession(project)
    session.import_candidates(extraction_set)

    def pick(label: str, fragment: str) -> ReconciledCandidate:
        matches = [
            c
            for c in session.reconciliation.candidates
            if c.category is CandidateCategory.MEASUREMENT
            and fragment in c.text
            and project.get_source(c.source_id).label == label
        ]
        assert len(matches) == 1
        return matches[0]

    first = pick("First", "28 inches")
    second = pick("Second", "30 inches")
    record = session.accept(first.reconciliation_id)
    assert record is not None

    contradiction = session.flag_contradiction(
        second.reconciliation_id,
        existing_record_id=record.record_id,
        description="Second source states 30 in; pack records 28 in",
    )
    resolved = session.resolve_contradiction(
        contradiction.contradiction_id,
        resolution=ACCEPT_INCOMING,
        note="re-measured on site",
    )
    assert resolved.state is ResolutionStatus.RESOLVED
    assert resolved.resolution == ACCEPT_INCOMING
    assert resolved.resolution_note == "re-measured on site"
    # The incoming information becomes canonical; the old value is kept
    # in the revision's previous_state, not silently dropped.
    assert record.fields["measurement"] == "30 inches"
    assert record.status is InfoStatus.MEASURED
    revision = session.pack.change_history[-1]
    assert revision.previous_state["fields"]["measurement"] == "28 inches"
    assert revision.new_state["fields"]["measurement"] == "30 inches"
    assert "Contradiction" in revision.description
    # The candidate is now accepted against the existing record id.
    assert second.state is ReconciliationState.ACCEPTED
    assert second.build_record_id == record.record_id
    # The old record was updated in place: still exactly one record.
    assert len(session.pack.sections["measurements"]) == 1


def test_contradiction_resolution_rejects_bad_input(tmp_path: Path) -> None:
    fixture = _prepare_project(tmp_path / "bad")
    session = ReconciliationSession(fixture["project"])
    session.import_candidates(fixture["extraction_set"])
    candidate = next(
        c for c in session.reconciliation.candidates if c.text == "30 inches"
    )
    with pytest.raises(ReconciliationError):
        session.flag_contradiction(candidate.reconciliation_id, description="")
    with pytest.raises(ReconciliationError):
        session.flag_contradiction(
            candidate.reconciliation_id,
            existing_record_id="no-such-record",
            description="x",
        )
    contradiction = session.flag_contradiction(
        candidate.reconciliation_id, description="conflicts"
    )
    with pytest.raises(ReconciliationError):
        session.resolve_contradiction(
            contradiction.contradiction_id, resolution="auto resolve"
        )
    # accept_incoming with no existing record to update is refused.
    with pytest.raises(ReconciliationError):
        session.resolve_contradiction(
            contradiction.contradiction_id, resolution=ACCEPT_INCOMING
        )


# ---------------------------------------------------------------------------
# 12-13: resolution history and revision history
# ---------------------------------------------------------------------------


def test_resolution_history_survives_serialization(decided: dict) -> None:
    project = decided["fixture"]["project"]
    reloaded = ReconciliationSession(project)
    by_id = {
        c.contradiction_id: c
        for c in reloaded.reconciliation.contradictions
    }
    open_ctrd = by_id[decided["contradiction_open"].contradiction_id]
    assert open_ctrd.state is ResolutionStatus.UNRESOLVED
    resolved_ctrd = by_id[decided["contradiction_resolved"].contradiction_id]
    assert resolved_ctrd.state is ResolutionStatus.RESOLVED
    assert resolved_ctrd.resolution == KEEP_EXISTING
    assert resolved_ctrd.resolution_note == "newer measurement stands"
    assert resolved_ctrd.resolved_at is not None


def test_revision_history_records_accepted_changes(decided: dict) -> None:
    history = decided["session"].pack.change_history
    assert len(history) >= 2
    revisions = [r.revision for r in history]
    assert revisions == sorted(revisions)
    assert len(set(revisions)) == len(revisions)
    by_candidate = {
        r.new_state.get("record_id"): r
        for r in history
        if isinstance(r.new_state, dict)
    }
    revision = by_candidate[decided["record_28"].record_id]
    assert decided["c_28"].reconciliation_id in revision.description
    assert revision.affected_area == "measurements"
    assert revision.previous_state is None
    assert revision.new_state["status"] == "MEASURED"
    assert revision.author == "reconciliation"


# ---------------------------------------------------------------------------
# 14-15: identity protection and measurement preservation
# ---------------------------------------------------------------------------


def test_project_identity_is_not_silently_changed(decided: dict) -> None:
    session = decided["session"]
    pack = session.pack
    before = pack.identity.to_dict()
    with pytest.raises(ReconciliationError):
        session.accept(decided["c_identity"].reconciliation_id)
    # Nothing changed: identity is byte-identical and the candidate is
    # still awaiting explicit human review.
    assert pack.identity.to_dict() == before
    assert decided["c_identity"].state is ReconciliationState.CANDIDATE


def test_measurement_information_is_preserved(decided: dict) -> None:
    record = decided["record_28"]
    # Original expression verbatim; value/unit separate; never converted
    # or recalculated by the reconciliation layer.
    assert record.fields["measurement"] == "28 inches"
    assert record.fields["value"] == 28
    assert record.fields["unit"] == "in"
    assert record.status is InfoStatus.MEASURED
    candidate = decided["c_28"]
    assert candidate.normalized == {"value": 28, "unit": "in"}
    assert candidate.text == record.fields["measurement"]


# ---------------------------------------------------------------------------
# 16-18: persistence and repeatability
# ---------------------------------------------------------------------------


def test_build_pack_can_be_saved_and_reloaded(decided: dict) -> None:
    project = decided["fixture"]["project"]
    pack = decided["session"].pack
    path = project.buildpack_store.save(pack)
    assert path.name == "buildpack.json"
    assert project.buildpack_store.exists()
    reloaded = project.buildpack_store.load()
    assert reloaded == pack
    reloaded.validate()


def test_reconciliation_data_can_be_saved_and_reloaded(
    decided: dict,
) -> None:
    project = decided["fixture"]["project"]
    reconciliation = decided["session"].reconciliation
    path = project.reconciliation_store.save(reconciliation)
    assert path.name == "reconciliation.json"
    reloaded = project.reconciliation_store.load()
    assert reloaded.equivalent(reconciliation)
    assert len(reloaded.candidates) == len(reconciliation.candidates)
    assert len(reloaded.contradictions) == len(reconciliation.contradictions)


def test_identical_inputs_produce_equivalent_results(tmp_path: Path) -> None:
    # Same project bytes (same source ids, same extraction ids) in two
    # locations; the identical decision flow runs independently in each.
    fixture_a = _prepare_project(tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    project_b = Project.load(tmp_path / "b" / "recon")
    extraction_b = project_b.extractions.load()

    ids_a = _run_decisions(fixture_a["project"], fixture_a)
    ids_b = _run_decisions(
        project_b,
        {**fixture_a, "project": project_b, "extraction_set": extraction_b},
    )

    pack_a = ids_a["session"].pack
    pack_b = ids_b["session"].pack
    assert packs_equivalent(pack_a, pack_b)
    # Deterministic ids: identical inputs -> identical record ids.
    ids_a_records = sorted(
        r.record_id for rs in pack_a.sections.values() for r in rs
    )
    ids_b_records = sorted(
        r.record_id for rs in pack_b.sections.values() for r in rs
    )
    assert ids_a_records == ids_b_records
    assert ids_a["session"].reconciliation.equivalent(
        ids_b["session"].reconciliation
    )


def test_build_pack_assembly_is_deterministic(tmp_path: Path) -> None:
    fixture = _prepare_project(tmp_path / "det")
    # Copy the *undecided* project, then decide independently in both.
    shutil.copytree(tmp_path / "det", tmp_path / "det2")
    project2 = Project.load(tmp_path / "det2" / "recon")
    fixture2 = {
        **fixture,
        "project": project2,
        "extraction_set": project2.extractions.load(),
    }
    ids_first = _run_decisions(fixture["project"], fixture)
    ids_second = _run_decisions(project2, fixture2)

    first = ids_first["session"].pack.to_dict()
    second = ids_second["session"].pack.to_dict()
    # Strip only volatile metadata; everything material must agree.
    for payload in (first, second):
        payload["identity"].pop("created")
        payload["identity"].pop("modified")
        for revision in payload["change_history"]:
            revision.pop("timestamp")
    assert first == second


# ---------------------------------------------------------------------------
# 19-21: read-only guarantees
# ---------------------------------------------------------------------------


def test_raw_source_material_remains_unchanged(decided: dict) -> None:
    fixture = decided["fixture"]
    project = fixture["project"]
    after = {}
    for path in sorted(project.sources_dir.rglob("*")):
        if path.is_file():
            after[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == fixture["source_hashes"]


def test_extraction_records_remain_unchanged(decided: dict) -> None:
    fixture = decided["fixture"]
    assert (
        fixture["project"].extractions.extractions_path.read_bytes()
        == fixture["extraction_bytes"]
    )


def test_existing_build_pack_data_remains_intact(decided: dict) -> None:
    pack = decided["session"].pack
    pre_existing = next(
        r for r in pack.sections["parts"] if r.record_id == "pre-existing-part-1"
    )
    assert pre_existing.fields == {"name": "Pre-existing mounting plate"}
    assert pre_existing.status is InfoStatus.KNOWN
    assert pre_existing.provenance is not None
    assert pre_existing.provenance.source_text == "shop notebook"


def test_read_only_inspection_does_not_mutate_the_pack(
    decided: dict,
) -> None:
    session = decided["session"]
    before = session.pack.to_dict()
    session.get_candidate(decided["c_28"].reconciliation_id)
    session.get_contradiction(
        decided["contradiction_open"].contradiction_id
    )
    session.find_record(decided["record_28"].record_id)
    session.find_duplicate(decided["c_30"])
    assert session.pack.to_dict() == before


# ---------------------------------------------------------------------------
# 22-24: relocation, unicode, import hygiene
# ---------------------------------------------------------------------------


def test_project_relocation_still_works(decided: dict, tmp_path: Path) -> None:
    fixture = decided["fixture"]
    shutil.copytree(tmp_path / "p1", tmp_path / "moved")
    moved = Project.load(tmp_path / "moved" / "recon")
    session = ReconciliationSession(moved)
    assert packs_equivalent(session.pack, decided["session"].pack)
    assert session.reconciliation.equivalent(
        decided["session"].reconciliation
    )
    # Work can continue in the new location: the deferred candidate is
    # still there and still acceptable.
    deferred = next(
        c
        for c in session.reconciliation.candidates
        if c.state is ReconciliationState.DEFERRED
    )
    record = session.accept(deferred.reconciliation_id)
    assert record is not None
    assert record.fields["measurement"] == "42 mm"
    session.save()
    assert moved.buildpack_store.load().validate() is None


def test_unicode_survives(tmp_path: Path) -> None:
    project = Project.create("Nefarious Rig", tmp_path / "uni")
    project.intake_text(
        "Part: café bracket — crème finish, naïve coating\n",
        label="Unicode",
    )
    extraction_set = project.extractions.run()
    session = ReconciliationSession(project)
    session.import_candidates(extraction_set)
    candidate = next(
        c
        for c in session.reconciliation.candidates
        if c.category is CandidateCategory.PART
    )
    assert "café" in candidate.text
    record = session.accept(candidate.reconciliation_id)
    assert record is not None
    assert "café" in record.fields["name"]
    session.save()
    reloaded_pack = project.buildpack_store.load()
    reloaded_record = next(
        r
        for r in reloaded_pack.sections["parts"]
        if r.record_id == record.record_id
    )
    assert "crème" in reloaded_record.fields["name"]
    reloaded_reconciliation = project.reconciliation_store.load()
    assert any(
        "café" in c.text
        for c in reloaded_reconciliation.candidates
    )


def test_import_candidates_is_idempotent(tmp_path: Path) -> None:
    fixture = _prepare_project(tmp_path / "idem")
    session = ReconciliationSession(fixture["project"])
    first = session.import_candidates(fixture["extraction_set"])
    assert first
    second = session.import_candidates(fixture["extraction_set"])
    assert second == []


def test_cross_project_import_is_refused(tmp_path: Path) -> None:
    fixture_a = _prepare_project(tmp_path / "xa")
    fixture_b = _prepare_project(tmp_path / "xb")
    session = ReconciliationSession(fixture_a["project"])
    with pytest.raises(ReconciliationError):
        session.import_candidates(fixture_b["extraction_set"])


# ---------------------------------------------------------------------------
# 25-26: no network, no LLM
# ---------------------------------------------------------------------------


def test_no_network_is_required(tmp_path: Path) -> None:
    def _no_sockets(*args, **kwargs):
        raise AssertionError("network access attempted")

    with mock.patch.object(socket, "socket", _no_sockets):
        fixture = _prepare_project(tmp_path / "nonet")
        _run_decisions(fixture["project"], fixture)


def test_no_llm_is_required() -> None:
    source = Path(reconciliation_module.__file__).read_text(
        encoding="utf-8"
    )
    for token in (
        "import ollama",
        "import pydanticai",
        "from openai",
        "from anthropic",
        "import requests",
        "Ollama(",
        "embeddings",
    ):
        assert token not in source, f"forbidden token in reconciliation.py: {token}"
    assert "ollama" not in sys.modules
    assert "pydanticai" not in sys.modules


# ---------------------------------------------------------------------------
# Mapping coverage, state guards, confidence neutrality
# ---------------------------------------------------------------------------


def test_category_section_map_covers_every_category() -> None:
    assert set(CATEGORY_SECTION_MAP) == set(CandidateCategory)
    unmapped = {
        category
        for category, section in CATEGORY_SECTION_MAP.items()
        if section is None
    }
    assert unmapped == {
        CandidateCategory.PROJECT_IDENTITY,
        CandidateCategory.CHANGE_HISTORY,
        CandidateCategory.GENERAL,
    }


def test_unmapped_category_cannot_be_accepted(tmp_path: Path) -> None:
    fixture = _prepare_project(tmp_path / "unmapped")
    session = ReconciliationSession(fixture["project"])
    session.import_candidates(fixture["extraction_set"])
    general = next(
        c
        for c in session.reconciliation.candidates
        if c.category is CandidateCategory.GENERAL
    )
    with pytest.raises(ReconciliationError):
        session.accept(general.reconciliation_id)
    assert general.state is ReconciliationState.CANDIDATE
    # Deferring it explicitly is the honest path.
    session.defer(general.reconciliation_id, reason="no safe mapping")
    assert general.state is ReconciliationState.DEFERRED


def test_decisions_require_candidate_state(decided: dict) -> None:
    session = decided["session"]
    accepted_id = decided["c_28"].reconciliation_id
    for operation in (
        lambda: session.accept(accepted_id),
        lambda: session.reject(accepted_id),
        lambda: session.defer(accepted_id),
        lambda: session.flag_contradiction(accepted_id, description="x"),
    ):
        with pytest.raises(ReconciliationError):
            operation()
    with pytest.raises(ReconciliationError):
        session.get_candidate("no-such-candidate")
    with pytest.raises(ReconciliationError):
        session.resolve_contradiction("no-such-contradiction", resolution=KEEP_EXISTING)


def test_extraction_confidence_is_not_permission(tmp_path: Path) -> None:
    # Confidence is recorded for inspection only. A candidate with
    # confidence forced to 0.0 accepts exactly like any other: the
    # explicit decision is the only permission.
    fixture = _prepare_project(tmp_path / "conf")
    session = ReconciliationSession(fixture["project"])
    session.import_candidates(fixture["extraction_set"])
    candidate = next(
        c
        for c in session.reconciliation.candidates
        if c.category is CandidateCategory.PART
    )
    candidate.confidence = 0.0
    record = session.accept(candidate.reconciliation_id)
    assert record is not None
    assert candidate.state is ReconciliationState.ACCEPTED


def _identity() -> ProjectIdentity:
    now = utc_now_iso()
    return ProjectIdentity(
        name="Nefarious Rig",
        project_id="test-project",
        description="Not recorded",
        intent="Not recorded",
        status="draft",
        created=now,
        modified=now,
        revision=0,
    )


def test_build_record_fields_cover_mapped_categories() -> None:
    CC = CandidateCategory
    for category, (section, field_name) in {
        CC.DONOR: ("donor", "notes"),
        CC.DESIGN: ("design", "concept_notes"),
        CC.MEASUREMENT: ("measurements", "measurement"),
        CC.PART: ("parts", "name"),
        CC.MATERIAL: ("finishing", "material"),
        CC.RESEARCH: ("research", "subject"),
        CC.ENGINEERING: ("engineering", "calculation"),
        CC.CAD_FABRICATION: ("cad", "component"),
        CC.BUILD_ASSEMBLY: ("build", "task"),
        CC.FINISHING: ("finishing", "material"),
        CC.TESTING_VERIFICATION: ("testing", "item"),
        CC.COST: ("cost", "item"),
        CC.SAFETY_LEGAL: ("safety", "issue"),
        CC.OPEN_ISSUE: ("open_issues", "question"),
    }.items():
        candidate = ReconciledCandidate(
            reconciliation_id=f"rc-{category.value}",
            extraction_id=f"ex-{category.value}",
            source_id="src-1",
            category=category,
            text="original expression",
            method="deterministic",
            status="PROPOSED",
            normalized=(
                {"value": 3, "unit": "in"}
                if category is CC.MEASUREMENT
                else None
            ),
            section=section,
        )
        fields = build_record_fields(candidate)
        assert fields[field_name] == "original expression"
        provenance = candidate_provenance(candidate)
        assert provenance.source_text == "original expression"
        # The mapped fields validate against the Brick 1 section schema.
        record = BuildRecord(
            record_id=f"rec-{category.value}",
            status=InfoStatus.PROPOSED,
            fields=fields,
            provenance=provenance,
        )
        pack = BuildPack(
            identity=_identity(),
            sections={section: [record]},
        )
        pack.validate()


def test_packs_equivalent_ignores_volatile_timestamps() -> None:
    pack_a = BuildPack(identity=_identity())
    pack_b = BuildPack(identity=_identity())
    assert packs_equivalent(pack_a, pack_b)
    pack_b.change_history.append(
        Revision(
            revision=1,
            timestamp=utc_now_iso(),
            description="change",
            affected_area="parts",
        )
    )
    assert not packs_equivalent(pack_a, pack_b)


def test_stores_reject_wrong_project_and_missing_files(
    tmp_path: Path,
) -> None:
    fixture = _prepare_project(tmp_path / "stores")
    project = fixture["project"]
    other = Project.create("Other", tmp_path / "other")

    with pytest.raises(ReconciliationError):
        other.buildpack_store.load()
    with pytest.raises(ReconciliationError):
        other.reconciliation_store.load()

    pack = project.buildpack_store.load()
    with pytest.raises(ReconciliationError):
        other.buildpack_store.save(pack)
    reconciliation = project.reconciliation_store.load()
    with pytest.raises(ReconciliationError):
        other.reconciliation_store.save(reconciliation)

    # Saving an invalid pack is refused before anything is written.
    bad = BuildPack(identity=pack.identity)
    bad.sections["no-such-section"] = []
    with pytest.raises(ReconciliationError):
        project.buildpack_store.save(bad)

    # Corrupt JSON is reported, not misread.
    project.buildpack_store.buildpack_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ReconciliationError):
        project.buildpack_store.load()


def test_contradiction_serialization_round_trip() -> None:
    contradiction = ReconciliationContradiction(
        contradiction_id="ctrd-1",
        candidate_id="rc-1",
        section="measurements",
        affected_field="measurement",
        description="conflict",
        candidate_provenance={"extraction_id": "ex-1"},
        existing_record_id="rec-1",
        existing_provenance={"source_text": "28 inches"},
    )
    assert contradiction.state is ResolutionStatus.UNRESOLVED
    restored = ReconciliationContradiction.from_dict(
        contradiction.to_dict()
    )
    assert restored.to_dict() == contradiction.to_dict()
    with pytest.raises(ReconciliationError):
        ReconciliationContradiction.from_dict(
            {**contradiction.to_dict(), "resolution": "auto"}
        )
