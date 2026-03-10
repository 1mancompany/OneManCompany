import sys
import json
import os
import asyncio

sys.path.append(os.path.abspath("./src"))

from onemancompany.core.config import Settings
from onemancompany.core.vessel import EmployeeManager

async def main():
    settings = Settings()
    manager = EmployeeManager()
    print("EmployeeManager methods:", [m for m in dir(manager) if "resume" in m])
    
    success = await manager.resume_held_task("00008", "8ef319f1ae91", "The email sent to playtester@example.com bounced with the following error: \"Address not found. Your message wasn't delivered to playtester@example.com because the domain example.com couldn't be found.\" Please use a valid email address for the playtester.")
    print(f"Task resumed: {success}")

asyncio.run(main())
