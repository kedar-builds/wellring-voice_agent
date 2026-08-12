from src.database import get_user_profile
from dotenv import load_dotenv

load_dotenv()
user = get_user_profile("+919082487585")
print(user)
