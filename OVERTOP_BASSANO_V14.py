#!/usr/bin/env python3
"""
OVERTOP BASSANO V14 — INTEGRATO THREAD-SAFE
Bot gira dentro app.py con heartbeat_data sincronizzato
"""

import json
import websocket
import threading
import time
from datetime import datetime
from collections import deque, defaultdict
import logging

logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

class Capsule1Coerenza:
    def valida(self, fingerprint_wr, momentum, volatility, trend):
        if fingerprint_wr > 0.75 and momentum == "FORTE" and volatility == "BASSA" and trend == "UP":
            return True, 0.95, "COERENZA PERFETTA"
        return False, 0.10, "BLOCCO"

class Capsule2Trappola:
    def riconosci(self, confidence):
        return confidence >= 0.30, "OK" if confidence >= 0.30 else "TRAPPOLA"

class Capsule3Protezione:
    def proteggi(self, momentum, volatility, fingerprint_wr):
        return True, "OK"

class Capsule4Opportunita:
    def riconosci(self, fingerprint_wr, momentum, volatility):
        if fingerprint_wr > 0.75 and momentum == "FORTE" and volatility == "BASSA":
            return True, 0.95, "ORO"
        return False, 0.40, "NO"

class Capsule5Tattica:
    def timing(self, entry_trigger, coerenza, confidence):
        if entry_trigger and coerenza and confidence > 0.80:
            return True, 45, "PERFETTO"
        return False, 0, "NO"

class MatrimonioIntelligente:
    MARRIAGES = {
        ("FORTE", "BASSA", "UP"): {"name": "STRONG_BULL", "wr": 0.85, "duration_avg": 45, "confidence": 0.95},
        ("MEDIO", "BASSA", "UP"): {"name": "MEDIUM_BULL", "wr": 0.70, "duration_avg": 25, "confidence": 0.80},
        ("MEDIO", "MEDIA", "UP"): {"name": "CAUTIOUS_BULL", "wr": 0.60, "duration_avg": 15, "confidence": 0.65},
        ("DEBOLE", "MEDIA", "SIDEWAYS"): {"name": "WEAK_NEUTRAL", "wr": 0.45, "duration_avg": 8, "confidence": 0.40},
        ("DEBOLE", "ALTA", "DOWN"): {"name": "TRAP_REVERSAL", "wr": 0.05, "duration_avg": 2, "confidence": 0.05},
    }
    
    @staticmethod
    def get_marriage(momentum_str, volatility_str, trend_str):
        key = (momentum_str, volatility_str, trend_str)
        return MatrimonioIntelligente.MARRIAGES.get(key, {
            "name": "UNKNOWN", "wr": 0.50, "duration_avg": 12, "confidence": 0.50,
        })

class MemoriaMatrimoni:
    def __init__(self):
        self.matrimoni_trust = defaultdict(lambda: 50)
        self.matrimoni_separazione = defaultdict(bool)
        self.matrimoni_blacklist = defaultdict(int)
        self.matrimoni_divorce_permanent = set()
        self.matrimoni_wr_history = defaultdict(list)
        self.matrimoni_wins = defaultdict(int)
        self.matrimoni_losses = defaultdict(int)
    
    def get_status(self, matrimonio_name):
        if matrimonio_name in self.matrimoni_divorce_permanent:
            return False, "DIVORZIO_PERMANENTE"
        if self.matrimoni_blacklist[matrimonio_name] > 0:
            self.matrimoni_blacklist[matrimonio_name] -= 1
            return False, "SEPARAZIONE_ATTIVA"
        if self.matrimoni_trust[matrimonio_name] < 30:
            return False, "TRUST_BASSO"
        return True, "OK_ENTRA"
    
    def record_trade(self, matrimonio_name, is_win, wr_expected):
        if is_win:
            self.matrimoni_wins[matrimonio_name] += 1
            self.matrimoni_trust[matrimonio_name] += 5
        else:
            self.matrimoni_losses[matrimonio_name] += 1
            self.matrimoni_trust[matrimonio_name] -= 15
        self.matrimoni_trust[matrimonio_name] = max(0, min(100, self.matrimoni_trust[matrimonio_name]))

