"""Deployment copy/paste templates must not restore superseded model defaults."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLES = {"default_model", "cheap_model", "balanced_model", "code_model", "audit_model", "premium_model"}


def test_shared_production_and_example_models_match_settings_defaults():
    tree = ast.parse((ROOT / "backend/app/core/config.py").read_text())
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    defaults = {
        node.target.id.upper(): ast.literal_eval(node.value)
        for node in settings.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in ROLES
    }
    for filename in [".env.example", "HIVE-PRODUCTION-SHARED.env"]:
        entries = {}
        for line in (ROOT / filename).read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name in defaults:
                entries[name] = value
        assert entries == defaults, f"Model assignments drifted in {filename}"
