import json, os, time, urllib.parse, urllib.request, urllib.error
BASE_URL=os.getenv('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.getenv('ADMIN_BEARER_TOKEN','')
Q=os.getenv('HIVE_SKILL_QUERY','podcast seo')
headers={'Authorization':f'Bearer {TOKEN}'} if TOKEN else {}
url=f"{BASE_URL}/v1/skills/search?q={urllib.parse.quote(Q)}&limit=10"
req=urllib.request.Request(url,headers=headers,method='GET')
start=time.time()
try:
    with urllib.request.urlopen(req,timeout=60) as r:
        data=json.loads(r.read().decode())
        print(json.dumps({'ok':data.get('ok'), 'status':r.status, 'elapsed_seconds':round(time.time()-start,2), 'query':Q, 'count':data.get('count'), 'top':data.get('items',[None])[0]}, indent=2))
except urllib.error.HTTPError as e:
    print(e.read().decode())
