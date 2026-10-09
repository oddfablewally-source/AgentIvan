"""
AgentIvan product package.

AgentIvan is the private OddFable AI Build Pack Generator. The canonical
Build Pack -- the structured representation of the current project
state and the source of truth for the compiled project -- lives here.

Brick 1 establishes the data model only: information statuses,
provenance, revisions, contradictions, validation, and JSON
serialization.

Brick 2 adds the cross-platform project workspace and source intake
layer: relocatable projects with stable identities, safe ingestion of
raw source material (text, files, images, references) with provenance
metadata and content hashing, and an active-project concept. No AI
ingestion, no UI, no PDF generation yet.

Brick 3 adds the deterministic source catalog + retrieval layer: a
stateless view over a project's source records with stable ordering,
verbatim content retrieval, metadata search/filter, integrity checking,
explicit removal, and rebuild-from-disk. Still no AI.

Brick 4 adds the source ingestion + structured extraction layer: a
deterministic, read-only extractor that turns raw sources into
structured candidate information (measurements, status markers, URLs,
labeled declarations, Markdown structure, JSON fields) with full
provenance. Extraction never modifies sources or the Build Pack; an
LLM extractor exists only as an unimplemented extension point.
"""

from __future__ import annotations

from .catalog import (
    CatalogError,
    IntegrityStatus,
    SourceCatalog,
    SourceIntegrity,
    catalog_order_key,
)
from .extraction import (
    EXTRACTIONS_FORMAT_NAME,
    EXTRACTIONS_FORMAT_VERSION,
    CandidateCategory,
    ExtractedItem,
    ExtractionError,
    ExtractionMethod,
    ExtractionNotAvailableError,
    ExtractionOutcome,
    ExtractionResult,
    ExtractionSet,
    ExtractionStore,
    ReviewState,
    SourceLocation,
    TextChunk,
    chunk_json,
    chunk_text,
    make_extraction_id,
)
from .extractors import (
    EXTRACTOR_NAMES,
    DeterministicExtractor,
    Extractor,
    LLMExtractor,
    get_extractor,
)
from .intake import (
    IntakeError,
    identity_hash_for_bytes,
    identity_hash_for_text,
    intake_file,
    intake_reference,
    intake_text,
    normalize_newlines,
    sha256_hex,
    utc_now_iso,
)
from .model import (
    FORMAT_NAME,
    FORMAT_VERSION,
    SECTION_SCHEMAS,
    BuildPack,
    BuildPackError,
    BuildRecord,
    Contradiction,
    ProjectIdentity,
    Provenance,
    Revision,
)
from .paths import (
    UnsafePathError,
    join_posix,
    sanitize_filename,
    validate_supplied_path,
)
from .project import (
    PROJECT_FORMAT_NAME,
    PROJECT_FORMAT_VERSION,
    Project,
    ProjectError,
)
from .reconciliation import (
    ACCEPT_INCOMING,
    BUILDPACK_FILENAME,
    CATEGORY_SECTION_MAP,
    KEEP_EXISTING,
    RECONCILIATION_FILENAME,
    RECONCILIATION_FORMAT_NAME,
    RECONCILIATION_FORMAT_VERSION,
    RESOLUTION_CHOICES,
    BuildPackStore,
    ReconciledCandidate,
    Reconciliation,
    ReconciliationContradiction,
    ReconciliationError,
    ReconciliationSession,
    ReconciliationState,
    ReconciliationStore,
    build_record_fields,
    candidate_provenance,
    packs_equivalent,
)
from .sources import (
    SourceKind,
    SourceRecord,
    SourceRecordError,
    SourceStatus,
)
from .query import (
    BuildPackQuery,
    ChangeEntry,
    ChangeSet,
    ContradictionDetail,
    KnowledgeReport,
    ProvenanceHit,
    QueryError,
    RecordHit,
    RemainingWork,
    diff_packs,
)
from .markdown import (
    MARKDOWN_FORMAT_NAME,
    MARKDOWN_FORMAT_VERSION,
    DEFAULT_EXPORT_FILENAME,
    BuildPackMarkdownExporter,
    MarkdownDocument,
    MarkdownExportError,
    export_project_markdown,
)
from .pdf import (
    PDF_FORMAT_NAME,
    PDF_FORMAT_VERSION,
    DEFAULT_PDF_FILENAME,
    BuildPackPDFExporter,
    PDFDocument,
    PDFExportError,
    export_project_pdf,
)
from .statuses import InfoStatus, ResolutionStatus
from .service import (
    ProjectService,
    ProjectServiceError,
)
from .snapshot import (
    SNAPSHOT_FORMAT_NAME,
    SNAPSHOT_FORMAT_VERSION,
    MANIFEST_NAME,
    ProjectSnapshotExporter,
    ProjectSnapshotImporter,
    SnapshotError,
    SnapshotResult,
    export_snapshot,
    import_snapshot,
)
from .workspace import (
    WORKSPACE_FORMAT_NAME,
    WORKSPACE_FORMAT_VERSION,
    AgentIvanWorkspace,
    WorkspaceError,
    default_workspace_root,
)

