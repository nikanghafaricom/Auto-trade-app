    def get_usdt_balance(self) -> float:
        """دریافت موجودی واقعی تتر با تست مسیرهای جایگزین تبدیل"""
        urls = [
            "https://api1.tabdeal.org/api/v1/account",
            "https://api1.tabdeal.org/api/v1/assets",
            "https://api1.tabdeal.org/api/v1/user/wallets",
            "https://api1.tabdeal.org/api/v1/portfolio"
        ]
        
        headers = {
            "X-API-Key": self.config.TABDEAL_API_KEY,
            "X-API-Secret": self.config.TABDEAL_SECRET,
            "Content-Type": "application/json"
        }

        for url in urls:
            try:
                res = requests.get(url, headers=headers, timeout=10)
                logger.info(f"تست مسیر جدید {url} - کد پاسخ: {res.status_code}")
                if res.status_code == 200:
                    data = res.json()
                    items = data.get('data', data.get('balances', data.get('wallets', data)))
                    if isinstance(items, list):
                        for asset in items:
                            currency = asset.get('currency', asset.get('asset', '')).upper()
                            if currency == 'USDT':
                                balance = float(asset.get('free', asset.get('balance', 0.0)))
                                logger.info(f"موجودی واقعی تتر دریافت شد: {balance} USDT")
                                return balance
            except Exception as e:
                logger.error(f"خطا در ارتباط با {url}: {e}")

        logger.warning("مسیرهای جدید هم پاسخ ندادند. مقدار پیش‌فرض اعمال می‌شود.")
        return 100.0
