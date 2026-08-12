import asyncio
from src.notifications import send_notification
from src.config import init_config

init_config()
asyncio.run(send_notification("whatsapp", "Hello from WellRing! Your Sandbox is working.", "+918421971145"))