class ContestoAnalyzer:
    def __init__(self, window=50):
        self.prices = deque(maxlen=window)
        self.tick_count = 0
    
    def add_price(self, price):
        self.prices.append(price)
        self.tick_count += 1
        if self.tick_count % 100 == 0:
            log.info(f"[TICK] {self.tick_count} tick ricevuti")
    
    def analyze(self):
        if len(self.prices) < 10:
            return None, None, None
        
        prices = list(self.prices)
        recent = prices[-5:]
        changes = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
        consecutive_up = sum(1 for c in changes if c > 0)
        avg_change = sum(changes) / len(changes) if changes else 0
        
        if consecutive_up >= 4 and avg_change > 0.001:
            momentum = "FORTE"
        elif consecutive_up >= 2 or avg_change > 0.0005:
            momentum = "MEDIO"
        else:
            momentum = "DEBOLE"
        
        recent_20 = prices[-20:]
        changes_20 = [abs(recent_20[i+1] - recent_20[i]) for i in range(len(recent_20)-1)]
        max_change = max(changes_20) if changes_20 else 0
        avg_change_20 = sum(changes_20) / len(changes_20) if changes_20 else 0
        
        if max_change > 0.01 or avg_change_20 > 0.005:
            volatility = "ALTA"
        elif avg_change_20 > 0.002:
            volatility = "MEDIA"
        else:
            volatility = "BASSA"
        
        start = prices[0]
        end = prices[-1]
        change_pct = (end - start) / start * 100 if start != 0 else 0
        
        trend = "UP" if change_pct > 0.3 else ("DOWN" if change_pct < -0.3 else "SIDEWAYS")
        return momentum, volatility, trend

