from app.core.version import BUILD_STAGE
from app.services.repo_hygiene import repo_hygiene_report


def test_build_stage_is_v113():
    assert BUILD_STAGE == "v1.19-controlled-execution-preview"


def test_repo_hygiene_report_detects_orphan_and_duplicates(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "one.txt").write_text("same", encoding="utf-8")
    (tmp_path / "b" / "two.txt").write_text("same", encoding="utf-8")
    (tmp_path / "b" / "stale.pyc").write_bytes(b"compiled")
    (tmp_path / "HIVE-main-old-patches.zip").write_bytes(b"zip-ish")

    report = repo_hygiene_report(repo_root=tmp_path, include_hashes=True)

    assert report["ok"] is True
    assert report["duplicate_content_group_count"] == 1
    assert report["orphan_candidate_count"] == 1
    assert report["generated_artifact_count"] == 1
    manifest = report["deletion_manifest"]
    assert manifest["dry_run"] is True
    assert "b/stale.pyc" in manifest["recommended_delete_paths"]
    assert "HIVE-main-old-patches.zip" in manifest["recommended_delete_paths"]


def test_repo_hygiene_report_is_read_only(tmp_path):
    target = tmp_path / "keep.pyc"
    target.write_bytes(b"compiled")

    report = repo_hygiene_report(repo_root=tmp_path)

    assert report["deletion_manifest"]["recommended_delete_count"] == 1
    assert target.exists()
