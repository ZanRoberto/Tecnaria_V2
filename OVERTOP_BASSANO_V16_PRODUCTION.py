#!/usr/bin/env python3
"""
OVERTOP BASSANO V16 PRODUCTION
═══════════════════════════════════════════════════════════════════════════════
Architettura pulita. Un solo motore decisionale. Memoria persistente su DB.

FILOSOFIA CORE:
  - Physics of market impulses: entra quando l'energia è alta, esci quando muore
  - Score vs Soglia: un solo gate di entry
  - CapsuleMemory: unico layer di blocco, persistente su DB
  - EVITA/MIGLIORA: l'unica classificazione che conta dopo una perdita
  - MemoriaMatrimoni: persistita su DB, mai solo RAM

CATENA DECISIONALE:
  tick → RegimeDetector → ContestoAnalyzer → SeedScorer →
  OracoloDinamico → CampoGravitazionale(score vs soglia) →
  CapsuleMemory.check() → ENTRY

  CLOSE → OracleClassifier(EVITA/MIGLIORA) → CapsuleMemory.update()

ELIMINATO RISPETTO A V15:
  - Capsule1-5 (Coerenza/Trappola/Protezione/Opportunita/Tattica)
  - CapsuleIntelligente (sistema immunitario predittivo)
  - IntelligenzaAutonoma / CapsuleRuntime / ConfigHotReloader
  - SuperCervello (sostituito da CampoGravitazionale puro)
  - MemoriaMatrimoni in RAM pura → ora persistita su DB
  - DIVORZIO_PERMANENTE che paralizza → diventa capsule EVITA con samples alti

INVARIATO:
  - CampoGravitazionale (score engine)
  - RegimeDetector
  - ContestoAnalyzer
  - SeedScorer
  - OracoloDinamico (fingerprint WR)
  - Exit engine (4 trigger + SMORZ)
  - PersistenzaStato
  - LEGGE FONDAMENTALE PnL USDC
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import websocket
import threading
import time
import sqlite3
import os
from datetime import datetime
from collections import deque, defaultdict
import logging
import sys

# ── IMPORTS OPZIONALI ────────────────────────────────────────────────────────
try:
    from comparto_engine import CompartoEngine, COMPARTI
    from nervosismo_engine import NervosismoEngine
    from breath_engine import BreathEngine
    _V16_ENGINES_OK = True
except ImportError:
    _V16_ENGINES_OK = False

try:
    from capsule_manager import CapsuleManager
    _CM_AVAILABLE = True
except ImportError:
    _CM_AVAILABLE = False

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── COSTANTI ─────────────────────────────────────────────────────────────────
SYMBOL            = os.environ.get("SYMBOL", "BTCUSDC")
PAPER_TRADE       = os.environ.get("PAPER_TRADE", "true").lower() != "false"
DB_PATH           = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
BINANCE_WS_URL    = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@aggTrade"

TRADE_SIZE_USD    = 1000.0
LEVERAGE          = 5
EXPOSURE          = TRADE_SIZE_USD * LEVERAGE   # $5000
FEE_PCT           = 0.0002
FEE_TRADE         = EXPOSURE * FEE_PCT * 2      # $2.00 fissi
STOP_LIVE_USD     = 7.0                         # lordo live
PROFIT_LOCK_PCT   = 0.40                        # retreat massimo dal picco

SEED_ENTRY_THRESHOLD = 0.45
DIVORCE_DRAWDOWN_PCT = 3.0
DIVORCE_FP_DIVERGE   = 0.40
DIVORCE_MIN_TRIGGERS = 2


# ═══════════════════════════════════════════════════════════════════════════
# ★ CAPSULE MEMORY — unico layer di blocco, persistente su DB
#   Sostituisce: Capsule1-5, CapsuleIntelligente, IntelligenzaAutonoma,
#               MemoriaMatrimoni RAM, CapsuleRuntime, CapsuleManager
#
#   Regola: INSERT se forma nuova, UPDATE se forma vista, REJECT se incompleta
#   ID canonico: SC_{EVITA|MIGLIORA}_{ASSET}_{DIR}_{REG}_{MOM}_{VOL}_{TRD}
# ═══════════════════════════════════════════════════════════════════════════

class CapsuleMemory:
    """
    Memoria operativa persistente delle forme di mercato.
    Ogni forma = un record nel DB con samples, wr, pnl_avg.
    Nessuna cancellazione automatica. Nessun reset.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS capsule_memory (
            id          TEXT PRIMARY KEY,
            asset       TEXT NOT NULL,
            direction   TEXT NOT NULL,
            regime      TEXT NOT NULL,
            momentum    TEXT NOT NULL,
            volatility  TEXT NOT NULL,
            trend       TEXT NOT NULL,
            matrimonio  TEXT DEFAULT '',
            action      TEXT NOT NULL,
            samples     INTEGER DEFAULT 1,
            wins        INTEGER DEFAULT 0,
            wr          REAL DEFAULT 0.0,
            pnl_avg     REAL DEFAULT 0.0,
            enabled     INTEGER DEFAULT 1,
            created_ts  REAL,
            updated_ts  REAL,
            note        TEXT DEFAULT ''
        )
    """

    MIN_SAMPLES_TO_BLOCK = 3    # minimo campioni per bloccare
    MIN_WR_TO_BLOCK      = 0.15 # WR sotto questa soglia = blocca

    def __init__(self, db_path: str = DB_PATH, asset: str = SYMBOL):
        self.db_path = db_path
        self.asset   = asset
        self._cache  = []
        self._last_reload = 0.0
        self._init_db()
        self._reload_cache()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute(self.SCHEMA)
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[CM16] init DB: {e}")

    def _canonical_id(self, asset, direction, regime, momentum, volatility, trend,
                       action, matrimonio=""):
        parts = ["SC"]
        parts.append("EVITA" if action == "blocca_entry" else "MIGLIORA")
        parts.append(asset.replace("USDC","").replace("USDT",""))
        if direction in ("LONG","SHORT"):
            parts.append(direction)
        if regime in ("RANGING","EXPLOSIVE","TRENDING_BULL","TRENDING_BEAR"):
            parts.append(regime)
        if momentum in ("FORTE","MEDIO","DEBOLE"):
            parts.append(momentum)
        if volatility in ("ALTA","MEDIA","BASSA"):
            parts.append(volatility)
        if trend in ("UP","SIDEWAYS","DOWN"):
            parts.append(trend)
        if matrimonio:
            parts.append(matrimonio[:20].upper().replace(" ","_"))
        return "_".join(parts)

    def _reload_cache(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            rows = conn.execute(
                "SELECT id,direction,regime,momentum,volatility,trend,matrimonio,"
                "action,samples,wr,pnl_avg,enabled FROM capsule_memory "
                "WHERE asset=? AND enabled=1 AND samples>=?",
                (self.asset, self.MIN_SAMPLES_TO_BLOCK)
            ).fetchall()
            conn.close()
            self._cache = rows
            self._last_reload = time.time()
        except Exception as e:
            log.error(f"[CM16] reload: {e}")

    def check(self, direction: str, regime: str, momentum: str,
              volatility: str, trend: str, matrimonio: str = "") -> dict:
        """
        Verifica se il contesto è bloccato da una capsule EVITA.
        Ritorna {'blocca': bool, 'reason': str, 'action': str}
        """
        if time.time() - self._last_reload > 30:
            self._reload_cache()

        for row in self._cache:
            rid, rdir, rreg, rmom, rvol, rtrd, rmat, ract, rsamples, rwr, rpnl, renabled = row
            if ract != "blocca_entry":
                continue
            if rwr > self.MIN_WR_TO_BLOCK:
                continue
            # Match contesto
            match = True
            if rdir and rdir != direction:
                match = False
            if rreg and rreg != regime:
                match = False
            if rmom and rmom != momentum:
                match = False
            if rvol and rvol != volatility:
                match = False
            if rtrd and rtrd != trend:
                match = False
            if rmat and rmat not in (matrimonio, ""):
                match = False
            if match:
                return {
                    'blocca': True,
                    'reason': rid,
                    'action': ract,
                    'wr': rwr,
                    'samples': rsamples,
                }
        return {'blocca': False, 'reason': '', 'action': ''}

    def get_soglia_boost(self, direction: str, regime: str, momentum: str,
                         volatility: str, trend: str) -> float:
        """Ritorna boost soglia da capsule MIGLIORA attive."""
        if time.time() - self._last_reload > 30:
            self._reload_cache()
        boost = 0.0
        for row in self._cache:
            rid, rdir, rreg, rmom, rvol, rtrd, rmat, ract, rsamples, rwr, rpnl, renabled = row
            if ract != "boost_soglia":
                continue
            if rdir and rdir != direction: continue
            if rreg and rreg != regime: continue
            if rmom and rmom != momentum: continue
            if rvol and rvol != volatility: continue
            if rtrd and rtrd != trend: continue
            boost += abs(rpnl) * 0.1  # boost proporzionale alla perdita media
        return min(boost, 20.0)

    def record(self, direction: str, regime: str, momentum: str,
               volatility: str, trend: str, matrimonio: str,
               action: str, is_win: bool, pnl: float, note: str = ""):
        """
        INSERT se forma nuova. UPDATE se forma già vista.
        action = 'blocca_entry' (EVITA) o 'boost_soglia' (MIGLIORA)
        """
        if not all([direction, regime, momentum, volatility, trend]):
            log.warning(f"[CM16] REJECT — forma incompleta: dir={direction} reg={regime} "
                       f"mom={momentum} vol={volatility} trd={trend}")
            return False

        cap_id = self._canonical_id(
            self.asset, direction, regime, momentum, volatility, trend,
            action, matrimonio
        )

        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            existing = conn.execute(
                "SELECT id, samples, wins, pnl_avg FROM capsule_memory WHERE id=?",
                (cap_id,)
            ).fetchone()

            now = time.time()

            if existing:
                old_id, old_s, old_w, old_pnl = existing
                new_s   = old_s + 1
                new_w   = old_w + (1 if is_win else 0)
                new_wr  = new_w / new_s
                new_pnl = ((old_pnl * old_s) + pnl) / new_s
                new_note = (f"{note} | " if note else "") + f"n={new_s} wr={new_wr:.0%}"
                conn.execute("""
                    UPDATE capsule_memory
                    SET samples=?, wins=?, wr=?, pnl_avg=?, updated_ts=?, note=?, enabled=1
                    WHERE id=?
                """, (new_s, new_w, new_wr, new_pnl, now, new_note[:500], cap_id))
                conn.commit()
                conn.close()
                log.info(f"[CM16] UPDATE id={cap_id} samples={new_s} wr={new_wr:.0%} pnl={new_pnl:.2f}")
            else:
                wr  = 1.0 if is_win else 0.0
                conn.execute("""
                    INSERT INTO capsule_memory
                    (id,asset,direction,regime,momentum,volatility,trend,matrimonio,
                     action,samples,wins,wr,pnl_avg,enabled,created_ts,updated_ts,note)
                    VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,1,?,?,?)
                """, (cap_id, self.asset, direction, regime, momentum, volatility,
                      trend, matrimonio, action, 1 if is_win else 0, wr, pnl,
                      now, now, note[:500]))
                conn.commit()
                conn.close()
                log.info(f"[CM16] INSERT id={cap_id} samples=1 wr={wr:.0%} pnl={pnl:.2f}")

            self._reload_cache()
            return True

        except Exception as e:
            log.error(f"[CM16] record error: {e}")
            return False

    def get_dashboard_data(self) -> list:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            rows = conn.execute("""
                SELECT id, action, samples, wr, pnl_avg, enabled, updated_ts
                FROM capsule_memory WHERE asset=?
                ORDER BY updated_ts DESC LIMIT 50
            """, (self.asset,)).fetchall()
            conn.close()
            return [{'id':r[0],'action':r[1],'samples':r[2],'wr':r[3],
                     'pnl_avg':r[4],'enabled':r[5]} for r in rows]
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════════════
# ★ ORACLE CLASSIFIER — classifica ogni perdita in EVITA o MIGLIORA
#   Poi aggiorna CapsuleMemory
# ═══════════════════════════════════════════════════════════════════════════

class OracleClassifier:
    """
    Classifica ogni trade chiuso in EVITA o MIGLIORA.
    
    EVITA: il contesto non aveva edge.
      - nessun WIN_+N nel motivo uscita
      - pnl_lordo < FEE_TRADE ($2)
      - non si poteva fare di meglio
      Azione: crea/aggiorna capsule blocca_entry per quella forma

    MIGLIORA: il mercato si era mosso a favore ma gestione sbagliata.
      - WIN_+N presente nel motivo uscita
      - pnl_lordo > FEE_TRADE ma pnl_netto negativo
      - si poteva fare di meglio
      Azione: crea/aggiorna capsule boost_soglia (non blocca)
    """

    def classify(self, trade: dict) -> dict:
        """
        trade contiene: pnl, pnl_lordo, reason, direction, regime,
                        momentum, volatility, trend, matrimonio
        Ritorna: {'tipo': 'EVITA'|'MIGLIORA', 'azione': str, 'motivo': str}
        """
        pnl_netto  = float(trade.get('pnl', 0.0))
        pnl_lordo  = float(trade.get('pnl_lordo', pnl_netto + FEE_TRADE))
        reason     = str(trade.get('reason', ''))

        # Trade vincente — non classificare
        if pnl_netto > 0:
            return {'tipo': 'WIN', 'azione': '', 'motivo': 'trade vincente'}

        # Cerca WIN_+N nel motivo
        import re
        win_ticks = 0
        m = re.search(r'WIN_\+(\d+)', reason)
        if m:
            win_ticks = int(m.group(1))

        # Classificazione
        if win_ticks >= 5 or (win_ticks > 0 and pnl_lordo > FEE_TRADE * 2):
            # C'era movimento vero ma gestione sbagliata
            return {
                'tipo': 'MIGLIORA',
                'azione': 'boost_soglia',
                'motivo': f"WIN_+{win_ticks} presente ma pnl={pnl_netto:.2f} — gestione da correggere"
            }
        else:
            # Nessun edge reale
            return {
                'tipo': 'EVITA',
                'azione': 'blocca_entry',
                'motivo': f"nessun edge — pnl_lordo={pnl_lordo:.2f} WIN_ticks={win_ticks}"
            }


# ═══════════════════════════════════════════════════════════════════
# CLASSI CORE — invariate da V15
# ═══════════════════════════════════════════════════════════════════

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

    # -- EVENTI CON SNAPSHOT -----------------------------------------------

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

    # -- EVENTI SENZA SNAPSHOT (decisioni leggere) -------------------------

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

    # B5: eventi telemetrici coesi
    def log_capsule_load(self, capsule_ids: list):
        e = self._base("CAPSULE_LOAD", "", "", False)
        e['capsule_ids'] = capsule_ids
        e['count'] = len(capsule_ids)
        self._events.append(e)

    def log_bridge_trigger(self, trigger_type: str, event_name: str = ""):
        e = self._base("BRIDGE_TRIGGER_" + trigger_type.upper(), "", "", False)
        e['event_name'] = event_name
        self._events.append(e)

    def log_heartbeat_enriched(self):
        e = self._base("HEARTBEAT_ENRICHED", "", "", False)
        self._events.append(e)

    def log_event_signal(self, signal_type: str, payload: dict):
        e = self._base("EVENT_SIGNAL_" + signal_type.upper(), "", "", False)
        e.update(payload)
        self._events.append(e)

    # -- REPORT ------------------------------------------------------------

    def generate_report(self) -> dict:
        """Genera il report completo. Solo numeri, zero interpretazione."""
        uptime_hours = max((time.time() - self._start_time) / 3600, 0.001)
        events = self._events

        # -- A. Bridge / parametri --
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

        # -- B. Direzione --
        flips = [e for e in events if e['event_type'] == 'DIRECTION_FLIP']
        holds = [e for e in events if e['event_type'] == 'DIRECTION_HOLD']
        flips_l2s = sum(1 for f in flips if f['old_direction'] == 'LONG' and f['new_direction'] == 'SHORT')
        flips_s2l = sum(1 for f in flips if f['old_direction'] == 'SHORT' and f['new_direction'] == 'LONG')

        # -- C. Stabilita --
        decisions_taken = [e for e in events if e['event_type'] in
                          ('DIRECTION_FLIP', 'PARAM_CHANGE', 'TRADE_CLOSE', 'TRADE_ENTRY')]
        decisions_not_taken = [e for e in events if e['event_type'] in
                              ('DIRECTION_HOLD', 'PARAM_REJECTED')]
        decision_cost = len(param_events) + len(flips) * 3

        # -- D. Performance per direzione --
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

        # -- E. Per regime --
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
        """Salva telemetria su SQLite - eventi singoli + report."""
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


# ===========================================================================
# CAPSULE RUNTIME
# ===========================================================================

# ===========================================================================
# CAPSULE MANAGER — Sistema Unificato
# Sostituisce CapsuleRuntime + ConfigHotReloader + IntelligenzaAutonoma
# + VETI_LONG/SHORT hardcodati. Asset-aware. SQLite. Dashboard-ready.
# ===========================================================================

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

# ===========================================================================
# AI EXPLAINER
# ===========================================================================


class SeedScorer:
    """
    Scoring dell'impulso a 4 componenti:
      1. Range Position      40% - dove si trova il prezzo nel range recente
      2. Volume Acceleration 25% - accelerazione del volume sugli ultimi tick
      3. Directional Consist 20% - coerenza direzionale delle ultime variazioni
      4. Breakout Score      15% - rottura del range precedente
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
        Score sequenziale a 7 feature — rileva transizione RANGING→TRENDING.
        Non misura uno snapshot — misura la TRAIETTORIA degli ultimi tick.
        Simulazione: WR 77.8% su mercato con rumori e fakeout reali.
        """
        if len(self.prices) < 20:
            return {'score': 0.0, 'pass': False, 'reason': 'insufficient_data'}

        prices  = list(self.prices)
        volumes = list(self.volumes)

        # ── FEATURE 1: Range Position ──────────────────────────────────
        # Prezzo verso bordo superiore del range (serve >= 0.80)
        low20  = min(prices[-20:])
        high20 = max(prices[-20:])
        r20    = high20 - low20
        range_pos = (prices[-1] - low20) / (r20 + 0.01)

        # ── FEATURE 2: Compression Ratio ───────────────────────────────
        # Range si stringe: r5/r10 < 0.80 = molla che si carica
        r5  = max(prices[-5:])  - min(prices[-5:])
        r10 = max(prices[-10:]) - min(prices[-10:])
        compression_ratio = r5 / (r10 + 0.01)
        # Score: più compresso = meglio (inverso)
        comp_score = max(0.0, min(1.0, 1.0 - compression_ratio))

        # ── FEATURE 3: Drift Persistence ───────────────────────────────
        # % tick con variazione positiva negli ultimi 10 (serve >= 0.55)
        changes = [prices[i+1]-prices[i] for i in range(len(prices)-11, len(prices)-1)]
        positive_ticks = sum(1 for c in changes if c > 0)
        drift_persist  = positive_ticks / len(changes) if changes else 0.5

        # ── FEATURE 4: Sign Flips ──────────────────────────────────────
        # Pochi cambi di direzione = drift coerente (serve <= 5 su 20)
        all_changes = [prices[i+1]-prices[i] for i in range(len(prices)-21, len(prices)-1)]
        sign_flips  = sum(1 for i in range(1,len(all_changes))
                         if all_changes[i]*all_changes[i-1] < 0)
        flip_score  = max(0.0, min(1.0, 1.0 - sign_flips/10.0))

        # ── FEATURE 5: Volume Pressure ─────────────────────────────────
        # Volume ultimi 5 tick vs media 15 tick (serve >= 1.1)
        vm5  = sum(volumes[-5:])  / 5
        vm15 = sum(volumes[-15:]) / 15 if len(volumes) >= 15 else vm5
        vol_pressure = vm5 / (vm15 + 0.01)
        vol_score    = min(1.0, vol_pressure / 2.0)

        # ── FEATURE 6: Drift Slope ─────────────────────────────────────
        # Drift sta accelerando: media_5 > media_15 (serve > 0)
        drift5  = [prices[i+1]-prices[i] for i in range(len(prices)-6, len(prices)-1)]
        drift15 = [prices[i+1]-prices[i] for i in range(len(prices)-16, len(prices)-1)]
        dm5  = sum(drift5)  / len(drift5)  if drift5  else 0
        dm15 = sum(drift15) / len(drift15) if drift15 else 0
        drift_slope = dm5 - dm15
        slope_score = min(1.0, max(0.0, 0.5 + drift_slope / 0.001))

        # ── FEATURE 7: Compression Duration ───────────────────────────
        # Quanti tick il range è rimasto stretto consecutivamente
        comp_dur = 0
        for i in range(len(prices)-1, max(0,len(prices)-20), -1):
            window = prices[max(0,i-5):i+1]
            if (max(window)-min(window)) < r20*0.65:
                comp_dur += 1
            else:
                break
        dur_score = min(1.0, comp_dur / 8.0)

        # ── SCORE TOTALE ───────────────────────────────────────────────
        # Pesi calibrati su simulazione cavalca_curva (WR 77.8%)
        total = (range_pos   * 0.25 +   # prezzo al bordo
                 comp_score  * 0.15 +   # compressione
                 drift_persist* 0.20 +  # persistenza direzionale
                 flip_score  * 0.15 +   # coerenza (pochi flip)
                 vol_score   * 0.10 +   # volume in accumulo
                 slope_score * 0.10 +   # drift accelera
                 dur_score   * 0.05)    # durata compressione

        return {
            'score':            round(total, 4),
            'range_pos':        round(range_pos, 4),
            'compression':      round(compression_ratio, 4),
            'drift_persist':    round(drift_persist, 4),
            'sign_flips':       sign_flips,
            'vol_pressure':     round(vol_pressure, 4),
            'drift_slope':      round(drift_slope, 6),
            'comp_duration':    comp_dur,
            'pass':             total >= SEED_ENTRY_THRESHOLD,
        }

# ===========================================================================
# ★ ORACOLO DINAMICO - TUA INVENZIONE
#   Fingerprint-based win-rate memory con decay.
#   Blocca pattern FANTASMA (contesti che storicamente perdono).
# ===========================================================================


class OracoloDinamico:
    """
    ORACOLO 2.0 - Il cervello della volpe.
    
    Non è un contatore. È un sistema che:
    1. Salva TUTTO il contesto di ogni trade (regime, RSI, drift, range_position, ora, durata)
    2. Trova i trade passati PIÙ SIMILI alla situazione attuale (context-matching)
    3. Genera capsule automatiche dai pattern che emergono
    4. Traccia cosa succede DOPO l'uscita (post-trade)
    5. Adatta il MIN_HOLD per ogni fingerprint (duration memory)
    
    Macroregole:
    - "Più contesto salvi, meglio decidi"
    - "Non chiedere se il pattern vince. Chiedi se QUESTA SITUAZIONE somiglia ai miei WIN"
    - "Ogni trade che esce genera una lezione"
    """

    FANTASMA_WR_THRESHOLD = 0.45
    DECAY_FACTOR          = 0.95
    MIN_SAMPLES           = 5
    MIN_PNL_EDGE          = 2.50    # profitto lordo minimo — lordo $2.50 = netto $0.50 dopo fee $2
    MIN_REAL_SAMPLES      = 5

    def __init__(self):
        self._memory: dict = {}
        # Trade completi per context-matching (ultimi 200)
        self._trade_history = deque(maxlen=200)
        # Capsule generate automaticamente
        self._auto_capsules = []
        # Post-trade tracking
        self._post_trade_queue = deque(maxlen=20)
        
        # -- INTELLIGENZA REALE - dati da trade veri 23 marzo 2026 ------
        self._memory = {
            "LONG|FORTE|ALTA|SIDEWAYS":   {'wins': 13.0, 'samples': 24.0, 'pnl_sum': 1.0, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|MEDIO|ALTA|SIDEWAYS":   {'wins': 8.6,  'samples': 20.0, 'pnl_sum': -1.0, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|DEBOLE|ALTA|SIDEWAYS":  {'wins': 1.4,  'samples': 7.4,  'pnl_sum': -1.33, 'real_samples': 0,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|FORTE|MEDIA|SIDEWAYS":  {'wins': 4.5,  'samples': 6.0,  'pnl_sum': 0.53, 'real_samples': 0,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|MEDIO|MEDIA|SIDEWAYS":  {'wins': 1.0,  'samples': 2.0,  'pnl_sum': -0.07, 'real_samples': 0,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|DEBOLE|MEDIA|SIDEWAYS": {'wins': 0.5,  'samples': 3.7,  'pnl_sum': -0.53, 'real_samples': 0,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "LONG|FORTE|BASSA|UP":        {'wins': 23.4, 'samples': 30.0, 'pnl_sum': 30.8, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=20), 'drift_loss': deque(maxlen=20)},
            "LONG|FORTE|MEDIA|UP":        {'wins': 15.3, 'samples': 22.5, 'pnl_sum': 12.0, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=20), 'drift_loss': deque(maxlen=20)},
            "LONG|MEDIO|BASSA|UP":        {'wins': 12.2, 'samples': 18.8, 'pnl_sum': 7.87, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=20), 'drift_loss': deque(maxlen=20)},
            "LONG|FORTE|MEDIA|DOWN":      {'wins': 2.5,  'samples': 5.0,  'pnl_sum': 0.17,  'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=20), 'drift_loss': deque(maxlen=20)},
            "SHORT|FORTE|ALTA|DOWN":      {'wins': 5.5,  'samples': 10.0, 'pnl_sum': 3.6, 'real_samples': 5,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=20), 'drift_loss': deque(maxlen=20)},
            "LONG|DEBOLE|BASSA|SIDEWAYS": {'wins': 1.9,  'samples': 2.9,  'pnl_sum': 0.13, 'real_samples': 0,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
            "SHORT|MEDIO|ALTA|SIDEWAYS":  {'wins': 0.3,  'samples': 4.0,  'pnl_sum': -1.12, 'real_samples': 2,
                                           'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                                           'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                                           'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                                           'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)},
        }
        
        # -- INIETTA DATI REALI - 6 trade del 23 marzo 2026 --------------
        # Duration data per FORTE|ALTA
        f = self._memory["LONG|FORTE|ALTA|SIDEWAYS"]
        f['durations_win'].append(54)    # WIN: 54s (entry 16:03:47 → exit 16:04:41)
        f['durations_loss'].append(46)   # LOSS: 46s (entry 16:05:36 → exit 16:06:22)
        f['durations_loss'].append(47)   # LOSS: 47s (entry 16:11:05 → exit 16:11:52)
        f['durations_loss'].append(45)   # LOSS: 45s (entry 16:14:51 → exit 16:15:36)
        f['rsi_win'].append(32)          # WIN era su RSI basso (ipervenduto)
        f['rsi_loss'].extend([55, 48, 62])
        f['drift_win'].append(0.09)      # WIN aveva drift positivo
        f['drift_loss'].extend([-0.05, 0.02, 0.01])
        f['range_pos_win'].append(0.18)  # WIN era al bordo basso del range
        f['range_pos_loss'].extend([0.55, 0.42, 0.65])
        
        # SHORT|MEDIO durations
        s = self._memory["SHORT|MEDIO|ALTA|SIDEWAYS"]
        s['durations_loss'].append(21)   # LOSS SHORT 1
        s['durations_loss'].append(20)   # LOSS SHORT 2
        s['rsi_loss'].extend([45, 52])
        s['drift_loss'].extend([0.08, 0.10])
        s['range_pos_loss'].extend([0.45, 0.50])

        # -- TRADE HISTORY per context-matching (6 trade reali) -----------
        self._trade_history = deque([
            # TRADE 1: WIN - bordo basso, RSI basso, drift positivo
            {'fp': 'LONG|FORTE|ALTA|SIDEWAYS', 'momentum': 'FORTE', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'LONG', 'regime': 'RANGING',
             'rsi': 32, 'drift': 0.09, 'range_position': 0.18,
             'pnl': 1.47, 'duration': 54, 'is_win': True, 'hour': 16, 'ts': 1774282000},
            # TRADE 2: LOSS - centro range, drift negativo
            {'fp': 'LONG|FORTE|ALTA|SIDEWAYS', 'momentum': 'FORTE', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'LONG', 'regime': 'RANGING',
             'rsi': 55, 'drift': -0.05, 'range_position': 0.55,
             'pnl': -12.17, 'duration': 46, 'is_win': False, 'hour': 16, 'ts': 1774282200},
            # TRADE 3: LOSS - centro range, drift quasi zero
            {'fp': 'LONG|FORTE|ALTA|SIDEWAYS', 'momentum': 'FORTE', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'LONG', 'regime': 'RANGING',
             'rsi': 48, 'drift': 0.02, 'range_position': 0.42,
             'pnl': -5.96, 'duration': 47, 'is_win': False, 'hour': 16, 'ts': 1774282400},
            # TRADE 4: LOSS - sopra centro, drift basso
            {'fp': 'LONG|FORTE|ALTA|SIDEWAYS', 'momentum': 'FORTE', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'LONG', 'regime': 'RANGING',
             'rsi': 62, 'drift': 0.01, 'range_position': 0.65,
             'pnl': -1.81, 'duration': 45, 'is_win': False, 'hour': 16, 'ts': 1774282600},
            # TRADE 5: LOSS SHORT - in EXPLOSIVE
            {'fp': 'SHORT|MEDIO|ALTA|SIDEWAYS', 'momentum': 'MEDIO', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'SHORT', 'regime': 'EXPLOSIVE',
             'rsi': 45, 'drift': 0.08, 'range_position': 0.45,
             'pnl': -6.13, 'duration': 21, 'is_win': False, 'hour': 16, 'ts': 1774283000},
            # TRADE 6: LOSS SHORT - in EXPLOSIVE
            {'fp': 'SHORT|MEDIO|ALTA|SIDEWAYS', 'momentum': 'MEDIO', 'volatility': 'ALTA',
             'trend': 'SIDEWAYS', 'direction': 'SHORT', 'regime': 'EXPLOSIVE',
             'rsi': 52, 'drift': 0.10, 'range_position': 0.50,
             'pnl': -5.70, 'duration': 20, 'is_win': False, 'hour': 16, 'ts': 1774283200},
        ], maxlen=200)

    def _fp(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> str:
        return f"{direction}|{momentum}|{volatility}|{trend}"

    def _new_memory_entry(self):
        return {'wins': 0.0, 'samples': 0.0, 'pnl_sum': 0.0, 'real_samples': 0,
                'durations_win': deque(maxlen=50), 'durations_loss': deque(maxlen=50),
                'rsi_win': deque(maxlen=50), 'rsi_loss': deque(maxlen=50),
                'drift_win': deque(maxlen=50), 'drift_loss': deque(maxlen=50),
                'range_pos_win': deque(maxlen=50), 'range_pos_loss': deque(maxlen=50)}

    # -- LETTURA ----------------------------------------------------------

    def get_wr(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> float:
        fp = self._fp(momentum, volatility, trend, direction)
        if fp not in self._memory or self._memory[fp]['samples'] < self.MIN_SAMPLES:
            return 0.72
        m = self._memory[fp]
        return m['wins'] / m['samples'] if m['samples'] > 0 else 0.72

    def get_pnl_avg(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> float:
        fp = self._fp(momentum, volatility, trend, direction)
        if fp not in self._memory or self._memory[fp]['samples'] < self.MIN_SAMPLES:
            return 0.0
        m = self._memory[fp]
        return m.get('pnl_sum', 0) / m['samples'] if m['samples'] > 0 else 0.0

    def get_avg_duration(self, momentum: str, volatility: str, trend: str, 
                         direction: str = "LONG", is_win: bool = True) -> float:
        """Durata media dei WIN o LOSS per questo fingerprint. None se dati insufficienti."""
        fp = self._fp(momentum, volatility, trend, direction)
        mem = self._memory.get(fp)
        if not mem:
            return None
        key = 'durations_win' if is_win else 'durations_loss'
        durations = mem.get(key)
        if not durations or len(durations) < 3:
            return None
        return sum(durations) / len(durations)

    # -- FANTASMA (PNL-aware + WR) ----------------------------------------

    def is_fantasma(self, momentum: str, volatility: str, trend: str, direction: str = "LONG") -> tuple:
        fp  = self._fp(momentum, volatility, trend, direction)
        wr  = self.get_wr(momentum, volatility, trend, direction)
        pnl_avg = self.get_pnl_avg(momentum, volatility, trend, direction)
        mem = self._memory.get(fp, {})
        if mem.get('samples', 0) < self.MIN_SAMPLES:
            return False, ''
        if wr < self.FANTASMA_WR_THRESHOLD:
            return True, f"FANTASMA_WR fp={fp} wr={wr:.2f}"
        real_samples = mem.get('real_samples', 0)
        if real_samples >= self.MIN_REAL_SAMPLES and pnl_avg <= self.MIN_PNL_EDGE:
            return True, f"FANTASMA_PNL fp={fp} wr={wr:.2f} pnl_avg={pnl_avg:+.2f} real={real_samples}"
        return False, ''

    # -- CONTEXT-MATCHING - trova i trade passati più simili --------------

    def context_match(self, regime: str, momentum: str, volatility: str, trend: str,
                      direction: str, rsi: float, drift: float, range_position: float) -> dict:
        """
        Cerca i 5 trade passati più simili a questa situazione.
        Ritorna il PnL medio dei vicini e la predizione.
        """
        if len(self._trade_history) < 10:
            return {'pnl_predicted': 0, 'confidence': 0, 'neighbors': 0, 'verdict': 'DATI_INSUFFICIENTI'}

        # Calcola distanza pesata per ogni trade passato
        scored = []
        for t in self._trade_history:
            dist = 0.0
            # Regime match (peso 3)
            dist += (0 if t['regime'] == regime else 3.0)
            # Direction match (peso 2)
            dist += (0 if t['direction'] == direction else 2.0)
            # Momentum match (peso 2)
            mom_map = {'FORTE': 2, 'MEDIO': 1, 'DEBOLE': 0}
            dist += abs(mom_map.get(t['momentum'], 1) - mom_map.get(momentum, 1)) * 1.0
            # Volatility match (peso 1)
            vol_map = {'ALTA': 2, 'MEDIA': 1, 'BASSA': 0}
            dist += abs(vol_map.get(t['volatility'], 1) - vol_map.get(volatility, 1)) * 0.5
            # RSI distance (peso 1.5)
            dist += abs(t.get('rsi', 50) - rsi) / 20.0 * 1.5
            # Drift distance (peso 1.5)
            dist += abs(t.get('drift', 0) - drift) / 0.10 * 1.5
            # Range position (peso 2)
            dist += abs(t.get('range_position', 0.5) - range_position) * 2.0

            scored.append((dist, t))

        # I 5 più vicini
        scored.sort(key=lambda x: x[0])
        neighbors = scored[:5]
        
        if not neighbors:
            return {'pnl_predicted': 0, 'confidence': 0, 'neighbors': 0, 'verdict': 'NO_NEIGHBORS'}

        pnls = [t['pnl'] for _, t in neighbors]
        pnl_avg = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        avg_dist = sum(d for d, _ in neighbors) / len(neighbors)
        confidence = max(0, min(1, 1.0 - avg_dist / 10.0))

        verdict = 'ENTRA' if pnl_avg > self.MIN_PNL_EDGE and wins >= 3 else 'BLOCCA'

        return {
            'pnl_predicted': round(pnl_avg, 2),
            'confidence': round(confidence, 2),
            'neighbors': len(neighbors),
            'wins': wins,
            'avg_distance': round(avg_dist, 2),
            'verdict': verdict,
        }

    # -- CAPSULE ORACOLO STATICHE (OC1-OC5) ------------------------------

    def check_capsules(self, regime, direction, rsi, drift, range_position, momentum, loss_streak) -> tuple:
        """
        5 capsule statiche dell'Oracolo. Ritorna (block, reason) o (False, '').
        """
        # OC1 - RANGING_MIDZONE: non tradare al centro del range
        if regime == "RANGING" and 0.40 <= range_position <= 0.60:
            return True, f"OC1_MIDZONE_{range_position:.0%}"

        # OC2 - RSI_EXTREME: non andare LONG in ipercomprato, SHORT in ipervenduto
        if direction == "LONG" and rsi > 75:
            return True, f"OC2_RSI_HIGH_{rsi:.0f}"
        if direction == "SHORT" and rsi < 25:
            return True, f"OC2_RSI_LOW_{rsi:.0f}"

        # OC3 - DRIFT_DIRECTION: soglia CONTESTUALE per regime
        # RANGING: drift oscilla per natura → soglia larga
        # TRENDING_*: drift è segnale vero → soglia stretta
        # EXPLOSIVE: movimento rapido → soglia media
        _oc3_thr = {"RANGING":-0.25,"TRENDING_BULL":-0.08,
                    "TRENDING_BEAR":-0.08,"EXPLOSIVE":-0.15}.get(regime,-0.15)
        if direction == "LONG" and drift < _oc3_thr:
            return True, f"OC3_DRIFT_{regime}_{drift:+.3f}(thr={_oc3_thr})"
        if direction == "SHORT" and drift > abs(_oc3_thr):
            return True, f"OC3_DRIFT_{regime}_{drift:+.3f}(thr={_oc3_thr})"

        # OC4 - MOMENTUM_RANGING: in RANGING FORTE senza drift = falso
        if regime == "RANGING" and momentum == "FORTE" and abs(drift) < 0.05:
            return True, f"OC4_FALSO_FORTE_drift{drift:+.3f}"

        # OC5 - LOSS_STREAK: dopo 5 loss, fermati
        if loss_streak >= 5:
            return True, f"OC5_LOSS_STREAK_{loss_streak}"

        # OC6 - RSI ESTREMO IN RANGING = rumore, non segnale
        # RSI > 72 in RANGING con SHORT: mercato ipercomprato ma laterale = instabile
        # RSI < 28 in RANGING con LONG: mercato ipervenduto ma laterale = instabile
        # In TRENDING questi RSI sono normali. In RANGING sono veleno.
        if regime == "RANGING" and direction == "SHORT" and rsi > 72:
            return True, f"OC6_RSI_RANGING_SHORT_{rsi:.0f}"
        if regime == "RANGING" and direction == "LONG" and rsi < 28:
            return True, f"OC6_RSI_RANGING_LONG_{rsi:.0f}"

        return False, ''

    # -- CAPSULE AUTO-GENERATIVE ------------------------------------------

    def maybe_generate_capsule(self, fp: str):
        """Genera capsule automatiche quando un fingerprint ha abbastanza dati."""
        mem = self._memory.get(fp)
        if not mem or mem.get('real_samples', 0) < 10:
            return
        
        wr = mem['wins'] / mem['samples'] if mem['samples'] > 0 else 0
        pnl_avg = mem.get('pnl_sum', 0) / mem['samples'] if mem['samples'] > 0 else 0
        
        # Pattern FORTE vincente: RSI basso + drift positivo + bordo basso
        if wr > 0.65 and pnl_avg > self.MIN_PNL_EDGE:
            rsi_wins = list(mem.get('rsi_win', []))
            drift_wins = list(mem.get('drift_win', []))
            rp_wins = list(mem.get('range_pos_win', []))
            
            if rsi_wins and drift_wins and rp_wins:
                avg_rsi = sum(rsi_wins) / len(rsi_wins)
                avg_drift = sum(drift_wins) / len(drift_wins)
                avg_rp = sum(rp_wins) / len(rp_wins)
                
                capsule = {
                    'fp': fp,
                    'type': 'WINNER_PATTERN',
                    'avg_rsi_win': round(avg_rsi, 1),
                    'avg_drift_win': round(avg_drift, 3),
                    'avg_range_pos_win': round(avg_rp, 2),
                    'wr': round(wr, 2),
                    'pnl_avg': round(pnl_avg, 2),
                    'samples': mem['real_samples'],
                    'created': time.time(),
                }
                # Non duplicare
                if not any(c['fp'] == fp and c['type'] == 'WINNER_PATTERN' for c in self._auto_capsules):
                    self._auto_capsules.append(capsule)
                    log.info(f"[ORACOLO] 🧬 CAPSULE AUTO: {fp} → WR {wr:.0%} pnl ${pnl_avg:+.2f} | "
                             f"RSI~{avg_rsi:.0f} drift~{avg_drift:+.3f} rpos~{avg_rp:.2f}")

        # Pattern TOSSICO: genera alert
        if wr < 0.30 and mem['real_samples'] >= 10:
            capsule = {
                'fp': fp,
                'type': 'TOXIC_PATTERN',
                'wr': round(wr, 2),
                'pnl_avg': round(pnl_avg, 2),
                'samples': mem['real_samples'],
                'created': time.time(),
            }
            if not any(c['fp'] == fp and c['type'] == 'TOXIC_PATTERN' for c in self._auto_capsules):
                self._auto_capsules.append(capsule)
                log.info(f"[ORACOLO] ☠️ TOXIC PATTERN: {fp} → WR {wr:.0%} pnl ${pnl_avg:+.2f}")

    # -- DURATION MEMORY - MIN_HOLD adattivo ------------------------------

    def get_dynamic_min_hold(self, momentum: str, volatility: str, trend: str,
                             direction: str = "LONG", regime: str = "RANGING") -> float:
        """
        MIN_HOLD completamente data-driven.

        Gerarchia dei dati (dal più specifico al più generale):
          1. Durata media WIN su questo fingerprint esatto (70%)
          2. Durata media WIN su tutti i fingerprint nello stesso regime (70%)
          3. Durata media di TUTTI i trade reali in memoria (60%)
          4. Zero — lascia decidere solo all'exit energy score

        Nessun numero fisso. La volpe impara dai propri trade.
        """
        # Livello 1: fingerprint specifico
        avg_dur = self.get_avg_duration(momentum, volatility, trend, direction, is_win=True)
        if avg_dur and avg_dur > 8:
            return avg_dur * 0.70

        # Livello 2: media WIN in tutto il regime corrente
        regime_wins = []
        for fp, m in self._memory.items():
            if regime.lower() in fp.lower() or True:  # tutti i pattern
                dw = m.get('durations_win')
                if dw and len(dw) >= 2:
                    regime_wins.extend(list(dw))
        if len(regime_wins) >= 3:
            avg_regime = sum(regime_wins) / len(regime_wins)
            if avg_regime > 8:
                return avg_regime * 0.70

        # Livello 3: media di tutti i trade reali
        all_durs = []
        for m in self._memory.values():
            dw = m.get('durations_win', [])
            dl = m.get('durations_loss', [])
            all_durs.extend(list(dw) + list(dl))
        if len(all_durs) >= 5:
            return (sum(all_durs) / len(all_durs)) * 0.60

        # Livello 4: nessun dato → 25 secondi minimi (evita EXIT_E15 prematuro)
        return 25.0

    # -- SCRITTURA - registra trade completo ------------------------------

    def record(self, momentum: str, volatility: str, trend: str, is_win: bool,
               direction: str = "LONG", pnl: float = 0.0, duration: float = 0.0,
               rsi: float = 50.0, drift: float = 0.0, range_position: float = 0.5,
               regime: str = "RANGING", hour: int = None):
        """Aggiorna memoria + salva trade completo per context-matching."""
        fp = self._fp(momentum, volatility, trend, direction)
        if fp not in self._memory:
            self._memory[fp] = self._new_memory_entry()
        m = self._memory[fp]
        
        # Decay
        m['wins']    *= self.DECAY_FACTOR
        m['samples'] *= self.DECAY_FACTOR
        m['pnl_sum']  = m.get('pnl_sum', 0.0) * self.DECAY_FACTOR
        
        # Nuovo dato
        m['wins']    += 1.0 if is_win else 0.0
        m['samples'] += 1.0
        m['pnl_sum'] += pnl
        m['real_samples'] = m.get('real_samples', 0) + 1

        # Memoria multi-dimensionale
        if is_win:
            m.setdefault('durations_win', deque(maxlen=50)).append(duration)
            m.setdefault('rsi_win', deque(maxlen=50)).append(rsi)
            m.setdefault('drift_win', deque(maxlen=50)).append(drift)
            m.setdefault('range_pos_win', deque(maxlen=50)).append(range_position)
        else:
            m.setdefault('durations_loss', deque(maxlen=50)).append(duration)
            m.setdefault('rsi_loss', deque(maxlen=50)).append(rsi)
            m.setdefault('drift_loss', deque(maxlen=50)).append(drift)
            m.setdefault('range_pos_loss', deque(maxlen=50)).append(range_position)

        # Trade history per context-matching
        self._trade_history.append({
            'fp': fp, 'momentum': momentum, 'volatility': volatility,
            'trend': trend, 'direction': direction, 'regime': regime,
            'rsi': rsi, 'drift': drift, 'range_position': range_position,
            'pnl': pnl, 'duration': duration, 'is_win': is_win,
            'hour': hour or datetime.utcnow().hour, 'ts': time.time(),
        })

        # Prova a generare capsule
        self.maybe_generate_capsule(fp)

        pnl_avg = m['pnl_sum'] / m['samples'] if m['samples'] > 0 else 0
        log.debug(f"[ORACOLO] {fp} → WR={m['wins']/m['samples']:.2f} pnl_avg={pnl_avg:+.2f} real={m['real_samples']}")

    # -- POST-TRADE TRACKER -----------------------------------------------

    def start_post_trade(self, fp: str, exit_price: float, direction: str):
        """Inizia il monitoraggio post-trade per 60 secondi."""
        self._post_trade_queue.append({
            'fp': fp, 'exit_price': exit_price, 'direction': direction,
            'start_time': time.time(), 'prices_after': [],
        })

    def update_post_trade(self, current_price: float):
        """Chiamato ogni tick - aggiorna i post-trade attivi."""
        to_close = []
        for i, pt in enumerate(self._post_trade_queue):
            elapsed = time.time() - pt['start_time']
            pt['prices_after'].append(current_price)
            
            if elapsed >= 60:
                # Valuta se il prezzo ha continuato nella direzione
                if pt['direction'] == 'LONG':
                    continued = current_price > pt['exit_price']
                    delta_after = current_price - pt['exit_price']
                else:
                    continued = current_price < pt['exit_price']
                    delta_after = pt['exit_price'] - current_price
                
                # Registra nell'Oracolo
                mem = self._memory.get(pt['fp'])
                if mem:
                    mem.setdefault('post_continued', deque(maxlen=50)).append(continued)
                    mem.setdefault('post_delta', deque(maxlen=50)).append(delta_after)
                
                if continued:
                    log.info(f"[POST-TRADE] ⚠️ {pt['fp']}: prezzo ha CONTINUATO +${delta_after:.0f} → exit era PRESTO")
                else:
                    log.info(f"[POST-TRADE] [OK] {pt['fp']}: prezzo ha INVERTITO ${delta_after:.0f} → exit era CORRETTA")
                
                to_close.append(i)
        
        for i in reversed(to_close):
            self._post_trade_queue.popleft() if i == 0 else None

    def get_exit_too_early_rate(self, fp: str) -> float:
        """% di volte che l'exit era troppo presto per questo fingerprint."""
        mem = self._memory.get(fp)
        if not mem or 'post_continued' not in mem or len(mem['post_continued']) < 3:
            return 0.5  # default neutro
        continued = list(mem['post_continued'])
        return sum(1 for c in continued if c) / len(continued)

    # -- DUMP -------------------------------------------------------------

    def dump(self) -> dict:
        result = {}
        for fp, m in self._memory.items():
            entry = {
                'wr': round(m['wins']/m['samples'], 3) if m['samples'] > 0 else 0,
                'pnl_avg': round(m.get('pnl_sum', 0)/m['samples'], 2) if m['samples'] > 0 else 0,
                'samples': round(m['samples'], 1),
                'real': m.get('real_samples', 0),
            }
            # Duration info
            dw = m.get('durations_win')
            if dw and len(dw) > 0:
                entry['dur_win_avg'] = round(sum(dw)/len(dw), 1)
            dl = m.get('durations_loss')
            if dl and len(dl) > 0:
                entry['dur_loss_avg'] = round(sum(dl)/len(dl), 1)
            # Post-trade info
            pc = m.get('post_continued')
            if pc and len(pc) > 0:
                entry['exit_too_early'] = round(sum(1 for c in pc if c)/len(pc), 2)
            result[fp] = entry
        
        result['_auto_capsules'] = len(self._auto_capsules)
        result['_trade_history'] = len(self._trade_history)
        return result

