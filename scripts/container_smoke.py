"""Container CI readiness gate; does not claim model availability."""
import json
import time
import urllib.error
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2) as response:
            body=json.load(response)
        assert body['status']=='ok' and body['version']=='0.2.0'
        print('Container HTTP health verified; model availability is a separate live gate.')
        break
    except (urllib.error.URLError,ConnectionError):
        time.sleep(1)
else:
    raise SystemExit('Container did not become ready in 30 attempts')
