#!/usr/bin/env python3
"""Install pinned CI scanners without third-party GitHub Action wrappers."""
from __future__ import annotations
import hashlib, lzma, os, shutil, sys, tarfile, tempfile, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / ".ci-tools" / "bin"
BIN.mkdir(parents=True, exist_ok=True)
TOOLS = {
    "trivy": {"version":"0.74.0","url":"https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz","checksums":"https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_checksums.txt","asset":"trivy_0.74.0_Linux-64bit.tar.gz","binary":"trivy","archive":"tar.gz"},
    "gitleaks": {"version":"8.30.1","url":"https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz","sha256":"551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb","binary":"gitleaks","archive":"tar.gz"},
    "actionlint": {"version":"1.7.12","url":"https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz","sha256":"8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8","binary":"actionlint","archive":"tar.gz"},
    "lychee": {"version":"0.24.2","url":"https://github.com/lycheeverse/lychee/releases/download/lychee-v0.24.2/lychee-x86_64-unknown-linux-musl.tar.gz","sha256":"73657a111819a30c47c08352896796f23d64e4eb2b3ed39b6d32149241566fc5","binary":"lychee","archive":"tar.gz"},
    "hadolint": {"version":"2.14.0","url":"https://github.com/hadolint/hadolint/releases/download/v2.14.0/hadolint-linux-x86_64","sha256":"6bf226944684f56c84dd014e8b979d27425c0148f61b3bd99bcc6f39e9dc5a47","binary":"hadolint","archive":"binary"},
    "shellcheck": {"version":"0.11.0","url":"https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz","sha256":"8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198","binary":"shellcheck","archive":"tar.xz"},
}
def download(url: str, dest: Path) -> None:
    req=urllib.request.Request(url,headers={"User-Agent":"HIVE-CI/1.0"})
    with urllib.request.urlopen(req,timeout=120) as response,dest.open("wb") as output: shutil.copyfileobj(response,output)
def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
def checksum_from_file(url: str, asset: str, directory: Path) -> str:
    f=directory/"checksums.txt"; download(url,f)
    for line in f.read_text(encoding="utf-8").splitlines():
        parts=line.strip().split()
        if len(parts)>=2 and parts[-1].lstrip("*")==asset: return parts[0].lower()
    raise RuntimeError(f"No checksum found for {asset}")
def install(name: str) -> None:
    if name not in TOOLS: raise SystemExit(f"Unsupported tool: {name}")
    spec=TOOLS[name]; target=BIN/spec["binary"]
    with tempfile.TemporaryDirectory(prefix=f"hive-{name}-") as tmp:
        d=Path(tmp); payload=d/"payload"; download(spec["url"],payload)
        expected=spec.get("sha256") or checksum_from_file(spec["checksums"],spec["asset"],d)
        actual=digest(payload)
        if actual.lower()!=expected.lower(): raise RuntimeError(f"SHA-256 mismatch for {name} {spec['version']}: {actual}")
        if spec["archive"]=="binary": shutil.copy2(payload,target)
        else:
            mode="r:gz" if spec["archive"]=="tar.gz" else "r:xz"
            with tarfile.open(payload,mode) as tar:
                members=[m for m in tar.getmembers() if Path(m.name).name==spec["binary"] and m.isfile()]
                if len(members)!=1: raise RuntimeError(f"Expected one {spec['binary']} binary, found {len(members)}")
                src=tar.extractfile(members[0])
                if src is None: raise RuntimeError(f"Could not extract {spec['binary']}")
                with target.open("wb") as out: shutil.copyfileobj(src,out)
        target.chmod(0o755)
    os.system(f'"{target}" --version')
def main() -> None:
    if len(sys.argv)<2: raise SystemExit("Usage: install_ci_tools.py <tool> [tool ...]")
    for name in sys.argv[1:]: install(name)
if __name__=="__main__": main()
