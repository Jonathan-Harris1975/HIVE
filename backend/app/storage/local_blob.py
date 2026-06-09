from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.storage.r2 import ObjectSummary, ReadObject, sha256_file


@dataclass(frozen=True)
class LocalObject:
    key: str
    path: str
    size_bytes: int
    sha256: str
    public_url: str | None = None


class LocalBlobStorage:
    """Development fallback when R2 credentials are not configured."""

    def __init__(self, root: Path = Path("local-data/uploads")) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, path: Path, key: str, content_type: str | None = None) -> LocalObject:  # noqa: ARG002
        destination = self._path_for_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        return LocalObject(
            key=key,
            path=str(destination),
            size_bytes=destination.stat().st_size,
            sha256=sha256_file(destination),
        )

    def list_objects(self, prefix: str = "", limit: int = 100) -> list[ObjectSummary]:
        safe_limit = max(1, min(int(limit), 1000))
        objects: list[ObjectSummary] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if prefix and not key.startswith(prefix):
                continue
            objects.append(
                ObjectSummary(
                    key=key,
                    size_bytes=path.stat().st_size,
                    last_modified=None,
                    public_url=None,
                )
            )
            if len(objects) >= safe_limit:
                break
        return objects

    def read_object(self, key: str, max_bytes: int) -> ReadObject:
        path = self._path_for_key(key)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Local object not found for key {key}")
        size_bytes = path.stat().st_size
        if size_bytes > max_bytes:
            raise ValueError(f"Object is {size_bytes} bytes; max read size is {max_bytes} bytes")
        content = path.read_bytes()
        return ReadObject(
            key=key,
            bucket="local",
            content=content,
            size_bytes=size_bytes,
            content_type=None,
            public_url=None,
        )

    def _path_for_key(self, key: str) -> Path:
        if not key or key.startswith("/"):
            raise ValueError("Invalid object key")
        root = self.root.resolve()
        path = (self.root / key).resolve()
        if root not in path.parents and path != root:
            raise ValueError("Invalid object key")
        return path
