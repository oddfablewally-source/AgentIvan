"""Brick 6 tests -- deterministic Build Pack query + change intelligence.

Reuses the synthetic Brick 5 reconciliation scenario ("Nefarious Rig")
via the Brick 5 helpers, then extends it: a fourth research source is
accepted (RESEARCHED / CALCULATED / UNKNOWN records, an open issue, and
unicode content), and two source files are tampered with to exercise
source-integrity queries (one changed, one missing). Brick 5's own test
module is untouched.

No real OddFable data. No network. No LLM.
"""

from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path
from unittest import mock

import pytest

import src.app.product.agentivan.query as query_module
from src.app.product.agentivan import (
    BuildPack,
    BuildPackQuery,
    ChangeSet,
    ContradictionDetail,
    InfoStatus,
    KEEP_EXISTING,
    KnowledgeReport,
    Project,
    ProvenanceHit,
    QueryError,
    ReconciliationSession,
    RecordHit,
    RemainingWork,
    diff_packs,
)
from src.app.product.agentivan.extraction import CandidateCategory
from tests.test_candidate_reconciliation import (
    _pick,
    _prepare_project,
)

SOURCE_D = (
    "# Research notes\n\n"
    "Harness length 2 m. [RESEARCHED]\n"
    "Part: crimson widget [PROPOSED]\n"
    "Load rating 500 kg. [CALCULATED]\n"
    "Part: café bracket\n"
)


def _run_brick6_decisions(project: Project, fixture: dict) -> dict:
    """The Brick 5 decision flow, except the paint TODO is accepted as an
    open issue (Brick 6 needs an open issue in the canonical pack)."""
    session = ReconciliationSession(project)
    session.import_candidates(fixture["extraction_set"])

    c_28 = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="28 inches",
        label="Session notes",
    )
    c_bracket = _pick(
        session, fixture,
        category=CandidateCategory.PART,
        fragment="steel bracket",
        label="Session notes",
    )
    c_paint = _pick(
        session, fixture,
        category=CandidateCategory.OPEN_ISSUE,
        fragment="paint color",
        label="Session notes",
    )
    c_42 = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="42 mm",
        label="Session notes",
    )
    c_30 = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="30 inches",
        label="Follow-up notes",
    )
    c_dup = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="28 inches",
        label="Follow-up notes",
    )
    c_26 = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="26 inches",
        label="Old notes",
    )

    record_28 = session.accept(
        c_28.reconciliation_id, note="reviewed: primary measurement"
    )
    session.accept(c_bracket.reconciliation_id)
    record_paint = session.accept(
        c_paint.reconciliation_id, note="tracked for later"
    )
    session.defer(c_42.reconciliation_id, reason="unclear which rail")

    contradiction_open = session.flag_contradiction(
        c_30.reconciliation_id,
        existing_record_id=record_28.record_id,
        description=(
            "Source B states seat height 30 in; "
            "the pack records 28 in (MEASURED)"
        ),
    )
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
    session.accept(c_dup.reconciliation_id)

    # Research source decisions.
    c_2m = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="2 m",
        label="Research notes",
    )
    c_500 = _pick(
        session, fixture,
        category=CandidateCategory.MEASUREMENT,
        fragment="500 kg",
        label="Research notes",
    )
    c_crimson = _pick(
        session, fixture,
        category=CandidateCategory.PART,
        fragment="crimson widget",
        label="Research notes",
    )
    c_cafe = _pick(
        session, fixture,
        category=CandidateCategory.PART,
        fragment="café bracket",
        label="Research notes",
    )
    session.accept(c_2m.reconciliation_id)
    session.accept(c_500.reconciliation_id)
    session.accept(c_crimson.reconciliation_id)
    session.accept(c_cafe.reconciliation_id)

    session.save()
    return {
        "session": session,
        "record_28": record_28,
        "record_paint": record_paint,
        "contradiction_open": contradiction_open,
        "contradiction_resolved": contradiction_resolved,
    }


