import os
from fastapi import FastAPI, Request
from dhanhq import dhanhq

app = FastAPI()

# Dhan API Configuration (Environment Variables se read hoga)
# Environment Variables से वैल्यू रीड हो रही है (यहाँ असली ID/Token लिखने की ज़रूरत नहीं है)
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN")

# Initialize Dhan Client (यहाँ Parameter Name लिखना ज़रूरी है)
dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
@app.get("/")
def home():
    return {"status": "Dhan-Chartink Webhook Server Running"}

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        # Chartink se aane wala JSON payload read karna
        data = await request.json()
        
        # Chartink multi-stock alerts comma-separated bhejta hai (e.g. "RELIANCE, SBIN")
        stocks = data.get("stocks", "").split(",")
        
        placed_orders = []

        for symbol in stocks:
            trading_symbol = symbol.strip().upper()
            if not trading_symbol:
                continue

            # Dhan Cash Order Parameters
            # ProductType: CNC (Delivery - agar 2-3 din hold karna ho) ya INTRADAY
            # Buy Order at Market Price
            order_response = dhan.place_order(
                security_id=trading_symbol,  # Note: Ideal cases mein Dhan Security ID map karni hoti hai
                exchange_segment=dhan.NSE,
                transaction_type=dhan.BUY,
                quantity=1,                   # Aap isse capital ke hisab se customize kar sakte hain
                order_type=dhan.MARKET,
                product_type=dhan.CNC,        # Short-term swing ke liye CNC / Delivery
                price=0
            )

            placed_orders.append({
                "symbol": trading_symbol,
                "response": order_response
            })

        return {"status": "success", "orders": placed_orders}

    except Exception as e:
        return {"status": "error", "message": str(e)}
