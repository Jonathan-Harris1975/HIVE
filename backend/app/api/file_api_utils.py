from __future__ import annotations

import base64
from pathlib import Path

from fastapi import HTTPException, status

# Upload media allow-list shared by multipart and base64 upload paths.
# application/octet-stream is retained for browsers/clients that cannot provide a more
# specific type; extension and archive inspection still provide defence in depth.
_ALLOWED_UPLOAD_MIME_PREFIXES: frozenset[str] = frozenset({
    "text/",
    "application/json",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.ms-word",
    "application/msword",
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip",
    "application/xml",
    "application/csv",
    "application/x-yaml",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
})
_ALLOWED_UPLOAD_MIME_EXACT: frozenset[str] = frozenset({"application/octet-stream"})

def _validate_upload_content_type(content_type: str | None) -> None:
    """Raise HTTP 415 if the upload Content-Type is not in the allow-list.

    This guard prevents users from uploading executable/binary files whose
    presence on R2 or in the ingestion pipeline could cause harm. It is a
    defence-in-depth measure layered on top of extension filtering and zip
    inspection — not a replacement for them.
    """
    ct = (content_type or "application/octet-stream").strip().split(";")[0].strip().lower()
    if ct in _ALLOWED_UPLOAD_MIME_EXACT:
        return
    for prefix in _ALLOWED_UPLOAD_MIME_PREFIXES:
        if ct.startswith(prefix):
            return
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=(
            f"Unsupported upload media type: '{ct}'. "
            "Only document, text, image, and archive types are accepted."
        ),
    )

def _batches(items: list[dict[str, object]], batch_size: int) -> list[list[dict[str, object]]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]

def _text_preview_supported(key: str, content_type: str | None) -> bool:
    suffix = Path(key).suffix.lower()
    supported_suffixes = {
        ".adoc",
        ".astro",
        ".bash",
        ".bat",
        ".c",
        ".cfg",
        ".cmd",
        ".conf",
        ".cpp",
        ".cs",
        ".css",
        ".csv",
        ".cxx",
        ".docx",
        ".env",
        ".fish",
        ".fs",
        ".fsx",
        ".go",
        ".gradle",
        ".graphql",
        ".gql",
        ".h",
        ".hcl",
        ".hpp",
        ".html",
        ".htm",
        ".ini",
        ".ipynb",
        ".java",
        ".js",
        ".json",
        ".jsonc",
        ".jsonl",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lock",
        ".log",
        ".lua",
        ".mjs",
        ".md",
        ".mdx",
        ".pdf",
        ".php",
        ".pl",
        ".pm",
        ".properties",
        ".proto",
        ".ps1",
        ".py",
        ".r",
        ".rb",
        ".rs",
        ".rss",
        ".rst",
        ".sass",
        ".sbt",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".svg",
        ".swift",
        ".tf",
        ".tfvars",
        ".toml",
        ".ts",
        ".tsv",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".xlsx",
        ".yaml",
        ".yml",
        ".zsh",
    }
    supported_filenames = {
        ".dockerignore",
        ".editorconfig",
        ".env",
        ".env.example",
        ".env.local",
        ".eslintignore",
        ".eslintrc",
        ".gitattributes",
        ".gitignore",
        ".npmrc",
        ".nvmrc",
        ".prettierignore",
        ".prettierrc",
        ".python-version",
        ".ruby-version",
        "dockerfile",
        "license",
        "makefile",
        "procfile",
        "readme",
        "requirements",
    }
    if suffix in supported_suffixes or Path(key).name.lower() in supported_filenames:
        return True
    media_type = (content_type or "").lower()
    return media_type.startswith("text/") or any(
        token in media_type
        for token in ["json", "xml", "csv", "pdf", "wordprocessingml", "spreadsheetml"]
    )

def _normalise_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return _validate_object_key(prefix, allow_trailing_slash=True)

def _validate_object_key(key: str, *, allow_trailing_slash: bool = False) -> str:
    clean_key = key.strip().lstrip("/")
    if not clean_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Object key is required"
        )
    parts = [part for part in clean_key.split("/") if part]
    if any(part in {".", ".."} for part in parts) or "\x00" in clean_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid object key")
    if not allow_trailing_slash and clean_key.endswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Object key must point to a file"
        )
    return clean_key

def _reply_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)

def _decode_base64_upload(
    content_base64: str, content_type: str | None, *, max_decoded_bytes: int | None = None
) -> tuple[str | None, bytes]:
    raw = content_base64.strip()
    detected_content_type = content_type
    if raw.startswith("data:") and "," in raw:
        header, raw = raw.split(",", 1)
        if ";base64" in header and not detected_content_type:
            detected_content_type = header.removeprefix("data:").split(";", 1)[0] or None

    if max_decoded_bytes is not None:
        maximum = max(0, int(max_decoded_bytes))
        max_encoded_chars = 4 * ((maximum + 2) // 3)
        if len(raw) > max_encoded_chars:
            raise ValueError(f"Upload exceeds max size of {maximum} bytes")

    decoded = base64.b64decode(raw, validate=True)
    if max_decoded_bytes is not None and len(decoded) > max(0, int(max_decoded_bytes)):
        raise ValueError(f"Upload exceeds max size of {max(0, int(max_decoded_bytes))} bytes")
    return detected_content_type, decoded

def _decode_text(content: bytes) -> tuple[str, bool]:
    decoded = content.decode("utf-8", errors="replace")
    return decoded, "\ufffd" in decoded

def _storage_error_hint(message: str) -> str:
    lowered = message.lower()
    if "accessdenied" in lowered or "forbidden" in lowered or "403" in lowered:
        return "R2 credentials are valid enough to reach R2, but this operation is not allowed. Check bucket permissions for list/read/write."
    if "nosuchbucket" in lowered or ("not found" in lowered and "bucket" in lowered):
        return "R2 bucket was not found. Check R2_BUCKET_UPLOADS/R2_BUCKET and account endpoint."
    if "nosuchkey" in lowered or "404" in lowered:
        return "Object key was not found. Use /v1/files/list to copy the exact key."
    if "timed out" in lowered or "timeout" in lowered:
        return "R2 operation timed out. Retry, then increase R2_READ_TIMEOUT_SECONDS if needed."
    if "signature" in lowered or "invalidaccesskeyid" in lowered:
        return "R2 signature/auth failed. Check R2 access key, secret key, endpoint and region."
    return "Check the R2 bucket name, endpoint URL, key permissions and exact object key."

