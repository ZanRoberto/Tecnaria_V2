#!/usr/bin/env python3
"""
OVERTOP BASSANO V14 — INTEGRATO IN APP.PY (SENZA HTTP ESTERNO)
═══════════════════════════════════════════════════════════════════════════════
Questo bot gira DENTRO app.py come thread.
NON manda heartbeat via HTTP.
Scrive DIRETTAMENTE a heartbeat_data passato da app.py.
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import websocket
import threading
import time
from datetime import datetime
from collections import deque, defaultdict
import logging
import sys

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING DETTAGLIATO
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# LE 5 CAPSULE
# ═══════════════════════════════════════════════════════════════════════════

class Capsule1Coerenza:
    def valida(self, fingerprint_wr, momentum, volatility, trend):
        if fingerprint_wr > 0.75:
            if momentum == "FORTE" and volatility == "BASSA" and trend == "UP":
                return True, 0.95, "COERENZA PERFETTA"
            elif momentum == "DEBOLE" or volatility == "ALTA" or trend != "UP":
                return False, 0.25, f"INCOERENZA"
        return False, 0.10, "BLOCCO"

class Capsule2Trappola:
    def riconosci(self, confidence):
        if confidence < 0.30:
            return False, "TRAPPOLA"
        elif confidence < 0.50:
            return False, "INCERTO"
        else:
            return True, "OK"

class Capsule3Protezione:
    def proteggi(self, momentum, volatility, fingerprint_wr):
        if momentum == "DEBOLE" and volatility == "ALTA":
            if fingerprint_wr > 0.70:
                return True, "OK"
            return False, "PROTETTO"
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

# ═══════════════════════════════════════════════════════════════════════════
# MATRIMONI
# ═══════════════════════════════════════════════════════════════════════════

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
            "name": "UNKNOWN",
            "wr": 0.50,
            "duration_avg": 12,
            "confidence": 0.50,
        })

# ═══════════════════════════════════════════════════════════════════════════
# MEMORIA MATRIMONI
# ═══════════════════════════════════════════════════════════════════════════

class MemoriaMatrimoni:
    """Gestisce la memoria dei matrimoni: trust, separazioni, divorzi"""
    
    def __init__(self):
        self.matrimoni_trust = defaultdict(lambda: 50)
        self.matrimoni_separazione = defaultdict(bool)
        self.matrimoni_blacklist = defaultdict(int)
        self.matrimoni_divorce_permanent = set()
        self.matrimoni_wr_history = defaultdict(list)
        self.matrimoni_wins = defaultdict(int)
        self.matrimoni_losses = defaultdict(int)
    
    def get_status(self, matrimonio_name):
        """Controlla stato matrimonio prima di entrare"""
        
        if matrimonio_name in self.matrimoni_divorce_permanent:
            return False, "DIVORZIO_PERMANENTE"
        
        if self.matrimoni_blacklist[matrimonio_name] > 0:
            self.matrimoni_blacklist[matrimonio_name] -= 1
            return False, "SEPARAZIONE_ATTIVA"
        
        if self.matrimoni_trust[matrimonio_name] < 30:
            return False, "TRUST_BASSO"
        
        return True, "OK_ENTRA"
    
    def record_trade(self, matrimonio_name, is_win, wr_expected):
        """Registra un trade per monitorare WR"""
        
        if is_win:
            self.matrimoni_wins[matrimonio_name] += 1
            self.matrimoni_trust[matrimonio_name] += 5
        else:
            self.matrimoni_losses[matrimonio_name] += 1
            self.matrimoni_trust[matrimonio_name] -= 15
        
        self.matrimoni_trust[matrimonio_name] = max(0, min(100, self.matrimoni_trust[matrimonio_name]))
        
        total = self.matrimoni_wins[matrimonio_name] + self.matrimoni_losses[matrimonio_name]
        if total > 0:
            wr_reale = self.matrimoni_wins[matrimonio_name] / total
            self.matrimoni_wr_history[matrimonio_name].append(wr_reale)
            
            if len(self.matrimoni_wr_history[matrimonio_name]) >= 10:
                recent_wr = sum(self.matrimoni_wr_history[matrimonio_name][-10:]) / 10
                
                if recent_wr < wr_expected * 0.6:
                    if matrimonio_name in self.matrimoni_separazione and self.matrimoni_separazione[matrimonio_name]:
                        self.matrimoni_divorce_permanent.add(matrimonio_name)
                        self.matrimoni_trust[matrimonio_name] = 0
                        log.warning(f"[DIVORZIO] {matrimonio_name} - PERMANENTE (WR={recent_wr:.1%})")
                    else:
                        self.matrimoni_separazione[matrimonio_name] = True
                        self.matrimoni_blacklist[matrimonio_name] = 50
                        self.matrimoni_trust[matrimonio_name] -= 30
                        log.warning(f"[SEPARAZIONE] {matrimonio_name} - Blacklist 50 trade (WR={recent_wr:.1%})")

# ═══════════════════════════════════════════════════════════════════════════
# ANALIZZATORE
# ═══════════════════════════════════════════════════════════════════════════

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
        avg_change = sum(changes) / len(changes)
        
        if consecutive_up >= 4 and avg_change > 0.001:
            momentum = "FORTE"
        elif consecutive_up >= 2 or avg_change > 0.0005:
            momentum = "MEDIO"
        else:
            momentum = "DEBOLE"
        
        recent_20 = prices[-20:]
        changes_20 = [abs(recent_20[i+1] - recent_20[i]) for i in range(len(recent_20)-1)]
        max_change = max(changes_20)
        avg_change_20 = sum(changes_20) / len(changes_20)
        
        if max_change > 0.01 or avg_change_20 > 0.005:
            volatility = "ALTA"
        elif avg_change_20 > 0.002:
            volatility = "MEDIA"
        else:
            volatility = "BASSA"
        
        start = prices[0]
        end = prices[-1]
        change_pct = (end - start) / start * 100
        
        if change_pct > 0.3:
            trend = "UP"
        elif change_pct < -0.3:
            trend = "DOWN"
        else:
            trend = "SIDEWAYS"
        
        return momentum, volatility, trend

# ═══════════════════════════════════════════════════════════════════════════
# BOT INTEGRATO (NO HTTP ESTERNO)
# ═══════════════════════════════════════════════════════════════════════════

class OvertopBassanoV14Memoria:
    def __init__(self, heartbeat_data=None, db_execute=None, heartbeat_lock=None):
        self.symbol = "BTCUSDC"
        self.ws_url = "wss://stream.binance.com:9443/ws/btcusdc@aggTrade"
        
        # RICEVE heartbeat_data e db_execute da app.py
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
        self.current_matrimonio = None
        
        self.fingerprint_wr = 0.72
        self.heartbeat_interval = 30
        self.last_heartbeat = time.time()
        
        self.ws = None
        
        log.info("═" * 80)
        log.info("OVERTOP BASSANO V14 — INTEGRATO IN APP.PY (NO HTTP)")
        log.info("═" * 80)
        log.info("[MEMORIA] Sistema attivato:")
        log.info("  ├─ Matrimoni: intelligenti")
        log.info("  ├─ Separazioni: 50 trade recovery")
        log.info("  ├─ Riconciliazioni: con cautela")
        log.info("  └─ Divorzi: permanenti")
    
    def connect_binance(self):
        """Connessione a Binance"""
        
        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                price = float(data.get('p', 0))
                
                if price > 0:
                    self.analyzer.add_price(price)
                    self._process_tick(price)
            except Exception as e:
                log.error(f"[ERRORE] {e}")
        
        def on_error(ws, error):
            log.error(f"[WS_ERROR] {error}")
        
        def on_close(ws, close_status_code, close_msg):
            log.warning("[WS_CLOSE] Websocket chiuso, tentando riconnessione...")
        
        def on_open(ws):
            log.info(f"[WS_OPEN] ✅ Connesso a Binance")
            log.info(f"[WS_INFO] Aspettando tick di prezzo...")
        
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        log.info("[WS_START] Avviando WebSocket thread...")
        threading.Thread(target=self.ws.run_forever, daemon=True).start()
    
    def _process_tick(self, price):
        """Processa ogni tick"""
        
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
        """ENTRY con MEMORIA"""
        
        matrimonio = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = matrimonio["name"]
        confidence = matrimonio["confidence"]
        
        can_enter, status = self.memoria.get_status(matrimonio_name)
        if not can_enter:
            if self.analyzer.tick_count % 500 == 0:
                log.info(f"[MEMORIA] {matrimonio_name}: {status} - NON ENTRO")
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
        
        log.info(f"""
