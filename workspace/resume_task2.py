import sys
import json
import os
import asyncio

sys.path.append(os.path.abspath("./src"))

from onemancompany.core.vessel import Vessel
from onemancompany.core.config import Settings

async def main():
    settings = Settings()
    vessel = Vessel(settings, "00008")
    
    # Try to find the method
    print(dir(vessel))
    
    # Let's try to use the workflow engine directly
    from onemancompany.core.workflow_engine import WorkflowEngine
    from onemancompany.core.task_persistence import TaskPersistence
    
    db = TaskPersistence(settings.db_path)
    engine = WorkflowEngine(db)
    
    success = await engine.resume_task("8ef319f1ae91", "The email sent to playtester@example.com bounced with the following error: \"Address not found. Your message wasn't delivered to playtester@example.com because the domain example.com couldn't be found.\" Please use a valid email address for the playtester.")
    print(f"Task resumed: {success}")

asyncio.run(main())
