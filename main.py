import os
from fastapi import FastAPI, Request
from dhanhq import dhanhq

app = FastAPI()

# Environment Variables से credentials लें
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN")

# Correct Dhan Client Initialization
dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)

@app.get("/")
def home():
    return {"status": "Dhan-Chartink Webhook Server Running"}

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        # Chartink से आने वाला JSON payload read करें
        data = await request.json()
        
        # Stocks लिस्ट निकालें (e.g. "RELIANCE, SBIN")
        stocks_raw = data.get("stocks", "")
        if not stocks_raw:
            return {"status": "ignored", "reason": "No stocks found"}
            
        stocks_list = [s.strip().upper() for s in stocks_raw.split(",") if s.strip()]

        # Orders execute करने का loop
        placed_orders = []
        for trading_symbol in stocks_list:
            order_response = dhan.place_order(
                tag='',
                transaction_type=dhan.BUY,
                exchange_segment=dhan.NSE,
                product_type=dhan.INTRA,
                order_type=dhan.MARKET,
                validity='DAY',
                security_id=trading_symbol,
                quantity=1,
                price=0
            )
            placed_orders.append({"symbol": trading_symbol, "response": order_response})

        return {"status": "success", "executed": placed_orders}

    except Exception as e:
        return {"status": "error", "message": str(e)}
