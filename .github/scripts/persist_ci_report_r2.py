#!/usr/bin/env python3
"""Persist exact-run DAST/Council evidence to the hive-repositories R2 bucket."""
from __future__ import annotations
import argparse, datetime as dt, hashlib, hmac, json, mimetypes, os, re, urllib.error, urllib.parse, urllib.request
from pathlib import Path

BUCKET="hive-repositories"
def req(name):
    v=os.environ.get(name,"").strip()
    if not v: raise SystemExit(f"{name} is required")
    return v
def signing_key(secret,date,region="auto",service="s3"):
    k=hmac.new(("AWS4"+secret).encode(),date.encode(),hashlib.sha256).digest()
    k=hmac.new(k,region.encode(),hashlib.sha256).digest()
    k=hmac.new(k,service.encode(),hashlib.sha256).digest()
    return hmac.new(k,b"aws4_request",hashlib.sha256).digest()
def request(method, endpoint, key, body=b"", ctype=None, expected_length=None):
    access,secret=req("R2_ACCESS_KEY_ID"),req("R2_SECRET_ACCESS_KEY")
    parsed=urllib.parse.urlsplit(endpoint.rstrip("/")+"/"+BUCKET+"/"+"/".join(urllib.parse.quote(p,safe="-._~") for p in key.split("/")))
    now=dt.datetime.now(dt.timezone.utc); amz=now.strftime("%Y%m%dT%H%M%SZ"); day=now.strftime("%Y%m%d")
    ph=hashlib.sha256(body).hexdigest()
    headers={"host":parsed.netloc,"x-amz-content-sha256":ph,"x-amz-date":amz}
    if ctype: headers["content-type"]=ctype
    names=sorted(headers); canon="".join(f"{n}:{headers[n]}\n" for n in names); signed=";".join(names)
    creq="\n".join([method,parsed.path,"",canon,signed,ph]); scope=f"{day}/auto/s3/aws4_request"
    sts="\n".join(["AWS4-HMAC-SHA256",amz,scope,hashlib.sha256(creq.encode()).hexdigest()])
    sig=hmac.new(signing_key(secret,day),sts.encode(),hashlib.sha256).hexdigest()
    out={k:v for k,v in headers.items() if k!="host"}
    out["Authorization"]=f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={sig}"
    r=urllib.request.Request(urllib.parse.urlunsplit(parsed),data=body if method=="PUT" else None,headers=out,method=method)
    try:
        with urllib.request.urlopen(r,timeout=45) as resp:
            if resp.status not in (200,201,204): raise RuntimeError(f"R2 HTTP {resp.status}")
            if expected_length is not None:
                remote = int(resp.headers.get("Content-Length", "-1"))
                if remote != expected_length:
                    raise RuntimeError(f"R2 length mismatch: expected {expected_length}, got {remote}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"R2 {method} failed with HTTP {e.code}") from e
def main():
    p=argparse.ArgumentParser(); p.add_argument("phase",choices=("dast","council")); p.add_argument("sha"); p.add_argument("source",type=Path); a=p.parse_args()
    repository = req("GITHUB_REPOSITORY")
    repo = repository.split("/")[-1]
    run = os.environ.get("EVIDENCE_RUN_ID", "").strip() or req("GITHUB_RUN_ID")
    attempt = (
        os.environ.get("EVIDENCE_RUN_ATTEMPT", "").strip()
        or req("GITHUB_RUN_ATTEMPT")
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40}",a.sha): raise SystemExit("exact 40-character SHA required")
    if not run.isdigit() or not attempt.isdigit(): raise SystemExit("numeric run id/attempt required")
    if not a.source.is_dir(): raise SystemExit("evidence source directory required")
    endpoint = (
        os.environ.get("R2_ENDPOINT", "").strip()
        or os.environ.get("R2_ENDPOINT_URL", "").strip()
    )
    if not endpoint:
        account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
        if account_id:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    parsed_endpoint = urllib.parse.urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "https"
        or not parsed_endpoint.hostname
        or parsed_endpoint.username
        or parsed_endpoint.password
        or parsed_endpoint.query
        or parsed_endpoint.fragment
        or parsed_endpoint.path not in ("", "/")
    ):
        raise SystemExit("credential-free bare HTTPS R2 endpoint required")
    now=dt.datetime.now(dt.timezone.utc); iso=now.isocalendar()
    prefix=f"ci-reports/{repo}/{iso.year}/{iso.week:02d}/{a.sha}/{a.phase}/{run}/attempt-{attempt}"
    files=[x for x in sorted(a.source.rglob("*")) if x.is_file() and x.name!="evidence-manifest.json"]
    if not files: raise SystemExit("refusing to persist empty evidence")
    objects=[]
    for f in files:
        rel=f.relative_to(a.source).as_posix(); key=f"{prefix}/{rel}"; body=f.read_bytes()
        request("PUT",endpoint,key,body,mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        request("HEAD",endpoint,key,expected_length=len(body))
        objects.append({"key":key,"sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body)})
    manifest = {
        "schema_version": 1,
        "repository": repo,
        "phase": a.phase,
        "default_branch_sha": a.sha,
        "workflow_run_id": run,
        "workflow_run_attempt": attempt,
        "created_at": now.isoformat(),
        "status": os.environ.get("EVIDENCE_STATUS", "UNKNOWN"),
        "objects": objects,
        "source_run": f"https://github.com/{repository}/actions/runs/{run}",
    }
    mb=(json.dumps(manifest,indent=2,sort_keys=True)+"\n").encode(); mkey=f"{prefix}/evidence-manifest.json"
    request("PUT",endpoint,mkey,mb,"application/json"); request("HEAD",endpoint,mkey,expected_length=len(mb))
    print(json.dumps({"verified":True,"bucket":BUCKET,"prefix":prefix,"manifest_key":mkey,"objects":len(objects)+1},sort_keys=True))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as h: h.write(f"\nR2 evidence verified: \`r2://{BUCKET}/{prefix}/\`\n")
if __name__=="__main__": main()
