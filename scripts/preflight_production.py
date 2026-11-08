"""Read-only governance/recovery checks using the application's production defaults."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_defaults(path: Path) -> None:
    """Match start.sh: literal key/value defaults, with resident overrides winning."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            os.environ.setdefault(name, value)


def main() -> int:
    load_defaults(Path(os.environ.get("HIVE_PRODUCTION_ENV_FILE", str(ROOT / "HIVE-PRODUCTION-SHARED.env"))))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "backend") + os.pathsep + environment.get("PYTHONPATH", "")
    commands = [
        [sys.executable, "-m", "app.monthly_governance_worker", "--previous-month", "--preflight-only"],
        [sys.executable, str(ROOT / "scripts/preflight_recovery_d1.py")],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        if result.returncode:
            return result.returncode
    print(json.dumps({"event": "production.preflight", "status": "verified"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
