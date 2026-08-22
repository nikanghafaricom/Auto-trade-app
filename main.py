import os
import requests
from typing import Annotated
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.getenv("EXCHANGE_API_KEY", "")
SECRET = os.getenv("EXCHANGE_SECRET", "")
BRIDGE_TOKEN = os.getenv("BRIDGE_SECRET_TOKEN", "my_secure_token_123")

class OrderRequest(BaseModel):
    symbol: str
    side: str
    amount: float

@app.post("/execute-order")
def execute_order(order: OrderRequest, authorization: Annotated[str | None, Header()] = None):
    if authorization != f"Bearer {BRIDGE_TOKEN}":
        raise HTTPException(status_code=403, detail="Unauthorized")

    # ارسال دستور به صرافی داخلی
    url = "https://api.tabdeal.org/p2p/v1/order"
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "symbol": order.symbol.replace("/", "_"),
        "side": order.side,
        "type": "MARKET",
        "quantity": order.amount
    }

    res = requests.post(url, json=payload, headers=headers, timeout=10)
    return {"status": res.status_code, "response": res.json() if res.status_code == 200 else res.text}
