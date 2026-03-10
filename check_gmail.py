import sys
import json
import os
sys.path.append(os.path.abspath('./company/assets/tools/gmail'))
from gmail import gmail_read_thread

result = gmail_read_thread.invoke({"thread_id": "19cda084067d156f"})
print(json.dumps(result, indent=2))
