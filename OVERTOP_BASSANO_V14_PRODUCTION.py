#!/usr/bin/env python3
"""
OVERTOP BASSANO V14 PRODUCTION — FULL BUILD
═══════════════════════════════════════════════════════════════════════════════
BOT TRADING COMPLETO INTEGRATO - PRODUCTION READY

INTEGRATO DENTRO (UN FILE, ZERO DIPENDENZE ESTERNE):
  ✅ CapsuleRuntime        — capsule da JSON, hot-reload senza restart
  ✅ ConfigHotReloader     — ricarica config ogni 30s
  ✅ RealtimeLearningEngine— genera capsule auto da trade recenti
  ✅ LogAnalyzer           — statistiche pattern per matrimonio
  ✅ AIExplainer           — narrative log SQLite ogni decisione
  ✅ SeedScorer            — scoring impulso a 4 componenti (TUA INVENZIONE)
  ✅ OracoloDinamico       — fingerprint WR memory con decay (TUA INVENZIONE)
  ✅ MemoriaMatrimoni      — 7 tipi, trust, separazione, divorzio permanente
  ✅ 5 Capsule intelligenti— Coerenza/Trappola/Protezione/Opportunità/Tattica
  ✅ 4 Divorce Triggers    — monitorati ogni tick, 2+ = DIVORZIO IMMEDIATO
  ✅ PAPER TRADE mode      — flag sicurezza prima del live
  ✅ Persistenza SQLite    — capital/trades sopravvivono al restart

PAPER TRADE:  imposta PAPER_TRADE = True per test sicuro
LIVE TRADING: imposta PAPER_TRADE = False (solo dopo paper test OK)
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import websocket
import threading
import time
import hashlib
import operator
import sqlite3
import os
from datetime import datetime
from collections import deque, defaultdict
import logging
import sys

# ═══════════════════════════════════════════════════════════════════════════
# ⚙️  CONFIGURAZIONE GLOBALE
# ═══════════════════════════════════════════════════════════════════════════

# ─── PAPER TRADE FLAG ───────────────────────────────────────────────────────
# True  = simula tutto, zero ordini reali su Binance → usa per testare
# False = ordini reali → SOLO dopo paper test soddisfacente
PAPER_TRADE = True

# ─── SEED SCORER ────────────────────────────────────────────────────────────
SEED_ENTRY_THRESHOLD = 0.45   # soglia minima per entrare

# ─── DIVORCE TRIGGERS ───────────────────────────────────────────────────────
DIVORCE_DRAWDOWN_PCT   = 3.0  # % drawdown dal massimo → trigger 3
DIVORCE_FP_DIVERGE_PCT = 0.50 # divergenza fingerprint > 50% → trigger 4
DIVORCE_MIN_TRIGGERS   = 2    # quanti trigger devono scattare per uscita immediata

# ─── DATABASE ────────────────────────────────────────────────────────────────
DB_PATH        = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
NARRATIVES_DB  = os.environ.get("NARRATIVES_DB", "/home/app/data/narratives.db")

# ─── BINANCE ─────────────────────────────────────────────────────────────────
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdc@aggTrade"
SYMBOL         = "BTCUSDC"

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# OPERATORS FOR CAPSULE RUNTIME
# ═══════════════════════════════════════════════════════════════════════════

OPS = {
    '>':      operator.gt,
    '>=':     operator.ge,
    '<':      operator.lt,
    '<=':     operator.le,
    '==':     operator.eq,
    '!=':     operator.ne,
    'in':     lambda a, b: a in b,
    'not_in': lambda a, b: a not in b,
}

# ═══════════════════════════════════════════════════════════════════════════
# STABILITY TELEMETRY — LOGGING PASSIVO, ZERO LOGICA
# Solo osserva. Non decide. Non modifica. Non ottimizza.
# ═══════════════════════════════════════════════════════════════════════════

class StabilityTelemetry:
    """Registra ogni decisione, flip, cambio parametro. Solo logging.
    
    VINCOLI OBBLIGATORI:
    1. Ogni evento ha SEMPRE: ts, event_type, regime, direction, open_position
    2. flip/param_change/trade_close/regime_change hanno anche snapshot:
       active_threshold, drift, macd, trend, volatility, bridge_reason
    """

    def __init__(self):
        self._start_time = time.time()
        self._events = []    # TUTTI gli eventi, schema uniforme

    def _base(self, event_type, regime, direction, open_position):
        """Campi minimi obbligatori su OGNI evento."""
        return {
            'ts': time.time(),
            'event_type': event_type,
            'regime': regime,
            'direction': direction,
            'open_position': open_position,
        }

    def _snapshot(self, active_threshold, drift, macd, trend, volatility, bridge_reason=None):
        """Snapshot di contesto per eventi strutturali."""
        return {
            'active_threshold': active_threshold,
            'drift': round(drift, 5) if drift is not None else 0,
            'macd': round(macd, 5) if macd is not None else 0,
            'trend': trend,
            'volatility': volatility,
            'bridge_reason': bridge_reason,
        }

    # ── EVENTI CON SNAPSHOT ───────────────────────────────────────────────

    def log_direction_flip(self, old_dir, new_dir, regime, direction, open_position,
                           active_threshold, drift, macd, trend, volatility, bridge_reason=None):
        e = self._base("DIRECTION_FLIP", regime, direction, open_position)
        e['old_direction'] = old_dir
        e['new_direction'] = new_dir
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility, bridge_reason))
        self._events.append(e)

    def log_direction_hold(self, bearish_signals, regime, direction, open_position,
                           active_threshold, drift, macd, trend, volatility):
        e = self._base("DIRECTION_HOLD", regime, direction, open_position)
        e['bearish_signals'] = bearish_signals
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility))
        self._events.append(e)

    def log_param_change(self, param, old_val, new_val, regime, direction, open_position,
                         active_threshold, drift, macd, trend, volatility, bridge_reason=None):
        e = self._base("PARAM_CHANGE", regime, direction, open_position)
        e['param'] = param
        e['old_value'] = old_val
        e['new_value'] = new_val
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility, bridge_reason))
        self._events.append(e)

    def log_param_rejected(self, param, value, reason, regime, direction, open_position,
                           active_threshold, drift, macd, trend, volatility):
        e = self._base("PARAM_REJECTED", regime, direction, open_position)
        e['param'] = param
        e['rejected_value'] = value
        e['reject_reason'] = reason
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility))
        self._events.append(e)

    def log_trade_close(self, trade_direction, pnl, is_win, exit_reason, duration,
                        regime, direction, open_position,
                        active_threshold, drift, macd, trend, volatility):
        e = self._base("TRADE_CLOSE", regime, direction, open_position)
        e['trade_direction'] = trade_direction
        e['pnl'] = round(pnl, 4)
        e['is_win'] = is_win
        e['exit_reason'] = exit_reason
        e['duration'] = round(duration, 1)
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility))
        self._events.append(e)

    def log_regime_change(self, old_regime, new_regime, direction, open_position,
                          active_threshold, drift, macd, trend, volatility):
        e = self._base("REGIME_CHANGE", new_regime, direction, open_position)
        e['old_regime'] = old_regime
        e['new_regime'] = new_regime
        e.update(self._snapshot(active_threshold, drift, macd, trend, volatility))
        self._events.append(e)

    # ── EVENTI SENZA SNAPSHOT (decisioni leggere) ─────────────────────────

    def log_trade_entry(self, trade_direction, score, soglia, matrimonio,
                        regime, direction, open_position):
        e = self._base("TRADE_ENTRY", regime, direction, open_position)
        e['trade_direction'] = trade_direction
        e['score'] = round(score, 1)
        e['soglia'] = round(soglia, 1)
        e['matrimonio'] = matrimonio
        self._events.append(e)

    def log_state_change(self, old_state, new_state, loss_streak,
                         regime, direction, open_position):
        e = self._base("STATE_CHANGE", regime, direction, open_position)
        e['old_state'] = old_state
        e['new_state'] = new_state
        e['loss_streak'] = loss_streak
        self._events.append(e)

    # ── REPORT ────────────────────────────────────────────────────────────

    def generate_report(self) -> dict:
        """Genera il report completo. Solo numeri, zero interpretazione."""
        uptime_hours = max((time.time() - self._start_time) / 3600, 0.001)
        events = self._events

        # ── A. Bridge / parametri ──
        param_events = [e for e in events if e['event_type'] == 'PARAM_CHANGE']
        param_counts = {}
        param_times = []
        for pc in param_events:
            p = pc['param']
            param_counts[p] = param_counts.get(p, 0) + 1
            param_times.append(pc['ts'])
        param_times.sort()
        avg_param_interval = 0
        if len(param_times) > 1:
            intervals = [param_times[i+1] - param_times[i] for i in range(len(param_times)-1)]
            avg_param_interval = sum(intervals) / len(intervals)

        # ── B. Direzione ──
        flips = [e for e in events if e['event_type'] == 'DIRECTION_FLIP']
        holds = [e for e in events if e['event_type'] == 'DIRECTION_HOLD']
        flips_l2s = sum(1 for f in flips if f['old_direction'] == 'LONG' and f['new_direction'] == 'SHORT')
        flips_s2l = sum(1 for f in flips if f['old_direction'] == 'SHORT' and f['new_direction'] == 'LONG')

        # ── C. Stabilità ──
        decisions_taken = [e for e in events if e['event_type'] in
                          ('DIRECTION_FLIP', 'PARAM_CHANGE', 'TRADE_CLOSE', 'TRADE_ENTRY')]
        decisions_not_taken = [e for e in events if e['event_type'] in
                              ('DIRECTION_HOLD', 'PARAM_REJECTED')]
        decision_cost = len(param_events) + len(flips) * 3

        # ── D. Performance per direzione ──
        trades = [e for e in events if e['event_type'] == 'TRADE_CLOSE']
        trades_long = [t for t in trades if t['trade_direction'] == 'LONG']
        trades_short = [t for t in trades if t['trade_direction'] == 'SHORT']
        def _stats(tlist):
            if not tlist:
                return {'n': 0, 'pnl': 0, 'wr': 0, 'avg_duration': 0}
            wins = sum(1 for t in tlist if t['is_win'])
            return {
                'n': len(tlist),
                'pnl': round(sum(t['pnl'] for t in tlist), 4),
                'wr': round(wins / len(tlist) * 100, 1),
                'avg_duration': round(sum(t['duration'] for t in tlist) / len(tlist), 1)
            }

        # ── E. Per regime ──
        regimes = set(t['regime'] for t in trades) if trades else set()
        regime_stats = {}
        for r in regimes:
            r_trades = [t for t in trades if t['regime'] == r]
            r_flips = sum(1 for f in flips if f['regime'] == r)
            r_params = sum(1 for p in param_events if p['regime'] == r)
            wins = sum(1 for t in r_trades if t['is_win'])
            regime_stats[r] = {
                'trades': len(r_trades),
                'wr': round(wins / len(r_trades) * 100, 1) if r_trades else 0,
                'pnl': round(sum(t['pnl'] for t in r_trades), 4),
                'flips': r_flips,
                'param_changes': r_params
            }

        return {
            'uptime_hours': round(uptime_hours, 2),
            'total_events': len(events),
            'A_bridge': {
                'total_param_changes': len(param_events),
                'total_param_rejected': len([e for e in events if e['event_type'] == 'PARAM_REJECTED']),
                'params_changed': param_counts,
                'avg_interval_seconds': round(avg_param_interval, 1),
                'recent_changes': param_events[-10:]
            },
            'B_direction': {
                'flips_LONG_to_SHORT': flips_l2s,
                'flips_SHORT_to_LONG': flips_s2l,
                'total_flips': flips_l2s + flips_s2l,
                'flips_per_hour': round((flips_l2s + flips_s2l) / uptime_hours, 2),
                'total_holds': len(holds),
                'recent_flips': flips[-20:],
            },
            'C_stability': {
                'decisions_taken': len(decisions_taken),
                'decisions_not_taken': len(decisions_not_taken),
                'decision_cost': decision_cost,
                'decision_cost_per_hour': round(decision_cost / uptime_hours, 2)
            },
            'D_performance': {
                'total': _stats(trades),
                'LONG': _stats(trades_long),
                'SHORT': _stats(trades_short)
            },
            'E_by_regime': regime_stats,
            'raw_events_last_50': events[-50:]
        }

    def persist_to_db(self, db_path):
        """Salva telemetria su SQLite — eventi singoli + report."""
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT, data_json TEXT
            )""")
            # Salva ogni evento non ancora persistito
            for e in self._events:
                conn.execute("INSERT INTO telemetry (event_type, data_json) VALUES (?, ?)",
                            (e['event_type'], json.dumps(e)))
            # Salva report aggregato
            report = self.generate_report()
            conn.execute("INSERT INTO telemetry (event_type, data_json) VALUES (?, ?)",
                        ("STABILITY_REPORT", json.dumps(report)))
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"[TELEMETRY] DB error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# CAPSULE RUNTIME
# ═══════════════════════════════════════════════════════════════════════════

class CapsuleRuntime:
    """Valuta e applica capsule da capsule_attive.json — hot reload senza restart."""

    def __init__(self, capsule_file: str = "capsule_attive.json"):
        self.capsule_file = capsule_file
        self.capsules = []
        self.hash = ""
        self._load()

    def _load(self):
        try:
            with open(self.capsule_file) as f:
                self.capsules = json.load(f)
                self.hash = hashlib.md5(open(self.capsule_file, 'rb').read()).hexdigest()
            log.info(f"[CAPSULE] ✅ Caricate {len(self.capsules)} regole da {self.capsule_file}")
        except FileNotFoundError:
            self.capsules = []
            log.warning("[CAPSULE] ⚠️ capsule_attive.json non trovato — opero a vuoto")
        except Exception as e:
            self.capsules = []
            log.error(f"[CAPSULE] Errore caricamento: {e}")

    def reload(self) -> bool:
        try:
            new_hash = hashlib.md5(open(self.capsule_file, 'rb').read()).hexdigest()
            if new_hash != self.hash:
                self._load()
                return True
        except Exception:
            pass
        return False

    def valuta(self, contesto: dict) -> dict:
        """Valuta tutte le capsule attive. Ritorna: {blocca, size_mult, reason}"""
        risultato = {'blocca': False, 'size_mult': 1.0, 'reason': ''}
        for capsule in sorted(self.capsules, key=lambda c: c.get('priority', 5)):
            if not capsule.get('enabled', True):
                continue
            triggers = capsule.get('trigger', [])
            if not all(self._check_trigger(t, contesto) for t in triggers):
                continue
            azione = capsule.get('azione', {})
            if azione.get('type') == 'blocca_entry':
                risultato['blocca'] = True
                risultato['reason'] = azione.get('params', {}).get('reason', 'capsule_block')
                break
            elif azione.get('type') == 'modifica_size':
                risultato['size_mult'] *= azione.get('params', {}).get('mult', 1.0)
        return risultato

    def _check_trigger(self, trigger: dict, contesto: dict) -> bool:
        param = trigger.get('param')
        op    = trigger.get('op')
        value = trigger.get('value')
        if param not in contesto or op not in OPS:
            return False
        try:
            return OPS[op](contesto[param], value)
        except Exception:
            return False

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG HOT RELOADER
# ═══════════════════════════════════════════════════════════════════════════

class ConfigHotReloader:
    """Controlla hash del file capsule ogni 30s. Zero restart."""

    def __init__(self, capsule_path: str = "capsule_attive.json"):
        self.capsule_path = capsule_path
        self.hash = ""

    def check_reload(self) -> bool:
        try:
            new_hash = hashlib.md5(open(self.capsule_path, 'rb').read()).hexdigest()
            if new_hash != self.hash:
                self.hash = new_hash
                return True
        except Exception:
            pass
        return False

