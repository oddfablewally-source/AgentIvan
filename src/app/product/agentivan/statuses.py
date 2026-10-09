"""
Controlled information statuses for the AgentIvan Build Pack.

These are part of the data model, not decorative labels. Every project
record carries exactly one of these, and the model never silently
converts one into another:

- KNOWN:      a directly established fact (e.g. donor identity from title).
- RESEARCHED: a manufacturer specification or finding obtained from
              documentation or research sources.
- PROPOSED:   a design idea or suggestion; not yet decided or measured.
- MEASURED:   a field measurement taken from the physical project.
- CALCULATED: a mathematical result derived from recorded inputs.
- VERIFIED:   a result confirmed through testing or inspection.
- UNKNOWN:    a missing value. Legitimate project state, never an error.

ResolutionStatus tracks whether a recorded Contradiction has been
addressed. Contradictions are never auto-resolved by the model.
"""

from __future__ import annotations

from enum import Enum


class InfoStatus(str, Enum):
    """The seven controlled information states of a Build Pack record."""

    KNOWN = "KNOWN"
    RESEARCHED = "RESEARCHED"
    PROPOSED = "PROPOSED"
    MEASURED = "MEASURED"
    CALCULATED = "CALCULATED"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


class ResolutionStatus(str, Enum):
    """Whether a recorded contradiction has been resolved."""

    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
