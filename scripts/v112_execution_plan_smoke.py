import json, os, time, urllib.request, urllib.error
BASE_URL=os.getenv('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.getenv('ADMIN_BEARER_TOKEN','')
payload={'task':os.getenv('HIVE_EXECUTION_TASK','review podcast SEO workflow and produce a dry-run plan'), 'repo':os.getenv('HIVE_REPO','AIMS'), 'workflow_preset':os.getenv('HIVE_WORKFLOW_PRESET','podcast_episode_review'), 'limit':5}
req=urllib.request.Request(f'{BASE_URL}/v1/ecosystem/execution-plan',data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'},method='POST')
start=time.time()
try:
    with urllib.request.urlopen(req,timeout=60) as r:
        data=json.loads(r.read().decode())
        print(json.dumps({'ok':data.get('ok'), 'status':r.status, 'elapsed_seconds':round(time.time()-start,2), 'build_stage_hint':data.get('build_stage_hint'), 'execution_mode':data.get('execution_mode'), 'can_execute_now':data.get('can_execute_now'), 'guardrails':data.get('guardrails'), 'steps':data.get('shared_steps')}, indent=2))
except urllib.error.HTTPError as e:
    print(e.read().decode())
