import json, os, urllib.request
BASE=os.environ.get('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.environ.get('ADMIN_BEARER_TOKEN','')
headers={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'}
body=json.dumps({'task':'Review podcast SEO workflow','repo':'AIMS','workflow_preset':'podcast_episode_review','dry_run':True}).encode()
req=urllib.request.Request(BASE+'/v1/execution-preview/save',data=body,headers=headers,method='POST')
print(urllib.request.urlopen(req,timeout=60).read().decode())
