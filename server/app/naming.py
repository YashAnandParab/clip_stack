"""
Turning a title into a safe path.

Two jobs: make names legal on every OS the vault might be synced to, and make
absolutely sure a template can never write outside the vault.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ILLEGAL = re.compile(r'[\\/:*?"<>|]')
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
WINDOWS_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE
)
MAX_SEGMENT = 180


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:100]


def sanitize_segment(name: str, fallback: str = "untitled") -> str:
    """Make one path segment safe. Never returns something with a separator."""
    out = ILLEGAL.sub("-", str(name or ""))
    out = CONTROL.sub("", out)
    out = re.sub(r"\s+", " ", out).strip(" .")

    if WINDOWS_RESERVED.match(out):
        out = "_" + out
    if not out:
        return fallback
    return out[:MAX_SEGMENT].rstrip()


def sanitize_relative_path(path: str) -> Path:
    """
    Clean a relative folder path. Drops empty segments, '.', and '..' so a
    template can never climb above the vault root.
    """
    parts = [
        sanitize_segment(seg, fallback="")
        for seg in re.split(r"[\\/]+", str(path or ""))
        if seg.strip() not in ("", ".", "..")
    ]
    return Path(*[p for p in parts if p]) if any(parts) else Path()


def resolve_in_vault(vault: Path, subfolder: str, filename: str) -> Path:
    """
    Build the final absolute path and assert it really is inside the vault.
    Belt and braces: the segments are already sanitised, but a symlinked
    subfolder could still point outside, so we check the resolved path too.
    """
    vault_root = vault.resolve()
    stem = sanitize_segment(str(filename).removesuffix(".md"), fallback="clipped-note")
    target = (vault_root / sanitize_relative_path(subfolder) / f"{stem}.md").resolve()

    if vault_root not in target.parents:
        raise ValueError("Refusing to write outside the vault")
    return target


def apply_conflict_policy(path: Path, policy: str) -> Path:
    """
    'overwrite'  -> return as-is
    'skip'       -> caller checks .exists() and bails
    'uniquify'   -> 'note.md' becomes 'note 2.md', 'note 3.md', ...
    """
    if policy in ("overwrite", "skip") or not path.exists():
        return path

    stem, parent = path.stem, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem} {n}.md"
        if not candidate.exists():
            return candidate
        n += 1
