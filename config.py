import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 🔐 Credentials
    APP_ID = os.getenv("APP_ID", "133069") 
    TOKEN = os.getenv("DERIV_TOKEN")
    
    # 🎯 Trading Strategy
    BASE_STAKE = 0.35
    MIN_STAKE = 0.35  
    BARRIER_UNDER = "6"
    BARRIER_OVER = "3"
    
    # 🧠 Recovery Logic
    MIN_GAP_PERCENT = 3.0
    RECO_TARGET_PROFIT = 0.05  # <--- RESTORED: Ensures recovery trades cover cost + profit
    
    # 🛡️ Risk Management (As requested: 2-hour cooldowns)
    DAILY_TARGET = 2.00       
    SESSION_TAKE_PROFIT = 1.00 
    STOP_LOSS_LIMIT = 2.00     
    
    # 📊 Markets
    MARKETS = {
        "R_10": {"name": "V10", "dp": 3},
        "R_25": {"name": "V25", "dp": 3},
        "R_50": {"name": "V50", "dp": 4},
        "R_75": {"name": "V75", "dp": 4},
        "R_100": {"name": "V100", "dp": 2}
    }
