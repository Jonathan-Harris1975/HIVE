"""Read-only failed-job index; never download logs, artifacts or branch code."""
import html
import json
import os
from pathlib import Path
import re
import urllib.request


def safe(value):
    return html.escape(str(value)).replace('[', '&#91;').replace(']', '&#93;').replace('|', '&#124;').replace('\n', ' ').replace('`', '&#96;')


def fetch(path):
    req = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def render(repo, run, jobs, artifacts):
    number = int(run['id'])
    url = f'https://github.com/{repo}/actions/runs/{number}'
    lines = ['# Deployment / CI failure details', '',
             f'**{safe(run.get("name"))}: {safe(run.get("conclusion"))}** — [open failed run]({url})', '',
             f'Head commit: `{safe(run.get("head_sha"))}`; attempt: {int(run.get("run_attempt", 1))}.', '',
             '| Job | Result | Failed / cancelled step | Evidence |', '|---|---|---|---|']
    failed = [j for j in jobs if j.get('conclusion') not in ('success', 'skipped', 'neutral')]
    for job in failed:
        steps = [s for s in job.get('steps', []) if s.get('conclusion') not in ('success', 'skipped', 'neutral')]
        names = ', '.join(safe(s.get('name')) for s in steps) or 'No failing step recorded; inspect runner/timeout/cancellation details'
        link = f'{url}/job/{int(job["id"])}'
        lines.append(f'| {safe(job.get("name"))} | {safe(job.get("conclusion") or job.get("status"))} | {names} | [Job log]({link}) |')
    if not failed:
        lines += ['', 'No failed jobs were recorded. The run may have been cancelled before jobs started or failed during workflow validation. Inspect the run-level message.']
    if artifacts:
        lines += ['', '## Available diagnostic artifacts', '']
        for item in artifacts:
            if item.get('expired'):
                continue
            lines.append(f'- [{safe(item["name"])}]({url}/artifacts/{int(item["id"])})')
    lines += ['', ('Security runs publish rule/file/line/commit evidence in **security-diagnostics**. '
                  'Test, build and deployment errors are in the linked failed step logs; logs are not copied into this report.'),
              'Fix the recorded cause and rerun the relevant checks. This reporting workflow grants no bypass and does not change the failed check conclusion.']
    return '\n'.join(lines) + '\n'


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    run_id = os.environ['SOURCE_RUN_ID']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not run_id.isdigit():
        raise ValueError('Invalid repository or workflow run ID')
    prefix = f'/repos/{repo}/actions/runs/{run_id}'
    run = fetch(prefix)
    attempt = int(run.get('run_attempt', 1))
    jobs = []
    for page in range(1, 11):
        part = fetch(f'{prefix}/attempts/{attempt}/jobs?per_page=100&page={page}')['jobs']
        jobs.extend(part)
        if len(part) < 100:
            break
    else:
        raise RuntimeError('Job pagination exceeded safe limit; refusing an incomplete report')
    artifacts = []
    for page in range(1, 11):
        part = fetch(f'{prefix}/artifacts?per_page=100&page={page}')['artifacts']
        artifacts.extend(part)
        if len(part) < 100:
            break
    else:
        raise RuntimeError('Artifact pagination exceeded safe limit')
    text = render(repo, run, jobs, artifacts)
    Path('deployment-failure-details.md').write_text(text)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(text)


if __name__ == '__main__':
    main()
