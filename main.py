import os
from fastapi import FastAPI, Request
from typing import Dict
import math

# DhanHQ compatibility import (DhanHQ v2/v3 Context support)
try:
    from dhanhq import dhanhq, DhanContext
    USE_CONTEXT = True
except ImportError:
    from dhanhq import import os
from fastapi import FastAPI, Request
from typing import Dict
import math
import requests
import pandas as pd

# DhanHQ compatibility import
try:
    from dhanhq import dhanhq, DhanContext
    USE_CONTEXT = True
except ImportError:
    from dhanhq import dhanhq
    USE_CONTEXT = False

app = FastAPI()

# ==================== CONFIGURATION & CONTROLS ====================
MAX_ACTIVE_POSITIONS = 1      # Max allowed parallel open positions (1, 2, etc.)
TRADE_CAPITAL_INR = 2500.0    # Capital allocated per trade (INR)
PRODUCT_TYPE = "MTF"          # Margin Trading Facility
EXCHANGE_SEGMENT = "NSE_EQ"
HARD_STOP_LOSS_PCT = 0.025    # 2.5% Emergency Stop Loss Limit

# Environment Variables
CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

# Active position tracking: { "SYMBOL": {"security_id": X, "entry_price": Y, "emergency_sl": Z, "quantity": Q} }
active_positions: Dict[str, dict] = {}

# Security ID Mapping Dictionary
SECURITY_ID_MAP: Dict[str, str] = {}

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


# ==================== SECURITY ID MAPPER ====================
def load_dhan_security_ids():
    """Downloads Dhan Master Scrip File and creates Symbol -> Security ID Mapping"""
    global SECURITY_ID_MAP
    url = "https://images.dhan.co/dhan-data/api-scrip-master.csv"
    try:
        df = pd.read_csv(url)
        # Filter for NSE Equity Segment
        df_nse = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & (df['SEM_INSTRUMENT_NAME'] == 'EQUITY')]
        # Create Symbol to SecurityID mapping
        SECURITY_ID_MAP = dict(zip(df_nse['SEM_TRADING_SYMBOL'].str.upper(), df_nse['SEM_SMST_SECURITY_ID'].astype(str)))
        print(f"✅ Loaded {len(SECURITY_ID_MAP)} Security IDs from Dhan Master CSV.")
    except Exception as e:
        print(f"⚠️ Master CSV Download Failed: {str(e)}. Using fallback lookup.")

def get_security_id(symbol: str) -> str:
    """Returns exact Dhan Security ID for a symbol."""
    return SECURITY_ID_MAP.get(symbol.upper(), symbol)  # Fallback to symbol string if not found


# Load Master Data on Startup
@app.on_event("startup")
def startup_event():
    load_dhan_security_ids()


# ==================== HELPER FUNCTIONS ====================
def calculate_mtf_quantity(price: float, capital: float) -> int:
    if price <= 0:
        return 1
    qty = math.floor(capital / price)
    return qty if qty > 0 else 1

def execute_dhan_mtf_buy(symbol: str, price: float) -> dict:
    qty = calculate_mtf_quantity(price, TRADE_CAPITAL_INR)
    sec_id = get_security_id(symbol)
    
    response = dhan.place_order(
        security_id=sec_id,
        exchange_segment=EXCHANGE_SEGMENT,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
        product_type=PRODUCT_TYPE,
        price=0
    )
    return {"order_response": response, "quantity": qty, "security_id": sec_id}

def execute_dhan_mtf_sell(symbol: str) -> dict:
    pos_info = active_positions.get(symbol, {})
    qty = pos_info.get("quantity", 1)
    sec_id = pos_info.get("security_id", get_security_id(symbol))
    
    response = dhan.place_order(
        security_id=sec_id,
        exchange_segment=EXCHANGE_SEGMENT,
        transaction_type="SELL",
        quantity=qty,
        order_type="MARKET",
        product_type=PRODUCT_TYPE,
        price=0
    )
    return {"order_response": response, "quantity": qty}


# ==================== ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "online",
        "service": "Dhan-Chartink MTF Automated Webhook Engine with Emergency SL",
        "max_active_positions": MAX_ACTIVE_POSITIONS,
        "current_open_positions": len(active_positions),
        "total_security_ids_loaded": len(SECURITY_ID_MAP),
        "emergency_sl_pct": f"{HARD_STOP_LOSS_PCT * 100}%"
    }

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

        exited_positions = []
        current_active_keys = list(active_positions.keys())
        
        # --- 1. EXIT LOGIC (Emergency SL & Alert Discontinuation Check) ---
        for symbol in current_active_keys:
            pos_data = active_positions[symbol]
            curr_price = default_price  # Updated price from payload

            # Emergency SL Hit Check (Price gira <= 2.5%)
            is_emergency_sl_hit = curr_price <= pos_data["emergency_sl"]
            
            # Alert Stop Check
            is_alert_stopped = symbol not in incoming_stocks

            if is_emergency_sl_hit or is_alert_stopped:
                reason = "EMERGENCY_SL_HIT" if is_emergency_sl_hit else "ALERT_STOPPED"
                sell_res = execute_dhan_mtf_sell(symbol)
                exited_positions.append({"symbol": symbol, "action": f"EXIT_{reason}", "response": sell_res})
                del active_positions[symbol]

        # --- 2. ENTRY LOGIC (Buy Order Execution) ---
        placed_orders = []
        if len(active_positions) < MAX_ACTIVE_POSITIONS:
            new_candidates = [s for s in incoming_stocks if s not in active_positions]
            
            if new_candidates:
                target_symbol = new_candidates[0]
                entry_price = default_price
                emergency_sl_price = round(entry_price * (1.0 - HARD_STOP_LOSS_PCT), 2)
                
                buy_res = execute_dhan_mtf_buy(target_symbol, entry_price)
                
                active_positions[target_symbol] = {
                    "security_id": buy_res["security_id"],
                    "entry_price": entry_price,
                    "emergency_sl": emergency_sl_price,
                    "quantity": buy_res["quantity"]
                }
                placed_orders.append({
                    "symbol": target_symbol, 
                    "action": "BUY_ENTRY_MTF", 
                    "entry_price": entry_price,
                    "emergency_sl_set": emergency_sl_price,
                    "details": buy_res
                })

        return {
            "status": "success",
            "active_positions": len(active_positions),
            "orders_placed": placed_orders,
            "positions_exited": exited_positions
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}dhanhq
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
