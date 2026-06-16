import os
import urllib.request
BASE=os.environ.get('HIVE_BASE_URL','https://liable-loreen-jonathanharris-57884580.koyeb.app')
TOKEN=os.environ.get('ADMIN_BEARER_TOKEN','')
req=urllib.request.Request(BASE+'/v1/execution-preview/policy-profiles',headers={'Authorization':f'Bearer {TOKEN}'})
print(urllib.request.urlopen(req,timeout=60).read().decode())