# ===========================================================================
# PRE-TRADE SIGNAL TRACKER
# ===========================================================================
# Il tracker speculare al phantom.
#
# Il phantom misura cosa succede quando il sistema NON entra.
# Il PreTradeSignalTracker misura cosa succede quando il sistema VUOLE entrare.
#
# Ogni segnale (score ≥ soglia) viene registrato con:
#   - prezzo, direzione, regime, score, momentum, rsi, macd
#   - poi segue il prezzo per 30s / 60s / 120s
#   - misura delta_30, delta_60, delta_120 nella direzione prevista
#
# Dopo 50 segnali emerge la distribuzione reale:
#   "LONG score 65+ in RANGING → prezzo sale $8 in 60s nel 68% dei casi"
#
# Questo è il MOTORE PREVISIONALE. Non "entro o non entro" —
# "il mercato si muoverà di X in Y secondi con probabilità Z".
# ===========================================================================


class PreTradeSignalTracker:
    """
    Traccia ogni segnale di entry (score ≥ soglia) e misura
    il movimento reale del prezzo nelle successive 30/60/120 secondi.

    Costruisce la distribuzione previsionale del sistema:
    per ogni contesto (regime, direction, score_band) → P(movimento > X in T secondi)
    """

    WINDOWS = [30, 60, 120]  # secondi di osservazione post-segnale
    MAX_OPEN  = 20            # max segnali aperti simultanei
    MAX_CLOSED = 500          # ultimi N segnali chiusi in memoria

    def __init__(self):
        self._open:   list         = []                    # segnali aperti
        self._closed: deque        = deque(maxlen=self.MAX_CLOSED)
        self._stats:  dict         = defaultdict(lambda: {
            'n': 0,
            'delta_30':  [], 'delta_60':  [], 'delta_120': [],
            'hit_30':    [], 'hit_60':    [], 'hit_120':   [],  # True = prezzo andato nella dir giusta
            'pnl_sim':   [],  # PnL simulato con fee
        })

    def record_signal(self, price: float, direction: str, score: float,
                      soglia: float, regime: str, momentum: str,
                      volatility: str, trend: str, rsi: float,
                      macd_hist: float, drift: float):
        """
        Registra segnale se score >= 25.
        Soglia bassa = più dati = distribuzione previsionale più ricca.
        """
        if score < 25:
            return  # sotto 25 è rumore puro
        if len(self._open) >= self.MAX_OPEN:
            return  # non sovraccaricare

        # Score band: categorizza lo score per analisi statistica
        if score >= 75:
            score_band = "FORTE_75+"
        elif score >= 65:
            score_band = "BUONO_65-75"
        elif score >= 58:
            score_band = "BASE_58-65"
        else:
            score_band = "DEBOLE_<58"

        signal = {
            'price':      price,
            'direction':  direction,
            'score':      score,
            'soglia':     soglia,
            'score_band': score_band,
            'regime':     regime,
            'momentum':   momentum,
            'volatility': volatility,
            'trend':      trend,
            'rsi':        rsi,
            'macd_hist':  macd_hist,
            'drift':      drift,
            'ts':         time.time(),
            'prices':     [],           # prezzi raccolti
            'closed':     False,
            'results':    {},           # delta_30, delta_60, delta_120
        }
        self._open.append(signal)

    def update(self, current_price: float):
        """Chiamato ogni tick. Aggiorna i segnali aperti."""
        now     = time.time()
        to_close = []

        for i, sig in enumerate(self._open):
            elapsed = now - sig['ts']
            sig['prices'].append(current_price)

            # Calcola risultati alle finestre temporali
            for w in self.WINDOWS:
                key = f'delta_{w}'
                if key not in sig['results'] and elapsed >= w:
                    if sig['direction'] == 'LONG':
                        delta = current_price - sig['price']
                    else:
                        delta = sig['price'] - current_price
                    # PnL LORDO: fee esclusa dal monitoring
                    pnl_sim = delta * (5000.0 / sig['price'])
                    sig['results'][key]        = round(delta, 2)
                    sig['results'][f'pnl_{w}'] = round(pnl_sim, 2)
                    sig['results'][f'hit_{w}']    = delta > 0

            # Chiudi dopo la finestra massima
            if elapsed >= max(self.WINDOWS):
                to_close.append(i)

        for i in reversed(to_close):
            sig = self._open.pop(i)
            sig['closed'] = True
            self._closed.append(sig)
            self._update_stats(sig)

    def _update_stats(self, sig: dict):
        """Aggiorna la distribuzione statistica dopo ogni segnale chiuso."""
        # Chiave per la distribuzione: regime + direction + score_band
        key = f"{sig['regime']}|{sig['direction']}|{sig['score_band']}"
        s   = self._stats[key]
        s['n'] += 1

        for w in self.WINDOWS:
            d   = sig['results'].get(f'delta_{w}')
            h   = sig['results'].get(f'hit_{w}')
            pnl = sig['results'].get(f'pnl_{w}')
            if d is not None:
                s[f'delta_{w}'].append(d)
                s[f'hit_{w}'].append(h)
            if pnl is not None:
                s['pnl_sim'].append(pnl)

        # Mantieni solo ultimi 100 per ogni chiave
        all_fields = ([f'delta_{w}' for w in self.WINDOWS] +
                      [f'hit_{w}'   for w in self.WINDOWS] + ['pnl_sim'])
        for field in all_fields:
            if field in s and len(s[field]) > 100:
                s[field] = s[field][-100:]

    def get_prediction(self, direction: str, score: float,
                       regime: str) -> dict:
        """
        Ritorna la predizione per questo contesto.
        "Se il sistema dice LONG con score 65 in RANGING, quanto si muove?"
        """
        if score >= 75:   band = "FORTE_75+"
        elif score >= 65: band = "BUONO_65-75"
        elif score >= 58: band = "BASE_58-65"
        else:             band = "DEBOLE_<58"

        key = f"{regime}|{direction}|{band}"
        s   = self._stats.get(key)

        if not s or s['n'] < 5:
            return {'confidence': 0, 'data_insufficienti': True, 'n': s['n'] if s else 0}

        result = {'n': s['n'], 'context': key}
        for w in self.WINDOWS:
            deltas = s.get(f'delta_{w}', [])
            hits   = s.get(f'hit_{w}',   [])
            if deltas:
                result[f'avg_delta_{w}s']  = round(sum(deltas)/len(deltas), 2)
                result[f'hit_rate_{w}s']   = round(sum(hits)/len(hits), 3) if hits else 0
                result[f'max_delta_{w}s']  = round(max(deltas), 2)

        pnls = s.get('pnl_sim', [])
        if pnls:
            result['avg_pnl_sim']  = round(sum(pnls)/len(pnls), 2)
            result['pnl_positive'] = round(sum(1 for p in pnls if p > 0)/len(pnls), 3)

        # Confidence: cresce con n campioni, max 1.0 a 50 campioni
        result['confidence'] = min(1.0, s['n'] / 50)
        return result

    def dump_top(self, n: int = 10) -> list:
        """Top N contesti per numero di segnali — per la dashboard."""
        rows = []
        for key, s in self._stats.items():
            if s['n'] < 1:
                continue
            hits_60 = s.get('hit_60', [])
            deltas_60 = s.get('delta_60', [])
            rows.append({
                'context':    key,
                'n':          s['n'],
                'hit_60s':    round(sum(hits_60)/len(hits_60), 3) if hits_60 else 0,
                'avg_delta_60s': round(sum(deltas_60)/len(deltas_60), 2) if deltas_60 else 0,
                'pnl_sim_avg': round(sum(s['pnl_sim'])/len(s['pnl_sim']), 2) if s['pnl_sim'] else 0,
            })
        rows.sort(key=lambda x: x['n'], reverse=True)
        return rows[:n]

    def predict_from_signals(self, regime: str, direction: str,
                              score: float, drift: float,
                              rsi: float) -> dict:
        """
        Predizione basata sui segnali storici chiusi.
        Cerca i segnali più simili e predice hit_rate e delta.

        Questo è l'Oracolo predittivo — anticipa prima che accada,
        non reagisce a quello che è già successo.
        """
        if len(self._closed) < 20:
            return {'confidence': 0, 'hit_rate': 0.5,
                    'avg_delta': 0, 'n_vicini': 0,
                    'verdict': 'DATI_INSUFFICIENTI'}

        # Cerca vicini per distanza pesata
        vicini = []
        for sig in self._closed:
            if sig.get('direction') != direction: continue
            if sig.get('regime')    != regime:    continue

            # Distanza su score, drift, rsi
            d_score = abs(sig.get('score', 50) - score) / 10.0
            d_drift = abs(sig.get('drift', 0)  - drift) / 0.05
            d_rsi   = abs(sig.get('rsi',   50) - rsi)   / 15.0

            dist = d_score * 2.0 + d_drift * 1.5 + d_rsi * 1.0

            # Solo vicini abbastanza simili
            if dist > 4.0: continue

            h60  = sig.get('results', {}).get('hit_60',  None)
            d60  = sig.get('results', {}).get('delta_60', None)
            p60  = sig.get('results', {}).get('pnl_60',   None)
            if h60 is None: continue

            vicini.append({'dist': dist, 'hit': h60, 'delta': d60 or 0, 'pnl': p60})

        if len(vicini) < 5:
            return {'confidence': 0, 'hit_rate': 0.5,
                    'avg_delta': 0, 'n_vicini': len(vicini),
                    'verdict': 'VICINI_INSUFFICIENTI'}

        # Peso inverso alla distanza
        tot_peso = sum(1/(v['dist']+0.1) for v in vicini)
        hit_rate  = sum((1/(v['dist']+0.1))*v['hit']   for v in vicini) / tot_peso
        avg_delta = sum((1/(v['dist']+0.1))*v['delta'] for v in vicini) / tot_peso

        # Confidence: cresce con n_vicini, max a 50
        confidence = min(1.0, len(vicini) / 50)

        # CRITERIO ECONOMICO EMERGENTE — nessuna soglia fissa
        # Fee simulata nella stessa scala di pnl_60: $250 * 0.02% * 2 = $0.10
        # hit_economica = % vicini con pnl_60 > fee_sim (coprono davvero i costi)
        # Il numero emerge dalla distribuzione storica dei vicini — non è inventato
        FEE_SIM = 2.00  # fee futures: $5000 × 0.02% × 2 = $2.00
        pnl_vicini = [v['pnl'] for v in vicini if v.get('pnl') is not None]

        if pnl_vicini:
            hit_econ = sum(1 for p in pnl_vicini if p > FEE_SIM) / len(pnl_vicini)
            pnl_medio = sum(pnl_vicini) / len(pnl_vicini)
            # ENTRA: maggioranza dei vicini copre davvero le fee E hit direzionale ok
            if hit_econ >= 0.50 and hit_rate >= 0.60:
                verdict = 'ENTRA'
            # BLOCCA: meno di 1/3 dei vicini copre le fee O hit direzionale basso
            elif hit_econ < 0.30 or hit_rate <= 0.40:
                verdict = 'BLOCCA'
            else:
                verdict = 'NEUTRO'
        else:
            # Fallback senza pnl: solo hit_rate + delta conservativo
            if hit_rate >= 0.65 and avg_delta > 20:
                verdict = 'ENTRA'
            elif hit_rate <= 0.40 or avg_delta < -5:
                verdict = 'BLOCCA'
            else:
                verdict = 'NEUTRO'

        return {
            'confidence': round(confidence, 2),
            'hit_rate':   round(hit_rate,   3),
            'avg_delta':  round(avg_delta,  2),
            'n_vicini':   len(vicini),
            'verdict':    verdict,
        }

    def get_open_count(self) -> int:
        return len(self._open)