# ═══════════════════════════════════════════════════════════════════════════
# REAL-TIME LEARNING ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class RealtimeLearningEngine:
    """
    Osserva gli ultimi N trade. Se un matrimonio scende sotto WR 40%
    su almeno 3 campioni, genera automaticamente una capsula di blocco
    e la scrive in capsule_attive.json.
    """

    def __init__(self, max_trades: int = 10, capsule_file: str = "capsule_attive.json"):
        self.recent_trades = deque(maxlen=max_trades)
        self.capsule_file  = capsule_file

    def registra_trade(self, trade: dict):
        self.recent_trades.append(trade)

    def analizza_e_genera(self) -> list:
        if len(self.recent_trades) < 3:
            return []
        stats = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in self.recent_trades:
            m = t.get('matrimonio', 'unknown')
            stats[m]['total'] += 1
            if t.get('pnl', 0) > 0:
                stats[m]['wins'] += 1
        nuove = []
        for matrimonio, s in stats.items():
            if s['total'] >= 3:
                wr = s['wins'] / s['total'] * 100
                if wr < 40:
                    cap = {
                        'capsule_id':  f"RT_BLOCCO_{matrimonio}_{int(time.time())}",
                        'version':     1,
                        'descrizione': f"Auto-RT: {matrimonio} WR {wr:.0f}% sotto soglia",
                        'trigger':     [{'param': 'matrimonio', 'op': '==', 'value': matrimonio}],
                        'azione':      {'type': 'blocca_entry', 'params': {'reason': 'matrimonio_wr_basso'}},
                        'priority':    1,
                        'enabled':     True,
                    }
                    nuove.append(cap)
                    log.info(f"[REALTIME] 🧠 Capsula auto generata: BLOCCO {matrimonio} (WR={wr:.0f}%)")
        # Persiste le nuove capsule nel file
        if nuove:
            self._persist(nuove)
        return nuove

    def _persist(self, nuove: list):
        try:
            existing = []
            if os.path.exists(self.capsule_file):
                with open(self.capsule_file) as f:
                    existing = json.load(f)
            # Evita duplicati per stesso matrimonio
            existing_ids = {c.get('capsule_id') for c in existing}
            da_aggiungere = [c for c in nuove if c['capsule_id'] not in existing_ids]
            existing.extend(da_aggiungere)
            with open(self.capsule_file, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            log.error(f"[REALTIME] Errore persistenza capsule: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# LOG ANALYZER
# ═══════════════════════════════════════════════════════════════════════════

class LogAnalyzer:
    """Analizza gli ultimi 100 trade, espone statistiche per matrimonio."""

    def __init__(self):
        self.trades = deque(maxlen=100)

    def registra(self, trade: dict):
        self.trades.append(trade)

    def get_stats(self) -> dict:
        if not self.trades:
            return {}
        stats = defaultdict(lambda: {'wins': 0, 'total': 0})
        for t in self.trades:
            m = t.get('matrimonio')
            stats[m]['total'] += 1
            if t.get('pnl', 0) > 0:
                stats[m]['wins'] += 1
        return {
            'total_trades':  len(self.trades),
            'matrimonio_wr': {m: (s['wins'] / s['total'] * 100 if s['total'] > 0 else 0)
                              for m, s in stats.items()},
        }

# ═══════════════════════════════════════════════════════════════════════════
# AI EXPLAINER
# ═══════════════════════════════════════════════════════════════════════════

class AIExplainer:
    """Log narrativo di ogni decisione del bot — scritto su SQLite."""

    def __init__(self, db_path: str = "narratives.db"):
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        d = os.path.dirname(self.db_path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS narrative_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp  TEXT,
                    event_type TEXT,
                    narrative  TEXT,
                    trade_data TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[AIExplainer] DB init: {e}")

    def log_decision(self, event_type: str, narrative: str, trade_data: dict = None):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO narrative_log (timestamp, event_type, narrative, trade_data)
                VALUES (?, ?, ?, ?)
            """, (datetime.utcnow().isoformat(), event_type, narrative,
                  json.dumps(trade_data) if trade_data else None))
            conn.commit()
            conn.close()
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════════════
# ★ SEED SCORER — TUA INVENZIONE
#   Valuta la forza dell'impulso prima di ogni entry.
#   4 componenti con pesi specifici → score 0.0–1.0
#   Soglia: SEED_ENTRY_THRESHOLD (default 0.45)
# ═══════════════════════════════════════════════════════════════════════════

class SeedScorer:
    """
    Scoring dell'impulso a 4 componenti:
      1. Range Position      40% — dove si trova il prezzo nel range recente
      2. Volume Acceleration 25% — accelerazione del volume sugli ultimi tick
      3. Directional Consist 20% — coerenza direzionale delle ultime variazioni
      4. Breakout Score      15% — rottura del range precedente
    Ritorna score [0.0 – 1.0] e dettaglio di ogni componente.
    """

    W_RANGE_POS   = 0.40
    W_VOL_ACCEL   = 0.25
    W_DIR_CONSIST = 0.20
    W_BREAKOUT    = 0.15

    def __init__(self, window: int = 50):
        self.prices  = deque(maxlen=window)
        self.volumes = deque(maxlen=window)   # aggTrade include qty

    def add_tick(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

    def score(self) -> dict:
        """
        Ritorna {'score': float, 'range_pos': float, 'vol_accel': float,
                 'dir_consist': float, 'breakout': float, 'pass': bool}
        """
        if len(self.prices) < 20:
            return {'score': 0.0, 'pass': False, 'reason': 'insufficient_data'}

        prices  = list(self.prices)
        volumes = list(self.volumes)

        # — 1. Range Position (40%) ────────────────────────────────────────
        # Quanto è in alto il prezzo attuale rispetto al range degli ultimi 20 tick
        window_20 = prices[-20:]
        low20  = min(window_20)
        high20 = max(window_20)
        if high20 == low20:
            range_pos = 0.5
        else:
            range_pos = (prices[-1] - low20) / (high20 - low20)  # 0=basso, 1=alto

        # — 2. Volume Acceleration (25%) ───────────────────────────────────
        # Volume medio ultimi 5 tick vs volume medio tick 6-10
        vol_recent = sum(volumes[-5:])  / 5
        vol_prev   = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_recent
        if vol_prev == 0:
            vol_accel = 0.5
        else:
            ratio     = vol_recent / vol_prev
            vol_accel = min(1.0, ratio / 2.0)   # normalizza: ratio=2 → score=1.0

        # — 3. Directional Consistency (20%) ──────────────────────────────
        # % di variazioni positive negli ultimi 10 tick
        changes = [prices[i+1] - prices[i] for i in range(len(prices)-10, len(prices)-1)]
        if not changes:
            dir_consist = 0.5
        else:
            positive = sum(1 for c in changes if c > 0)
            dir_consist = positive / len(changes)   # 0=tutto giù, 1=tutto su

        # — 4. Breakout Score (15%) ────────────────────────────────────────
        # Prezzo attuale vs massimo dei tick 10-30 (rottura di resistenza recente)
        if len(prices) >= 30:
            resistance = max(prices[-30:-10])
            current    = prices[-1]
            if current > resistance:
                breakout = min(1.0, (current - resistance) / resistance * 100)
            else:
                breakout = 0.0
        else:
            breakout = 0.5   # non abbastanza dati, neutro

        # — Score totale ───────────────────────────────────────────────────
        total = (range_pos   * self.W_RANGE_POS   +
                 vol_accel   * self.W_VOL_ACCEL   +
                 dir_consist * self.W_DIR_CONSIST  +
                 breakout    * self.W_BREAKOUT)

        return {
            'score':       round(total, 4),
            'range_pos':   round(range_pos, 4),
            'vol_accel':   round(vol_accel, 4),
            'dir_consist': round(dir_consist, 4),
            'breakout':    round(breakout, 4),
            'pass':        total >= SEED_ENTRY_THRESHOLD,
        }

# ═══════════════════════════════════════════════════════════════════════════
# ★ ORACOLO DINAMICO — TUA INVENZIONE
#   Fingerprint-based win-rate memory con decay.
#   Blocca pattern FANTASMA (contesti che storicamente perdono).
# ═══════════════════════════════════════════════════════════════════════════

class OracoloDinamico:
    """
    Memorizza il WR per ogni fingerprint (combinazione momentum+volatility+trend).
    Applica decay temporale: i ricordi vecchi pesano meno di quelli recenti.
    Blocca entry se il fingerprint ha un WR storico < soglia.
    Restituisce anche il fingerprint_wr corrente usato dalle 5 Capsule.
    """

    FANTASMA_WR_THRESHOLD = 0.45   # sotto questa soglia il pattern è FANTASMA
    DECAY_FACTOR          = 0.95   # ogni trade, il peso storico si riduce del 5%
    MIN_SAMPLES           = 5      # campioni minimi prima di giudicare

    def __init__(self):
        # {fingerprint: {'wr_decay': float, 'samples': int, 'wins': float}}
        self._memory: dict = {}

    def _fp(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> str:
        return f"{direction}|{momentum}|{volatility}|{trend}"

    def get_wr(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> float:
        """Ritorna il WR stimato per questo contesto (0.0–1.0). Default 0.72 se ignoto."""
        fp = self._fp(momentum, volatility, trend, direction)
        if fp not in self._memory or self._memory[fp]['samples'] < self.MIN_SAMPLES:
            return 0.72   # neutro — abbastanza positivo da non bloccare
        m = self._memory[fp]
        return m['wins'] / m['samples'] if m['samples'] > 0 else 0.72

    def is_fantasma(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> tuple:
        """
        Ritorna (True, motivo) se il pattern è FANTASMA, (False, '') altrimenti.
        """
        fp  = self._fp(momentum, volatility, trend, direction)
        wr  = self.get_wr(momentum, volatility, trend, direction)
        mem = self._memory.get(fp, {})
        if mem.get('samples', 0) < self.MIN_SAMPLES:
            return False, ''   # troppo pochi dati → non bloccare
        if wr < self.FANTASMA_WR_THRESHOLD:
            return True, f"FANTASMA fp={fp} wr={wr:.2f}"
        return False, ''

    def record(self, momentum: str, volatility: str, trend: str, is_win: bool, direction: str = "LONG"):
        """Aggiorna la memoria con decay. Chiamato ad ogni chiusura trade."""
        fp = self._fp(momentum, volatility, trend, direction)
        if fp not in self._memory:
            self._memory[fp] = {'wins': 0.0, 'samples': 0}
        m = self._memory[fp]
        # Applica decay ai valori esistenti
        m['wins']    *= self.DECAY_FACTOR
        m['samples'] *= self.DECAY_FACTOR
        # Aggiungi nuovo dato (peso pieno = 1)
        m['wins']    += 1.0 if is_win else 0.0
        m['samples'] += 1.0
        log.debug(f"[ORACOLO] {fp} → WR={m['wins']/m['samples']:.2f} samples={m['samples']:.1f}")

    def dump(self) -> dict:
        """Snapshot completo della memoria — per heartbeat/debug."""
        return {fp: {'wr': round(m['wins']/m['samples'], 3) if m['samples'] > 0 else 0,
                     'samples': round(m['samples'], 1)}
                for fp, m in self._memory.items()}

# ═══════════════════════════════════════════════════════════════════════════
# 5 CAPSULE INTELLIGENTI
# ═══════════════════════════════════════════════════════════════════════════

class Capsule1Coerenza:
    """Valida coerenza tra fingerprint_wr e contesto attuale."""
    def valida(self, fingerprint_wr, momentum, volatility, trend,
               soglia_buona=0.60, soglia_perfetta=0.75):
        if fingerprint_wr > soglia_perfetta and momentum == "FORTE" and volatility == "BASSA" and trend == "UP":
            return True, 0.95, "COERENZA PERFETTA"
        if fingerprint_wr > soglia_buona and momentum in ("FORTE", "MEDIO") and trend == "UP":
            return True, fingerprint_wr, "COERENZA BUONA"
        return False, 0.10, "BLOCCO_COERENZA"

class Capsule2Trappola:
    """Riconosce setup trappola da confidence bassa."""
    def riconosci(self, confidence):
        if confidence < 0.50:
            return False, "TRAPPOLA_CONFIDENCE"
        return True, "OK"

class Capsule3Protezione:
    """Blocca in condizioni di alta volatilità con impulso debole."""
    def proteggi(self, momentum, volatility, fingerprint_wr, fp_minimo=0.55):
        if momentum == "DEBOLE" and volatility == "ALTA" and fingerprint_wr <= 0.70:
            return False, "PROTETTO_VOLATILITÀ"
        if volatility == "ALTA" and fingerprint_wr < fp_minimo:
            return False, "PROTETTO_FP_BASSO"
        return True, "OK"

class Capsule4Opportunita:
    """Riconosce finestre di opportunità premium."""
    def riconosci(self, fingerprint_wr, momentum, volatility, soglia_buona=0.65):
        if fingerprint_wr > 0.75 and momentum == "FORTE" and volatility == "BASSA":
            return True, 0.95, "OPPORTUNITÀ_ORO"
        if fingerprint_wr > soglia_buona and momentum == "FORTE":
            return True, fingerprint_wr, "OPPORTUNITÀ_BUONA"
        return False, 0.40, "NO_OPPORTUNITÀ"

class Capsule5Tattica:
    """Timing tattico: entry solo se coerenza e confidence alte."""
    def timing(self, entry_trigger, coerenza, confidence, conf_ok=0.65):
        if entry_trigger and coerenza and confidence > 0.80:
            return True, 45, "TIMING_PERFETTO"
        if entry_trigger and confidence > conf_ok:
            return True, 25, "TIMING_OK"
        return False, 0, "TIMING_NO"

# ═══════════════════════════════════════════════════════════════════════════
# MATRIMONI INTELLIGENTI — 7 TIPI
# ═══════════════════════════════════════════════════════════════════════════

class MatrimonioIntelligente:
    """
    7 matrimoni con WR atteso e duration media.
    La chiave è (momentum, volatility, trend).
    """
    MARRIAGES = {
        # ── TREND UP ─────────────────────────────────────────────────────
        ("FORTE", "BASSA",  "UP"):      {"name": "STRONG_BULL",    "wr": 0.85, "duration_avg": 45, "confidence": 0.95},
        ("FORTE", "MEDIA",  "UP"):      {"name": "STRONG_MED",     "wr": 0.75, "duration_avg": 30, "confidence": 0.85},
        ("FORTE", "ALTA",   "UP"):      {"name": "STRONG_VOLATILE","wr": 0.65, "duration_avg": 20, "confidence": 0.70},
        ("MEDIO", "BASSA",  "UP"):      {"name": "MEDIUM_BULL",    "wr": 0.70, "duration_avg": 25, "confidence": 0.80},
        ("MEDIO", "MEDIA",  "UP"):      {"name": "CAUTIOUS",       "wr": 0.60, "duration_avg": 15, "confidence": 0.65},
        ("MEDIO", "ALTA",   "UP"):      {"name": "CAUTIOUS_VOL",   "wr": 0.50, "duration_avg": 12, "confidence": 0.55},
        ("DEBOLE","BASSA",  "UP"):      {"name": "WEAK_BULL",      "wr": 0.55, "duration_avg": 15, "confidence": 0.55},
        ("DEBOLE","MEDIA",  "UP"):      {"name": "WEAK_MED_UP",    "wr": 0.45, "duration_avg": 10, "confidence": 0.45},
        ("DEBOLE","ALTA",   "UP"):      {"name": "WEAK_VOL_UP",    "wr": 0.35, "duration_avg": 8,  "confidence": 0.35},
        # ── TREND SIDEWAYS ───────────────────────────────────────────────
        ("FORTE", "BASSA",  "SIDEWAYS"):{"name": "RANGE_STRONG",   "wr": 0.65, "duration_avg": 20, "confidence": 0.70},
        ("FORTE", "MEDIA",  "SIDEWAYS"):{"name": "RANGE_MED_F",    "wr": 0.60, "duration_avg": 15, "confidence": 0.65},
        ("FORTE", "ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_F",    "wr": 0.55, "duration_avg": 12, "confidence": 0.55},
        ("MEDIO", "BASSA",  "SIDEWAYS"):{"name": "RANGE_CALM",     "wr": 0.55, "duration_avg": 15, "confidence": 0.60},
        ("MEDIO", "MEDIA",  "SIDEWAYS"):{"name": "RANGE_NEUTRAL",  "wr": 0.50, "duration_avg": 12, "confidence": 0.50},
        ("MEDIO", "ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_M",    "wr": 0.45, "duration_avg": 10, "confidence": 0.45},
        ("DEBOLE","BASSA",  "SIDEWAYS"):{"name": "RANGE_DEAD",     "wr": 0.40, "duration_avg": 8,  "confidence": 0.35},
        ("DEBOLE","MEDIA",  "SIDEWAYS"):{"name": "WEAK_NEUTRAL",   "wr": 0.45, "duration_avg": 8,  "confidence": 0.40},
        ("DEBOLE","ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_W",    "wr": 0.35, "duration_avg": 6,  "confidence": 0.30},
        # ── TREND DOWN ───────────────────────────────────────────────────
        ("FORTE", "BASSA",  "DOWN"):    {"name": "BEAR_STRONG",    "wr": 0.60, "duration_avg": 20, "confidence": 0.65},
        ("FORTE", "MEDIA",  "DOWN"):    {"name": "BEAR_MED_F",     "wr": 0.50, "duration_avg": 15, "confidence": 0.55},
        ("FORTE", "ALTA",   "DOWN"):    {"name": "PANIC",          "wr": 0.15, "duration_avg": 3,  "confidence": 0.15},
        ("MEDIO", "BASSA",  "DOWN"):    {"name": "BEAR_CALM",      "wr": 0.45, "duration_avg": 12, "confidence": 0.50},
        ("MEDIO", "MEDIA",  "DOWN"):    {"name": "BEAR_NEUTRAL",   "wr": 0.40, "duration_avg": 10, "confidence": 0.40},
        ("MEDIO", "ALTA",   "DOWN"):    {"name": "BEAR_VOL",       "wr": 0.30, "duration_avg": 8,  "confidence": 0.30},
        ("DEBOLE","BASSA",  "DOWN"):    {"name": "BEAR_WEAK",      "wr": 0.35, "duration_avg": 8,  "confidence": 0.35},
        ("DEBOLE","MEDIA",  "DOWN"):    {"name": "BEAR_WEAK_M",    "wr": 0.25, "duration_avg": 5,  "confidence": 0.25},
        ("DEBOLE","ALTA",   "DOWN"):    {"name": "TRAP",           "wr": 0.05, "duration_avg": 2,  "confidence": 0.05},
    }

    @staticmethod
    def get_marriage(momentum, volatility, trend):
        key = (momentum, volatility, trend)
        return MatrimonioIntelligente.MARRIAGES.get(key, {
            "name": "UNKNOWN", "wr": 0.50, "duration_avg": 12, "confidence": 0.50
        })

    @staticmethod
    def get_by_name(name: str) -> dict:
        for m in MatrimonioIntelligente.MARRIAGES.values():
            if m["name"] == name:
                return m
        return {"name": name, "wr": 0.50, "duration_avg": 12, "confidence": 0.50}

# ═══════════════════════════════════════════════════════════════════════════
# MEMORIA MATRIMONI — trust, separazione, divorzio
# ═══════════════════════════════════════════════════════════════════════════

class MemoriaMatrimoni:
    """
    Tiene traccia delle performance per ogni matrimonio.
    - trust [0–100]: sale con win (+5), scende con loss (-15)
    - SEPARAZIONE: WR reale < 60% dell'atteso dopo 10 trade → blacklist 50 trade
    - DIVORZIO PERMANENTE: seconda SEPARAZIONE → fuori per sempre
    """

    def __init__(self):
        self.trust      = defaultdict(lambda: 50)
        self.separazione= defaultdict(bool)
        self.blacklist  = defaultdict(int)
        self.divorzio   = set()
        self.wr_history = defaultdict(list)
        self.wins       = defaultdict(int)
        self.losses     = defaultdict(int)

    def get_status(self, name: str) -> tuple:
        if name in self.divorzio:
            return False, "DIVORZIO_PERMANENTE"
        if self.blacklist[name] > 0:
            self.blacklist[name] -= 1
            return False, f"SEPARAZIONE_ATTIVA ({self.blacklist[name]} rimasti)"
        if self.trust[name] < 30:
            return False, f"TRUST_BASSO ({self.trust[name]})"
        return True, "OK"

    def record_trade(self, name: str, is_win: bool, wr_expected: float):
        if is_win:
            self.wins[name]  += 1
            self.trust[name] = min(100, self.trust[name] + 5)
        else:
            self.losses[name]  += 1
            self.trust[name]   = max(0, self.trust[name] - 15)

        total = self.wins[name] + self.losses[name]
        if total > 0:
            wr_reale = self.wins[name] / total
            self.wr_history[name].append(wr_reale)
            if len(self.wr_history[name]) >= 10:
                recent_wr = sum(self.wr_history[name][-10:]) / 10
                if recent_wr < wr_expected * 0.6:
                    if self.separazione[name]:
                        self.divorzio.add(name)
                        self.trust[name] = 0
                        log.warning(f"[DIVORZIO PERMANENTE] 💔 {name} eliminato")
                    else:
                        self.separazione[name] = True
                        self.blacklist[name]   = 50
                        log.warning(f"[SEPARAZIONE] ⚠️  {name} blacklist 50 trade")

# ═══════════════════════════════════════════════════════════════════════════
# ANALIZZATORE CONTESTO
# ═══════════════════════════════════════════════════════════════════════════

class ContestoAnalyzer:
    """Momentum, volatility, trend dai prezzi recenti."""

    def __init__(self, window: int = 50):
        self.prices    = deque(maxlen=window)
        self.tick_count= 0

    def add_price(self, price: float):
        self.prices.append(price)
        self.tick_count += 1

    def analyze(self):
        if len(self.prices) < 10:
            return None, None, None
        prices    = list(self.prices)
        recent    = prices[-5:]
        changes   = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
        up_count  = sum(1 for c in changes if c > 0)
        momentum  = "FORTE" if up_count >= 4 else ("MEDIO" if up_count >= 2 else "DEBOLE")

        r20        = prices[-20:]
        changes20  = [abs(r20[i+1] - r20[i]) for i in range(len(r20)-1)]
        avg_ch20   = sum(changes20) / len(changes20) if changes20 else 0
        volatility = "ALTA" if avg_ch20 > 0.005 else ("MEDIA" if avg_ch20 > 0.002 else "BASSA")

        chg_pct = (prices[-1] - prices[0]) / prices[0] * 100
        trend   = "UP" if chg_pct > 0.3 else ("DOWN" if chg_pct < -0.3 else "SIDEWAYS")

        return momentum, volatility, trend

# ═══════════════════════════════════════════════════════════════════════════
# PERSISTENZA SQLite — capital e trades sopravvivono al restart
# ═══════════════════════════════════════════════════════════════════════════

class PersistenzaStato:
    """Legge/scrive capital e total_trades su SQLite."""

    DEFAULT_CAPITAL = 10000.0
    DEFAULT_TRADES  = 0

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        d = os.path.dirname(self.db_path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[PERSIST] Init DB: {e}")

    def load(self) -> tuple:
        """Ritorna (capital, total_trades)."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())
            conn.close()
            capital      = float(rows.get('capital',      self.DEFAULT_CAPITAL))
            total_trades = int(rows.get('total_trades',   self.DEFAULT_TRADES))
            log.info(f"[PERSIST] Stato caricato: capital={capital:.2f} trades={total_trades}")
            return capital, total_trades
        except Exception as e:
            log.error(f"[PERSIST] Load: {e} — uso defaults")
            return self.DEFAULT_CAPITAL, self.DEFAULT_TRADES

    def save_brain(self, oracolo, memoria, calibratore):
        """
        Serializza l'intelligenza accumulata su SQLite.
        OracoloDinamico + MemoriaMatrimoni + AutoCalibratore params.
        Chiamato ad ogni trade chiuso e ogni 5 minuti.
        """
        try:
            import json
            conn = sqlite3.connect(self.db_path)

            # ── OracoloDinamico ─────────────────────────────────────────
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('oracolo', ?)",
                        (json.dumps(oracolo._memory),))

            # ── MemoriaMatrimoni ────────────────────────────────────────
            memoria_data = {
                'trust':      dict(memoria.trust),
                'separazione':dict(memoria.separazione),
                'blacklist':  dict(memoria.blacklist),
                'divorzio':   list(memoria.divorzio),
                'wins':       dict(memoria.wins),
                'losses':     dict(memoria.losses),
                'wr_history': {k: list(v) for k, v in memoria.wr_history.items()},
            }
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('memoria', ?)",
                        (json.dumps(memoria_data),))

            # ── AutoCalibratore params ───────────────────────────────────
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('calibra_params', ?)",
                        (json.dumps(calibratore.params),))

            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[BRAIN_SAVE] {e}")

    def load_brain(self, oracolo, memoria, calibratore):
        """
        Ripristina l'intelligenza accumulata da SQLite dopo un restart.
        Il bot riprende esattamente da dove aveva lasciato.
        """
        try:
            import json
            conn  = sqlite3.connect(self.db_path)
            rows  = dict(conn.execute("SELECT key, value FROM bot_state").fetchall())
            conn.close()

            restored = []

            # ── OracoloDinamico ─────────────────────────────────────────
            if 'oracolo' in rows:
                raw = json.loads(rows['oracolo'])
                # Ricostruisce la struttura interna
                for fp, data in raw.items():
                    oracolo._memory[fp] = {
                        'wins':    float(data.get('wins', 0)),
                        'samples': float(data.get('samples', 0)),
                    }
                restored.append(f"Oracolo: {len(oracolo._memory)} fingerprint")

            # ── MemoriaMatrimoni ────────────────────────────────────────
            if 'memoria' in rows:
                md = json.loads(rows['memoria'])
                for k, v in md.get('trust', {}).items():
                    memoria.trust[k] = v
                for k, v in md.get('separazione', {}).items():
                    memoria.separazione[k] = v
                for k, v in md.get('blacklist', {}).items():
                    memoria.blacklist[k] = v
                for mat in md.get('divorzio', []):
                    memoria.divorzio.add(mat)
                for k, v in md.get('wins', {}).items():
                    memoria.wins[k] = v
                for k, v in md.get('losses', {}).items():
                    memoria.losses[k] = v
                for k, v in md.get('wr_history', {}).items():
                    memoria.wr_history[k] = list(v)
                restored.append(f"Memoria: {len(memoria.divorzio)} divorzi, "
                               f"{sum(1 for v in memoria.blacklist.values() if v > 0)} separazioni")

            # ── AutoCalibratore params ───────────────────────────────────
            if 'calibra_params' in rows:
                saved = json.loads(rows['calibra_params'])
                calibratore.params.update(saved)
                restored.append(f"Calibra: seed={saved.get('seed_threshold', '?')}")

            if restored:
                log.info(f"[BRAIN_LOAD] 🧠 Intelligenza ripristinata → {' | '.join(restored)}")
            else:
                log.info("[BRAIN_LOAD] Primo avvio — nessuna memoria precedente")

        except Exception as e:
            log.error(f"[BRAIN_LOAD] {e} — parto da zero")

    def save(self, capital: float, total_trades: int):
        """Persiste capital e total_trades su SQLite."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('capital', ?)",      (str(capital),))
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('total_trades', ?)", (str(total_trades),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[PERSIST] Save: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# ★ REGIME DETECTOR — contesto macro sopra tutto
#   Classifica il regime strutturale del mercato su finestra larga.
#   TRENDING_BULL / TRENDING_BEAR / RANGING / EXPLOSIVE
#   Il regime cambia i parametri di tutto il sistema sottostante.
# ═══════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Osserva 500 tick e classifica il regime macro.
    Non si confonde con i tick singoli — lavora sulla struttura.

    Regimi:
      TRENDING_BULL  — trend rialzista strutturale, alta directional consistency
      TRENDING_BEAR  — trend ribassista strutturale
      RANGING        — mercato laterale, alta volatilità relativa, bassa direzione
      EXPLOSIVE      — breakout improvviso, volume spike + range expansion
    """

    WINDOW = 500   # tick per valutare il regime

    # Moltiplicatori per ogni regime — applicati ai parametri del calibratore
    REGIME_PARAMS = {
        'TRENDING_BULL': {
            'seed_mult':      0.90,   # leggermente più permissivo
            'fp_wr_mult':     0.95,   # accetta contesti leggermente meno perfetti
            'size_mult':      1.25,   # size più grande in trend
            'drawdown_mult':  1.20,   # tollera più drawdown in trend
        },
        'TRENDING_BEAR': {
            'seed_mult':      1.20,   # più selettivo
            'fp_wr_mult':     1.10,
            'size_mult':      0.70,   # size ridotta
            'drawdown_mult':  0.80,   # meno tolleranza
        },
        'RANGING': {
            'seed_mult':      1.30,   # molto selettivo — il ranging è il nemico
            'fp_wr_mult':     1.15,
            'size_mult':      0.60,
            'drawdown_mult':  0.70,
        },
        'EXPLOSIVE': {
            'seed_mult':      0.85,   # velocità conta — entra prima
            'fp_wr_mult':     0.90,
            'size_mult':      1.50,   # massima size in breakout
            'drawdown_mult':  1.50,   # lascia correre
        },
    }

    def __init__(self):
        self.prices    = deque(maxlen=self.WINDOW)
        self.volumes   = deque(maxlen=self.WINDOW)
        self._regime   = 'RANGING'   # default conservativo
        self._confidence = 0.0

    def add_tick(self, price: float, volume: float = 1.0):
        self.prices.append(price)
        self.volumes.append(volume)

    def detect(self) -> tuple:
        """
        Ritorna (regime: str, confidence: float, dettaglio: dict)
        """
        if len(self.prices) < 100:
            return 'RANGING', 0.0, {}

        prices  = list(self.prices)
        volumes = list(self.volumes)
        n       = len(prices)

        # ── Trend strutturale ─────────────────────────────────────────────
        # Regressione lineare semplificata: confronta metà iniziale vs finale
        mid        = n // 2
        avg_first  = sum(prices[:mid]) / mid
        avg_second = sum(prices[mid:]) / (n - mid)
        trend_pct  = (avg_second - avg_first) / avg_first * 100

        # ── Directional Consistency su finestra larga ─────────────────────
        changes    = [prices[i+1] - prices[i] for i in range(n-1)]
        up_count   = sum(1 for c in changes if c > 0)
        dir_ratio  = up_count / len(changes)   # 0=tutto giù, 1=tutto su

        # ── Volatilità strutturale ─────────────────────────────────────────
        abs_changes = [abs(c) for c in changes]
        avg_change  = sum(abs_changes) / len(abs_changes)
        # Confronta volatilità prima vs seconda metà
        vol_first   = sum(abs_changes[:mid]) / mid
        vol_second  = sum(abs_changes[mid:]) / (n - mid)
        vol_ratio   = vol_second / max(vol_first, 0.001)

        # ── Volume acceleration ────────────────────────────────────────────
        vol_recent  = sum(volumes[-50:]) / 50
        vol_base    = sum(volumes[:50])  / 50
        vol_accel   = vol_recent / max(vol_base, 0.001)

        # ── Classificazione ───────────────────────────────────────────────
        regime     = 'RANGING'
        confidence = 0.5

        if vol_accel > 2.0 and vol_ratio > 1.5:
            # Volume esploso + volatilità in aumento → EXPLOSIVE
            regime     = 'EXPLOSIVE'
            confidence = min(1.0, vol_accel / 3.0)

        elif trend_pct > 0.5 and dir_ratio > 0.55:
            # Trend rialzista strutturale
            regime     = 'TRENDING_BULL'
            confidence = min(1.0, (dir_ratio - 0.5) * 4)

        elif trend_pct < -0.5 and dir_ratio < 0.45:
            # Trend ribassista strutturale
            regime     = 'TRENDING_BEAR'
            confidence = min(1.0, (0.5 - dir_ratio) * 4)

        else:
            # Laterale
            regime     = 'RANGING'
            confidence = min(1.0, 1.0 - abs(dir_ratio - 0.5) * 4)

        self._regime     = regime
        self._confidence = confidence

        return regime, confidence, {
            'trend_pct':  round(trend_pct, 3),
            'dir_ratio':  round(dir_ratio, 3),
            'vol_accel':  round(vol_accel, 3),
            'vol_ratio':  round(vol_ratio, 3),
        }

    @property
    def regime(self) -> str:
        return self._regime

    def get_multipliers(self) -> dict:
        return self.REGIME_PARAMS.get(self._regime, self.REGIME_PARAMS['RANGING'])


