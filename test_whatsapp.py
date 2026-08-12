from src.notifications import send_test_whatsapp
import os
from dotenv import load_dotenv

load_dotenv()

def main():
    # Use the phone number the user provided
    phone_number = "+918421971145"
    result = send_test_whatsapp(phone_number)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
