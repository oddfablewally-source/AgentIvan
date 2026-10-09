"""
Focused tests for the AgentIvan canonical Build Pack model (Brick 1).

These prove the model can represent the required information and that
validation rejects malformed packs. They do not touch the existing
boilerplate baseline; those 142 tests must keep passing unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.product.agentivan import (
    BuildPack,
    BuildPackError,
    BuildRecord,
    Contradiction,
    InfoStatus,
    ProjectIdentity,
    Provenance,
    ResolutionStatus,
    Revision,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "canonical_build_pack.json"


def _identity(**overrides) -> ProjectIdentity:
    fields = {
        "name": "Test Project",
        "project_id": "test-001",
        "description": "test",
        "intent": "test",
        "status": "active",
        "created": "2026-09-20",
        "modified": "2026-09-20",
        "revision": 0,
    }
    fields.update(overrides)
    return ProjectIdentity(**fields)


def _record(record_id: str, section_fields: dict, status=InfoStatus.KNOWN):
    return BuildRecord(
        record_id=record_id, status=status, fields=section_fields
    )


def _minimal_pack(**overrides) -> BuildPack:
    defaults = {
        "sections": {
            "donor": [
                _record("donor-1", {"manufacturer": "Sample Co."}),
            ]
        }
    }
    defaults.update(overrides)
    return BuildPack(identity=_identity(), **defaults)


def _load_fixture() -> BuildPack:
    return BuildPack.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# §10.1: a valid Build Pack can be created
# ---------------------------------------------------------------------------


def test_valid_build_pack_can_be_created() -> None:
    pack = _minimal_pack()
    assert pack.validate() is None
    assert pack.identity.name == "Test Project"


def test_canonical_fixture_is_valid() -> None:
    pack = _load_fixture()
    assert pack.validate() is None
    assert pack.identity.project_id == "sample-trailer-001"


# ---------------------------------------------------------------------------
# §10.2 / §10.3: serialization and deserialization
# ---------------------------------------------------------------------------


def test_serialization_is_deterministic_json() -> None:
    pack = _load_fixture()
    first = pack.to_json()
    second = pack.to_json()
    assert first == second
    parsed = json.loads(first)
    assert parsed["format"] == "agentivan-build-pack"
    assert parsed["format_version"] == 1
    assert set(parsed) == {
        "format",
        "format_version",
        "identity",
        "sections",
        "change_history",
        "contradictions",
    }


def test_deserialization_round_trip() -> None:
    pack = _load_fixture()
    assert BuildPack.from_json(pack.to_json()) == pack


def test_empty_pack_round_trip() -> None:
    pack = BuildPack(identity=_identity())
    assert pack.validate() is None
    assert BuildPack.from_json(pack.to_json()) == pack


# ---------------------------------------------------------------------------
# §10.4: information statuses survive serialization
# ---------------------------------------------------------------------------


def test_all_seven_statuses_survive_serialization() -> None:
    pack = _load_fixture()
    statuses = {
        record.status
        for records in pack.sections.values()
        for record in records
    }
    assert statuses == set(InfoStatus)

    restored = BuildPack.from_json(pack.to_json())
    for section, records in pack.sections.items():
        for record in records:
            match = [
                r
                for r in restored.sections[section]
                if r.record_id == record.record_id
            ]
            assert len(match) == 1
            assert match[0].status is record.status


# ---------------------------------------------------------------------------
# §10.5: provenance survives serialization
# ---------------------------------------------------------------------------


def test_provenance_survives_serialization() -> None:
    pack = _load_fixture()
    record = next(
        r
        for r in pack.sections["research"]
        if r.record_id == "bearing-research"
    )
    assert record.provenance is not None
    assert record.provenance.source_type == "manufacturer_document"
    assert (
        record.provenance.source_text
        == "Bearing kit: L44643 cone / L44610 cup (sample excerpt)"
    )

    restored = BuildPack.from_json(pack.to_json())
    restored_record = next(
        r
        for r in restored.sections["research"]
        if r.record_id == "bearing-research"
    )
    assert restored_record.provenance == record.provenance


def test_missing_provenance_stays_missing() -> None:
    """No provenance is never invented during (de)serialization."""
    pack = _load_fixture()
    record = next(
        r
        for r in pack.sections["measurements"]
        if r.record_id == "axle-rating"
    )
    assert record.provenance is None
    restored = BuildPack.from_json(pack.to_json())
    restored_record = next(
        r
        for r in restored.sections["measurements"]
        if r.record_id == "axle-rating"
    )
    assert restored_record.provenance is None


# ---------------------------------------------------------------------------
# §10.6: revision information survives serialization
# ---------------------------------------------------------------------------


def test_revision_survives_serialization() -> None:
    pack = _load_fixture()
    assert len(pack.change_history) == 1
    revision = pack.change_history[0]
    assert revision.revision == 1
    assert revision.affected_area == "testing:coupler-latch-test"

    restored = BuildPack.from_json(pack.to_json())
    assert restored.change_history == pack.change_history
    assert restored.change_history[0].new_state == {
        "status": "VERIFIED",
        "actual_result": "latch seated, no play observed",
    }
    assert restored.change_history[0].previous_state is None


# ---------------------------------------------------------------------------
# §10.7: contradictions without automatic resolution
# ---------------------------------------------------------------------------


def test_contradiction_represented_without_resolution() -> None:
    pack = _load_fixture()
    assert len(pack.contradictions) == 1
    contradiction = pack.contradictions[0]
    assert contradiction.resolution_status is ResolutionStatus.UNRESOLVED
    assert contradiction.resolution_note is None

    # Both conflicting records are retained, statuses untouched.
    by_id = {
        r.record_id: r
        for records in pack.sections.values()
        for r in records
    }
    assert by_id["deck-width-tape"].fields["value"] == 60.5
    assert by_id["deck-width-spec"].fields["value"] == 60.0
    assert by_id["deck-width-tape"].status is InfoStatus.MEASURED
    assert by_id["deck-width-spec"].status is InfoStatus.MEASURED

    restored = BuildPack.from_json(pack.to_json())
    assert restored.contradictions == pack.contradictions


# ---------------------------------------------------------------------------
# §10.8: UNKNOWN records are valid
# ---------------------------------------------------------------------------


def test_unknown_records_are_valid() -> None:
    pack = _load_fixture()
    unknown_records = [
        r
        for records in pack.sections.values()
        for r in records
        if r.status is InfoStatus.UNKNOWN
    ]
    assert len(unknown_records) >= 1
    assert pack.validate() is None

    axle = next(r for r in unknown_records if r.record_id == "axle-rating")
    assert axle.fields["value"] is None
    assert axle.provenance is None


# ---------------------------------------------------------------------------
# §10.9: invalid status values are rejected
# ---------------------------------------------------------------------------


def test_invalid_status_rejected_at_construction() -> None:
    with pytest.raises(BuildPackError):
        BuildRecord(record_id="r1", status="GUESSED", fields={})


def test_invalid_status_rejected_at_deserialization() -> None:
    pack = _load_fixture()
    data = pack.to_dict()
    data["sections"]["donor"][0]["status"] = "ALMOST_CERTAIN"
    with pytest.raises(BuildPackError):
        BuildPack.from_dict(data)


def test_missing_status_is_not_defaulted() -> None:
    """A record without a status is malformed, never silently promoted."""
    pack = _load_fixture()
    data = pack.to_dict()
    del data["sections"]["donor"][0]["status"]
    with pytest.raises(BuildPackError):
        BuildPack.from_dict(data)


# ---------------------------------------------------------------------------
# §10.10: malformed required project data is rejected
# ---------------------------------------------------------------------------


def test_blank_project_name_rejected() -> None:
    with pytest.raises(BuildPackError):
        _identity(name="  ")


def test_malformed_identity_date_rejected() -> None:
    with pytest.raises(BuildPackError):
        _identity(created="not-a-date")


def test_negative_identity_revision_rejected() -> None:
    with pytest.raises(BuildPackError):
        _identity(revision=-1)


def test_missing_identity_rejected() -> None:
    pack = _load_fixture()
    data = pack.to_dict()
    del data["identity"]
    with pytest.raises(BuildPackError):
        BuildPack.from_dict(data)


def test_record_missing_required_section_field_rejected() -> None:
    pack = _minimal_pack(
        sections={"measurements": [_record("m1", {"value": 3.0})]}
    )
    with pytest.raises(BuildPackError, match="missing required field"):
        pack.validate()


def test_unknown_section_rejected() -> None:
    pack = _minimal_pack(
        sections={"teleportation": [_record("t1", {"device": "pad"})]}
    )
    with pytest.raises(BuildPackError, match="unknown section"):
        pack.validate()


def test_unknown_field_rejected() -> None:
    pack = _minimal_pack(
        sections={"donor": [_record("d1", {"manufacturer": "X", "warp_drive": True})]}
    )
    with pytest.raises(BuildPackError, match="unknown field"):
        pack.validate()


# ---------------------------------------------------------------------------
# Sensible required relationships
# ---------------------------------------------------------------------------


def test_duplicate_record_id_rejected() -> None:
    pack = _minimal_pack(
        sections={
            "donor": [
                _record("dup", {"manufacturer": "A"}),
                _record("dup", {"manufacturer": "B"}),
            ]
        }
    )
    with pytest.raises(BuildPackError, match="duplicate record_id"):
        pack.validate()


def test_contradiction_with_unknown_record_rejected() -> None:
    pack = _minimal_pack(
        contradictions=[
            Contradiction(
                contradiction_id="c1",
                record_ids=["donor-1", "no-such-record"],
                affected_field="manufacturer",
                explanation="sample",
            )
        ]
    )
    with pytest.raises(BuildPackError, match="unknown record"):
        pack.validate()


def test_contradiction_needs_two_distinct_records() -> None:
    with pytest.raises(BuildPackError):
        Contradiction(
            contradiction_id="c1",
            record_ids=["only-one"],
            affected_field="value",
            explanation="sample",
        )


def test_resolved_contradiction_requires_resolution_note() -> None:
    pack = _minimal_pack(
        sections={
            "measurements": [
                _record("m1", {"measurement": "width", "value": 1.0}),
                _record("m2", {"measurement": "width", "value": 2.0}),
            ]
        },
        contradictions=[
            Contradiction(
                contradiction_id="c1",
                record_ids=["m1", "m2"],
                affected_field="value",
                explanation="sample",
                resolution_status=ResolutionStatus.RESOLVED,
                resolution_note=None,
            )
        ],
    )
    with pytest.raises(BuildPackError, match="resolution_note"):
        pack.validate()


def test_non_increasing_revision_numbers_rejected() -> None:
    pack = _minimal_pack(
        change_history=[
            Revision(
                revision=2,
                timestamp="2026-09-20T10:00:00",
                description="second",
                affected_area="donor",
            ),
            Revision(
                revision=2,
                timestamp="2026-09-20T11:00:00",
                description="duplicate number",
                affected_area="donor",
            ),
        ]
    )
    with pytest.raises(BuildPackError, match="strictly increasing"):
        pack.validate()


def test_malformed_json_rejected() -> None:
    with pytest.raises(BuildPackError):
        BuildPack.from_json("{not json")
