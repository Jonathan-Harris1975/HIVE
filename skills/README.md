# HIVE repository-local skills

`catalogue_metadata.json` is the authoritative skill catalogue for this repository. Each `HIVE-skNNN` record is an original, local description of behaviour already implemented by the native paths listed in `implementation_paths`.

HIVE does not download skill bodies, import a shared manifest into D1, read a skills bucket, or install third-party skill packages at runtime. The catalogue is bounded, read-only at runtime and released with the application.

When adding or changing a record:

1. Keep the `HIVE-skNNN` identifier stable and unique.
2. Point only to safe repository-relative implementation paths that exist in the same release.
3. Keep descriptions original to HIVE and set `external_content_copied` to `false`.
4. Preserve the normal approval gates; catalogue metadata never grants execution authority.
5. Run the catalogue integrity endpoint and the backend test suite before release.
