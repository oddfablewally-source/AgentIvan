"""
AgentIvan unified project service / operations facade (Brick 10).

``ProjectService`` is a thin orchestration boundary through which
higher-level AgentIvan features operate on a project without knowing
the implementation details of the individual bricks:

                    HIGHER-LEVEL AGENTIVAN
                             |
                             v
                  +---------------------+
                  |   ProjectService    |
                  +----------+----------+
                             |
          +------------------+------------------+
          v                  v                  v
      Workspace          BuildPack           Sources
      /Project            Query               Intake
          |                  |                  |
          +--------------+--+----------------+--+
          v              v                  v
     Reconciliation   Exports           Snapshots
       /Assembly     Markdown/PDF         ZIP

The service coordinates existing functionality; it never duplicates
it. There is no second project model, no second persistence
mechanism, no cache, no background work, no network, no LLM, no UI.

Every operation delegates to the layer that owns it:

- project lifecycle ......... AgentIvanWorkspace / Project (Brick 2)
- source intake ............. Project intake (Brick 2)
- source inspection ......... SourceCatalog (Brick 3)
- extraction ................ ExtractionStore (Brick 4)
- reconciliation ............ ReconciliationSession (Brick 5)
- Build Pack queries ........ BuildPackQuery (Brick 6)
- Markdown export ........... Project.export_markdown (Brick 7)
- PDF export ................ Project.export_pdf (Brick 8)
- snapshots ................. snapshot exporter/importer (Brick 9)

Operation kinds are explicit in the docstrings:

- READ-ONLY: inspect without mutating anything.
- MUTATING: change canonical state through the existing controlled
  persistence mechanism (the same save paths the lower layers use).
- OUTPUT: write a regenerable artifact (Markdown/PDF/snapshot bytes).
- IMPORT: restore a project from a snapshot archive.

Domain errors from the lower layers (ProjectError, WorkspaceError,
CatalogError, ExtractionError, ReconciliationError, QueryError,
MarkdownExportError, PDFExportError, SnapshotError, ...) propagate
unchanged. ``ProjectServiceError`` exists only for errors genuinely
caused by service orchestration (for example: no project bound).

Stdlib only. No database, no network, no AI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import IntegrityStatus, SourceCatalog
from .extraction import ExtractionSet
from .model import BuildPack, BuildRecord, ProjectIdentity
from .query import (
    BuildPackQuery,
    ChangeSet,
    KnowledgeReport,
    RecordHit,
    RemainingWork,
    diff_packs,
)
from .reconciliation import (
    ReconciledCandidate,
    ReconciliationContradiction,
    ReconciliationSession,
    ReconciliationState,
)
from .snapshot import SnapshotResult, import_snapshot
from .sources import SourceKind, SourceRecord
from .project import Project
from .workspace import AgentIvanWorkspace

__all__ = [
    "ProjectService",
    "ProjectServiceError",
]


class ProjectServiceError(ValueError):
    """Raised for errors genuinely caused by service orchestration.

    Lower-layer domain errors are never converted into this type:
    they propagate unchanged so higher layers keep useful error
    identity.
    """


class ProjectService:
    """Thin orchestration facade over exactly one AgentIvan project.

    The service is bound to a single :class:`Project` for its whole
    lifetime, except that the explicit lifecycle operations
    (:meth:`create_project`, :meth:`open_project`, :meth:`load_project`)
    rebind it to a different project. Because the bound project is
    explicit, an operation can never accidentally act on whichever
    project happens to be active elsewhere.

    The service keeps no state of its own: no cache, no second
    active-project flag, no background threads. Reads always go
    through the owning layers, so they observe canonical state.
    """

    # ------------------------------------------------------------------
    # binding / lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        workspace: AgentIvanWorkspace | None = None,
        *,
        project_id: str | None = None,
        project: Project | None = None,
    ) -> None:
        """Bind the service to one project.

        Give ``project`` directly, or ``project_id`` to load it from the
        workspace without changing the active project, or neither to
        bind the workspace's active project. Raises
        :class:`ProjectServiceError` when no project can be bound.
        """
        self._workspace = (
            workspace if workspace is not None else AgentIvanWorkspace()
        )
        if project is not None:
            if not isinstance(project, Project):
                raise ProjectServiceError(
                    "project must be an AgentIvan Project instance"
                )
            self._project = project
        elif project_id is not None:
            # WorkspaceError (unknown project) propagates unchanged.
            self._project = self._workspace.get_project(project_id)
        else:
            active_id = self._workspace.active_project_id
            if active_id is None:
                raise ProjectServiceError(
                    "no project bound: pass project= or project_id=, "
                    "or open a project in the workspace first"
                )
            self._project = self._workspace.get_project(active_id)

    @property
    def workspace(self) -> AgentIvanWorkspace:
        """The workspace this service was built on. READ-ONLY accessor."""
        return self._workspace

    @property
    def project(self) -> Project:
        """The bound project. READ-ONLY accessor; never a copy."""
        return self._project

    @property
    def project_id(self) -> str:
        """Canonical identity of the bound project. READ-ONLY."""
        return self._project.project_id

    @property
    def active_project_id(self) -> str | None:
        """The workspace's active project id. READ-ONLY.

        This is the workspace's own state, exposed -- never a second
        service-level copy that could diverge.
        """
        return self._workspace.active_project_id

    def create_project(self, name: str) -> Project:
        """MUTATING. Create a project, bind the service, make it active.

        Delegates to :meth:`AgentIvanWorkspace.create_project`; the new
        project is also set as the workspace's active project so the
        service binding and workspace state stay coherent.
        """
        project = self._create_in_workspace(self._workspace, name)
        self._project = project
        return project

    @classmethod
    def create(
        cls, workspace: AgentIvanWorkspace | None, name: str
    ) -> "ProjectService":
        """MUTATING. Create a project and return a service bound to it.

        The bootstrap for a fresh workspace: no bound service is needed
        first. The new project becomes the workspace's active project.
        """
        ws = workspace if workspace is not None else AgentIvanWorkspace()
        project = cls._create_in_workspace(ws, name)
        return cls(ws, project=project)

    @staticmethod
    def _create_in_workspace(
        workspace: AgentIvanWorkspace, name: str
    ) -> Project:
        project = workspace.create_project(name)
        workspace.set_active_project(project.project_id)
        return project

    def open_project(self, project_id: str) -> Project:
        """MUTATING (workspace active-project only). Open and bind.

        Delegates to :meth:`AgentIvanWorkspace.open_project`, which
        makes the project active. Canonical project state is untouched.
        """
        project = self._workspace.open_project(project_id)
        self._project = project
        return project

    def load_project(self, project_id: str) -> Project:
        """READ-ONLY (workspace state untouched). Load and bind.

        Delegates to :meth:`AgentIvanWorkspace.get_project`: the
        workspace's active project is not changed.
        """
        project = self._workspace.get_project(project_id)
        self._project = project
        return project

    # ------------------------------------------------------------------
    # source intake (Brick 2) -- MUTATING
    # ------------------------------------------------------------------

    def add_text(self, text: str, **kwargs: Any) -> SourceRecord:
        """MUTATING. Intake raw text; persist via the project save path."""
        record = self._project.intake_text(text, **kwargs)
        self._project.save()
        return record

    def add_file(self, source_path: str | Path, **kwargs: Any) -> SourceRecord:
        """MUTATING. Intake a file byte-for-byte; persist via save path."""
        record = self._project.intake_file(source_path, **kwargs)
        self._project.save()
        return record

    def add_reference(self, reference: str, **kwargs: Any) -> SourceRecord:
        """MUTATING. Record a local reference (never fetched). Persisted."""
        record = self._project.intake_reference(reference, **kwargs)
        self._project.save()
        return record

    # ------------------------------------------------------------------
    # source inspection (Brick 3) -- READ-ONLY
    # ------------------------------------------------------------------

    def source_catalog(self) -> SourceCatalog:
        """READ-ONLY. The project's deterministic source catalog."""
        return self._project.catalog

    def get_source(self, source_id: str) -> SourceRecord:
        """READ-ONLY. One source record by id."""
        return self._project.catalog.get(source_id)

    def list_sources(
        self, kind: SourceKind | None = None, **kwargs: Any
    ) -> list[SourceRecord]:
        """READ-ONLY. Sources in deterministic catalog order."""
        if kind is None:
            return self._project.catalog.list(**kwargs)
        return self._project.catalog.list(kind=kind, **kwargs)

    def search_sources(self, query: str | None = None, **kwargs: Any) -> list[SourceRecord]:
        """READ-ONLY. Deterministic metadata substring search (no semantics)."""
        return self._project.catalog.search(query, **kwargs)

    def verify_source(self, source_id: str) -> IntegrityStatus:
        """READ-ONLY. Integrity of one source against its recorded hash."""
        return self._project.catalog.verify(source_id)

    def verify_all_sources(self) -> list[IntegrityStatus]:
        """READ-ONLY. Integrity of every source, deterministic order."""
        return self._project.catalog.verify_all()

    # ------------------------------------------------------------------
    # extraction (Brick 4) -- MUTATING (explicit only)
    # ------------------------------------------------------------------

    def extract(self) -> ExtractionSet:
        """MUTATING. Run the deterministic extractor and persist results.

        Uses the store's default :class:`DeterministicExtractor`; there
        is deliberately no extractor parameter, so no LLM extraction
        can be introduced through this path. Nothing is extracted
        automatically anywhere else in the service: callers request it
        explicitly here.
        """
        store = self._project.extractions
        extraction_set = store.run()
        store.save(extraction_set)
        return extraction_set

    def get_extraction_set(self) -> ExtractionSet:
        """READ-ONLY. The persisted extraction results, if any."""
        return self._project.extractions.load()

    # ------------------------------------------------------------------
    # reconciliation / Build Pack assembly (Brick 5)
    # ------------------------------------------------------------------

    def import_candidates(self) -> list[ReconciledCandidate]:
        """MUTATING. Register extracted items as reviewable candidates.

        Delegates to :meth:`ReconciliationSession.import_candidates`
        and persists the reconciliation state. Idempotent: items
        already imported are skipped by the lower layer.
        """
        session = ReconciliationSession(self._project)
        added = session.import_candidates(self._project.extractions.load())
        session.save()
        return added

    def pending_candidates(self) -> list[ReconciledCandidate]:
        """READ-ONLY. Candidates awaiting an explicit decision.

        A candidate is pending while its state is ``CANDIDATE``;
        deferred candidates are listed separately through
        :meth:`buildpack_query` (``deferred_candidates()``).
        """
        reconciliation = self._project.reconciliation_store.load_or_empty()
        return [
            candidate
            for candidate in reconciliation.candidates
            if candidate.state is ReconciliationState.CANDIDATE
        ]

    def get_candidate(self, reconciliation_id: str) -> ReconciledCandidate:
        """READ-ONLY. Inspect one candidate. Never mutates."""
        return self._project.reconciliation_store.load_or_empty().get_candidate(
            reconciliation_id
        )

    def get_contradiction(
        self, contradiction_id: str
    ) -> ReconciliationContradiction:
        """READ-ONLY. Inspect one contradiction. Never mutates."""
        return self._project.reconciliation_store.load_or_empty().get_contradiction(
            contradiction_id
        )

    def accept_candidate(
        self, reconciliation_id: str, *, note: str | None = None
    ) -> BuildRecord | None:
        """MUTATING. Explicitly accept a candidate into the Build Pack.

        Delegates to :meth:`ReconciliationSession.accept` and persists
        both the pack and the decision. Returns the created
        :class:`BuildRecord`, or ``None`` for an exact duplicate (the
        lower layer marks the candidate ``duplicate`` instead).
        """
        session = ReconciliationSession(self._project)
        record = session.accept(reconciliation_id, note=note)
        session.save()
        return record

    def reject_candidate(
        self, reconciliation_id: str, *, reason: str | None = None
    ) -> ReconciledCandidate:
        """MUTATING. Explicitly reject a candidate; it stays recorded."""
        session = ReconciliationSession(self._project)
        candidate = session.reject(reconciliation_id, reason=reason)
        session.save()
        return candidate

    def defer_candidate(
        self, reconciliation_id: str, *, reason: str | None = None
    ) -> ReconciledCandidate:
        """MUTATING. Explicitly defer a candidate; it stays unresolved."""
        session = ReconciliationSession(self._project)
        candidate = session.defer(reconciliation_id, reason=reason)
        session.save()
        return candidate

    def flag_contradiction(
        self,
        reconciliation_id: str,
        *,
        existing_record_id: str | None = None,
        affected_field: str | None = None,
        description: str,
        note: str | None = None,
    ) -> ReconciliationContradiction:
        """MUTATING. Record that a candidate conflicts with pack content.

        The existing record is left untouched; the conflict stays
        visible as an UNRESOLVED contradiction.
        """
        session = ReconciliationSession(self._project)
        contradiction = session.flag_contradiction(
            reconciliation_id,
            existing_record_id=existing_record_id,
            affected_field=affected_field,
            description=description,
            note=note,
        )
        session.save()
        return contradiction

    def resolve_contradiction(
        self,
        contradiction_id: str,
        *,
        resolution: str,
        note: str | None = None,
    ) -> ReconciliationContradiction:
        """MUTATING. Explicitly resolve a contradiction.

        ``resolution`` is ``"keep_existing"`` or ``"accept_incoming"``
        (see :mod:`reconciliation`). Nothing is auto-resolved.
        """
        session = ReconciliationSession(self._project)
        contradiction = session.resolve_contradiction(
            contradiction_id, resolution=resolution, note=note
        )
        session.save()
        return contradiction

    # ------------------------------------------------------------------
    # Build Pack queries (Brick 6) -- READ-ONLY
    # ------------------------------------------------------------------

    def get_build_pack(self) -> BuildPack:
        """READ-ONLY. The canonical Build Pack (created on first use)."""
        return self._project.buildpack_store.load_or_create()

    def buildpack_query(self) -> BuildPackQuery:
        """READ-ONLY. A fully-wired query over pack, reconciliation, catalog."""
        return BuildPackQuery.for_project(self._project)

    def get_project_identity(self) -> ProjectIdentity:
        """READ-ONLY. The canonical project identity from the Build Pack."""
        return self.buildpack_query().identity()

    def query(
        self,
        text: str | None = None,
        *,
        section: str | None = None,
        status: Any = None,
        source_id: str | None = None,
    ) -> list[RecordHit]:
        """READ-ONLY. Deterministic record query over the Build Pack.

        Routes to :meth:`BuildPackQuery.records`: exact filters only,
        no semantic matching.
        """
        return self.buildpack_query().records(
            section=section, status=status, source_id=source_id, text=text
        )

    def search(
        self,
        query_text: str | None = None,
        *,
        section: str | None = None,
        status: Any = None,
        field: str | None = None,
        value: Any = None,
    ) -> list[RecordHit]:
        """READ-ONLY. Deterministic substring/field search (no semantics)."""
        return self.buildpack_query().search(
            query_text, section=section, status=status, field=field, value=value
        )

    def what_do_we_know(self) -> KnowledgeReport:
        """READ-ONLY. Canonical information grouped by information status."""
        return self.buildpack_query().what_do_we_know()

    def what_remains(self) -> RemainingWork:
        """READ-ONLY. Unresolved work: issues, unknowns, deferred, conflicts."""
        return self.buildpack_query().what_remains()

    def get_changes(self, before: BuildPack, after: BuildPack) -> ChangeSet:
        """READ-ONLY. Structural diff between two Build Packs."""
        return diff_packs(before, after)

    def diff_build_packs(self, before: BuildPack, after: BuildPack) -> ChangeSet:
        """READ-ONLY. Alias of :meth:`get_changes` (Brick 6 ``diff_packs``)."""
        return diff_packs(before, after)

    # ------------------------------------------------------------------
    # exports -- OUTPUT (Brick 7 / Brick 8)
    # ------------------------------------------------------------------

    def export_markdown(
        self, destination: str | Path | None = None
    ) -> Path:
        """OUTPUT. Render the canonical Build Pack to Markdown (Brick 7).

        Delegates to :meth:`Project.export_markdown`. Canonical state
        is never mutated.
        """
        return self._project.export_markdown(destination)

    def export_pdf(self, destination: str | Path | None = None) -> Path:
        """OUTPUT. Render the canonical Build Pack to PDF (Brick 8).

        Delegates to :meth:`Project.export_pdf`. Canonical state is
        never mutated.
        """
        return self._project.export_pdf(destination)

    # ------------------------------------------------------------------
    # snapshots -- OUTPUT / IMPORT (Brick 9)
    # ------------------------------------------------------------------

    def export_snapshot(
        self, destination: str | Path | None = None
    ) -> SnapshotResult:
        """OUTPUT. Deterministic portable snapshot of the project (Brick 9).

        Delegates to :meth:`Project.export_snapshot`. ``None`` returns
        the archive bytes inside the result without writing a file.
        Read-only against canonical state.
        """
        return self._project.export_snapshot(destination)

    @staticmethod
    def import_snapshot(
        archive: str | Path | bytes, destination: str | Path
    ) -> Project:
        """IMPORT. Restore a project from a snapshot archive (Brick 9).

        Static because it does not operate on the bound project: it
        validates the whole archive before writing anything and refuses
        non-empty or conflicting destinations. Delegates to the Brick 9
        importer; all archive validation and path safety stay there.
        """
        return import_snapshot(archive, destination)
