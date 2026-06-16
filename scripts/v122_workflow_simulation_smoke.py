import json
import os
import urllib.request
BASE=os.environ.get('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.environ.get('ADMIN_BEARER_TOKEN','')
headers={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'}
body=json.dumps({'task':'Simulate audit review workflow','repo':'RAMS','workflow_preset':'audit_report_review','policy_profile':'human_approval_required'}).encode()
req=urllib.request.Request(BASE+'/v1/workflow-simulation',data=body,headers=headers,method='POST')
print(urllib.request.urlopen(req,timeout=60).read().decode())