def _prepare_brick6(root: Path) -> dict:
    """Build the full Brick 6 fixture: Brick 5 scenario + research
    decisions + one changed and one missing source file."""
    fixture = _prepare_project(root)
    project = fixture["project"]
    rec_d = project.intake_text(SOURCE_D, label="Research notes")
    fixture["source_ids"]["Research notes"] = rec_d.source_id
    extraction_set = project.extractions.run()
    project.extractions.save(extraction_set)
    fixture["extraction_set"] = extraction_set

    ids = _run_brick6_decisions(project, fixture)
    fixture.update(ids)

    # Persist the project record itself so a reloaded/relocated project
    # still knows its sources (session.save covers pack + reconciliation
    # only). This must happen before the integrity tampering below, which
    # is file-level and must not alter any record.
    project.save()

    # Tamper AFTER all decisions: one source file changed on disk,
    # one source file gone entirely.
    catalog = project.catalog
    rec_b = catalog.get(fixture["source_ids"]["Follow-up notes"])
    path_b = catalog._stored_file_path(rec_b)
    with open(path_b, "a", encoding="utf-8") as handle:
        handle.write("\nSneaky extra line appended after intake.\n")
    rec_c = catalog.get(fixture["source_ids"]["Old notes"])
    catalog._stored_file_path(rec_c).unlink()
    return fixture


@pytest.fixture()
def brick6(tmp_path: Path) -> dict:
    return _prepare_brick6(tmp_path / "brick6")


@pytest.fixture()
def query(brick6: dict) -> BuildPackQuery:
    return BuildPackQuery.for_project(brick6["project"])


# ---------------------------------------------------------------------------
# 1-4: identity, sections, retrieval
# ---------------------------------------------------------------------------


def test_project_identity_query(query: BuildPackQuery) -> None:
    identity = query.identity()
    assert identity.name == "Nefarious Rig"
    assert identity.project_id


def test_section_listing(query: BuildPackQuery) -> None:
    assert query.sections() == ["measurements", "open_issues", "parts"]


def test_section_retrieval(query: BuildPackQuery) -> None:
    hits = query.section("measurements")
    assert {hit.record_id for hit in hits}
    assert all(hit.section == "measurements" for hit in hits)
    # deterministic ordering by record_id
    assert [hit.record_id for hit in hits] == sorted(
        hit.record_id for hit in hits
    )
    with pytest.raises(QueryError):
        query.section("nope")


def test_section_has_content(query: BuildPackQuery) -> None:
    assert query.section_has_content("measurements") is True
    assert query.section_has_content("open_issues") is True
    with pytest.raises(QueryError):
        query.section_has_content("nope")


# ---------------------------------------------------------------------------
# 5-8: status and text filtering
# ---------------------------------------------------------------------------


def test_status_filtering_is_exact(query: BuildPackQuery) -> None:
    measured = query.by_status(InfoStatus.MEASURED)
    assert len(measured) == 1
    assert "28" in measured[0].fields["measurement"]

    researched = query.by_status("RESEARCHED")  # string coercion works
    assert len(researched) == 1
    assert "2 m" in researched[0].fields["measurement"]

    calculated = query.by_status(InfoStatus.CALCULATED)
    assert len(calculated) == 1

    known = query.by_status(InfoStatus.KNOWN)
    assert len(known) == 1  # the pre-existing mounting plate

    verified = query.by_status(InfoStatus.VERIFIED)
    assert verified == []  # nothing invented to fill the group

    with pytest.raises(QueryError):
        query.by_status("ALMOST_KNOWN")


def test_unknown_query(query: BuildPackQuery) -> None:
    hits = query.unknown()
    texts = " ".join(
        json.dumps(hit.fields, sort_keys=True, ensure_ascii=False)
        for hit in hits
    )
    assert "steel bracket" in texts
    assert "crimson widget" in texts
    assert "café bracket" in texts
    assert "paint color" in texts
    assert all(hit.status == InfoStatus.UNKNOWN.value for hit in hits)


def test_combined_record_filters(query: BuildPackQuery) -> None:
    hits = query.records(
        section="parts", status=InfoStatus.UNKNOWN, text="café"
    )
    assert len(hits) == 1
    assert hits[0].section == "parts"


def test_text_matching_is_case_insensitive(query: BuildPackQuery) -> None:
    lower = query.records(text="café")
    upper = query.records(text="CAFÉ")
    assert [h.record_id for h in lower] == [h.record_id for h in upper]
    assert len(lower) == 1


# ---------------------------------------------------------------------------
# 9: source / provenance filtering
# ---------------------------------------------------------------------------