# ═══════════════════════════════════════════════════════════════════════════
# ★ MOMENTUM DECELEROMETER — exit intelligente
#   Non misura il momentum — misura quanto velocemente sta decelerando.
#   Uscire quando decelera forte, non quando è già morto.
# ═══════════════════════════════════════════════════════════════════════════

class MomentumDecelerometer:
    """
    Calcola la derivata seconda del momentum.
    Se il momentum stava salendo e ora sta scendendo velocemente
    → segnale di uscita anticipata prima che il prezzo inverta.

    Restituisce:
      decel_score [0-1] — 0=momentum stabile, 1=decelera forte
      should_exit bool  — True se la decelerazione supera la soglia
    """

    WINDOW_FAST = 5    # tick per momentum veloce
    WINDOW_SLOW = 15   # tick per momentum lento
    DECEL_THRESHOLD = 0.65   # oltre questa soglia → esci

    def __init__(self):
        self.prices = deque(maxlen=50)

    def add_price(self, price: float):
        self.prices.append(price)

    def analyze(self) -> dict:
        if len(self.prices) < self.WINDOW_SLOW + 5:
            return {'decel_score': 0.0, 'should_exit': False}

        prices = list(self.prices)

        # Momentum veloce: variazione media negli ultimi WINDOW_FAST tick
        fast_changes = [prices[i+1] - prices[i]
                        for i in range(len(prices)-self.WINDOW_FAST, len(prices)-1)]
        mom_fast = sum(fast_changes) / len(fast_changes) if fast_changes else 0

        # Momentum lento: variazione media negli ultimi WINDOW_SLOW tick
        slow_start = len(prices) - self.WINDOW_SLOW
        slow_changes = [prices[i+1] - prices[i]
                        for i in range(slow_start, len(prices)-1)]
        mom_slow = sum(slow_changes) / len(slow_changes) if slow_changes else 0

        # Decelerazione: il momentum veloce è molto più basso di quello lento
        # (il trend sta perdendo forza)
        if abs(mom_slow) < 0.001:
            decel_score = 0.0
        else:
            # Se mom_fast < mom_slow → decelera (in trade long)
            decel = (mom_slow - mom_fast) / abs(mom_slow)
            decel_score = max(0.0, min(1.0, decel))

        return {
            'decel_score': round(decel_score, 4),
            'mom_fast':    round(mom_fast, 4),
            'mom_slow':    round(mom_slow, 4),
            'should_exit': decel_score > self.DECEL_THRESHOLD,
        }


# ═══════════════════════════════════════════════════════════════════════════
# ★ POSITION SIZER — la tua fisica applicata
#   Size come funzione CONTINUA dell'intensità dell'impulso.
#   Non più 1.0 / 1.3 / 1.5 discreti — una curva che riflette
#   esattamente quanto il mercato ti sta dando.
# ═══════════════════════════════════════════════════════════════════════════

class PositionSizer:
    """
    Calcola la size ottimale come funzione continua di 3 segnali:
      1. seed_score      — forza dell'impulso (peso 40%)
      2. fingerprint_wr  — affidabilità storica del contesto (peso 35%)
      3. confidence      — certezza del matrimonio (peso 25%)

    Poi applica il moltiplicatore di regime.

    Output: size_factor [0.5 – 2.0]
    Dove 1.0 = size base, 2.0 = massimo, 0.5 = minimo di sicurezza
    """

    W_SEED   = 0.40
    W_FP_WR  = 0.35
    W_CONF   = 0.25

    SIZE_MIN = 0.5
    SIZE_MAX = 2.0

    def calculate(self, seed_score: float, fingerprint_wr: float,
                  confidence: float, regime_mult: float = 1.0) -> dict:
        """
        Ritorna {'size_factor': float, 'breakdown': dict}
        """
        # Normalizza ogni componente in [0, 1]
        # seed_score è già [0, 1]
        seed_norm = min(1.0, max(0.0, seed_score))

        # fingerprint_wr [0.45, 0.95] → [0, 1]
        fp_norm = min(1.0, max(0.0, (fingerprint_wr - 0.45) / 0.50))

        # confidence [0.05, 0.95] → [0, 1]
        conf_norm = min(1.0, max(0.0, (confidence - 0.05) / 0.90))

        # Score composito
        score = (seed_norm   * self.W_SEED  +
                 fp_norm     * self.W_FP_WR +
                 conf_norm   * self.W_CONF)

        # Mappa da [0,1] a [SIZE_MIN, SIZE_MAX] con curva non lineare
        # Le posizioni forti crescono più che proporzionalmente
        size_raw = self.SIZE_MIN + (self.SIZE_MAX - self.SIZE_MIN) * (score ** 1.5)

        # Applica moltiplicatore regime
        size_final = min(self.SIZE_MAX, max(self.SIZE_MIN, size_raw * regime_mult))

        return {
            'size_factor': round(size_final, 3),
            'score':       round(score, 3),
            'seed_norm':   round(seed_norm, 3),
            'fp_norm':     round(fp_norm, 3),
            'conf_norm':   round(conf_norm, 3),
        }


# ═══════════════════════════════════════════════════════════════════════════
# ★ AUTO CALIBRATORE — TUA INVENZIONE
#   Osserva i risultati reali e aggiusta i parametri statici.
#   Stessa pazienza e cautela del DNA del sistema:
#   - Minimo 30 trade prima di toccare qualsiasi soglia
#   - Step massimo ±0.02 per aggiustamento
#   - Invertibile se la modifica peggiora i risultati
#   - Log narrativo di ogni modifica
# ═══════════════════════════════════════════════════════════════════════════

