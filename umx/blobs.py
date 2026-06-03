"""Content-addressed blob store for raw attachments.

Implements the ``local/blobs/`` layer described in the spec: raw binary
attachments (screenshots, rendered diffs, large log excerpts) are stored under
``local/blobs/{sha256-prefix}/{filename}`` where ``sha256-prefix`` is the first
8 hex digits of the SHA-256 of the file contents. Identical contents always
produce the same path, so duplication across sessions is impossible.

Blobs are local-only (``local/`` is gitignored). Facts and handover notes
reference a blob by a ``[blob:<prefix>/<filename>]`` marker; a blob that no
committed memory references is "stale" and can be purged.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SHA_PREFIX_LEN = 8
BLOB_REF_PATTERN = re.compile(r"\[blob:([0-9a-f]{%d})/([^\]\s]+)\]" % SHA_PREFIX_LEN)
_REFERENCE_SUFFIXES = {".md", ".markdown", ".txt", ".jsonl", ".json"}


class BlobError(RuntimeError):
    """Raised when a blob reference is malformed or a blob is missing."""


@dataclass(slots=True, frozen=True)
class BlobRef:
    prefix: str
    filename: str

    @property
    def key(self) -> str:
        return f"{self.prefix}/{self.filename}"

    def path(self, repo_dir: Path) -> Path:
        return blobs_dir(repo_dir) / self.prefix / self.filename


def blobs_dir(repo_dir: Path) -> Path:
    return repo_dir / "local" / "blobs"


def _sha_prefix(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:SHA_PREFIX_LEN]


def _safe_filename(filename: str) -> str:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise BlobError(f"invalid blob filename: {filename!r}")
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise BlobError(f"invalid blob filename: {filename!r}")
    return name


def parse_blob_key(key: str) -> BlobRef:
    """Parse a ``<prefix>/<filename>`` blob key into a BlobRef."""
    prefix, sep, filename = key.partition("/")
    if not sep:
        raise BlobError(f"blob key must be '<prefix>/<filename>': {key!r}")
    if len(prefix) != SHA_PREFIX_LEN or not re.fullmatch(r"[0-9a-f]+", prefix):
        raise BlobError(f"invalid blob hash prefix: {prefix!r}")
    return BlobRef(prefix=prefix, filename=_safe_filename(filename))


def store_blob(
    repo_dir: Path,
    source: Path | bytes,
    *,
    filename: str | None = None,
) -> BlobRef:
    """Store bytes (or a file) by content hash and return its BlobRef.

    Storing identical contents is idempotent — the same path is reused.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if filename is None:
            raise BlobError("filename is required when storing raw bytes")
        name = _safe_filename(filename)
    else:
        data = source.read_bytes()
        name = _safe_filename(filename or source.name)

    ref = BlobRef(prefix=_sha_prefix(data), filename=name)
    target = ref.path(repo_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return ref


def get_blob(repo_dir: Path, key: str) -> bytes:
    """Return blob contents for a ``<prefix>/<filename>`` key."""
    ref = parse_blob_key(key)
    path = ref.path(repo_dir)
    if not path.exists():
        raise BlobError(f"blob not found: {key}")
    return path.read_bytes()


def list_blobs(repo_dir: Path) -> list[BlobRef]:
    """Return all stored blobs, sorted by key."""
    root = blobs_dir(repo_dir)
    if not root.exists():
        return []
    refs: list[BlobRef] = []
    for prefix_dir in root.iterdir():
        if not prefix_dir.is_dir():
            continue
        for blob_path in prefix_dir.iterdir():
            if blob_path.is_file():
                refs.append(BlobRef(prefix=prefix_dir.name, filename=blob_path.name))
    return sorted(refs, key=lambda ref: ref.key)


def referenced_blob_keys(repo_dir: Path) -> set[str]:
    """Scan committed memory text for ``[blob:...]`` references."""
    keys: set[str] = set()
    blob_root = blobs_dir(repo_dir)
    for path in repo_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _REFERENCE_SUFFIXES:
            continue
        if blob_root in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in BLOB_REF_PATTERN.finditer(text):
            keys.add(f"{match.group(1)}/{match.group(2)}")
    return keys


def find_unreferenced_blobs(repo_dir: Path) -> list[BlobRef]:
    """Return stored blobs that no committed memory references."""
    referenced = referenced_blob_keys(repo_dir)
    return [ref for ref in list_blobs(repo_dir) if ref.key not in referenced]


def purge_unreferenced(repo_dir: Path, *, dry_run: bool = False) -> list[BlobRef]:
    """Remove blobs that nothing references. Returns the purged refs."""
    unreferenced = find_unreferenced_blobs(repo_dir)
    if dry_run:
        return unreferenced
    for ref in unreferenced:
        path = ref.path(repo_dir)
        path.unlink(missing_ok=True)
        prefix_dir = path.parent
        if prefix_dir.is_dir() and not any(prefix_dir.iterdir()):
            prefix_dir.rmdir()
    return unreferenced


def stale_blob_summary(repo_dir: Path) -> dict[str, object]:
    """Doctor summary of unreferenced ("stale") blobs."""
    unreferenced = find_unreferenced_blobs(repo_dir)
    return {
        "total": len(list_blobs(repo_dir)),
        "unreferenced_count": len(unreferenced),
        "unreferenced": [ref.key for ref in unreferenced],
    }
