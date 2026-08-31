import os
import logging
from tabdeal.spot import Spot
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

API_KEY = os.getenv("TABDEAL_API_KEY", "")
API_SECRET = os.getenv("TABDEAL_SECRET", "")

if __name__ == "__main__":
    try:
        print("در حال تست اتصال به صرافی...")
        client = Spot(API_KEY, API_SECRET)
        account_info = client.get_account()
        print(f"✅ اتصال موفق بود! نتیجه: {account_info}")
    except Exception as e:
        print(f"❌ خطا رخ داد: {e}")