class OvertopBassanoV14Memoria:
    def __init__(self, heartbeat_data=None, db_execute=None, heartbeat_lock=None):
        self.symbol = "BTCUSDC"
        self.ws_url = "wss://stream.binance.com:9443/ws/btcusdc@aggTrade"
        
        self.heartbeat_data = heartbeat_data if heartbeat_data is not None else {}
        self.heartbeat_lock = heartbeat_lock
        self.db_execute = db_execute
        
        self.capital = 10116.48
        self.total_trades = 894
        self.wins = 0
        self.losses = 0
        
        self.analyzer = ContestoAnalyzer(window=50)
        self.memoria = MemoriaMatrimoni()
        self.capsule1 = Capsule1Coerenza()
        self.capsule2 = Capsule2Trappola()
        self.capsule3 = Capsule3Protezione()
        self.capsule4 = Capsule4Opportunita()
        self.capsule5 = Capsule5Tattica()
        
        self.trade_open = None
        self.entry_time = None
        self.max_price = None
        self.fingerprint_wr = 0.72
        self.heartbeat_interval = 30
        self.last_heartbeat = time.time()
        self.ws = None
        
        log.info("═" * 80)
        log.info("OVERTOP BASSANO V14 — INTEGRATO THREAD-SAFE")
        log.info("═" * 80)
    
    def connect_binance(self):
        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                price = float(data.get('p', 0))
                if price > 0:
                    self.analyzer.add_price(price)
                    self._process_tick(price)
            except Exception as e:
                log.error(f"[ERRORE] {e}")
        
        def on_open(ws):
            log.info(f"[WS_OPEN] ✅ Connesso a Binance")
        
        self.ws = websocket.WebSocketApp(self.ws_url, on_message=on_message, on_open=on_open)
        threading.Thread(target=self.ws.run_forever, daemon=True).start()
    
    def _process_tick(self, price):
        if time.time() - self.last_heartbeat > self.heartbeat_interval:
            self._update_heartbeat()
            self.last_heartbeat = time.time()
        
        contesto = self.analyzer.analyze()
        if not contesto[0]:
            return
        
        momentum, volatility, trend = contesto
        
        if self.analyzer.tick_count % 200 == 0:
            log.info(f"[CONTESTO] {momentum}_{volatility}_{trend}")
        
        if self.trade_open:
            self._evaluate_exit(price, momentum, volatility)
        else:
            self._evaluate_entry(price, momentum, volatility, trend)
    
    def _evaluate_entry(self, price, momentum, volatility, trend):
        matrimonio = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = matrimonio["name"]
        confidence = matrimonio["confidence"]
        
        can_enter, status = self.memoria.get_status(matrimonio_name)
        if not can_enter:
            return
        
        allow_1, conf_1, reason_1 = self.capsule1.valida(self.fingerprint_wr, momentum, volatility, trend)
        if not allow_1:
            return
        
        allow_2, reason_2 = self.capsule2.riconosci(confidence)
        if not allow_2:
            return
        
        allow_3, reason_3 = self.capsule3.proteggi(momentum, volatility, self.fingerprint_wr)
        if not allow_3:
            return
        
        allow_4, conf_4, reason_4 = self.capsule4.riconosci(self.fingerprint_wr, momentum, volatility)
        allow_5, duration_min, reason_5 = self.capsule5.timing(True, allow_1, conf_1)
        if not allow_5:
            return
        
        log.info(f"[ENTRY] 🚀 {matrimonio_name}")
        
        self.trade_open = {
            "price_entry": price,
            "matrimonio": matrimonio_name,
            "duration_avg": matrimonio["duration_avg"],
        }
        self.max_price = price
        self.entry_time = time.time()
        self.total_trades += 1
    
    def _evaluate_exit(self, price, momentum, volatility):
        if price > self.max_price:
            self.max_price = price
        
        duration = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]
        
        pnl = price - self.trade_open["price_entry"]
        is_win = pnl > 0
        
        should_exit = duration > duration_avg * 3
        
        if not should_exit:
            return
        
        matrimonio_name = self.trade_open["matrimonio"]
        
        if is_win:
            self.wins += 1
        else:
            self.losses += 1
        
        self.capital += pnl
        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        
        log.info(f"[EXIT] 🏁 {matrimonio_name} | PnL: ${pnl:+.2f} | WR: {wr:.1f}%")
        
        self._save_trade_log(pnl, is_win)
        self._update_heartbeat()
        
        self.trade_open = None
        self.entry_time = None
    
    def _save_trade_log(self, pnl, is_win):
        if self.db_execute:
            try:
                self.db_execute("""
                    INSERT INTO trades 
                    (event_type, asset, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    "EXIT",
                    self.symbol,
                    pnl,
                    "LONG",
                    "Trade completato",
                    json.dumps({"is_win": is_win})
                ))
            except Exception as e:
                log.warning(f"[DB_ERROR] {e}")
    
    def _update_heartbeat(self):
        """Aggiorna heartbeat_data CON LOCK (THREAD-SAFE)"""
        if self.heartbeat_data is not None and self.heartbeat_lock is not None:
            try:
                self.heartbeat_lock.acquire()
                self.heartbeat_data["status"] = "RUNNING"
                self.heartbeat_data["capital"] = self.capital
                self.heartbeat_data["trades"] = self.total_trades
                self.heartbeat_data["wins"] = self.wins
                self.heartbeat_data["wr"] = self.wins / max(1, self.total_trades)
                self.heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
                log.debug("[HEARTBEAT] Aggiornato (thread-safe)")
            finally:
                self.heartbeat_lock.release()
    
    def run(self):
        log.info("[START] Avviando bot...")
        self.connect_binance()
        log.info("[INFO] Bot aspetta tick da Binance...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("[STOP] Bot fermato")
