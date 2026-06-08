from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.storage.r2 import sha256_file


@dataclass(frozen=True)
class LocalObject:
    key: str
    path: str
    size_bytes: int
    sha256: str


class LocalBlobStorage:
    """Development fallback when R2 credentials are not configured."""

    def __init__(self, root: Path = Path("local-data/uploads")) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, path: Path, key: str, content_type: str | None = None) -> LocalObject:  # noqa: ARG002
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        return LocalObject(
            key=key,
            path=str(destination),
            size_bytes=destination.stat().st_size,
            sha256=sha256_file(destination),
        )
