import os
from fastapi import FastAPI, Request

# DhanHQ compatibility import (DhanHQ v2/v3 Context support)
try:
    from dhanhq import dhanhq, DhanContext
    USE_CONTEXT = True
except ImportError:
    from dhanhq import dhanhq
    USE_CONTEXT = False

app = FastAPI()

# Environment Variables
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

# Initialize Dhan Client Safely
dhan = None
try:
    if USE_CONTEXT:
        # New DhanHQ SDK Method
        context = DhanContext(CLIENT_ID, ACCESS_TOKEN)
        dhan = dhanhq(context)
    else:
        # Legacy DhanHQ SDK Method
        dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
    print("✅ Dhan Client Initialized Successfully!")
except Exception as e:
    print(f"⚠️ Dhan Initialization Error: {str(e)}")

@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Dhan-Chartink Webhook Server is Active"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/webhook")
async def receive_webhook(request: Request):
    if not dhan:
        return {"status": "error", "message": "Dhan client not initialized"}

    try:
        data = await request.json()
        stocks_str = data.get("stocks", "")
        
        if not stocks_str:
            return {"status": "ignored", "reason": "No stocks found in payload"}

        stocks = [s.strip().upper() for s in stocks_str.split(",") if s.strip()]
        placed_orders = []

        for symbol in stocks:
            # Dhan Order Execution
            # Note: Dhan API expects Security ID for production trades.
            response = dhan.place_order(
                security_id=symbol,
                exchange_segment="NSE_EQ",
                transaction_type="BUY",
                quantity=1,
                order_type="MARKET",
                product_type="CNC",
                price=0
            )
            placed_orders.append({"symbol": symbol, "response": response})

        return {"status": "success", "orders": placed_orders}

    except Exception as e:
        return {"status": "error", "message": str(e)}
