"""Prove exact ignored fingerprints and detection of unlisted synthetic findings."""

from pathlib import Path
import hashlib, json, os, subprocess, sys, tempfile

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get("GITLEAKS_BIN", ROOT / ".ci-tools/bin/gitleaks")).resolve()


def scan(args, report, expected):
    p = subprocess.run(
        [
            str(BINARY),
            *args,
            "--redact=100",
            "--no-banner",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--exit-code",
            "42",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode not in expected:
        raise RuntimeError(f"Gitleaks returned {p.returncode}, expected {expected}")
    return json.loads(report.read_text()) if report.exists() else []


with tempfile.TemporaryDirectory(prefix="gitleaks-proof-") as folder:
    tmp = Path(folder)
    (tmp / "empty").write_text("")
    fixture = tmp / "fixture"
    fixture.mkdir()
    ignore = tmp / "ignore"
    ignore.write_text("")

    # Generated tokens are inert fixtures and never live credentials.
    def token(s):
        return "gh" + "p_" + hashlib.sha256(s.encode()).hexdigest()[:36]

    (fixture / "one.txt").write_text('token = "' + token("one") + '"\n')
    found = scan(
        ["dir", str(fixture), "--gitleaks-ignore-path", str(ignore)],
        tmp / "found.json",
        {42},
    )
    if not found:
        raise RuntimeError("Synthetic finding missing")
    ignore.write_text(found[0]["Fingerprint"] + "\n")
    suppressed = scan(
        ["dir", str(fixture), "--gitleaks-ignore-path", str(ignore)],
        tmp / "ignored.json",
        {0},
    )
    if suppressed:
        raise RuntimeError("Exact synthetic fingerprint was not ignored")
    (fixture / "two.txt").write_text('token = "' + token("two") + '"\n')
    again = scan(
        ["dir", str(fixture), "--gitleaks-ignore-path", str(ignore)],
        tmp / "unlisted.json",
        {42},
    )
    if not again:
        raise RuntimeError("Unlisted synthetic finding was incorrectly ignored")
    baseline = ROOT / ".gitleaksignore"
    if baseline.exists() and "--history" in sys.argv:
        entries = [
            x.strip()
            for x in baseline.read_text().splitlines()
            if x.strip() and not x.lstrip().startswith("#")
        ]
        if not entries:
            raise RuntimeError("Existing fingerprint baseline has no entries")
        fingerprint = entries[0]
        sha = fingerprint.split(":", 1)[0]
        subprocess.run(
            ["git", "cat-file", "-e", sha + "^{commit}"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        before = scan(
            [
                "git",
                ".",
                "--log-opts=-1 " + sha,
                "--gitleaks-ignore-path",
                str(tmp / "empty"),
            ],
            tmp / "history-before.json",
            {0, 42},
        )
        if not any(f["Fingerprint"] == fingerprint for f in before):
            raise RuntimeError(
                "The historical baseline fingerprint was not independently reproduced"
            )
        after = scan(
            [
                "git",
                ".",
                "--log-opts=-1 " + sha,
                "--gitleaks-ignore-path",
                str(baseline),
            ],
            tmp / "history-after.json",
            {0, 42},
        )
        if any(f["Fingerprint"] == fingerprint for f in after):
            raise RuntimeError("Repository baseline fingerprint was not ignored")
print(
    "PASS: exact synthetic fingerprint suppressed and unlisted synthetic finding detected; baseline files unchanged"
)
