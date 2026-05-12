import asyncio, json, websockets, time, sys, os
from collections import deque
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from config import Config

# --- WEB DASHBOARD & HEARTBEAT ---
app = Flask('')
bot_instance = None # Global reference so Flask can read the bot's data

@app.route('/')
def home():
    if not bot_instance:
        return "<body style='background:#121212; color:white; font-family:monospace; padding:20px;'>Bot is starting up... Refresh in a few seconds.</body>"
    
    # Calculate values
    daily_pnl = bot_instance.balance - bot_instance.daily_start_bal
    lock_status = "🟢 ACTIVE" if not bot_instance.lock_until or datetime.now() >= bot_instance.lock_until else f"🔴 LOCKED UNTIL {bot_instance.lock_until.strftime('%H:%M:%S')}"
    
    # Build a clean HTML page
    html = f"""
    <html>
    <head>
        <title>SniperBot Dashboard</title>
        <meta http-equiv="refresh" content="2"> <style>
            body {{ background-color: #0d1117; color: #c9d1d9; font-family: 'Courier New', Courier, monospace; padding: 20px; }}
            h2 {{ color: #58a6ff; margin-bottom: 5px; }}
            .stats {{ background: #161b22; padding: 15px; border-radius: 8px; border: 1px solid #30363d; margin-bottom: 20px; font-size: 16px; line-height: 1.6; }}
            .highlight {{ color: #7ee787; font-weight: bold; }}
            .warning {{ color: #ff7b72; font-weight: bold; }}
            .logs-container {{ background: #010409; padding: 15px; border-radius: 8px; border: 1px solid #30363d; height: 400px; overflow-y: auto; }}
            .log-line {{ margin: 5px 0; border-bottom: 1px dashed #21262d; padding-bottom: 5px; }}
        </style>
    </head>
    <body>
        <h2>🎯 SniperBot Live Dashboard</h2>
        <div class="stats">
            <div>💰 <b>Balance:</b> ${bot_instance.balance:,.2f}</div>
            <div>📈 <b>Daily P/L:</b> <span class="{'highlight' if daily_pnl >= 0 else 'warning'}">${daily_pnl:+.2f}</span></div>
            <div>🎯 <b>Mode:</b> {bot_instance.mode}</div>
            <div>⏱️ <b>Status:</b> {lock_status}</div>
        </div>
        
        <h3>📋 Recent Logs</h3>
        <div class="logs-container">
    """
    
    # Add logs in reverse so newest is at the top
    for log in reversed(list(bot_instance.logs)):
        html += f"<div class='log-line'>{log}</div>"
        
    html += """
        </div>
    </body>
    </html>
    """
    return html

def run_heartbeat():
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_heartbeat)
    t.daemon = True
    t.start()


class MarketIntel:
    def __init__(self, symbol):
        self.symbol = symbol
        self.ticks = deque(maxlen=1000)
        self.counts = {i: 0 for i in range(10)}
        self.last_digit, self.prev_digit = None, None
        self.dp = Config.MARKETS[symbol]["dp"]

    def update(self, price):
        digit = int(("{:." + str(self.dp) + "f}").format(float(price))[-1])
        if len(self.ticks) == 1000:
            self.counts[self.ticks[0]] -= 1
        self.prev_digit = self.last_digit
        self.last_digit = digit
        self.ticks.append(digit)
        self.counts[digit] += 1

    def get_analysis(self):
        if len(self.ticks) < 1000: return None
        pcts = {i: round(self.counts[i] / 10.0, 1) for i in range(10)}
        if sum(self.counts.values()) != 1000:
            self.counts = {i: 0 for i in range(10)}
            for d in self.ticks: self.counts[d] += 1
            pcts = {i: round(self.counts[i] / 10.0, 1) for i in range(10)}
        return pcts, max(pcts, key=pcts.get), min(pcts, key=pcts.get)

    def is_anti_digit(self, king):
        if self.last_digit is None: return False
        return (king <= 4 and self.last_digit >= 6) or (king >= 5 and self.last_digit <= 3)