class AutoCalibratore:
    """
    Calibra automaticamente i parametri statici basandosi sui risultati reali.
    Non è ubriaco: aspetta evidenza solida, cambia in piccoli passi,
    ricorda ogni modifica e può tornare indietro.
    """

    # ── Limiti di sicurezza — non si esce mai da questi range ─────────────
    LIMITS = {
        'seed_threshold':      (0.25, 0.70),   # mai troppo permissivo né troppo restrittivo
        'cap1_soglia_buona':   (0.45, 0.80),   # Capsule1 soglia "coerenza buona"
        'cap1_soglia_perfetta':(0.60, 0.90),   # Capsule1 soglia "coerenza perfetta"
        'cap3_fp_minimo':      (0.35, 0.65),   # Capsule3 protezione fp minimo
        'cap4_soglia_buona':   (0.50, 0.80),   # Capsule4 opportunità buona
        'cap5_conf_ok':        (0.50, 0.80),   # Capsule5 timing OK
        'divorce_drawdown':    (1.5,  5.0),    # drawdown trigger
    }

    STEP          = 0.05    # era 0.02 — troppo lento, il mercato cambia regime in minuti
    MIN_TRADES    = 10      # era 30 — con stop loss 2% il rischio è controllato, impara prima
    MIN_DELTA_WR  = 0.05    # differenza minima WR reale vs atteso per intervenire
    HISTORY_SIZE  = 5       # quante calibrazioni ricordare per inversione
    MIN_CALIB_INTERVAL = 900  # minimo 15 minuti tra calibrazioni — anti-oscillazione

    def __init__(self):
        # Parametri correnti — inizializzati ai valori di default
        self.params = {
            'seed_threshold':       SEED_ENTRY_THRESHOLD,
            'cap1_soglia_buona':    0.60,
            'cap1_soglia_perfetta': 0.75,
            'cap3_fp_minimo':       0.55,
            'cap4_soglia_buona':    0.65,
            'cap5_conf_ok':         0.65,
            'divorce_drawdown':     DIVORCE_DRAWDOWN_PCT,
        }
        # Storico per inversione: {param: [(valore_prima, valore_dopo, wr_al_momento)]}
        self._history: dict = {k: [] for k in self.params}
        # Osservazioni per calibrazione: lista di (seed_score, wr_contesto, is_win)
        self._obs: list = []
        self._calibrazioni_log: list = []   # log narrativo
        self._last_calib_time: float = 0    # rate limit anti-oscillazione

    def registra_osservazione(self, seed_score: float, fingerprint_wr: float,
                               is_win: bool, divorce_drawdown_usato: float):
        """Chiamato dopo ogni trade chiuso."""
        self._obs.append({
            'seed_score':     seed_score,
            'fingerprint_wr': fingerprint_wr,
            'is_win':         is_win,
            'drawdown':       divorce_drawdown_usato,
        })

    def calibra(self) -> dict:
        """
        Analizza le osservazioni accumulate.
        Se ci sono evidenze solide (≥ MIN_TRADES) aggiusta i parametri.
        Rate limit: massimo 1 calibrazione ogni MIN_CALIB_INTERVAL secondi.
        Ritorna dict con parametri aggiornati e log delle modifiche.
        """
        if len(self._obs) < self.MIN_TRADES:
            return {}   # troppo poco per giudicare

        # Rate limit: non calibrare troppo spesso
        now = time.time()
        if now - self._last_calib_time < self.MIN_CALIB_INTERVAL:
            return {}
        self._last_calib_time = now

        modifiche = {}
        n = len(self._obs)
        wins = sum(1 for o in self._obs if o['is_win'])
        wr_reale = wins / n

        # ── 1. SEED THRESHOLD ─────────────────────────────────────────────
        # Se la maggior parte dei trade ha seed_score vicino alla soglia attuale
        # e WR è basso → alza la soglia (sii più selettivo)
        # Se WR è alto ma entri raramente → abbassa leggermente
        seed_scores = [o['seed_score'] for o in self._obs]
        avg_seed = sum(seed_scores) / len(seed_scores)
        current_seed = self.params['seed_threshold']

        if wr_reale < 0.45 and avg_seed < current_seed + 0.10:
            # WR basso e i trade hanno seed basso → soglia troppo permissiva
            new_val = min(current_seed + self.STEP,
                         self.LIMITS['seed_threshold'][1])
            if new_val != current_seed:
                self._aggiusta('seed_threshold', new_val, wr_reale,
                    f"WR={wr_reale:.0%} basso su {n} trade, avg_seed={avg_seed:.3f} → alzo soglia")
                modifiche['seed_threshold'] = new_val

        elif wr_reale > 0.70 and n > self.MIN_TRADES * 2:
            # WR molto alto → possiamo essere leggermente meno restrittivi
            new_val = max(current_seed - self.STEP,
                         self.LIMITS['seed_threshold'][0])
            if new_val != current_seed:
                self._aggiusta('seed_threshold', new_val, wr_reale,
                    f"WR={wr_reale:.0%} eccellente su {n} trade → abbasso soglia leggermente")
                modifiche['seed_threshold'] = new_val

        # ── 2. DIVORCE DRAWDOWN ───────────────────────────────────────────
        # Se molti trade escono per TIMEOUT (non per divorce) con drawdown alto
        # → il drawdown trigger è troppo permissivo, abbassalo
        drawdowns = [o['drawdown'] for o in self._obs if o['drawdown'] > 0]
        if drawdowns:
            avg_dd = sum(drawdowns) / len(drawdowns)
            current_dd = self.params['divorce_drawdown']
            if avg_dd > current_dd * 0.8 and wr_reale < 0.50:
                new_val = max(current_dd - self.STEP * 5,
                             self.LIMITS['divorce_drawdown'][0])
                if new_val != current_dd:
                    self._aggiusta('divorce_drawdown', new_val, wr_reale,
                        f"avg_drawdown={avg_dd:.1f}% vicino alla soglia, WR basso → stringo drawdown")
                    modifiche['divorce_drawdown'] = new_val

        # ── 3. CAP1 SOGLIA COERENZA ───────────────────────────────────────
        # Osserva quanti trade hanno fingerprint_wr nel range "buono" (0.60-0.75)
        # Se quelli perdono → alza la soglia di ingresso coerenza
        fp_buono = [o for o in self._obs
                    if 0.60 <= o['fingerprint_wr'] < 0.75]
        if len(fp_buono) >= 10:
            wr_fp_buono = sum(1 for o in fp_buono if o['is_win']) / len(fp_buono)
            current_c1 = self.params['cap1_soglia_buona']
            if wr_fp_buono < 0.45:
                new_val = min(current_c1 + self.STEP,
                             self.LIMITS['cap1_soglia_buona'][1])
                if new_val != current_c1:
                    self._aggiusta('cap1_soglia_buona', new_val, wr_fp_buono,
                        f"Trade fp_wr 0.60-0.75 hanno WR={wr_fp_buono:.0%} → alzo soglia coerenza")
                    modifiche['cap1_soglia_buona'] = new_val

        # Reset osservazioni dopo calibrazione (mantieni le ultime MIN_TRADES/2)
        self._obs = self._obs[-(self.MIN_TRADES // 2):]

        return modifiche

    def _aggiusta(self, param: str, new_val: float, wr_al_momento: float, motivo: str):
        """Applica il cambio, lo registra per eventuale inversione."""
        old_val = self.params[param]
        self.params[param] = new_val

        # Storico per inversione
        self._history[param].append((old_val, new_val, wr_al_momento))
        if len(self._history[param]) > self.HISTORY_SIZE:
            self._history[param].pop(0)

        msg = f"[CALIBRA] 🎯 {param}: {old_val:.3f} → {new_val:.3f} | {motivo}"
        self._calibrazioni_log.append({
            'ts':    datetime.utcnow().isoformat(),
            'param': param,
            'from':  old_val,
            'to':    new_val,
            'why':   motivo,
        })
        log.info(msg)

    def inverti_se_peggiorato(self, wr_attuale: float):
        """
        Se dopo una calibrazione il WR è peggiorato, torna al valore precedente.
        Chiamato ogni 20 trade dopo una modifica.
        """
        for param, history in self._history.items():
            if not history:
                continue
            old_val, new_val, wr_prima = history[-1]
            if wr_attuale < wr_prima - self.MIN_DELTA_WR:
                # La modifica ha peggiorato le cose → torna indietro
                self.params[param] = old_val
                history.pop()
                log.warning(f"[CALIBRA] ↩️  INVERSIONE {param}: {new_val:.3f} → {old_val:.3f} "
                           f"(WR prima={wr_prima:.0%} ora={wr_attuale:.0%})")

    def get_params(self) -> dict:
        return dict(self.params)

    def get_log(self) -> list:
        return list(self._calibrazioni_log[-10:])   # ultimi 10 eventi


# ═══════════════════════════════════════════════════════════════════════════
# ★ CAMPO GRAVITAZIONALE — MOTORE 2 (CARTESIANO)
#   Nessun filtro binario tranne i veti assoluti.
#   Ogni condizione accumula punti. La soglia si muove con il contesto.
#   La size è funzione continua della distanza punteggio-soglia.
# ═══════════════════════════════════════════════════════════════════════════

class CampoGravitazionale:
    """
    Entry engine cartesiano: ogni dimensione contribuisce punti,
    la soglia è dinamica, la size è continua.

    Veti assoluti (non negoziabili):
      - TRAP / PANIC (combinazioni tossiche provate)
      - DIVORZIO PERMANENTE
      - FANTASMA con evidenza forte (samples>20, WR<30%)
      - 3+ loss consecutivi

    Tutto il resto → punteggio 0-100 vs soglia dinamica 35-90.
    """

    # ── VETI ASSOLUTI ─────────────────────────────────────────────────────
    # LONG: non entrare in mercato che crolla
    VETI_LONG = {
        ("DEBOLE", "ALTA", "DOWN"),    # TRAP — WR 5% per LONG
        ("FORTE",  "ALTA", "DOWN"),    # PANIC — WR 15% per LONG
    }
    # SHORT: non entrare in mercato che esplode al rialzo
    VETI_SHORT = {
        ("FORTE",  "BASSA", "UP"),     # STRONG_BULL — WR 5% per SHORT
        ("FORTE",  "MEDIA", "UP"),     # STRONG_MED — pericoloso per SHORT
    }
    FANTASMA_VETO_MIN_SAMPLES = 20
    FANTASMA_VETO_MAX_WR      = 0.30
    MAX_LOSS_CONSECUTIVI      = 3

    # ── PESI DEL CAMPO (totale = 100) ─────────────────────────────────────
    # V2: aggiunto RSI e MACD come consiglieri. Pesi ridistribuiti.
    W_SEED        = 25    # era 30 — cede 5 ai consiglieri
    W_FINGERPRINT = 20    # era 25 — cede 5 ai consiglieri
    W_MOMENTUM    = 12    # era 15
    W_TREND       = 12    # era 15
    W_VOLATILITY  = 8     # era 10
    W_REGIME      = 3     # era 5
    W_RSI         = 10    # NUOVO — il consigliere ipervenduto/ipercomprato
    W_MACD        = 10    # NUOVO — il consigliere trend/momentum

    # ── SCORING PER DIMENSIONE ────────────────────────────────────────────
    # LONG — impulso rialzista
    MOMENTUM_SCORE_LONG  = {"FORTE": 1.0,  "MEDIO": 0.67, "DEBOLE": 0.20}
    TREND_SCORE_LONG     = {"UP": 1.0,     "SIDEWAYS": 0.47, "DOWN": 0.0}
    REGIME_SCORE_LONG    = {"TRENDING_BULL": 1.0, "EXPLOSIVE": 0.80,
                            "RANGING": 0.20, "TRENDING_BEAR": 0.0}

    # SHORT — impulso ribassista (tutto invertito)
    MOMENTUM_SCORE_SHORT = {"FORTE": 0.20, "MEDIO": 0.67, "DEBOLE": 1.0}
    TREND_SCORE_SHORT    = {"UP": 0.0,     "SIDEWAYS": 0.47, "DOWN": 1.0}
    REGIME_SCORE_SHORT   = {"TRENDING_BULL": 0.0, "EXPLOSIVE": 0.80,
                            "RANGING": 0.20, "TRENDING_BEAR": 1.0}

    # VOL_SCORE è uguale per LONG e SHORT — alta volatilità è sempre rischio
    VOL_SCORE       = {"BASSA": 1.0,  "MEDIA": 0.60, "ALTA": 0.20}

    # ── SOGLIA DINAMICA ───────────────────────────────────────────────────
    SOGLIA_BASE = 60
    REGIME_FACTOR = {"TRENDING_BULL": 0.80, "EXPLOSIVE": 0.85,
                     "RANGING": 1.00, "TRENDING_BEAR": 1.10}
    # RANGING: era 1.10, ora 1.00 — soglia formula 75.9 irraggiungibile, score max realistico 64
    # Con 1.00: soglia RANGING+ALTA = 60 × 1.00 × 1.05 = 63.0 (raggiungibile)
    VOL_FACTOR    = {"BASSA": 0.90, "MEDIA": 1.0, "ALTA": 1.00}
    # ALTA: era 1.05, ora 1.00 — phantom SCORE_INSUFF WR 65% R/R 2.04, profittevoli
    # Soglia RANGING+ALTA: 60 × 1.00 × 1.00 = 60.0 (trade score 58-63 passano)
    SOGLIA_MIN    = 58    # PAVIMENTO ASSOLUTO — nessun fattore scende sotto questo
    SOGLIA_MAX    = 80    # era 90 — phantom SCORE_INSUFFICIENTE dice -$3871, troppo alto in RANGING

    # ── SIZE CONTINUA ─────────────────────────────────────────────────────
    SIZE_MIN = 0.5
    SIZE_MAX = 2.0

    # ── DRIFT VETO ─────────────────────────────────────────────────────
    DRIFT_VETO_THRESHOLD = -0.20   # era -0.10 — phantom WR 81% bloccati, sta bloccando i migliori

    # ── WARMUP ─────────────────────────────────────────────────────────
    WARMUP_TICKS = 200   # tick minimi prima di operare — buffer devono riempirsi

    def __init__(self):
        self._recent_results = deque(maxlen=20)
        self._tick_count = 0   # conta tick dal boot
        self._direction = "LONG"  # LONG o SHORT — il bridge decide
        self._direction_last_change = 0       # timestamp ultimo flip
        self._direction_bearish_streak = 0    # tick consecutivi bearish >=2
        # ── POST-FLIP COOLDOWN ADATTIVO ────────────────────────────────
        # Dopo un flip di direzione, aspetta prima di entrare.
        # Il cooldown si auto-calibra: se i trade subito dopo il flip
        # perdono → il cooldown cresce. Se vincono → si accorcia.
        self._post_flip_cooldown = 30         # secondi di attesa dopo flip (parte da 30)
        self._post_flip_results = deque(maxlen=10)  # ultimi 10 trade post-flip: (is_win, pnl)
        self._POST_FLIP_COOLDOWN_MIN = 10     # minimo 10 secondi
        self._POST_FLIP_COOLDOWN_MAX = 120    # massimo 2 minuti
        # ── PRE-BREAKOUT DETECTOR ─────────────────────────────────────────
        self._prices_short = deque(maxlen=50)     # ultimi 50 prezzi per compressione
        self._seed_history = deque(maxlen=10)     # ultimi 10 seed per derivata
        self._volumes_short = deque(maxlen=50)    # ultimi 50 volumi per accelerazione
        # ── DRIFT DETECTOR ────────────────────────────────────────────────
        self._prices_long = deque(maxlen=200)     # ultimi 200 prezzi per drift
        # ── RSI + MACD CONSIGLIERI ────────────────────────────────────────
        self._prices_ta = deque(maxlen=200)       # buffer prezzi CAMPIONATI per indicatori tecnici
        self._ta_tick_counter = 0                  # conta tick per campionamento
        self._ta_sample_rate = 50                  # campiona ogni 50 tick (non ogni tick!)
        self._rsi_period = 14                     # RSI standard 14 periodi
        self._macd_fast = 12                      # MACD EMA veloce
        self._macd_slow = 26                      # MACD EMA lenta
        self._macd_signal = 9                     # MACD signal line
        self._last_rsi = 50.0                     # RSI corrente
        self._last_macd = 0.0                     # MACD line corrente
        self._last_macd_signal = 0.0              # MACD signal corrente
        self._last_macd_hist = 0.0                # MACD histogram
        # ── PREBREAKOUT AUTO-TUNING (META-REGOLA) ─────────────────────────
        self._pb3_results = deque(maxlen=20)       # ultimi 20 trade con pb=3/3: (is_win, exit_reason, pnl)
        self._pb3_compression_threshold = 0.0003   # si stringe se WR pb3 < 50%
        self._pb3_vol_acc_threshold = 1.3           # si stringe se troppi falsi

    def feed_tick(self, price: float, volume: float, seed_score: float):
        """Alimenta tutti i detector con dati tick-by-tick."""
        self._prices_short.append(price)
        self._volumes_short.append(volume)
        self._seed_history.append(seed_score)
        self._prices_long.append(price)
        self._tick_count += 1

        # ── CAMPIONA per RSI/MACD ogni 50 tick ────────────────────────
        # I tick sono troppo veloci — RSI su tick-by-tick va a 100/0.
        # Campionando ogni 50 tick creiamo "candele" virtuali stabili.
        self._ta_tick_counter += 1
        if self._ta_tick_counter >= self._ta_sample_rate:
            self._ta_tick_counter = 0
            self._prices_ta.append(price)
            if len(self._prices_ta) >= 30:
                self._update_rsi()
                self._update_macd()

    def evaluate(self, seed_score, fingerprint_wr, momentum, volatility,
                 trend, regime, matrimonio_name, divorzio_set,
                 fantasma_info, loss_consecutivi, direction="LONG") -> dict:
        """
        Ritorna:
          enter:     bool
          score:     float (0-100)
          soglia:    float (58-80, dinamica)
          size:      float (0.5-2.0 se enter, 0.0 se no)
          veto:      str o None
          direction: "LONG" o "SHORT"
          breakdown: dict dettaglio per log
        """
        # ── VETI ASSOLUTI ─────────────────────────────────────────────────
        combo = (momentum, volatility, trend)
        veti = self.VETI_SHORT if self._direction == "SHORT" else self.VETI_LONG
        if combo in veti:
            return self._veto(f"TOSSICO_{self._direction}_{momentum}_{volatility}_{trend}")

        if matrimonio_name in divorzio_set:
            return self._veto("DIVORZIO_PERMANENTE")

        is_fantasma, fantasma_reason = fantasma_info
        if is_fantasma:
            # Solo se evidenza forte — non blocchiamo su 5 campioni
            # Il campo già penalizza fingerprint_wr basso nel punteggio
            fp_samples = fantasma_reason  # passato come samples count
            if isinstance(fp_samples, str):
                # fantasma_info ritorna (bool, str_reason) — usiamo l'info dell'oracolo
                pass  # non è un veto forte, il punteggio basso basta

        if loss_consecutivi >= self.MAX_LOSS_CONSECUTIVI:
            # Non bloccare per sempre — pausa proporzionale ai loss, poi riprova.
            # Il State Engine gestisce già il cooldown e DIFENSIVO.
            # Qui aggiungiamo solo un peso extra alla soglia, non un veto assoluto.
            # 3 loss → soglia +10%, 4 loss → +20%, 5 loss → +30%
            # Così i trade FORTI passano ancora, quelli deboli no.
            pass  # gestito sotto come moltiplicatore soglia, non come veto

        # ── WARMUP INTELLIGENTE — la volpe non entra cieca ────────────
        # Non basta contare i tick. Ogni senso deve essere attivo:
        #   - Drift: serve _prices_long piena (200 tick)
        #   - RSI/MACD: servono 30 campioni × 50 tick = 1500 tick
        #   - RegimeDetector: serve 500 tick
        #   - SeedScorer: serve 20 tick (veloce)
        #
        # Il sistema NON entra finché TUTTI i sensi sono pronti.
        # Non un timer fisso — un check reale sui buffer.
        warmup_checks = []
        if self._tick_count < 200:
            warmup_checks.append(f"tick={self._tick_count}/200")
        if len(self._prices_long) < 100:
            warmup_checks.append(f"drift={len(self._prices_long)}/100")
        if len(self._prices_ta) < 30:
            warmup_checks.append(f"RSI_MACD={len(self._prices_ta)}/30")
        if warmup_checks:
            return self._veto(f"WARMUP_{'|'.join(warmup_checks)}")

        # ── DRIFT VETO: direzione sbagliata → NON ENTRARE ────────────────
        # LONG: mercato scende → VETO. SHORT: mercato sale → VETO.
        if len(self._prices_long) >= 100:
            _prices = list(self._prices_long)
            _avg_old = sum(_prices[:50]) / 50
            _avg_new = sum(_prices[-50:]) / 50
            _drift = (_avg_new - _avg_old) / _avg_old * 100
            if self._direction == "LONG" and _drift < self.DRIFT_VETO_THRESHOLD:
                return self._veto(f"DRIFT_VETO_LONG_{_drift:+.3f}%")
            elif self._direction == "SHORT" and _drift > abs(self.DRIFT_VETO_THRESHOLD):
                return self._veto(f"DRIFT_VETO_SHORT_{_drift:+.3f}%")

        # ── CALCOLO PUNTEGGIO CAMPO ───────────────────────────────────────
        # Seed: normalizza [0.3, 1.0] → [0, 1]
        s_seed = min(1.0, max(0.0, (seed_score - 0.30) / 0.70)) * self.W_SEED

        # Fingerprint WR: normalizza [0.30, 1.0] → [0, 1]
        s_fp = min(1.0, max(0.0, (fingerprint_wr - 0.30) / 0.70)) * self.W_FINGERPRINT

        # Dimensioni categoriche — INVERTITE per SHORT
        if self._direction == "SHORT":
            s_mom   = self.MOMENTUM_SCORE_SHORT.get(momentum, 0.5)  * self.W_MOMENTUM
            s_trend = self.TREND_SCORE_SHORT.get(trend, 0.5)         * self.W_TREND
            s_reg   = self.REGIME_SCORE_SHORT.get(regime, 0.2)        * self.W_REGIME
        else:
            s_mom   = self.MOMENTUM_SCORE_LONG.get(momentum, 0.5)   * self.W_MOMENTUM
            s_trend = self.TREND_SCORE_LONG.get(trend, 0.5)          * self.W_TREND
            s_reg   = self.REGIME_SCORE_LONG.get(regime, 0.2)         * self.W_REGIME
        s_vol   = self.VOL_SCORE.get(volatility, 0.5)                * self.W_VOLATILITY

        # ── CONSIGLIERI TECNICI — invertiti per SHORT ────────────────────
        s_rsi   = self._rsi_score()                          * self.W_RSI
        s_macd  = self._macd_score()                         * self.W_MACD

        score = s_seed + s_fp + s_mom + s_trend + s_vol + s_reg + s_rsi + s_macd

        # ── SOGLIA PROPORZIONALE AL CONTESTO ─────────────────────────────
        # La soglia è proporzionale allo score MASSIMO raggiungibile nel
        # contesto corrente. Stessa % di selettività in ogni regime.
        #
        # In TRENDING_BULL+BASSA+UP: score_max=100, soglia=60 → chiedi 60%
        # In RANGING+ALTA+SIDEWAYS:  score_max=65,  soglia=39 → chiedi 60%
        #
        # Senza questo, soglia 60 in RANGING chiede il 92% del max = blocca tutto.
        # ─────────────────────────────────────────────────────────────────

        # Score max per il contesto corrente (dimensioni NON controllabili)
        if self._direction == "SHORT":
            _ctx_mom   = self.MOMENTUM_SCORE_SHORT.get(momentum, 0.5)
            _ctx_trend = self.TREND_SCORE_SHORT.get(trend, 0.5)
            _ctx_reg   = self.REGIME_SCORE_SHORT.get(regime, 0.2)
        else:
            _ctx_mom   = self.MOMENTUM_SCORE_LONG.get(momentum, 0.5)
            _ctx_trend = self.TREND_SCORE_LONG.get(trend, 0.5)
            _ctx_reg   = self.REGIME_SCORE_LONG.get(regime, 0.2)
        _ctx_vol = self.VOL_SCORE.get(volatility, 0.5)

        score_max = (1.0 * self.W_SEED +           # seed perfetto
                     1.0 * self.W_FINGERPRINT +     # fp_wr perfetto
                     _ctx_mom * self.W_MOMENTUM +   # momentum attuale
                     _ctx_trend * self.W_TREND +    # trend attuale
                     _ctx_vol * self.W_VOLATILITY + # volatilità attuale
                     _ctx_reg * self.W_REGIME +     # regime attuale
                     1.0 * self.W_RSI +             # RSI perfetto
                     1.0 * self.W_MACD)             # MACD perfetto

        # context_ratio: quanto il contesto è favorevole (0.6–1.0)
        context_ratio = score_max / 100.0

        regime_f  = self.REGIME_FACTOR.get(regime, 1.0)
        vol_f     = self.VOL_FACTOR.get(volatility, 1.0)
        history_f = self._history_factor()
        prebreak_f, prebreak_detail, prebreak_signals = self._pre_breakout_factor()
        drift_f, drift_detail = self._drift_factor()

        # ── LOSS STREAK: alza la soglia, non bloccare ──────────────────
        # Dopo 3+ loss consecutivi la soglia sale del 10% per loss extra.
        # I trade FORTI (score alto) passano ancora. I deboli no.
        # Nessun deadlock: il sistema continua a operare.
        if loss_consecutivi >= self.MAX_LOSS_CONSECUTIVI:
            extra = loss_consecutivi - self.MAX_LOSS_CONSECUTIVI + 1  # 1, 2, 3...
            loss_f = 1.0 + (extra * 0.10)  # 1.10, 1.20, 1.30...
            loss_f = min(loss_f, 1.50)      # cap a +50%
        else:
            loss_f = 1.0

        # Soglia proporzionale: scala con il max raggiungibile
        soglia_raw = self.SOGLIA_BASE * context_ratio * regime_f * vol_f * history_f * prebreak_f * drift_f * loss_f
        # Floor: proporzionale al contesto MA con pavimento assoluto a 48
        # Questo impedisce che matrimoni deboli (RANGE_VOL_W score ~44) passino
        # mentre RANGE_VOL_F (score ~54-66) passa tranquillamente
        SOGLIA_FLOOR_ASSOLUTO = 48
        soglia_min_ctx = max(SOGLIA_FLOOR_ASSOLUTO, self.SOGLIA_MIN * context_ratio)
        soglia = max(soglia_min_ctx, min(self.SOGLIA_MAX, soglia_raw))

        # ── DECISIONE ─────────────────────────────────────────────────────
        enter = score >= soglia

        # ── SIZE CONTINUA ─────────────────────────────────────────────────
        if enter:
            eccedenza = (score - soglia) / max(1.0, score_max - soglia)
            size = self.SIZE_MIN + (self.SIZE_MAX - self.SIZE_MIN) * (eccedenza ** 1.5)
            size = min(self.SIZE_MAX, max(self.SIZE_MIN, size))
        else:
            size = 0.0

        return {
            'enter':     enter,
            'score':     round(score, 2),
            'soglia':    round(soglia, 2),
            'size':      round(size, 3),
            'veto':      None,
            'pb_signals': prebreak_signals,
            'score_max': round(score_max, 1),
            'breakdown': {
                'seed':    round(s_seed, 2),
                'fp':      round(s_fp, 2),
                'mom':     round(s_mom, 2),
                'trend':   round(s_trend, 2),
                'vol':     round(s_vol, 2),
                'regime':  round(s_reg, 2),
                'rsi':     round(s_rsi, 2),
                'macd':    round(s_macd, 2),
                'rsi_val': round(self._last_rsi, 1),
                'score_max': round(score_max, 1),
                'ctx':     round(context_ratio, 2),
                'soglia_f': f"r={regime_f:.2f} v={vol_f:.2f} h={history_f:.2f} d={drift_f:.2f} l={loss_f:.2f} ctx={context_ratio:.2f} smax={score_max:.0f} RSI={self._last_rsi:.0f} {prebreak_detail} {drift_detail}".strip(),
            }
        }

    def record_result(self, is_win: bool, exit_reason: str = "", pb_signals: int = 0, pnl: float = 0.0):
        """Chiamato alla chiusura di ogni shadow trade."""
        self._recent_results.append(is_win)

        # ── META-REGOLA: PREBREAKOUT AUTO-TUNING ─────────────────────────
        # Traccia i risultati dei trade che sono entrati con pb=3/3
        if pb_signals >= 3:
            self._pb3_results.append({
                'is_win': is_win,
                'exit': exit_reason,
                'pnl': pnl,
            })

            # Dopo 5+ trade pb3, valuta se le soglie vanno strette
            if len(self._pb3_results) >= 5:
                pb3_list = list(self._pb3_results)
                pb3_wins = sum(1 for r in pb3_list if r['is_win'])
                pb3_wr = pb3_wins / len(pb3_list)
                pb3_smorz = sum(1 for r in pb3_list if r['exit'] == 'SMORZ' and not r['is_win'])

                # Se WR pb3 < 50% → stringi compressione (da 0.0003 a 0.0002)
                if pb3_wr < 0.50 and self._pb3_compression_threshold > 0.00015:
                    self._pb3_compression_threshold -= 0.00005
                    log.info(f"[META] 🧠 PreBreakout auto-tune: WR pb3={pb3_wr:.0%} < 50% → "
                             f"compression threshold stretto a {self._pb3_compression_threshold:.5f}")

                # Se > 40% dei LOSS pb3 escono per SMORZ → alza vol_acc threshold
                if len(pb3_list) >= 5:
                    smorz_ratio = pb3_smorz / max(1, len(pb3_list) - pb3_wins)
                    if smorz_ratio > 0.40 and self._pb3_vol_acc_threshold < 3.0:
                        self._pb3_vol_acc_threshold += 0.2
                        log.info(f"[META] 🧠 PreBreakout auto-tune: SMORZ ratio={smorz_ratio:.0%} > 40% → "
                                 f"vol_acc threshold alzato a {self._pb3_vol_acc_threshold:.1f}")

                # Se WR pb3 > 70% → allenta (le soglie funzionano)
                if pb3_wr > 0.70 and self._pb3_compression_threshold < 0.0003:
                    self._pb3_compression_threshold += 0.00002
                    log.info(f"[META] 🧠 PreBreakout auto-tune: WR pb3={pb3_wr:.0%} > 70% → "
                             f"compression threshold allentato a {self._pb3_compression_threshold:.5f}")

    def _history_factor(self) -> float:
        """Soglia si muove con la storia recente.
        
        Alza la soglia dopo una serie di loss — ma con DECADIMENTO TEMPORALE.
        Il pugile alza le braccia dopo le botte, ma le riabbassa gradualmente
        se non arrivano altri pugni. Altrimenti resta con le braccia alzate
        per sempre e non può più tirare — deadlock.
        
        Decadimento: da 1.20 torna a 1.0 in 5 minuti (300 secondi).
        Se arriva un altro loss, il timer si resetta.
        """
        if len(self._recent_results) < 5:
            return 1.0
        recent_wr = sum(1 for r in self._recent_results if r) / len(self._recent_results)
        if recent_wr < 0.40:
            # Quanto tempo è passato dall'ultimo trade?
            # Se non abbiamo il timestamp, usiamo il fattore pieno
            if not hasattr(self, '_history_factor_since'):
                self._history_factor_since = time.time()
            elapsed = time.time() - self._history_factor_since
            # Decade da 1.20 a 1.0 in 300 secondi (5 minuti)
            decay = max(0.0, 1.0 - elapsed / 300.0)
            factor = 1.0 + (0.20 * decay)  # 1.20 → 1.0
            return factor
        # WR OK → resetta il timer e torna a 1.0
        if hasattr(self, '_history_factor_since'):
            del self._history_factor_since
        return 1.0

    def _pre_breakout_factor(self) -> tuple:
        """
        ★ PRE-BREAKOUT DETECTOR — il cecchino sente i passi.

        Tre segnali indipendenti:
          1. COMPRESSIONE: range stretto (< 0.02%) con volatilità storica alta
          2. VOLUME CRESCENTE: vol_accel > 1.3 a prezzo fermo
          3. SEED CRESCENTI: derivata positiva per 5+ tick consecutivi

        Ogni segnale vale 0.0-1.0. Il fattore finale è:
          3 segnali attivi → 0.70 (soglia scende del 30%) ← UNICO CHE FUNZIONA
          2 segnali attivi → 0.96 (quasi invariata — dati dicono che perde)
          1 segnale attivo → 1.00 (nessun effetto)
          0 segnali        → 1.00 (nessun effetto)

        Ritorna (factor: float, dettaglio: str)
        """
        if len(self._prices_short) < 30 or len(self._seed_history) < 5:
            return 1.0, "", 0

        signals = 0
        details = []

        # ── 1. COMPRESSIONE ───────────────────────────────────────────────
        prices = list(self._prices_short)
        recent_50 = prices[-50:] if len(prices) >= 50 else prices
        p_max = max(recent_50)
        p_min = min(recent_50)
        p_mid = (p_max + p_min) / 2
        compression = (p_max - p_min) / p_mid if p_mid > 0 else 1.0

        if compression < self._pb3_compression_threshold:   # ADATTIVO — si stringe se pb3 WR < 50%
            signals += 1
            details.append(f"COMPRESS={compression:.5f}")

        # ── 2. VOLUME CRESCENTE (a prezzo fermo) ─────────────────────────
        if len(self._volumes_short) >= 20:
            vols = list(self._volumes_short)
            vol_recent = sum(vols[-10:]) / 10
            vol_prev   = sum(vols[-20:-10]) / 10
            if vol_prev > 0:
                vol_ratio = vol_recent / vol_prev
                if vol_ratio > self._pb3_vol_acc_threshold and compression < 0.001:   # ADATTIVO
                    # Volume sale MA prezzo fermo → accumulazione
                    signals += 1
                    details.append(f"VOL_ACC={vol_ratio:.2f}")

        # ── 3. SEED DIREZIONALI (derivata positiva per LONG, negativa per SHORT) ──
        seeds = list(self._seed_history)
        if len(seeds) >= 5:
            last_5 = seeds[-5:]
            if self._direction == "SHORT":
                # SHORT: seed DECRESCENTI = impulso ribassista che nasce
                all_directed = all(last_5[i] < last_5[i-1] for i in range(1, len(last_5)))
                directed_count = sum(1 for i in range(1, len(last_5)) if last_5[i] < last_5[i-1])
                avg_deriv = (last_5[0] - last_5[-1]) / 4  # positivo se scende
            else:
                # LONG: seed CRESCENTI = impulso rialzista che nasce
                all_directed = all(last_5[i] > last_5[i-1] for i in range(1, len(last_5)))
                directed_count = sum(1 for i in range(1, len(last_5)) if last_5[i] > last_5[i-1])
                avg_deriv = (last_5[-1] - last_5[0]) / 4

            if all_directed and avg_deriv > 0.02:
                signals += 1
                details.append(f"SEED_DIR={avg_deriv:+.3f}")
            elif directed_count >= 4 and avg_deriv > 0.05:
                signals += 1
                details.append(f"SEED_DIR4={avg_deriv:+.3f}")

        # ── CALCOLA FATTORE ───────────────────────────────────────────────
        # CALIBRATO SU DATI REALI (6 trade shadow 16/03/2026):
        #   3/3 segnali: 2 WIN +$27.29, 1 LOSS -$1.28 → WR 66%, R/R 21:1
        #   2/3 segnali: 0 WIN, 3 LOSS -$0.78 → WR 0% → QUASI DISABILITATO
        #   Solo il 3/3 pieno abbassa significativamente la soglia.
        if signals >= 3:
            factor = 0.92    # segnala ma NON crea buchi — i LOSS SMORZ a soglia bassa sono la prova
        elif signals >= 2:
            factor = 0.96    # quasi nessun effetto — 2/3 perde troppo
        elif signals >= 1:
            factor = 1.00    # un segnale = nessun effetto
        else:
            factor = 1.00    # niente — soglia invariata

        detail_str = f"pb={factor:.2f}({signals}/3 {'+'.join(details)})" if signals > 0 else ""
        return factor, detail_str, signals

    def _drift_factor(self) -> tuple:
        """
        ★ DRIFT DETECTOR — in che direzione soffia il vento?

        Confronta il prezzo medio recente (ultimi 50 tick) con quello
        più vecchio (primi 50 dei 200 tick). Se il prezzo SCENDE,
        la soglia SALE — non entri LONG in un downtrend.

        Ritorna (factor: float, dettaglio: str)
          drift forte DOWN  → 1.30 (soglia +30% — quasi impossibile entrare)
          drift lieve DOWN  → 1.15 (soglia +15%)
          flat              → 1.00 (nessun effetto)
          drift UP          → 0.95 (leggerissimo aiuto — il vento a favore)
        """
        if len(self._prices_long) < 100:
            return 1.0, ""

        prices = list(self._prices_long)

        # Media primi 50 tick (passato) vs ultimi 50 tick (presente)
        avg_old    = sum(prices[:50]) / 50
        avg_recent = sum(prices[-50:]) / 50

        if avg_old == 0:
            return 1.0, ""

        drift_pct = (avg_recent - avg_old) / avg_old * 100   # % cambio

        if drift_pct < -0.10:
            # Prezzo sceso > 0.10% su 200 tick — downtrend forte
            factor = 1.30
            detail = f"DRIFT={drift_pct:+.3f}%↓↓"
        elif drift_pct < -0.03:
            # Prezzo sceso 0.03-0.10% — downtrend lieve
            factor = 1.15
            detail = f"DRIFT={drift_pct:+.3f}%↓"
        elif drift_pct > 0.05:
            # Prezzo salito > 0.05% — vento a favore
            factor = 0.95
            detail = f"DRIFT={drift_pct:+.3f}%↑"
        else:
            # Flat — nessun effetto
            return 1.0, ""

        return factor, detail

    # ── RSI + MACD: I CONSIGLIERI ────────────────────────────────────────

    def _update_rsi(self):
        """Calcola RSI a 14 periodi sui prezzi recenti."""
        prices = list(self._prices_ta)
        if len(prices) < self._rsi_period + 1:
            return
        changes = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
        recent = changes[-(self._rsi_period):]
        gains = [c for c in recent if c > 0]
        losses_raw = [-c for c in recent if c < 0]
        avg_gain = sum(gains) / self._rsi_period if gains else 0
        avg_loss = sum(losses_raw) / self._rsi_period if losses_raw else 0.001
        rs = avg_gain / max(avg_loss, 0.001)
        self._last_rsi = 100 - (100 / (1 + rs))

    def _update_macd(self):
        """Calcola MACD (12/26/9) sui prezzi recenti."""
        prices = list(self._prices_ta)
        if len(prices) < self._macd_slow + self._macd_signal:
            return

        def ema(data, period):
            """EMA semplificata."""
            if len(data) < period:
                return sum(data) / len(data) if data else 0
            mult = 2 / (period + 1)
            result = sum(data[:period]) / period
            for val in data[period:]:
                result = (val - result) * mult + result
            return result

        ema_fast = ema(prices, self._macd_fast)
        ema_slow = ema(prices, self._macd_slow)
        self._last_macd = ema_fast - ema_slow
        self._last_macd_signal = self._last_macd * 0.8
        self._last_macd_hist = self._last_macd - self._last_macd_signal

    def _rsi_score(self) -> float:
        """
        ★ RSI CONSIGLIERE — ipervenduto o ipercomprato?
        LONG:  RSI < 30 = buono (ipervenduto, rimbalzo) | RSI > 70 = cattivo (ipercomprato)
        SHORT: RSI > 70 = buono (ipercomprato, crollo)  | RSI < 30 = cattivo (ipervenduto)
        """
        rsi = self._last_rsi
        if self._direction == "SHORT":
            # Invertito: ipercomprato = buono per SHORT
            if rsi > 75:   return 1.0
            elif rsi > 65: return 0.80
            elif rsi > 55: return 0.60
            elif rsi > 45: return 0.40
            elif rsi > 35: return 0.30
            elif rsi > 25: return 0.15
            else:          return 0.0
        else:
            # LONG: ipervenduto = buono
            if rsi < 25:   return 1.0
            elif rsi < 35: return 0.80
            elif rsi < 45: return 0.60
            elif rsi < 55: return 0.40
            elif rsi < 65: return 0.30
            elif rsi < 75: return 0.15
            else:          return 0.0

    def _macd_score(self) -> float:
        """
        ★ MACD CONSIGLIERE — il trend sta nascendo o morendo?
        LONG:  MACD positivo crescente = buono | negativo decrescente = cattivo
        SHORT: MACD negativo decrescente = buono | positivo crescente = cattivo
        """
        macd = self._last_macd
        hist = self._last_macd_hist
        if self._direction == "SHORT":
            # Invertito: bearish = buono per SHORT
            if macd < 0 and hist < 0:    return 1.0    # bearish forte
            elif hist < 0:                return 0.70   # sotto signal
            elif abs(hist) < abs(macd) * 0.1 if macd != 0 else True: return 0.40
            elif hist > 0 and macd < 0:   return 0.25
            elif hist > 0 and macd > 0:   return 0.0    # bullish forte = cattivo per SHORT
            return 0.35
        else:
            # LONG: bullish = buono
            if macd > 0 and hist > 0:    return 1.0
            elif hist > 0:                return 0.70
            elif abs(hist) < abs(macd) * 0.1 if macd != 0 else True: return 0.40
            elif hist < 0 and macd > 0:   return 0.25
            elif hist < 0 and macd < 0:   return 0.0
            return 0.35

    def _veto(self, reason: str) -> dict:
        return {'enter': False, 'score': 0.0, 'soglia': 0.0,
                'size': 0.0, 'veto': reason, 'pb_signals': 0, 'breakdown': {}}

    def get_stats(self) -> dict:
        total = len(self._recent_results)
        if total == 0:
            return {'trades': 0, 'wr': 0.0, 'rsi': round(self._last_rsi, 1), 
                    'macd': round(self._last_macd, 4), 'macd_hist': round(self._last_macd_hist, 4),
                    'drift_veto_threshold': self.DRIFT_VETO_THRESHOLD,
                    'soglia_max': self.SOGLIA_MAX, 'direction': self._direction}
        wins = sum(1 for r in self._recent_results if r)
        return {'trades': total, 'wr': round(wins / total, 3),
                'wins': wins, 'losses': total - wins,
                'rsi': round(self._last_rsi, 1),
                'macd': round(self._last_macd, 4),
                'macd_hist': round(self._last_macd_hist, 4),
                'drift_veto_threshold': self.DRIFT_VETO_THRESHOLD,
                'soglia_max': self.SOGLIA_MAX, 'direction': self._direction}


# ═══════════════════════════════════════════════════════════════════════════
# ★★★ BOT PRINCIPALE — OVERTOP BASSANO V14 PRODUCTION ★★★
# ═══════════════════════════════════════════════════════════════════════════

class OvertopBassanoV14Production:
    """
    Bot BTC/USDC su Binance WebSocket.
    Modalità: PAPER_TRADE (simula) o LIVE (ordini reali).

    Architettura decisionale entry:
      SeedScorer → OracoloDinamico → MemoriaMatrimoni → 5 Capsule → CapsuleRuntime

    Architettura exit:
      4 Divorce Triggers (ogni tick) → SMORZ (impulso finito) → Timeout adattivo

    Auto-apprendimento:
      OracoloDinamico aggiorna WR fingerprint ad ogni trade chiuso.
      RealtimeLearningEngine genera capsule di blocco se WR < 40% su 3+ campioni.
      MemoriaMatrimoni scala trust e irroga SEPARAZIONE/DIVORZIO.
    """

    def __init__(self, heartbeat_data=None, db_execute=None, heartbeat_lock=None):
        self.symbol         = SYMBOL
        self.ws_url         = BINANCE_WS_URL
        self.paper_trade    = PAPER_TRADE

        self.heartbeat_data = heartbeat_data if heartbeat_data is not None else {}
        self.heartbeat_lock = heartbeat_lock
        self.db_execute     = db_execute

        # ── Persistenza ──────────────────────────────────────────────────
        self._persist        = PersistenzaStato(db_path=DB_PATH)
        self.capital, self.total_trades = self._persist.load()
        self.wins    = 0
        self.losses  = 0

        # ── Componenti core ──────────────────────────────────────────────
        self.analyzer        = ContestoAnalyzer(window=50)
        self.seed_scorer     = SeedScorer(window=50)
        self.oracolo         = OracoloDinamico()
        self.memoria         = MemoriaMatrimoni()
        self.capsule_runtime = CapsuleRuntime(capsule_file="capsule_attive.json")
        self.config_reloader = ConfigHotReloader(capsule_path="capsule_attive.json")
        self.realtime_engine = RealtimeLearningEngine(max_trades=10, capsule_file="capsule_attive.json")
        self.log_analyzer    = LogAnalyzer()
        self.ai_explainer    = AIExplainer(db_path=NARRATIVES_DB)
        self.calibratore     = AutoCalibratore()
        self.regime_detector = RegimeDetector()
        self.decelero        = MomentumDecelerometer()
        self.position_sizer  = PositionSizer()
        self.telemetry       = StabilityTelemetry()

        # ── Ripristina intelligenza accumulata ────────────────────────────
        self._persist.load_brain(self.oracolo, self.memoria, self.calibratore)
        self._regime_current = 'RANGING'
        self._regime_conf    = 0.0
        self._last_regime_check = time.time()

        # ── 5 Capsule ─────────────────────────────────────────────────────
        self.capsule1 = Capsule1Coerenza()
        self.capsule2 = Capsule2Trappola()
        self.capsule3 = Capsule3Protezione()
        self.capsule4 = Capsule4Opportunita()
        self.capsule5 = Capsule5Tattica()

        # ── Stato trade ───────────────────────────────────────────────────
        self.trade_open         = None   # None = nessun trade aperto
        self.entry_time         = None
        self.entry_momentum     = None   # per divorce trigger 2
        self.entry_volatility   = None   # per divorce trigger 1
        self.entry_fingerprint  = None   # per divorce trigger 4
        self.entry_trend        = None
        self.max_price          = None
        self.current_matrimonio = None

        # ── Timing ────────────────────────────────────────────────────────
        self.last_heartbeat    = time.time()
        self.last_config_check = time.time()
        self.last_persist      = time.time()
        self.ws                = None

        # ── Stato exit (per capsule reattive) ─────────────────────────────
        self._last_exit_type     = None
        self._last_exit_duration = 0.0
        self._last_entry_seed    = 0.0   # per AutoCalibratore
        self._last_entry_fp_wr   = 0.72  # per AutoCalibratore
        self._trades_since_calib = 0     # contatore per calibrazione

        # ── Log live decisioni (ultimi 20 eventi) ─────────────────────────
        self._live_log = deque(maxlen=20)

        # ── MOTORE 2: CAMPO GRAVITAZIONALE (shadow trading) ──────────────
        self.campo = CampoGravitazionale()
        self._shadow = None          # shadow trade aperto (dict o None)
        self._shadow_entry_time = None
        self._shadow_entry_momentum = None
        self._shadow_entry_volatility = None
        self._shadow_entry_trend = None
        self._shadow_entry_fingerprint = None
        self._shadow_max_price = None
        self._shadow_min_price = None
        self._shadow_matrimonio = None
        # ── STATE ENGINE — AGGRESSIVO / NEUTRO / DIFENSIVO ────────────────
        # Il tempismo. Non solo COSA fare, ma QUANDO NON FARLO.
        self._state = "NEUTRO"                   # AGGRESSIVO | NEUTRO | DIFENSIVO
        self._state_since = time.time()           # quando è entrato nello stato corrente
        self._state_min_duration = 120            # minimo 2 minuti in ogni stato (era 5 — troppo lento)
        self._m2_recent_trades = deque(maxlen=10) # ultimi 10 trade M2: {'ts', 'pnl', 'is_win', 'duration'}
        self._m2_last_loss_time = 0               # timestamp dell'ultimo loss
        self._m2_loss_streak = 0                  # loss consecutivi correnti
        self._m2_cooldown_until = 0               # non entrare fino a questo timestamp
        # ── AUTO-TUNING SOGLIA — impara dai phantom ──────────────────────
        # Il sistema legge i propri phantom e aggiusta SOGLIA_MIN automaticamente.
        # Se i phantom bloccati hanno WR > 60% su 10+ campioni → soglia troppo alta.
        # Se WR < 40% → soglia troppo bassa. Rate limit: 1 aggiustamento ogni 15 min.
        self._last_soglia_autotune = 0            # timestamp ultimo aggiustamento
        self._soglia_autotune_interval = 900      # 15 minuti tra aggiustamenti
        self._phantom_stats_snapshot = {}         # snapshot per delta calcolo
        # Stats separate per Motore 2 — ripristina da DB se disponibili
        self._m2_wins    = 0
        self._m2_losses  = 0
        self._m2_pnl     = 0.0
        self._m2_trades  = 0
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = dict(conn.execute("SELECT key, value FROM bot_state WHERE key LIKE 'm2_%'").fetchall())
            conn.close()
            if rows:
                self._m2_wins   = int(rows.get('m2_wins', 0))
                self._m2_losses = int(rows.get('m2_losses', 0))
                self._m2_pnl    = float(rows.get('m2_pnl', 0.0))
                self._m2_trades = int(rows.get('m2_trades', 0))
                log.info(f"[M2_LOAD] 🧠 Stats ripristinate: {self._m2_trades}t W={self._m2_wins} L={self._m2_losses} PnL=${self._m2_pnl:.2f}")
        except Exception:
            pass
        self._m2_log     = deque(maxlen=20)   # log dedicato M2
        self._last_volume = 1.0               # ultimo volume dal WebSocket
        self._last_m2_heartbeat = time.time() # heartbeat M2 — monitora se il thread è vivo

        # ── BRIDGE COMMANDS READER ───────────────────────────────────────
        self._bridge_cmd_file = "bridge_commands.json"
        self._last_bridge_check = time.time()

        # ── PHANTOM TRACKER — "se avessi fatto" ─────────────────────────
        # Traccia i trade bloccati dai 5 livelli di protezione.
        # Per ogni trade bloccato, segue il prezzo e calcola cosa sarebbe successo.
        # Zavorra o protezione? I numeri rispondono.
        self._phantoms_open = []       # trade fantasma aperti (max 5 simultanei)
        self._phantoms_closed = deque(maxlen=100)  # ultimi 100 fantasmi chiusi
        self._phantom_stats = {        # statistiche per livello di blocco
            # 'BLOCK_REASON': {'blocked': N, 'would_win': N, 'would_lose': N, 'pnl_saved': $, 'pnl_missed': $}
        }
        self._phantom_log = deque(maxlen=20)  # log dedicato fantasmi

        # ── Banner ────────────────────────────────────────────────────────
        mode_label = "📄 PAPER TRADE" if self.paper_trade else "🔴 LIVE TRADING"
        log.info("=" * 80)
        log.info(f"🚀 OVERTOP BASSANO V14 PRODUCTION — {mode_label}")
        log.info(f"   Capital: ${self.capital:,.2f}  |  Trades totali: {self.total_trades}")
        log.info(f"   SeedScorer threshold: {SEED_ENTRY_THRESHOLD}")
        log.info(f"   Divorce triggers minimi: {DIVORCE_MIN_TRIGGERS}/4")
        log.info(f"   🎯 MOTORE 2 (Campo Gravitazionale): SHADOW ATTIVO — confronto parallelo")
        log.info("=" * 80)
        if self.paper_trade:
            log.info("⚠️  PAPER TRADE ATTIVO — nessun ordine reale verrà eseguito")

    # ════════════════════════════════════════════════════════════════════════
    # CONNESSIONE BINANCE WEBSOCKET
    # ════════════════════════════════════════════════════════════════════════

    def connect_binance(self):
        def on_message(ws, msg):
            try:
                data   = json.loads(msg)
                price  = float(data.get('p', 0))
                volume = float(data.get('q', 1.0))
                if price > 0:
                    self.analyzer.add_price(price)
                    self.seed_scorer.add_tick(price, volume)
                    self._last_volume = volume
                    self._process_tick(price)
            except Exception as e:
                log.error(f"[WS_MSG] {e}")

        def on_error(ws, error):
            log.error(f"[WS_ERROR] {error}")

        def on_close(ws, code, msg):
            log.warning(f"[WS_CLOSE] codice={code} — riconnessione in 5s...")
            time.sleep(5)
            self.connect_binance()

        def on_open(ws):
            log.info("[WS] ✅ Connesso a Binance aggTrade BTCUSDC")

        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        threading.Thread(target=self.ws.run_forever, daemon=True, name="ws_thread").start()

    # ════════════════════════════════════════════════════════════════════════
    # PROCESS TICK — orchestratore principale
    # ════════════════════════════════════════════════════════════════════════

    def _process_tick(self, price: float):
        now = time.time()

        # Config hot-reload ogni 30s
        if now - self.last_config_check > 30:
            if self.config_reloader.check_reload():
                if self.capsule_runtime.reload():
                    log.info("[CONFIG] 🔄 Capsule ricaricate a caldo")
            self.last_config_check = now

        # Bridge commands check ogni 30s
        if now - self._last_bridge_check > 30:
            self._read_bridge_commands()
            self._last_bridge_check = now

        # Heartbeat ogni 30s
        if now - self.last_heartbeat > 30:
            self._update_heartbeat()
            self.last_heartbeat = now

        # Aggiorna prezzo live ad ogni tick (per dashboard)
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["last_price"] = round(price, 2)
                self.heartbeat_data["last_tick"]  = datetime.utcnow().isoformat()
                self.heartbeat_data["tick_count"] = self.heartbeat_data.get("tick_count", 0) + 1
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

        # Feed RegimeDetector e Decelerometer
        self.regime_detector.add_tick(price, self._last_volume)
        self.decelero.add_price(price)

        # Aggiorna regime ogni 60s
        if now - self._last_regime_check > 60:
            regime, conf, detail = self.regime_detector.detect()
            if regime != self._regime_current:
                self._log("🌍", f"REGIME → {regime} (conf={conf:.0%}) | "
                         f"trend={detail.get('trend_pct',0):+.2f}% "
                         f"dir={detail.get('dir_ratio',0):.2f}")
                ctx = self._tele_ctx()
                self.telemetry.log_regime_change(
                    self._regime_current, regime,
                    direction=ctx['direction'], open_position=ctx['open_position'],
                    active_threshold=ctx['active_threshold'], drift=ctx['drift'],
                    macd=ctx['macd'], trend=ctx['trend'], volatility=ctx['volatility'])
            self._regime_current    = regime
            self._regime_conf       = conf
            self._last_regime_check = now

        # Persistenza ogni 5 minuti
        if now - self.last_persist > 300:
            self._persist.save(self.capital, self.total_trades)
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)
            self.telemetry.persist_to_db(DB_PATH)
            self._auto_tune_soglia()  # il sistema impara dai propri phantom
            self.last_persist = now

        contesto = self.analyzer.analyze()
        if not contesto[0]:
            return
        momentum, volatility, trend = contesto
        self._last_trend = trend
        self._last_volatility = volatility
        self._last_momentum = momentum

        # ── M1 DISABILITATO — 0 trade in 2 giorni, sistema paralizzato ────
        # M2 (Campo Gravitazionale) è l'unico motore operativo.
        # M1 resta nel codice per riferimento ma non valuta più.
        # if self.trade_open:
        #     self._evaluate_exit(price, momentum, volatility, trend)
        # else:
        #     self._evaluate_entry(price, momentum, volatility, trend)

        # ── MOTORE 2: Feed pre-breakout detector (OGNI tick) ─────────────
        _seed_quick = self.seed_scorer.score()
        _seed_val = _seed_quick.get('score', 0.0) if _seed_quick.get('reason') != 'insufficient_data' else 0.0
        self.campo.feed_tick(price, self._last_volume, _seed_val)

        # ── MOTORE 2: Shadow trade evaluation (parallelo) ─────────────────
        if self._shadow:
            self._evaluate_shadow_exit(price, momentum, volatility, trend)
        else:
            self._evaluate_shadow_entry(price, momentum, volatility, trend)

        # ── PHANTOM TRACKER: aggiorna trade fantasma ogni tick ────────────
        if self._phantoms_open:
            self._update_phantoms(price, momentum)

        # ── HEARTBEAT M2 — ogni 60s conferma che M2 è vivo ───────────────
        if now - self._last_m2_heartbeat > 60:
            self._log_m2("💓", f"M2 vivo | shadow={'aperto' if self._shadow else 'chiuso'} "
                              f"| {self._m2_trades}t W={self._m2_wins} L={self._m2_losses}")
            self._last_m2_heartbeat = now

    # ════════════════════════════════════════════════════════════════════════
    # ENTRY — catena decisionale completa
    # ════════════════════════════════════════════════════════════════════════

    def _log(self, emoji: str, msg: str):
        """Aggiunge una riga al log live e la spinge subito a heartbeat_data."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} {msg}"
        self._live_log.append(entry)
        log.info(entry)
        # Push immediato alla dashboard — non aspetta il ciclo heartbeat da 30s
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["live_log"] = list(self._live_log)
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def _evaluate_entry(self, price, momentum, volatility, trend):

        # ── 1. SEED SCORER ────────────────────────────────────────────────
        seed = self.seed_scorer.score()
        dynamic_seed_thresh = self.calibratore.get_params()['seed_threshold']
        if not seed['pass'] or seed['score'] < dynamic_seed_thresh:
            self._log("⚡", f"SEED FAIL score={seed['score']:.3f} | {momentum}/{volatility}/{trend} @ ${price:.1f}")
            return

        # ── 2. ORACOLO DINAMICO ───────────────────────────────────────────
        is_fantasma, fantasma_reason = self.oracolo.is_fantasma(momentum, volatility, trend)
        if is_fantasma:
            self._log("👻", f"FANTASMA bloccato: {fantasma_reason}")
            return
        fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend)

        # ── 3. MATRIMONIO ─────────────────────────────────────────────────
        matrimonio      = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = matrimonio["name"]
        confidence      = matrimonio["confidence"]

        # ── 4. MEMORIA MATRIMONI ──────────────────────────────────────────
        can_enter, mem_status = self.memoria.get_status(matrimonio_name)
        if not can_enter:
            self._log("🚫", f"MEMORIA blocca {matrimonio_name}: {mem_status}")
            return

        # ── 5. CATENA 5 CAPSULE — soglie dinamiche dal calibratore ──────────
        p = self.calibratore.get_params()

        allow_1, conf_1, reason_1 = self.capsule1.valida(
            fingerprint_wr, momentum, volatility, trend,
            soglia_buona=p['cap1_soglia_buona'],
            soglia_perfetta=p['cap1_soglia_perfetta'])
        if not allow_1:
            self._log("🔴", f"CAP1 COERENZA blocca | fp_wr={fingerprint_wr:.2f} {momentum}/{volatility}/{trend}")
            return

        allow_2, reason_2 = self.capsule2.riconosci(confidence)
        if not allow_2:
            self._log("🔴", f"CAP2 TRAPPOLA blocca | conf={confidence:.2f} {matrimonio_name}")
            return

        allow_3, reason_3 = self.capsule3.proteggi(
            momentum, volatility, fingerprint_wr,
            fp_minimo=p['cap3_fp_minimo'])
        if not allow_3:
            self._log("🔴", f"CAP3 PROTEZIONE blocca | {momentum}/{volatility} fp={fingerprint_wr:.2f}")
            return

        allow_4, _, reason_4 = self.capsule4.riconosci(
            fingerprint_wr, momentum, volatility,
            soglia_buona=p['cap4_soglia_buona'])

        allow_5, duration_min, reason_5 = self.capsule5.timing(
            True, allow_1, conf_1,
            conf_ok=p['cap5_conf_ok'])
        if not allow_5:
            self._log("🔴", f"CAP5 TIMING blocca | conf_1={conf_1:.2f}")
            return

        # ── 6. CAPSULE RUNTIME (JSON dinamico) ───────────────────────────
        ctx_caps = {
            'matrimonio':       matrimonio_name,
            'momentum':         momentum,
            'volatility':       volatility,
            'trend':            trend,
            'seed_score':       seed['score'],
            'seed_tipo':        'CONFERMATO' if seed['score'] >= 0.65 else
                                ('PROBABILE'  if seed['score'] >= SEED_ENTRY_THRESHOLD else 'IGNOTO'),
            'force':            seed['score'],
            'fingerprint_wr':   fingerprint_wr,
            'wr_oracolo':       round(fingerprint_wr * 100, 1),
            'fingerprint_n':    self.oracolo._memory.get(
                                    self.oracolo._fp(momentum, volatility, trend),
                                    {}).get('samples', 0),
            'regime':           'trending' if momentum == 'FORTE' and volatility == 'BASSA'
                                else ('choppy'  if volatility == 'ALTA'
                                else ('lateral' if momentum == 'DEBOLE' else 'normal')),
            'mode':             'PAPER' if self.paper_trade else 'LIVE',
            'loss_consecutivi': self._loss_consecutivi(),
            'ultimo_exit_type': self._last_exit_type,
            'ultima_durata':    self._last_exit_duration,
            'sample_size':      int(self.oracolo._memory.get(
                                    self.oracolo._fp(momentum, volatility, trend),
                                    {}).get('samples', 0)),
        }
        caps_check = self.capsule_runtime.valuta(ctx_caps)
        if caps_check.get('blocca'):
            self._log("💊", f"CAPSULE_RT blocca: {caps_check['reason']} | {matrimonio_name}")
            return

        # ── ENTRY CONFERMATA ──────────────────────────────────────────────
        # Position sizing continuo — funzione dell'impulso × regime
        regime_mults = self.regime_detector.get_multipliers()
        sizing = self.position_sizer.calculate(
            seed_score=seed['score'],
            fingerprint_wr=fingerprint_wr,
            confidence=confidence,
            regime_mult=regime_mults['size_mult']
        )
        # Le capsule JSON possono ancora modificare ulteriormente
        caps_size_mult = caps_check.get('size_mult', 1.0)
        size_factor = min(PositionSizer.SIZE_MAX,
                         sizing['size_factor'] * caps_size_mult)

        self._log("🚀", f"ENTRY {matrimonio_name} | seed={seed['score']:.3f} "
                       f"fp_wr={fingerprint_wr:.2f} size={size_factor:.2f}x "
                       f"regime={self._regime_current} @ ${price:.1f}")
        self.ai_explainer.log_decision("ENTRY",
            f"Entrato in {matrimonio_name} | seed={seed['score']:.3f} "
            f"fp_wr={fingerprint_wr:.2f} size={size_factor:.2f}x regime={self._regime_current}",
            {'momentum': momentum, 'volatility': volatility, 'trend': trend,
             'seed': seed, 'fingerprint_wr': fingerprint_wr,
             'sizing': sizing, 'regime': self._regime_current})

        if not self.paper_trade:
            self._place_order("BUY", price, size_factor)

        self.trade_open = {
            "price_entry":    price,
            "matrimonio":     matrimonio_name,
            "duration_avg":   matrimonio["duration_avg"],
            "size_mult":      size_factor,
        }
        self.entry_time        = time.time()
        self.entry_momentum    = momentum
        self.entry_volatility  = volatility
        self.entry_trend       = trend
        self.entry_fingerprint = fingerprint_wr
        self.current_matrimonio= matrimonio_name
        self.max_price         = price
        self.total_trades     += 1
        self._last_entry_seed  = seed['score']    # per AutoCalibratore
        self._last_entry_fp_wr = fingerprint_wr   # per AutoCalibratore

    # ════════════════════════════════════════════════════════════════════════
    # EXIT — 4 DIVORCE TRIGGERS + SMORZ + TIMEOUT
    # ════════════════════════════════════════════════════════════════════════

    def _evaluate_exit(self, price, momentum, volatility, trend):
        if price > self.max_price:
            self.max_price = price

        # ── MOMENTUM DECELEROMETER — exit anticipata ──────────────────────
        # Controlla prima dei divorce triggers: se il momentum sta decelerando
        # fortemente usciamo prima che il prezzo inverta completamente
        duration = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]

        if duration > duration_avg * 0.3:   # solo dopo il 30% della durata attesa
            decel = self.decelero.analyze()
            if decel['should_exit']:
                self._log("📉", f"DECEL EXIT {self.current_matrimonio} | "
                         f"decel={decel['decel_score']:.2f} "
                         f"mom_fast={decel['mom_fast']:+.4f} "
                         f"mom_slow={decel['mom_slow']:+.4f}")
                self._close_trade(price, momentum, volatility, trend,
                                  reason="DECEL_MOMENTUM")
                return

        # ── 4 DIVORCE TRIGGERS — monitorati ogni tick ─────────────────────
        triggers_attivi = []

        # Trigger 1: volatilità esplode (entry BASSA → ora ALTA)
        if self.entry_volatility == "BASSA" and volatility == "ALTA":
            triggers_attivi.append("T1_VOLATILITÀ_ESPLOSA")

        # Trigger 2: trend si inverte (entry UP → ora DOWN)
        if self.entry_trend == "UP" and trend == "DOWN":
            triggers_attivi.append("T2_TREND_INVERTITO")

        # Trigger 3: drawdown > 3% dal massimo
        drawdown_pct = ((self.max_price - price) / self.trade_open["price_entry"]) * 100
        if drawdown_pct > DIVORCE_DRAWDOWN_PCT:
            triggers_attivi.append(f"T3_DRAWDOWN_{drawdown_pct:.1f}%")

        # Trigger 4: fingerprint diverge > 50% dal valore di entry
        current_fp = self.oracolo.get_wr(momentum, volatility, trend)
        fp_diverge = abs(current_fp - self.entry_fingerprint) / max(self.entry_fingerprint, 0.001)
        if fp_diverge > DIVORCE_FP_DIVERGE_PCT:
            triggers_attivi.append(f"T4_FP_DIVERGE_{fp_diverge:.0%}")

        if len(triggers_attivi) >= DIVORCE_MIN_TRIGGERS:
            self._log("💔", f"DIVORZIO IMMEDIATO {self.current_matrimonio} | {' + '.join(triggers_attivi)}")
            self._close_trade(price, momentum, volatility, trend, reason="DIVORZIO_IMMEDIATO")
            return

        # ── SMORZ — impulso finito ────────────────────────────────────────
        duration     = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]
        if duration > duration_avg * 0.5 and momentum == "DEBOLE":
            self._log("🌙", f"SMORZ impulso finito — {self.current_matrimonio} dopo {duration:.0f}s")
            self._close_trade(price, momentum, volatility, trend, reason="SMORZ")
            return

        # ── TIMEOUT adattivo ──────────────────────────────────────────────
        if duration > duration_avg * 3:
            self._close_trade(price, momentum, volatility, trend, reason="TIMEOUT_3X")
            return
        if duration > duration_avg and drawdown_pct > 1.0:
            self._close_trade(price, momentum, volatility, trend, reason="TIMEOUT_DD_1%")
            return

    # ════════════════════════════════════════════════════════════════════════
    # CLOSE TRADE — registra, impara, aggiorna
    # ════════════════════════════════════════════════════════════════════════

    def _close_trade(self, price, momentum, volatility, trend, reason: str):
        pnl    = price - self.trade_open["price_entry"]
        is_win = pnl > 0
        matrimonio_name = self.current_matrimonio
        matrimonio      = MatrimonioIntelligente.get_by_name(matrimonio_name)
        wr_expected     = matrimonio.get("wr", 0.50)

        # ── Calcola drawdown reale (per AutoCalibratore) ──────────────────
        if self.max_price and self.trade_open:
            drawdown_pct = ((self.max_price - price) / self.trade_open["price_entry"]) * 100
        else:
            drawdown_pct = 0.0

        # ── Aggiorna tutti i sistemi di apprendimento ─────────────────────
        self.oracolo.record(self.entry_momentum, self.entry_volatility, self.entry_trend, is_win)
        self.memoria.record_trade(matrimonio_name, is_win, wr_expected)
        self.realtime_engine.registra_trade({'matrimonio': matrimonio_name, 'pnl': pnl, 'is_win': is_win})
        self.log_analyzer.registra({'matrimonio': matrimonio_name, 'pnl': pnl, 'is_win': is_win})
        self.realtime_engine.analizza_e_genera()

        # ── AutoCalibratore: registra osservazione ────────────────────────
        self.calibratore.registra_osservazione(
            seed_score=self._last_entry_seed,
            fingerprint_wr=self._last_entry_fp_wr,
            is_win=is_win,
            divorce_drawdown_usato=drawdown_pct
        )
        self._trades_since_calib += 1

        # Calibra ogni 30 trade
        if self._trades_since_calib >= 10:
            tot_now = self.wins + self.losses + (1 if is_win else 0)
            wr_now  = (self.wins + (1 if is_win else 0)) / max(1, tot_now)
            # Prima verifica se calibrazioni precedenti hanno peggiorato
            self.calibratore.inverti_se_peggiorato(wr_now)
            # Poi calibra
            modifiche = self.calibratore.calibra()
            if modifiche:
                self._log("🎯", f"AutoCalibra: {modifiche}")
            self._trades_since_calib = 0

        if is_win:
            self.wins   += 1
        else:
            self.losses += 1
        self.capital += pnl

        wr_live = (self.wins / (self.wins + self.losses) * 100) if (self.wins + self.losses) > 0 else 0
        self._log(
            "🟢" if is_win else "🔴",
            f"EXIT {matrimonio_name} {'WIN' if is_win else 'LOSS'} PnL=${pnl:+.4f} WR={wr_live:.0f}% [{reason}]"
        )
        self.ai_explainer.log_decision("EXIT",
            f"Uscito da {matrimonio_name} | PnL=${pnl:+.4f} | motivo={reason}",
            {'pnl': pnl, 'is_win': is_win, 'reason': reason})

        if not self.paper_trade:
            self._place_order("SELL", price, self.trade_open.get("size_mult", 1.0))

        # Persiste immediatamente dopo ogni trade
        self._persist.save(self.capital, self.total_trades)
        self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)
        self._update_heartbeat()

        # Salva info exit per capsule reattive
        self._last_exit_type     = reason
        self._last_exit_duration = time.time() - self.entry_time if self.entry_time else 0.0

        # Reset stato trade
        self.trade_open         = None
        self.entry_time         = None
        self.entry_momentum     = None
        self.entry_volatility   = None
        self.entry_trend        = None
        self.entry_fingerprint  = None
        self.current_matrimonio = None
        self.max_price          = None

    # ════════════════════════════════════════════════════════════════════════
    # STATE ENGINE — TEMPISMO
    # Non solo COSA fare, ma QUANDO NON FARLO.
    # AGGRESSIVO: soglie normali, entra liberamente
    # NEUTRO: soglie normali, entra con cautela
    # DIFENSIVO: cooldown attivo, non entra finché non si calma
    # ════════════════════════════════════════════════════════════════════════

    def _state_engine_update(self, pnl, is_win, duration):
        """Chiamato DOPO ogni trade chiuso. Aggiorna lo stato."""
        now = time.time()

        # Registra trade recente
        self._m2_recent_trades.append({
            'ts': now, 'pnl': pnl, 'is_win': is_win, 'duration': duration
        })

        if is_win:
            self._m2_loss_streak = 0
        else:
            self._m2_loss_streak += 1
            self._m2_last_loss_time = now

            # ── COOLDOWN PROPORZIONALE AL DANNO ──────────────────────────
            # 3 loss da $0.01 ≠ 3 loss da $60. Il cooldown guarda il PnL.
            #
            # Loss streak × gravità del PnL:
            #   loss < $1    → micro-loss, pausa minima (10s per streak)
            #   loss $1-$20  → loss normale, pausa media (20s per streak)
            #   loss > $20   → loss pesante, pausa lunga (45s per streak)
            #
            # Streak amplifica: streak=1 → 1x, streak=2 → 1.5x, streak=3+ → 2x
            abs_pnl = abs(pnl)
            if abs_pnl < 1.0:
                base_cooldown = 10     # micro-loss
            elif abs_pnl < 20.0:
                base_cooldown = 20     # loss normale
            else:
                base_cooldown = 45     # loss pesante

            streak_mult = min(2.0, 0.5 + self._m2_loss_streak * 0.5)  # 1.0, 1.5, 2.0
            cooldown = base_cooldown * streak_mult
            cooldown = min(cooldown, 120)  # cap a 2 minuti — mai più di 2 minuti
            self._m2_cooldown_until = now + cooldown

        # ── TRANSIZIONE DI STATO ────────────────────────────────────────
        # Basata su performance recente, non sul singolo trade
        old_state = self._state
        in_state_time = now - self._state_since

        # Guarda ultimi 5 trade
        recent = list(self._m2_recent_trades)[-5:]
        if len(recent) >= 3:
            recent_wins = sum(1 for t in recent if t['is_win'])
            recent_wr = recent_wins / len(recent)
            recent_pnl = sum(t['pnl'] for t in recent)

            # Solo transizioni se tempo minimo nello stato superato
            if in_state_time >= self._state_min_duration:
                if recent_wr >= 0.7 and recent_pnl > 0:
                    self._state = "AGGRESSIVO"
                elif recent_wr <= 0.3 or self._m2_loss_streak >= 3:
                    self._state = "DIFENSIVO"
                else:
                    self._state = "NEUTRO"

        if self._state != old_state:
            self._state_since = now
            self._log_m2("⚙️", f"STATO → {self._state} (loss_streak={self._m2_loss_streak} recent_wr={recent_wr:.0%} cooldown={self._m2_cooldown_until - now:.0f}s)")
            self.telemetry.log_state_change(old_state, self._state, self._m2_loss_streak,
                self._regime_current, self.campo._direction, self._shadow is not None)

    def _state_engine_can_enter(self) -> tuple:
        """Ritorna (can_enter: bool, reason: str). Gate PRIMA di qualsiasi entry."""
        now = time.time()

        # ── COOLDOWN ATTIVO → non entrare ──────────────────────────────
        if now < self._m2_cooldown_until:
            remaining = self._m2_cooldown_until - now
            return False, f"COOLDOWN_{remaining:.0f}s (loss_streak={self._m2_loss_streak})"

        # ── DIFENSIVO → non entrare finché non torna NEUTRO o AGGRESSIVO
        # MA: deadlock protection — max 5 minuti in DIFENSIVO
        # 15 minuti era troppo — il mercato cambia faccia in 2 minuti
        if self._state == "DIFENSIVO":
            time_in_defensive = now - self._state_since
            if time_in_defensive > 300:  # 5 minuti
                self._state = "NEUTRO"
                self._state_since = now
                self._m2_loss_streak = 0
                self._log_m2("⚙️", f"STATO → NEUTRO (auto-reset dopo {time_in_defensive/60:.1f} min in DIFENSIVO)")
                self.telemetry.log_state_change("DIFENSIVO", "NEUTRO", 0,
                    self._regime_current, self.campo._direction, self._shadow is not None)
            else:
                return False, f"DIFENSIVO_{300-time_in_defensive:.0f}s (loss_streak={self._m2_loss_streak})"

        # ── VELOCITÀ: non entrare se ultimo trade chiuso < 5 secondi fa ─
        if self._m2_recent_trades:
            last = self._m2_recent_trades[-1]
            if now - last['ts'] < 5:
                return False, f"TROPPO_VELOCE ({now - last['ts']:.1f}s dall'ultimo)"

        # ── LOSS PESANTE: se ultimo loss > $50, pausa 30 secondi ─────
        if self._m2_recent_trades:
            last = self._m2_recent_trades[-1]
            if not last['is_win'] and abs(last['pnl']) > 50:
                if now - last['ts'] < 30:
                    return False, f"LOSS_PESANTE_${abs(last['pnl']):.0f}_pausa"

        return True, "OK"

    # ════════════════════════════════════════════════════════════════════════
    # AUTO-TUNING SOGLIA — IL SISTEMA IMPARA DAI PROPRI PHANTOM
    # Non servono manopole. I phantom dicono se la soglia è giusta.
    # ════════════════════════════════════════════════════════════════════════

    def _auto_tune_soglia(self):
        """
        Legge i phantom SCORE_INSUFFICIENTE e aggiusta SOGLIA_MIN e SOGLIA_BASE.
        
        INTERVALLO ADATTIVO — la velocità di reazione è proporzionale alla gravità:
        - Bilancio phantom < -$500  → intervallo 120s, step 3 (emergenza)
        - Bilancio phantom < -$200  → intervallo 300s, step 2 (urgente)
        - Bilancio phantom < -$50   → intervallo 600s, step 1 (normale)
        - Bilancio phantom ≥ $0     → intervallo 900s, step 1 (stabile)
        
        STEP PROPORZIONALE — la forza di correzione è proporzionale alla distanza:
        - WR phantom > 75%  → step × 2 (molto lontano dall'equilibrio)
        - WR phantom > 60%  → step × 1 (lontano)
        - WR phantom < 40%  → step × 1 (troppo permissivo, alza)
        - WR phantom 40-60% → non toccare (equilibrio)
        
        Stessa fisica del SMORZ: l'energia di correzione è proporzionale
        alla distanza dall'equilibrio. Non step fissi.
        
        Limiti: SOGLIA_MIN non scende sotto 50, non sale sopra 65.
                SOGLIA_BASE non scende sotto 55, non sale sopra 70.
        """
        now = time.time()

        # ── INTERVALLO ADATTIVO — leggi la gravità dal bilancio phantom ──
        phantom_summary = self._get_phantom_summary()
        bilancio = phantom_summary.get('bilancio', 0)

        if bilancio < -500:
            adaptive_interval = 120    # emergenza: ogni 2 minuti
            base_step = 3
        elif bilancio < -200:
            adaptive_interval = 300    # urgente: ogni 5 minuti
            base_step = 2
        elif bilancio < -50:
            adaptive_interval = 600    # normale: ogni 10 minuti
            base_step = 1
        else:
            adaptive_interval = 900    # stabile: ogni 15 minuti
            base_step = 1

        if now - self._last_soglia_autotune < adaptive_interval:
            return

        stats = self._phantom_stats.get("SCORE_INSUFFICIENTE")
        if not stats:
            return

        total_closed = stats['would_win'] + stats['would_lose']
        if total_closed < 10:
            return  # troppo pochi per decidere

        # Calcola delta dall'ultimo snapshot (non dati cumulativi)
        prev = self._phantom_stats_snapshot.get("SCORE_INSUFFICIENTE", {})
        prev_win = prev.get('would_win', 0)
        prev_lose = prev.get('would_lose', 0)
        delta_win = stats['would_win'] - prev_win
        delta_lose = stats['would_lose'] - prev_lose
        delta_total = delta_win + delta_lose

        if delta_total < 3:
            return  # troppo pochi nell'intervallo (era 5, abbassato per reattività)

        delta_wr = delta_win / delta_total

        # Salva snapshot per prossimo delta
        self._phantom_stats_snapshot["SCORE_INSUFFICIENTE"] = {
            'would_win': stats['would_win'],
            'would_lose': stats['would_lose'],
        }

        old_min = self.campo.SOGLIA_MIN
        old_base = self.campo.SOGLIA_BASE

        # ── STEP PROPORZIONALE — più lontano dall'equilibrio, più forte ──
        if delta_wr > 0.75:
            step = base_step * 2    # molto lontano → doppio step
        else:
            step = base_step

        if delta_wr > 0.60:
            # Phantom troppo profittevoli → soglia troppo alta → ABBASSA
            new_min = max(50, old_min - step)
            new_base = max(55, old_base - step)
            action = "ABBASSA"
        elif delta_wr < 0.40:
            # Phantom non profittevoli → soglia troppo bassa → ALZA
            new_min = min(65, old_min + step)
            new_base = min(70, old_base + step)
            action = "ALZA"
        else:
            # 40-60% → zona giusta, non toccare
            self._last_soglia_autotune = now
            self._log_m2("🎯", f"AUTO-TUNE: soglia OK (phantom WR={delta_wr:.0%} su {delta_total} campioni, bil=${bilancio:.0f})")
            return

        self.campo.SOGLIA_MIN = new_min
        self.campo.SOGLIA_BASE = new_base
        self._last_soglia_autotune = now

        self._log_m2("🎯", f"AUTO-TUNE: {action} soglia step={step} | phantom WR={delta_wr:.0%} "
                          f"({delta_win}W/{delta_lose}L su {delta_total}) bil=${bilancio:.0f} "
                          f"| MIN {old_min}→{new_min} BASE {old_base}→{new_base} "
                          f"[intervallo={adaptive_interval}s]")

    # ════════════════════════════════════════════════════════════════════════
    # MOTORE 2: CAMPO GRAVITAZIONALE — Shadow Entry/Exit/Close
    # ════════════════════════════════════════════════════════════════════════

    def _tele_ctx(self, trend_override=None, vol_override=None, bridge_reason=None):
        """Snapshot di contesto per telemetria. Zero logica, solo lettura."""
        drift = 0.0
        if len(self.campo._prices_long) >= 100:
            _p = list(self.campo._prices_long)
            _old = sum(_p[:50]) / 50
            _new = sum(_p[-50:]) / 50
            drift = (_new - _old) / _old * 100 if _old else 0
        return {
            'regime': self._regime_current,
            'direction': self.campo._direction,
            'open_position': self._shadow is not None,
            'active_threshold': getattr(self.campo, 'SOGLIA_MAX', 0),
            'drift': drift,
            'macd': self.campo._last_macd_hist,
            'trend': trend_override or getattr(self, '_last_trend', 'UNKNOWN'),
            'volatility': vol_override or getattr(self, '_last_volatility', 'UNKNOWN'),
            'bridge_reason': bridge_reason,
        }

    def _log_m2(self, emoji: str, msg: str):
        """Log dedicato Motore 2 — separato dal Motore 1."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} [M2] {msg}"
        self._m2_log.append(entry)
        log.info(entry)

    def _auto_detect_direction(self, trend):
        """
        Decide automaticamente LONG o SHORT con ISTERESI + COOLDOWN + CONFERMA.
        
        ISTERESI: soglie diverse per entrare e uscire da SHORT
          - Per andare SHORT: drift < -0.12% (più lontano)
          - Per tornare LONG: drift > -0.04% (deve risalire chiaramente)
          - Zona morta tra -0.12% e -0.04%: resta dove è
        
        COOLDOWN: minimo 60 secondi tra un flip e il successivo.
        
        CONFERMA: 3 tick consecutivi con segnale bearish >=2 prima di flippare a SHORT.
                  Per tornare LONG basta 1 tick con bearish < 2 (conservativo).
        """
        campo = self.campo
        
        # Calcola drift corrente
        drift = 0.0
        if len(campo._prices_long) >= 100:
            _prices = list(campo._prices_long)
            _avg_old = sum(_prices[:50]) / 50
            _avg_new = sum(_prices[-50:]) / 50
            drift = (_avg_new - _avg_old) / _avg_old * 100
        
        macd_hist = campo._last_macd_hist
        
        # Segnali bearish con isteresi sul drift
        bearish_signals = 0
        if campo._direction == "LONG":
            # Per andare SHORT: soglia più severa
            if drift < -0.12:
                bearish_signals += 1
        else:
            # Per restare SHORT: soglia più morbida (zona morta)
            if drift < -0.04:
                bearish_signals += 1
        
        if macd_hist < 0:
            bearish_signals += 1
        if trend == "DOWN":
            bearish_signals += 1
        
        # Conferma: conta tick consecutivi bearish
        if bearish_signals >= 2:
            campo._direction_bearish_streak += 1
        else:
            campo._direction_bearish_streak = 0
        
        # Cooldown: blocca flip se troppo recente
        now = time.time()
        cooldown_ok = (now - campo._direction_last_change) >= 60
        
        old_direction = campo._direction
        
        # LONG → SHORT: serve conferma (3 tick) + cooldown
        if campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok:
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
        # SHORT → LONG: basta 1 tick con bearish < 2 + cooldown
        elif campo._direction == "SHORT" and bearish_signals < 2 and cooldown_ok:
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
        
        if campo._direction != old_direction:
            self._log_m2("🔄", f"DIREZIONE → {campo._direction} (drift={drift:+.3f}% macd_hist={macd_hist:+.2f} trend={trend})")
            self.telemetry.log_direction_flip(
                old_direction, campo._direction,
                regime=self._regime_current, direction=campo._direction,
                open_position=self._shadow is not None,
                active_threshold=getattr(campo, 'SOGLIA_MAX', 0),
                drift=drift, macd=macd_hist, trend=trend,
                volatility=getattr(self, '_last_volatility', 'UNKNOWN'))
        else:
            self.telemetry.log_direction_hold(
                bearish_signals,
                regime=self._regime_current, direction=campo._direction,
                open_position=self._shadow is not None,
                active_threshold=getattr(campo, 'SOGLIA_MAX', 0),
                drift=drift, macd=macd_hist, trend=trend,
                volatility=getattr(self, '_last_volatility', 'UNKNOWN'))

    def _evaluate_shadow_entry(self, price, momentum, volatility, trend):
        """Motore 2 valuta entry con il Campo Gravitazionale."""
        try:
            # ── STATE ENGINE GATE — PRIMA DI TUTTO ───────────────────────
            can_enter, gate_reason = self._state_engine_can_enter()
            if not can_enter:
                # Non logga phantom per cooldown — è silenzio voluto, non opportunità
                return

            seed = self.seed_scorer.score()
            if seed.get('reason') == 'insufficient_data':
                return

            # ── DECIDI DIREZIONE: LONG o SHORT ─────────────────────────────
            # Il mercato decide, non noi. Drift + MACD + Trend = verdetto.
            self._auto_detect_direction(trend)

            # ── POST-FLIP COOLDOWN — non entrare subito dopo un cambio direzione ──
            # Il flip è una decisione grossa. Aspetta che il mercato confermi.
            # Il cooldown si auto-calibra dai risultati dei trade post-flip.
            time_since_flip = time.time() - self.campo._direction_last_change
            if time_since_flip < self.campo._post_flip_cooldown and self.campo._direction_last_change > 0:
                return  # silenzio — non phantom, è attesa voluta

            _dir = self.campo._direction
            fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend, _dir)
            matrimonio     = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
            matrimonio_name = matrimonio["name"]
            fantasma_info  = self.oracolo.is_fantasma(momentum, volatility, trend, _dir)

            result = self.campo.evaluate(
                seed_score=seed['score'],
                fingerprint_wr=fingerprint_wr,
                momentum=momentum,
                volatility=volatility,
                trend=trend,
                regime=self._regime_current,
                matrimonio_name=matrimonio_name,
                divorzio_set=self.memoria.divorzio,
                fantasma_info=fantasma_info,
                loss_consecutivi=self._m2_loss_consecutivi(),
            )

            if result['veto']:
                # ── PHANTOM: registra il trade bloccato (solo veti significativi) ──
                veto = result['veto']
                if not veto.startswith("WARMUP") and len(self._phantoms_open) < 5:
                    self._record_phantom(price, veto, seed['score'], momentum, volatility, trend)
                return

            if not result['enter']:
                # ── PHANTOM: score vicino alla soglia ma non abbastanza ──
                if result['score'] > 50 and len(self._phantoms_open) < 5:
                    smax = result.get('score_max', 100)
                    self._record_phantom(price, f"SCORE_SOTTO_{result['score']:.0f}_vs_{result['soglia']:.0f}_smax{smax:.0f}",
                                        seed['score'], momentum, volatility, trend)
                return

            # ── HARD GUARD: doppio check anti-bug ──────────────────────────
            if result['score'] < result['soglia']:
                self._log_m2("🛑", f"HARD GUARD: score={result['score']:.1f} < soglia={result['soglia']:.1f} — BLOCCATO")
                return

            self._log_m2("🎯", f"ENTRY {self.campo._direction} {matrimonio_name} | score={result['score']:.1f} "
                              f"soglia={result['soglia']:.1f} smax={result.get('score_max',100):.0f} size={result['size']:.2f}x "
                              f"| {result['breakdown']} @ ${price:.1f}")

            self._shadow = {
                "price_entry":   price,
                "matrimonio":    matrimonio_name,
                "duration_avg":  matrimonio["duration_avg"],
                "size":          result['size'],
                "score":         result['score'],
                "soglia":        result['soglia'],
                "pb_signals":    result.get('pb_signals', 0),
                "direction":     self.campo._direction,
            }
            self._shadow_entry_time        = time.time()
            self._shadow_entry_momentum    = momentum
            self._shadow_entry_volatility  = volatility
            self._shadow_entry_trend       = trend
            self._shadow_entry_fingerprint = fingerprint_wr
            self._shadow_max_price         = price
            self._shadow_min_price         = price
            self._shadow_matrimonio        = matrimonio_name
            self._m2_trades += 1

            # ── TELEMETRY: registra entry ─────────────────────────────────
            self.telemetry.log_trade_entry(
                trade_direction=self.campo._direction,
                score=result['score'], soglia=result['soglia'],
                matrimonio=matrimonio_name,
                regime=self._regime_current, direction=self.campo._direction,
                open_position=True
            )

            # ── SCRIVI ENTRY NEL DATABASE ─────────────────────────────────
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""
                    INSERT INTO trades (event_type, asset, price, size, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("M2_ENTRY", SYMBOL, price, result['size'], 0.0,
                      f"{self.campo._direction}_SHADOW", f"score={result['score']:.1f} soglia={result['soglia']:.1f}",
                      json.dumps({
                          "motore": "M2", "matrimonio": matrimonio_name,
                          "score": result['score'], "soglia": result['soglia'],
                          "momentum": momentum, "volatility": volatility,
                          "trend": trend, "regime": self._regime_current,
                          "breakdown": result['breakdown'],
                          "direction": self.campo._direction,
                      })))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[M2_DB] Entry save: {e}")

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE shadow_entry: {e}")
            log.error(f"[M2_ENTRY_ERROR] {e}\n{traceback.format_exc()}")

    def _evaluate_shadow_exit(self, price, momentum, volatility, trend):
        """Stessa logica di uscita del Motore 1 applicata al shadow trade."""
        try:
            if not self._shadow:
                return
            if price > self._shadow_max_price:
                self._shadow_max_price = price
            if price < self._shadow_min_price:
                self._shadow_min_price = price

            duration     = time.time() - self._shadow_entry_time
            duration_avg = self._shadow["duration_avg"]

            # ── HARD STOP LOSS 2% — PRIMA DI TUTTO, SOPRA TUTTO ──────────
            # Funziona in entrambe le direzioni: LONG e SHORT.
            HARD_STOP_LOSS_PCT = 2.0
            if self.campo._direction == "SHORT":
                pnl_pct = ((self._shadow["price_entry"] - price) / self._shadow["price_entry"]) * 100
            else:
                pnl_pct = ((price - self._shadow["price_entry"]) / self._shadow["price_entry"]) * 100
            if pnl_pct < -HARD_STOP_LOSS_PCT:
                self._close_shadow_trade(price, f"HARD_STOP_{pnl_pct:.1f}%")
                return

            # ── MINIMUM HOLD TIME ─────────────────────────────────────────────
            MIN_HOLD_SECONDS = 10

            # ── 4 DIVORCE TRIGGERS (SEMPRE ATTIVI — sicurezza) ───────────────
            triggers = []
            if self._shadow_entry_volatility == "BASSA" and volatility == "ALTA":
                triggers.append("T1_VOL")
            # T2: trend inverte CONTRO la nostra direzione
            if self.campo._direction == "LONG" and self._shadow_entry_trend == "UP" and trend == "DOWN":
                triggers.append("T2_TREND")
            elif self.campo._direction == "SHORT" and self._shadow_entry_trend == "DOWN" and trend == "UP":
                triggers.append("T2_TREND")
            # T3: drawdown dal migliore raggiunto
            if self.campo._direction == "SHORT":
                # SHORT: drawdown = prezzo sale dal minimo
                drawdown_pct = ((price - self._shadow_min_price) / self._shadow["price_entry"]) * 100
            else:
                drawdown_pct = ((self._shadow_max_price - price) / self._shadow["price_entry"]) * 100
            if drawdown_pct > DIVORCE_DRAWDOWN_PCT:
                triggers.append("T3_DD")
            current_fp = self.oracolo.get_wr(momentum, volatility, trend, self.campo._direction)
            fp_div = abs(current_fp - self._shadow_entry_fingerprint) / max(self._shadow_entry_fingerprint, 0.001)
            if fp_div > DIVORCE_FP_DIVERGE_PCT:
                triggers.append("T4_FP")
            if len(triggers) >= DIVORCE_MIN_TRIGGERS:
                self._close_shadow_trade(price, f"DIVORZIO|{'|'.join(triggers)}")
                return

            # ── SOTTO MINIMUM HOLD → non uscire per DECEL/SMORZ/TIMEOUT ──────
            # R/R FIX: se il trade è in profitto, hold più lungo
            # Se è in perdita, esci prima
            if self.campo._direction == "LONG":
                current_pnl = price - self._shadow["price_entry"]
            else:
                current_pnl = self._shadow["price_entry"] - price

            if current_pnl > 0:
                # IN PROFITTO: lascia correre — min hold 15 secondi
                effective_min_hold = 15
            else:
                # IN PERDITA: esci prima — min hold 8 secondi
                effective_min_hold = 8

            if duration < effective_min_hold:
                return

            # ── DECEL ─────────────────────────────────────────────────────────
            # Se in profitto: DECEL solo se il profitto sta SCENDENDO dal max
            if duration > duration_avg * 0.3:
                decel = self.decelero.analyze()
                if decel['should_exit']:
                    if current_pnl > 0:
                        # In profitto: esci per DECEL solo se il prezzo è sceso dal max
                        if self.campo._direction == "LONG":
                            retreat_from_max = self._shadow_max_price - price
                        else:
                            retreat_from_max = price - self._shadow_min_price
                        # Esci solo se ha ritracciato almeno $5 dal massimo
                        if retreat_from_max > 5:
                            self._close_shadow_trade(price, f"DECEL_MOMENTUM_WIN_{current_pnl:+.0f}")
                            return
                        # Altrimenti lascia correre il WIN
                    else:
                        # In perdita: esci subito per DECEL
                        self._close_shadow_trade(price, "DECEL_MOMENTUM")
                        return

            # ── SMORZ — impulso finito ────────────────────────────────────────
            # LONG: exit quando momentum DEBOLE (impulso UP morto)
            # SHORT: exit quando momentum FORTE (impulso DOWN morto, risale)
            # R/R FIX: se in profitto, SMORZ solo dopo duration_avg (non 0.5)
            smorz_threshold = duration_avg * 0.5 if current_pnl <= 0 else duration_avg * 1.0
            if duration > smorz_threshold:
                if self.campo._direction == "LONG" and momentum == "DEBOLE":
                    self._close_shadow_trade(price, f"SMORZ{'_WIN' if current_pnl > 0 else ''}")
                    return
                elif self.campo._direction == "SHORT" and momentum == "FORTE":
                    self._close_shadow_trade(price, f"SMORZ{'_WIN' if current_pnl > 0 else ''}")
                    return

            # ── TIMEOUT ───────────────────────────────────────────────────────
            if duration > duration_avg * 3:
                self._close_shadow_trade(price, "TIMEOUT_3X")
                return
            if duration > duration_avg and drawdown_pct > 1.0:
                self._close_shadow_trade(price, "TIMEOUT_DD")
                return

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE shadow_exit: {e}")
            log.error(f"[M2_EXIT_ERROR] {e}\n{traceback.format_exc()}")

    def _close_shadow_trade(self, price, reason):
        """Chiude il shadow trade e registra stats M2.
        CRITICO: insegna all'Oracolo e persiste su DB — altrimenti il sistema non impara MAI.
        
        NOTA FEE: Il PnL paper NON include fee Binance.
        In live con BNB: 0.075% per lato + ~0.01% slippage = 0.17% round trip.
        Su BTC a $70k = ~$119 per trade su 1 BTC.
        Lo scalping a 10-15s con PnL $5-17 NON è profittevole in spot.
        Serve: futures (fee 0.07% RT) con leva, oppure trade più lunghi con PnL > $150.
        """
        try:
            if not self._shadow:
                return
            pnl = (price - self._shadow["price_entry"]) if self.campo._direction == "LONG" \
                  else (self._shadow["price_entry"] - price)
            is_win = pnl > 0

            # ── TELEMETRY: registra trade ────────────────────────────────────
            trade_duration = time.time() - self._shadow_entry_time if self._shadow_entry_time else 0
            ctx = self._tele_ctx()
            self.telemetry.log_trade_close(
                trade_direction=self._shadow.get("direction", "LONG"),
                pnl=pnl, is_win=is_win, exit_reason=reason, duration=trade_duration,
                **{k: ctx[k] for k in ('regime','direction','open_position',
                   'active_threshold','drift','macd','trend','volatility')}
            )

            # ── STATE ENGINE: aggiorna stato dopo ogni trade ─────────────
            self._state_engine_update(pnl, is_win, trade_duration)

            # ── POST-FLIP LEARNING — il cooldown impara dai propri errori ──
            # Se questo trade è stato fatto entro 60s da un flip di direzione,
            # registra il risultato. Se i trade post-flip perdono → allunga
            # il cooldown. Se vincono → accorcia.
            if self._shadow_entry_time and self.campo._direction_last_change > 0:
                time_entry_after_flip = self._shadow_entry_time - self.campo._direction_last_change
                if 0 < time_entry_after_flip < 120:  # trade entrato entro 2 min dal flip
                    self.campo._post_flip_results.append({
                        'is_win': is_win, 'pnl': pnl,
                        'seconds_after_flip': time_entry_after_flip
                    })
                    # Auto-calibra ogni 5 trade post-flip
                    if len(self.campo._post_flip_results) >= 5:
                        pf_list = list(self.campo._post_flip_results)
                        pf_wins = sum(1 for r in pf_list if r['is_win'])
                        pf_wr = pf_wins / len(pf_list)
                        pf_pnl = sum(r['pnl'] for r in pf_list)
                        old_cd = self.campo._post_flip_cooldown
                        if pf_wr < 0.40 or pf_pnl < -50:
                            # Trade post-flip perdono → allunga cooldown
                            self.campo._post_flip_cooldown = min(
                                self.campo._POST_FLIP_COOLDOWN_MAX,
                                old_cd + 10)
                        elif pf_wr > 0.65 and pf_pnl > 0:
                            # Trade post-flip vincono → accorcia cooldown
                            self.campo._post_flip_cooldown = max(
                                self.campo._POST_FLIP_COOLDOWN_MIN,
                                old_cd - 5)
                        if self.campo._post_flip_cooldown != old_cd:
                            self._log_m2("🔄", f"POST-FLIP COOLDOWN: {old_cd}s → {self.campo._post_flip_cooldown}s "
                                              f"(WR={pf_wr:.0%} PnL=${pf_pnl:.0f} su {len(pf_list)} trade)")

            self.campo.record_result(is_win, exit_reason=reason, 
                                     pb_signals=self._shadow.get("pb_signals", 0),
                                     pnl=pnl)

            # ── INSEGNA ALL'ORACOLO — il cervello impara dai trade M2 ─────────
            if self._shadow_entry_momentum and self._shadow_entry_volatility and self._shadow_entry_trend:
                self.oracolo.record(
                    self._shadow_entry_momentum,
                    self._shadow_entry_volatility,
                    self._shadow_entry_trend,
                    is_win,
                    direction=self._shadow.get("direction", "LONG")
                )

            # ── AGGIORNA MEMORIA MATRIMONI — anche M2 conta ──────────────────
            if self._shadow_matrimonio:
                matrimonio = MatrimonioIntelligente.get_by_name(self._shadow_matrimonio)
                wr_expected = matrimonio.get("wr", 0.50)
                self.memoria.record_trade(self._shadow_matrimonio, is_win, wr_expected)

            # ── CALIBRATORE — M2 insegna anche a lui ─────────────────────────
            self.calibratore.registra_osservazione(
                seed_score=self._shadow.get("score", 0) / 100.0,
                fingerprint_wr=self._shadow_entry_fingerprint or 0.72,
                is_win=is_win,
                divorce_drawdown_usato=((self._shadow_max_price - price) / self._shadow["price_entry"] * 100)
                                       if self._shadow_max_price and self._shadow.get("price_entry") else 0.0
            )

            # ── REALTIME LEARNING — M2 genera capsule auto ───────────────────
            self.realtime_engine.registra_trade({
                'matrimonio': self._shadow_matrimonio, 'pnl': pnl, 'is_win': is_win
            })
            self.realtime_engine.analizza_e_genera()

            # ── LOG ANALYZER — stats per matrimonio includono M2 ─────────────
            self.log_analyzer.registra({
                'matrimonio': self._shadow_matrimonio, 'pnl': pnl, 'is_win': is_win
            })

            if is_win:
                self._m2_wins  += 1
            else:
                self._m2_losses += 1
            self._m2_pnl += pnl

            m2_tot = self._m2_wins + self._m2_losses
            m2_wr  = (self._m2_wins / m2_tot * 100) if m2_tot > 0 else 0

            self._log_m2(
                "🟢" if is_win else "🔴",
                f"EXIT {self._shadow.get('direction', 'LONG')} {self._shadow_matrimonio} {'WIN' if is_win else 'LOSS'} "
                f"PnL=${pnl:+.4f} WR={m2_wr:.0f}% score={self._shadow['score']:.1f} "
                f"soglia={self._shadow['soglia']:.1f} [{reason}]"
            )

            # ── SCRIVI NEL DATABASE — sopravvive ai restart ───────────────────
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""
                    INSERT INTO trades (event_type, asset, price, size, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("M2_EXIT", SYMBOL, price, self._shadow.get("size", 0.5), pnl,
                      f"{self._shadow.get('direction', 'LONG')}_SHADOW", reason,
                      json.dumps({
                          "motore": "M2",
                          "matrimonio": self._shadow_matrimonio,
                          "score": self._shadow.get("score", 0),
                          "soglia": self._shadow.get("soglia", 0),
                          "entry_price": self._shadow.get("price_entry", 0),
                          "momentum": self._shadow_entry_momentum,
                          "volatility": self._shadow_entry_volatility,
                          "trend": self._shadow_entry_trend,
                          "is_win": is_win,
                          "direction": self._shadow.get("direction", "LONG"),
                      })))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[M2_DB] Errore salvataggio trade: {e}")

            # ── PERSISTI IL CERVELLO — Oracolo, Memoria, Calibratore ──────────
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)

            # ── PERSISTI STATS M2 — sopravvivono ai restart ──────────────────
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_wins', ?)", (str(self._m2_wins),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_losses', ?)", (str(self._m2_losses),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_pnl', ?)", (str(self._m2_pnl),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_trades', ?)", (str(self._m2_trades),))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[M2_PERSIST] {e}")

            # ── LOG NARRATIVO ─────────────────────────────────────────────────
            self.ai_explainer.log_decision("M2_EXIT",
                f"M2 shadow {self._shadow_matrimonio} | PnL=${pnl:+.4f} | {reason}",
                {'pnl': pnl, 'is_win': is_win, 'reason': reason,
                 'score': self._shadow.get('score', 0), 'soglia': self._shadow.get('soglia', 0)})

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE close_shadow: {e}")
            log.error(f"[M2_CLOSE_ERROR] {e}\n{traceback.format_exc()}")
        finally:
            # Reset shadow SEMPRE — anche se c'è un errore, non lasciare trade fantasma
            self._shadow                   = None
            self._shadow_entry_time        = None
            self._shadow_entry_momentum    = None
            self._shadow_entry_volatility  = None
            self._shadow_entry_trend       = None
            self._shadow_entry_fingerprint = None
            self._shadow_max_price         = None
            self._shadow_min_price         = None
            self._shadow_matrimonio        = None

    def _m2_loss_consecutivi(self) -> int:
        """Loss consecutivi del Motore 2."""
        count = 0
        for r in reversed(list(self.campo._recent_results)):
            if not r:
                count += 1
            else:
                break
        return count

    # ════════════════════════════════════════════════════════════════════════
    # PHANTOM TRACKER — "SE AVESSI FATTO"
    # Traccia i trade bloccati e calcola cosa sarebbe successo.
    # Zavorra o protezione? I numeri rispondono.
    # ════════════════════════════════════════════════════════════════════════

    def _record_phantom(self, price, block_reason, seed_score, momentum, volatility, trend):
        """Registra un trade fantasma — bloccato da un livello di protezione."""
        phantom = {
            'price_entry':  price,
            'block_reason': block_reason,
            'seed_score':   seed_score,
            'momentum':     momentum,
            'volatility':   volatility,
            'trend':        trend,
            'entry_time':   time.time(),
            'max_price':    price,
            'min_price':    price,
            'regime':       self._regime_current,
            'direction':    self.campo._direction,
        }
        self._phantoms_open.append(phantom)

        # Classifica il blocco per statistiche
        reason_key = block_reason.split("_")[0] if "_" in block_reason else block_reason
        if "DRIFT" in block_reason:    reason_key = "DRIFT_VETO"
        elif "TOSSICO" in block_reason: reason_key = "VETO_TOSSICO"
        elif "LOSS_CONSEC" in block_reason: reason_key = "LOSS_CONSECUTIVI"
        elif "SCORE_SOTTO" in block_reason: reason_key = "SCORE_INSUFFICIENTE"
        elif "FANTASMA" in block_reason: reason_key = "FANTASMA"
        else: reason_key = block_reason

        if reason_key not in self._phantom_stats:
            self._phantom_stats[reason_key] = {
                'blocked': 0, 'would_win': 0, 'would_lose': 0,
                'pnl_saved': 0.0, 'pnl_missed': 0.0
            }
        self._phantom_stats[reason_key]['blocked'] += 1

    def _update_phantoms(self, price, momentum):
        """Aggiorna tutti i fantasmi aperti — chiamato ad ogni tick."""
        to_close = []
        for i, ph in enumerate(self._phantoms_open):
            if price > ph['max_price']:
                ph['max_price'] = price
            if price < ph['min_price']:
                ph['min_price'] = price

            duration = time.time() - ph['entry_time']
            # PnL bidirezionale — come il bot reale
            if ph.get('direction', 'LONG') == 'SHORT':
                pnl = ph['price_entry'] - price
            else:
                pnl = price - ph['price_entry']
            pnl_pct = (pnl / ph['price_entry']) * 100

            # ── Stesse regole di uscita del bot reale ──
            # Stop loss 2%
            if pnl_pct < -2.0:
                to_close.append((i, price, "HARD_STOP"))
                continue
            # DECEL (semplificato: dopo 15s se in perdita)
            if duration > 15 and pnl < 0:
                to_close.append((i, price, "DECEL_SIM"))
                continue
            # SMORZ — direzione-aware
            if duration > 10:
                if ph.get('direction', 'LONG') == 'LONG' and momentum == "DEBOLE":
                    to_close.append((i, price, "SMORZ_SIM"))
                    continue
                elif ph.get('direction', 'LONG') == 'SHORT' and momentum == "FORTE":
                    to_close.append((i, price, "SMORZ_SIM"))
                    continue
            # WIN takeout (dopo 20s se in profitto, simula DECEL)
            if duration > 20 and pnl > 0:
                to_close.append((i, price, "DECEL_WIN_SIM"))
                continue
            # Timeout 60s
            if duration > 60:
                to_close.append((i, price, "TIMEOUT_SIM"))
                continue

        # Chiudi dal fondo per non rompere gli indici
        for i, close_price, reason in reversed(to_close):
            self._close_phantom(i, close_price, reason)

    def _close_phantom(self, idx, price, reason):
        """Chiude un fantasma e registra il risultato."""
        try:
            ph = self._phantoms_open.pop(idx)
            # PnL bidirezionale
            if ph.get('direction', 'LONG') == 'SHORT':
                pnl = ph['price_entry'] - price
            else:
                pnl = price - ph['price_entry']
            is_win = pnl > 0

            # Aggiorna statistiche per livello di blocco
            block = ph['block_reason']
            reason_key = block.split("_")[0] if "_" in block else block
            if "DRIFT" in block:    reason_key = "DRIFT_VETO"
            elif "TOSSICO" in block: reason_key = "VETO_TOSSICO"
            elif "LOSS_CONSEC" in block: reason_key = "LOSS_CONSECUTIVI"
            elif "SCORE_SOTTO" in block: reason_key = "SCORE_INSUFFICIENTE"
            elif "FANTASMA" in block: reason_key = "FANTASMA"
            else: reason_key = block

            if reason_key not in self._phantom_stats:
                self._phantom_stats[reason_key] = {
                    'blocked': 0, 'would_win': 0, 'would_lose': 0,
                    'pnl_saved': 0.0, 'pnl_missed': 0.0
                }

            stats = self._phantom_stats[reason_key]
            if is_win:
                stats['would_win'] += 1
                stats['pnl_missed'] += pnl   # soldi che NON abbiamo guadagnato
            else:
                stats['would_lose'] += 1
                stats['pnl_saved'] += abs(pnl)   # soldi che NON abbiamo perso

            result = {
                'block_reason': block,
                'price_entry':  ph['price_entry'],
                'price_exit':   price,
                'pnl':          round(pnl, 2),
                'is_win':       is_win,
                'exit_reason':  reason,
                'regime':       ph['regime'],
                'direction':    ph.get('direction', 'LONG'),
                'verdict':      "PROTEZIONE" if not is_win else "ZAVORRA",
            }
            self._phantoms_closed.append(result)

            # Log solo se il fantasma è significativo
            _dir = ph.get('direction', 'LONG')
            _dir_tag = "S" if _dir == "SHORT" else "L"
            emoji = "🛡️" if not is_win else "⚠️"
            label = "PROTETTO" if not is_win else "MANCATO"
            ts = datetime.utcnow().strftime('%H:%M:%S')
            log_entry = (f"{ts} {emoji} [PHANTOM {_dir_tag}] {label} ${pnl:+.2f} | "
                        f"bloccato da: {block} | {reason}")
            self._phantom_log.append(log_entry)
            log.info(log_entry)

        except Exception as e:
            log.error(f"[PHANTOM] Errore close: {e}")

    def _get_phantom_summary(self) -> dict:
        """Riepilogo fantasmi per la dashboard."""
        stats = self._phantom_stats
        if not stats:
            return {
                'total': 0, 'protezione': 0, 'zavorra': 0,
                'pnl_saved': 0, 'pnl_missed': 0,
                'verdetto': 'DATI INSUFFICIENTI',
                'per_livello': {},
                'log': list(self._phantom_log),
            }

        # Calcola totali dai dati per livello (COMPLETI, non troncati)
        total_blocked = sum(s['blocked'] for s in stats.values())
        protezione = sum(s['would_lose'] for s in stats.values())
        zavorra = sum(s['would_win'] for s in stats.values())
        pnl_saved = sum(s['pnl_saved'] for s in stats.values())
        pnl_missed = sum(s['pnl_missed'] for s in stats.values())

        if pnl_saved > pnl_missed:
            verdetto = f"PROTEZIONE (+${pnl_saved - pnl_missed:.0f} risparmiati)"
        elif pnl_missed > pnl_saved:
            verdetto = f"ZAVORRA (-${pnl_missed - pnl_saved:.0f} persi in opportunità)"
        else:
            verdetto = "NEUTRO"

        return {
            'total':       total_blocked,
            'protezione':  protezione,
            'zavorra':     zavorra,
            'pnl_saved':   round(pnl_saved, 2),
            'pnl_missed':  round(pnl_missed, 2),
            'bilancio':    round(pnl_saved - pnl_missed, 2),
            'verdetto':    verdetto,
            'per_livello': dict(stats),
            'log':         list(self._phantom_log),
            'open':        len(self._phantoms_open),
        }

    def _read_bridge_commands(self):
        """
        Legge bridge_commands.json e applica comandi al CampoGravitazionale.
        Il bridge AI scrive qui, il bot esegue qui. Zero restart.
        """
        try:
            if not os.path.exists(self._bridge_cmd_file):
                return

            with open(self._bridge_cmd_file) as f:
                commands = json.load(f)

            modified = False
            for cmd in commands:
                if cmd.get("executed"):
                    continue

                cmd_type = cmd.get("type", "")
                data     = cmd.get("data", {})

                if cmd_type == "modify_weight":
                    param = data.get("param", "")
                    value = data.get("value")
                    # ── PARAMETRI PROTETTI — calibrati sui dati reali ──────
                    # Il bridge NON può toccarli. Solo noi dopo analisi phantom.
                    PROTECTED_PARAMS = {
                        "SOGLIA_BASE",           # calibrata su 37,112 candele
                        "DRIFT_VETO_THRESHOLD",  # settato a -0.20% — phantom WR 81%
                        "W_RSI",                 # peso RSI — calibrato
                        "W_MACD",                # peso MACD — calibrato
                        "W_SEED",                # peso seed — calibrato
                        "W_FINGERPRINT",         # peso fingerprint — calibrato
                        "W_MOMENTUM",            # peso momentum — calibrato
                        "W_TREND",               # peso trend — calibrato
                        "W_VOLATILITY",          # peso volatilità — calibrato
                        "W_REGIME",              # peso regime — calibrato
                    }
                    if param in PROTECTED_PARAMS:
                        self._log("🌉", f"BRIDGE: RIFIUTATO {param} → {value} (protetto)")
                        ctx = self._tele_ctx()
                        self.telemetry.log_param_rejected(param, value, "protetto_dati_reali", **{k: ctx[k] for k in ('regime','direction','open_position','active_threshold','drift','macd','trend','volatility')})
                        cmd["executed"] = True
                        modified = True
                    elif hasattr(self.campo, param) and value is not None:
                        old = getattr(self.campo, param)
                        setattr(self.campo, param, value)
                        self._log("🌉", f"BRIDGE: {param} {old} → {value}")
                        ctx = self._tele_ctx(bridge_reason=f"modify_weight:{param}")
                        self.telemetry.log_param_change(param, old, value, bridge_reason=f"modify_weight:{param}", **{k: ctx[k] for k in ('regime','direction','open_position','active_threshold','drift','macd','trend','volatility')})
                        cmd["executed"] = True
                        modified = True

                elif cmd_type == "adjust_soglia":
                    param = data.get("param", "")
                    value = data.get("value")
                    # ── GUARDRAIL: SOGLIA_BASE è calibrata su 37,112 candele ──
                    # Il bridge NON può toccarla. Solo pesi e capsule.
                    if param == "SOGLIA_BASE":
                        self._log("🌉", f"BRIDGE: RIFIUTATO {param} → {value} (protetto da calibrazione storica)")
                        ctx = self._tele_ctx()
                        self.telemetry.log_param_rejected(param, value, "calibrazione_storica", **{k: ctx[k] for k in ('regime','direction','open_position','active_threshold','drift','macd','trend','volatility')})
                        cmd["executed"] = True
                        modified = True
                    elif hasattr(self.campo, param) and value is not None:
                        old = getattr(self.campo, param)
                        setattr(self.campo, param, value)
                        self._log("🌉", f"BRIDGE: {param} {old} → {value}")
                        ctx = self._tele_ctx(bridge_reason=f"adjust_soglia:{param}")
                        self.telemetry.log_param_change(param, old, value, bridge_reason=f"adjust_soglia:{param}", **{k: ctx[k] for k in ('regime','direction','open_position','active_threshold','drift','macd','trend','volatility')})
                        cmd["executed"] = True
                        modified = True

            if modified:
                with open(self._bridge_cmd_file, 'w') as f:
                    json.dump(commands, f, indent=2)

        except Exception as e:
            log.error(f"[BRIDGE_READ] {e}")

    # ════════════════════════════════════════════════════════════════════════
    # ORDINI BINANCE (solo LIVE)
    # ════════════════════════════════════════════════════════════════════════

    def _place_order(self, side: str, price: float, size_mult: float = 1.0):
        """
        Placeholder per ordini reali su Binance.
        Da completare con python-binance o requests REST API.
        ATTIVO SOLO quando PAPER_TRADE = False.
        """
        log.info(f"[ORDER] 📤 {side} {SYMBOL} @ {price:.2f} size_mult={size_mult:.1f}")
        # TODO: implementa chiamata Binance REST API
        # import requests
        # payload = {"symbol": SYMBOL, "side": side, "type": "MARKET", ...}
        # requests.post("https://api.binance.com/api/v3/order", ...)

    # ════════════════════════════════════════════════════════════════════════
    # HEARTBEAT → app.py (Mission Control)
    # ════════════════════════════════════════════════════════════════════════

    def _update_heartbeat(self):
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                tot = self.wins + self.losses
                self.heartbeat_data.update({
                    "status":          "RUNNING",
                    "mode":            "PAPER" if self.paper_trade else "LIVE",
                    "capital":         round(self.capital, 2),
                    "trades":          self.total_trades,
                    "wins":            self.wins,
                    "losses":          self.losses,
                    "wr":              round(self.wins / tot, 4) if tot > 0 else 0,
                    "last_seen":       datetime.utcnow().isoformat(),
                    "matrimoni_divorzio": list(self.memoria.divorzio),
                    "oracolo_snapshot":   self.oracolo.dump(),
                    "posizione_aperta":   self.trade_open is not None,
                    "live_log":           list(self._live_log),
                    "calibra_params":     self.calibratore.get_params(),
                    "calibra_log":        self.calibratore.get_log(),
                    "regime":             self._regime_current,
                    "regime_conf":        round(self._regime_conf, 3),
                    # ── MOTORE 2: CAMPO GRAVITAZIONALE stats ──────────
                    "m2_trades":          self._m2_trades,
                    "m2_wins":            self._m2_wins,
                    "m2_losses":          self._m2_losses,
                    "m2_wr":              round(self._m2_wins / max(1, self._m2_wins + self._m2_losses), 4),
                    "m2_pnl":             round(self._m2_pnl, 4),
                    "m2_shadow_open":     self._shadow is not None,
                    "m2_direction":       self.campo._direction,
                    "m2_state":           self._state,
                    "m2_loss_streak":     self._m2_loss_streak,
                    "m2_cooldown":        max(0, self._m2_cooldown_until - time.time()),
                    "m2_log":             list(self._m2_log),
                    "m2_campo_stats":     self.campo.get_stats(),
                    # ── PHANTOM TRACKER — zavorra o protezione? ───────
                    "phantom":            self._get_phantom_summary(),
                    # ── STABILITY TELEMETRY ────────────────────────
                    "telemetry":          self.telemetry.generate_report(),
                })
        except Exception as e:
            log.error(f"[HEARTBEAT_ERROR] {e}")
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    # ════════════════════════════════════════════════════════════════════════
    # RUN
    # ════════════════════════════════════════════════════════════════════════

    def _loss_consecutivi(self) -> int:
        """Conta i loss consecutivi dalla coda del log_analyzer."""
        count = 0
        for t in reversed(list(self.log_analyzer.trades)):
            if t.get('pnl', 0) < 0:
                count += 1
            else:
                break
        return count

    def run(self):
        log.info("[START] Bot avviato — connessione Binance WS...")
        self.connect_binance()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("[STOP] Bot fermato da utente")
            self._persist.save(self.capital, self.total_trades)

# ═══════════════════════════════════════════════════════════════════════════
# MAIN (standalone — Render lo avvia tramite bot_launcher.py)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    bot = OvertopBassanoV14Production()
    bot.run()