[ENTRY] 🚀 INTELLIGENTE
├─ Matrimonio: {matrimonio_name} (Trust: {self.memoria.matrimoni_trust[matrimonio_name]}/100)
├─ Contesto: {momentum}_{volatility}_{trend}
├─ Confidence: {conf_1:.0%}
├─ Duration: {duration_min}s
└─ Capsule: 1✅ 2✅ 3✅ 4✅ 5✅
""")
        
        self.trade_open = {
            "price_entry": price,
            "matrimonio": matrimonio_name,
            "duration_avg": matrimonio["duration_avg"],
        }
        self.current_matrimonio = matrimonio_name
        self.max_price = price
        self.entry_time = time.time()
        self.total_trades += 1
    
    def _evaluate_exit(self, price, momentum, volatility):
        """EXIT con registrazione WR a memoria"""
        
        if price > self.max_price:
            self.max_price = price
        
        duration = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]
        
        pnl = price - self.trade_open["price_entry"]
        is_win = pnl > 0
        
        should_exit = False
        reason = "CONTINUA"
        
        if duration > duration_avg * 3:
            should_exit = True
            reason = "TIMEOUT"
        elif duration > duration_avg:
            drawdown = ((self.max_price - price) / self.trade_open["price_entry"]) * 100
            if drawdown > 1.0:
                should_exit = True
                reason = "MOMENTUM_DECAY"
        
        if not should_exit:
            return
        
        matrimonio_name = self.trade_open["matrimonio"]
        matrimonio = MatrimonioIntelligente.get_marriage("", "", "")
        
        for key, m in MatrimonioIntelligente.MARRIAGES.items():
            if m["name"] == matrimonio_name:
                matrimonio = m
                break
        
        wr_expected = matrimonio.get("confidence", 0.50)
        
        self.memoria.record_trade(matrimonio_name, is_win, wr_expected)
        
        if is_win:
            self.wins += 1
        else:
            self.losses += 1
        
        self.capital += pnl
        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        
        log.info(f"""
