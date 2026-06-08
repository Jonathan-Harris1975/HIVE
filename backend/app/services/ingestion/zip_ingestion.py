from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafeZipError(ValueError):
    pass


@dataclass(frozen=True)
class ZipMember:
    filename: str
    size: int
    compressed_size: int
    is_dir: bool


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
