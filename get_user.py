from src.database import get_user_by_phone
from dotenv import load_dotenv

load_dotenv()
user = get_user_by_phone("+919082487585")
print(user)
