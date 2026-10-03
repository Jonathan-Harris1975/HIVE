#!/usr/bin/env python3
"""Reconcile current PR blockers using trusted metadata and bounded agent repairs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
import pr_repair_router as router

BOT_REVIEWERS = {'chatgpt-codex-connector', 'kilo-code-bot'}
HOLD_LABELS = {'autonomy:human-hold', 'autonomy:obsolete', 'autonomy:superseded', 'hold', 'do-not-merge', 'needs-manual-review'}
RECEIPT = re.compile(r'<!-- pr-blocker-resolution:(\{[^\n]*\}) -->')


def login(value):
    return str(value or '').removesuffix('[bot]')


def review_threads(number):
    rows = []
    cursor = None
    owner, name = router.REPO.split('/', 1)
    for _ in range(10):
        result = router.api('POST', '/graphql', {'query': '''query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
          repository(owner:$owner,name:$name) { pullRequest(number:$number) {
            reviewThreads(first:100,after:$cursor) { pageInfo { hasNextPage endCursor }
              nodes { id isResolved isOutdated path line comments(first:50) {
                pageInfo { hasNextPage } nodes { body author { login } } } } } } } }''',
          'variables': {'owner': owner, 'name': name, 'number': number, 'cursor': cursor}})
        if result.get('errors'):
            raise RuntimeError('Unable to read complete review-thread metadata')
        page = result['data']['repository']['pullRequest']['reviewThreads']
        for thread in page['nodes']:
            if thread['comments']['pageInfo']['hasNextPage']:
                raise RuntimeError('Review thread exceeds the evidence limit')
            rows.append(thread)
        if not page['pageInfo']['hasNextPage']:
            return rows
        cursor = page['pageInfo']['endCursor']
    raise RuntimeError('Review-thread pagination exceeds safe limit')


def required_checks_pass(pr):
    # GitHub supplies the effective native branch requirements. Never infer them
    # from workflow names or accept an empty requirement set as approval.
    branch = router.urllib.parse.quote(router.DEFAULT, safe='')
    rules = router.api('GET', f'/repos/{router.REPO}/rules/branches/{branch}')
    required = [item for rule in rules if rule.get('type') == 'required_status_checks'
                for item in rule.get('parameters', {}).get('required_status_checks', [])]
    if not required:
        return False
    sha = pr['head']['sha']
    checks = []
    for page in range(1, 11):
        part = router.api('GET', f'/repos/{router.REPO}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}')['check_runs']
        checks.extend(part)
        if len(part) < 100:
            break
    else:
        raise RuntimeError('Check pagination exceeds safe limit')
    statuses = router.api('GET', f'/repos/{router.REPO}/commits/{sha}/status')['statuses']
    for item in required:
        context, app = item['context'], item.get('integration_id')
        matches = [c for c in checks if c['name'] == context and (not app or c.get('app', {}).get('id') == app)]
        if matches:
            if any(c.get('status') != 'completed' or c.get('conclusion') != 'success' for c in matches):
                return False
        elif app or not any(s['context'] == context and s['state'] == 'success' for s in statuses):
            return False
    return True


def verified_receipts(comments, sha, base_sha):
    trusted = {login(router.KILO_IMPLEMENTER), login(router.REPAIR_APP_LOGIN)} - {''}
    receipts = {}
    for comment in comments:
        if login(comment.get('user', {}).get('login')) not in trusted:
            continue
        for match in RECEIPT.finditer(comment.get('body', '')):
            try:
                data = json.loads(match.group(1))
            except ValueError:
                continue
            if data.get('sha') != sha or data.get('base_sha') != base_sha:
                continue
            for item in data.get('threads', []):
                if isinstance(item, dict) and re.fullmatch(r'PRRT_[A-Za-z0-9_-]+', str(item.get('id', ''))) and isinstance(item.get('evidence'), str) and len(item['evidence'].strip()) >= 40:
                    receipts[item['id']] = item['evidence']
    return receipts


def recover(number):
    pr = router.pr_details(number)
    if not pr or {x.get('name') for x in pr.get('labels', [])} & HOLD_LABELS:
        return {'pr': number, 'state': 'excluded'}
    base = router.api('GET', f'/repos/{router.REPO}/commits/{router.urllib.parse.quote(router.DEFAULT, safe="")}')['sha']
    sha = pr['head']['sha']
    if pr.get('mergeable') is False or pr.get('mergeable_state') == 'dirty':
        # Include base SHA in the deduplication identity: a later base change is
        # a new conflict, rather than a lifetime two-attempt limit for this PR.
        request = router.dispatch(pr, 'merge-conflict-' + base, [f'Current PR #{number} has merge conflicts at head {sha} against base {base}.'])
        return {'pr': number, 'state': 'conflict-' + request, 'head': sha, 'base': base}
    if pr.get('mergeable') is not True:
        return {'pr': number, 'state': 'mergeability-pending'}
    if pr.get('mergeable_state') == 'behind':
        request = router.dispatch(pr, 'branch-behind-' + base, [f'PR #{number} is behind required current base {base}; head is {sha}.'])
        return {'pr': number, 'state': 'behind-' + request, 'head': sha, 'base': base}
    threads = [t for t in review_threads(number) if not t['isResolved']]
    if not threads:
        return {'pr': number, 'state': 'no-conflict-or-review-blocker'}
    bot_threads = [t for t in threads if t['comments']['nodes'] and
                   login(t['comments']['nodes'][0].get('author', {}).get('login')) in BOT_REVIEWERS]
    comments = router.all_pages(f'/repos/{router.REPO}/issues/{number}/comments')
    receipts = verified_receipts(comments, sha, base)
    resolved = []
    if receipts and required_checks_pass(pr):
        for thread in bot_threads:
            if thread['id'] not in receipts:
                continue
            # Re-read both tips before every mutation; stale evidence never
            # dismisses a review on a newer head or changed base.
            fresh = router.pr_details(number)
            tip = router.api('GET', f'/repos/{router.REPO}/commits/{router.urllib.parse.quote(router.DEFAULT, safe="")}')['sha']
            if not fresh or fresh['head']['sha'] != sha or tip != base or fresh.get('mergeable') is not True or {x.get('name') for x in fresh.get('labels', [])} & HOLD_LABELS:
                return {'pr': number, 'state': 'changed-during-recovery'}
            result = router.api('POST', '/graphql', {'query': 'mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}', 'variables': {'id': thread['id']}})
            if result.get('errors') or not result.get('data', {}).get('resolveReviewThread', {}).get('thread', {}).get('isResolved'):
                raise RuntimeError('Review thread resolution was not confirmed')
            resolved.append(thread['id'])
    remaining = [t for t in bot_threads if t['id'] not in resolved]
    request = None
    if remaining:
        evidence = [f"Thread {t['id']} at {t['path']}:{t.get('line') or 'historical line'} (outdated={t['isOutdated']}): " +
                    router.review_evidence(t['comments']['nodes'][0]['body'], t['path'])[:3500] for t in remaining]
        request = router.dispatch(pr, 'review-threads-' + base, evidence)
    return {'pr': number, 'state': 'review-recovery', 'head': sha, 'base': base,
            'resolved': resolved, 'request_status': request, 'bot_threads_remaining': len(remaining), 'human_threads_remaining': len(threads) - len(bot_threads)}


def candidate_numbers(event):
    if event.get('pull_request'):
        return [int(event['pull_request']['number'])]
    if event.get('issue', {}).get('pull_request'):
        return [int(event['issue']['number'])]
    requested = event.get('inputs', {}).get('pr_number')
    if requested:
        if not str(requested).isdigit():
            raise ValueError('Invalid requested PR number')
        return [int(requested)]
    if event.get('workflow_run', {}).get('event') == 'pull_request':
        pr = router.resolve_run_pr(event['workflow_run'])
        return [int(pr['number'])] if pr else []
    return [int(pr['number']) for pr in router.all_pages(f'/repos/{router.REPO}/pulls?state=open')]


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    results = []
    errors = 0
    for number in candidate_numbers(event):
        try:
            results.append(recover(number))
        except Exception as exc:
            errors += 1
            results.append({'pr': number, 'state': 'recovery-error', 'error_type': type(exc).__name__})
            print(f'::error::Blocker recovery for PR #{number} failed ({type(exc).__name__}). Inspect API permissions and the configured repair webhook.')
    text = json.dumps({'repository': router.REPO, 'results': results}, indent=2) + '\n'
    Path('pr-blocker-recovery.json').write_text(text)
    summary = '# Automatic PR blocker recovery\n\n' + '\n'.join(f"- PR #{r['pr']}: {r['state']}" for r in results) + '\n'
    with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as stream:
        stream.write(summary)
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