# ===========================================================================
# 5 CAPSULE INTELLIGENTI
# ===========================================================================


class MatrimonioIntelligente:
    """
    7 matrimoni con WR atteso e duration media.
    La chiave è (momentum, volatility, trend).
    """
    MARRIAGES = {
        # -- TREND UP -----------------------------------------------------
        ("FORTE", "BASSA",  "UP"):      {"name": "STRONG_BULL",    "wr": 0.85, "duration_avg": 45, "confidence": 0.95},
        ("FORTE", "MEDIA",  "UP"):      {"name": "STRONG_MED",     "wr": 0.75, "duration_avg": 30, "confidence": 0.85},
        ("FORTE", "ALTA",   "UP"):      {"name": "STRONG_VOLATILE","wr": 0.65, "duration_avg": 20, "confidence": 0.70},
        ("MEDIO", "BASSA",  "UP"):      {"name": "MEDIUM_BULL",    "wr": 0.70, "duration_avg": 25, "confidence": 0.80},
        ("MEDIO", "MEDIA",  "UP"):      {"name": "CAUTIOUS",       "wr": 0.60, "duration_avg": 15, "confidence": 0.65},
        ("MEDIO", "ALTA",   "UP"):      {"name": "CAUTIOUS_VOL",   "wr": 0.50, "duration_avg": 12, "confidence": 0.55},
        ("DEBOLE","BASSA",  "UP"):      {"name": "WEAK_BULL",      "wr": 0.55, "duration_avg": 15, "confidence": 0.55},
        ("DEBOLE","MEDIA",  "UP"):      {"name": "WEAK_MED_UP",    "wr": 0.45, "duration_avg": 10, "confidence": 0.45},
        ("DEBOLE","ALTA",   "UP"):      {"name": "WEAK_VOL_UP",    "wr": 0.35, "duration_avg": 8,  "confidence": 0.35},
        # -- TREND SIDEWAYS -----------------------------------------------
        # CALIBRATO su 500+ trade reali (sessioni 22-23 marzo 2026)
        ("FORTE", "BASSA",  "SIDEWAYS"):{"name": "RANGE_STRONG",   "wr": 0.65, "duration_avg": 45, "confidence": 0.70},
        ("FORTE", "MEDIA",  "SIDEWAYS"):{"name": "RANGE_MED_F",    "wr": 0.60, "duration_avg": 40, "confidence": 0.65},
        ("FORTE", "ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_F",    "wr": 0.60, "duration_avg": 35, "confidence": 0.60},
        ("MEDIO", "BASSA",  "SIDEWAYS"):{"name": "RANGE_CALM",     "wr": 0.50, "duration_avg": 35, "confidence": 0.55},
        ("MEDIO", "MEDIA",  "SIDEWAYS"):{"name": "RANGE_NEUTRAL",  "wr": 0.45, "duration_avg": 30, "confidence": 0.45},
        ("MEDIO", "ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_M",    "wr": 0.43, "duration_avg": 30, "confidence": 0.40},
        ("DEBOLE","BASSA",  "SIDEWAYS"):{"name": "RANGE_DEAD",     "wr": 0.35, "duration_avg": 25, "confidence": 0.30},
        ("DEBOLE","MEDIA",  "SIDEWAYS"):{"name": "WEAK_NEUTRAL",   "wr": 0.35, "duration_avg": 25, "confidence": 0.30},
        ("DEBOLE","ALTA",   "SIDEWAYS"):{"name": "RANGE_VOL_W",    "wr": 0.19, "duration_avg": 20, "confidence": 0.15},
        # -- TREND DOWN ---------------------------------------------------
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

# ===========================================================================
# MEMORIA MATRIMONI - trust, separazione, divorzio
# ===========================================================================


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

    def get_wr(self, name: str) -> float:
        total = self.wins.get(name, 0) + self.losses.get(name, 0)
        return self.wins.get(name, 0) / total if total > 0 else 0.5

    def get_trust(self, name: str) -> float:
        return self.trust.get(name, 50) / 100.0

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

# ===========================================================================
# ANALIZZATORE CONTESTO
# ===========================================================================


class ContestoAnalyzer:
    """Momentum, volatility, trend dai prezzi recenti."""

    def __init__(self, window: int = 50):
        self.prices    = deque(maxlen=window)
        self.tick_count= 0

    def add_price(self, price: float):
        self.prices.append(price)
        self.tick_count += 1

    def analyze(self, regime=None, drift=None):
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

        # -- RANGING DOWNGRADE: FORTE in laterale senza direzione = falso --
        # 4 tick su = FORTE, ma in RANGING con drift ~0 è solo rumore.
        # Declassa solo se drift conferma assenza di direzione reale.
        # NON declassare se drift è forte (impulso vero al bordo del range).
        if regime == "RANGING" and trend == "SIDEWAYS" and drift is not None:
            if abs(drift) < 0.10:  # drift sotto 0.10% = nessuna direzione
                if momentum == "FORTE":
                    momentum = "MEDIO"
                elif momentum == "MEDIO":
                    momentum = "DEBOLE"

        return momentum, volatility, trend

# ===========================================================================
# PERSISTENZA SQLite - capital e trades sopravvivono al restart
# ===========================================================================


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS capsule_permanenti (
                    id                 TEXT PRIMARY KEY,
                    azione             TEXT,
                    params_json        TEXT,
                    motivo             TEXT,
                    forza              REAL,
                    contesto           TEXT,
                    creata_ts          TEXT,
                    analisi_causale    TEXT,
                    prompt_contestuale TEXT,
                    n_attivazioni      INTEGER DEFAULT 0
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
            log.error(f"[PERSIST] Load: {e} - uso defaults")
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

            # -- OracoloDinamico 2.0 --------------------------------------
            # Serializza _memory con deque → list per JSON
            oracolo_data = {}
            for fp, m in oracolo._memory.items():
                entry = {}
                for k, v in m.items():
                    if isinstance(v, deque):
                        entry[k] = list(v)
                    else:
                        entry[k] = v
                oracolo_data[fp] = entry
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('oracolo', ?)",
                        (json.dumps(oracolo_data),))

            # -- MemoriaMatrimoni ----------------------------------------
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

            # -- AutoCalibratore params -----------------------------------
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('calibra_params', ?)",
                        (json.dumps(calibratore.params),))

            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[BRAIN_SAVE] {e}")

    def save_runtime_state(self, bot):
        """
        Persiste TUTTO lo stato runtime che ha valore statistico.
        Chiamato ogni 5 minuti. Zero dati preziosi persi tra deploy.
        """
        try:
            data = {
                # Phantom stats — storico protezioni/zavorre
                'phantom_stats': bot._phantom_stats,

                # Ultimi 100 fantasmi chiusi
                'phantoms_closed': [
                    {k: v for k, v in ph.items()
                     if k not in ('prices',)} # escludi liste grandi
                    for ph in list(bot._phantoms_closed)
                ],

                # Trade buffer IntelligenzaAutonoma
                'ia_trade_buffer': list(bot.realtime_engine._trade_buffer),

                # PreBreakout results per auto-tune
                'pb3_results': list(bot.campo._pb3_results)
                    if hasattr(bot.campo, '_pb3_results') else [],

                # Ultimi risultati campo per history_factor
                'campo_recent_results': list(bot.campo._recent_results)
                    if hasattr(bot.campo, '_recent_results') else [],

                # State engine — ultimi trade M2
                'm2_recent_trades': list(bot._m2_recent_trades),

                # Contatori M2
                'm2_wins':   bot._m2_wins,
                'm2_losses': bot._m2_losses,
                'm2_pnl':    bot._m2_pnl,
                'm2_trades': bot._m2_trades,
                # Pesi SC — sopravvivono ai restart
                'sc_pesi': bot.supercervello._pesi if hasattr(bot,'supercervello') else {},
                'sc_storia_n': len(bot.supercervello._storia) if hasattr(bot,'supercervello') else 0,
                # NOTA: soglia NON salvata — viene calcolata dinamicamente dal Signal Tracker
                # Veritas — salva segnali chiusi e statistiche
                'veritas_closed': [
                    {k:v for k,v in s.items() if k != 'deltas'}
                    for s in list(bot.veritas._closed)[-200:]
                ] if hasattr(bot, 'veritas') else [],
                'veritas_stats': bot.veritas._stats if hasattr(bot, 'veritas') else {},
            }
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('runtime_state', ?)",
                        (json.dumps(data, default=str),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[RUNTIME_SAVE] {e}")

    def load_runtime_state(self, bot):
        """Ripristina lo stato runtime dal DB."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = dict(conn.execute(
                "SELECT key, value FROM bot_state WHERE key='runtime_state'"
            ).fetchall())
            conn.close()

            if 'runtime_state' not in rows:
                return

            data = json.loads(rows['runtime_state'])
            restored = []

            # Phantom stats
            if 'phantom_stats' in data:
                for k, v in data['phantom_stats'].items():
                    if k not in bot._phantom_stats:
                        bot._phantom_stats[k] = v
                restored.append(f"phantom_stats:{len(data['phantom_stats'])}")

            # IA trade buffer
            if 'ia_trade_buffer' in data:
                for t in data['ia_trade_buffer']:
                    bot.realtime_engine._trade_buffer.append(t)
                restored.append(f"ia_buffer:{len(data['ia_trade_buffer'])}")

            # PreBreakout results
            if 'pb3_results' in data and hasattr(bot.campo, '_pb3_results'):
                for r in data['pb3_results']:
                    bot.campo._pb3_results.append(r)
                restored.append(f"pb3:{len(data['pb3_results'])}")

            # Campo recent results
            if 'campo_recent_results' in data and hasattr(bot.campo, '_recent_results'):
                for r in data['campo_recent_results']:
                    bot.campo._recent_results.append(r)
                restored.append(f"campo_recent:{len(data['campo_recent_results'])}")

            # State engine recent trades
            if 'm2_recent_trades' in data:
                for t in data['m2_recent_trades']:
                    bot._m2_recent_trades.append(t)
                restored.append(f"m2_trades:{len(data['m2_recent_trades'])}")

            # Ripristina pesi SC
            if 'sc_pesi' in data and hasattr(bot, 'supercervello') and data['sc_pesi']:
                pesi_caricati = data['sc_pesi']
                # Applica pavimento — campo_carica non può essere sotto 30%
                if pesi_caricati.get('campo_carica', 0) < 0.30:
                    log.warning("[RUNTIME_LOAD] ⚠️ Pesi SC degradati — ripristino valori sicuri")
                    pesi_caricati = {
                        'oracolo_fp': 0.25, 'signal_tracker': 0.20,
                        'campo_carica': 0.30, 'matrimonio': 0.13, 'phantom_ratio': 0.12
                    }
                if pesi_caricati.get('campo_carica', 0) > 0.45:
                    log.warning("[RUNTIME_LOAD] ⚠️ Pesi degradati — reset default")
                    pesi_caricati = dict(SuperCervello.PESI_DEFAULT)
                bot.supercervello._pesi = pesi_caricati
                log.info(f"[RUNTIME_LOAD] 🧠 Pesi SC: {pesi_caricati}")

            # Ripristina Veritas
            if 'veritas_closed' in data and hasattr(bot, 'veritas'):
                for s in data['veritas_closed']:
                    bot.veritas._closed.append(s)
                    bot.veritas._aggiorna_stats(s)
                log.info(f"[RUNTIME_LOAD] ⚖️ Veritas ripristinato: {len(data['veritas_closed'])} segnali")
            if 'veritas_stats' in data and hasattr(bot, 'veritas'):
                for k,v in data['veritas_stats'].items():
                    if k not in bot.veritas._stats:
                        bot.veritas._stats[k] = v

            # Soglia calcolata dinamicamente dal Signal Tracker — mai dal DB
            # Il DB non salva la soglia: viene ricalcolata ad ogni boot
            _soglia_dinamica = _calcola_soglia_da_signal_tracker(bot)
            bot.campo.SOGLIA_BASE = _soglia_dinamica['base']
            bot.campo.SOGLIA_MIN  = _soglia_dinamica['min']
            log.info(f"[RUNTIME_LOAD] 🎯 Soglia dinamica calcolata: "
                    f"base={_soglia_dinamica['base']} min={_soglia_dinamica['min']} "
                    f"({_soglia_dinamica['motivo']})")

            if restored:
                log.info(f"[RUNTIME_LOAD] 💾 Stato runtime ripristinato → {' | '.join(restored)}")

        except Exception as e:
            log.error(f"[RUNTIME_LOAD] {e}")

    def save_signal_tracker(self, tracker):
        """Persiste le stats del PreTradeSignalTracker su DB — sopravvive ai restart."""
        try:
            import json
            # Serializza solo _stats (le distribuzioni) — non i segnali aperti
            stats_data = {}
            for key, s in tracker._stats.items():
                stats_data[key] = {
                    'n':        s['n'],
                    'delta_30': list(s.get('delta_30', [])),
                    'delta_60': list(s.get('delta_60', [])),
                    'delta_120':list(s.get('delta_120',[])),
                    'hit_30':   list(s.get('hit_30',   [])),
                    'hit_60':   list(s.get('hit_60',   [])),
                    'hit_120':  list(s.get('hit_120',  [])),
                    'pnl_sim':  list(s.get('pnl_sim',  [])),
                }
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('signal_tracker', ?)",
                        (json.dumps({
                            'stats':        stats_data,
                            'total_closed': len(tracker._closed),
                        }),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[SIGNAL_SAVE] {e}")

    def load_signal_tracker(self, tracker):
        """Ripristina le stats del PreTradeSignalTracker dal DB."""
        try:
            import json
            conn = sqlite3.connect(self.db_path)
            rows = dict(conn.execute("SELECT key, value FROM bot_state WHERE key='signal_tracker'").fetchall())
            conn.close()
            if 'signal_tracker' not in rows:
                return
            data = json.loads(rows['signal_tracker'])
            stats = data.get('stats', {})
            for key, s in stats.items():
                tracker._stats[key] = {
                    'n':        s.get('n', 0),
                    'delta_30': s.get('delta_30', []),
                    'delta_60': s.get('delta_60', []),
                    'delta_120':s.get('delta_120',[]),
                    'hit_30':   s.get('hit_30',   []),
                    'hit_60':   s.get('hit_60',   []),
                    'hit_120':  s.get('hit_120',  []),
                    'pnl_sim':  s.get('pnl_sim',  []),
                }
            total = data.get('total_closed', 0)
            log.info(f"[SIGNAL_LOAD] 📡 SignalTracker ripristinato: "
                     f"{len(stats)} contesti, {total} segnali storici")
        except Exception as e:
            log.error(f"[SIGNAL_LOAD] {e}")

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

            # -- OracoloDinamico 2.0 --------------------------------------
            if 'oracolo' in rows:
                raw = json.loads(rows['oracolo'])
                deque_fields = ['durations_win', 'durations_loss', 'rsi_win', 'rsi_loss',
                               'drift_win', 'drift_loss', 'range_pos_win', 'range_pos_loss',
                               'post_continued', 'post_delta']
                for fp, data in raw.items():
                    entry = {
                        'wins':    float(data.get('wins', 0)),
                        'samples': float(data.get('samples', 0)),
                        'pnl_sum': float(data.get('pnl_sum', 0)),
                        'real_samples': int(data.get('real_samples', 0)),
                    }
                    for df in deque_fields:
                        if df in data and isinstance(data[df], list):
                            entry[df] = deque(data[df], maxlen=50)
                        else:
                            entry[df] = deque(maxlen=50)
                    oracolo._memory[fp] = entry
                restored.append(f"Oracolo 2.0: {len(oracolo._memory)} fingerprint, "
                               f"{sum(m.get('real_samples',0) for m in oracolo._memory.values())} real")

            # -- MemoriaMatrimoni ----------------------------------------
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

            # -- AutoCalibratore params -----------------------------------
            if 'calibra_params' in rows:
                saved = json.loads(rows['calibra_params'])
                calibratore.params.update(saved)
                restored.append(f"Calibra: seed={saved.get('seed_threshold', '?')}")

            if restored:
                log.info(f"[BRAIN_LOAD] 🧠 Intelligenza ripristinata → {' | '.join(restored)}")
            else:
                log.info("[BRAIN_LOAD] Primo avvio - nessuna memoria precedente")

        except Exception as e:
            log.error(f"[BRAIN_LOAD] {e} - parto da zero")

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

# ===========================================================================
# ★ REGIME DETECTOR - contesto macro sopra tutto
#   Classifica il regime strutturale del mercato su finestra larga.
#   TRENDING_BULL / TRENDING_BEAR / RANGING / EXPLOSIVE
#   Il regime cambia i parametri di tutto il sistema sottostante.
# ===========================================================================


class RegimeDetector:
    """
    Osserva 500 tick e classifica il regime macro.
    Non si confonde con i tick singoli - lavora sulla struttura.

    Regimi:
      TRENDING_BULL  - trend rialzista strutturale, alta directional consistency
      TRENDING_BEAR  - trend ribassista strutturale
      RANGING        - mercato laterale, alta volatilita relativa, bassa direzione
      EXPLOSIVE      - breakout improvviso, volume spike + range expansion
    """

    WINDOW = 200   # tick per valutare il regime — finestra più reattiva

    # Moltiplicatori per ogni regime - applicati ai parametri del calibratore
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
            'seed_mult':      1.30,   # molto selettivo - il ranging è il nemico
            'fp_wr_mult':     1.15,
            'size_mult':      0.60,
            'drawdown_mult':  0.70,
        },
        'EXPLOSIVE': {
            'seed_mult':      0.85,   # velocita conta - entra prima
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
        if len(self.prices) < 50:
            return 'RANGING', 0.0, {}

        prices  = list(self.prices)
        volumes = list(self.volumes)
        n       = len(prices)

        # -- Trend strutturale ---------------------------------------------
        # Regressione lineare semplificata: confronta meta iniziale vs finale
        mid        = n // 2
        avg_first  = sum(prices[:mid]) / mid
        avg_second = sum(prices[mid:]) / (n - mid)
        trend_pct  = (avg_second - avg_first) / avg_first * 100

        # -- Directional Consistency su finestra larga ---------------------
        changes    = [prices[i+1] - prices[i] for i in range(n-1)]
        up_count   = sum(1 for c in changes if c > 0)
        dir_ratio  = up_count / len(changes)   # 0=tutto giù, 1=tutto su

        # -- Volatilita strutturale -----------------------------------------
        abs_changes = [abs(c) for c in changes]
        avg_change  = sum(abs_changes) / len(abs_changes)
        # Confronta volatilita prima vs seconda meta
        vol_first   = sum(abs_changes[:mid]) / mid
        vol_second  = sum(abs_changes[mid:]) / (n - mid)
        vol_ratio   = vol_second / max(vol_first, 0.001)

        # -- Volume acceleration --------------------------------------------
        vol_recent  = sum(volumes[-50:]) / 50
        vol_base    = sum(volumes[:50])  / 50
        vol_accel   = vol_recent / max(vol_base, 0.001)

        # -- Classificazione -----------------------------------------------
        regime     = 'RANGING'
        confidence = 0.5

        if vol_accel > 2.0 and vol_ratio > 1.5:
            # Volume esploso + volatilita in aumento → EXPLOSIVE
            regime     = 'EXPLOSIVE'
            confidence = min(1.0, vol_accel / 3.0)

        elif trend_pct > 0.3 and dir_ratio > 0.52:
            # Trend rialzista strutturale — soglia abbassata per rilevare prima
            regime     = 'TRENDING_BULL'
            confidence = min(1.0, (dir_ratio - 0.50) * 5)

        elif trend_pct < -0.3 and dir_ratio < 0.48:
            # Trend ribassista strutturale
            regime     = 'TRENDING_BEAR'
            confidence = min(1.0, (0.50 - dir_ratio) * 5)

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


# ===========================================================================
# ★ MOMENTUM DECELEROMETER - exit intelligente
#   Non misura il momentum - misura quanto velocemente sta decelerando.
#   Uscire quando decelera forte, non quando è gia morto.
# ===========================================================================


class MomentumDecelerometer:
    """
    Calcola la derivata seconda del momentum.
    Se il momentum stava salendo e ora sta scendendo velocemente
    → segnale di uscita anticipata prima che il prezzo inverta.

    Restituisce:
      decel_score [0-1] - 0=momentum stabile, 1=decelera forte
      should_exit bool  - True se la decelerazione supera la soglia
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


# ===========================================================================
# ★ POSITION SIZER - la tua fisica applicata
#   Size come funzione CONTINUA dell'intensita dell'impulso.
#   Non più 1.0 / 1.3 / 1.5 discreti - una curva che riflette
#   esattamente quanto il mercato ti sta dando.
# ===========================================================================


class PositionSizer:
    """
    Calcola la size ottimale come funzione continua di 3 segnali:
      1. seed_score      - forza dell'impulso (peso 40%)
      2. fingerprint_wr  - affidabilita storica del contesto (peso 35%)
      3. confidence      - certezza del matrimonio (peso 25%)

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
        # seed_score è gia [0, 1]
        seed_norm = min(1.0, max(0.0, seed_score))

        # fingerprint_wr [0.30, 0.80] → [0, 1] (calibrato su valori reali)
        fp_norm = min(1.0, max(0.0, (fingerprint_wr - 0.30) / 0.50))

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


# ===========================================================================
# ★ AUTO CALIBRATORE - TUA INVENZIONE
#   Osserva i risultati reali e aggiusta i parametri statici.
#   Stessa pazienza e cautela del DNA del sistema:
#   - Minimo 30 trade prima di toccare qualsiasi soglia
#   - Step massimo ±0.02 per aggiustamento
#   - Invertibile se la modifica peggiora i risultati
#   - Log narrativo di ogni modifica
# ===========================================================================


class AutoCalibratore:
    """
    Calibra automaticamente i parametri statici basandosi sui risultati reali.
    Non è ubriaco: aspetta evidenza solida, cambia in piccoli passi,
    ricorda ogni modifica e può tornare indietro.
    """

    # -- Limiti di sicurezza - non si esce mai da questi range -------------
    LIMITS = {
        'seed_threshold':      (0.25, 0.70),   # mai troppo permissivo né troppo restrittivo
        'cap1_soglia_buona':   (0.45, 0.80),   # Capsule1 soglia "coerenza buona"
        'cap1_soglia_perfetta':(0.60, 0.90),   # Capsule1 soglia "coerenza perfetta"
        'cap3_fp_minimo':      (0.35, 0.65),   # Capsule3 protezione fp minimo
        'cap4_soglia_buona':   (0.50, 0.80),   # Capsule4 opportunita buona
        'cap5_conf_ok':        (0.50, 0.80),   # Capsule5 timing OK
        'divorce_drawdown':    (1.5,  5.0),    # drawdown trigger
    }

    STEP          = 0.05    # era 0.02 - troppo lento, il mercato cambia regime in minuti
    MIN_TRADES    = 10      # era 30 - con stop loss 2% il rischio è controllato, impara prima
    MIN_DELTA_WR  = 0.05    # differenza minima WR reale vs atteso per intervenire
    HISTORY_SIZE  = 5       # quante calibrazioni ricordare per inversione
    MIN_CALIB_INTERVAL = 900  # minimo 15 minuti tra calibrazioni - anti-oscillazione

    def __init__(self):
        # Parametri correnti - inizializzati ai valori di default
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

        # -- 1. SEED THRESHOLD ---------------------------------------------
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

        # -- 2. DIVORCE DRAWDOWN -------------------------------------------
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

        # -- 3. CAP1 SOGLIA COERENZA ---------------------------------------
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


# ===========================================================================
# ★ CAMPO GRAVITAZIONALE - MOTORE 2 (CARTESIANO)
#   Nessun filtro binario tranne i veti assoluti.
#   Ogni condizione accumula punti. La soglia si muove con il contesto.
#   La size è funzione continua della distanza punteggio-soglia.
# ===========================================================================


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

    # -- VETI ASSOLUTI -----------------------------------------------------
    # LONG: non entrare in mercato che crolla
    VETI_LONG = {
        ("DEBOLE", "ALTA", "DOWN"),    # TRAP - WR 5% per LONG
        ("FORTE",  "ALTA", "DOWN"),    # PANIC - WR 15% per LONG
        # RIMOSSO 21/04/2026: Signal Tracker V15 dice DEBOLE|ALTA|SIDEWAYS = WR 73% su 1632 campioni reali
        # ("DEBOLE", "ALTA", "SIDEWAYS"),# era WR 19% dati V13 — ora 73% V15
        ("FORTE",  "ALTA", "SIDEWAYS"),# RANGE_VOL_F - WR 34% dati reali Oracolo
        ("MEDIO",  "ALTA", "SIDEWAYS"),# RANGE_VOL_M - WR 28% dati reali Oracolo
    }
    # SHORT: non entrare in mercato che esplode al rialzo
    VETI_SHORT = {
        ("FORTE",  "BASSA", "UP"),     # STRONG_BULL - WR 5% per SHORT
        ("FORTE",  "MEDIA", "UP"),     # STRONG_MED - pericoloso per SHORT
        ("DEBOLE", "ALTA", "SIDEWAYS"),# RANGE_VOL_W - WR 10% in SHORT
        ("FORTE",  "ALTA", "SIDEWAYS"),# RANGE_VOL_F - WR 12% in SHORT
        # RIMOSSO 02/05/2026: real=0 campioni reali SHORT in questo contesto — WR 8% era simulato
        # ("MEDIO",  "ALTA", "SIDEWAYS"),# RANGE_VOL_M - WR 8% in SHORT — da rivalutare con dati reali
    }
    FANTASMA_VETO_MIN_SAMPLES = 20
    FANTASMA_VETO_MAX_WR      = 0.30
    MAX_LOSS_CONSECUTIVI      = 3

    # -- PESI DEL CAMPO (totale = 100) -------------------------------------
    # V2: aggiunto RSI e MACD come consiglieri. Pesi ridistribuiti.
    W_SEED        = 25    # era 30 - cede 5 ai consiglieri
    W_FINGERPRINT = 20    # era 25 - cede 5 ai consiglieri
    W_MOMENTUM    = 12    # era 15
    W_TREND       = 12    # era 15
    W_VOLATILITY  = 8     # era 10
    W_REGIME      = 3     # era 5
    W_RSI         = 10    # NUOVO - il consigliere ipervenduto/ipercomprato
    W_MACD        = 10    # NUOVO - il consigliere trend/momentum

    # -- SCORING PER DIMENSIONE --------------------------------------------
    # LONG - impulso rialzista
    MOMENTUM_SCORE_LONG  = {"FORTE": 1.0,  "MEDIO": 0.67, "DEBOLE": 0.20}
    TREND_SCORE_LONG     = {"UP": 1.0,     "SIDEWAYS": 0.47, "DOWN": 0.0}
    REGIME_SCORE_LONG    = {"TRENDING_BULL": 1.0, "EXPLOSIVE": 0.80,
                            "RANGING": 0.20, "TRENDING_BEAR": 0.0}

    # SHORT - impulso ribassista (tutto invertito)
    MOMENTUM_SCORE_SHORT = {"FORTE": 0.20, "MEDIO": 0.67, "DEBOLE": 1.0}
    TREND_SCORE_SHORT    = {"UP": 0.0,     "SIDEWAYS": 0.47, "DOWN": 1.0}
    REGIME_SCORE_SHORT   = {"TRENDING_BULL": 0.0, "EXPLOSIVE": 0.80,
                            "RANGING": 0.20, "TRENDING_BEAR": 1.0}

    # VOL_SCORE è uguale per LONG e SHORT - alta volatilita è sempre rischio
    VOL_SCORE       = {"BASSA": 1.0,  "MEDIA": 0.60, "ALTA": 0.20}

    # -- SOGLIA DINAMICA ---------------------------------------------------
    SOGLIA_BASE = 50
    REGIME_FACTOR = {"TRENDING_BULL": 0.80, "EXPLOSIVE": 0.85,
                     "RANGING": 1.00, "TRENDING_BEAR": 1.10}
    # RANGING: era 1.10, ora 1.00 - soglia formula 75.9 irraggiungibile, score max realistico 64
    # Con 1.00: soglia RANGING+ALTA = 60 × 1.00 × 1.05 = 63.0 (raggiungibile)
    VOL_FACTOR    = {"BASSA": 0.90, "MEDIA": 1.0, "ALTA": 1.00}
    # ALTA: era 1.05, ora 1.00 - phantom SCORE_INSUFF WR 65% R/R 2.04, profittevoli
    # Soglia RANGING+ALTA: 60 × 1.00 × 1.00 = 60.0 (trade score 58-63 passano)
    SOGLIA_MIN    = 44    # Abbassato da 48 — calibrato sulla sessione 3 aprile WR 58.5%
    SOGLIA_MAX    = 80    # era 90 - phantom SCORE_INSUFFICIENTE dice -$3871, troppo alto in RANGING

    # -- SIZE CONTINUA -----------------------------------------------------
    SIZE_MIN = 0.5
    SIZE_MAX = 2.0

    # -- DRIFT VETO -----------------------------------------------------
    DRIFT_VETO_THRESHOLD = -0.20   # era -0.10 - phantom WR 81% bloccati, sta bloccando i migliori

    # -- WARMUP ---------------------------------------------------------
    WARMUP_TICKS = 50    # tick minimi prima di operare (era 200)

    def __init__(self):
        self._recent_results = deque(maxlen=20)
        self._tick_count = 0   # conta tick dal boot
        self._direction = "LONG"  # LONG o SHORT - il bridge decide
        self._direction_last_change = 0       # timestamp ultimo flip
        self._direction_bearish_streak = 0    # tick consecutivi bearish >=2
        # -- PRE-BREAKOUT DETECTOR -----------------------------------------
        self._prices_short = deque(maxlen=50)     # ultimi 50 prezzi per compressione
        self._seed_history = deque(maxlen=10)     # ultimi 10 seed per derivata
        self._volumes_short = deque(maxlen=50)    # ultimi 50 volumi per accelerazione
        # -- DRIFT DETECTOR ------------------------------------------------
        self._prices_long = deque(maxlen=100)     # ultimi 100 prezzi per drift
        # -- RSI + MACD CONSIGLIERI ----------------------------------------
        self._prices_ta = deque(maxlen=50)        # buffer prezzi CAMPIONATI per indicatori tecnici
        self._ta_tick_counter = 0                  # conta tick per campionamento
        self._ta_sample_rate = 1                   # warmup: campiona ogni tick
        self._rsi_period = 14                     # RSI standard 14 periodi
        self._macd_fast = 12                      # MACD EMA veloce
        self._macd_slow = 26                      # MACD EMA lenta
        self._macd_signal = 9                     # MACD signal line
        self._last_rsi = 50.0                     # RSI corrente
        self._last_macd = 0.0                     # MACD line corrente
        self._last_macd_signal = 0.0              # MACD signal corrente
        self._last_macd_hist = 0.0                # MACD histogram
        # -- PREBREAKOUT AUTO-TUNING (META-REGOLA) -------------------------
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

        # -- CAMPIONA per RSI/MACD ogni 50 tick ------------------------
        # I tick sono troppo veloci - RSI su tick-by-tick va a 100/0.
        # Campionando ogni 50 tick creiamo "candele" virtuali stabili.
        self._ta_tick_counter += 1
        # Sample rate dinamico: 1 durante warmup, 5 a regime
        _sample_now = 1 if len(self._prices_ta) < self._rsi_period else 5
        if self._ta_tick_counter >= _sample_now:
            self._ta_tick_counter = 0
            self._prices_ta.append(price)
            if len(self._prices_ta) >= 30:
                self._update_rsi()
                self._update_macd()

    def score_now(self, seed_score: float, fingerprint_wr: float,
                  momentum: str, volatility: str, trend: str,
                  regime: str, direction: str = "LONG") -> dict:
        """
        Calcola score e soglia ORA senza decidere nulla.
        Nessun veto, nessun effetto collaterale — pura osservazione.
        Chiamato ogni tick per il SignalTracker.
        """
        if self._tick_count < self.WARMUP_TICKS:
            return {'score': 0, 'soglia': 60, 'valid': False}

        W = {"seed":25,"fp":20,"mom":12,"trend":12,"vol":8,"regime":3,"rsi":10,"mac":10}
        MOM_L  = {"FORTE":1.0,"MEDIO":0.67,"DEBOLE":0.20}
        MOM_S  = {"FORTE":0.20,"MEDIO":0.67,"DEBOLE":1.0}
        TRD_L  = {"UP":1.0,"SIDEWAYS":0.47,"DOWN":0.0}
        TRD_S  = {"UP":0.0,"SIDEWAYS":0.47,"DOWN":1.0}
        REG_L  = {"TRENDING_BULL":1.0,"EXPLOSIVE":0.80,"RANGING":0.20,"TRENDING_BEAR":0.0}
        REG_S  = {"TRENDING_BULL":0.0,"EXPLOSIVE":0.80,"RANGING":0.20,"TRENDING_BEAR":1.0}
        VOL_S  = {"BASSA":1.0,"MEDIA":0.60,"ALTA":0.20}
        REG_F  = {"TRENDING_BULL":0.80,"EXPLOSIVE":0.85,"RANGING":1.00,"TRENDING_BEAR":1.10}

        if direction == "SHORT":
            s_mom = MOM_S.get(momentum,0.5)*W["mom"]
            s_trd = TRD_S.get(trend,0.5)*W["trend"]
            s_reg = REG_S.get(regime,0.2)*W["regime"]
        else:
            s_mom = MOM_L.get(momentum,0.5)*W["mom"]
            s_trd = TRD_L.get(trend,0.5)*W["trend"]
            s_reg = REG_L.get(regime,0.2)*W["regime"]

        s_seed = min(1.0,max(0.0,(seed_score-0.20)/0.60))*W["seed"]
        s_fp   = min(1.0,max(0.0,(fingerprint_wr-0.30)/0.50))*W["fp"]
        s_vol  = VOL_S.get(volatility,0.5)*W["vol"]
        s_rsi  = self._rsi_score()*W["rsi"]
        s_macd = self._macd_score()*W["mac"]
        score  = s_seed+s_fp+s_mom+s_trd+s_vol+s_reg+s_rsi+s_macd

        # Score max per context_ratio
        sm = (W["seed"]+W["fp"]+
              (MOM_S if direction=="SHORT" else MOM_L).get(momentum,0.5)*W["mom"]+
              (TRD_S if direction=="SHORT" else TRD_L).get(trend,0.5)*W["trend"]+
              VOL_S.get(volatility,0.5)*W["vol"]+
              (REG_S if direction=="SHORT" else REG_L).get(regime,0.2)*W["regime"]+
              W["rsi"]+W["mac"])
        ctx   = sm/100.0
        rf    = REG_F.get(regime,1.0)
        soglia_raw = 60*ctx*rf
        soglia = max(max(48,58*ctx), min(80,soglia_raw))

        return {
            'score':  round(score,1),
            'soglia': round(soglia,1),
            'valid':  True,
            'ctx':    round(ctx,2),
        }

    def evaluate(self, seed_score, fingerprint_wr, momentum, volatility,
                 trend, regime, matrimonio_name, divorzio_set,
                 fantasma_info, loss_consecutivi, direction="LONG", **kwargs) -> dict:
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
        # -- VETI ASSOLUTI — ora gestiti da CapsuleManager ----------------
        # Se CapsuleManager disponibile: i veti sono nel DB, asset-aware,
        # modificabili da dashboard senza deploy.
        # Se non disponibile: fallback ai VETI_LONG/SHORT hardcodati.
        combo = (momentum, volatility, trend)
        _bot = getattr(self, '_bot_ref', None)
        _cm  = getattr(_bot, 'capsule_manager', None) if _bot else None
        if _cm is not None:
            _bot_oi_carica = getattr(getattr(self, '_bot_ref', None), '_oi_carica', 0.0)
            _bot_oi_stato  = getattr(getattr(self, '_bot_ref', None), '_oi_stato',  'ATTESA')
            _veto_ctx = {
                'momentum':   momentum,
                'volatility': volatility,
                'trend':      trend,
                'direction':  self._direction,
                'regime':     getattr(self, '_regime_current', ''),
                'drift_pct':  getattr(self, '_last_drift', 0.0),
                'oi_carica':  _bot_oi_carica,
                'oi_stato':   _bot_oi_stato,
            }
            _cm_result = _cm.valuta(_veto_ctx)
            if _cm_result.get('blocca'):
                return self._veto(_cm_result.get('reason', f"CM_TOSSICO_{self._direction}_{momentum}_{volatility}_{trend}"))
        else:
            # Fallback hardcodato
            veti = self.VETI_SHORT if self._direction == "SHORT" else self.VETI_LONG
            if combo in veti:
                return self._veto(f"TOSSICO_{self._direction}_{momentum}_{volatility}_{trend}")

        if matrimonio_name in divorzio_set:
            return self._veto("DIVORZIO_PERMANENTE")

        is_fantasma, fantasma_reason = fantasma_info
        if is_fantasma:
            # Solo se evidenza forte - non blocchiamo su 5 campioni
            # Il campo gia penalizza fingerprint_wr basso nel punteggio
            fp_samples = fantasma_reason  # passato come samples count
            if isinstance(fp_samples, str):
                # fantasma_info ritorna (bool, str_reason) - usiamo l'info dell'oracolo
                pass  # non è un veto forte, il punteggio basso basta

        if loss_consecutivi >= self.MAX_LOSS_CONSECUTIVI:
            # Soglia sale, non veto assoluto. Trade forti passano ancora.
            pass  # gestito sotto nel calcolo soglia come loss_f

        # -- WARMUP INTELLIGENTE - la volpe non entra cieca ------------
        # Non basta contare i tick. Ogni senso deve essere attivo:
        #   - Tick >= 200 (buffer base)
        #   - prices_long >= 100 (drift affidabile)
        #   - prices_ta >= 35 (RSI=14 periodi + MACD=26+9=35 periodi)
        # ~6 minuti di warmup - la volpe annusa, guarda, ascolta.
        warmup_checks = []
        if self._tick_count < self.WARMUP_TICKS:
            warmup_checks.append(f"tick={self._tick_count}/{self.WARMUP_TICKS}")
        if len(self._prices_long) < 50:
            warmup_checks.append(f"drift={len(self._prices_long)}/100")
        if len(self._prices_ta) < 20:
            warmup_checks.append(f"RSI_MACD={len(self._prices_ta)}/20")
        if warmup_checks:
            return self._veto(f"WARMUP_{'|'.join(warmup_checks)}")

        # -- DRIFT VETO CONTESTUALE: dipende dal regime, non fisso --------
        # RANGING: oscillazione normale, soglia larga (-0.30%)
        # TRENDING_BULL/BEAR: segnale vero, soglia stretta (-0.10%)
        # EXPLOSIVE: movimento rapido, soglia media (-0.18%)
        if len(self._prices_long) >= 100:
            _prices = list(self._prices_long)
            _avg_old = sum(_prices[:50]) / 50
            _avg_new = sum(_prices[-50:]) / 50
            _drift = (_avg_new - _avg_old) / _avg_old * 100
            _drift_thr = {"RANGING":-0.30,"TRENDING_BULL":-0.10,
                          "TRENDING_BEAR":-0.10,"EXPLOSIVE":-0.18}.get(regime,-0.20)
            if self._direction == "LONG" and _drift < _drift_thr:
                return self._veto(f"DRIFT_VETO_LONG_{_drift:+.3f}%(thr={_drift_thr})")
            elif self._direction == "SHORT" and _drift > abs(_drift_thr):
                return self._veto(f"DRIFT_VETO_SHORT_{_drift:+.3f}%(thr={_drift_thr})")

        # -- CALCOLO PUNTEGGIO CAMPO ---------------------------------------
        # Seed: normalizza [0.3, 1.0] → [0, 1]
        # Normalizzazione calibrata sui valori reali di produzione [0.20, 0.80]
        # I valori teorici [0.30, 1.0] escludevano quasi tutti i segnali reali
        s_seed = min(1.0, max(0.0, (seed_score - 0.20) / 0.60)) * self.W_SEED

        # Fingerprint WR: normalizza [0.30, 0.80] → [0, 1]
        s_fp = min(1.0, max(0.0, (fingerprint_wr - 0.30) / 0.50)) * self.W_FINGERPRINT
        self._last_fp_score = round(s_fp, 2)  # cached per heartbeat

        # Dimensioni categoriche - INVERTITE per SHORT
        if self._direction == "SHORT":
            s_mom   = self.MOMENTUM_SCORE_SHORT.get(momentum, 0.5)  * self.W_MOMENTUM
            s_trend = self.TREND_SCORE_SHORT.get(trend, 0.5)         * self.W_TREND
            s_reg   = self.REGIME_SCORE_SHORT.get(regime, 0.2)        * self.W_REGIME
        else:
            s_mom   = self.MOMENTUM_SCORE_LONG.get(momentum, 0.5)   * self.W_MOMENTUM
            s_trend = self.TREND_SCORE_LONG.get(trend, 0.5)          * self.W_TREND
            s_reg   = self.REGIME_SCORE_LONG.get(regime, 0.2)         * self.W_REGIME
        s_vol   = self.VOL_SCORE.get(volatility, 0.5)                * self.W_VOLATILITY

        # -- CONSIGLIERI TECNICI - invertiti per SHORT --------------------
        s_rsi   = self._rsi_score()                          * self.W_RSI
        s_macd  = self._macd_score()                         * self.W_MACD

        score = s_seed + s_fp + s_mom + s_trend + s_vol + s_reg + s_rsi + s_macd

        # -- SOGLIA PROPORZIONALE AL CONTESTO -----------------------------
        # La soglia scala con lo score MASSIMO raggiungibile nel contesto.
        # In TRENDING_BULL+BASSA+UP: score_max=100, soglia=60 → chiedi 60%
        # In RANGING+ALTA+SIDEWAYS:  score_max=65,  soglia=39 → chiedi 60%
        # -----------------------------------------------------------------
        if self._direction == "SHORT":
            _ctx_mom   = self.MOMENTUM_SCORE_SHORT.get(momentum, 0.5)
            _ctx_trend = self.TREND_SCORE_SHORT.get(trend, 0.5)
            _ctx_reg   = self.REGIME_SCORE_SHORT.get(regime, 0.2)
        else:
            _ctx_mom   = self.MOMENTUM_SCORE_LONG.get(momentum, 0.5)
            _ctx_trend = self.TREND_SCORE_LONG.get(trend, 0.5)
            _ctx_reg   = self.REGIME_SCORE_LONG.get(regime, 0.2)
        _ctx_vol = self.VOL_SCORE.get(volatility, 0.5)

        score_max = (1.0 * self.W_SEED + 1.0 * self.W_FINGERPRINT +
                     _ctx_mom * self.W_MOMENTUM + _ctx_trend * self.W_TREND +
                     _ctx_vol * self.W_VOLATILITY + _ctx_reg * self.W_REGIME +
                     1.0 * self.W_RSI + 1.0 * self.W_MACD)
        context_ratio = score_max / 100.0

        regime_f  = self.REGIME_FACTOR.get(regime, 1.0)
        vol_f     = self.VOL_FACTOR.get(volatility, 1.0)
        history_f = self._history_factor()
        prebreak_f, prebreak_detail, prebreak_signals = self._pre_breakout_factor()
        self._last_regime_for_drift = regime  # passa il regime al drift_factor
        drift_f, drift_detail = self._drift_factor()

        # Loss streak: alza soglia proporzionalmente, non blocca
        if loss_consecutivi >= self.MAX_LOSS_CONSECUTIVI:
            extra = loss_consecutivi - self.MAX_LOSS_CONSECUTIVI + 1
            loss_f = min(1.50, 1.0 + extra * 0.10)
        else:
            loss_f = 1.0

        soglia_raw = self.SOGLIA_BASE * context_ratio * regime_f * vol_f * history_f * prebreak_f * drift_f * loss_f

        SOGLIA_FLOOR_ASSOLUTO = 48
        soglia_min_ctx = max(SOGLIA_FLOOR_ASSOLUTO, self.SOGLIA_MIN * context_ratio)
        
        # SOGLIA_MAX ADATTIVA PER REGIME - non più fissa
        dynamic_max = self._get_dynamic_soglia_max(regime, volatility)
        soglia = max(soglia_min_ctx, min(dynamic_max, soglia_raw))

        # -- BOOST SOGLIA DA CAPSULE L3 (IntelligenzaAutonoma) -------------
        # Le capsule L3 possono alzare la soglia proporzionalmente alla gravità.
        # Il pavimento SOGLIA_FLOOR_ASSOLUTO=48 rimane inviolabile.
        soglia_boost = kwargs.get('soglia_boost', 0.0)
        if soglia_boost > 0:
            soglia = max(soglia_min_ctx, min(dynamic_max, soglia + soglia_boost))

        # -- DECISIONE -----------------------------------------------------
        # Salva score e soglia per heartbeat/grafico
        self._last_score  = score
        self._last_soglia = soglia
        enter = score >= soglia

        # -- SIZE CONTINUA -------------------------------------------------
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
                'soglia_f': f"r={regime_f:.2f} v={vol_f:.2f} h={history_f:.2f} d={drift_f:.2f} ctx={context_ratio:.2f} smax={score_max:.0f} RSI={self._last_rsi:.0f} {prebreak_detail} {drift_detail}".strip(),
            }
        }

    def _get_dynamic_soglia_max(self, regime: str, volatility: str) -> float:
        """
        SOGLIA_MAX ADATTIVA - il regime e il contesto decidono il tetto.
        Usa range_position, drift, volatility per calibrare.
        """
        # Calcola range_position dai prezzi recenti
        range_position = 0.5  # default centro
        if len(self._prices_long) >= 200:
            recent = list(self._prices_long)[-200:]
            r_high = max(recent)
            r_low = min(recent)
            r_size = r_high - r_low
            if r_size > 0:
                range_position = (recent[-1] - r_low) / r_size
        
        # Calcola drift
        drift_pct = 0.0
        if len(self._prices_long) >= 100:
            _p = list(self._prices_long)
            _avg_old = sum(_p[:50]) / 50
            _avg_new = sum(_p[-50:]) / 50
            drift_pct = (_avg_new - _avg_old) / _avg_old * 100
        
        if regime == "RANGING":
            base = 80
            # Centro del range → più selettivo
            if 0.40 <= range_position <= 0.60:
                base = 83
                if volatility == "ALTA":
                    base += 2  # 85 - molto selettivo al centro con alta vol
            # Bordi del range → più permissivo
            elif range_position <= 0.25 or range_position >= 0.75:
                base = 76
                if abs(drift_pct) >= 0.10:
                    base -= 2  # 74 - drift vero al bordo, lascia entrare
        
        elif regime == "TRENDING_BULL":
            base = 70
            if drift_pct > 0.10:
                base = 66  # trend confermato, più permissivo
            if volatility == "BASSA":
                base -= 2  # trend pulito, ancora più permissivo
        
        elif regime == "TRENDING_BEAR":
            base = 75
            if drift_pct < -0.10:
                base = 72  # trend bear confermato
        
        elif regime == "EXPLOSIVE":
            base = 75   # EXPLOSIVE rischiosa — serve segnale forte
            if volatility == "ALTA":
                base = 72  # ancora alta — esplosione vera ma volatilità aumenta rischio
        
        else:
            base = 80
        
        return float(base)

    def record_result(self, is_win: bool, exit_reason: str = "", pb_signals: int = 0, pnl: float = 0.0):
        """Chiamato alla chiusura di ogni shadow trade."""
        self._recent_results.append(is_win)

        # -- META-REGOLA: PREBREAKOUT AUTO-TUNING -------------------------
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
        """Soglia sale dopo loss streak ma decade nel tempo (5 min).
        Il pugile alza le braccia ma le riabbassa se non arrivano pugni."""
        if len(self._recent_results) < 5:
            return 1.0
        recent_wr = sum(1 for r in self._recent_results if r) / len(self._recent_results)
        if recent_wr < 0.40:
            if not hasattr(self, '_history_factor_since'):
                self._history_factor_since = time.time()
            elapsed = time.time() - self._history_factor_since
            decay = max(0.0, 1.0 - elapsed / 300.0)
            return 1.0 + (0.20 * decay)
        if hasattr(self, '_history_factor_since'):
            del self._history_factor_since
        return 1.0

    def _pre_breakout_factor(self) -> tuple:
        """
        ★ PRE-BREAKOUT DETECTOR - il cecchino sente i passi.

        Tre segnali indipendenti:
          1. COMPRESSIONE: range stretto (< 0.02%) con volatilita storica alta
          2. VOLUME CRESCENTE: vol_accel > 1.3 a prezzo fermo
          3. SEED CRESCENTI: derivata positiva per 5+ tick consecutivi

        Ogni segnale vale 0.0-1.0. Il fattore finale è:
          3 segnali attivi → 0.70 (soglia scende del 30%) ← UNICO CHE FUNZIONA
          2 segnali attivi → 0.96 (quasi invariata - dati dicono che perde)
          1 segnale attivo → 1.00 (nessun effetto)
          0 segnali        → 1.00 (nessun effetto)

        Ritorna (factor: float, dettaglio: str)
        """
        if len(self._prices_short) < 30 or len(self._seed_history) < 5:
            return 1.0, "", 0

        signals = 0
        details = []

        # -- 1. COMPRESSIONE -----------------------------------------------
        prices = list(self._prices_short)
        recent_50 = prices[-50:] if len(prices) >= 50 else prices
        p_max = max(recent_50)
        p_min = min(recent_50)
        p_mid = (p_max + p_min) / 2
        compression = (p_max - p_min) / p_mid if p_mid > 0 else 1.0

        if compression < self._pb3_compression_threshold:   # ADATTIVO - si stringe se pb3 WR < 50%
            signals += 1
            details.append(f"COMPRESS={compression:.5f}")

        # -- 2. VOLUME CRESCENTE (a prezzo fermo) -------------------------
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

        # -- 3. SEED DIREZIONALI (derivata positiva per LONG, negativa per SHORT) --
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

        # -- CALCOLA FATTORE -----------------------------------------------
        # CALIBRATO SU DATI REALI (6 trade shadow 16/03/2026):
        #   3/3 segnali: 2 WIN +$27.29, 1 LOSS -$1.28 → WR 66%, R/R 21:1
        #   2/3 segnali: 0 WIN, 3 LOSS -$0.78 → WR 0% → QUASI DISABILITATO
        #   Solo il 3/3 pieno abbassa significativamente la soglia.
        if signals >= 3:
            factor = 0.92    # segnala ma NON crea buchi - i LOSS SMORZ a soglia bassa sono la prova
        elif signals >= 2:
            factor = 0.96    # quasi nessun effetto - 2/3 perde troppo
        elif signals >= 1:
            factor = 1.00    # un segnale = nessun effetto
        else:
            factor = 1.00    # niente - soglia invariata

        detail_str = f"pb={factor:.2f}({signals}/3 {'+'.join(details)})" if signals > 0 else ""
        return factor, detail_str, signals

    def _drift_factor(self) -> tuple:
        """
        ★ DRIFT DETECTOR - in che direzione soffia il vento?
        
        RANGING: il drift oscilla costantemente ±0.05%. Non è un segnale,
        è rumore. Il fattore è DIMEZZATO per non bloccare trade buoni.
        
        TRENDING: il drift è un segnale reale. Fattore pieno.
        """
        if len(self._prices_long) < 50:
            return 1.0, ""

        prices = list(self._prices_long)
        avg_old    = sum(prices[:50]) / 50
        avg_recent = sum(prices[-50:]) / 50

        if avg_old == 0:
            return 1.0, ""

        drift_pct = (avg_recent - avg_old) / avg_old * 100

        if drift_pct < -0.10:
            factor = 1.30
            detail = f"DRIFT={drift_pct:+.3f}%↓↓"
        elif drift_pct < -0.03:
            factor = 1.15
            detail = f"DRIFT={drift_pct:+.3f}%↓"
        elif drift_pct > 0.05:
            factor = 0.95
            detail = f"DRIFT={drift_pct:+.3f}%↑"
        else:
            return 1.0, ""

        # In RANGING il drift è rumore - dimezza l'effetto
        # factor 1.15 → 1.075, factor 1.30 → 1.15
        # Così un drift -0.04% non alza la soglia da 49 a 56 ma solo a 53
        if hasattr(self, '_last_regime_for_drift'):
            regime = self._last_regime_for_drift
        else:
            regime = "RANGING"
        if regime == "RANGING":
            factor = 1.0 + (factor - 1.0) * 0.5
            detail += " (R½)"

        return factor, detail

    # -- RSI + MACD: I CONSIGLIERI ----------------------------------------

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
        ★ RSI CONSIGLIERE - ipervenduto o ipercomprato?
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
        ★ MACD CONSIGLIERE - il trend sta nascendo o morendo?
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


# ===========================================================================
# ★★★ BOT PRINCIPALE - OVERTOP BASSANO V14 PRODUCTION ★★★
# ===========================================================================


class VeritatisTracker:
    """
    Tracker della Verità — confronta in tempo reale chi aveva ragione.
    
    Ogni volta che l'Oracolo dice FUOCO registra il momento.
    Dopo 30/60 secondi misura dove è andato il prezzo.
    Confronta con cosa diceva il SuperCervello nello stesso istante.
    
    Non aspetta trade confermati — usa delta_30/60s come verità.
    Dopo 50 segnali sa chi aveva ragione e di quanto.
    """
    
    def __init__(self, sc_ref=None):
        self._open   = []   # segnali aperti in attesa di conferma
        self._closed = []   # segnali chiusi con verità nota
        self._sc_ref = sc_ref  # SuperCervello per calibrazione automatica
        self._stats  = {    # statistiche per combinazione
            # chiave: f"{oi_stato}|{sc_decisione}"
            # es: "FUOCO|BLOCCA" o "FUOCO|ENTRA" o "CARICA|BLOCCA"
        }
    
    def registra(self, price: float, oi_stato: str, oi_carica: float,
                 sc_decisione: str, sc_confidenza: float,
                 regime: str, ts: float):
        """Registra un segnale al momento della decisione."""
        self._open.append({
            'price':        price,
            'oi_stato':     oi_stato,
            'oi_carica':    round(oi_carica, 3),
            'sc_decisione': sc_decisione,
            'sc_conf':      round(sc_confidenza, 3),
            'regime':       regime,
            'ts':           ts,
            'chiave':       f"{oi_stato}|{sc_decisione}",
        })
    
    def aggiorna(self, price_now: float, ts_now: float):
        """
        Ogni tick aggiorna i segnali aperti.
        Chiude quelli con 30/60/120 secondi trascorsi.
        """
        ancora_aperti = []
        for sig in self._open:
            elapsed = ts_now - sig['ts']
            delta   = price_now - sig['price']
            # Hit vero: il delta deve coprire le fee reali
            # $1000 margine × 5x leva = $5000 esposti
            # Fee: $5000 × 0.02% × 2 lati = $2.00
            pnl_sim = delta * (5000.0 / sig['price'])  # lordo — fee al close
            hit     = delta > 0  # direzione corretta
            
            if elapsed >= 60:
                # Chiudi con verità a 60 secondi
                sig['delta_60'] = round(delta, 2)
                sig['hit_60']   = hit
                sig['pnl_60']   = round(pnl_sim, 2)
                sig['elapsed']  = round(elapsed, 1)
                self._closed.append(sig)
                if len(self._closed) > 500:
                    self._closed.pop(0)
                # Aggiorna statistiche
                self._aggiorna_stats(sig)
            else:
                ancora_aperti.append(sig)
        
        self._open = ancora_aperti
    
    def _aggiorna_stats(self, sig: dict):
        """Aggiorna statistiche per chiave oi_stato|sc_decisione."""
        k = sig['chiave']
        if k not in self._stats:
            self._stats[k] = {
                'n': 0, 'hits': 0, 'pnl': 0.0,
                'deltas': [], 'oi_carica_avg': 0.0,
            }
        s = self._stats[k]
        s['n']    += 1
        s['hits'] += sig['hit_60']
        s['pnl']  += sig['pnl_60']
        s['deltas'].append(sig['delta_60'])
        if len(s['deltas']) > 100: s['deltas'].pop(0)
        # Media carica
        s['oi_carica_avg'] = round(
            (s['oi_carica_avg'] * (s['n']-1) + sig['oi_carica']) / s['n'], 3)
        # Calibra SC automaticamente dal verdetto
        if self._sc_ref and s['n'] >= 10:
            self._calibra_sc(sig, s)

    def _calibra_sc(self, sig: dict, stats: dict):
        """
        Ogni volta che il Veritas chiude un segnale con n>=10,
        aggiusta i pesi del SuperCervello in base al verdetto reale.
        
        Logica:
        - Oracolo FUOCO con SC BLOCCA e hit_rate >= 0.60 → SC era sbagliato
          → aumenta peso campo_carica (organo dell'Oracolo)
          → riduci peso signal_tracker (troppo conservativo)
        - Oracolo FUOCO con SC BLOCCA e hit_rate <= 0.40 → SC aveva ragione
          → aumenta peso signal_tracker
          → riduci peso campo_carica
        - FUOCO_SHORT con BLOCCA e hit_rate >= 0.60 → SHORT bloccato era giusto
          → aumenta campo_carica per SHORT
        """
        if not self._sc_ref:
            return
            
        hit_rate = stats['hits'] / stats['n']
        chiave   = sig['chiave']
        pesi     = self._sc_ref._pesi
        STEP     = 0.008  # step piccolo — cambiamento graduale
        
        try:
            if 'FUOCO' in chiave and 'BLOCCA' in chiave:
                if hit_rate >= 0.60:
                    # Oracolo aveva ragione — SC bloccava
                    # campo_carica sale più velocemente fino a max 0.60
                    pesi['campo_carica']   = min(0.60, pesi['campo_carica']   + STEP * 2)
                    pesi['signal_tracker'] = max(0.05, pesi['signal_tracker'] - STEP)
                    pesi['oracolo_fp']     = max(0.05, pesi['oracolo_fp']     - STEP/2)
                    pesi['matrimonio']     = max(0.05, pesi['matrimonio']     - STEP/2)
                elif hit_rate <= 0.40:
                    # SC aveva ragione a bloccare
                    pesi['signal_tracker'] = min(0.45, pesi['signal_tracker'] + STEP)
                    pesi['campo_carica']   = max(0.05, pesi['campo_carica']   - STEP)

            elif 'FUOCO' in chiave and 'ENTRA' in chiave:
                if hit_rate >= 0.60:
                    # Oracolo + SC concordavano e avevano ragione
                    pesi['campo_carica'] = min(0.60, pesi['campo_carica'] + STEP)
                    pesi['oracolo_fp']   = min(0.40, pesi['oracolo_fp']   + STEP/2)
                elif hit_rate <= 0.40:
                    pesi['campo_carica'] = max(0.05, pesi['campo_carica'] - STEP)

            # Pavimento/soffitto pesi — campo_carica non scende mai sotto 30%
            # signal_tracker non sale mai sopra 25% — non deve dominare
            pesi['campo_carica']   = max(0.30, pesi['campo_carica'])
            pesi['signal_tracker'] = min(0.25, pesi['signal_tracker'])
            pesi['oracolo_fp']     = max(0.15, pesi['oracolo_fp'])
            # Rinormalizza sempre a somma 1.0
            tot = sum(pesi.values())
            for k in pesi:
                pesi[k] = round(pesi[k] / tot, 4)
                
        except Exception:
            pass  # mai crashare per calibrazione
    
    def verdetto(self) -> dict:
        """
        Calcola il verdetto finale:
        - Chi aveva ragione: Oracolo o SuperCervello?
        - Quanto valore ha bloccato il SC?
        - Quanto ha protetto?
        """
        risultati = {}
        for k, s in self._stats.items():
            if s['n'] < 3:
                continue
            hit_rate = s['hits'] / s['n']
            pnl_avg  = s['pnl'] / s['n']
            risultati[k] = {
                'n':         s['n'],
                'hit_rate':  round(hit_rate, 3),
                'pnl_avg':   round(pnl_avg, 2),
                'pnl_tot':   round(s['pnl'], 2),
                'carica_avg':s['oi_carica_avg'],
                'verdetto':  'GIUSTO' if pnl_avg > 0 else 'SBAGLIATO',
            }
        
        # Calcola chi aveva ragione nei conflitti
        fuoco_entra = risultati.get('FUOCO|ENTRA', {})
        fuoco_blocca = risultati.get('FUOCO|BLOCCA', {})
        
        conflitto = {}
        if fuoco_entra and fuoco_blocca:
            pnl_entra  = fuoco_entra.get('pnl_avg', 0)
            pnl_blocca = fuoco_blocca.get('pnl_avg', 0)
            # Se bloccare aveva PnL positivo → SC aveva ragione
            # Se bloccare aveva PnL negativo → Oracolo aveva ragione
            if pnl_blocca > 0:
                conflitto['chi_aveva_ragione'] = 'SC'
                conflitto['pnl_perso_bloccando'] = fuoco_blocca.get('pnl_tot', 0)
            else:
                conflitto['chi_aveva_ragione'] = 'ORACOLO'
                conflitto['pnl_salvato_bloccando'] = abs(fuoco_blocca.get('pnl_tot', 0))
        
        return {'stats': risultati, 'conflitto': conflitto, 'n_closed': len(self._closed)}
    
    def dump_dashboard(self) -> dict:
        """Dati per la dashboard."""
        v = self.verdetto()
        rows = []
        for k, s in v['stats'].items():
            oi, sc = k.split('|')
            rows.append({
                'chiave':   k,
                'oi':       oi,
                'sc':       sc,
                'n':        s['n'],
                'hit_rate': s['hit_rate'],
                'pnl_avg':  s['pnl_avg'],
                'pnl_tot':  s['pnl_tot'],
                'verdetto': s['verdetto'],
            })
        rows.sort(key=lambda x: -x['n'])
        return {
            'rows':      rows,
            'conflitto': v['conflitto'],
            'n_closed':  v['n_closed'],
            'n_open':    len(self._open),
        }

    def save(self, db_path: str):
        """Persiste _stats e _closed su SQLite — sopravvive al restart."""
        try:
            import sqlite3, json
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS veritas_stats
                         (chiave TEXT PRIMARY KEY, data TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS veritas_closed
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT)""")
            # Salva stats
            for k, s in self._stats.items():
                c.execute("INSERT OR REPLACE INTO veritas_stats VALUES (?,?)",
                          (k, json.dumps(s)))
            # Salva ultimi 200 closed (non duplicare)
            c.execute("DELETE FROM veritas_closed")
            for sig in self._closed[-200:]:
                c.execute("INSERT INTO veritas_closed (data) VALUES (?)",
                          (json.dumps(sig),))
            conn.commit(); conn.close()
        except Exception as e:
            log.error(f"[VERITAS_SAVE] {e}")

    def load(self, db_path: str):
        """Carica _stats e _closed da SQLite al boot."""
        try:
            import sqlite3, json, os
            if not os.path.exists(db_path): return
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            # Stats
            try:
                for row in c.execute("SELECT chiave, data FROM veritas_stats"):
                    self._stats[row[0]] = json.loads(row[1])
            except: pass
            # Closed
            try:
                for row in c.execute("SELECT data FROM veritas_closed ORDER BY id"):
                    self._closed.append(json.loads(row[0]))
            except: pass
            conn.close()
            if self._stats:
                log.info(f"[VERITAS_LOAD] Caricati {len(self._stats)} stats, {len(self._closed)} closed")
        except Exception as e:
            log.error(f"[VERITAS_LOAD] {e}")




# ── OPS per valutazioni capsule ───────────────────────────────────────────
import operator
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

def _calcola_soglia_da_signal_tracker(bot) -> dict:
    """Calcola soglia ottimale dai dati reali del Signal Tracker."""
    try:
        if not hasattr(bot, 'signal_tracker'):
            return {'base': 52, 'min': 48, 'motivo': 'NO_TRACKER'}
        stats = getattr(bot.signal_tracker, '_stats', {})
        if not stats:
            return {'base': 52, 'min': 48, 'motivo': 'NO_DATA'}
        contesti = []
        for ctx, s in stats.items():
            hits = s.get('hit_60', [])
            pnls = s.get('pnl_sim', [])
            n = len(hits)
            if n < 20:
                continue
            hit_rate = sum(hits) / n
            pnl_avg  = sum(pnls) / len(pnls) if pnls else 0
            contesti.append({'ctx': ctx, 'n': n, 'hit_rate': hit_rate, 'pnl_avg': pnl_avg})
        if not contesti:
            return {'base': 52, 'min': 48, 'motivo': 'POCHI_DATI'}
        tot_n   = sum(c['n'] for c in contesti)
        avg_hit = sum(c['hit_rate'] * c['n'] for c in contesti) / tot_n
        avg_pnl = sum(c['pnl_avg']  * c['n'] for c in contesti) / tot_n
        if avg_hit >= 0.65 and avg_pnl > 0:
            return {'base': 46, 'min': 42, 'motivo': f"OTTIMO hit={avg_hit:.0%}"}
        elif avg_hit >= 0.60 and avg_pnl > 0:
            return {'base': 48, 'min': 44, 'motivo': f"BUONO hit={avg_hit:.0%}"}
        elif avg_hit >= 0.55:
            return {'base': 50, 'min': 46, 'motivo': f"DISCRETO hit={avg_hit:.0%}"}
        else:
            return {'base': 52, 'min': 48, 'motivo': f"STANDARD hit={avg_hit:.0%}"}
    except Exception as e:
        return {'base': 52, 'min': 48, 'motivo': f'ERRORE: {e}'}


# ═══════════════════════════════════════════════════════════════════════════
# ★ OVERTOP BASSANO V16 — MOTORE PRINCIPALE
#   Un solo motore. Una sola catena decisionale. Memoria persistente.
# ═══════════════════════════════════════════════════════════════════════════

class OvertopBassanoV16Production:
    """
    Bot BTC/USDC su Binance WebSocket.

    Catena entry:
      tick → RegimeDetector → ContestoAnalyzer → SeedScorer →
      OracoloDinamico → CampoGravitazionale(score>soglia) →
      CapsuleMemory.check() → entry

    Catena exit:
      4 trigger divorzio + SMORZ (energia morta) + timeout adattivo

    Catena apprendimento:
      close → OracleClassifier(EVITA|MIGLIORA) → CapsuleMemory.record()
      OracoloDinamico.update_wr()
      MemoriaMatrimoni.record_trade() → persistita su DB
    """

    def __init__(self, heartbeat_data=None, db_execute=None, heartbeat_lock=None):
        self.symbol      = SYMBOL
        self.paper_trade = PAPER_TRADE
        self.heartbeat_data = heartbeat_data if heartbeat_data is not None else {}
        self.heartbeat_lock = heartbeat_lock
        self.db_execute     = db_execute

        # -- Legge fondamentale PnL USDC ----------------------------------
        self.TRADE_SIZE_USD = TRADE_SIZE_USD
        self.LEVERAGE       = LEVERAGE
        self.EXPOSURE       = EXPOSURE
        self.FEE_TRADE      = FEE_TRADE
        self.STOP_LIVE      = STOP_LIVE_USD
        self.wins    = 0
        self.losses  = 0

        # -- Persistenza --------------------------------------------------
        self._persist = PersistenzaStato(db_path=DB_PATH)
        self.capital, self.total_trades = self._persist.load()

        # -- Componenti core ----------------------------------------------
        self.analyzer       = ContestoAnalyzer(window=50)
        self.seed_scorer    = SeedScorer(window=50)
        self.oracolo        = OracoloDinamico()
        self.memoria        = MemoriaMatrimoni()
        self.regime_det     = RegimeDetector()
        self.decelero       = MomentumDecelerometer()
        self.position_sizer = PositionSizer()
        self.calibratore    = AutoCalibratore()
        self.signal_tracker = PreTradeSignalTracker()
        self.veritas        = VeritatisTracker()
        self.telemetry      = StabilityTelemetry()
        self.log_analyzer   = LogAnalyzer()
        self.campo          = CampoGravitazionale()
        self.campo._bot_ref = self

        # -- V16: memoria unica persistente --------------------------------
        self.capsule_memory  = CapsuleMemory(db_path=DB_PATH, asset=SYMBOL)
        self.oracle_clf      = OracleClassifier()

        # -- V16 engines (opzionali) ----------------------------------------
        if _V16_ENGINES_OK:
            try:
                self._comparto = CompartoEngine()
                self._nerv     = NervosismoEngine()
                self._breath   = BreathEngine()
            except Exception:
                self._comparto = self._nerv = self._breath = None
        else:
            self._comparto = self._nerv = self._breath = None

        # -- CapsuleManager legacy (per compatibilità dashboard) ------------
        if _CM_AVAILABLE:
            self.capsule_manager = CapsuleManager(db_path=DB_PATH, asset=SYMBOL)
            self.capsule_runtime = self.capsule_manager
        else:
            self.capsule_manager = None
            self.capsule_runtime = None

        # -- OI state -------------------------------------------------------
        self._oi_carica = 0.0
        self._oi_stato  = 'ATTESA'

        # -- Ripristina intelligenza accumulata -----------------------------
        self._persist.load_brain(self.oracolo, self.memoria, self.calibratore)
        self._persist.load_signal_tracker(self.signal_tracker)
        self._persist.load_runtime_state(self)

        # -- Regime --------------------------------------------------------
        self._regime_current = 'RANGING'
        self._regime_conf    = 0.0
        self._last_regime_check = time.time()

        # -- Stato trade ---------------------------------------------------
        self._trade = None          # trade aperto: dict con entry info
        self._trade_max_price = None
        self._trade_min_price = None
        self._trade_entry_time = None
        self._trade_matrimonio = None
        self._trade_entry_momentum  = None
        self._trade_entry_volatility = None
        self._trade_entry_trend     = None
        self._trade_entry_fingerprint = None

        # -- Contatori performance M -----------------------------------------
        self._m_wins   = 0
        self._m_losses = 0
        self._m_pnl    = 0.0
        self._m_trades = 0
        self._m_recent_trades = deque(maxlen=50)
        self._loss_streak = 0

        # -- Timing --------------------------------------------------------
        self.last_heartbeat    = time.time()
        self.last_config_check = time.time()
        self.last_persist      = time.time()
        self.ws                = None
        self._boot_time        = time.time()
        self._tick_count       = 0

        # -- Narratore trade storia (per OracleAuto) -----------------------
        if 'narratore_trade_storia' not in self.heartbeat_data:
            self.heartbeat_data['narratore_trade_storia'] = []

        # Chiavi V15 legacy — inizializzate per compatibilità con app.py dashboard
        _hb_defaults = {
            'trades':                  [],
            'last_seen':               0.0,
            'narrativa_ds':            '',
            'narratore_ultima_capsula': '',
            'trade_analisi':           '',
            'capsule_ragionatore':     [],
            'ds_blocca_sc':            False,
            'ds_blocca_sc_ts':         0.0,
            'ds_forza_entry':          False,
            'ds_forza_ts':             0.0,
            'ds_reset_pesi':           False,
            'ds_soglia_override':      None,
            'ds_soglia_ts':            0.0,
            'oracle_trigger':          '',
            'oracle_log':              [],
            'oracle_last_analysis':    '',
        }
        for k, v in _hb_defaults.items():
            if k not in self.heartbeat_data:
                self.heartbeat_data[k] = v

        log.info(f"[V16] ✅ OvertopBassanoV16Production inizializzato — PAPER={PAPER_TRADE}")
        log.info(f"[V16] 💊 CapsuleMemory attiva — {len(self.capsule_memory._cache)} capsule in cache")

    def _log(self, emoji: str, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        log.info(f"{ts} {emoji} {msg}")

    # ────────────────────────────────────────────────────────────────────
    # PROCESS TICK — cuore del bot
    # ────────────────────────────────────────────────────────────────────

    def _process_tick(self, price: float, volume: float = 1.0):
        now = time.time()
        self._tick_count += 1

        # -- Feed dati ai detector -----------------------------------------
        self.seed_scorer.add_tick(price, volume)
        self.analyzer.add_price(price)
        self.regime_det.add_tick(price, volume)
        self.campo.feed_tick(price, volume, 0.5)
        self.decelero.add_price(price)
        # veritas.registra chiamato su segnali OI — non su ogni tick

        # -- Regime ogni 10s -----------------------------------------------
        if now - self._last_regime_check > 10:
            regime, conf, detail = self.regime_det.detect()
            self._regime_current = regime
            self._regime_conf    = conf
            self._last_regime_check = now

        regime = self._regime_current

        # -- Contesto -----------------------------------------------------
        drift_val = 0.0
        if len(self.campo._prices_long) >= 100:
            _p = list(self.campo._prices_long)
            drift_val = (_p[-1] - _p[0]) / _p[0] * 100

        momentum, volatility, trend = self.analyzer.analyze(
            regime=regime, drift=drift_val
        )
        if not momentum:
            return

        # -- Trade aperto: valuta uscita ----------------------------------
        if self._trade:
            self._evaluate_exit(price, momentum, volatility, trend)
            self._update_heartbeat(price, momentum, volatility, trend)
            return

        # -- Nessun trade aperto: valuta ingresso -------------------------
        self._evaluate_entry(price, volume, momentum, volatility, trend, regime)
        self._update_heartbeat(price, momentum, volatility, trend)

        # -- Persist ogni 5 min -------------------------------------------
        if now - self.last_persist > 300:
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)
            self._persist.save_runtime_state(self)
            self.last_persist = now

    # ────────────────────────────────────────────────────────────────────
    # EVALUATE ENTRY
    # ────────────────────────────────────────────────────────────────────

    def _evaluate_entry(self, price, volume, momentum, volatility, trend, regime):
        """
        Catena entry V16:
        1. Seed score
        2. Fingerprint WR (Oracolo)
        3. Matrimonio
        4. CampoGravitazionale (score vs soglia)
        5. CapsuleMemory (unico gate di blocco)
        → ENTRY
        """
        # 1. Seed
        seed = self.seed_scorer.score()
        if not seed.get('pass'):
            return

        seed_score = seed['score']
        fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend,
                                              self.campo._direction)

        # 2. Oracolo fantasma (solo se evidenza forte)
        is_fantasma, fp_reason = self.oracolo.is_fantasma(
            momentum, volatility, trend, self.campo._direction
        )
        if is_fantasma:
            self._log("👻", f"FANTASMA: {fp_reason} — skip")
            return

        # 3. Matrimonio
        mat_result = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = mat_result.get("name", "NEUTRAL") if mat_result else "NEUTRAL"
        ok, mem_reason = self.memoria.get_status(matrimonio_name)
        if not ok:
            self._log("💔", f"VETO: {mem_reason}")
            return

        # 4. CampoGravitazionale
        result = self.campo.evaluate(
            seed_score=seed_score,
            fingerprint_wr=fingerprint_wr,
            momentum=momentum,
            volatility=volatility,
            trend=trend,
            regime=regime,
            matrimonio_name=matrimonio_name,
            divorzio_set=self.memoria.divorzio,
            fantasma_info=(False, ""),
            loss_consecutivi=self._loss_streak,
            direction=self.campo._direction,
        )

        if result.get('veto'):
            return

        if not result.get('enter'):
            return

        score  = result['score']
        soglia = result['soglia']

        # 5. CapsuleMemory — unico gate di blocco
        cap_check = self.capsule_memory.check(
            direction=self.campo._direction,
            regime=regime,
            momentum=momentum,
            volatility=volatility,
            trend=trend,
            matrimonio=matrimonio_name,
        )
        if cap_check['blocca']:
            self._log("💊", f"[CM16] 🚫 BLOCCO {cap_check['reason']} "
                           f"wr={cap_check['wr']:.0%} samples={cap_check['samples']}")
            return

        # Soglia boost da capsule MIGLIORA
        boost = self.capsule_memory.get_soglia_boost(
            self.campo._direction, regime, momentum, volatility, trend
        )
        if boost > 0 and score < soglia + boost:
            self._log("💊", f"[CM16] SOGLIA_BOOST +{boost:.1f} score={score} < {soglia+boost:.1f}")
            return

        # ── ENTRY CONFERMATA ────────────────────────────────────────────
        size_info = self.position_sizer.calculate(
            seed_score=seed_score,
            fingerprint_wr=fingerprint_wr,
            confidence=self.memoria.get_trust(matrimonio_name),
            regime_mult=self.regime_det.get_multipliers().get('size_mult', 1.0)
        )

        self._open_trade(
            price=price,
            momentum=momentum,
            volatility=volatility,
            trend=trend,
            regime=regime,
            matrimonio_name=matrimonio_name,
            score=score,
            soglia=soglia,
            size=size_info['size_factor'],
            seed_score=seed_score,
            fingerprint_wr=fingerprint_wr,
        )

    def _open_trade(self, price, momentum, volatility, trend, regime,
                    matrimonio_name, score, soglia, size,
                    seed_score=0.5, fingerprint_wr=0.5):
        direction = self.campo._direction
        self._trade = {
            'price_entry':  price,
            'direction':    direction,
            'momentum':     momentum,
            'volatility':   volatility,
            'trend':        trend,
            'regime':       regime,
            'matrimonio':   matrimonio_name,
            'score':        score,
            'soglia':       soglia,
            'size':         size,
            'seed':         seed_score,
            'fp_wr':        fingerprint_wr,
            'ts_entry':     time.time(),
            'duration_avg': 60.0,
        }
        self._trade_max_price      = price
        self._trade_min_price      = price
        self._trade_entry_time     = time.time()
        self._trade_matrimonio     = matrimonio_name
        self._trade_entry_momentum  = momentum
        self._trade_entry_volatility = volatility
        self._trade_entry_trend     = trend
        self._trade_entry_fingerprint = fingerprint_wr

        self._log("🟢", f"ENTRY {direction} @ {price:.2f} | "
                        f"score={score:.1f}/{soglia:.1f} | "
                        f"mat={matrimonio_name} | size={size:.2f}x")

    # ────────────────────────────────────────────────────────────────────
    # EVALUATE EXIT
    # ────────────────────────────────────────────────────────────────────

    def _evaluate_exit(self, price, momentum, volatility, trend):
        if not self._trade:
            return

        entry_direction = self._trade.get('direction', 'LONG')
        entry_price     = self._trade['price_entry']
        entry_time      = self._trade['ts_entry']
        duration        = time.time() - entry_time

        # Update price extremes
        if price > self._trade_max_price:
            self._trade_max_price = price
        if price < self._trade_min_price:
            self._trade_min_price = price

        # PnL lordo corrente (LEGGE FONDAMENTALE)
        btc_qty = self.EXPOSURE / entry_price
        if entry_direction == 'SHORT':
            pnl_lordo = (entry_price - price) * btc_qty
        else:
            pnl_lordo = (price - entry_price) * btc_qty

        # Hard stop
        if pnl_lordo < -self.STOP_LIVE:
            self._close_trade(price, f"HARD_STOP_${abs(pnl_lordo):.1f}", momentum, volatility, trend)
            return

        if duration < 10:
            return

        # Drawdown dal picco
        if entry_direction == 'LONG':
            max_profit = (self._trade_max_price - entry_price) * btc_qty
            retreat    = self._trade_max_price - price
            drawdown_pct = (self._trade_max_price - price) / entry_price * 100
        else:
            max_profit = (entry_price - self._trade_min_price) * btc_qty
            retreat    = price - self._trade_min_price
            drawdown_pct = (price - self._trade_min_price) / entry_price * 100

        # -- 4 DIVORCE TRIGGERS -------------------------------------------
        triggers = []
        if self._trade_entry_volatility == "BASSA" and volatility == "ALTA" and pnl_lordo < 0:
            triggers.append("T1_VOL")
        if entry_direction == "LONG" and self._trade_entry_trend == "UP" and trend == "DOWN":
            triggers.append("T2_TREND")
        elif entry_direction == "SHORT" and self._trade_entry_trend == "DOWN" and trend == "UP":
            triggers.append("T2_TREND")
        if drawdown_pct > DIVORCE_DRAWDOWN_PCT:
            triggers.append("T3_DD")
        current_fp = self.oracolo.get_wr(momentum, volatility, trend, entry_direction)
        entry_fp   = self._trade_entry_fingerprint or 0.0
        if entry_fp > 0:
            fp_div = abs(current_fp - entry_fp) / max(entry_fp, 0.001)
            if fp_div > DIVORCE_FP_DIVERGE and pnl_lordo < 0:
                triggers.append("T4_FP")
        if len(triggers) >= DIVORCE_MIN_TRIGGERS:
            self._close_trade(price, f"DIVORZIO|{'|'.join(triggers)}", momentum, volatility, trend)
            return

        # -- PROFIT LOCK --------------------------------------------------
        if max_profit > 2.0:
            retreat_pct = retreat / (entry_price * btc_qty) * 100 if max_profit > 0 else 0
            pnl_win_avg = abs(self.oracolo.get_pnl_avg(
                self._trade_entry_momentum or momentum,
                self._trade_entry_volatility or volatility,
                self._trade_entry_trend or trend,
                direction=entry_direction
            )) or 5.0
            tolleranza = min(0.70, max(0.40, 0.40 + (pnl_win_avg / 50.0) * 0.30))
            retreat_ratio = retreat / max_profit if max_profit > 0 else 0
            if retreat_ratio > tolleranza:
                self._close_trade(price,
                    f"PROFIT_LOCK_WIN_{max_profit:+.0f}", momentum, volatility, trend)
                return

        # -- EXIT ENERGY --------------------------------------------------
        decel = self.decelero.analyze()
        if entry_direction == "LONG":
            mom_score   = {'FORTE': 30, 'MEDIO': 20, 'DEBOLE': 5}.get(momentum, 15)
            trend_score = {'UP': 20, 'SIDEWAYS': 10, 'DOWN': 0}.get(trend, 10)
        else:
            mom_score   = {'DEBOLE': 30, 'MEDIO': 20, 'FORTE': 5}.get(momentum, 15)
            trend_score = {'DOWN': 20, 'SIDEWAYS': 10, 'UP': 0}.get(trend, 10)

        decel_comp  = int((1.0 - decel.get('decel_score', 0)) * 25)
        profit_comp = 15 if max_profit <= 0 else max(5, int((1.0 - min(1.0, retreat/max(max_profit,0.01))) * 25))
        exit_energy = mom_score + trend_score + decel_comp + profit_comp

        # Soglia uscita adattiva
        exit_soglia = max(30, 60 - int(duration / 10) * 2)
        wins_ticks  = max(0, int((pnl_lordo / max(1, abs(self.FEE_TRADE))) * 5))
        win_tag     = f"_WIN_+{wins_ticks}" if wins_ticks > 0 else ""

        if exit_energy <= exit_soglia:
            self._close_trade(price,
                f"EXIT_E{exit_energy}_S{exit_soglia}{win_tag}",
                momentum, volatility, trend)
            return

        # Timeout
        if duration > 300:
            self._close_trade(price, "TIMEOUT_MAX", momentum, volatility, trend)

    def _close_trade(self, price, reason, momentum, volatility, trend):
        if not self._trade:
            return

        entry_price = self._trade['price_entry']
        direction   = self._trade.get('direction', 'LONG')
        btc_qty     = self.EXPOSURE / entry_price

        if direction == 'SHORT':
            pnl_lordo = (entry_price - price) * btc_qty
        else:
            pnl_lordo = (price - entry_price) * btc_qty
        pnl_netto = pnl_lordo - self.FEE_TRADE

        is_win = pnl_netto > 0
        duration = time.time() - self._trade['ts_entry']

        # -- Aggiorna statistiche -----------------------------------------
        self.total_trades += 1
        self._m_trades    += 1
        self._m_pnl       += pnl_netto
        if is_win:
            self.wins       += 1
            self._m_wins    += 1
            self._loss_streak = 0
        else:
            self.losses        += 1
            self._m_losses     += 1
            self._loss_streak  += 1

        self._m_recent_trades.append({'is_win': is_win, 'pnl': pnl_netto})
        self.capital += pnl_netto

        # -- Log ----------------------------------------------------------
        emoji = "✅" if is_win else "❌"
        self._log(emoji, f"CLOSE {direction} @ {price:.2f} | "
                        f"pnl={pnl_netto:+.2f} | reason={reason} | "
                        f"dur={duration:.0f}s | {self._m_wins}W/{self._m_losses}L")

        # -- Aggiorna Oracolo e Memoria matrimoni --------------------------
        matrimonio_name = self._trade.get('matrimonio', '')
        entry_momentum  = self._trade_entry_momentum or momentum
        entry_volatility = self._trade_entry_volatility or volatility
        entry_trend     = self._trade_entry_trend or trend
        regime          = self._trade.get('regime', self._regime_current)

        self.oracolo.record(entry_momentum, entry_volatility, entry_trend,
                               direction, is_win, pnl_netto)
        if matrimonio_name:
            mat_data = MatrimonioIntelligente.get_by_name(matrimonio_name)
            wr_expected = mat_data.get('wr', 0.50) if mat_data else 0.50
            self.memoria.record_trade(matrimonio_name, is_win, wr_expected)

        # -- CapsuleMemory: classifica e aggiorna memoria -----------------
        trade_data = {
            'pnl':        pnl_netto,
            'pnl_lordo':  pnl_lordo,
            'reason':     reason,
            'direction':  direction,
            'regime':     regime,
            'momentum':   entry_momentum,
            'volatility': entry_volatility,
            'trend':      entry_trend,
            'matrimonio': matrimonio_name,
        }
        classification = self.oracle_clf.classify(trade_data)

        if classification['tipo'] in ('EVITA', 'MIGLIORA'):
            self.capsule_memory.record(
                direction=direction,
                regime=regime,
                momentum=entry_momentum,
                volatility=entry_volatility,
                trend=entry_trend,
                matrimonio=matrimonio_name,
                action=classification['azione'],
                is_win=is_win,
                pnl=pnl_netto,
                note=classification['motivo'][:200],
            )

        # -- Narratore storia (per OracleAuto) ----------------------------
        _storia = self.heartbeat_data.get('narratore_trade_storia', [])
        _storia.append({
            'id':         self.total_trades,
            'is_win':     is_win,
            'pnl':        round(pnl_netto, 4),
            'pnl_lordo':  round(pnl_lordo, 4),
            'reason':     reason,
            'direction':  direction,
            'regime':     regime,
            'momentum':   entry_momentum,
            'volatility': entry_volatility,
            'trend':      entry_trend,
            'matrimonio': matrimonio_name,
            'duration':   round(duration, 1),
            'ts':         time.time(),
        })
        if len(_storia) > 50:
            _storia = _storia[-50:]
        self.heartbeat_data['narratore_trade_storia'] = _storia

        # -- Persist state ------------------------------------------------
        self._persist.save(self.capital, self.total_trades)

        # -- Reset trade --------------------------------------------------
        self._trade              = None
        self._trade_max_price    = None
        self._trade_min_price    = None
        self._trade_entry_time   = None
        self._trade_matrimonio   = None

        self.campo.record_result(is_win, reason)

    # ────────────────────────────────────────────────────────────────────
    # HEARTBEAT
    # ────────────────────────────────────────────────────────────────────

    def _update_heartbeat(self, price, momentum, volatility, trend):
        now = time.time()
        if now - self.last_heartbeat < 1.0:
            return
        self.last_heartbeat = now

        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is None:
                return

            _recent = list(self._m_recent_trades)
            _wr_recent = (sum(1 for t in _recent if t['is_win']) / len(_recent)
                         if _recent else 0.0)

            self.heartbeat_data.update({
                "status":          "RUNNING",
                "mode":            "PAPER" if self.paper_trade else "LIVE",
                "capital":         round(self.capital, 2),
                "price":           round(price, 2),
                "regime":          self._regime_current,
                "regime_conf":     round(self._regime_conf, 2),
                "direction":       self.campo._direction,
                "momentum":        momentum,
                "volatility":      volatility,
                "trend":           trend,
                "trade_open":      self._trade is not None,
                "trade_entry":     self._trade.get('price_entry') if self._trade else None,
                "trade_direction": self._trade.get('direction') if self._trade else None,
                "wins":            self._m_wins,
                "losses":          self._m_losses,
                "pnl_total":       round(self._m_pnl, 2),
                "wr_recent":       round(_wr_recent, 3),
                "loss_streak":     self._loss_streak,
                "capsule_count":   len(self.capsule_memory._cache),
                "uptime":          round(now - self._boot_time, 0),
                "tick_count":      self._tick_count,
                "timestamp":       datetime.utcnow().isoformat(),
            })
        except Exception as e:
            log.error(f"[HB] {e}")
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    # ────────────────────────────────────────────────────────────────────
    # WEBSOCKET
    # ────────────────────────────────────────────────────────────────────

    def connect_binance(self):
        def on_message(ws, msg):
            try:
                data  = json.loads(msg)
                price = float(data['p'])
                volume = float(data.get('q', 1.0))
                self._process_tick(price, volume)
            except Exception as e:
                log.error(f"[WS_MSG] {e}")

        def on_error(ws, error):
            log.error(f"[WS_ERR] {error}")

        def on_close(ws, *args):
            log.warning("[WS] Connessione chiusa — reconnect in 5s")
            time.sleep(5)
            self.connect_binance()

        def on_open(ws):
            log.info(f"[WS] ✅ Connesso a {BINANCE_WS_URL}")

        self.ws = websocket.WebSocketApp(
            BINANCE_WS_URL,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        wst = threading.Thread(target=self.ws.run_forever, daemon=True)
        wst.start()

    def run(self):
        log.info("[V16] 🚀 OvertopBassanoV16Production avviato")
        self.connect_binance()

        # Avvia OracleAuto se disponibile
        try:
            import oracle_auto
            oracle_auto.start_background(bot_instance=self)
            _mode = os.environ.get("ORACLE_AUTO_MODE", "MANUAL").upper()
            if _mode in ("AUTO", "MANUAL"):
                oracle_auto.set_mode(_mode)
            log.info(f"[ORACLE_AUTO] Background worker avviato mode={_mode}")
        except ImportError:
            log.warning("[ORACLE_AUTO] oracle_auto.py non trovato")
        except Exception as e:
            log.error(f"[ORACLE_AUTO] errore avvio: {e}")

        # Loop principale
        while True:
            time.sleep(60)
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)


# ═══════════════════════════════════════════════════════════════════════════
# ENTRYPOINT STANDALONE
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    bot = OvertopBassanoV16Production()
    bot.run()

