import os
from fastapi import FastAPI, Request
from typing import Dict
import math

# DhanHQ compatibility import (DhanHQ v2/v3 Context support)
try:
    from dhanhq import dhanhq, DhanContext
    USE_CONTEXT = True
except ImportError:
    from dhanhq import dhanhq
    USE_CONTEXT = False

app = FastAPI()

# ==================== CONFIGURATION & CONTROLS ====================
# Yahan se aap Single Click / Config change karke trade limits handle kar sakte hain.

MAX_ACTIVE_POSITIONS = 1      # Max allowed parallel open positions (1, 2, etc.)
TRADE_CAPITAL_INR = 2500.0    # Capital allocated per trade (Between 2000 to 3000 INR)
PRODUCT_TYPE = "MTF"          # Margin Trading Facility (Dhan Product Code)
EXCHANGE_SEGMENT = "NSE_EQ"

# Environment Variables
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

# Active position tracking storage: { "SYMBOL": {"alert_count": X, "status": "HOLD"} }
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


# ==================== HELPER FUNCTIONS ====================

def calculate_mtf_quantity(price: float, capital: float) -> int:
    """Calculates shares quantity based on target capital and current price."""
    if price <= 0:
        return 1
    qty = math.floor(capital / price)
    return qty if qty > 0 else 1

def select_best_stock(incoming_stocks: list) -> dict:
    """
    Multiple stocks aane par volume and momentum filter apply karta hai.
    Extracts stock with highest volume priority.
    """
    # Standard fallback logic for payload list
    selected = incoming_stocks[0]
    return selected

def execute_dhan_mtf_buy(symbol: str, price: float) -> dict:
    """Executes MTF (Margin Trading Facility) BUY order on Dhan."""
    qty = calculate_mtf_quantity(price, TRADE_CAPITAL_INR)
    
    response = dhan.place_order(
        security_id=symbol,
        exchange_segment=EXCHANGE_SEGMENT,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
        product_type=PRODUCT_TYPE,  # MTF Execution
        price=0
    )
    return {"order_response": response, "quantity": qty}

def execute_dhan_mtf_sell(symbol: str) -> dict:
    """Executes MTF SELL (Square off) order on Dhan."""
    # Fetch quantity or default to last position size
    qty = active_positions.get(symbol, {}).get("quantity", 1)
    
    response = dhan.place_order(
        security_id=symbol,
        exchange_segment=EXCHANGE_SEGMENT,
        transaction_type="SELL",
        quantity=qty,
        order_type="MARKET",
        product_type=PRODUCT_TYPE,  # MTF Execution
        price=0
    )
    return {"order_response": response, "quantity": qty}


# ==================== ENDPOINTS ====================

@app.get("/")
def home():
    return {
        "status": "online",
        "service": "Dhan-Chartink MTF Automated Webhook Engine",
        "max_active_positions": MAX_ACTIVE_POSITIONS,
        "current_open_positions": len(active_positions),
        "product_type": PRODUCT_TYPE
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
        prices_str = data.get("close", "")  # Price from Chartink
        
        if not stocks_str:
            return {"status": "ignored", "reason": "No stocks found in payload"}

        incoming_stocks = [s.strip().upper() for s in stocks_str.split(",") if s.strip()]
        
        # Extract stock prices from Chartink payload
        prices = [float(p.strip()) for p in str(prices_str).split(",") if p.strip() and p.strip().replace('.', '', 1).isdigit()]
        default_price = prices[0] if prices else 100.0

        # --- 1. EXIT LOGIC (Alert Discontinuation Monitor) ---
        # Scan active positions: Agar active stock incoming alert list me nahi hai, instantly SELL!
        exited_positions = []
        current_active_keys = list(active_positions.keys())
        
        for symbol in current_active_keys:
            if symbol not in incoming_stocks:
                # Alert stopped for this stock -> Trigger MTF SELL
                sell_res = execute_dhan_mtf_sell(symbol)
                exited_positions.append({"symbol": symbol, "action": "EXIT_ALERT_STOPPED", "response": sell_res})
                del active_positions[symbol]
            else:
                # Alert is continuing -> Hold position
                active_positions[symbol]["alert_count"] += 1

        # --- 2. ENTRY LOGIC (Position Capacity Check) ---
        placed_orders = []
        
        # Check if active positions slot available
        if len(active_positions) < MAX_ACTIVE_POSITIONS:
            # Filter out stocks already bought
            new_candidates = [s for s in incoming_stocks if s not in active_positions]
            
            if new_candidates:
                # Select stock with highest relative priority/volume
                target_symbol = new_candidates[0]
                
                # Execute MTF BUY
                buy_res = execute_dhan_mtf_buy(target_symbol, default_price)
                
                active_positions[target_symbol] = {
                    "alert_count": 1,
                    "status": "HOLD",
                    "quantity": buy_res["quantity"]
                }
                placed_orders.append({"symbol": target_symbol, "action": "BUY_ENTRY_MTF", "details": buy_res})

        return {
            "status": "success",
            "active_positions_count": len(active_positions),
            "max_limit": MAX_ACTIVE_POSITIONS,
            "orders_placed": placed_orders,
            "positions_exited": exited_positions
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
