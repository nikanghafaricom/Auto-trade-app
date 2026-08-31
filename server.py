# ==============================================
# Tabdeal Test Bot - 1. BUY BTC
# ==============================================
import os
import time
import logging
import hmac
import hashlib
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

API_KEY = os.getenv("TABDEAL_API_KEY", "")
API_SECRET = os.getenv("TABDEAL_SECRET", "")

def generate_signature(query_string: str) -> str:
    return hmac.new(API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

def get_usdt_balance() -> float:
    try:
        url = "https://api1.tabdeal.org/api/v1/account"
        timestamp = str(int(time.time() * 1000))
        query_string = f"timestamp={timestamp}"
        signature = generate_signature(query_string)
        
        headers = {"X-MBX-APIKEY": API_KEY, "Content-Type": "application/json"}
        res = requests.get(f"{url}?{query_string}&signature={signature}", headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json().get('data', res.json().get('balances', []))
            if isinstance(data, list):
                for asset in data:
                    if asset.get('currency', '').upper() == 'USDT' or asset.get('asset', '').upper() == 'USDT':
                        return float(asset.get('free', asset.get('balance', 0.0)))
        return 0.0
    except Exception as e:
        logger.error(f"خطا در دریافت موجودی: {e}")
        return 0.0

def buy_btc():
    usdt = get_usdt_balance()
    logger.info(f"موجودی تتر فعلی: {usdt}")
    if usdt < 1.0:
        logger.error("موجودی تتر برای خرید کافی نیست.")
        return

    # تخصیص ۲۰ درصد موجودی برای خرید تستی
    budget = usdt * 0.20
    if budget < 1.0: budget = 1.0
    
    # گرفتن قیمت لحظه‌ای
    try:
        ticker = requests.get("https://api1.tabdeal.org/api/v1/ticker/24hr?symbol=BTC_USDT", timeout=5).json()
        price = float(ticker.get('lastPrice', ticker.get('last', 60000)))
    except:
        price = 60000.0

    quantity = f"{budget / price:.6f}"
    timestamp = str(int(time.time() * 1000))
    
    # ساخت دقیق Query String طبق مستندات صرافی
    query_params = f"quantity={quantity}&side=BUY&symbol=BTCUSDT&tabdealSymbol=BTC_USDT&timestamp={timestamp}&type=MARKET"
    signature = generate_signature(query_params)
    
    url = f"https://api1.tabdeal.org/api/v1/order?{query_params}&signature={signature}"
    headers = {"X-MBX-APIKEY": API_KEY, "Content-Type": "application/json"}
    
    res = requests.post(url, headers=headers, timeout=10)
    logger.info(f"پاسخ صرافی برای خرید: {res.status_code} - {res.text}")

if __name__ == "__main__":
    logger.info("تست خرید آغاز شد...")
    buy_btc()