def test_by_source(query: BuildPackQuery, brick6: dict) -> None:
    source_d = brick6["source_ids"]["Research notes"]
    hits = query.by_source(source_d)
    assert len(hits) == 4
    assert all(isinstance(hit, ProvenanceHit) for hit in hits)
    for hit in hits:
        assert hit.provenance_kind == "extracted"
        assert hit.extraction_id
        assert hit.reconciliation_id
        assert hit.reconciliation_state == "accepted"
        assert source_d in (hit.provenance or {}).get("source_reference", "")

    source_a = brick6["source_ids"]["Session notes"]
    hits_a = query.by_source(source_a)
    assert len(hits_a) == 3  # 28 in + bracket + paint issue
    assert {hit.section for hit in hits_a} == {
        "measurements",
        "parts",
        "open_issues",
    }

    with pytest.raises(QueryError):
        query.by_source("")


def test_manual_provenance_is_not_fabricated(query: BuildPackQuery) -> None:
    hits = query.records(text="mounting plate")
    assert len(hits) == 1
    # The pre-existing record has manual provenance: by_source for any
    # extraction source must not claim it, and its kind reports honestly.
    by_any = query.by_source("src-does-not-exist")
    assert all(h.record_id != hits[0].record_id for h in by_any)


# ---------------------------------------------------------------------------
# 10-11: open issues, contradictions, deferred, integrity
# ---------------------------------------------------------------------------


def test_open_issue_query(query: BuildPackQuery) -> None:
    issues = query.open_issues()
    assert len(issues) == 1
    assert "paint color" in json.dumps(issues[0].fields)


def test_deferred_candidate_query(
    query: BuildPackQuery, brick6: dict
) -> None:
    deferred = query.deferred_candidates()
    assert len(deferred) == 1
    assert "42 mm" in deferred[0].text

    # without reconciliation state the query says so, instead of guessing
    bare = BuildPackQuery(brick6["project"].buildpack_store.load())
    with pytest.raises(QueryError):
        bare.deferred_candidates()


def test_unresolved_contradiction_query(
    query: BuildPackQuery, brick6: dict
) -> None:
    open_ctrds = query.unresolved_contradictions()
    assert len(open_ctrds) == 1
    assert (
        open_ctrds[0].contradiction_id
        == brick6["contradiction_open"].contradiction_id
    )


def test_resolved_contradiction_query(
    query: BuildPackQuery, brick6: dict
) -> None:
    resolved = query.resolved_contradictions()
    assert len(resolved) == 1
    assert (
        resolved[0].contradiction_id
        == brick6["contradiction_resolved"].contradiction_id
    )
    assert resolved[0].state.value == "RESOLVED"
    assert resolved[0].resolution == KEEP_EXISTING

    all_ctrds = query.contradictions()
    assert len(all_ctrds) == 2


def test_contradiction_detail(
    query: BuildPackQuery, brick6: dict
) -> None:
    cid = brick6["contradiction_open"].contradiction_id
    detail = query.contradiction_detail(cid)
    assert isinstance(detail, ContradictionDetail)
    assert detail.resolution is None  # still open: nothing decided here
    assert detail.existing is not None
    assert detail.existing["section"] == "measurements"
    assert detail.existing["record_id"] == brick6["record_28"].record_id
    assert "28" in json.dumps(detail.existing["fields"])
    assert detail.candidate is not None
    assert "30 inches" in detail.candidate["text"]
    assert detail.candidate["state"] == "contradicted"

    cid_resolved = brick6["contradiction_resolved"].contradiction_id
    detail_resolved = query.contradiction_detail(cid_resolved)
    assert detail_resolved.resolution is not None
    assert detail_resolved.resolution["decision"] == KEEP_EXISTING

    with pytest.raises(Exception):
        query.contradiction_detail("ctrd-does-not-exist")


def test_source_integrity_query(query: BuildPackQuery, brick6: dict) -> None:
    statuses = query.source_integrity()
    by_label = {
        label: next(
            s
            for s in statuses
            if s["source_id"] == brick6["source_ids"][label]
        )
        for label in brick6["source_ids"]
    }
    assert by_label["Session notes"]["state"] == "intact"
    assert by_label["Research notes"]["state"] == "intact"
    assert by_label["Follow-up notes"]["state"] == "changed"
    assert by_label["Old notes"]["state"] == "missing"

    warnings = query.integrity_warnings()
    assert {w["state"] for w in warnings} == {"changed", "missing"}
    assert len(warnings) == 2

    # without a catalog the query says so instead of inventing results
    bare = BuildPackQuery(brick6["project"].buildpack_store.load())
    with pytest.raises(QueryError):
        bare.source_integrity()


