from __future__ import annotations

import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from app.ingestion.text_extractors import SUPPORTED_TEXT_SUFFIXES, extract_text_with_metadata


class UnsafeZipError(ValueError):
    pass


@dataclass(frozen=True)
class ZipMember:
    filename: str
    size: int
    compressed_size: int
    is_dir: bool


@dataclass(frozen=True)
class ZipExtractItem:
    filename: str
    size: int
    depth: int
    suffix: str
    chars: int
    extractor: str
    truncated: bool
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ZipSkippedItem:
    filename: str
    size: int
    depth: int
    reason: str


def normalise_zip_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeZipError(f"Unsafe ZIP path: {name}")
    safe = str(path).lstrip("/")
    if not safe or safe.startswith("../"):
        raise UnsafeZipError(f"Unsafe ZIP path: {name}")
    return safe


def inspect_zip(path: Path, max_files: int, max_uncompressed_bytes: int) -> list[ZipMember]:
    members: list[ZipMember] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > max_files:
            raise UnsafeZipError(f"ZIP has {len(infos)} files, limit is {max_files}")
        for info in infos:
            safe_name = normalise_zip_member_name(info.filename)
            total += info.file_size
            if total > max_uncompressed_bytes:
                raise UnsafeZipError("ZIP uncompressed size exceeds limit")
            members.append(
                ZipMember(
                    filename=safe_name,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    is_dir=info.is_dir(),
                )
            )
    return members


def extract_zip_safely(path: Path, destination: Path, max_files: int, max_uncompressed_bytes: int) -> list[ZipMember]:
    members = inspect_zip(path, max_files=max_files, max_uncompressed_bytes=max_uncompressed_bytes)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for member in members:
            if member.is_dir:
                continue
            target = destination / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member.filename) as source, target.open("wb") as out:
                out.write(source.read())
    return members


def extract_text_from_zip(
    path: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
    max_members: int,
    max_member_bytes: int,
    max_total_text_chars: int,
    max_depth: int,
    supported_suffixes: set[str] | None = None,
    supported_filenames: set[str] | None = None,
) -> dict[str, object]:
    """Extract bounded text from a ZIP, including nested ZIPs when enabled.

    Designed for Koyeb free-tier safety: every major dimension is bounded and
    skipped members are reported rather than silently ignored.
    """

    suffixes = {item.lower() for item in (supported_suffixes or SUPPORTED_TEXT_SUFFIXES)}
    filenames = {item.lower() for item in (supported_filenames or set())}
    items: list[ZipExtractItem] = []
    skipped: list[ZipSkippedItem] = []
    text_parts: list[str] = []
    state = {"members_seen": 0, "text_chars": 0, "nested_archives": 0, "truncated": False}

    with tempfile.TemporaryDirectory(prefix="hive-zip-extract-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        _extract_zip_recursive(
            path=path,
            prefix="",
            depth=0,
            tmp_root=tmp_root,
            max_files=max_files,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_members=max_members,
            max_member_bytes=max_member_bytes,
            max_total_text_chars=max_total_text_chars,
            max_depth=max_depth,
            supported_suffixes=suffixes,
            supported_filenames=filenames,
            text_parts=text_parts,
            items=items,
            skipped=skipped,
            state=state,
        )

    combined_text = "\n\n".join(text_parts)
    if len(combined_text) > max_total_text_chars:
        combined_text = combined_text[:max_total_text_chars]
        state["truncated"] = True

    return {
        "ok": True,
        "text": combined_text,
        "text_chars": len(combined_text),
        "truncated": bool(state["truncated"]),
        "limits": {
            "max_files": max_files,
            "max_uncompressed_bytes": max_uncompressed_bytes,
            "max_members": max_members,
            "max_member_bytes": max_member_bytes,
            "max_total_text_chars": max_total_text_chars,
            "max_depth": max_depth,
        },
        "summary": {
            "members_seen": state["members_seen"],
            "extracted_count": len(items),
            "skipped_count": len(skipped),
            "nested_archives": state["nested_archives"],
        },
        "items": [asdict(item) for item in items],
        "skipped": [asdict(item) for item in skipped],
    }


