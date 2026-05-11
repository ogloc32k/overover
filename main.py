import asyncio, json, websockets, time, sys, os
from collections import deque
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from config import Config

# --- RENDER KEEP-ALIVE TRICK ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Online"

def run_heartbeat():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run_heartbeat)
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
        return pcts, max(pcts, key=pcts.get), min(pcts, key=pcts.get)

    def is_anti_digit(self, king):
        if self.last_digit is None: return False
        return (king <= 4 and self.last_digit >= 6) or (king >= 5 and self.last_digit <= 3)

class SniperBot:
    def __init__(self):
        self.markets = {s: MarketIntel(s) for s in Config.MARKETS.keys()}
        self.balance, self.start_bal, self.peak_bal = 0.0, 0.0, 0.0
        self.daily_start_bal = 0.0
        self.is_trading = False
        self.mode = "DIFFERS" 
        self.logs = deque(maxlen=10)
        self.lock_until = None
        self.current_day = datetime.now().date()

    def log(self, msg): self.logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def check_limits(self):
        now = datetime.now()
        
        # 1. Midnight Reset Logic
        if now.date() > self.current_day:
            self.log("🕛 Midnight Reset: New Daily Target active.")
            self.current_day = now.date()
            self.daily_start_bal = self.balance
            self.lock_until = None
            return True

        # 2. Check if bot is in a "Time-Out"
        if self.lock_until and now < self.lock_until:
            return False

        # 3. Check Daily Profit/Loss
        daily_profit = self.balance - self.daily_start_bal
        if daily_profit >= Config.DAILY_TARGET:
            self.log(f"🏆 Daily Target ${Config.DAILY_TARGET} Hit! Sleeping until Midnight.")
            self.lock_until = now.replace(hour=23, minute=59, second=59)
            return False
        if daily_profit <= -Config.DAILY_STOP_LOSS:
            self.log(f"🛑 Daily Stop Loss -${Config.DAILY_STOP_LOSS} Hit! Locked.")
            self.lock_until = now.replace(hour=23, minute=59, second=59)
            return False

        return True

    def calculate_stake(self):
        if self.mode == "DIFFERS": return Config.BASE_STAKE
        deficit = self.peak_bal - self.balance
        if deficit <= 0: return Config.BASE_STAKE
        
        target_recovery = deficit if self.balance >= self.start_bal else deficit * 0.50
        req_stake = round(target_recovery / 0.60, 2)
        return min(max(req_stake, Config.MIN_STAKE), round(self.balance * 0.15, 2))

    async def trade(self, ws, symbol, c_type, barrier=None):
        if not self.is_trading and self.check_limits():
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
        while True:
            sys.stdout.write("\033[H")
            stake = self.calculate_stake()
            daily_p = self.balance - self.daily_start_bal
            
            status = "🟢 ACTIVE"
            if self.lock_until and datetime.now() < self.lock_until:
                status = f"🔴 LOCKED UNTIL {self.lock_until.strftime('%H:%M')}"

            out = [
                f"💰 BAL: ${self.balance:,.2f} | DAILY: ${daily_p:+.2f} | STATUS: {status}",
                f"🎯 MODE: {self.mode:<10} | NEXT STAKE: ${stake:.2f} | PEAK: ${self.peak_bal:.2f}",
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
                if not self.is_trading and status == "🟢 ACTIVE":
                    if self.mode == "DIFFERS" and m.last_digit == king and m.prev_digit == king:
                        signal = "🎯 DIFFERS"
                    elif self.mode == "RECOVERY" and m.is_anti_digit(king) and gap >= Config.MIN_GAP_PERCENT:
                        signal = "🔥 RECOVER"
                out.append(f"{Config.MARKETS[s]['name']:<10} | {m.last_digit} | {king} ({pcts[king]:.1f}%) | {slave} ({pcts[slave]:.1f}%) | {signal}")
            
            out.append("━" * 105 + "\n📋 LOGS:")
            for l in self.logs: out.append(f"   {l}")
            sys.stdout.write("\n".join(out) + "\033[J\n")
            await asyncio.sleep(0.5)

    async def run(self):
        keep_alive()
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
                                self.daily_start_bal = self.balance
                                self.peak_bal = self.balance

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
                                self.mode = "DIFFERS" if (is_win and self.balance >= self.start_bal) or self.balance >= self.peak_bal else "RECOVERY"
                                
                                # --- Session Management ---
                                if not is_win:
                                    self.log(f"❌ Loss! Locking for 2 hours to cool down.")
                                    self.lock_until = datetime.now() + timedelta(hours=2)
                                elif profit >= Config.SESSION_TAKE_PROFIT:
                                    self.log(f"✅ Session Win ${profit}! 1-hour break.")
                                    self.lock_until = datetime.now() + timedelta(hours=1)
                                
                                self.log(f"{'✅' if is_win else '❌'} Result: ${profit:+.2f} | Mode: {self.mode}")
                                self.is_trading = False 

                        if "tick" in data and not self.is_trading and self.check_limits():
                            t_sym = data["tick"]["symbol"]
                            self.markets[t_sym].update(data["tick"]["quote"])
                            
                            # (Market Selection logic remains exactly same as your original)
                            for s_code, m_intel in self.markets.items():
                                analysis = m_intel.get_analysis()
                                if not analysis: continue
                                pcts, king, slave = analysis
                                gap = pcts[king] - pcts[slave]

                                if self.mode == "DIFFERS" and m_intel.last_digit == king and m_intel.prev_digit == king:
                                    await self.trade(ws, s_code, "DIGITDIFF", barrier=slave)
                                    break
                                elif self.mode == "RECOVERY" and m_intel.is_anti_digit(king) and gap >= Config.MIN_GAP_PERCENT:
                                    c_type = "DIGITUNDER" if king <= 4 else "DIGITOVER"
                                    barrier = Config.BARRIER_UNDER if king <= 4 else Config.BARRIER_OVER
                                    await self.trade(ws, s_code, c_type, barrier=barrier)
                                    break

            except Exception as e:
                self.log(f"📡 Connection Error: {e}")
                self.is_trading = False 
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(SniperBot().run())