import os
import logging
from tabdeal.spot import Spot
from tabdeal.enums import OrderSides, OrderTypes
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

API_KEY = os.getenv("TABDEAL_API_KEY", "")
API_SECRET = os.getenv("TABDEAL_SECRET", "")

if __name__ == "__main__":
    try:
        print("در حال ارسال سفارش خرید تستی...")
        client = Spot(API_KEY, API_SECRET)
        
        # ثبت سفارش خرید با کتابخانه رسمی
        # نماد و مقدار بر اساس موجودی تتر شما تنظیم شده است
        order = client.new_order(
            symbol='BTCUSDT',
            side=OrderSides.BUY,
            type=OrderTypes.MARKET,
            quantity="0.0001"  # مقدار بسیار کم برای تست
        )
        print(f"✅ سفارش خرید با موفقیت ثبت شد!\n{order}")
        
    except Exception as e:
        print(f"❌ خطا در ثبت سفارش: {e}")
