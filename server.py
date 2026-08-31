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
        print("در حال بررسی موجودی برای خرید...")
        client = Spot(API_KEY, API_SECRET)
        
        # ۱. گرفتن موجودی حساب
        account = client.account()
        balances = account.get('balances', [])
        
        usdt_free = 0.0
        for asset in balances:
            if asset.get('asset') == 'USDT':
                usdt_free = float(asset.get('free', 0.0))
                break
                
        print(f"موجودی تتر آزاد شما: {usdt_free}")
        
        if usdt_free < 1.0:
            print("❌ موجودی تتر برای خرید کمتر از حد مجاز است.")
        else:
            # تخصیص تمام یا درصدی از تتر موجود برای خرید
            # مثلاً ۹۵ درصد موجودی تتر
            budget = usdt_free * 0.95
            
            # گرفتن قیمت لحظه‌ای بیت‌کوین برای محاسبه مقدار دقیق
            ticker = client.ticker_24hr(symbol='BTC_USDT')
            price = float(ticker.get('lastPrice', 60000))
            
            # محاسبه مقدار بیت‌کوین بر اساس بودجه تتر
            quantity = f"{budget / price:.5f}"
            
            print(f"ارسال سفارش خرید به ارزش تقریبی {budget} تتر...")
            order = client.new_order(
                symbol='BTCUSDT',
                side=OrderSides.BUY,
                type=OrderTypes.MARKET,
                quantity=quantity
            )
            print(f"✅ سفارش با موفقیت ثبت و نهایی شد!\n{order}")
        
    except Exception as e:
        print(f"❌ خطا در اجرا: {e}")
