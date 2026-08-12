from src.notifications import send_test_whatsapp
from dotenv import load_dotenv

load_dotenv()
result = send_test_whatsapp("+918421971145", "Atharva")
print(result)
