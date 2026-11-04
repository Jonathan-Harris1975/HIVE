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
DEFAULT_RECOVERY_D1_DATABASES: dict[str, dict[str, str]] = {
    'HIVE': {
        'name': 'hive-autonomous-recovery',
        'id': 'bf78bd7a-e224-465a-a04e-443822204980'
    },
    'HIVE-UI': {
        'name': 'hive-ui-autonomous-recovery',
        'id': 'd4c2f00c-0470-42ed-8b41-20ce8b6bb358'
    },
    'AIMS': {
        'name': 'aims-autonomous-recovery',
        'id': 'd2f0496c-6ce0-4761-8432-4809ea8253b7'
    },
    'AIMS-UI': {
        'name': 'aims-ui-autonomous-recovery',
        'id': '8823fb35-aaf3-4eb5-94d5-2484ebb7bbe2'
    },
    'RAMS': {
        'name': 'rams-autonomous-recovery',
        'id': '759c49e9-0d72-4967-8a32-1e8b6dd6a1f8'
    },
    'MAST': {
        'name': 'mast-autonomous-recovery',
        'id': '5fe29644-ed20-4174-b9da-895c4d61d267'
    },
    'IRS': {
        'name': 'irs-autonomous-recovery',
        'id': '9058b9af-f520-4c50-9714-feb6f4255211'
    },
    'Website': {
        'name': 'jonathan-harris-website-autonomous-recovery',
        'id': '634ed79f-1042-4607-b9b9-ef1bdf5be893'
    }
}

DEFAULT_GITHUB_SOURCES_JSON = json.dumps(DEFAULT_GITHUB_SOURCES, separators=(",", ":"))


def canonical_governed_repository_id(value: str) -> str | None:
    normalized = value.strip().lower()
    alias = GOVERNED_REPOSITORY_ALIASES.get(normalized)
    if alias:
        return alias
    return next(
        (repo_id for repo_id, source in DEFAULT_GITHUB_SOURCES.items()
         if source.lower() == normalized),
        None,
    )
