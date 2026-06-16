import json
import os
import time
import urllib.request
import urllib.error
BASE_URL=os.getenv('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.getenv('ADMIN_BEARER_TOKEN','')
payload={'task':os.getenv('HIVE_ROUTE_TASK','triage RSS rewrite quarantine issue'), 'repo':os.getenv('HIVE_REPO','AIMS'), 'limit':5}
req=urllib.request.Request(f'{BASE_URL}/v1/skills/route',data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'},method='POST')
start=time.time()
try:
    with urllib.request.urlopen(req,timeout=60) as r:
        data=json.loads(r.read().decode())
        print(json.dumps({'ok':data.get('ok'), 'status':r.status, 'elapsed_seconds':round(time.time()-start,2), 'primary_skill':data.get('primary_skill'), 'execution_policy':data.get('execution_policy'), 'route_plan':data.get('route_plan')}, indent=2))
except urllib.error.HTTPError as e:
    print(e.read().decode())