[EXIT] 🏁 INTELLIGENTE {'🟢 PROFIT' if is_win else '🔴 LOSS'}
├─ Matrimonio: {matrimonio_name}
├─ Reason: {reason}
├─ PnL: ${pnl:+.4f}
├─ Capital: ${self.capital:,.2f}
├─ Trust: {self.memoria.matrimoni_trust[matrimonio_name]}/100
└─ WR: {wr:.1f}%
""")
        
        self._save_trade_log(pnl, is_win)
        self._update_heartbeat()
        
        self.trade_open = None
        self.entry_time = None
        self.current_matrimonio = None
    
    def _save_trade_log(self, pnl, is_win):
        """Salva il trade nel DB (se disponibile)"""
        if self.db_execute:
            try:
                self.db_execute("""
                    INSERT INTO trades 
                    (event_type, asset, price, size, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "EXIT",
                    self.symbol,
                    0,
                    0,
                    pnl,
                    "LONG",
                    "Trade completato",
                    json.dumps({"is_win": is_win})
                ))
                log.debug("[DB] Trade salvato")
            except Exception as e:
                log.warning(f"[DB_ERROR] {e}")
    
    def _update_heartbeat(self):
        """Aggiorna heartbeat_data DIRETTAMENTE (no HTTP)"""
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["status"] = "RUNNING"
                self.heartbeat_data["capital"] = self.capital
                self.heartbeat_data["trades"] = self.total_trades
                self.heartbeat_data["wins"] = self.wins
                self.heartbeat_data["wr"] = self.wins / max(1, self.total_trades)
                self.heartbeat_data["last_seen"] = datetime.utcnow().isoformat()
                log.info("[HEARTBEAT] ✅ Aggiornato | Status=RUNNING | Capital=${:.2f} | Trades={}".format(
                    self.capital, self.total_trades))
            else:
                log.error("[HEARTBEAT] ❌ ERRORE CRITICO: heartbeat_data is None!")
        except Exception as e:
            log.error("[HEARTBEAT] ❌ ERRORE durante aggiornamento: {}".format(e))
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()
    
    def run(self):
        """Avvia il bot"""
        log.info("[START] Avviando bot con MEMORIA MATRIMONI...")
        self.connect_binance()
        
        log.info("[INFO] Bot è VIVO, aspettando tick di prezzo da Binance...")
        log.info("[INFO] MEMORIA ATTIVATA: Matrimoni/Separazioni/Divorzi")
        log.info("[INFO] Se rimane fermo, controllare:")
        log.info("  1. Connessione internet")
        log.info("  2. Binance API non bloccata")
        log.info("  3. VPN se necessario")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("[STOP] Bot fermato")