def _extract_zip_recursive(
    *,
    path: Path,
    prefix: str,
    depth: int,
    tmp_root: Path,
    max_files: int,
    max_uncompressed_bytes: int,
    max_members: int,
    max_member_bytes: int,
    max_total_text_chars: int,
    max_depth: int,
    supported_suffixes: set[str],
    supported_filenames: set[str],
    text_parts: list[str],
    items: list[ZipExtractItem],
    skipped: list[ZipSkippedItem],
    state: dict[str, object],
) -> None:
    members = inspect_zip(path, max_files=max_files, max_uncompressed_bytes=max_uncompressed_bytes)
    with zipfile.ZipFile(path) as archive:
        for member in members:
            if member.is_dir:
                continue
            if int(state["members_seen"]) >= max_members:
                state["truncated"] = True
                skipped.append(ZipSkippedItem(filename=_join_zip_path(prefix, member.filename), size=member.size, depth=depth, reason="member_limit_reached"))
                continue
            state["members_seen"] = int(state["members_seen"]) + 1
            full_name = _join_zip_path(prefix, member.filename)
            suffix = Path(member.filename).suffix.lower()

            if member.size > max_member_bytes:
                skipped.append(ZipSkippedItem(filename=full_name, size=member.size, depth=depth, reason="member_too_large"))
                continue

            if suffix == ".zip":
                if depth >= max_depth:
                    skipped.append(ZipSkippedItem(filename=full_name, size=member.size, depth=depth, reason="nested_zip_depth_limit"))
                    continue
                nested_path = tmp_root / f"nested-{len(items)}-{Path(member.filename).name}"
                nested_path.write_bytes(archive.read(member.filename))
                state["nested_archives"] = int(state["nested_archives"]) + 1
                _extract_zip_recursive(
                    path=nested_path,
                    prefix=full_name,
                    depth=depth + 1,
                    tmp_root=tmp_root,
                    max_files=max_files,
                    max_uncompressed_bytes=max_uncompressed_bytes,
                    max_members=max_members,
                    max_member_bytes=max_member_bytes,
                    max_total_text_chars=max_total_text_chars,
                    max_depth=max_depth,
                    supported_suffixes=supported_suffixes,
                    supported_filenames=supported_filenames,
                    text_parts=text_parts,
                    items=items,
                    skipped=skipped,
                    state=state,
                )
                continue

            filename = Path(member.filename).name.lower()
            if suffix not in supported_suffixes and filename not in supported_filenames:
                skipped.append(
                    ZipSkippedItem(
                        filename=full_name,
                        size=member.size,
                        depth=depth,
                        reason="unsupported_suffix_or_filename",
                    )
                )
                continue

            temp_path = tmp_root / f"member-{len(items)}{suffix or '.txt'}"
            temp_path.write_bytes(archive.read(member.filename))
            remaining_chars = max_total_text_chars - int(state["text_chars"])
            if remaining_chars <= 0:
                state["truncated"] = True
                skipped.append(ZipSkippedItem(filename=full_name, size=member.size, depth=depth, reason="text_char_limit_reached"))
                continue
            extracted = extract_text_with_metadata(temp_path, max_chars=remaining_chars)
            if not extracted.text:
                skipped.append(ZipSkippedItem(filename=full_name, size=member.size, depth=depth, reason="no_extractable_text"))
                continue
            header = f"# ZIP member: {full_name}\n"
            text_parts.append(header + extracted.text)
            state["text_chars"] = int(state["text_chars"]) + len(header) + len(extracted.text)
            if extracted.truncated or int(state["text_chars"]) >= max_total_text_chars:
                state["truncated"] = True
            items.append(
                ZipExtractItem(
                    filename=full_name,
                    size=member.size,
                    depth=depth,
                    suffix=suffix,
                    chars=len(extracted.text),
                    extractor=extracted.extractor,
                    truncated=extracted.truncated,
                    metadata=extracted.metadata,
                )
            )


def _join_zip_path(prefix: str, filename: str) -> str:
    return f"{prefix}!/{filename}" if prefix else filename
