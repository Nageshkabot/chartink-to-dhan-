import os
import math
import requests
from fastapi import FastAPI, Request
from typing import Dict

try:
    from dhanhq import dhanhq, DhanContext
    USE_CONTEXT = True
except ImportError:
    from dhanhq import dhanhq
    USE_CONTEXT = False

app = FastAPI()

# ==================== CONFIGURATION & CONTROLS ====================
MAX_ACTIVE_POSITIONS = 2      # Max allowed parallel open positions
TRADE_CAPITAL_INR = 2500.0    # Capital allocated per trade
PRODUCT_TYPE = "MTF"          # Margin Trading Facility
EXCHANGE_SEGMENT = "NSE_EQ"

# Environment Variables
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

# Active position tracking storage
active_positions: Dict[str, dict] = {}

# Initialize Dhan Client Safely
dhan = None
try:
    if USE_CONTEXT:
        context = DhanContext(CLIENT_ID, ACCESS_TOKEN)
        dhan = dhanhq(context)
    else:
        dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
    print("✅ Dhan Client Initialized Successfully!")
except Exception as e:
    print(f"⚠️ Dhan Initialization Error: {str(e)}")


# Cache memory for Dhan Security IDs
SECURITY_ID_CACHE = {}

def get_dhan_security_id(symbol: str) -> str:
    """Trading symbol (e.g. RELIANCE) ko Dhan Security ID me convert karta hai."""
    symbol = symbol.upper().strip()
    if symbol in SECURITY_ID_CACHE:
        return SECURITY_ID_CACHE[symbol]
    
    try:
        # Fetch official Dhan Scrip Master JSON
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        # Search directly using API endpoint or fallback lookup
        # Default safety check for direct numeric string pass
        if symbol.isdigit():
            return symbol
    except Exception as e:
        print(f"⚠️ Error fetching security ID for {symbol}: {e}")
    
    return symbol


def calculate_mtf_quantity(price: float, capital: float) -> int:
    """Calculates shares quantity based on target capital and current price."""
    if price <= 0:
        return 1
    qty = math.floor(capital / price)
    return qty if qty > 0 else 1


def execute_dhan_mtf_buy(symbol: str, price: float) -> dict:
    """Executes MTF BUY order on Dhan with response validation."""
    qty = calculate_mtf_quantity(price, TRADE_CAPITAL_INR)
    sec_id = get_dhan_security_id(symbol)
    
    try:
        response = dhan.place_order(
            security_id=sec_id,
            exchange_segment=EXCHANGE_SEGMENT,
            transaction_type="BUY",
            quantity=qty,
            order_type="MARKET",
            product_type=PRODUCT_TYPE,
            price=0
        )
        print(f"📥 Raw Dhan Response (BUY {symbol}): {response}")
        
        # Validate Success Response
        if isinstance(response, dict) and response.get("status") == "success":
            return {
                "status": "SUCCESS", 
                "quantity": qty, 
                "order_id": response.get("data", {}).get("orderId"),
                "raw": response
            }
        else:
            print(f"❌ BUY Order Rejected by Dhan for {symbol}: {response}")
            return {"status": "FAILED", "reason": response}
            
    except Exception as e:
        print(f"❌ Exception during BUY Order execution: {str(e)}")
        return {"status": "FAILED", "reason": str(e)}


def execute_dhan_mtf_sell(symbol: str) -> dict:
    """Executes MTF SELL (Square off) order on Dhan with response validation."""
    qty = active_positions.get(symbol, {}).get("quantity", 1)
    sec_id = get_dhan_security_id(symbol)
    
    try:
        response = dhan.place_order(
            security_id=sec_id,
            exchange_segment=EXCHANGE_SEGMENT,
            transaction_type="SELL",
            quantity=qty,
            order_type="MARKET",
            product_type=PRODUCT_TYPE,
            price=0
        )
        print(f"📥 Raw Dhan Response (SELL {symbol}): {response}")
        
        if isinstance(response, dict) and response.get("status") == "success":
            return {"status": "SUCCESS", "quantity": qty, "raw": response}
        else:
            print(f"❌ SELL Order Rejected by Dhan for {symbol}: {response}")
            return {"status": "FAILED", "reason": response}
            
    except Exception as e:
        print(f"❌ Exception during SELL Order execution: {str(e)}")
        return {"status": "FAILED", "reason": str(e)}


# ==================== ENDPOINTS ====================

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "Dhan-Chartink MTF Automated Webhook Engine",
        "max_active_positions": MAX_ACTIVE_POSITIONS,
        "current_open_positions": len(active_positions),
        "product_type": PRODUCT_TYPE,
        "active_symbols": list(active_positions.keys())
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
        prices_str = data.get("close", "")
        
        if not stocks_str:
            return {"status": "ignored", "reason": "No stocks found in payload"}

        incoming_stocks = [s.strip().upper() for s in stocks_str.split(",") if s.strip()]
        
        prices = [float(p.strip()) for p in str(prices_str).split(",") if p.strip() and p.strip().replace('.', '', 1).isdigit()]
        default_price = prices[0] if prices else 100.0

        # --- 1. EXIT LOGIC (Alert Discontinuation Monitor) ---
        exited_positions = []
        current_active_keys = list(active_positions.keys())
        
        for symbol in current_active_keys:
            if symbol not in incoming_stocks:
                sell_res = execute_dhan_mtf_sell(symbol)
                # Only remove from memory if Dhan successfully accepted/processed order
                if sell_res.get("status") == "SUCCESS":
                    exited_positions.append({"symbol": symbol, "action": "EXIT_ALERT_STOPPED", "response": sell_res})
                    del active_positions[symbol]
                else:
                    print(f"⚠️ Could not exit position for {symbol}. Keeping in active tracking.")
            else:
                active_positions[symbol]["alert_count"] += 1

        # --- 2. ENTRY LOGIC (Position Capacity Check) ---
        placed_orders = []
        
        if len(active_positions) < MAX_ACTIVE_POSITIONS:
            new_candidates = [s for s in incoming_stocks if s not in active_positions]
            
            if new_candidates:
                target_symbol = new_candidates[0]
                buy_res = execute_dhan_mtf_buy(target_symbol, default_price)
                
                # CRITICAL FIX: Only add to active_positions if order execution was SUCCESSFUL!
                if buy_res.get("status") == "SUCCESS":
                    active_positions[target_symbol] = {
                        "alert_count": 1,
                        "status": "HOLD",
                        "quantity": buy_res["quantity"]
                    }
                    placed_orders.append({"symbol": target_symbol, "action": "BUY_ENTRY_MTF", "details": buy_res})
                else:
                    print(f"⚠️ Order placement failed on Dhan for {target_symbol}. Position NOT counted.")

        return {
            "status": "success",
            "active_positions_count": len(active_positions),
            "max_limit": MAX_ACTIVE_POSITIONS,
            "orders_placed": placed_orders,
            "positions_exited": exited_positions
        }

    except Exception as e:
        print(f"💥 Webhook Error: {str(e)}")
        return {"status": "error", "message": str(e)}
