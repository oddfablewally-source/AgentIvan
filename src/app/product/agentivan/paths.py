"""
Cross-platform path safety for AgentIvan (Brick 2).

Every path decision here is made with platform-neutral logic so the
same code is correct on Linux and on Windows:

- No hard-coded OS paths, no shell commands, no os.sep-dependent logic.
- User-supplied names are validated against BOTH POSIX and Windows path
  semantics regardless of the host OS, so a Windows-style attack string
  is rejected even when the code runs on Linux (and vice versa).
- Stored paths inside a project are always recorded POSIX-style
  (forward slashes), which pathlib parses correctly on every platform.

Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

__all__ = [
    "UnsafePathError",
    "validate_supplied_path",
    "sanitize_filename",
    "join_posix",
]


class UnsafePathError(ValueError):
    """Raised when a supplied filename/path is unsafe or invalid."""


# Windows reserved device names (checked case-insensitively on the stem).
_WINDOWS_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _split_segments(name: str) -> list[str]:
    """Split on both separators so neither platform's style slips through."""
    return re.split(r"[\\/]", name)


def validate_supplied_path(name: str) -> str:
    """Validate a user-supplied filename or relative path.

    Returns the normalized POSIX-style relative path (e.g.
    ``"notes.md"`` or ``"photos/img.png"``).

    Raises UnsafePathError when the input:
      - is empty / not a string / contains NUL,
      - is absolute under POSIX or Windows rules,
      - carries a Windows drive letter (``C:...``),
      - is a UNC/network path (``\\\\server\\share``),
      - contains ``..`` traversal in any segment.
    """
    if not isinstance(name, str):
        raise UnsafePathError("path must be a string")
    if "\x00" in name:
        raise UnsafePathError("path contains NUL byte")
    stripped = name.strip()
    if not stripped:
        raise UnsafePathError("path is empty")

    # Absolute by either platform's rules -> reject on every host.
    if PurePosixPath(stripped).is_absolute():
        raise UnsafePathError(f"absolute POSIX path not allowed: {name!r}")
    if PureWindowsPath(stripped).is_absolute():
        raise UnsafePathError(f"absolute Windows path not allowed: {name!r}")
    if PureWindowsPath(stripped).drive:
        raise UnsafePathError(f"drive-letter path not allowed: {name!r}")
    # A leading backslash is a drive-rooted Windows path (\dir\file):
    # it escapes the project directory on Windows even though pathlib
    # does not call it "absolute", so reject it explicitly.
    if stripped.startswith("\\"):
        raise UnsafePathError(f"rooted Windows path not allowed: {name!r}")
    # A leading double backslash is UNC/network-path confusion even when
    # it does not parse as a complete \\server\share path.
    if stripped.startswith("\\\\"):
        raise UnsafePathError(f"UNC/network path not allowed: {name!r}")

    segments = [seg for seg in _split_segments(stripped) if seg not in ("", ".")]
    if not segments:
        raise UnsafePathError(f"path has no usable segments: {name!r}")
    for seg in segments:
        if seg == "..":
            raise UnsafePathError(f"path traversal not allowed: {name!r}")

    return "/".join(segments)


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Make a single path segment safe to store on Linux and Windows.

    Unicode letters are preserved (unicode filenames must keep working);
    only characters that are unsafe or illegal on either platform are
    replaced. Reserved Windows device names are prefixed so the stored
    file is valid if the project is later opened on Windows.
    """
    # Work on the final segment only; any directory structure is flattened.
    segment = _split_segments(name)[-1] if name else ""
    cleaned = _UNSAFE_CHARS.sub("_", segment).strip(" .")
    if not cleaned:
        cleaned = "_"
    stem, dot, ext = cleaned.partition(".")
    if stem.lower() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
        cleaned = stem + (dot + ext if dot else "")
    if len(cleaned) > max_length and dot:
        keep_ext = ext[:10]
        cleaned = cleaned[: max_length - len(keep_ext) - 1] + "." + keep_ext
    elif len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def join_posix(*parts: str) -> str:
    """Join path parts with forward slashes for stored relative paths."""
    return PurePosixPath(*parts).as_posix()
