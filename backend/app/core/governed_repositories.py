from __future__ import annotations

import json

GOVERNED_REPOSITORY_IDS: tuple[str, ...] = (
    "HIVE",
    "HIVE-UI",
    "AIMS",
    "AIMS-UI",
    "RAMS",
    "MAST",
    "IRS",
    "Website",
)

GOVERNED_REPOSITORY_ID_SET = frozenset(GOVERNED_REPOSITORY_IDS)

GOVERNED_REPOSITORY_ALIASES: dict[str, str] = {
    "hive": "HIVE",
    "hive-ui": "HIVE-UI",
    "aims": "AIMS",
    "aims-ui": "AIMS-UI",
    "rams": "RAMS",
    "mast": "MAST",
    "irs": "IRS",
    "website": "Website",
    "jonathan-harris-website": "Website",
}

DEFAULT_GITHUB_SOURCES: dict[str, str] = {
    "HIVE": "Jonathan-Harris1975/HIVE",
    "HIVE-UI": "Jonathan-Harris1975/HIVE-UI",
    "AIMS": "Jonathan-Harris1975/AIMS",
    "AIMS-UI": "Jonathan-Harris1975/AIMS-UI",
    "RAMS": "Jonathan-Harris1975/RAMS",
    "MAST": "Jonathan-Harris1975/MAST",
    "IRS": "Jonathan-Harris1975/IRS",
    "Website": "Jonathan-Harris1975/jonathan-harris-website",
}
DEFAULT_GITHUB_SOURCES_JSON = json.dumps(DEFAULT_GITHUB_SOURCES, separators=(",", ":"))


def canonical_governed_repository_id(value: str) -> str | None:
    return GOVERNED_REPOSITORY_ALIASES.get(value.strip().lower())