# ---------------------------------------------------------------------------
# 12-13: revisions
# ---------------------------------------------------------------------------


def test_revision_listing_is_chronological(query: BuildPackQuery) -> None:
    revisions = query.revisions()
    numbers = [rev.revision for rev in revisions]
    assert numbers == sorted(numbers)
    assert len(numbers) >= 7  # one per accepted record
    assert all(rev.description for rev in revisions)
    assert all(rev.affected_area for rev in revisions)


def test_revision_retrieval(query: BuildPackQuery) -> None:
    first = query.revision(1)
    assert first.revision == 1
    with pytest.raises(QueryError):
        query.revision(9999)


def test_revision_sources_reported_where_recorded(
    query: BuildPackQuery,
) -> None:
    reported = 0
    for rev in query.revisions():
        sources = query.revision_sources(rev)
        if sources is not None:
            reported += 1
            assert "source_reference" in sources
    assert reported >= 1  # acceptance revisions carry extraction provenance


# ---------------------------------------------------------------------------
# 14-19: structural diff
# ---------------------------------------------------------------------------


def _load_pack(project: Project) -> BuildPack:
    return project.buildpack_store.load()


def test_diff_of_identical_packs_is_empty(query: BuildPackQuery) -> None:
    pack = query._pack
    changeset = diff_packs(pack, pack)
    assert changeset.is_empty()
    assert changeset.unchanged  # ...but unchanged records are identified