class SniperBot:
    def __init__(self):
        global bot_instance
        bot_instance = self  # Give Flask access to this specific bot instance
        self.markets = {s: MarketIntel(s) for s in Config.MARKETS.keys()}
        self.balance, self.start_bal, self.peak_bal = 0.0, 0.0, 0.0
        self.daily_start_bal = 0.0
        self.is_trading = False
        self.mode = "DIFFERS" 
        self.logs = deque(maxlen=20) # Increased to 20 so the web page shows more history
        self.lock_until = None
        self.current_day = datetime.now().date()

    def log(self, msg): self.logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def check_limits(self):
        now = datetime.now()
        if now.date() > self.current_day:
            self.log("🕛 Midnight Reset: Refreshing Targets.")
            self.current_day = now.date()
            self.daily_start_bal = self.balance
            self.lock_until = None
            return True

        if self.lock_until and now < self.lock_until:
            return False

        daily_profit = self.balance - self.daily_start_bal
        if daily_profit >= Config.DAILY_TARGET:
            self.lock_until = now.replace(hour=23, minute=59, second=59)
            return False
        return True

    def calculate_stake(self):
        if self.mode == "DIFFERS":
            return Config.BASE_STAKE
        
        deficit = self.peak_bal - self.balance
        if deficit <= 0: return Config.BASE_STAKE
        
        if self.balance >= self.start_bal:
            target_recovery = deficit 
            multiplier = 0.60 
        else:
            target_recovery = deficit * 0.50
            multiplier = 0.60

        req_stake = round(target_recovery / multiplier, 2)
        max_risk = round(self.balance * 0.15, 2)
        
        return min(max(req_stake, Config.MIN_STAKE), max_risk)

    async def trade(self, ws, symbol, c_type, barrier=None):
        if not self.is_trading: 
             self.is_trading = True
        
        stake = self.calculate_stake()
        payload = {
            "buy": 1, "price": stake,
            "parameters": {
                "amount": stake, "basis": "stake", "contract_type": c_type,
                "currency": "USD", "duration": 1, "duration_unit": "t", "symbol": symbol
            }
        }
        if barrier is not None: payload["parameters"]["barrier"] = str(barrier)
        await ws.send(json.dumps(payload))

    async def dashboard(self):
        # We keep the terminal dashboard running too, just in case you look at it locally
        while True:
            sys.stdout.write("\033[H")
            stake = self.calculate_stake()
            strat = "1-STEP" if self.balance >= self.start_bal else "2-STEP"
            status_tag = f"\033[92m[PEAK]\033[0m" if self.mode == "DIFFERS" else f"\033[91m[RECO {strat}]\033[0m"
            lock_status = "🟢 ACTIVE" if not self.lock_until or datetime.now() >= self.lock_until else f"🔴 LOCKED UNTIL {self.lock_until.strftime('%H:%M:%S')}"
            
            out = [
                f"💰 BAL: ${self.balance:,.2f} | DAILY: ${(self.balance - self.daily_start_bal):+.2f} | {lock_status}",
                f"🎯 MODE: {self.mode:<10} | NEXT STAKE: ${stake:.2f} | {status_tag}",
                "━" * 105,
                f"{'MARKET':<10} | L | {'KING (1000t)':<15} | {'SLAVE (1000t)':<15} | ACTION",
                "━" * 105
            ]
            for s, m in self.markets.items():
                analysis = m.get_analysis()
                if not analysis:
                    out.append(f"{Config.MARKETS[s]['name']:<10} | SYNCING {len(m.ticks)}/1000")
                    continue
                pcts, king, slave = analysis
                gap = pcts[king] - pcts[slave]
                signal = "⚪ WAIT"
                if not self.is_trading and (not self.lock_until or datetime.now() >= self.lock_until):
                    if self.mode == "DIFFERS" and m.last_digit == king and m.prev_digit == king and pcts[king] >= Config.KING_MIN_PCT:
                        if slave == 0: signal = "🎯 OVER 0"
                        elif slave == 9: signal = "🎯 UNDER 9"
                        else: signal = "🎯 DIFFERS"
                    elif self.mode == "RECOVERY" and m.is_anti_digit(king) and gap >= Config.MIN_GAP_PERCENT and pcts[king] >= Config.KING_MIN_PCT:
                        signal = "🔥 RECOVER"
                out.append(f"{Config.MARKETS[s]['name']:<10} | {m.last_digit} | "
                           f"{king} ({pcts[king]:.1f}%) | {slave} ({pcts[slave]:.1f}%) | {signal}")
            
            out.append("━" * 105 + "\n📋 LOGS (Check your Render Web URL for clean view!):")
            sys.stdout.write("\n".join(out) + "\033[J\n")
            await asyncio.sleep(0.4)

    async def run(self):
        keep_alive()
        os.system('clear' if os.name == 'posix' else 'cls')
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={Config.APP_ID}"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20) as ws:
                    await ws.send(json.dumps({"authorize": Config.TOKEN}))
                    asyncio.create_task(self.dashboard())
                    while True:
                        data = json.loads(await ws.recv())
                        
                        if "authorize" in data:
                            await ws.send(json.dumps({"balance": 1, "subscribe": 1}))
                            await ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))
                            for s in Config.MARKETS.keys():
                                await ws.send(json.dumps({"ticks_history": s, "count": 1000, "end": "latest"}))
                        
                        if "balance" in data:
                            self.balance = float(data["balance"]["balance"])
                            if self.start_bal == 0: 
                                self.start_bal = self.balance
                                self.peak_bal = self.balance
                                self.daily_start_bal = self.balance

                        if "history" in data:
                            sym = data["echo_req"]["ticks_history"]
                            for p in data["history"]["prices"]: self.markets[sym].update(p)
                            await ws.send(json.dumps({"ticks": sym}))

                        if "proposal_open_contract" in data:
                            c = data["proposal_open_contract"]
                            if c and c.get("is_sold"):
                                is_win = (c["status"] == "won")
                                profit = float(c.get('profit', 0))
                                if self.balance > self.peak_bal: self.peak_bal = self.balance
                                
                                # Mode Switch
                                if (is_win and self.balance >= self.start_bal) or self.balance >= self.peak_bal:
                                    self.mode = "DIFFERS"
                                else:
                                    self.mode = "RECOVERY"
                                
                                # Cooldowns
                                now = datetime.now()
                                daily_p = self.balance - self.daily_start_bal
                                if is_win and profit >= Config.SESSION_TAKE_PROFIT:
                                    self.log(f"✅ Session Hit! 2h Lock.")
                                    self.lock_until = now + timedelta(hours=2)
                                elif not is_win and daily_p <= -Config.STOP_LOSS_LIMIT:
                                    self.log(f"⚠️ Stop Loss! 2h Lock.")
                                    self.lock_until = now + timedelta(hours=2)

                                self.log(f"{'✅' if is_win else '❌'} {c.get('contract_type')} | ${profit:+.2f} | MODE: {self.mode}")
                                self.is_trading = False 

                        if "tick" in data and not self.is_trading and self.check_limits():
                            t_sym = data["tick"]["symbol"]
                            self.markets[t_sym].update(data["tick"]["quote"])
                            
                            best_market = None
                            max_gap = -1.0

                            for s_code, m_intel in self.markets.items():
                                analysis = m_intel.get_analysis()
                                if not analysis: continue
                                pcts, king, slave = analysis
                                gap = pcts[king] - pcts[slave]

                                # 🛡️ Sniping Logic
                                if self.mode == "DIFFERS":
                                    if m_intel.last_digit == king and m_intel.prev_digit == king and pcts[king] >= Config.KING_MIN_PCT:
                                        if slave == 0:
                                            best_market = (s_code, "DIGITOVER", "0")
                                        elif slave == 9:
                                            best_market = (s_code, "DIGITUNDER", "9")
                                        else:
                                            best_market = (s_code, "DIGITDIFF", str(slave))
                                        break 

                                elif self.mode == "RECOVERY":
                                    if m_intel.is_anti_digit(king) and gap >= Config.MIN_GAP_PERCENT and pcts[king] >= Config.KING_MIN_PCT:
                                        if gap > max_gap:
                                            max_gap = gap
                                            c_type = "DIGITUNDER" if king <= 4 else "DIGITOVER"
                                            barrier = Config.BARRIER_UNDER if king <= 4 else Config.BARRIER_OVER
                                            best_market = (s_code, c_type, barrier)

                            if best_market and not self.is_trading:
                                self.is_trading = True 
                                s_id, c_id, b_id = best_market
                                asyncio.create_task(self.trade(ws, s_id, c_id, barrier=b_id))

            except Exception as e:
                self.log(f"📡 Error: {e}")
                self.is_trading = False 
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(SniperBot().run())
