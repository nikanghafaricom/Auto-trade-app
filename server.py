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
        print("در حال بررسی موجودی بیت‌کوین برای فروش...")
        client = Spot(API_KEY, API_SECRET)
        
        # ۱. گرفتن موجودی حساب
        account = client.account()
        balances = account.get('balances', [])
        
        btc_free = 0.0
        for asset in balances:
            if asset.get('asset') == 'BTC':
                btc_free = float(asset.get('free', 0.0))
                break
                
        print(f"موجودی بیت‌کوین آزاد شما: {btc_free}")
        
        if btc_free <= 0.00001:
            print("❌ موجودی بیت‌کوین برای فروش کافی نیست.")
        else:
            # مشخص کردن دقیق مقدار برای فروش
            quantity = f"{btc_free:.5f}"
            
            print(f"ارسال سفارش فروش بازار (SELL) برای {quantity} بیت‌کوین...")
            order = client.new_order(
                symbol='BTCUSDT',
                side=OrderSides.SELL,
                type=OrderTypes.MARKET,
                quantity=quantity
            )
            print(f"✅ بیت‌کوین با موفقیت فروخته شد و به تتر تبدیل شد!\n{order}")
        
    except Exception as e:
        print(f"❌ خطا در اجرای فروش: {e}")