def test_diff_detects_added_information(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    hits = BuildPackQuery(after).section("measurements")
    assert hits  # sanity

    from src.app.product.agentivan import BuildRecord

    new_record = BuildRecord(
        record_id="rec-synthetic-added",
        status=InfoStatus.PROPOSED,
        fields={"item": "synthetic addition"},
    )
    after.sections.setdefault("testing", []).append(new_record)

    changeset = diff_packs(before, after)
    assert len(changeset.added) == 1
    entry = changeset.added[0]
    assert entry.change == "added"
    assert entry.section == "testing"
    assert entry.record_id == "rec-synthetic-added"
    assert entry.old_value is None
    assert entry.new_value["fields"] == {"item": "synthetic addition"}
    assert not changeset.removed


def test_diff_detects_removed_information(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    removed_id = after.sections["parts"][0].record_id
    after.sections["parts"] = after.sections["parts"][1:]

    changeset = diff_packs(before, after)
    assert len(changeset.removed) == 1
    entry = changeset.removed[0]
    assert entry.change == "removed"
    assert entry.record_id == removed_id
    assert entry.new_value is None
    assert entry.old_value["fields"]
    assert not changeset.added


def test_diff_detects_modified_information(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    record = after.sections["measurements"][0]
    record.fields["measurement"] = "29 inches"
    record.notes = "re-measured"

    changeset = diff_packs(before, after)
    paths = {(e.path, e.old_value, e.new_value) for e in changeset.modified}
    assert any(
        path == "fields.measurement" and new == "29 inches"
        for path, _old, new in paths
    )
    assert any(path == "notes" for path, _old, _new in paths)
    assert not changeset.added and not changeset.removed


def test_diff_identifies_unchanged_information(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    changeset = diff_packs(before, after)
    total_records = sum(len(rs) for rs in before.sections.values())
    assert len(changeset.unchanged) == total_records
    assert all(
        {"section", "record_id"} == set(pair) for pair in changeset.unchanged
    )


def test_diff_preserves_structured_paths(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    record = after.sections["measurements"][0]
    record.status = InfoStatus.VERIFIED

    changeset = diff_packs(before, after)
    status_changes = [e for e in changeset.modified if e.path == "status"]
    assert len(status_changes) == 1
    entry = status_changes[0]
    assert entry.section == "measurements"
    assert entry.record_id == record.record_id
    assert entry.old_value != entry.new_value


def test_diff_is_deterministic(brick6: dict) -> None:
    before = _load_pack(brick6["project"])
    after = BuildPack.from_dict(before.to_dict())
    after.sections["measurements"][0].fields["measurement"] = "29 inches"
    first = diff_packs(before, after).to_dict()
    second = diff_packs(before, after).to_dict()
    assert first == second


# ---------------------------------------------------------------------------
# 20-21: "what do we know?" / "what remains?"
# ---------------------------------------------------------------------------


def test_what_do_we_know_grouping(query: BuildPackQuery) -> None:
    report = query.what_do_we_know()
    assert isinstance(report, KnowledgeReport)
    groups = report.groups
    assert [s for s in groups] == [s.value for s in InfoStatus]
    assert len(groups["MEASURED"]) == 1
    assert len(groups["RESEARCHED"]) == 1
    assert len(groups["CALCULATED"]) == 1
    assert len(groups["KNOWN"]) == 1
    assert len(groups["UNKNOWN"]) == 4
    assert groups["VERIFIED"] == []
    assert groups["PROPOSED"] == []
    # every record in the pack appears exactly once across groups
    total = sum(len(hits) for hits in groups.values())
    assert total == sum(len(rs) for rs in query._pack.sections.values())


def test_what_remains_grouping(query: BuildPackQuery) -> None:
    remaining = query.what_remains()
    assert isinstance(remaining, RemainingWork)
    assert len(remaining.open_issues) == 1
    assert len(remaining.unknown_entries) == 4
    assert len(remaining.deferred_candidates) == 1
    assert "42 mm" in remaining.deferred_candidates[0]["text"]
    assert len(remaining.unresolved_contradictions) == 1
    assert {w["state"] for w in remaining.source_integrity_warnings} == {
        "changed",
        "missing",
    }
    # categories stay distinct: nothing is merged into a "problem" bucket
    assert set(remaining.to_dict()) == {
        "open_issues",
        "unknown_entries",
        "deferred_candidates",
        "unresolved_contradictions",
        "source_integrity_warnings",
    }


# ---------------------------------------------------------------------------
# 22: search
# ---------------------------------------------------------------------------


def test_search(query: BuildPackQuery) -> None:
    assert len(query.search("café")) == 1
    assert len(query.search("INCHES")) >= 1  # case-insensitive
    assert query.search("inches", section="parts") == []  # section filter

    exact = query.search(field="measurement", value="28 inches")
    assert len(exact) == 1
    assert exact[0].section == "measurements"

    assert query.search(field="measurement", value="nope") == []

    status_hits = query.search("bracket", status=InfoStatus.UNKNOWN)
    assert len(status_hits) >= 1
    assert all(h.status == "UNKNOWN" for h in status_hits)

    with pytest.raises(QueryError):
        query.search()
    with pytest.raises(QueryError):
        query.search(field="measurement")  # value missing


# ---------------------------------------------------------------------------
# 23: serialization
# ---------------------------------------------------------------------------


def test_query_results_serialize_and_load(query: BuildPackQuery) -> None:
    hit = query.section("measurements")[0]
    assert RecordHit.from_dict(hit.to_dict()).to_dict() == hit.to_dict()

    assert query.by_source("src-does-not-exist") == []  # no hits, no crash

    source_d = next(
        s["source_id"]
        for s in query.source_integrity()
        if s["state"] == "intact"
    )
    phit = query.by_source(source_d)[0]
    assert ProvenanceHit.from_dict(phit.to_dict()).to_dict() == phit.to_dict()

    before = query._pack
    after = BuildPack.from_dict(before.to_dict())
    after.sections["measurements"][0].fields["measurement"] = "29 inches"
    changeset = diff_packs(before, after)
    loaded = ChangeSet.from_json(changeset.to_json())
    assert loaded.to_dict() == changeset.to_dict()

    report = query.what_do_we_know()
    assert KnowledgeReport.from_json(report.to_json()).to_dict() == (
        report.to_dict()
    )

    remaining = query.what_remains()
    assert RemainingWork.from_json(remaining.to_json()).to_dict() == (
        remaining.to_dict()
    )

    detail = query.contradiction_detail(
        query.unresolved_contradictions()[0].contradiction_id
    )
    assert ContradictionDetail.from_dict(detail.to_dict()).to_dict() == (
        detail.to_dict()
    )

    with pytest.raises(QueryError):
        ChangeSet.from_json("{not json")


# ---------------------------------------------------------------------------
# 24-28: read-only guarantees
# ---------------------------------------------------------------------------


def _snapshot(brick6: dict) -> dict:
    project = brick6["project"]
    return {
        "pack": project.buildpack_store.load().to_dict(),
        "reconciliation": project.reconciliation_store.load().to_dict(),
        "extractions": project.extractions.load().to_json(),
        "project": project.to_dict(),
    }


def test_queries_are_read_only(query: BuildPackQuery, brick6: dict) -> None:
    before = _snapshot(brick6)

    query.identity()
    query.sections()
    query.section("measurements")
    query.records(text="bracket")
    query.by_status(InfoStatus.MEASURED)
    query.unknown()
    query.open_issues()
    query.by_source(brick6["source_ids"]["Research notes"])
    query.deferred_candidates()
    query.contradictions()
    query.contradiction_detail(
        query.unresolved_contradictions()[0].contradiction_id
    )
    query.revisions()
    query.revision(1)
    query.source_integrity()
    query.integrity_warnings()
    query.what_do_we_know()
    query.what_remains()
    query.search("inches")
    diff_packs(query._pack, BuildPack.from_dict(query._pack.to_dict()))

    after = _snapshot(brick6)
    assert after == before


def test_raw_sources_unchanged_by_queries(
    query: BuildPackQuery, brick6: dict
) -> None:
    catalog = brick6["project"].catalog
    hashes_before = {
        r.source_id: r.sha256 for r in catalog.list()
    }
    query.what_remains()
    query.source_integrity()
    query.by_source(brick6["source_ids"]["Session notes"])
    hashes_after = {r.source_id: r.sha256 for r in catalog.list()}
    assert hashes_after == hashes_before


# ---------------------------------------------------------------------------
# 29-31: relocation, unicode, determinism
# ---------------------------------------------------------------------------


def test_project_relocation_still_works(
    tmp_path: Path, brick6: dict, query: BuildPackQuery
) -> None:
    moved = tmp_path / "moved"
    shutil.copytree(brick6["project"].root, moved)
    reloaded = Project.load(moved)
    moved_query = BuildPackQuery.for_project(reloaded)

    assert moved_query.what_do_we_know().to_dict() == (
        query.what_do_we_know().to_dict()
    )
    assert moved_query.what_remains().to_dict()["open_issues"] == (
        query.what_remains().to_dict()["open_issues"]
    )
    assert moved_query.sections() == query.sections()
    assert moved_query.identity().name == "Nefarious Rig"


def test_unicode_survives(query: BuildPackQuery) -> None:
    hits = query.search("café")
    assert len(hits) == 1
    payload = json.dumps(hits[0].to_dict(), ensure_ascii=False)
    assert "café" in payload
    assert "café" in json.loads(payload)["fields"]["name"]


def test_results_are_deterministic(
    tmp_path: Path, brick6: dict, query: BuildPackQuery
) -> None:
    # Equivalent states: the same finished project, copied. Content-derived
    # ids are identical, so query results must be byte-identical too.
    moved = tmp_path / "det-copy"
    shutil.copytree(brick6["project"].root, moved)
    other = BuildPackQuery.for_project(Project.load(moved))

    assert other.what_do_we_know().to_dict() == query.what_do_we_know().to_dict()
    assert other.sections() == query.sections()
    assert [h.to_dict() for h in other.records()] == [
        h.to_dict() for h in query.records()
    ]
    assert other.what_remains().to_dict() == query.what_remains().to_dict()
    pack = query._pack
    assert diff_packs(pack, other._pack).is_empty()


# ---------------------------------------------------------------------------
# 32-33: no network, no LLM
# ---------------------------------------------------------------------------


def test_no_network_is_required(tmp_path: Path) -> None:
    def _no_sockets(*args, **kwargs):
        raise AssertionError("network access attempted")

    with mock.patch.object(socket, "socket", _no_sockets):
        fixture = _prepare_brick6(tmp_path / "nonet")
        query = BuildPackQuery.for_project(fixture["project"])
        query.what_do_we_know()
        query.what_remains()
        query.search("inches")
        diff_packs(query._pack, BuildPack.from_dict(query._pack.to_dict()))


def test_no_llm_is_required() -> None:
    source = Path(query_module.__file__).read_text(encoding="utf-8")
    for token in (
        "import ollama",
        "import pydanticai",
        "from openai",
        "from anthropic",
        "import requests",
        "Ollama(",
    ):
        assert token not in source, f"forbidden token in query.py: {token}"
    assert "ollama" not in sys.modules
    assert "pydanticai" not in sys.modules