__all__ = [
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "SECTION_SCHEMAS",
    "PROJECT_FORMAT_NAME",
    "PROJECT_FORMAT_VERSION",
    "WORKSPACE_FORMAT_NAME",
    "WORKSPACE_FORMAT_VERSION",
    "AgentIvanWorkspace",
    "ACCEPT_INCOMING",
    "BUILDPACK_FILENAME",
    "BuildPack",
    "BuildPackError",
    "BuildPackMarkdownExporter",
    "BuildPackPDFExporter",
    "BuildPackQuery",
    "BuildPackStore",
    "BuildRecord",
    "CATEGORY_SECTION_MAP",
    "CatalogError",
    "CandidateCategory",
    "ChangeEntry",
    "ChangeSet",
    "Contradiction",
    "ContradictionDetail",
    "DEFAULT_EXPORT_FILENAME",
    "DeterministicExtractor",
    "ExtractedItem",
    "ExtractionError",
    "ExtractionMethod",
    "ExtractionNotAvailableError",
    "ExtractionOutcome",
    "ExtractionResult",
    "ExtractionSet",
    "ExtractionStore",
    "Extractor",
    "EXTRACTIONS_FORMAT_NAME",
    "EXTRACTIONS_FORMAT_VERSION",
    "EXTRACTOR_NAMES",
    "InfoStatus",
    "IntakeError",
    "IntegrityStatus",
    "KEEP_EXISTING",
    "KnowledgeReport",
    "LLMExtractor",
    "MARKDOWN_FORMAT_NAME",
    "MARKDOWN_FORMAT_VERSION",
    "MARKDOWN_FORMAT_NAME",
    "MARKDOWN_FORMAT_VERSION",
    "MANIFEST_NAME",
    "MarkdownDocument",
    "MarkdownExportError",
    "PDFDocument",
    "PDFExportError",
    "PDF_FORMAT_NAME",
    "PDF_FORMAT_VERSION",
    "DEFAULT_PDF_FILENAME",
    "Project",
    "ProjectError",
    "ProjectIdentity",
    "ProjectService",
    "ProjectServiceError",
    "ProjectSnapshotExporter",
    "ProjectSnapshotImporter",
    "Provenance",
    "ProvenanceHit",
    "QueryError",
    "RECONCILIATION_FILENAME",
    "RECONCILIATION_FORMAT_NAME",
    "RECONCILIATION_FORMAT_VERSION",
    "RESOLUTION_CHOICES",
    "ReconciledCandidate",
    "Reconciliation",
    "ReconciliationContradiction",
    "ReconciliationError",
    "ReconciliationSession",
    "ReconciliationState",
    "ReconciliationStore",
    "RecordHit",
    "RemainingWork",
    "ResolutionStatus",
    "Revision",
    "ReviewState",
    "SNAPSHOT_FORMAT_NAME",
    "SNAPSHOT_FORMAT_VERSION",
    "SnapshotError",
    "SnapshotResult",
    "SourceKind",
    "SourceRecord",
    "SourceCatalog",
    "SourceRecordError",
    "SourceStatus",
    "SourceIntegrity",
    "SourceLocation",
    "UnsafePathError",
    "WorkspaceError",
    "build_record_fields",
    "candidate_provenance",
    "diff_packs",
    "catalog_order_key",
    "chunk_json",
    "chunk_text",
    "default_workspace_root",
    "export_project_markdown",
    "export_project_pdf",
    "export_snapshot",
    "get_extractor",
    "identity_hash_for_bytes",
    "identity_hash_for_text",
    "import_snapshot",
    "intake_file",
    "intake_reference",
    "intake_text",
    "join_posix",
    "make_extraction_id",
    "packs_equivalent",
    "normalize_newlines",
    "sanitize_filename",
    "sha256_hex",
    "TextChunk",
    "utc_now_iso",
    "validate_supplied_path",
]
