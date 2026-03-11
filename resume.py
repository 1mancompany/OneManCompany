import sys
import json
import urllib.request

def resume_task(task_id, result):
    url = f"http://localhost:8000/api/tasks/{task_id}/resume"
    data = json.dumps({"result": result}).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.read().decode())
    except Exception as e:
        print(f"Error: {e}")

resume_task("8ef319f1ae91", "The email sent to `playtester@example.com` bounced back with a \"Delivery Status Notification (Failure)\" stating that the domain `example.com` couldn't be found. Please provide a valid email address for the playtester, or use my target_email (richf5451@gmail.com) if you want me to act as the relay for the playtest.")
