import sys
import asyncio
from onemancompany.core.vessel import get_agent_loop

async def main():
    vessel = get_agent_loop("00008")
    result = await vessel.resume_held_task("00008", "8ef319f1ae91", "The email sent to playtester@example.com bounced back with a 'Delivery Status Notification (Failure)' stating that the address was not found. Please resend the email to the correct target email address: richf5451@gmail.com.")
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
