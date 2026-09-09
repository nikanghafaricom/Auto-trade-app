# ==============================================
# تست مستقل فروش BNB - همون متد اصلی، بدون هیچ بخش اضافه
# به محض دپلوی، یک‌بار سعی می‌کند کل موجودی BNB را بفروشد و نتیجه را دقیق لاگ می‌کند.
# ==============================================
import os
import math
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

WALLEX_API_KEY = os.getenv("WALLEX_API_KEY", "").strip()
BASE_URL = "https://api.wallex.ir/v1"
SYMBOL_BASE = "BNB"
WALLEX_SYMBOL = "BNBUSDT"


def get_market_limits(wallex_symbol: str):
    url = f"{BASE_URL}/markets"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
        data = res.json()
        symbols = data.get('result', {}).get('symbols', {})
        symbol_data = symbols.get(wallex_symbol, {})
        min_qty = float(symbol_data.get('minQty', 0) or 0)
        min_notional = float(symbol_data.get('minNotional', 0) or 0)
        step_size = int(symbol_data.get('stepSize', 6))
        return min_qty, min_notional, step_size
    logger.error(f"خطا در دریافت محدودیت‌های بازار {wallex_symbol} - کد: {res.status_code}")
    return 0.0, 0.0, 6


def get_wallex_price(wallex_symbol: str):
    url = f"{BASE_URL}/markets"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
        data = res.json()
        symbols = data.get('result', {}).get('symbols', {})
        symbol_data = symbols.get(wallex_symbol, {})
        last_price = symbol_data.get('stats', {}).get('lastPrice')
        if last_price is not None:
            return float(last_price)
    logger.error(f"خطا در دریافت قیمت لحظه‌ای {wallex_symbol}")
    return None


def get_base_free(base_symbol: str):
    url = f"{BASE_URL}/account/balances"
    headers = {"X-API-Key": WALLEX_API_KEY}
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        res_json = res.json()
        result_data = res_json.get('result', res_json)
        balances_dict = result_data.get('balances', result_data)

        if isinstance(balances_dict, dict):
            asset_info = balances_dict.get(base_symbol, {})
            return float(asset_info.get('value', asset_info.get('free', 0.0)))
        elif isinstance(balances_dict, list):
            for asset in balances_dict:
                if asset.get('asset', asset.get('symbol', '')).upper() == base_symbol.upper():
                    return float(asset.get('value', asset.get('free', 0.0)))
        return 0.0
    else:
        logger.error(f"خطا در دریافت موجودی - کد: {res.status_code} | متن: {res.text}")
        return 0.0


def submit_order(wallex_symbol: str, side: str, quantity: float, price: float, order_type: str = "market"):
    url = f"{BASE_URL}/account/orders"
    headers = {
        "X-API-Key": WALLEX_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "symbol": wallex_symbol,
        "type": order_type,
        "side": side,
        "quantity": quantity,
        "price": str(price)
    }
    logger.info(f"ارسال سفارش -> {json.dumps(payload, ensure_ascii=False)}")
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    return response


def place_sell_with_retries(wallex_symbol: str, quantity: float, price: float):
    limit_offsets = [0, 0.002, 0.01]

    response = submit_order(wallex_symbol, "sell", quantity, price, "market")
    logger.info(f"پاسخ Market (sell) - کد: {response.status_code} | متن: {response.text}")
    if response.status_code in [200, 201]:
        return response

    if "امکان ثبت سفارش قیمت بازار" not in response.text:
        return response

    for offset in limit_offsets:
        limit_price = price * (1 - offset)
        response = submit_order(wallex_symbol, "sell", quantity, limit_price, "limit")
        logger.info(f"پاسخ Limit آفست {offset * 100:.1f}٪ (sell) - کد: {response.status_code} | متن: {response.text}")
        if response.status_code in [200, 201]:
            return response

    return response


def main():
    logger.info(f"=== تست مستقل فروش {SYMBOL_BASE} شروع شد ===")

    if not WALLEX_API_KEY:
        logger.error("WALLEX_API_KEY تنظیم نشده. تست متوقف شد.")
        return

    base_free = get_base_free(SYMBOL_BASE)
    logger.info(f"موجودی فعلی {SYMBOL_BASE}: {base_free}")

    if base_free <= 0:
        logger.warning(f"موجودی {SYMBOL_BASE} صفر یا منفی است. چیزی برای فروش نیست.")
        return

    price = get_wallex_price(WALLEX_SYMBOL)
    if price is None:
        logger.error("دریافت قیمت لحظه‌ای ناموفق بود. تست متوقف شد.")
        return
    logger.info(f"قیمت لحظه‌ای {WALLEX_SYMBOL}: {price}")

    min_qty, min_notional, step_size = get_market_limits(WALLEX_SYMBOL)
    logger.info(f"محدودیت‌های بازار {WALLEX_SYMBOL} -> minQty: {min_qty} | minNotional: {min_notional} | stepSize: {step_size}")

    factor = 10 ** step_size
    quantity = math.floor(base_free * factor) / factor
    logger.info(f"مقدار نهایی برای فروش پس از گرد کردن ({step_size} رقم اعشار): {quantity}")

    if quantity <= 0:
        logger.warning("مقدار پس از گرد کردن صفر شد. فروش ممکن نیست.")
        return

    result = place_sell_with_retries(WALLEX_SYMBOL, quantity, price)

    if result.status_code in [200, 201]:
        logger.info(f"✅ فروش {SYMBOL_BASE} موفق بود.")
    else:
        logger.error(f"❌ فروش {SYMBOL_BASE} ناموفق بود. کد نهایی: {result.status_code} | متن نهایی: {result.text}")

    logger.info("=== تست تمام شد ===")


if __name__ == "__main__":
    main()
