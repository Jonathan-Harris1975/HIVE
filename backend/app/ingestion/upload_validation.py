from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException, status

_ALLOWED_MIME_PREFIXES: tuple[str, ...] = (
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
)

# application/octet-stream is accepted only after filename/signature checks.
_GENERIC_BINARY_MIME = "application/octet-stream"

_DANGEROUS_BINARY_SUFFIXES = {
    ".app",
    ".com",
    ".cpl",
    ".dll",
    ".dmg",
    ".drv",
    ".exe",
    ".iso",
    ".msi",
    ".msp",
    ".scr",
    ".sys",
}

# These extensions are intentionally conservative. Source-code/script formats
# that HIVE indexes as text remain valid because their bytes must also look like
# text; native executable/container formats are never accepted as generic data.
_SAFE_OCTET_STREAM_SUFFIXES = {
    ".adoc", ".astro", ".bash", ".c", ".cfg", ".conf", ".cpp", ".cs",
    ".css", ".csv", ".docx", ".fs", ".go", ".graphql", ".h", ".hcl",
    ".hpp", ".html", ".ini", ".ipynb", ".java", ".json", ".jsonl",
    ".jsx", ".kt", ".kts", ".less", ".lock", ".log", ".lua", ".md",
    ".mdx", ".pdf", ".php", ".pl", ".properties", ".proto", ".py",
    ".r", ".rb", ".rs", ".rst", ".sass", ".scala", ".scss", ".sh",
    ".sql", ".svelte", ".svg", ".swift", ".tf", ".tfvars", ".toml",
    ".ts", ".tsv", ".tsx", ".txt", ".vue", ".xml", ".xlsx", ".yaml",
    ".yml", ".zip",
}

_TEXT_FILENAMES = {
    ".dockerignore",
    ".editorconfig",
    ".gitignore",
    ".npmrc",
    ".nvmrc",
    "dockerfile",
    "license",
    "makefile",
    "procfile",
    "readme",
    "requirements",
}


def _normalise_mime(content_type: str | None) -> str:
    return (content_type or _GENERIC_BINARY_MIME).split(";", 1)[0].strip().lower()


def _looks_like_native_executable(data: bytes) -> bool:
    head = data[:8]
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF"):
        return True
    # Mach-O / fat binary magics in both endiannesses.
    return head[:4] in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _expected_mime_family(suffix: str) -> tuple[str, ...] | None:
    mapping: dict[str, tuple[str, ...]] = {
        ".pdf": ("application/pdf",),
        ".zip": ("application/zip", "application/x-zip-compressed", "multipart/x-zip"),
        ".png": ("image/png",),
        ".jpg": ("image/jpeg",),
        ".jpeg": ("image/jpeg",),
        ".gif": ("image/gif",),
        ".webp": ("image/webp",),
        ".svg": ("image/svg+xml", "text/"),
        ".json": ("application/json", "text/"),
        ".xml": ("application/xml", "text/"),
        ".csv": ("application/csv", "text/"),
        ".yaml": ("application/x-yaml", "text/"),
        ".yml": ("application/x-yaml", "text/"),
        ".docx": ("application/vnd.openxmlformats-officedocument",),
        ".xlsx": ("application/vnd.openxmlformats-officedocument",),
    }
    return mapping.get(suffix)


def validate_upload_content(
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> str:
    """Validate one upload before any object or metadata persistence.

    The policy is shared by multipart, Base64 and text ingestion. It rejects
    executable signatures regardless of caller-supplied MIME, rejects known
    executable extensions, and treats application/octet-stream as a generic
    fallback only for recognised safe document/source/archive filenames.
    """

    name = Path(filename or "upload.bin").name
    suffix = Path(name).suffix.lower()
    mime = _normalise_mime(content_type)

    if suffix in _DANGEROUS_BINARY_SUFFIXES or _looks_like_native_executable(data):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Executable or native-binary uploads are not accepted.",
        )

    if mime == _GENERIC_BINARY_MIME:
        if suffix not in _SAFE_OCTET_STREAM_SUFFIXES and name.lower() not in _TEXT_FILENAMES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    "application/octet-stream is accepted only for recognised safe "
                    "document, source, image, or archive filenames."
                ),
            )
    elif not any(mime.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported upload media type: '{mime}'. "
                "Only document, text, image, and archive types are accepted."
            ),
        )

    expected = _expected_mime_family(suffix)
    if expected is not None and mime != _GENERIC_BINARY_MIME:
        if not any(mime.startswith(prefix) for prefix in expected):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Upload media type '{mime}' does not match filename '{name}'.",
            )

    # Text/source formats should not conceal opaque binary payloads when the
    # caller supplies a text-like MIME. ZIP/PDF/Office/image formats are binary.
    guessed = mimetypes.guess_type(name)[0] or ""
    text_like = (
        mime.startswith("text/")
        or mime in {"application/json", "application/xml", "application/csv", "application/x-yaml"}
        or guessed.startswith("text/")
    )
    if text_like and not _is_probably_text(data):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload content does not match the declared text/document media type.",
        )

    return mime
