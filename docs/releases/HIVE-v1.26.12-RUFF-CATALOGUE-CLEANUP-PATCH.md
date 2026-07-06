# HIVE v1.26.12 Ruff catalogue cleanup patch

Date: 22 June 2026

## Issue

GitHub Actions backend tests passed, but Ruff failed with F841 because `_generated_skill_description()` assigned `title` and never used it.

## Fix

Removed the unused local variable from `backend/app/services/catalogue_metadata.py`.

## Validation

- `PYTHONPATH=backend python -m pytest backend/tests -q` -> 186 passed locally.
- Ruff was not installed in the local sandbox, but the logged F841 violation is removed by this patch.

## Build marker

No version bump. This is a lint-only cleanup for the existing `v1.26.12-catalogue-metadata` build.
