from pathlib import Path
import zipfile

import pytest

from app.ingestion.zip_ingestion import UnsafeZipError, inspect_zip, normalise_zip_member_name


def test_rejects_zip_path_traversal() -> None:
    with pytest.raises(UnsafeZipError):
        normalise_zip_member_name("../secrets.env")


def test_inspect_zip_counts_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("folder/a.txt", "hello")
        archive.writestr("folder/b.txt", "world")

    members = inspect_zip(archive_path, max_files=10, max_uncompressed_bytes=100)
    assert len(members) == 2
    assert members[0].filename == "folder/a.txt"
