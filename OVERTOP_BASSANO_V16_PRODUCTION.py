#!/usr/bin/env python3
"""
OVERTOP BASSANO V16 PRODUCTION - FULL BUILD
BOT TRADING COMPLETO INTEGRATO - PRODUCTION READY

╔══════════════════════════════════════════════════════════════════════════╗
║  LEGGE FONDAMENTALE — LEGGILA PRIMA DI TOCCARE QUALSIASI CALCOLO        ║
║                                                                          ║
║  TUTTO è calcolato in USDC. MAI in BTC. MAI in delta prezzo puro.       ║
║                                                                          ║
║  PARAMETRI FISSI:                                                        ║
║    TRADE_SIZE_USD = $1000  (margine per trade)                           ║
║    LEVERAGE       = 5      (leva)                                        ║
║    EXPOSURE       = $5000  (TRADE_SIZE_USD × LEVERAGE)                  ║
║    BTC_QTY        = EXPOSURE / entry_price  (mai hardcodato)             ║
║    FEE            = EXPOSURE × 0.0002 × 2 = $2.00 fissi per trade       ║
║                                                                          ║
║  FORMULA UNICA PER QUALSIASI PnL:                                        ║
║    delta    = price - entry  (LONG) | entry - price  (SHORT)             ║
║    pnl_lordo = delta × (EXPOSURE / entry_price)      ← USDC             ║
║    pnl_netto = pnl_lordo - FEE                       ← USDC             ║
║                                                                          ║
║  DURANTE IL TRADE  → usa pnl_lordo  (il trade respira)                  ║
║  AL CLOSE          → usa pnl_netto  (fee sottratta una sola volta)       ║
║  STATISTICHE       → usa pnl_netto  (oracolo, phantom, capsule)          ║
║                                                                          ║
║  BREAKEVEN: delta BTC minimo = FEE / BTC_QTY ≈ +$30                     ║
║  STOP LIVE: pnl_lordo < -$7  (= pnl_netto < -$5 dopo fee)               ║
║                                                                          ║
║  SE SCRIVI  pnl = price - entry  → È SBAGLIATO. SEMPRE.                 ║
║  SE SCRIVI  pnl = delta * btc_qty  SENZA  / entry_price  → SBAGLIATO.   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import json
import websocket
import threading
import time
import hashlib
import operator
import sqlite3
import os
# ════════════════════════════════════════════════════════════════════
# BUILD_MD5 (11giu, Roberto: "serve un segno sulla lista per sapere quale
# codice ha prodotto il trade"). Calcolo l'md5 del file all'avvio, una
# volta sola, e lo marchio su ogni trade. Così nel database si legge nero
# su bianco quale assetto l'ha generato — niente più "è vecchio o nuovo?"
# a vista. Si auto-aggiorna: cambi file, cambia il marchio.
try:
    with open(os.path.abspath(__file__), "rb") as _bf:
        BUILD_MD5 = hashlib.md5(_bf.read()).hexdigest()[:12]
except Exception:
    BUILD_MD5 = "unknown"
# ════════════════════════════════════════════════════════════════════
# ════════════════════════════════════════════════════════════════════
# FIX DEFINITIVO LOCK (6giu) — connessione serializzata da lock globale.
# Tutte le connessioni passano da _safe_connect, che le mette in fila con
# un unico lock. Due scritture non possono MAI sovrapporsi -> niente piu'
# "database is locked". Sostituisce i cerotti (timeout, interruttori).
import threading as _thr_db
_DB_GLOBAL_LOCK = _thr_db.RLock()
_orig_sqlite_connect = sqlite3.connect
def _safe_connect(*args, **kwargs):
    kwargs.setdefault("timeout", 30)
    kwargs["check_same_thread"] = False
    _DB_GLOBAL_LOCK.acquire()
    try:
        _c = _orig_sqlite_connect(*args, **kwargs)
        try:
            _c.execute("PRAGMA journal_mode=WAL;")
            _c.execute("PRAGMA busy_timeout=30000;")
            _c.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        return _DBConnWrapper(_c)
    except Exception:
        _DB_GLOBAL_LOCK.release()
        raise
class _DBConnWrapper:
    """Avvolge la connessione: rilascia il lock globale alla chiusura/uscita."""
    def __init__(self, conn):
        self._conn = conn
        self._released = False
    def _release(self):
        if not self._released:
            self._released = True
            try:
                _DB_GLOBAL_LOCK.release()
            except Exception:
                pass
    def close(self):
        try:
            self._conn.close()
        finally:
            self._release()
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def __enter__(self):
        self._conn.__enter__()
        return self
    def __exit__(self, *a):
        try:
            self._conn.__exit__(*a)
        finally:
            self._release()
from datetime import datetime
from collections import deque, defaultdict
import logging
import sys

# ===========================================================================
# [CFG]️  CONFIGURAZIONE GLOBALE
# ===========================================================================

# --- PAPER TRADE FLAG -------------------------------------------------------
# True  = simula tutto, zero ordini reali su Binance → usa per testare
# False = ordini reali → SOLO dopo paper test soddisfacente
PAPER_TRADE = True

# --- SAFETY GUARD ANTI-LIVE (Bug #1) ----------------------------------------
# Fix 12mag2026: impedisce avvio LIVE finché _place_order è placeholder.
# Se PAPER_TRADE=False per errore, il bot CRASHA all'avvio invece di
# simulare ordini fittizi facendo credere all'utente di guadagnare.
_PLACE_ORDER_IMPLEMENTED = False  # True SOLO dopo implementazione Binance API reale

if not PAPER_TRADE and not _PLACE_ORDER_IMPLEMENTED:
    raise RuntimeError(
        "\n" + "="*70 + "\n"
        "🚨 LIVE MODE BLOCCATO — SICUREZZA ANTI-PLACEHOLDER 🚨\n"
        "="*70 + "\n"
        "PAPER_TRADE=False richiede _place_order IMPLEMENTATA con Binance API.\n"
        "Per LIVE: implementare python-binance + test micro-size 1 settimana.\n"
        "Bot terminato per sicurezza.\n"
        + "="*70
    )

# --- SEED SCORER ------------------------------------------------------------
SEED_ENTRY_THRESHOLD = 0.45   # soglia minima per entrare

# --- DIVORCE TRIGGERS -------------------------------------------------------
DIVORCE_DRAWDOWN_PCT   = 3.0  # % drawdown dal massimo → trigger 3
DIVORCE_FP_DIVERGE_PCT = 0.50 # divergenza fingerprint > 50% → trigger 4
DIVORCE_MIN_TRIGGERS   = 2    # quanti trigger devono scattare per uscita immediata

# --- DATABASE ----------------------------------------------------------------
DB_PATH        = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
NARRATIVES_DB  = os.environ.get("NARRATIVES_DB", "/home/app/data/narratives.db")

# --- PASSO 15.F (15mag2026) — LIBRO DI PESCA SU FINGERPRINT VERITAS ---------
# La pesca NON parte più dall'OI carica (principio sbagliato — fallito).
# Parte dal fingerprint ORACOLO DINAMICO: (momentum × volatilità × trend × dir).
# Pianta solo se il fingerprint corrente ha WR storico ≥ 60% con n ≥ 30 + PnL_sum > 0.
# Esempio: LONG|FORTE|BASSA|UP → WR 78% n=30 PnL+$30 → PIANTA.
# Stima: 0-2 piantate ogni 10-30 minuti (solo quando contesto è oro).
# Per disattivare: Render env LIBRO_PESCA_ENABLED=false
LIBRO_PESCA_ENABLED = os.environ.get("LIBRO_PESCA_ENABLED", "true").lower() in ("true", "1", "yes")

# --- BINANCE -----------------------------------------------------------------
SYMBOL         = "BTCUSDC"
BINANCE_WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@aggTrade"

# ===========================================================================
# LOGGING
# ===========================================================================

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)

# ═══════════════════════════════════════════════
# V16 ENGINES — integrati in V15
# ═══════════════════════════════════════════════
try:
    from comparto_engine import CompartoEngine, COMPARTI
    from nervosismo_engine import NervosismoEngine
    from breath_engine import BreathEngine
    _V16_ENGINES_OK = True
except ImportError:
    _V16_ENGINES_OK = False
    log_placeholder = None

log = logging.getLogger(__name__)

# ===========================================================================
# OPERATORS FOR CAPSULE RUNTIME
# ===========================================================================

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

# ===========================================================================
# PATCH 6 BUG 13 — WINNING ENVIRONMENT SIGNATURE LOGGER (osservatore puro)
# ===========================================================================
# Idea originale di Roberto, 17 maggio 2026:
#
#   "Il secondo trade deve trovare nel mercato lo stesso ambiente del primo
#    per agire in modo corretto. Non si forza il bot a replicare. Si verifica
#    se il mercato ripresenta da solo la firma vincente."
#
# Cosa fa:
#   1. Quando un trade chiude come WIN_NET (pnl_netto > 0), registra la
#      "firma" completa dell'ambiente al momento dell'ENTRY.
#   2. Per ogni nuova entry candidate, calcola la similarità con:
#        - ultima firma WIN_NET (entro 60 min)
#        - firme LOSS_FEE recenti
#        - firme LOSS_REAL recenti
#   3. Salva nel DB i match per analisi successiva.
#
# Cosa NON fa:
#   - non vota
#   - non blocca
#   - non modifica soglie
#   - non tocca entry/exit/V2/Sinapsi/Oracolo
#   - osservatore puro, solo dati per il report
#
# Lo scopo è raccogliere evidenza per rispondere alla domanda:
#   "I WIN_NET successivi accadono quando match_win è alto?"
#   "I LOSS_FEE accadono quando match_win è basso?"
#   "Esiste davvero una firma comune tra le vittorie?"
# Solo dopo aver risposto, in patch separata, si potrà trasformare
# la firma in filtro entry.
# ===========================================================================
import math as _wsig_math


class WinningSignatureLogger:
    """Osservatore puro: registra firme di trade vincenti e calcola similarità.
    
    NON modifica nulla del bot. Solo legge e scrive nel DB su tabella dedicata.
    """
    
    # Campi numerici della firma — la similarità è euclidea normalizzata su questi
    NUMERIC_FIELDS = [
        'oi_carica', 'vol_pressure', 'rsi', 'drift',
        'pred_delta_fuoco', 'pred_delta_carica', 'pred_v2_delta',
        'score', 'soglia',
    ]
    # Campi categorici — devono matchare esattamente per peso massimo
    CATEGORICAL_FIELDS = [
        'momentum', 'volatility', 'trend', 'direction',
        'regime', 'oi_stato', 'pred_source',
    ]
    # Tolleranze relative per i campi numerici (per normalizzare la distanza)
    NUMERIC_RANGES = {
        'oi_carica': 1.0,           # 0-1
        'vol_pressure': 2.0,        # 0-4 tipicamente
        'rsi': 100.0,               # 0-100
        'drift': 5.0,               # ±5% tipico
        'pred_delta_fuoco': 10.0,
        'pred_delta_carica': 10.0,
        'pred_v2_delta': 30.0,
        'score': 100.0,
        'soglia': 100.0,
    }
    # Tempo massimo (secondi) per considerare una firma "viva"
    SIGNATURE_TTL_SEC = 3600   # 60 minuti
    
    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_table()
    
    def _ensure_table(self):
        """Crea la tabella se non esiste. Idempotente."""
        try:
            import sqlite3
            conn = _safe_connect(self.db_path, timeout=30)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS winning_signatures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    trade_outcome TEXT NOT NULL,  -- WIN_NET / LOSS_FEE / LOSS_REAL
                    pnl_netto REAL,
                    pnl_lordo REAL,
                    signature_json TEXT NOT NULL,
                    match_at_entry REAL  -- similarità all'ultima firma WIN viva, calcolata al momento dell'entry
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_winsig_ts ON winning_signatures(ts)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_winsig_outcome ON winning_signatures(trade_outcome)
            """)
            conn.commit()
            conn.close()
        except Exception:
            # Non blocca il bot se il DB ha problemi
            pass
    
    def save_signature(self, trade_outcome, signature, pnl_netto, pnl_lordo, match_at_entry):
        """Salva una firma chiusa (post-trade). Non blocca mai."""
        try:
            import sqlite3
            import json
            import time
            conn = _safe_connect(self.db_path, timeout=30)
            conn.execute("""
                INSERT INTO winning_signatures (ts, trade_outcome, pnl_netto, pnl_lordo, signature_json, match_at_entry)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), trade_outcome,
                  float(pnl_netto) if pnl_netto is not None else None,
                  float(pnl_lordo) if pnl_lordo is not None else None,
                  json.dumps(signature),
                  float(match_at_entry) if match_at_entry is not None else None))
            conn.commit()
            conn.close()
        except Exception:
            pass
    
    def get_recent_signatures(self, outcome_filter, max_age_sec=None):
        """Ritorna le firme recenti per un dato outcome. None = tutte."""
        if max_age_sec is None:
            max_age_sec = self.SIGNATURE_TTL_SEC
        try:
            import sqlite3
            import json
            import time
            conn = _safe_connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            cutoff = time.time() - max_age_sec
            if outcome_filter:
                rows = conn.execute("""
                    SELECT signature_json FROM winning_signatures
                    WHERE trade_outcome = ? AND ts >= ?
                    ORDER BY ts DESC LIMIT 20
                """, (outcome_filter, cutoff)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT signature_json, trade_outcome FROM winning_signatures
                    WHERE ts >= ?
                    ORDER BY ts DESC LIMIT 50
                """, (cutoff,)).fetchall()
            conn.close()
            return [json.loads(r['signature_json']) for r in rows]
        except Exception:
            return []
    
    @classmethod
    def similarity(cls, sig_a, sig_b):
        """Calcola similarità 0.0-1.0 tra due firme.
        
        - Campi categorici: match esatto = 1.0, mismatch = 0.0
        - Campi numerici: 1 - (distanza_assoluta / range_tipico), capped a [0,1]
        - Media pesata: categorici peso 1.5, numerici peso 1.0
        """
        if not sig_a or not sig_b:
            return 0.0
        scores = []
        weights = []
        # Categorici
        for f in cls.CATEGORICAL_FIELDS:
            a = sig_a.get(f)
            b = sig_b.get(f)
            if a is None or b is None:
                continue
            scores.append(1.0 if a == b else 0.0)
            weights.append(1.5)
        # Numerici
        for f in cls.NUMERIC_FIELDS:
            a = sig_a.get(f)
            b = sig_b.get(f)
            if a is None or b is None:
                continue
            try:
                a_f = float(a)
                b_f = float(b)
                rng = cls.NUMERIC_RANGES.get(f, 1.0)
                dist = abs(a_f - b_f) / rng
                sim = max(0.0, 1.0 - dist)
                scores.append(sim)
                weights.append(1.0)
            except (ValueError, TypeError):
                continue
        if not scores:
            return 0.0
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        total_w = sum(weights)
        return weighted_sum / total_w if total_w > 0 else 0.0
    
    def compute_match_at_entry(self, current_signature):
        """Calcola match della firma corrente con l'ultima WIN_NET viva.
        
        Ritorna float 0.0-1.0 o None se non c'è alcuna firma WIN recente.
        """
        wins = self.get_recent_signatures('WIN_NET')
        if not wins:
            return None
        # Prendi la più recente (è la prima nella lista per ORDER BY ts DESC)
        last_win = wins[0]
        return self.similarity(current_signature, last_win)
    
    @staticmethod
    def build_signature_from_context(momentum, volatility, trend, direction,
                                      regime, oi_stato, oi_carica, vol_pressure,
                                      rsi, drift, score, soglia,
                                      pred_delta_fuoco, pred_delta_carica,
                                      pred_v2_delta, pred_source,
                                      # ════════════════════════════════════════════════════════════════
                                      # PATCH 13 BUG 20a — OSSERVATIVO (firmato ChatGPT 18 mag 2026)
                                      # ════════════════════════════════════════════════════════════════
                                      # 6 nuovi campi per verifica empirica intuizioni Roberto:
                                      #   1. "Soglia di non ritorno" pre-entry → pb_signals + 3 flag
                                      #   2. "Il grasso arriva dopo" → fp_exit_too_early_rate + fp_post_delta_avg
                                      # Default None: backward-compat con chiamate esistenti.
                                      # NESSUN filtro operativo. Solo SAVE nel DB.
                                      # ════════════════════════════════════════════════════════════════
                                      pb_signals=None,
                                      pb_compression=None,
                                      pb_volume_acc=None,
                                      pb_seed_directed=None,
                                      fp_exit_too_early_rate=None,
                                      fp_post_delta_avg=None,
                                      # ════════════════════════════════════════════════════════════════
                                      # CABLAGGIO_25MAG2026 — TESTIMONI TSUNAMI nella firma trade
                                      # ════════════════════════════════════════════════════════════════
                                      # Aggiunti 10 testimoni (Tsunami 3 timeframe + confidenza).
                                      # I valori vengono letti via self.tsunami.last_decision() al
                                      # momento della chiamata (riga ~11200), seguendo lo stesso
                                      # pattern di _record_phantom (riga 12696).
                                      # Default None: backward-compat.
                                      # ════════════════════════════════════════════════════════════════
                                      ts_30s_strength=None,
                                      ts_30s_direction=None,
                                      ts_30s_coerenza=None,
                                      ts_2min_strength=None,
                                      ts_2min_direction=None,
                                      ts_2min_coerenza=None,
                                      ts_10min_strength=None,
                                      ts_10min_direction=None,
                                      ts_10min_coerenza=None,
                                      ts_confidenza=None):
        """Helper: costruisce un dict-firma dai sensori live."""
        sig = {
            'momentum': momentum,
            'volatility': volatility,
            'trend': trend,
            'direction': direction,
            'regime': regime,
            'oi_stato': oi_stato,
            'oi_carica': round(float(oi_carica or 0), 4),
            'vol_pressure': round(float(vol_pressure or 0), 4),
            'rsi': round(float(rsi or 50), 2),
            'drift': round(float(drift or 0), 4),
            'score': round(float(score or 0), 2),
            'soglia': round(float(soglia or 60), 2),
            'pred_delta_fuoco': round(float(pred_delta_fuoco or 0), 4),
            'pred_delta_carica': round(float(pred_delta_carica or 0), 4),
            'pred_v2_delta': round(float(pred_v2_delta or 0), 4),
            'pred_source': pred_source or 'UNKNOWN',
        }
        # ════════════════════════════════════════════════════════════════
        # PATCH 13: campi osservativi pre-entry (None se non calcolati)
        # ════════════════════════════════════════════════════════════════
        sig['pb_signals'] = int(pb_signals) if pb_signals is not None else None
        sig['pb_compression'] = bool(pb_compression) if pb_compression is not None else None
        sig['pb_volume_acc'] = bool(pb_volume_acc) if pb_volume_acc is not None else None
        sig['pb_seed_directed'] = bool(pb_seed_directed) if pb_seed_directed is not None else None
        sig['fp_exit_too_early_rate'] = round(float(fp_exit_too_early_rate), 4) \
            if fp_exit_too_early_rate is not None else None
        sig['fp_post_delta_avg'] = round(float(fp_post_delta_avg), 4) \
            if fp_post_delta_avg is not None else None
        # ════════════════════════════════════════════════════════════════
        # CABLAGGIO_25MAG2026: scrivo i 10 testimoni Tsunami nella firma
        # ════════════════════════════════════════════════════════════════
        sig['ts_30s_strength']  = round(float(ts_30s_strength), 4)  if ts_30s_strength is not None else None
        sig['ts_30s_direction'] = ts_30s_direction if ts_30s_direction else None
        sig['ts_30s_coerenza']  = round(float(ts_30s_coerenza), 4)  if ts_30s_coerenza is not None else None
        sig['ts_2min_strength']  = round(float(ts_2min_strength), 4)  if ts_2min_strength is not None else None
        sig['ts_2min_direction'] = ts_2min_direction if ts_2min_direction else None
        sig['ts_2min_coerenza']  = round(float(ts_2min_coerenza), 4)  if ts_2min_coerenza is not None else None
        sig['ts_10min_strength']  = round(float(ts_10min_strength), 4)  if ts_10min_strength is not None else None
        sig['ts_10min_direction'] = ts_10min_direction if ts_10min_direction else None
        sig['ts_10min_coerenza']  = round(float(ts_10min_coerenza), 4)  if ts_10min_coerenza is not None else None
        sig['ts_confidenza']      = int(ts_confidenza) if ts_confidenza is not None else None
        return sig


# ===========================================================================
# STABILITY TELEMETRY - LOGGING PASSIVO, ZERO LOGICA
# Solo osserva. Non decide. Non modifica. Non ottimizza.
# ===========================================================================

class StabilityTelemetry:
    """Registra ogni decisione, flip, cambio parametro. Solo logging.
    
    VINCOLI OBBLIGATORI:
    1. Ogni evento ha SEMPRE: ts, event_type, regime, direction, open_position
    2. flip/param_change/trade_close/regime_change hanno anche snapshot:
       active_threshold, drift, macd, trend, volatility, bridge_reason
    """

    def __init__(self):
        self._start_time = time.time()
        self._events = deque(maxlen=10000)    # FIX V16: cap RAM (era list illimitata)

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
        """Salva telemetria su SQLite - eventi singoli + report.

        FIX V16:
        - Drena self._events dopo il commit (era N²: riscriveva tutto ad ogni chiamata)
        - Auto-pruning a 50.000 righe (era unbounded: 6.4M righe = 3.8 GB)

        INTERRUTTORE 6giu: TELEMETRY_OFF=true spegne questa scrittura ad alta
        frequenza (martellava il DB ogni tick -> lock). Reversibile via env var.
        """
        if os.environ.get("TELEMETRY_OFF", "false").lower() == "true":
            try:
                self._events.clear()
            except Exception:
                pass
            return
        try:
            # Drena eventi accumulati: copia + svuota subito,
            # cosi' le append concorrenti non perdono dati.
            events_to_save = list(self._events)
            self._events.clear()

            conn = _safe_connect(db_path, timeout=15)
            conn.execute("PRAGMA busy_timeout=15000;")
            conn.execute("""CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT, data_json TEXT
            )""")
            # Salva eventi drenati (lista locale, immutabile)
            for e in events_to_save:
                conn.execute("INSERT INTO telemetry (event_type, data_json) VALUES (?, ?)",
                            (e['event_type'], json.dumps(e)))
            # Salva report aggregato
            report = self.generate_report()
            conn.execute("INSERT INTO telemetry (event_type, data_json) VALUES (?, ?)",
                        ("STABILITY_REPORT", json.dumps(report)))
            # AUTO-PRUNING: tieni solo ultime 50.000 righe (cap ~30 MB)
            conn.execute("""DELETE FROM telemetry
                            WHERE id < (SELECT MAX(id) - 50000 FROM telemetry)""")
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
try:
    from capsule_executor import CapsuleExecutor
    # ═══════════════════════════════════════════════════════════════
    # PATCH 15 BUG 22 — Skill esterna CapsulaCanvas
    # Vive in file capsula_canvas.py. Modulo indipendente.
    # Livelli armati via env CANVAS_L1..L6, kill switch CANVAS_DEAD.
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_canvas import CapsulaCanvas
        _CANVAS_AVAILABLE = True
    except Exception as _e_canvas:
        _CANVAS_AVAILABLE = False

    # ═══════════════════════════════════════════════════════════════
    # PATCH 17 — SKILL REGINA — CapsulaMemoria
    # Vive in file capsula_memoria.py. Memoria viva per Claude.
    # Kill switch: env MEMORIA_DEAD=true
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_memoria import CapsulaMemoria, set_memoria
        _MEMORIA_AVAILABLE = True
    except Exception as _e_memoria:
        _MEMORIA_AVAILABLE = False
    # ═══════════════════════════════════════════════════════════════
    # PATCH 18 — CAPSULA FASE (25mag2026)
    # Scoperta Roberto Zan: WIN/LOSS arrivano in cluster temporali.
    # La fase del mercato (delta prezzo 2/5/10 min) è il vero
    # discriminante. Capsula adattiva con 6 livelli armabili.
    # Kill switch: env CAPSULA_FASE_DEAD=true
    # Default: L1+L2+L3 attivi (OBSERVER, non blocca).
    # Per attivare blocco: env CAPSULA_FASE_L4=true
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_fase import CapsulaFase
        _FASE_AVAILABLE = True
    except Exception as _e_fase:
        _FASE_AVAILABLE = False
    # ═══════════════════════════════════════════════════════════════
    # PATCH 19 — CAPSULA TSUNAMI DISCORDE (27mag2026)
    # Capsula adattogena per discordanze Tsunami vs campo.
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_tsunami_discorde import CapsulaTsunamiDiscorde
        _CAP_TSUNAMI_AVAILABLE = True
    except Exception as _e_cap_ts:
        _CAP_TSUNAMI_AVAILABLE = False
    # ═══════════════════════════════════════════════════════════════
    # PATCH 20 — CAPSULA MATRIGNA / SuperRisponditrice (27mag2026)
    # Capsula che GENERA capsule figlie adattogene per ogni firma di
    # mercato. Le firme nuove diventano capsule autonome. Le firme
    # storiche partono con stats già mature dal DB.
    # Visione Roberto: "se arriva un mercato nuovo, il SuperRisponditore
    # dovrebbe creare la capsula".
    # Kill: env CAPSULA_MATRIGNA_DEAD=true | Decide: CAP_MAT_L4_DECIDE=true
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_matrigna import CapsulaMatrigna
        _CAP_MATRIGNA_AVAILABLE = True
    except Exception as _e_cap_mat:
        _CAP_MATRIGNA_AVAILABLE = False
    # ═══════════════════════════════════════════════════════════════
    # CAPSULA REGIME-EDGE (31mag2026) — Gioca dove i win pagano i loss.
    # Sui dati reali oracolo_snapshot: SIDEWAYS = merda (perdita reale
    # dimostrata), UP/trend = campo buono (WR 55-78%, ma simulato).
    # Nasce OSSERVATRICE (L3): osserva e registra, NON tocca il bot.
    # Kill: env CAPSULA_REGIME_EDGE_DEAD=true | Decide: CAPSULA_REGIME_EDGE_L4=true
    # ═══════════════════════════════════════════════════════════════
    try:
        from capsula_regime_edge import CapsulaRegimeEdge
        _CAP_REGIME_EDGE_AVAILABLE = True
    except Exception as _e_cap_re:
        _CAP_REGIME_EDGE_AVAILABLE = False
    _CE_AVAILABLE = True
except ImportError:
    _CE_AVAILABLE = False
    _CANVAS_AVAILABLE = False
    _MEMORIA_AVAILABLE = False
    _FASE_AVAILABLE = False
    _CAP_TSUNAMI_AVAILABLE = False
    _CAP_MATRIGNA_AVAILABLE = False
    _CAP_REGIME_EDGE_AVAILABLE = False
    log.warning("[CE] capsule_executor.py non trovato")

try:
    from capsule_manager import CapsuleManager
    _CM_AVAILABLE = True
    log.info("[CM] ✅ CapsuleManager disponibile")
except ImportError:
    _CM_AVAILABLE = False
    log.warning("[CM] ⚠️ capsule_manager.py non trovato — uso fallback CapsuleRuntime")

# ═══════════════════════════════════════════════════════════════════════════
# 🌊 TSUNAMI DETECTOR — invenzione Roberto Zanardo (12mag2026)
# Misura forza strutturata multi-scala (30s + 2min + 10min) per distinguere
# TSUNAMI (energia accumulata su tutte le scale) da SCHIUMA (rumore localizzato).
# ═══════════════════════════════════════════════════════════════════════════
try:
    from tsunami_detector import TsunamiEngine
    _TSUNAMI_AVAILABLE = True
    log.info("[TSUNAMI] 🌊 TsunamiEngine disponibile")
except ImportError:
    _TSUNAMI_AVAILABLE = False
    log.warning("[TSUNAMI] ⚠️ tsunami_detector.py non trovato — modulo disabilitato")

class CapsuleRuntime:
    """Valuta e applica capsule da capsule_attive.json - hot reload senza restart."""

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
            log.info(f"[CAPSULE] [OK] Caricate {len(self.capsules)} regole da {self.capsule_file}")
        except FileNotFoundError:
            self.capsules = []
            log.warning("[CAPSULE] ⚠️ capsule_attive.json non trovato - opero a vuoto")
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
        """Valuta tutte le capsule attive. Ritorna: {blocca, size_mult, soglia_boost, reason}"""
        ora = time.time()
        risultato = {'blocca': False, 'size_mult': 1.0, 'soglia_boost': 0.0, 'reason': ''}
        for capsule in sorted(self.capsules, key=lambda c: c.get('priority', 5)):
            if not capsule.get('enabled', True):
                continue
            # Capsule scadute: salta (verranno pulite da IntelligenzaAutonoma)
            if capsule.get('scade_ts') and capsule['scade_ts'] < ora:
                continue
            triggers = capsule.get('trigger', [])
            if triggers and not all(self._check_trigger(t, contesto) for t in triggers):
                continue
            azione = capsule.get('azione', {})
            if azione.get('type') == 'blocca_entry':
                risultato['blocca'] = True
                risultato['reason'] = azione.get('params', {}).get('reason', 'capsule_block')
                log.info(f"[CAPSULE_APPLY] capsule_id={capsule.get('capsule_id','?')} action=blocca_entry reason={risultato['reason']}")
                break
            elif azione.get('type') == 'modifica_size':
                old_mult = risultato['size_mult']
                risultato['size_mult'] *= azione.get('params', {}).get('mult', 1.0)
                log.info(f"[CAPSULE_APPLY] capsule_id={capsule.get('capsule_id','?')} action=size_mult old={old_mult:.2f} new={risultato['size_mult']:.2f}")
            elif azione.get('type') == 'boost_soglia':
                old_boost = risultato['soglia_boost']
                risultato['soglia_boost'] += azione.get('params', {}).get('delta', 0.0)
                log.info(f"[CAPSULE_APPLY] capsule_id={capsule.get('capsule_id','?')} action=boost_soglia old={old_boost:.1f} new={risultato['soglia_boost']:.1f}")
            # NUOVE AZIONI AUTO-CORRETTIVE
            elif azione.get('type') == 'ripristina_pesi_sc':
                # Segnala al bot di ripristinare i pesi SC
                risultato['ripristina_pesi_sc'] = azione.get('params', {})
            elif azione.get('type') == 'sblocca_short_ranging':
                # Sblocca SHORT in RANGING per questa capsula
                risultato['sblocca_short_ranging'] = True
            elif azione.get('type') == 'oracolo_override':
                # Oracolo supera i blocchi difensivi
                risultato['oracolo_override'] = True
            elif azione.get('type') == 'blocca_long':
                # Blocca LONG per N secondi
                risultato['blocca_long'] = True
                risultato['blocca'] = True
                risultato['reason'] = 'AUTO_STOP_LONG'
            elif azione.get('type') == 'set_soglia_ranging':
                # Imposta soglia ottimale per RANGING
                risultato['soglia_ranging'] = azione.get('params', {}).get('soglia', 48)
            elif azione.get('type') == 'set_cap2_soglia':
                # Auto-calibra soglia Capsule2 dai phantom
                risultato['cap2_soglia'] = azione.get('params', {}).get('soglia', 0.35)
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

# ===========================================================================
# CONFIG HOT RELOADER
# ===========================================================================

class ConfigHotReloader:
    """Controlla hash del file capsule ogni 30 s. Zero restart."""

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

    def force_reload(self) -> bool:
        """Forza reload resettando l'hash — usato quando il file viene scritto ex-novo."""
        self.hash = ""
        return self.check_reload()

# ===========================================================================
# REAL-TIME LEARNING ENGINE
# ===========================================================================

class IntelligenzaAutonoma:
    """
    MOTORE DI INTELLIGENZA AUTONOMA - Sostituisce RealtimeLearningEngine.

    Non alza/abbassa manopole. OSSERVA → MISURA la gravità/opportunità →
    GENERA capsule con vita propria → le TESTA → le ELIMINA se non servono.

    TRE LIVELLI:
      L1 - Capsule Strutturali: le 5 hardcoded. Non toccate mai.
      L2 - Capsule di Esperienza: nate da pattern statistici reali.
           Vita: 50-500 trade. Muoiono se WR si normalizza.
      L3 - Capsule di Evento: nate da anomalie ADESSO.
           Vita: minuti/ore. Auto-scadono senza intervento umano.

    PAVIMENTI NON SUPERABILI (hardcoded, non delegati):
      - Score minimo assoluto: 48
      - Stop loss PnL: 1% margine ($10)
      - TRAP/PANIC: veti assoluti
      - FANTASMA WR < 30% su 20+ campioni reali

    TUTTO IL RESTO è output del motore, non input fisso.
    """

    # -- PAVIMENTI FISICI - non delegabili --------------------------------
    SCORE_FLOOR       = int(os.environ.get("SCORE_FLOOR", "48"))  # ENV (era 48 fisso)
    STOP_LOSS_PCT     = 0.01   # 1% margine max loss per trade
    MIN_SAMPLES_L2    = 8      # campioni minimi per capsule L2
    # ANTIAEREA (29mag, Roberto): "UN loss con firma X marca la firma."
    # Il cancello generale scende a 1 → l'apprendimento di FIRMA PRECISA parte
    # dal PRIMO loss. La prudenza contro "blocca tutto il regime su pochi
    # campioni" è applicata INTERNAMENTE solo all'analisi regime-largo
    # (MIN_SAMPLES_REGIME), non più come cancello che frena tutto.
    MIN_SAMPLES_L3    = 1      # firma precisa: marca dal primo loss
    MIN_SAMPLES_REGIME = 8     # regime largo: resta prudente (evita "blocca tutto RANGING")
    MAX_CAPSULE_AGE   = 86400  # 24h max vita capsule L2 senza conferma
    MAX_CAPSULE_EVENT = 3600   # 1h max vita capsule L3

    def __init__(self, capsule_file: str = "capsule_attive.json", db_path: str = None):
        self.capsule_file = capsule_file
        self.db_path      = db_path
        self._trade_buffer = deque(maxlen=200)   # memoria rolling per analisi
        self._capsule_meta = {}                   # {capsule_id: {nato_ts, trade_count, wr_al_nato, ...}}
        self._last_analisi = 0
        self._analisi_interval = 30               # analizza ogni 30 trade
        self._trade_count = 0

    # =====================================================================
    # INTERFACCIA PUBBLICA
    # =====================================================================

    def registra_trade(self, trade: dict):
        """Ogni trade chiuso passa da qui. Arricchisce con timestamp."""
        trade['_ts'] = time.time()
        self._trade_buffer.append(trade)
        self._trade_count += 1
        # Analisi ogni N trade O se evento critico
        is_critico = (not trade.get('is_win') and abs(trade.get('pnl', 0)) > 5)
        if self._trade_count % self._analisi_interval == 0 or is_critico:
            self.analizza_e_genera()
        # Pulizia capsule scadute - ogni trade è un'occasione
        if self._trade_count % 10 == 0:
            self._pulisci_scadute()

    def analizza_e_genera(self) -> list:
        """Cuore del motore. Osserva, misura, genera. Ritorna le capsule create."""
        nuove = []
        trades = list(self._trade_buffer)

        # -- LIVELLO 2 da Signal Tracker — non aspetta trade reali --------
        # Il Signal Tracker ha centinaia di osservazioni — usale subito
        nuove += self._analisi_l2_signal_tracker()

        if len(trades) < self.MIN_SAMPLES_L3:
            if nuove:
                self._persisti(nuove)
            return nuove

        # -- LIVELLO 2: pattern statistici ---------------------------------
        nuove += self._analisi_l2_matrimoni(trades)
        nuove += self._analisi_l2_contesto(trades)
        nuove += self._analisi_l2_drift_regime(trades)

        # -- LIVELLO 3: eventi anomali adesso ------------------------------
        nuove += self._analisi_l3_loss_streak(trades)
        nuove += self._analisi_l3_regime_tossico(trades)
        nuove += self._analisi_l3_opportunita(trades)

        # -- AUTO-CORRETTIVE: capsule dai dati live -------------------------
        nuove += self._analisi_auto_correttive()

        if nuove:
            self._persisti(nuove)
        return nuove

    def _analisi_l2_signal_tracker(self) -> list:
        """
        Genera capsule direttamente dal Signal Tracker.
        Non aspetta trade reali — usa le osservazioni di mercato accumulate.
        MIN 100 segnali chiusi per contesto.
        """
        nuove = []
        if not hasattr(self, '_signal_tracker_ref') or not self._signal_tracker_ref:
            return nuove

        stats = self._signal_tracker_ref._stats
        for context, s in stats.items():
            n = s.get('n', 0)
            if n < 100:
                continue

            hit = s.get('hit_60s', 0.5)
            pnl = s.get('pnl_sim_avg', 0)

            # Parsing context: "RANGING|LONG|DEBOLE_<58"
            parts = context.split('|')
            if len(parts) != 3:
                continue
            regime, direction, score_band = parts

            cap_id = f"L2_ST_{context.replace('|','_').replace('<','lt').replace('>','gt')}"

            # OPPORTUNITA: hit > 55% E pnl positivo → boost entry
            if hit >= 0.55 and pnl > 0.05 and n >= 200:
                if not any(c.get('id') == cap_id for c in self._auto_capsules):
                    nuove.append({
                        'id': cap_id,
                        'livello': 'L2',
                        'tipo': 'BOOST_ST',
                        'descrizione': f"L2_ST OPP: {context} hit={hit:.0%} pnl={pnl:+.3f} n={n}",
                        'trigger': [
                            {'param': 'regime',    'op': '==', 'value': regime},
                            {'param': 'direction', 'op': '==', 'value': direction},
                        ],
                        'azione': {'type': 'modifica_soglia', 'params': {'delta': -3}},
                        'priority': 5,
                        'enabled': True,
                        'vita': 3600,
                        'samples': n,
                        'wr': hit,
                        'pnl_avg': pnl,
                    })

            # TOSSICO: hit < 40% E pnl negativo → blocca
            elif hit < 0.40 and pnl < -0.05 and n >= 100:
                if not any(c.get('id') == cap_id for c in self._auto_capsules):
                    nuove.append({
                        'id': cap_id,
                        'livello': 'L2',
                        'tipo': 'BLOCCA_ST',
                        'descrizione': f"L2_ST TOSSICO: {context} hit={hit:.0%} pnl={pnl:+.3f} n={n}",
                        'trigger': [
                            {'param': 'regime',    'op': '==', 'value': regime},
                            {'param': 'direction', 'op': '==', 'value': direction},
                        ],
                        'azione': {'type': 'blocca_entry', 'params': {'reason': f'ST_TOSSICO_{context}'}},
                        'priority': 8,
                        'enabled': True,
                        'vita': 7200,
                        'samples': n,
                        'wr': hit,
                        'pnl_avg': pnl,
                    })

        return nuove

    def _analisi_auto_correttive(self) -> list:
        """
        Capsule auto-correttive dai dati live.
        Osserva SC pesi, Oracolo, drift, regime e genera capsule correttive.
        Trasparente: ogni capsula ha motivo leggibile nel log.
        """
        capsule = []
        ts = time.time()

        # Leggi contesto live dal bot (passato tramite _ctx)
        ctx = getattr(self, '_ctx', {})
        sc_pesi    = ctx.get('sc_pesi', {})
        oi_carica  = ctx.get('oi_carica', 0.0)
        oi_stato   = ctx.get('oi_stato', '')
        drift      = ctx.get('drift', 0.0)
        macd_hist  = ctx.get('macd_hist', 0.0)
        regime     = ctx.get('regime', '')
        st_stats   = ctx.get('signal_tracker_stats', {})
        vt_stats   = ctx.get('veritas_stats', {})
        phantom    = ctx.get('phantom_stats', {})

        # CAPSULA 6: auto-calibrazione Capsule2 dai phantom
        # Se un matrimonio bloccato da CAP2 ha net positivo su 50+ blocchi
        # → la soglia è troppo alta → genera capsula per abbassarla
        for key, s in phantom.items():
            if not key.startswith('CAP2_M2_'):
                continue
            blk = s.get('blocked', 0)
            if blk < 50:
                continue
            pnl_missed = s.get('pnl_missed', 0)
            pnl_saved  = s.get('pnl_saved', 0)
            net = pnl_missed - pnl_saved  # positivo = stavamo perdendo opportunità
            # Estrai confidence dalla chiave: CAP2_M2_RANGE_VOL_M_conf0.40
            try:
                conf = float(key.split('_conf')[-1])
            except Exception:
                continue
            if net > 200 and conf >= 0.30:
                cap_id = f'AUTO_CAP2_SOGLIA_{conf:.2f}'
                if self._è_nuova(cap_id):
                    capsule.append({
                        'id': cap_id,
                        'tipo': 'L2',
                        'motivo': f"Phantom {key}: net=+${net:.0f} su {blk} blocchi conf={conf:.2f} → soglia troppo alta",
                        'azione': {'type': 'set_cap2_soglia', 'params': {'soglia': max(0.30, conf - 0.05)}},
                        'scade_ts': ts + 86400, 'vita_ore': 24
                    })
                    log.info(f"[IA_AUTO] 📊 Capsula AUTO_CAP2_SOGLIA generata: conf={conf:.2f} net=+${net:.0f}")

        # CAPSULA 1: Pesi SC degradati
        if sc_pesi.get('campo_carica', 0.30) < 0.25:
            if self._è_nuova('AUTO_SC_PESI_FIX'):
                capsule.append({
                    'id': 'AUTO_SC_PESI_FIX',
                    'tipo': 'L2',
                    'motivo': f"campo_carica={sc_pesi.get('campo_carica',0):.2f} degradato",
                    'azione': {'type': 'ripristina_pesi_sc', 'params': {
                        'campo_carica': 0.35, 'signal_tracker': 0.20,
                        'oracolo_fp': 0.25, 'matrimonio': 0.10, 'phantom_ratio': 0.10
                    }},
                    'scade_ts': ts + 3600, 'vita_ore': 1
                })
                log.info(f"[IA_AUTO] 🔧 Capsula AUTO_SC_PESI_FIX generata")

        # CAPSULA 2: SHORT bloccato con mercato che scende
        if (drift < -0.03 and macd_hist < -1.0 and
                oi_stato in ("FUOCO", "CARICA") and oi_carica >= 0.70):
            if self._è_nuova('AUTO_SHORT_UNLOCK'):
                capsule.append({
                    'id': 'AUTO_SHORT_UNLOCK',
                    'tipo': 'L3',
                    'motivo': f"drift={drift:+.3f}% macd={macd_hist:.1f} oracolo={oi_carica:.2f}",
                    'azione': {'type': 'sblocca_short_ranging', 'params': {}},
                    'scade_ts': ts + 300, 'vita_ore': 0.08
                })
                log.info(f"[IA_AUTO] 🔧 Capsula AUTO_SHORT_UNLOCK generata")

        # CAPSULA 3: Oracolo forte bloccato da DIFENSIVO
        if oi_stato == "FUOCO" and oi_carica >= 0.85:
            if self._è_nuova('AUTO_ORACOLO_OVERRIDE'):
                capsule.append({
                    'id': 'AUTO_ORACOLO_OVERRIDE',
                    'tipo': 'L3',
                    'motivo': f"Oracolo FUOCO carica={oi_carica:.2f}",
                    'azione': {'type': 'oracolo_override', 'params': {}},
                    'scade_ts': ts + 120, 'vita_ore': 0.03
                })
                log.info(f"[IA_AUTO] 🔥 Capsula AUTO_ORACOLO_OVERRIDE generata")

        # CAPSULA 4: LONG in mercato che scende persistentemente
        if drift < -0.05 and macd_hist < -2.0:
            if self._è_nuova('AUTO_STOP_LONG'):
                capsule.append({
                    'id': 'AUTO_STOP_LONG',
                    'tipo': 'L2',
                    'motivo': f"drift={drift:+.3f}% macd={macd_hist:.1f} — stop LONG",
                    'azione': {'type': 'blocca_long', 'params': {'durata': 300}},
                    'scade_ts': ts + 300, 'vita_ore': 0.08
                })
                log.info(f"[IA_AUTO] 🚫 Capsula AUTO_STOP_LONG generata")

        # CAPSULA 5: soglia RANGING ottimale dai dati reali
        # Calcola soglia ottimale quando Signal Tracker ha 50+ segnali
        _ranging_stats = {k:v for k,v in st_stats.items() 
                         if 'LONG' in k and 'RANGING' in k}
        for k, s in _ranging_stats.items():
            hits = s.get('hit_60', [])
            if len(hits) >= 50:
                # Calcola hit rate — se sotto 55% alza la soglia
                hit_rate = sum(hits) / len(hits)
                if hit_rate < 0.55:
                    soglia_suggerita = 54
                elif hit_rate >= 0.65:
                    soglia_suggerita = 48
                else:
                    soglia_suggerita = 51
                if self._è_nuova(f'AUTO_SOGLIA_RANGING_{soglia_suggerita}'):
                    capsule.append({
                        'id': f'AUTO_SOGLIA_RANGING_{soglia_suggerita}',
                        'tipo': 'L2',
                        'motivo': f"RANGING hit_rate={hit_rate:.0%} n={len(hits)} → soglia={soglia_suggerita}",
                        'azione': {'type': 'set_soglia_ranging', 
                                  'params': {'soglia': soglia_suggerita}},
                        'scade_ts': ts + 7200, 'vita_ore': 2
                    })
                    log.info(f"[IA_AUTO] 📊 Soglia RANGING ottimale={soglia_suggerita} da {len(hits)} campioni")
                break



        return capsule

    # =====================================================================
    # LIVELLO 2 — CAPSULE DI ESPERIENZA
    # =====================================================================

    def _analisi_l2_matrimoni(self, trades: list) -> list:
        """
        Ogni combinazione (matrimonio, regime, volatilità, direction) ha una sua firma.
        Se la firma mostra WR < soglia_gravita su N campioni → BLOCCA.
        Se mostra WR > soglia_opportunita → BOOST size.
        La soglia non è fissa: scala con la gravità del danno.
        
        FIX #16 (12mag2026): aggiunta direction al raggruppamento.
        Bug originale: MAT_TOSSICO bloccava sia LONG che SHORT.
        Risultato: SHORT castrato da capsule LEARNED senza direction.
        """
        nuove = []
        # FIX #16: Raggruppa per (matrimonio, regime, volatility, direction)
        pattern: dict = defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0, 'trades': []})
        for t in trades:
            key = (
                t.get('matrimonio', 'UNKNOWN'),
                t.get('regime', 'RANGING'),
                t.get('volatility', 'MEDIA'),
                t.get('direction', 'LONG'),  # FIX #16
            )
            pattern[key]['total'] += 1
            pattern[key]['pnl']   += t.get('pnl', 0)
            if t.get('is_win'):
                pattern[key]['wins'] += 1
            pattern[key]['trades'].append(t)

        for (mat, reg, vol, direction), s in pattern.items():
            if s['total'] < self.MIN_SAMPLES_L2:
                continue

            wr       = s['wins'] / s['total']
            pnl_avg  = s['pnl'] / s['total']
            cap_id   = f"L2_MAT_{mat}_{reg}_{vol}_{direction}"  # FIX #16: direction nell'id

            # -- GRAVITÀ: WR basso E PnL negativo → blocca -----------------
            # Soglia non fissa: più campioni → più fiducia → soglia meno severa
            fiducia = min(1.0, s['total'] / 30)  # 0 → 1 con 30 campioni
            soglia_blocco = 0.42 - (0.07 * fiducia)  # da 0.42 → 0.35 con più dati

            if wr < soglia_blocco and pnl_avg < -0.5:
                vita = self._calcola_vita_l2(wr, pnl_avg, s['total'])
                cap = self._crea_capsule_blocco(
                    cap_id, mat, reg, vol, wr, pnl_avg, s['total'], vita,
                    f"L2_MAT: {direction} WR={wr:.0%} pnl={pnl_avg:+.2f} su {s['total']} trade (fiducia={fiducia:.0%})",
                    direction=direction  # FIX #16: passo direction
                )
                if cap:
                    nuove.append(cap)
                    log.info(f"[IA] 🔴 L2_BLOCCO {mat}/{reg}/{vol}/{direction} WR={wr:.0%} pnl={pnl_avg:+.2f} vita={vita}s")

            # -- OPPORTUNITÀ: WR alto E PnL positivo → boost size ----------
            elif wr > 0.68 and pnl_avg > 2.5 and s['total'] >= 10:  # lordo > breakeven
                boost = min(1.4, 1.0 + (wr - 0.65) * 2.0)  # max +40% size
                cap = self._crea_capsule_boost(
                    cap_id, mat, reg, vol, wr, pnl_avg, s['total'], boost,
                    f"L2_OPP: {direction} WR={wr:.0%} pnl={pnl_avg:+.2f} boost={boost:.2f}x",
                    direction=direction  # FIX #16
                )
                if cap:
                    nuove.append(cap)
                    log.info(f"[IA] 🟢 L2_BOOST {mat}/{reg}/{vol}/{direction} WR={wr:.0%} boost={boost:.2f}x")

        return nuove

    def _analisi_l2_contesto(self, trades: list) -> list:
        """
        Analizza (regime, volatility, trend, direction) come firma di contesto.
        Genera capsule su contesti sistematicamente negativi/positivi.
        """
        nuove = []
        pattern: dict = defaultdict(lambda: {'wins': 0, 'total': 0, 'pnl': 0.0})
        for t in trades:
            key = (
                t.get('regime', 'RANGING'),
                t.get('volatility', 'MEDIA'),
                t.get('trend', 'SIDEWAYS'),
                t.get('direction', 'LONG'),
            )
            pattern[key]['total'] += 1
            pattern[key]['pnl']   += t.get('pnl', 0)
            if t.get('is_win'):
                pattern[key]['wins'] += 1

        for (reg, vol, trend, direction), s in pattern.items():
            if s['total'] < self.MIN_SAMPLES_L2:
                continue
            wr      = s['wins'] / s['total']
            pnl_avg = s['pnl'] / s['total']
            cap_id  = f"L2_CTX_{reg}_{vol}_{trend}_{direction}"

            if wr < 0.38 and pnl_avg < -1.0:
                vita = self._calcola_vita_l2(wr, pnl_avg, s['total'])
                # FIX #14 (12mag): aggiunto 'direction' al trigger.
                # Bug originale: la direction veniva raggruppata ma non inserita
                # nei trigger → capsule bloccavano sia LONG che SHORT.
                # Effetto pre-fix: SHORT castrato perché LEARNED LONG bloccavano anche SHORT.
                trigger = [
                    {'param': 'regime',    'op': '==', 'value': reg},
                    {'param': 'volatility','op': '==', 'value': vol},
                    {'param': 'trend_dir', 'op': '==', 'value': trend},
                    {'param': 'direction', 'op': '==', 'value': direction},  # FIX #14
                ]
                cap = {
                    'capsule_id':   cap_id,
                    'livello':      'L2',
                    'tipo':         'CONTESTO_TOSSICO',
                    'version':      1,
                    'descrizione':  f"L2_CTX: {reg}/{vol}/{trend}/{direction} WR={wr:.0%} pnl={pnl_avg:+.2f}",
                    'trigger':      trigger,
                    'azione':       {'type': 'blocca_entry', 'params': {'reason': f'CTX_TOSSICO_{reg}_{vol}_{trend}_{direction}'}},
                    'priority':     2,
                    'enabled':      True,
                    'scade_ts':     time.time() + vita,
                    'nato_ts':      time.time(),
                    'wr_al_nato':   round(wr, 3),
                    'samples':      s['total'],
                }
                if self._è_nuova(cap_id):
                    nuove.append(cap)
                    log.info(f"[IA] 🔴 L2_CTX {reg}/{vol}/{trend}/{direction} WR={wr:.0%}")

        return nuove

    def _analisi_l2_drift_regime(self, trades: list) -> list:
        """
        Il drift al momento dell'entry è la firma più potente.
        Se drift < -X% in LONG → pattern sistematicamente negativo.
        Soglia non fissa: calcolata dalla distribuzione dei drift nei LOSS.
        """
        nuove = []
        long_trades  = [t for t in trades if t.get('direction') == 'LONG' and 'drift' in t]
        short_trades = [t for t in trades if t.get('direction') == 'SHORT' and 'drift' in t]

        for direction, pool in [('LONG', long_trades), ('SHORT', short_trades)]:
            if len(pool) < self.MIN_SAMPLES_L2:
                continue

            wins  = [t for t in pool if t.get('is_win')]
            losses = [t for t in pool if not t.get('is_win')]

            if len(losses) < 3:
                continue

            # Calcola il drift medio dei loss
            drift_loss_avg = sum(t['drift'] for t in losses) / len(losses)
            drift_win_avg  = sum(t['drift'] for t in wins) / max(1, len(wins))

            # Se i loss hanno drift sistematicamente contro la direzione
            if direction == 'LONG' and drift_loss_avg < -0.05:
                # Soglia di veto = media drift loss - 1 deviazione standard
                drifts_loss = [t['drift'] for t in losses]
                std = (sum((d - drift_loss_avg)**2 for d in drifts_loss) / len(drifts_loss)) ** 0.5
                soglia_veto = drift_loss_avg + std  # più permissivo della media loss
                soglia_veto = min(-0.05, soglia_veto)  # mai sopra -0.05% (pavimento)

                cap_id = f"L2_DRIFT_LONG_VETO"
                if self._è_nuova(cap_id):
                    cap = {
                        'capsule_id': cap_id,
                        'livello':    'L2',
                        'tipo':       'DRIFT_VETO_ADATTIVO',
                        'version':    1,
                        'descrizione': f"L2_DRIFT: LONG con drift<{soglia_veto:+.3f}% → loss sistematici (avg={drift_loss_avg:+.3f}% vs win_avg={drift_win_avg:+.3f}%)",
                        'trigger':    [
                            {'param': 'drift_pct',  'op': '<',  'value': round(soglia_veto, 4)},
                            {'param': 'direction',  'op': '==', 'value': 'LONG'},
                        ],
                        'azione':     {'type': 'blocca_entry', 'params': {'reason': f'DRIFT_VETO_ADATTIVO_{soglia_veto:+.3f}'}},
                        'priority':   1,
                        'enabled':    True,
                        'scade_ts':   time.time() + 7200,  # 2 ore
                        'nato_ts':    time.time(),
                        'soglia_calcolata': round(soglia_veto, 4),
                        'samples':    len(pool),
                    }
                    nuove.append(cap)
                    log.info(f"[IA] 🧭 L2_DRIFT_VETO LONG: soglia adattiva={soglia_veto:+.3f}% (media loss={drift_loss_avg:+.3f}%)")

        return nuove

    # =====================================================================
    # LIVELLO 3 — CAPSULE DI EVENTO
    # =====================================================================

    def _analisi_l3_loss_streak(self, trades: list) -> list:
        """
        Loss streak → capsule evento che alza la soglia proporzionalmente.
        Non blocca. Non fissa un numero. Misura la GRAVITÀ dei loss.
        """
        nuove = []
        recenti = list(trades)[-10:]
        if len(recenti) < 3:
            return nuove

        streak = 0
        danno_totale = 0.0
        for t in reversed(recenti):
            if not t.get('is_win'):
                streak += 1
                danno_totale += abs(t.get('pnl', 0))
            else:
                break

        if streak < 2:
            return nuove

        # Gravità proporzionale al danno reale, non al numero di loss
        danno_per_loss = danno_totale / streak
        if danno_per_loss < 1.0:
            return nuove  # loss minuscoli, non reagire

        # Boost soglia proporzionale alla gravità
        gravita = min(1.0, danno_totale / 20.0)  # 0→1 con $20 di danno
        boost_soglia = round(3.0 + gravita * 7.0, 1)  # +3 → +10 punti soglia
        vita = int(60 + gravita * 240)  # 1min → 5min di vita

        cap_id = f"L3_STREAK_{streak}"
        if self._è_nuova(cap_id):
            cap = {
                'capsule_id': cap_id,
                'livello':    'L3',
                'tipo':       'LOSS_STREAK_EVENTO',
                'version':    1,
                'descrizione': f"L3_STREAK: {streak} loss consecutivi ${danno_totale:.1f} danno → soglia +{boost_soglia:.0f}pt per {vita}s",
                'trigger':    [],  # sempre attiva mentre esiste
                'azione':     {'type': 'boost_soglia', 'params': {'delta': boost_soglia, 'reason': f'STREAK_{streak}_${danno_totale:.0f}'}},
                'priority':   1,
                'enabled':    True,
                'scade_ts':   time.time() + vita,
                'nato_ts':    time.time(),
                'streak':     streak,
                'danno':      round(danno_totale, 2),
            }
            nuove.append(cap)
            log.info(f"[IA] ⚡ L3_STREAK {streak}x ${danno_totale:.1f} → soglia+{boost_soglia:.0f} per {vita}s")

        return nuove

    def _analisi_l3_regime_tossico(self, trades: list) -> list:
        """
        Se il regime corrente sta sistematicamente perdendo ADESSO
        (ultimi 10 trade nello stesso regime+direction) → capsule evento.
        
        FIX #14/15 (12mag2026):
          - Raggruppa per (regime, direction) invece di solo regime
          - Soglia minima 10 trade (era MIN_SAMPLES_L3 = 3 → troppo aggressivo)
          - Genera capsula con direction nel trigger (era bloccava anche SHORT)
        Bug originale: AUTO_REGIME_TOSSICO_RANGING bloccava TUTTO in RANGING
        su 3-6 samples. Eliminato col reset 12mag.
        """
        nuove = []
        recenti = list(trades)[-12:]
        if len(recenti) < 5:
            return nuove

        # FIX #14: Raggruppa per (regime, direction)
        per_regime_dir: dict = defaultdict(list)
        for t in recenti:
            key = (t.get('regime', 'RANGING'), t.get('direction', 'LONG'))
            per_regime_dir[key].append(t)

        for (regime, direction), pool in per_regime_dir.items():
            # FIX #15: minimo 10 trade (non 3) per generare blocco regime
            if len(pool) < 10:
                continue
            wins   = sum(1 for t in pool if t.get('is_win'))
            wr     = wins / len(pool)
            pnl    = sum(t.get('pnl', 0) for t in pool)

            if wr <= 0.25 and pnl < -2.0:
                gravita = min(1.0, abs(pnl) / 10.0)
                vita    = int(120 + gravita * 360)  # 2min → 8min
                cap_id  = f"L3_REGIME_{regime}_{direction}_TOSSICO"
                if self._è_nuova(cap_id):
                    cap = {
                        'capsule_id': cap_id,
                        'livello':    'L3',
                        'tipo':       'REGIME_TOSSICO_EVENTO',
                        'version':    1,
                        'descrizione': f"L3_REGIME: {regime}/{direction} WR={wr:.0%} pnl={pnl:+.2f} su {len(pool)} trade recenti",
                        'trigger':    [
                            {'param': 'regime',    'op': '==', 'value': regime},
                            {'param': 'direction', 'op': '==', 'value': direction},  # FIX #14
                        ],
                        'azione':     {'type': 'blocca_entry', 'params': {'reason': f'REGIME_TOSSICO_{regime}_{direction}_WR{wr:.0%}'}},
                        'priority':   2,
                        'enabled':    True,
                        'scade_ts':   time.time() + vita,
                        'nato_ts':    time.time(),
                        'wr_snapshot': round(wr, 3),
                    }
                    nuove.append(cap)
                    log.info(f"[IA] 🔴 L3_REGIME {regime}/{direction} tossico WR={wr:.0%} pnl={pnl:+.2f} vita={vita}s")

        return nuove

    def _analisi_l3_opportunita(self, trades: list) -> list:
        """
        Se gli ultimi trade in un contesto stanno vincendo forte
        → capsule evento di boost temporaneo.
        """
        nuove = []
        recenti = list(trades)[-8:]
        if len(recenti) < 3:
            return nuove

        per_regime: dict = defaultdict(list)
        for t in recenti:
            per_regime[t.get('regime', 'RANGING')].append(t)

        for regime, pool in per_regime.items():
            if len(pool) < self.MIN_SAMPLES_L3:
                continue
            wins   = sum(1 for t in pool if t.get('is_win'))
            wr     = wins / len(pool)
            pnl    = sum(t.get('pnl', 0) for t in pool)
            pnl_avg = pnl / len(pool)

            if wr >= 0.75 and pnl_avg > 2.5:  # lordo > breakeven
                boost = min(1.3, 1.0 + (wr - 0.70) * 1.5)
                vita  = int(90 + (wr - 0.70) * 600)  # 90s → 4min
                cap_id = f"L3_OPP_{regime}_BOOST"
                if self._è_nuova(cap_id):
                    cap = {
                        'capsule_id': cap_id,
                        'livello':    'L3',
                        'tipo':       'OPPORTUNITA_EVENTO',
                        'version':    1,
                        'descrizione': f"L3_OPP: {regime} WR={wr:.0%} pnl_avg={pnl_avg:+.2f} → boost {boost:.2f}x",
                        'trigger':    [{'param': 'regime', 'op': '==', 'value': regime}],
                        'azione':     {'type': 'modifica_size', 'params': {'mult': boost, 'reason': f'OPP_{regime}'}},
                        'priority':   3,
                        'enabled':    True,
                        'scade_ts':   time.time() + vita,
                        'nato_ts':    time.time(),
                        'wr_snapshot': round(wr, 3),
                    }
                    nuove.append(cap)
                    log.info(f"[IA] 🟢 L3_OPP {regime} WR={wr:.0%} → boost {boost:.2f}x vita={vita}s")

        return nuove

    # =====================================================================
    # SUPPORTO
    # =====================================================================

    def _calcola_vita_l2(self, wr: float, pnl_avg: float, samples: int) -> int:
        """
        La vita di una capsule L2 scala con la gravità del problema.
        Più è grave → più dura. Non è un parametro fisso.
        """
        gravita_wr  = max(0.0, 0.45 - wr) / 0.45       # 0→1
        gravita_pnl = min(1.0, abs(pnl_avg) / 5.0)     # 0→1 con $5 avg loss
        gravita_n   = min(1.0, samples / 30.0)          # 0→1 con 30 campioni
        gravita     = (gravita_wr * 0.4 + gravita_pnl * 0.4 + gravita_n * 0.2)
        # Vita: da 30 min (bassa gravità) a 12 ore (altissima)
        return int(1800 + gravita * 41400)

    def _è_nuova(self, cap_id: str) -> bool:
        """Evita di ricreare capsule già attive nel file."""
        try:
            if not os.path.exists(self.capsule_file):
                return True
            with open(self.capsule_file) as f:
                existing = json.load(f)
            # Controlla se esiste già una capsule attiva con stesso id
            for c in existing:
                if c.get('capsule_id') == cap_id and c.get('enabled'):
                    # Aggiorna scade_ts se è più vecchia
                    return False
            return True
        except Exception:
            return True

    def _crea_capsule_blocco(self, cap_id, mat, reg, vol, wr, pnl_avg, samples, vita, desc, direction='LONG') -> dict | None:
        """FIX #16: ora accetta direction obbligatoria nel trigger."""
        if not self._è_nuova(cap_id):
            return None
        return {
            'capsule_id':  cap_id,
            'livello':     'L2',
            'tipo':        'MATRIMONIO_TOSSICO',
            'version':     1,
            'descrizione': desc,
            'trigger':     [
                {'param': 'matrimonio', 'op': '==', 'value': mat},
                {'param': 'regime',     'op': '==', 'value': reg},
                {'param': 'volatility', 'op': '==', 'value': vol},
                {'param': 'direction',  'op': '==', 'value': direction},  # FIX #16
            ],
            'azione':      {'type': 'blocca_entry', 'params': {'reason': f'L2_TOSSICO_{mat}_{reg}_{vol}_{direction}'}},
            'priority':    2,
            'enabled':     True,
            'scade_ts':    time.time() + vita,
            'nato_ts':     time.time(),
            'wr_al_nato':  round(wr, 3),
            'pnl_avg':     round(pnl_avg, 2),
            'samples':     samples,
        }

    def _crea_capsule_boost(self, cap_id, mat, reg, vol, wr, pnl_avg, samples, boost, desc, direction='LONG') -> dict | None:
        """FIX #16: ora accetta direction obbligatoria nel trigger."""
        if not self._è_nuova(cap_id):
            return None
        return {
            'capsule_id':  cap_id,
            'livello':     'L2',
            'tipo':        'MATRIMONIO_OPPORTUNITA',
            'version':     1,
            'descrizione': desc,
            'trigger':     [
                {'param': 'matrimonio', 'op': '==', 'value': mat},
                {'param': 'regime',     'op': '==', 'value': reg},
                {'param': 'volatility', 'op': '==', 'value': vol},
                {'param': 'direction',  'op': '==', 'value': direction},  # FIX #16
            ],
            'azione':      {'type': 'modifica_size', 'params': {'mult': boost, 'reason': f'L2_OPP_{mat}_{direction}'}},
            'priority':    3,
            'enabled':     True,
            'scade_ts':    time.time() + 14400,  # 4 ore
            'nato_ts':     time.time(),
            'wr_al_nato':  round(wr, 3),
            'pnl_avg':     round(pnl_avg, 2),
            'samples':     samples,
        }

    def _pulisci_scadute(self):
        """Rimuove capsule scadute dal file. Zero intervento umano."""
        try:
            if not os.path.exists(self.capsule_file):
                return
            with open(self.capsule_file) as f:
                existing = json.load(f)
            ora = time.time()
            # Tieni: strutturali (no scade_ts) + non scadute
            attive    = [c for c in existing if 'scade_ts' not in c or c['scade_ts'] > ora]
            scadute   = [c for c in existing if 'scade_ts' in c and c['scade_ts'] <= ora]
            if scadute:
                with open(self.capsule_file, 'w') as f:
                    json.dump(attive, f, indent=2)
                for c in scadute:
                    log.info(f"[IA] 🗑️ Capsule scaduta rimossa: {c['capsule_id']} (era {c.get('tipo','?')})")
        except Exception as e:
            log.error(f"[IA] Errore pulizia capsule: {e}")

    def _persisti(self, nuove: list):
        """Scrive nuove capsule nel file. Hot-reload le carica automaticamente."""
        try:
            existing = []
            if os.path.exists(self.capsule_file):
                with open(self.capsule_file) as f:
                    existing = json.load(f)
            existing_ids = {c.get('capsule_id') for c in existing}
            da_aggiungere = [c for c in nuove if c['capsule_id'] not in existing_ids]
            existing.extend(da_aggiungere)
            with open(self.capsule_file, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            log.error(f"[IA] Errore persistenza: {e}")

    def get_stats(self) -> dict:
        """Esposto al heartbeat per monitoraggio dashboard."""
        try:
            if not os.path.exists(self.capsule_file):
                return {'attive': 0, 'l2': 0, 'l3': 0, 'scadono_presto': 0}
            with open(self.capsule_file) as f:
                caps = json.load(f)
            ora  = time.time()
            l2   = [c for c in caps if c.get('livello') == 'L2' and c.get('enabled')]
            l3   = [c for c in caps if c.get('livello') == 'L3' and c.get('enabled')]
            presto = [c for c in caps if 'scade_ts' in c and 0 < c['scade_ts'] - ora < 300]
            return {
                'attive':        len([c for c in caps if c.get('enabled')]),
                'l2':            len(l2),
                'l3':            len(l3),
                'scadono_presto': len(presto),
                'trade_osservati': self._trade_count,
            }
        except Exception:
            return {'attive': 0, 'l2': 0, 'l3': 0, 'scadono_presto': 0}


# Alias per compatibilità con il codice esistente
RealtimeLearningEngine = IntelligenzaAutonoma

# ===========================================================================
# CAPSULE INTELLIGENTE — Sistema Immunitario Predittivo
# ===========================================================================

class CapsuleIntelligente:
    """
    Sistema immunitario predittivo del bot.

    NON reagisce al regime dichiarato — ANTICIPA il cambiamento leggendo
    i precursori: breath, nervosismo, comparto, OI, drift, regime, momentum.

    QUATTRO LIVELLI:
      P1 - Predittivo:  legge precursori → prepara capsule PRIMA del cambio
      P2 - Adattivo:    genera il set capsule giusto per QUESTO momento
      P3 - Pervasivo:   ogni punto di decisione (entry/exit/size/soglia) sente le capsule
      P4 - Evolutivo:   le capsule imparano dal Phantom → si rafforzano o muoiono

    INTERFACCIA con il bot:
      tick(ctx)         → aggiorna stato predittivo ad ogni tick
      get_entry_mods()  → modifche da applicare all'entry (soglia delta, size mult, veto)
      get_exit_signal() → segnale anticipato di uscita
      get_narrative()   → testo vivo per la dashboard
      register_outcome(capsule_id, was_win, pnl) → apprendimento post-trade
    """

    # Soglie precursori
    NERV_RAIN_SOGLIA   = 0.60   # nervosismo sopra = RAIN in arrivo
    NERV_SLICK_SOGLIA  = 0.25   # nervosismo sotto = pista asciutta
    BREATH_ESALAZ      = "ESALAZIONE"
    BREATH_INALAZ      = "INALAZIONE"
    BREATH_PICCO       = "PICCO"
    OI_FUOCO_MIN       = 0.75   # OI FUOCO da questa carica in su

    def __init__(self):
        self._stato_predittivo = "NEUTRO"   # ALLERTA / OFFENSIVO / DIFENSIVO / NEUTRO
        self._capsule_attive: dict = {}      # {capsule_id: capsula_dict}
        self._storia: list = []              # log ultimi 20 eventi per narrativa
        self._outcome_memory: dict = {}      # {capsule_id: {'wins':0,'losses':0,'pnl':0}}
        self._tick_count = 0
        self._ultimo_cambio_stato = 0.0
        self._precursori_storia: list = []   # rolling 10 tick per trend precursori
        self._ctx_prev = {}

    # ── TICK PRINCIPALE ──────────────────────────────────────────────────

    def tick(self, ctx: dict):
        """
        Chiamato ad ogni tick del bot.
        ctx contiene: breath_fase, breath_energia, nervosismo, gomme,
                      comparto, oi_stato, oi_carica, regime, drift,
                      momentum, volatility, trend, sc_pesi, phantom_stats
        """
        self._tick_count += 1
        try:
            # 1. Leggi precursori
            precursori = self._leggi_precursori(ctx)

            # 2. Aggiorna storico precursori (rolling 10)
            self._precursori_storia.append(precursori)
            if len(self._precursori_storia) > 10:
                self._precursori_storia.pop(0)

            # 3. Calcola stato predittivo
            nuovo_stato = self._calcola_stato_predittivo(precursori)

            # 4. Se cambio di stato → genera capsule appropriate
            if nuovo_stato != self._stato_predittivo:
                self._on_cambio_stato(self._stato_predittivo, nuovo_stato, precursori, ctx)
                self._stato_predittivo = nuovo_stato
                self._ultimo_cambio_stato = time.time()

            # 5. Aggiorna capsule esistenti (vita, rinforzo)
            self._aggiorna_capsule_attive(precursori, ctx)

            # 6. Verifica se creare capsule di opportunità
            self._verifica_opportunita(precursori, ctx)

            # 7. Capsule di contesto persistente — attive anche senza cambio stato
            self._verifica_contesto_persistente(precursori, ctx)

            # 8. Apprendimento dal Phantom
            if self._tick_count % 50 == 0:
                self._apprendi_dal_phantom(ctx.get('phantom_stats', {}))

        except Exception as e:
            log.error(f"[CI_TICK] {e}")

        self._ctx_prev = ctx.copy()

    # ── LETTURA PRECURSORI ────────────────────────────────────────────────

    def _leggi_precursori(self, ctx: dict) -> dict:
        """Trasforma il contesto grezzo in segnali predittivi interpretati."""
        nerv         = ctx.get('nervosismo', 0.3)
        breath_fase  = ctx.get('breath_fase', 'NEUTRO')
        breath_en    = ctx.get('breath_energia', 0.5)
        comparto     = ctx.get('comparto', 'NEUTRO')
        oi_stato     = ctx.get('oi_stato', 'ATTESA')
        oi_carica    = ctx.get('oi_carica', 0.0)
        regime       = ctx.get('regime', 'RANGING')
        drift        = ctx.get('drift', 0.0)
        gomme        = ctx.get('gomme', 'INTER')

        # Segnale RAIN in arrivo: nervosismo sale + breath in esalazione
        rain_precursore = (nerv > self.NERV_RAIN_SOGLIA * 0.8 and
                           breath_fase in (self.BREATH_ESALAZ, self.BREATH_PICCO))

        # Segnale ATTACCO: OI FUOCO + breath in inalazione + comparto offensivo
        attacco_precursore = (oi_carica >= self.OI_FUOCO_MIN and
                               breath_fase == self.BREATH_INALAZ and
                               comparto in ('ATTACCO', 'TRENDING_BULL'))

        # Segnale DIFESA: nervosismo alto + drift negativo + comparto difensivo
        difesa_precursore = (nerv > self.NERV_RAIN_SOGLIA and
                              drift < -0.02 and
                              comparto in ('DIFENSIVO', 'NEUTRO'))

        # Trend nervosismo: sta salendo o scendendo?
        nerv_trend = 0.0
        if len(self._precursori_storia) >= 3:
            nerv_storia = [p.get('nervosismo', 0.3) for p in self._precursori_storia[-3:]]
            nerv_trend = nerv_storia[-1] - nerv_storia[0]

        return {
            'nervosismo':       nerv,
            'breath_fase':      breath_fase,
            'breath_energia':   breath_en,
            'comparto':         comparto,
            'oi_stato':         oi_stato,
            'oi_carica':        oi_carica,
            'regime':           regime,
            'drift':            drift,
            'gomme':            gomme,
            'rain_precursore':  rain_precursore,
            'attacco_precursore': attacco_precursore,
            'difesa_precursore':  difesa_precursore,
            'nerv_trend':       round(nerv_trend, 3),
        }

    # ── STATO PREDITTIVO ─────────────────────────────────────────────────

    def _calcola_stato_predittivo(self, prec: dict) -> str:
        """
        Determina lo stato predittivo del sistema PRIMA che il mercato si dichiari.
        Priorità: ALLERTA > DIFENSIVO > OFFENSIVO > NEUTRO
        """
        # ALLERTA: RAIN imminente — segnali multipli concordi
        if prec['rain_precursore'] and prec['nerv_trend'] > 0.05:
            return "ALLERTA"

        # DIFENSIVO: drift negativo + nervosismo alto
        if prec['difesa_precursore']:
            return "DIFENSIVO"

        # OFFENSIVO: tutto verde — OI carico, breath in inalazione, comparto attacco
        if prec['attacco_precursore'] and prec['drift'] > 0:
            return "OFFENSIVO"

        # NEUTRO: condizioni miste
        return "NEUTRO"

    # ── CAMBIO DI STATO ──────────────────────────────────────────────────

    def _on_cambio_stato(self, stato_da: str, stato_a: str, prec: dict, ctx: dict):
        """Quando lo stato predittivo cambia → genera le capsule appropriate."""
        ts = time.time()
        evento = f"{stato_da}→{stato_a}"

        if stato_a == "ALLERTA":
            # RAIN in arrivo — capsule difensive immediate
            self._attiva_capsula("CI_RAIN_SIZE", {
                'id': 'CI_RAIN_SIZE', 'tipo': 'DIFENSIVO',
                'azione': 'RIDUCI_SIZE', 'params': {'mult': 0.20},
                'motivo': f"RAIN precursore: nerv={prec['nervosismo']:.0%} breath={prec['breath_fase']}",
                'vita': 600, 'ts_nato': ts, 'forza': 0.8,
            })
            self._attiva_capsula("CI_RAIN_SOGLIA", {
                'id': 'CI_RAIN_SOGLIA', 'tipo': 'DIFENSIVO',
                'azione': 'ALZA_SOGLIA', 'params': {'delta': +10},
                'motivo': f"RAIN precursore: soglia alzata preventivamente",
                'vita': 600, 'ts_nato': ts, 'forza': 0.7,
            })
            self._log_evento(f"🌧 ALLERTA RAIN — 2 capsule difensive attivate PRIMA del cambio regime")

        elif stato_a == "OFFENSIVO":
            # Siamo pronti ad attaccare — capsule offensive
            self._attiva_capsula("CI_ATTACCO_SOGLIA", {
                'id': 'CI_ATTACCO_SOGLIA', 'tipo': 'OFFENSIVO',
                'azione': 'ABBASSA_SOGLIA', 'params': {'delta': -5},
                'motivo': f"OI FUOCO carica={prec['oi_carica']:.2f} + breath={prec['breath_fase']}",
                'vita': 300, 'ts_nato': ts, 'forza': 0.75,
            })
            self._attiva_capsula("CI_ATTACCO_SIZE", {
                'id': 'CI_ATTACCO_SIZE', 'tipo': 'OFFENSIVO',
                'azione': 'BOOST_SIZE', 'params': {'mult': 1.25},
                'motivo': f"Comparto={prec['comparto']} + OI carica alta",
                'vita': 300, 'ts_nato': ts, 'forza': 0.65,
            })
            self._log_evento(f"🚀 OFFENSIVO — 2 capsule offensive. OI={prec['oi_carica']:.2f} comparto={prec['comparto']}")

        elif stato_a == "DIFENSIVO":
            self._attiva_capsula("CI_DIFESA_SOGLIA", {
                'id': 'CI_DIFESA_SOGLIA', 'tipo': 'DIFENSIVO',
                'azione': 'ALZA_SOGLIA', 'params': {'delta': +7},
                'motivo': f"drift={prec['drift']:.3f} + nerv={prec['nervosismo']:.0%}",
                'vita': 480, 'ts_nato': ts, 'forza': 0.6,
            })
            self._log_evento(f"🛡 DIFENSIVO — soglia alzata +7. drift={prec['drift']:.3f}")

        elif stato_a == "NEUTRO":
            # Ritorno al neutro — rimuovi capsule più aggressive
            self._disattiva_tipo("ALLERTA")
            self._log_evento(f"⚖️ NEUTRO — capsule di allerta rimosse. Mercato si stabilizza.")

    # ── CAPSULE OPPORTUNITÀ ───────────────────────────────────────────────

    def _verifica_opportunita(self, prec: dict, ctx: dict):
        """
        Controlla se esistono condizioni di opportunità non ancora capsulate.
        Genera capsule di boost se tutto è allineato.
        """
        # Allineamento perfetto: FUOCO + INALAZIONE + SLICK + ATTACCO
        if (prec['oi_carica'] >= 0.85 and
            prec['breath_fase'] == self.BREATH_INALAZ and
            prec['gomme'] == 'SLICK' and
            prec['comparto'] in ('ATTACCO', 'TRENDING_BULL') and
            'CI_PERFETTO' not in self._capsule_attive):

            self._attiva_capsula("CI_PERFETTO", {
                'id': 'CI_PERFETTO', 'tipo': 'OPPORTUNITA',
                'azione': 'BOOST_SIZE', 'params': {'mult': 1.40},
                'motivo': f"Allineamento PERFETTO: OI={prec['oi_carica']:.2f} SLICK INALAZIONE ATTACCO",
                'vita': 180, 'ts_nato': time.time(), 'forza': 0.95,
            })
            self._log_evento(f"⚡ ALLINEAMENTO PERFETTO — capsula boost max attivata")

    def _verifica_contesto_persistente(self, prec: dict, ctx: dict):
        """
        Capsule attive anche senza cambio di stato — leggono il contesto
        accumulato nel tempo, non solo i transitori.

        CAPSULA 1 — RANGING_EDGE:
        Signal Tracker ha hit_rate > 0.55 su 200+ osservazioni in RANGING
        → abbassa soglia di 8 punti per catturare l'edge statistico.

        CAPSULA 2 — FUOCO_WINDOW:
        OI ha toccato FUOCO nell'ultimo minuto (anche se ora è sceso a CARICA)
        → apre finestra di 30s con soglia abbassata di 6 punti.
        """
        ts = time.time()
        signal_top = ctx.get('signal_top', [])
        oi_stato   = prec.get('oi_stato', 'ATTESA')
        oi_carica  = prec.get('oi_carica', 0.0)
        regime     = prec.get('regime', 'RANGING')

        # ── CAPSULA 1: RANGING_EDGE ─────────────────────────────────────────
        # Signal Tracker dice che in RANGING c'è un edge reale > 55%
        ranging_hit = 0.0
        ranging_n   = 0
        for st in signal_top:
            if 'RANGING' in st.get('context', '') and st.get('n', 0) >= 200:
                ranging_hit = st.get('hit_60s', 0.0)
                ranging_n   = st.get('n', 0)
                break

        if (ranging_hit >= 0.55 and ranging_n >= 200 and
            regime == 'RANGING' and
            'CI_RANGING_EDGE' not in self._capsule_attive):
            self._attiva_capsula("CI_RANGING_EDGE", {
                'id': 'CI_RANGING_EDGE', 'tipo': 'OPPORTUNITA',
                'azione': 'ABBASSA_SOGLIA', 'params': {'delta': -8},
                'motivo': f"RANGING edge: hit={ranging_hit:.0%} n={ranging_n} — soglia abbassata",
                'vita': 300, 'ts_nato': ts, 'forza': min(1.0, (ranging_hit - 0.55) * 10 + 0.5),
            })
            self._log_evento(f"📊 RANGING_EDGE — hit={ranging_hit:.0%} n={ranging_n} → soglia -8")

        elif ranging_hit < 0.52 and 'CI_RANGING_EDGE' in self._capsule_attive:
            # Edge è svanito — rimuovi la capsula
            self._capsule_attive.pop('CI_RANGING_EDGE', None)
            self._log_evento(f"📊 RANGING_EDGE rimossa — hit sceso a {ranging_hit:.0%}")

        # ── CAPSULA 2: FUOCO_WINDOW ─────────────────────────────────────────
        # Traccia l'ultimo momento in cui OI era FUOCO
        if oi_stato == 'FUOCO' and oi_carica >= 0.80:
            self._ultimo_fuoco_ts = ts

        # Se FUOCO è stato visto negli ultimi 45 secondi → finestra aperta
        _ultimo_fuoco = getattr(self, '_ultimo_fuoco_ts', 0.0)
        _eta_fuoco    = ts - _ultimo_fuoco
        if (_eta_fuoco < 45 and _ultimo_fuoco > 0 and
            'CI_FUOCO_WINDOW' not in self._capsule_attive and
            regime == 'RANGING'):
            self._attiva_capsula("CI_FUOCO_WINDOW", {
                'id': 'CI_FUOCO_WINDOW', 'tipo': 'OPPORTUNITA',
                'azione': 'ABBASSA_SOGLIA', 'params': {'delta': -6},
                'motivo': f"FUOCO visto {_eta_fuoco:.0f}s fa — finestra entry aperta",
                'vita': 45, 'ts_nato': ts, 'forza': max(0.3, 1.0 - _eta_fuoco/45),
            })
            self._log_evento(f"🔥 FUOCO_WINDOW aperta — {_eta_fuoco:.0f}s dal FUOCO")

        # ── CAPSULA 3: OI_ESTREMO ────────────────────────────────────────────
        # OI >= 0.95 è una dichiarazione fisica del mercato — non un'opinione.
        # Quando il mercato urla così forte, il VETO storico diventa irrilevante.
        # Questa capsula bypassa il requisito hit_rate >= 0.63 nel CI_OVERRIDE.
        _oi_estremo = oi_carica >= 0.95 and oi_stato == 'FUOCO'
        if _oi_estremo and 'CI_OI_ESTREMO' not in self._capsule_attive:
            self._attiva_capsula("CI_OI_ESTREMO", {
                'id': 'CI_OI_ESTREMO', 'tipo': 'OPPORTUNITA',
                'azione': 'ABBASSA_SOGLIA', 'params': {'delta': -12},
                'motivo': f"OI ESTREMO carica={oi_carica:.3f} — dichiarazione fisica del mercato",
                'vita': 60, 'ts_nato': ts, 'forza': min(1.0, (oi_carica - 0.95) * 20 + 0.5),
            })
            self._log_evento(f"⚡ OI_ESTREMO — carica={oi_carica:.3f} soglia -12 — il mercato urla")
        elif not _oi_estremo and 'CI_OI_ESTREMO' in self._capsule_attive:
            self._capsule_attive.pop('CI_OI_ESTREMO', None)
            self._log_evento(f"⚡ OI_ESTREMO rimossa — carica scesa a {oi_carica:.3f}")



    # ── AGGIORNAMENTO CAPSULE ─────────────────────────────────────────────

    def _aggiorna_capsule_attive(self, prec: dict, ctx: dict):
        """Rimuove capsule scadute, aggiorna forza in base a contesto corrente."""
        ts = time.time()
        scadute = []
        for cid, cap in self._capsule_attive.items():
            # Verifica scadenza
            eta = ts - cap.get('ts_nato', ts)
            if eta > cap.get('vita', 300):
                scadute.append(cid)
                continue
            # Rafforza capsule DIFENSIVE se il nervosismo continua a salire
            if cap['tipo'] == 'DIFENSIVO' and prec['nerv_trend'] > 0.03:
                cap['forza'] = min(1.0, cap['forza'] + 0.05)
            # Indebolisce capsule OFFENSIVE se il breath cade
            if cap['tipo'] == 'OFFENSIVO' and prec['breath_fase'] == self.BREATH_ESALAZ:
                cap['forza'] = max(0.0, cap['forza'] - 0.10)
                if cap['forza'] < 0.2:
                    scadute.append(cid)

        for cid in scadute:
            self._capsule_attive.pop(cid, None)
            self._log_evento(f"💊 Capsula {cid} scaduta/rimossa")

    # ── APPRENDIMENTO DAL PHANTOM ─────────────────────────────────────────

    def _apprendi_dal_phantom(self, phantom_stats: dict):
        """
        Le capsule guardano il Phantom e imparano.
        Se una capsula ha bloccato trade che il Phantom dice erano vincenti → si indebolisce.
        Se ha bloccato perdenti → si rinforza.
        """
        for cid, cap in list(self._capsule_attive.items()):
            mem = self._outcome_memory.get(cid, {})
            wins   = mem.get('wins', 0)
            losses = mem.get('losses', 0)
            tot = wins + losses
            if tot < 5:
                continue
            wr = wins / tot
            # Capsula difensiva che blocca troppi vincenti → indeboliscila
            if cap['tipo'] == 'DIFENSIVO' and wr > 0.55:
                cap['forza'] = max(0.1, cap['forza'] - 0.15)
                self._log_evento(f"📉 {cid} indebolita — stava bloccando troppi vincenti (wr={wr:.0%})")
            # Capsula difensiva che blocca perdenti → rinforza
            elif cap['tipo'] == 'DIFENSIVO' and wr < 0.30:
                cap['forza'] = min(1.0, cap['forza'] + 0.10)
                self._log_evento(f"📈 {cid} rafforzata — sta bloccando perdenti (wr={wr:.0%})")

    def register_outcome(self, capsule_id: str, was_win: bool, pnl: float):
        """Registra l'esito di un trade bloccato/modificato da una capsula."""
        if capsule_id not in self._outcome_memory:
            self._outcome_memory[capsule_id] = {'wins': 0, 'losses': 0, 'pnl': 0.0}
        mem = self._outcome_memory[capsule_id]
        if was_win:
            mem['wins'] += 1
        else:
            mem['losses'] += 1
        mem['pnl'] += pnl

    # ── INTERFACCIA CON IL BOT ────────────────────────────────────────────

    def get_entry_mods(self) -> dict:
        """
        Ritorna le modifiche da applicare all'entry.
        Chiamato da _evaluate_shadow_entry prima di ogni decisione.
        """
        soglia_delta = 0.0
        size_mult    = 1.0
        veto         = None
        motivi       = []

        for cid, cap in self._capsule_attive.items():
            forza = cap.get('forza', 0.5)
            az    = cap.get('azione', '')
            par   = cap.get('params', {})

            if az == 'ALZA_SOGLIA':
                delta = par.get('delta', 0) * forza
                soglia_delta += delta
                motivi.append(f"{cid}:+{delta:.1f}")

            elif az == 'ABBASSA_SOGLIA':
                delta = par.get('delta', 0) * forza
                soglia_delta += delta
                motivi.append(f"{cid}:{delta:.1f}")

            elif az == 'RIDUCI_SIZE':
                size_mult = min(size_mult, par.get('mult', 1.0) + (1.0 - par.get('mult', 1.0)) * (1 - forza))
                motivi.append(f"{cid}:size={size_mult:.2f}")

            elif az == 'BOOST_SIZE':
                size_mult = max(size_mult, 1.0 + (par.get('mult', 1.0) - 1.0) * forza)
                motivi.append(f"{cid}:size={size_mult:.2f}")

        return {
            'soglia_delta': round(soglia_delta, 2),
            'size_mult':    round(size_mult, 3),
            'veto':         veto,
            'motivi':       motivi,
            'n_capsule':    len(self._capsule_attive),
            'stato':        self._stato_predittivo,
        }

    def get_exit_signal(self, ctx: dict) -> dict:
        """
        Segnale anticipato di uscita dalla posizione.
        Ritorna {'esci': bool, 'motivo': str}
        """
        prec = self._leggi_precursori(ctx)
        # Uscita anticipata se ALLERTA mentre siamo long + breath in esalazione + nerv esplode
        if (self._stato_predittivo == "ALLERTA" and
            prec['breath_fase'] == self.BREATH_ESALAZ and
            prec['nervosismo'] > 0.70):
            return {'esci': True, 'motivo': f"CI_EXIT_ANTICIPATA: ALLERTA+ESALAZ+nerv={prec['nervosismo']:.0%}"}
        return {'esci': False, 'motivo': ''}

    # ── NARRATIVA VIVA ───────────────────────────────────────────────────

    def get_narrative(self) -> list:
        """
        Ritorna la storia viva delle ultime decisioni per la dashboard.
        Non numeri — frasi che raccontano cosa sta succedendo.
        """
        return list(self._storia)

    def get_status_dashboard(self) -> dict:
        """Snapshot completo per heartbeat dashboard."""
        caps_list = []
        for cid, cap in self._capsule_attive.items():
            eta = time.time() - cap.get('ts_nato', time.time())
            vita_rim = max(0, cap.get('vita', 300) - eta)
            caps_list.append({
                'id':      cid,
                'tipo':    cap.get('tipo', '?'),
                'azione':  cap.get('azione', '?'),
                'motivo':  cap.get('motivo', ''),
                'forza':   round(cap.get('forza', 0), 2),
                'ttl':     round(vita_rim),
            })
        return {
            'stato':           self._stato_predittivo,
            'capsule_attive':  caps_list,
            'n_capsule':       len(self._capsule_attive),
            'storia':          self._storia[-8:],
            'tick':            self._tick_count,
        }

    # ── HELPER INTERNI ────────────────────────────────────────────────────

    def _attiva_capsula(self, cid: str, cap: dict):
        self._capsule_attive[cid] = cap

    def _disattiva_tipo(self, tipo: str):
        da_rimuovere = [cid for cid, c in self._capsule_attive.items() if c.get('tipo') == tipo]
        for cid in da_rimuovere:
            self._capsule_attive.pop(cid, None)

    def _log_evento(self, msg: str):
        ts_str = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"[{ts_str}] {msg}"
        self._storia.append(entry)
        if len(self._storia) > 20:
            self._storia.pop(0)
        log.info(f"[CI] {msg}")


# ===========================================================================
# LOG ANALYZER
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

class AIExplainer:
    """Log narrativo di ogni decisione del bot - scritto su SQLite."""

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
            conn = _safe_connect(self.db_path, timeout=30)
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
            conn = _safe_connect(self.db_path, timeout=30)
            conn.execute("""
                INSERT INTO narrative_log (timestamp, event_type, narrative, trade_data)
                VALUES (?, ?, ?, ?)
            """, (datetime.utcnow().isoformat(), event_type, narrative,
                  json.dumps(trade_data) if trade_data else None))
            conn.commit()
            conn.close()
        except Exception:
            pass

# ===========================================================================
# ★ SEED SCORER - TUA INVENZIONE
#   Valuta la forza dell'impulso prima di ogni entry.
#   4 componenti con pesi specifici → score 0.0–1.0
#   Soglia: SEED_ENTRY_THRESHOLD (default 0.45)
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
        # LOSS_STREAK_OFF (18giu, Roberto): freno a conteggio spento se richiesto.
        if (loss_streak >= 5
                and os.environ.get("LOSS_STREAK_OFF", "false").lower() != "true"):
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
    """Blocca in condizioni di alta volatilita con impulso debole."""
    def proteggi(self, momentum, volatility, fingerprint_wr, fp_minimo=0.55):
        if momentum == "DEBOLE" and volatility == "ALTA" and fingerprint_wr <= 0.70:
            return False, "PROTETTO_VOLATILITÀ"
        if volatility == "ALTA" and fingerprint_wr < fp_minimo:
            return False, "PROTETTO_FP_BASSO"
        return True, "OK"

class Capsule4Opportunita:
    """Riconosce finestre di opportunita premium."""
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

# ===========================================================================
# MATRIMONI INTELLIGENTI - 7 TIPI
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
        # ════════════════════════════════════════════════════════════════
        # PATCH 1 (16mag2026) — VOLATILITY PERCENTUALE
        # PRIMA: soglie ASSOLUTE 0.005 / 0.002 in $.
        #   Su BTC $79000 una variazione tick media è $1-5 → avg_ch20 sempre
        #   ben sopra 0.005 → volatility = "ALTA" praticamente sempre.
        #   Fingerprint Oracolo contaminato al 100% (vedi memorie 16mag).
        # ADESSO: rapporto percentuale rispetto al prezzo medio.
        #   ALTA  se avg_ch20_pct > 0.00015 (vibrazione tick > ~$12 su BTC $79k)
        #   MEDIA se avg_ch20_pct > 0.00005 (vibrazione tick > ~$4)
        #   BASSA altrimenti
        # ════════════════════════════════════════════════════════════════
        prezzo_medio_20 = sum(r20) / len(r20) if r20 else 0
        if prezzo_medio_20 > 0:
            avg_ch20_pct = avg_ch20 / prezzo_medio_20
        else:
            avg_ch20_pct = 0
        if avg_ch20_pct > 0.00015:
            volatility = "ALTA"
        elif avg_ch20_pct > 0.00005:
            volatility = "MEDIA"
        else:
            volatility = "BASSA"

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

def _calcola_soglia_da_signal_tracker(bot) -> dict:
    """
    Calcola la soglia ottimale dai dati reali del Signal Tracker.
    Usa hit_rate e PnL reale — non regime fisso.
    La soglia emerge dai dati — non è mai un numero scritto a mano.
    """
    try:
        if not hasattr(bot, 'signal_tracker'):
            return {'base': 40, 'min': 34, 'motivo': 'NO_TRACKER'}

        stats = getattr(bot.signal_tracker, '_stats', {})
        if not stats:
            return {'base': 40, 'min': 34, 'motivo': 'NO_DATA'}

        # Raccoglie tutti i contesti con abbastanza campioni
        contesti = []
        for ctx, s in stats.items():
            hits = s.get('hit_60', [])
            pnls = s.get('pnl_sim', [])
            n = len(hits)
            if n < 20:  # minimo 20 campioni
                continue
            hit_rate = sum(hits) / n
            pnl_avg  = sum(pnls) / len(pnls) if pnls else 0
            contesti.append({
                'ctx': ctx, 'n': n,
                'hit_rate': hit_rate,
                'pnl_avg': pnl_avg
            })

        if not contesti:
            return {'base': 40, 'min': 34, 'motivo': 'POCHI_DATI'}

        # Media pesata per n campioni
        tot_n    = sum(c['n'] for c in contesti)
        avg_hit  = sum(c['hit_rate'] * c['n'] for c in contesti) / tot_n
        avg_pnl  = sum(c['pnl_avg']  * c['n'] for c in contesti) / tot_n

        # Soglia proporzionale all'hit rate reale (L1 — allineata a comparti)
        # hit 65%+ → soglia 38/32  (mercato favorevole)
        # hit 60%+  → soglia 40/34
        # hit 55%+  → soglia 44/38
        # hit <55%  → soglia 48/42 (conservativo)
        if avg_hit >= 0.65 and avg_pnl > 0:
            base, min_s = 38, 32
            motivo = f"OTTIMO hit={avg_hit:.0%} pnl={avg_pnl:+.2f} n={tot_n}"
        elif avg_hit >= 0.60 and avg_pnl > 0:
            base, min_s = 40, 34
            motivo = f"BUONO hit={avg_hit:.0%} pnl={avg_pnl:+.2f} n={tot_n}"
        elif avg_hit >= 0.55:
            base, min_s = 44, 38
            motivo = f"DISCRETO hit={avg_hit:.0%} n={tot_n}"
        else:
            base, min_s = 48, 42
            motivo = f"STANDARD hit={avg_hit:.0%} n={tot_n}"

        return {'base': base, 'min': min_s, 'motivo': motivo}

    except Exception as e:
        log.error(f"[SOGLIA_DINAMICA] Errore: {e}")
        return {'base': 40, 'min': 34, 'motivo': 'ERRORE fallback'}


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
            conn = _safe_connect(self.db_path, timeout=30)
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
            # ════════════════════════════════════════════════════════════
            # FIX #21 (12mag2026): TABELLA FORENSE PHANTOM_NO_ENTRY
            # ════════════════════════════════════════════════════════════
            # Cattura il "fingerprint fisico" di ogni phantom bloccato dal
            # TsunamiGate per analizzare a posteriori cosa distingue gli
            # 11 WIN dai 115 LOSS (caso 12mag pomeriggio: ratio 8.4%).
            # Roberto: "Sono gli unici soldi che possiamo prendere".
            # ════════════════════════════════════════════════════════════
            conn.execute("""
                CREATE TABLE IF NOT EXISTS phantom_forensic (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_entry        REAL,
                    ts_close        REAL,
                    block_reason    TEXT,
                    direction       TEXT,
                    price_entry     REAL,
                    price_close     REAL,
                    pnl_netto       REAL,
                    is_win          INTEGER,
                    duration_sec    REAL,
                    -- Tsunami snapshot al momento del blocco
                    ts_30s_strength    REAL,
                    ts_30s_direction   TEXT,
                    ts_30s_coerenza    REAL,
                    ts_2min_strength   REAL,
                    ts_2min_direction  TEXT,
                    ts_2min_coerenza   REAL,
                    ts_10min_strength  REAL,
                    ts_10min_direction TEXT,
                    ts_10min_coerenza  REAL,
                    ts_confidenza      INTEGER,
                    -- Altri organi al momento del blocco
                    seed_score      REAL,
                    oi_carica       REAL,
                    rsi             REAL,
                    macd            REAL,
                    momentum        TEXT,
                    volatility      TEXT,
                    trend           TEXT,
                    regime          TEXT,
                    matrimonio      TEXT,
                    score           REAL,
                    soglia          REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_phf_block_reason
                ON phantom_forensic(block_reason, is_win)
            """)
            # ════════════════════════════════════════════════════════════════
            # FIX MFE/MAE (28mag, Roberto): aggiungo 4 colonne forensiche per
            # registrare la TRAIETTORIA del prezzo, non solo il punto di chiusura.
            # max_price = prezzo MASSIMO raggiunto durante la vita del fantasma
            # min_price = prezzo MINIMO raggiunto durante la vita del fantasma
            # mfe_usd = movimento favorevole massimo in dollari (direzionale)
            # mae_usd = movimento avverso massimo in dollari (direzionale)
            # ALTER è idempotente: se la colonna esiste già, ignora silenziosamente.
            # ════════════════════════════════════════════════════════════════
            for _col, _typ in [("max_price","REAL"),("min_price","REAL"),
                               ("mfe_usd","REAL"),("mae_usd","REAL")]:
                try:
                    conn.execute(f"ALTER TABLE phantom_forensic ADD COLUMN {_col} {_typ}")
                except Exception:
                    pass  # colonna già esistente
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[PERSIST] Init DB: {e}")

    def load(self) -> tuple:
        """Ritorna (capital, total_trades)."""
        try:
            conn = _safe_connect(self.db_path, timeout=30)
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
            conn = _safe_connect(self.db_path, timeout=30)

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
                'trust':      {},  # V16: non persistito — riparte da default 50
                'separazione':dict(memoria.separazione),
                'blacklist':  dict(memoria.blacklist),
                'divorzio':   [],  # V16: non persistito — solo RAM di sessione
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
                
                # ════════════════════════════════════════════════════════════
                # FIX #18 (12mag2026): PERSISTENZA BUFFER PREZZI
                # ════════════════════════════════════════════════════════════
                # Bug pre-fix: ad ogni restart il bot perdeva la sua memoria
                # fisica (prices_long, prices_short, prices_ta = vuoti) e doveva
                # rifare 10+ minuti di warmup cieco. In quei minuti:
                #   - RSI/MACD non disponibili
                #   - SeedScorer ritorna 'insufficient_data'
                #   - Bot non poteva tradare
                # Effetto: 14 mesi di lavoro che potrebbero NON essersi 
                # accumulati perché ogni restart resetta la memoria fisica.
                # 
                # Fix: salvo i 3 buffer prezzi essenziali ogni 5 min.
                # Al boot li ripristino → ZERO warmup → bot OPERATIVO subito.
                # ════════════════════════════════════════════════════════════
                'prices_long':  list(bot.campo._prices_long)  if hasattr(bot, 'campo') else [],
                'prices_short': list(bot.campo._prices_short) if hasattr(bot, 'campo') else [],
                'prices_ta':    list(bot.campo._prices_ta)    if hasattr(bot, 'campo') else [],
                'tick_count':   getattr(bot.campo, '_tick_count', 0) if hasattr(bot, 'campo') else 0,
                
                # ════════════════════════════════════════════════════════════
                # FIX #20 (12mag2026): PERSISTENZA CANDELE TSUNAMI
                # ════════════════════════════════════════════════════════════
                # Le candele 30s/2min/10min richiedono tempo per costruirsi:
                #   - 30s × 30 candele = 15 min
                #   - 2min × 30 candele = 1 ora
                #   - 10min × 30 candele = 5 ore
                # Senza persistenza, ad ogni restart si perdono ore di storia
                # e il TsunamiDetector resta cieco per ore. Inaccettabile.
                # ════════════════════════════════════════════════════════════
                'tsunami_state': bot.tsunami.to_persist() if (hasattr(bot, 'tsunami') and bot.tsunami is not None) else None,
            }
            conn = _safe_connect(self.db_path, timeout=30)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('runtime_state', ?)",
                        (json.dumps(data, default=str),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[RUNTIME_SAVE] {e}")

    def load_runtime_state(self, bot):
        """Ripristina lo stato runtime dal DB."""
        try:
            conn = _safe_connect(self.db_path, timeout=30)
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

            # ════════════════════════════════════════════════════════════
            # FIX #18 (12mag2026): RIPRISTINO BUFFER PREZZI
            # ════════════════════════════════════════════════════════════
            # Ripristina la memoria fisica del bot per evitare warmup cieco
            # ad ogni restart. Critico per:
            #   - RSI/MACD disponibili subito
            #   - SeedScorer operativo subito  
            #   - Drift detection accurate
            #   - (Futuro) TsunamiDetector multi-finestra
            # ════════════════════════════════════════════════════════════
            if hasattr(bot, 'campo'):
                try:
                    if 'prices_long' in data and data['prices_long']:
                        for p in data['prices_long']:
                            bot.campo._prices_long.append(float(p))
                        restored.append(f"prices_long:{len(data['prices_long'])}")
                    
                    if 'prices_short' in data and data['prices_short']:
                        for p in data['prices_short']:
                            bot.campo._prices_short.append(float(p))
                        restored.append(f"prices_short:{len(data['prices_short'])}")
                    
                    if 'prices_ta' in data and data['prices_ta']:
                        for p in data['prices_ta']:
                            bot.campo._prices_ta.append(float(p))
                        restored.append(f"prices_ta:{len(data['prices_ta'])}")
                        # FIX #22 (12mag): RSI/MACD DISARMATI — non più ricalcolati.
                        # Buffer prices_ta resta persistito per coerenza, ma il bot
                        # non ne calcola più gli indicatori ortodossi.
                        # bot.campo._update_rsi()
                        # bot.campo._update_macd()
                    
                    if 'tick_count' in data:
                        bot.campo._tick_count = int(data.get('tick_count', 0))
                        restored.append(f"tick_count:{bot.campo._tick_count}")
                except Exception as _bex:
                    log.error(f"[RUNTIME_LOAD] errore ripristino buffer prezzi: {_bex}")

            # ════════════════════════════════════════════════════════════
            # FIX #20 (12mag2026): RIPRISTINO CANDELE TSUNAMI
            # ════════════════════════════════════════════════════════════
            if hasattr(bot, 'tsunami') and bot.tsunami is not None:
                try:
                    ts_state = data.get('tsunami_state')
                    if ts_state:
                        bot.tsunami.from_persist(ts_state)
                        c30 = len(ts_state.get('30s', []))
                        c2 = len(ts_state.get('2min', []))
                        c10 = len(ts_state.get('10min', []))
                        restored.append(f"tsunami:30s={c30},2min={c2},10min={c10}")
                        log.info(f"[RUNTIME_LOAD] 🌊 Tsunami candele ripristinate: "
                                f"30s={c30} 2min={c2} 10min={c10}")
                except Exception as _tex:
                    log.error(f"[RUNTIME_LOAD] errore ripristino tsunami: {_tex}")

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
            conn = _safe_connect(self.db_path, timeout=30)
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
            conn = _safe_connect(self.db_path, timeout=30)
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
            conn  = _safe_connect(self.db_path, timeout=30)
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
                # V16: trust non caricato dal DB — riparte da default 50
                for k, v in md.get('separazione', {}).items():
                    memoria.separazione[k] = v
                for k, v in md.get('blacklist', {}).items():
                    memoria.blacklist[k] = v
                # V16: divorzi non caricati dal DB — ripartono sempre vuoti
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
            conn = _safe_connect(self.db_path, timeout=30)
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
        self._regime   = 'RANGING'
        self._confidence = 0.0
        # FIX Bug #9 (12mag): isteresi cambi regime
        # Problema: detect() ogni tick → flip ogni 60s + confidence -42% impossibile
        # Fix: nuovo regime deve essere stabile per CHANGE_TICKS tick consecutivi
        self._pending_regime = 'RANGING'
        self._pending_count  = 0
        self.CHANGE_TICKS    = 30  # ~30s di conferma per switchare regime

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
            # ════════════════════════════════════════════════════════════
            # PASSO 8 (14mag2026) — FIX FORMULA CONFIDENCE RANGING
            # ════════════════════════════════════════════════════════════
            # Prima: confidence = 1.0 - abs(dir_ratio - 0.5) * 4
            #   con dir_ratio=0.286 → 1.0 - 0.214*4 = 0.14
            #   con dir_ratio fuori [0.25,0.75] → NEGATIVA → clamp a 0.0
            #   Risultato: 'regime conf 0.0' anche su RANGING legittimi.
            #   Il *4 è troppo aggressivo: confonde "RANGING sbilanciato"
            #   (probabile trend nascente) con "confidenza zero".
            #
            # Ora: due concetti separati.
            #  - dir_ratio vicino a 0.5  → RANGING VERO, confidence ALTA
            #    (mercato che oscilla bilanciato, nessuna direzione = è
            #     davvero un range, e di questo siamo confidenti)
            #  - dir_ratio lontano da 0.5 → RANGING INCERTO, confidence BASSA
            #    (sbilanciato: forse sta trendando ma sotto le soglie trend.
            #     NON è "zero" — è "RANGING poco affidabile", ~0.3)
            # Coefficiente 2.0 invece di 4.0: la confidence degrada ma non
            # satura a zero. Floor 0.30: un RANGING resta un RANGING, anche
            # incerto — non diventa mai "non lo so" assoluto.
            regime     = 'RANGING'
            confidence = max(0.30, 1.0 - abs(dir_ratio - 0.5) * 2.0)

        # FIX Bug #9 (12mag): clamp confidence + isteresi cambio regime
        confidence = max(0.0, min(1.0, confidence))

        # isteresi: il regime cambia solo dopo CHANGE_TICKS conferme
        if regime == self._regime:
            self._pending_regime = regime
            self._pending_count = 0
        elif regime == self._pending_regime:
            self._pending_count += 1
            if self._pending_count >= self.CHANGE_TICKS:
                self._regime = regime
                self._pending_count = 0
        else:
            self._pending_regime = regime
            self._pending_count = 1

        # ════════════════════════════════════════════════════════════════
        # PASSO 8 — RIALLINEAMENTO regime/confidence
        # ════════════════════════════════════════════════════════════════
        # Prima: self._confidence = confidence  (calcolata sul regime
        #   ISTANTANEO), ma il return restituisce self._regime (quello
        #   ISTERESIZZATO). Se l'isteresi non ha ancora confermato il
        #   cambio, uscivano accoppiati un regime e una confidence che si
        #   riferiscono a momenti diversi → 'conf' incoerente col 'regime'.
        #
        # Ora: la confidence segue il regime EFFETTIVAMENTE restituito.
        #  - se regime calcolato == self._regime → confidence è coerente,
        #    si usa così com'è
        #  - se l'isteresi sta ancora trattenendo un cambio → la confidence
        #    del regime restituito (quello vecchio, self._regime) è
        #    "in transizione": la si attenua proporzionalmente a quanto
        #    il pending si sta avvicinando al cambio. Più il nuovo regime
        #    accumula conferme, meno siamo confidenti del vecchio.
        if regime == self._regime:
            self._confidence = confidence
        else:
            # isteresi attiva: regime restituito = self._regime (vecchio),
            # ma c'è un pending che spinge. Attenua la confidence del
            # vecchio regime in base a quanto il pending è avanzato.
            _transizione = self._pending_count / max(1, self.CHANGE_TICKS)
            self._confidence = round(max(0.10, confidence * (1.0 - _transizione * 0.7)), 3)

        return self._regime, self._confidence, {
            'trend_pct':       round(trend_pct, 3),
            'dir_ratio':       round(dir_ratio, 3),
            'vol_accel':       round(vol_accel, 3),
            'vol_ratio':       round(vol_ratio, 3),
            'regime_calcolato': regime,
            'confidence_calcolata': round(confidence, 3),
            'pending_count':   self._pending_count,
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
    # ════════════════════════════════════════════════════════════════
    # PATCH 2 BUG 7 (16mag2026): SIMMETRIA VETI con VETI_LONG.
    # Prima: VETI_LONG aveva 4 voci, VETI_SHORT aveva 4 voci ma su
    # combinazioni diverse. Era simmetrico nei numeri, non nella
    # semantica. Mancavano veti speculari per SHORT in scenari
    # rialzisti che sono pericolosi quanto i loro opposti per LONG.
    # Aggiunti: (DEBOLE,ALTA,UP) speculare a (DEBOLE,ALTA,DOWN) LONG
    #           (MEDIO,ALTA,UP) speculare a (MEDIO,ALTA,SIDEWAYS) range tossico
    # ════════════════════════════════════════════════════════════════
    VETI_SHORT = {
        ("FORTE",  "BASSA", "UP"),     # STRONG_BULL - WR 5% per SHORT
        ("FORTE",  "MEDIA", "UP"),     # STRONG_MED - pericoloso per SHORT
        ("DEBOLE", "ALTA", "SIDEWAYS"),# RANGE_VOL_W - WR 10% in SHORT
        ("FORTE",  "ALTA", "SIDEWAYS"),# RANGE_VOL_F - WR 12% in SHORT
        # PATCH 2 aggiunti (16mag): simmetria con VETI_LONG
        ("DEBOLE", "ALTA", "UP"),      # TRAP_UP - speculare a (DEBOLE,ALTA,DOWN) LONG
        ("MEDIO",  "ALTA", "UP"),      # RANGE_VOL_UP_M - speculare a (MEDIO,ALTA,SIDEWAYS) LONG
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
    # ════════════════════════════════════════════════════════════════
    # FIX #22 (12mag2026): DISARMO RSI/MACD — Roberto Zanardo
    # ════════════════════════════════════════════════════════════════
    # MOTIVAZIONE STRATEGICA:
    # RSI (Wilder 1978) e MACD (Appel 1979) sono nati per:
    #   - grafici giornalieri/orari
    #   - mercati azionari NYSE
    #   - calcoli su carta o calcolatrice
    # NON sono nati per:
    #   - BTC che fa 10 tick/secondo
    #   - crypto con volatilità diversa
    #   - algoritmi su scale di secondi
    #   - il modello fisico di Roberto (OI_carica, SeedScorer, 
    #     TsunamiEngine, Matrimoni, Compartimenti)
    # 
    # Forensic 12mag con 151 phantom ha mostrato:
    #   RSI:  W=None  L=43.59  → NON discrimina
    #   MACD: W=None  L=-3.57  → NON discrimina
    #
    # Roberto: "Esistevano, non servono. Oggi sono zavorra. 
    #           Strozzano il nostro sistema."
    #
    # Mantengo le funzioni nel codice (reversibile) ma azzero i pesi:
    # le decisioni non saranno più influenzate da letteratura ortodossa.
    # Sperimentale: se il bot peggiora rimetto W=10. Se migliora 
    # procediamo a rimozione totale (LIVELLO 3).
    # ════════════════════════════════════════════════════════════════
    W_RSI         = 0     # ERA 10 — disarmato 12mag (letteratura ortodossa)
    W_MACD        = 0     # ERA 10 — disarmato 12mag (letteratura ortodossa)

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
    # NOTA L1: SOGLIA_BASE/MIN sono FALLBACK. CompartoEngine li sovrascrive
    # ad ogni tick con valori del comparto attivo (NEUTRO=40/34, ATTACCO=44/40,
    # DIFENSIVO=38/32, TRENDING_BULL=46/42, TRENDING_BEAR=50/46).
    # Se CompartoEngine non parte, questi default permettono al sistema di
    # continuare a funzionare invece di bloccarsi a 50/48.
    SOGLIA_BASE = 40   # era 50 - fallback coerente con NEUTRO comparto
    REGIME_FACTOR = {"TRENDING_BULL": 0.80, "EXPLOSIVE": 0.85,
                     "RANGING": 1.00, "TRENDING_BEAR": 1.10}
    # RANGING: era 1.10, ora 1.00 - soglia formula 75.9 irraggiungibile, score max realistico 64
    # Con 1.00: soglia RANGING+ALTA = 60 × 1.00 × 1.05 = 63.0 (raggiungibile)
    VOL_FACTOR    = {"BASSA": 0.90, "MEDIA": 1.0, "ALTA": 1.00}
    # ALTA: era 1.05, ora 1.00 - phantom SCORE_INSUFF WR 65% R/R 2.04, profittevoli
    # Soglia RANGING+ALTA: 60 × 1.00 × 1.00 = 60.0 (trade score 58-63 passano)
    SOGLIA_MIN    = 34    # era 44 - fallback coerente con NEUTRO comparto
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
        # SHORT_VIA_DRIFT (30mag, Roberto): tick consecutivi con drift negativo
        # sostenuto. Via pulita allo SHORT in RANGING — speculare al LONG.
        # Non dipende da RSI (cadavere, fisso 50) né da pred (mai qualificata).
        self._drift_neg_streak = 0            # tick consecutivi drift < soglia short
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
                # FIX #22 (12mag): RSI/MACD DISARMATI — letteratura ortodossa zavorra.
                # Mantengo le funzioni nel codice (reversibile) ma non le chiamo.
                # Decisione del bot si basa solo su organi del modello fisico:
                # OI_carica, SeedScorer, TsunamiEngine, Matrimoni, drift, comparto.
                # self._update_rsi()
                # self._update_macd()
                pass

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

        # FIX #22 (12mag): RSI/MACD pesi azzerati anche in score_now (letteratura ortodossa).
        # I valori 10,10 ERANO i pesi originali. Mantengo le chiavi per compatibilità.
        W = {"seed":25,"fp":20,"mom":12,"trend":12,"vol":8,"regime":3,"rsi":0,"mac":0}
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
        # Soglia proporzionale pura: sanity floor 24, allineato a evaluate()
        soglia = max(24, min(80, soglia_raw))

        # SOGLIA_PIATTA (20giu, Roberto): anche qui (score_now alimenta il
        # display e _last_soglia via riga ~8864). Se attiva, soglia FISSA al
        # valore SCORE_FLOOR: niente giudizio adattivo, decide il cancello.
        if os.environ.get("SOGLIA_PIATTA", "false").lower() == "true":
            soglia = float(int(os.environ.get("SCORE_FLOOR", "34")))

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
                'momentum':      momentum,
                'volatility':    volatility,
                'trend':         trend,
                'direction':     self._direction,
                'regime':        getattr(self, '_regime_current', ''),
                'drift_pct':     getattr(self, '_last_drift', 0.0),
                'oi_carica':     _bot_oi_carica,
                'oi_stato':      _bot_oi_stato,
                # V16: precursore esplosivo — passa ai CapsuleManager per bypass matrimoni
                'oi_short':      getattr(getattr(self, '_bot_ref', None), '_oi_carica_short', 0.0),
                'breath_fase':   getattr(getattr(getattr(self,'_bot_ref',None),'_breath',None),'_fase','NEUTRO'),
                'breath_energia':getattr(getattr(getattr(self,'_bot_ref',None),'_breath',None),'_energia',0.0),
            }
            _cm_result = _cm.valuta(_veto_ctx)
            if _cm_result.get('blocca') and os.environ.get("ATTRITO_OFF","false").lower() != "true":
                return self._veto(_cm_result.get('reason', f"CM_TOSSICO_{self._direction}_{momentum}_{volatility}_{trend}"))
        else:
            # Fallback hardcodato
            veti = self.VETI_SHORT if self._direction == "SHORT" else self.VETI_LONG
            if combo in veti and os.environ.get("ATTRITO_OFF","false").lower() != "true":
                return self._veto(f"TOSSICO_{self._direction}_{momentum}_{volatility}_{trend}")

        if matrimonio_name in divorzio_set and os.environ.get("ATTRITO_OFF","false").lower() != "true":
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
            _attrito_off = os.environ.get("ATTRITO_OFF","false").lower() == "true"
            if self._direction == "LONG" and _drift < _drift_thr and not _attrito_off:
                return self._veto(f"DRIFT_VETO_LONG_{_drift:+.3f}%(thr={_drift_thr})")
            elif self._direction == "SHORT" and _drift > abs(_drift_thr) and not _attrito_off:
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
        # ATTRITO_OFF (20giu, Roberto): lo storico del sistema VECCHIO (femmine
        # perse, -1286$) non deve zavorrare la soglia del cancello nuovo. Con
        # ATTRITO_OFF=true, history_f=1.0 (nessuna paura ereditata). La soglia
        # resta adattiva su regime/volatilita/drift, ma non sul passato sporco.
        if os.environ.get("ATTRITO_OFF", "false").lower() == "true":
            history_f = 1.0
        else:
            history_f = self._history_factor()
        prebreak_f, prebreak_detail, prebreak_signals = self._pre_breakout_factor()
        self._last_regime_for_drift = regime  # passa il regime al drift_factor
        drift_f, drift_detail = self._drift_factor()

        # Loss streak: alza soglia proporzionalmente, non blocca
        # LOSS_STREAK_OFF (18giu, Roberto): "tira via il blocco dopo 3 perdite".
        # E' un freno a CONTEGGIO (contro REGOLA-22MAG: mai conteggi). Con
        # LOSS_STREAK_OFF=true il freno e' spento: loss_f resta 1.0 sempre,
        # decide solo il piattello (sale/scende), trade per trade, senza memoria
        # di quante perdite di fila. Default false = vecchio comportamento.
        if os.environ.get("LOSS_STREAK_OFF", "false").lower() == "true":
            loss_f = 1.0
        elif loss_consecutivi >= self.MAX_LOSS_CONSECUTIVI:
            extra = loss_consecutivi - self.MAX_LOSS_CONSECUTIVI + 1
            loss_f = min(1.50, 1.0 + extra * 0.10)
        else:
            loss_f = 1.0

        soglia_raw = self.SOGLIA_BASE * context_ratio * regime_f * vol_f * history_f * prebreak_f * drift_f * loss_f

        # ════════════════════════════════════════════════════════════════════
        # LAVORO 1 — DEMOLIZIONE TRIPLE-SOGLIA (8 maggio 2026)
        # ════════════════════════════════════════════════════════════════════
        # CompartoEngine è UNICA FONTE di verità per la soglia. Il pavimento
        # fisso 48 hardcoded ammazzava RANGING (score max ~39). Eliminato.
        # Resta solo SANITY_FLOOR=24 — anti-rumore puro, non punitivo.
        # SOGLIA_BASE/MIN arrivano dal Comparto attivo (sovrascritti ogni tick).
        # ════════════════════════════════════════════════════════════════════
        SANITY_FLOOR = 24  # sotto questo è puro rumore in qualunque regime
        soglia_min_ctx = max(SANITY_FLOOR, self.SOGLIA_MIN * context_ratio)

        # SOGLIA_MAX ADATTIVA PER REGIME - non più fissa
        dynamic_max = self._get_dynamic_soglia_max(regime, volatility)
        soglia = max(soglia_min_ctx, min(dynamic_max, soglia_raw))

        # ════════════════════════════════════════════════════════════════════
        # FIX 2026-05-09 OPZIONE C — SOGLIA FLOOR DINAMICO basato su PRED_SCORE
        # ════════════════════════════════════════════════════════════════════
        # La predizione comanda sull'auto-difesa.
        # Quando pred_score sale, la soglia floor scende automaticamente —
        # il sistema "scopre da solo" che può fidarsi della propria predizione.
        # Quando pred_score scende, la soglia si rialza per sicurezza.
        # Tutto SENZA deploy, SENZA manopole, SENZA mio intervento.
        #
        #   pred_score  0%  →  floor 50  (cold start, cauto)
        #   pred_score 30%  →  floor 47
        #   pred_score 50%  →  floor 44
        #   pred_score 70%  →  floor 38  (predizione viva, si fida)
        #   pred_score 85%+ →  floor 34  (predizione molto viva, ottimo)
        # ════════════════════════════════════════════════════════════════════
        # ════════════════════════════════════════════════════════════════════
        # FIX 29mag (Roberto): "È CIECO — non si entra con energia bassa!"
        # Il floor scendeva fino a 34 fidandosi del pred_score. Ma la predizione
        # indovina ~51.8% (accuracy_segno) — testa o croce, CIECA. Dava le chiavi
        # della porta a un oracolo che non vede. Risultato: tutti i LOSS a S35
        # (energia bassa), tutti i WIN a S65 (energia alta) — la frontiera è netta.
        # ORA: il floor dinamico NON scende mai sotto SCORE_FLOOR (48), che il
        # codice stesso definisce "sotto = rumore puro". La predizione può alzare
        # la prudenza ma NON può più aprire la porta sull'energia bassa.
        # ════════════════════════════════════════════════════════════════════
        _pred_score_floor = kwargs.get('pred_score', 0.0)
        # SCORE_FLOOR configurabile via ENV (20giu, Roberto): il floor era
        # inchiodato a 48 ("freno" da storico negativo). Ora abbassabile:
        # SCORE_FLOOR=40 (o meno) lascia entrare lo score piu' basso, cosi'
        # i candidati arrivano al cancello che decide sul movimento.
        # Default 48 (comportamento invariato). REVERSIBILE.
        _floor_base = int(os.environ.get("SCORE_FLOOR", "48"))
        if _pred_score_floor >= 85:
            _floor_dyn = _floor_base
        elif _pred_score_floor >= 70:
            _floor_dyn = _floor_base
        elif _pred_score_floor >= 50:
            _floor_dyn = _floor_base
        elif _pred_score_floor >= 30:
            _floor_dyn = _floor_base + 1
        else:
            _floor_dyn = _floor_base + 2  # cold start: prudenza

        # Applica il floor dinamico SOLO se è più alto del soglia_min_ctx
        # (così non strangola il sistema sotto sanity_floor)
        soglia = max(_floor_dyn, soglia)

        # ════════════════════════════════════════════════════════════════════
        # SOGLIA_PIATTA (20giu, Roberto) — UN MODELLO SOLO, NIENTE CONFUSIONE.
        # Il calcolo adattivo sopra (regime_f, vol_f, history_f, floor_dyn...)
        # giudica il MERCATO a priori: in RANGING alza l'asticella per
        # "scartare il mercato non adatto". Ma il modello di Roberto NON giudica
        # il mercato sui fattori: aspetta e guarda il MOVIMENTO (cancello).
        # Anche in RANGING un maschio puo' nascere -> la soglia adattiva alta lo
        # ucciderebbe PRIMA del cancello. Le due logiche sono INCOMPATIBILI.
        # SOGLIA_PIATTA=true abbraccia il modello di Roberto: ignora tutto il
        # calcolo adattivo e mette la soglia FISSA al valore SCORE_FLOOR. E' solo
        # un filtro anti-rumore: sopra -> candidato -> il cancello decide sul
        # movimento. REVERSIBILE: SOGLIA_PIATTA=false (o tolto) -> adattivo.
        if os.environ.get("SOGLIA_PIATTA", "false").lower() == "true":
            soglia = float(int(os.environ.get("SCORE_FLOOR", "34")))

        # -- BOOST SOGLIA DA CAPSULE L3 (Narratore/IntelligenzaAutonoma) ----
        # Le capsule generate da DeepSeek possono alzare la soglia in base
        # all'osservazione del mercato. Il pavimento proporzionale resta valido.
        soglia_boost = kwargs.get('soglia_boost', 0.0)
        if soglia_boost > 0:
            # FIX 29mag: pavimento = _floor_dyn (48), NON soglia_min_ctx (che
            # poteva scendere a 24-34). Nemmeno il boost riapre la porta sotto rumore.
            soglia = max(_floor_dyn, min(dynamic_max, soglia + soglia_boost))

        # SOGLIA_PIATTA ha l'ULTIMA parola: ne' boost ne' floor la rialzano.
        # Filtro anti-rumore fisso, il cancello decide il resto.
        if os.environ.get("SOGLIA_PIATTA", "false").lower() == "true":
            soglia = float(int(os.environ.get("SCORE_FLOOR", "34")))

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
        # ════════════════════════════════════════════════════════════════
        # CARICA VIVA (29mag, Roberto) — la fisica che precede la vittoria.
        # "Prima della vittoria c'è un'energia che lo segnala — ma può morire
        #  se non ha forza sufficiente. Il vincente ha la sua identità PRIMA."
        # I seed direzionali (carica che SALE per 5 tick = impulso vivo che
        # nasce) erano riconosciuti ma schiacciati a 0.92 (effetto 8%, sprecato).
        # ORA la carica viva pesa davvero: più segnali concordi → più forza →
        # soglia più bassa (il bot entra dove l'impulso nasce). Carica morta
        # o piatta (signals 0) → NON tocca nulla (resta prudente).
        # Come il tiro al piattello: non spari all'uscita, becchi la traiettoria.
        # ════════════════════════════════════════════════════════════════
        if signals >= 3:
            factor = 0.82    # carica VIVA e forte: impulso che nasce, entra
        elif signals >= 2:
            factor = 0.90    # carica viva media: effetto moderato
        elif signals >= 1:
            factor = 0.96    # un segnale: piccolo aiuto
        else:
            factor = 1.00    # carica morta/piatta: nessuno sconto, prudenza

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
        """
        DISARMATO 23mag2026 — RSI rimosso dal sistema decisionale.
        RSI non parla il paradigma fisico (energia/OI/fuoco) ed è rumore a 60s.
        _last_rsi resta a 50.0 (init) per sempre. Funzione lasciata vuota
        per compatibilità con eventuali chiamate esistenti.
        Mai più deve aprire porte ai ladri o chiuderle a chi porta soldi.
        """
        return

    def _update_macd(self):
        """
        DISARMATO 23mag2026 — MACD rimosso dal sistema decisionale.
        Stesso motivo di RSI: indicatore classico non compatibile con
        paradigma fisico (energia/OI/fuoco), rumore a 60s.
        _last_macd, _last_macd_signal, _last_macd_hist restano a 0.0 (init)
        per sempre. Funzione lasciata vuota per compatibilità con chiamate esistenti.
        Mai più deve aprire porte ai ladri o chiuderle a chi porta soldi.
        """
        return

    def _rsi_score(self) -> float:
        """
        DISARMATO 23mag2026 — RSI rimosso dal sistema decisionale.
        Ritorna sempre 0.0. Tutti i consumatori (4692, 4832, 13417) ricevono
        contributo nullo, indipendentemente da W_RSI.
        Mai più deve aprire porte ai ladri o chiuderle a chi porta soldi.
        """
        return 0.0

    def _macd_score(self) -> float:
        """
        DISARMATO 23mag2026 — MACD rimosso dal sistema decisionale.
        Ritorna sempre 0.0. Tutti i consumatori (4693, 4833, 13418) ricevono
        contributo nullo, indipendentemente da W_MACD.
        Mai più deve aprire porte ai ladri o chiuderle a chi porta soldi.
        """
        return 0.0

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

            # ════════════════════════════════════════════════════════════════
            # GUARDIANO 1 RIBILANCIATO (29mag, Roberto):
            # PRIMA: campo_carica MIN 30% (OI costretto a dominare),
            #        signal_tracker MAX 25% (economia soffocata).
            # Questo costringeva il bot a decidere sull'ENERGIA OI (che non dà
            # direzione) invece che sulla CONVENIENZA (che sa: TRENDING_BEAR vince,
            # RANGING LONG perde — 128k campioni Signal Tracker).
            # ORA: campo_carica MAX 30% (OI non può dominare),
            #      signal_tracker MIN 13% (economia libera di crescere).
            # Coerente coi PESI_DEFAULT (campo 0.22, signal 0.13).
            # ════════════════════════════════════════════════════════════════
            pesi['campo_carica']   = min(0.30, pesi['campo_carica'])   # OI: tetto 30%, non dominante
            pesi['signal_tracker'] = max(0.13, pesi['signal_tracker']) # economia: pavimento 13%, può salire
            pesi['oracolo_fp']     = max(0.10, pesi['oracolo_fp'])     # pavimento abbassato
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
        """Persiste _stats e _closed su SQLite — sopravvive al restart.

        INTERRUTTORE 6giu: VERITAS_OFF=true spegne questa scrittura frequente
        (DELETE+INSERT a raffica) che contribuiva al lock. Reversibile.
        """
        if os.environ.get("VERITAS_OFF", "false").lower() == "true":
            return
        try:
            import sqlite3, json
            conn = _safe_connect(db_path, timeout=15)
            conn.execute("PRAGMA busy_timeout=15000;")
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
            conn = _safe_connect(db_path, timeout=30)
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

    # =========================================================================
    # STATUTO COSTITUZIONALE DI VERITAS (Passo 2, 13mag2026)
    # =========================================================================
    # AUDIT ROBERTO V1 / SC SOVRANO.
    #
    # Veritas oggi è giudice retrospettivo + calibratore (_calibra_sc applica
    # subito i pesi). Lo Statuto aggiunge la modalità COSTITUZIONALE:
    #   - get_ctx_stats()      → espone le statistiche di un contesto a SC
    #                            come input (testimonianza storica).
    #   - recalibra_sc_pesi()  → PROPONE i nuovi pesi senza applicarli, e li
    #                            logga. È la versione passiva di _calibra_sc.
    #
    # MODALITÀ PASSIVA: questi due metodi esistono ma:
    #   - get_ctx_stats() non viene ancora chiamato dal flow (sarà nella scena)
    #   - recalibra_sc_pesi() PROPONE soltanto — _calibra_sc continua a fare
    #     il lavoro vero finché il Passo 6 non commuta ufficialmente.
    # Behavior del bot invariato.
    # =========================================================================
    def get_ctx_stats(self, ctx_stats_dict: dict, ctx_key: str) -> dict:
        """Espone le statistiche di un contesto come testimonianza per SC.

        ctx_stats_dict: il dizionario _m2_ctx_stats del bot (vive nel bot,
                        non in Veritas — Veritas lo riceve come parametro).
        ctx_key:        "MOMENTUM|VOLATILITY|TREND"

        Ritorna:
          ctx_wr           float | None  — win rate del contesto
          ctx_samples      int           — numero trade reali nel contesto
          ctx_pnl_avg      float | None  — pnl medio
          ctx_pnl_sum      float         — pnl totale
          last_judgement   str           — 'TOSSICO' | 'NEUTRO' | 'BUONO' | 'IGNOTO'
        """
        st = (ctx_stats_dict or {}).get(ctx_key)
        if not st or st.get('n', 0) == 0:
            return {
                "ctx_wr":         None,
                "ctx_samples":    0,
                "ctx_pnl_avg":    None,
                "ctx_pnl_sum":    0.0,
                "last_judgement": "IGNOTO",
            }
        n       = st['n']
        wins    = st.get('wins', 0)
        pnl_sum = st.get('pnl_sum', 0.0)
        wr      = wins / n
        pnl_avg = pnl_sum / n
        # Verdetto sintetico sul contesto
        if n >= 10 and wr < 0.20 and pnl_avg < -1.0:
            judgement = "TOSSICO"
        elif n >= 5 and wr >= 0.55 and pnl_avg > 0.5:
            judgement = "BUONO"
        elif n >= 3:
            judgement = "NEUTRO"
        else:
            judgement = "IGNOTO"
        return {
            "ctx_wr":         round(wr, 3),
            "ctx_samples":    n,
            "ctx_pnl_avg":    round(pnl_avg, 2),
            "ctx_pnl_sum":    round(pnl_sum, 2),
            "last_judgement": judgement,
        }

    def recalibra_sc_pesi(self) -> dict:
        """PROPONE nuovi pesi per SC sulla base dei verdetti accumulati.

        VERSIONE PASSIVA: calcola la proposta, la logga, la ritorna.
        NON applica i pesi. _calibra_sc() continua a essere il calibratore
        attivo finché il Passo 6 non commuta.

        Ritorna:
          proposta       dict   — pesi proposti {organo: peso}
          delta          dict   — variazione proposta per ogni organo
          motivi         list   — perché ogni variazione
          applicato      bool   — sempre False in modalità passiva
        """
        out = {
            "proposta":  {},
            "delta":     {},
            "motivi":    [],
            "applicato": False,
        }
        if not self._sc_ref or not hasattr(self._sc_ref, '_pesi'):
            out["motivi"].append("nessun riferimento SC disponibile")
            return out

        pesi_attuali = dict(self._sc_ref._pesi)
        proposta     = dict(pesi_attuali)
        STEP_MAX     = 0.02  # max variazione per ciclo (più conservativo di _calibra_sc)

        try:
            v = self.verdetto()
            stats = v.get('stats', {})

            # Analizza i verdetti FUOCO|BLOCCA e FUOCO|ENTRA
            for chiave, s in stats.items():
                if s['n'] < 10:
                    continue
                hit_rate = s['hit_rate']
                pnl_avg  = s['pnl_avg']

                if 'FUOCO' in chiave and 'BLOCCA' in chiave:
                    if pnl_avg < -0.5 and hit_rate >= 0.55:
                        # SC bloccava ma il mercato saliva → SC troppo timido
                        _d = min(STEP_MAX, abs(pnl_avg) * 0.01)
                        proposta['campo_carica'] = min(0.60, proposta.get('campo_carica',0.30) + _d)
                        out["motivi"].append(
                            f"{chiave}: SC perdeva ${pnl_avg:+.2f}/trade bloccando "
                            f"(hit {hit_rate:.0%}) → propongo campo_carica +{_d:.3f}")
                    elif pnl_avg > 0.5 and hit_rate <= 0.45:
                        # SC bloccava e aveva ragione
                        _d = min(STEP_MAX, pnl_avg * 0.01)
                        proposta['signal_tracker'] = min(0.25, proposta.get('signal_tracker',0.20) + _d)
                        out["motivi"].append(
                            f"{chiave}: SC bloccava giustamente (pnl ${pnl_avg:+.2f}) "
                            f"→ propongo signal_tracker +{_d:.3f}")

            # Rinormalizza la proposta a somma 1.0
            tot = sum(proposta.values())
            if tot > 0:
                for k in proposta:
                    proposta[k] = round(proposta[k] / tot, 4)

            # Calcola i delta
            for k in proposta:
                d = proposta[k] - pesi_attuali.get(k, 0)
                if abs(d) > 0.0001:
                    out["delta"][k] = round(d, 4)

            out["proposta"] = proposta

            # LOG della proposta — visibile in app.log, NON applicata
            if out["delta"]:
                log.info(f"[VERITAS_RECALIBRATE_PROPOSAL] delta={out['delta']} "
                         f"motivi={len(out['motivi'])}")
                for m in out["motivi"]:
                    log.info(f"  └─ {m}")
            else:
                log.debug("[VERITAS_RECALIBRATE_PROPOSAL] nessuna variazione proposta")

        except Exception as e:
            log.error(f"[VERITAS_RECALIBRATE] {e}")
            out["motivi"].append(f"errore: {e}")

        return out


class SuperCervello:
    """
    Supercervello — legge tutti gli organi simultaneamente ogni tick.
    Produce una decisione unica: ENTRA / ATTENDI / BLOCCA
    con size_mult e soglia_adj calcolati dai voti pesati.
    I pesi si adattano autonomamente dopo ogni trade.
    """
    # ════════════════════════════════════════════════════════════════════
    # PASSO 6 (14mag2026) — LA SINTESI PIENA DI SC
    # I 5 pesi originali diventano 8: Veritas, Capsule, Breath entrano nello
    # score pesato. Prima erano ricevuti-e-loggati; ora VOTANO.
    # Ridistribuzione: somma = 1.0. Veritas 0.16 / Capsule 0.14 pesano ma
    # NON dominano (vincolo anti-dittatore: nessun voto singolo ribalta la
    # maggioranza). Breath 0.00 — calcolato ma peso nullo finché un 6-bis
    # non lo attiva (evita di introdurre 3 variabili insieme).
    # ════════════════════════════════════════════════════════════════════
    PESI_DEFAULT = {
        'campo_carica':  0.22,   # era 0.30
        'oracolo_fp':    0.18,   # era 0.25
        'veritas':       0.16,   # NUOVO — testimonianza storica del contesto
        'capsule':       0.14,   # NUOVO — il voto pesato degli anticorpi
        'signal_tracker':0.13,   # era 0.20
        'matrimonio':    0.09,   # era 0.13
        'phantom_ratio': 0.08,   # era 0.12
        'breath':        0.00,   # NUOVO — calcolato, peso nullo fino a 6-bis
    }

    def __init__(self):
        self._pesi = dict(self.PESI_DEFAULT)
        self._storia = []
        self._n = 0
        # ════════════════════════════════════════════════════════════════
        # PATCH 0 (16mag2026) — VERITÀ PREDITTIVA
        # _pred_score_ref / _pred_calib_ref restano per compat runtime (mai None
        # internamente), MA decidere usa SEMPRE _get_pred_state() che controlla
        # source + enabled. Nessun valore default ha più diritto di voto.
        # Init: BOOT_MUTED. Nessun valore predittivo finto al boot.
        # ════════════════════════════════════════════════════════════════
        self._pred_score_ref = 0.0
        self._pred_calib_ref = 0.0
        self._pred_source = "BOOT_MUTED"
        self._pred_decision_enabled = False
        self._veritas_stats_ref = None

    def _get_pred_state(self) -> dict:
        """
        PATCH 0 — accesso CENTRALIZZATO allo stato predittivo.
        Ogni punto operativo DEVE leggere da qui, non direttamente da
        self._pred_score_ref / _pred_calib_ref.

        Ritorna:
          score:     float (runtime sicuro, mai None)
          calib:     float (runtime sicuro, mai None)
          source:    str  ("BOOT_MUTED" | "V2_OBSERVING" | "V2_QUALIFIED" | "NONE")
          enabled:   bool (False finché non c'è qualifica V2 per cella)
          qualified: bool (True SOLO se enabled E source == V2_QUALIFIED)

        Regola d'oro: qualified=True è l'UNICA condizione che dà diritto di
        voto operativo (sbloccare veti, amplificare pesi, abbassare soglie,
        skippare auto-tune, aprire SHORT in RANGING, modificare comparto).
        """
        score  = getattr(self, "_pred_score_ref", 0.0)
        calib  = getattr(self, "_pred_calib_ref", 0.0)
        source = getattr(self, "_pred_source", "NONE")
        enabled = bool(getattr(self, "_pred_decision_enabled", False))
        return {
            "score":     float(score if score is not None else 0.0),
            "calib":     float(calib if calib is not None else 0.0),
            "source":    source,
            "enabled":   enabled,
            "qualified": enabled and source == "V2_QUALIFIED",
        }

    def decide(self, fp_wr, fp_samples, st_hit_rate, st_n, st_pnl,
               oi_carica, oi_stato, score, soglia,
               matrimonio_wr, matrimonio_trust,
               ph_protezione, ph_zavorra,
               regime, midzone, loss_streak,
               fp_wr_opposite=None, fp_samples_opposite=None,
               current_direction=None,
               # ── PASSO 11 (15mag2026) — OI SHORT per pred_boost bilaterale ──
               # Prima: pred_boost guardava SOLO oi_stato (LONG).
               # Quando il FUOCO era dal lato SHORT, ignorato → 20 LONG zero SHORT.
               # Ora: pred_boost guarda ENTRAMBI i lati e flippa direzione.
               oi_stato_short=None, oi_carica_short=None,
               # ── STATUTO COSTITUZIONALE (Passo 4, 13mag2026) ──────────────
               # Input dagli organi che oggi sono dittatori con early return.
               # In MODALITÀ PASSIVA: SC li RICEVE e li LOGGA (SC_INPUTS_FULL)
               # ma NON li usa per decidere. La decisione resta identica.
               # L'attivazione (SC che li USA) è il Passo 6.
               tsunami_vote=None, tsunami_confidence=None,
               tsunami_direction=None, tsunami_reason=None,
               veritas_ctx_wr=None, veritas_ctx_samples=None,
               veritas_ctx_pnl_avg=None, veritas_last_judgement=None,
               capsule_block_score=None, capsule_boost_score=None,
               capsule_reasons=None, capsule_oracolo_override=None,
               proposed_direction=None, flip_confidence=None,
               breath_fase=None, breath_energia=None,
               sc_inputs_full=False) -> dict:
        """
        FIX #32 (12mag2026 sera): aggiunti 3 parametri OPZIONALI con default None
        per retro-compat. Se forniti, calcolano direction_vote.
        - fp_wr_opposite/fp_samples_opposite: stats del fingerprint con direzione opposta
        - current_direction: direzione corrente del campo ('LONG' o 'SHORT')
        Se direzione opposta ha edge nettamente migliore (>=0.15 WR delta, n>=20, 
        WR_opposite>=0.65), emette `direction_vote` opposto nell'output.

        PASSO 4 — STATUTO COSTITUZIONALE (13mag2026):
        Aggiunti i parametri degli organi che oggi sono dittatori (Tsunami,
        Veritas, Capsule, Flip). SC li riceve come testimonianze.
        MODALITÀ PASSIVA: se sc_inputs_full=True, SC logga SC_INPUTS_FULL con
        tutti i nuovi input — ma la logica decisionale NON cambia. Gli input
        nuovi sono ignorati nel calcolo. Behavior bit-identico.
        L'attivazione vera è il Passo 6 (post-scena).
        """

        self._n += 1

        # ── STATUTO COSTITUZIONALE: log SC_INPUTS_FULL (modalità passiva) ────
        # SC dichiara cosa vede. Non lo usa ancora — lo espone per tracciabilità.
        if sc_inputs_full:
            try:
                _const_in = {
                    "tsunami_vote":         tsunami_vote,
                    "tsunami_confidence":   tsunami_confidence,
                    "tsunami_direction":    tsunami_direction,
                    "veritas_ctx_wr":       veritas_ctx_wr,
                    "veritas_ctx_samples":  veritas_ctx_samples,
                    "veritas_judgement":    veritas_last_judgement,
                    "capsule_block_score":  capsule_block_score,
                    "capsule_boost_score":  capsule_boost_score,
                    "capsule_override":     capsule_oracolo_override,
                    "proposed_direction":   proposed_direction,
                    "flip_confidence":      flip_confidence,
                    "breath_fase":          breath_fase,
                }
                _vis = " ".join(f"{k}={v}" for k, v in _const_in.items() if v is not None)
                log.info(f"🏛️ [SC_INPUTS_FULL] {_vis if _vis else '(nessun input costituzionale)'}")
            except Exception:
                pass  # il log non deve mai rompere decide()

        # Blocchi assoluti
        # midzone resta veto fisico assoluto: zona morta di mercato, non giudizio
        # di merito. Non si tocca.
        if midzone:
            return self._out("BLOCCA", 0.5, 0, "midzone", 0.95)

        # ════════════════════════════════════════════════════════════════════
        # PASSO 5b — LO STREAK È UN VOTO, NON UN VETO (14mag2026)
        # ════════════════════════════════════════════════════════════════════
        # Prima: `if loss_streak >= 4: return BLOCCA` — dittatore dentro SC.
        # Tagliava decide() prima di guardare il verbale costituzionale.
        #
        # Ora: lo streak deposita un PESO CONTRARIO. Il verbale può
        # controbilanciarlo SOLO con prove fisiche concrete e con MARGINE NETTO.
        # Nel dubbio lo streak vince — resta la difesa di default.
        #
        # Numeri: nessuna soglia inventata.
        #  - oi_carica >= 0.75  → stessa soglia di VERITAS_FUOCO (riga sotto)
        #  - tsunami_confidence → già 0-3 nel vota() di Tsunami
        #  - capsule_block_score, veritas_ctx_wr → valori osservati nei log live
        #  - MARGINE_OVERRIDE → unico parametro nuovo. Parte CONSERVATIVO.
        # ════════════════════════════════════════════════════════════════════
        # ════════════════════════════════════════════════════════════════════
        # DISATTIVATO (4giu, Roberto): streak >= 4 era ZAVORRA, non salvagente.
        # Nello stato attuale (caccia al cromosoma-seme) bloccare dopo 4 loss
        # IMPEDISCE di raccogliere i trade che servono a misurare se il seme
        # separa femmine/maschi. I loss NON sono pericolo da cui nascondersi:
        # sono DATI che alimentano le capsule e raffinano il cromosoma.
        # Soglia alzata 4 → 999 = di fatto mai. Meccanismo INTATTO sotto: se
        # un domani serve un freno vero (es. >= 20), basta cambiare il numero.
        # ════════════════════════════════════════════════════════════════════
        if loss_streak >= 999:
            # Retro-compat: se il verbale non è arrivato (chiamata vecchia,
            # tutti i param costituzionali None) → comportamento di prima.
            _verbale_presente = (tsunami_vote is not None
                                 or capsule_block_score is not None
                                 or veritas_ctx_wr is not None)
            if not _verbale_presente:
                return self._out("BLOCCA", 0.5, 0, f"streak_{loss_streak}", 0.90)

            # --- piatto 1: lo streak ---
            # Più lungo lo streak, più pesa — ma plafonato (non infinito).
            # streak 4 → 4.0 ; streak 8 → 8.0 ; streak 12+ → 10.0 (cap)
            streak_contro = float(min(10.0, loss_streak))

            # --- piatto 2: il verbale può controbilanciare ---
            verbale_pro = 0.0
            _pro_reasons = []

            # (a) impulso fisico coerente multi-segnale: Tsunami + FLIP allineati
            _tc = tsunami_confidence if tsunami_confidence is not None else 0
            _td = tsunami_direction
            _pd = proposed_direction
            _fc = flip_confidence if flip_confidence is not None else 0.0
            if (tsunami_vote in ("ENTRA_LONG", "ENTRA_SHORT")
                    and _tc >= 2
                    and _pd is not None and _td is not None and _pd == _td
                    and _fc >= 0.7):
                verbale_pro += 6.0
                _pro_reasons.append(f"impulso_coerente(ts_conf={_tc},flip={_fc:.1f})")

            # (b) energia confermata: OI FUOCO carica alta
            if oi_stato == "FUOCO" and oi_carica >= 0.75:
                verbale_pro += 4.0
                _pro_reasons.append(f"oi_FUOCO_c{oi_carica:.2f}")

            # --- il verbale può anche RAFFORZARE lo streak ---
            # se gli altri testimoni confermano "contesto tossico", lo streak
            # ha ragione: capsule che bloccano + Veritas che certifica WR basso.
            if (capsule_block_score is not None and capsule_block_score >= 100
                    and veritas_ctx_wr is not None and veritas_ctx_wr < 0.30):
                streak_contro += 4.0
                _pro_reasons.append("verbale_conferma_tossico")

            # --- LA BILANCIA — SC decide ---
            # MARGINE_OVERRIDE conservativo: il verbale deve battere lo streak
            # di almeno 2.0 punti netti. Nel dubbio, lo streak vince.
            MARGINE_OVERRIDE = 2.0
            if verbale_pro - streak_contro >= MARGINE_OVERRIDE:
                # Override: NON blocca per streak. Prosegue alla valutazione
                # normale (voti organi, soglia su st, direction_vote).
                # Lo streak NON è cancellato — è stato controbilanciato.
                log.info(f"🏛️ [SC_STREAK_OVERRIDE] streak={loss_streak} "
                         f"verbale_pro={verbale_pro:.1f} > streak_contro={streak_contro:.1f} "
                         f"| {','.join(_pro_reasons)} → prosegue")
                # (cade fuori dall'if — decide() continua normalmente)
            else:
                log.info(f"🏛️ [SC_STREAK_CONFERMATO] streak={loss_streak} "
                         f"verbale_pro={verbale_pro:.1f} <= streak_contro={streak_contro:.1f} "
                         f"→ BLOCCA")
                return self._out("BLOCCA", 0.5, 0, f"streak_{loss_streak}", 0.90)

        # ════════════════════════════════════════════════════════════════
        # FIX 26mag2026 (Roberto): RIMOZIONE BYPASS VERITAS_FUOCO
        # ════════════════════════════════════════════════════════════════
        # Era: oi_stato=FUOCO + oi_carica>=0.75 → ENTRA SUBITO (bypass capsule)
        # Storia: scritto quando le capsule erano stupide e il SC doveva
        #         agire da solo. "373 segnali, $112 di guadagni" — vero allora.
        # Oggi:   le capsule TOSSICO (CTX/REGIME/MAT) sono cresciute con
        #         WR misurato su 88-187 sample. Hanno depositato 4253 hits
        #         sul pattern killer. Bypassarle ha aperto 144 trade a WR 20.8%
        #         (-$249.66 documentati).
        # Fix:    VERITAS_FUOCO diventa DEPOSIZIONE A FAVORE, non bypass.
        #         Si aggiunge ai pesi del giudice. Le capsule possono
        #         controbilanciarlo. Il SC PESA, non DECRETA da solo.
        # ════════════════════════════════════════════════════════════════
        # Veto FP_TOSSICO viene PRIMA. Se le capsule sono unanimi sul tossico,
        # nemmeno OI FUOCO supera il veto. Il bot ha alleati, li ascolta.
        # Eccezione VERITAS_FUOCO sopra FP_TOSSICO mantenuta SOLO se le
        # capsule sono SILENTI (block_score basso → no "TOSSICO" deposto).

        # VETO ASSOLUTO FINGERPRINT TOSSICO
        # Se il fingerprint ha 20+ campioni con WR < 45% — blocca sempre
        # ECCEZIONE: pred_score >= 70% + OI FUOCO (qualunque lato) = predizione attiva supera il veto storico
        # PATCH 0: SOLO se _pred["qualified"] (V2_QUALIFIED + decision_enabled).
        # Senza qualifica, il veto FP_TOSSICO è SEMPRE attivo. Nessuna eccezione su pred finta.
        _pred = self._get_pred_state()
        _ps = _pred["score"]
        _pc = _pred["calib"]
        # PASSO 11: predizione attiva su uno qualunque dei due lati
        # PATCH 0: aggiunto _pred["qualified"] come prerequisito assoluto
        _pred_attiva_long  = (_pred["qualified"]
                              and _ps >= 70 and _pc >= 50
                              and oi_stato == "FUOCO")
        _pred_attiva_short = (_pred["qualified"]
                              and _ps >= 70 and _pc >= 50
                              and oi_stato_short == "FUOCO"
                              and (oi_carica_short or 0) >= 0.75)
        _pred_attiva = _pred_attiva_long or _pred_attiva_short
        _fp_tossico_off = os.environ.get("SC_FP_TOSSICO_OFF", "false").lower() == "true"
        if fp_samples >= 20 and fp_wr < 0.45 and not _pred_attiva:
            if _fp_tossico_off:
                # INTERRUTTORE (7giu, Roberto): veto firma-tossica DISATTIVATO da ENV.
                # Lascio passare al voto SC + CROMO + ritardo (le reti nuove filtrano a valle).
                # Reversibile in un colpo: SC_FP_TOSSICO_OFF=false (o tolto) -> veto di nuovo attivo.
                # Log con freno a 30s per non intasare i log.
                _now_fpoff = time.time()
                if _now_fpoff - getattr(self, "_fp_off_last_log", 0.0) > 30:
                    self._fp_off_last_log = _now_fpoff
                    print(f"[SC] 🔓 FP_TOSSICO_OFF attivo: lascio passare firma tossica "
                          f"wr={fp_wr:.0%}_n={fp_samples} (veto spento da ENV)")
            else:
                return self._out("BLOCCA", 0.5, 0, f"FP_TOSSICO_wr={fp_wr:.0%}_n={fp_samples}", 0.95)

        # ════════════════════════════════════════════════════════════════
        # PASSO 11 — BOOST PREDIZIONE BILATERALE (15mag2026)
        # Prima: solo FUOCO LONG. Risultato: 20 LONG zero SHORT mentre il
        # FUOCO SHORT a 0.93 veniva ignorato. La predizione perfetta
        # (Score 100%, Scostamento $1.7) non guidava mai trade SHORT.
        # Ora: riconosce FUOCO da entrambi i lati ed emette direction_vote
        # per il lato giusto. La scena (riga 9118+) lo applica.
        # ════════════════════════════════════════════════════════════════
        if _pred_attiva and not midzone:
            # Decide la direzione in base a QUALE FUOCO è attivo
            # Se entrambi attivi (raro): vince il più carico
            if _pred_attiva_long and _pred_attiva_short:
                _dv = "LONG" if (oi_carica or 0) >= (oi_carica_short or 0) else "SHORT"
            elif _pred_attiva_short:
                _dv = "SHORT"
            else:
                _dv = "LONG"
            _motivo = f"pred_boost score={_ps:.0f}% calib={_pc:.0f}% dir={_dv}"
            # Se la direzione richiesta è diversa da quella attuale del campo,
            # emette direction_vote. La scena flippa prima di aprire.
            _dv_out = _dv if (current_direction and _dv != current_direction) else None
            _dv_conf = 0.95 if _dv_out else 0.0
            return self._out("ENTRA", 1.2, -5, _motivo, 0.85,
                             direction_vote=_dv_out,
                             direction_vote_confidence=_dv_conf)

        # Voti organi
        v = {}
        # Fingerprint
        v['oracolo_fp'] = (1.0 if fp_wr>=0.70 and fp_samples>=10 else
                           0.6 if fp_wr>=0.55 and fp_samples>=10 else
                           0.0 if fp_wr<=0.35 and fp_samples>=10 else 0.5)
        # Signal Tracker
        v['signal_tracker'] = (1.0 if st_hit_rate>=0.65 and st_pnl>0 and st_n>=10 else
                                0.6 if st_hit_rate>=0.55 and st_n>=10 else
                                0.0 if (st_hit_rate<=0.40 or st_pnl<-1) and st_n>=10 else 0.5)
        # Carica — voto basato sulla carica reale dell'Oracolo
        if oi_stato == "FUOCO":
            if oi_carica >= 0.80:   v['campo_carica'] = 1.0
            elif oi_carica >= 0.65: v['campo_carica'] = 0.85
            else:                   v['campo_carica'] = 0.70
        elif oi_stato == "CARICA":
            if oi_carica >= 0.80:   v['campo_carica'] = 0.75
            elif oi_carica >= 0.65: v['campo_carica'] = 0.60
            else:                   v['campo_carica'] = 0.45
        else:
            v['campo_carica'] = 0.1
        # Matrimonio
        v['matrimonio'] = (1.0 if matrimonio_trust>=0.7 and matrimonio_wr>=0.65 else
                           0.6 if matrimonio_wr>=0.55 else
                           0.0 if matrimonio_wr<=0.40 else 0.5)
        # Phantom
        if ph_protezione + ph_zavorra > 0:
            r = ph_protezione / (ph_protezione + ph_zavorra + 0.01)
            v['phantom_ratio'] = 1.0 if r>=0.80 else 0.7 if r>=0.60 else 0.2 if r<=0.40 else 0.5
        else:
            v['phantom_ratio'] = 0.5

        # ════════════════════════════════════════════════════════════════
        # PASSO 6 — I 3 VOTI NUOVI: Veritas, Capsule, Breath
        # Prima ricevuti-e-loggati (SC_INPUTS_FULL). Ora VOTANO nello score.
        # Retro-compat: input None → voto 0.5 (neutro) → non sposta st.
        # ════════════════════════════════════════════════════════════════

        # VERITAS — la testimonianza storica del contesto.
        # Mi fido del verdetto che Veritas dà già con la sua logica (n+wr).
        if veritas_last_judgement == 'TOSSICO':
            v['veritas'] = 0.0          # contesto certificato tossico → voto contro pieno
        elif veritas_last_judgement == 'BUONO':
            v['veritas'] = 1.0          # contesto certificato buono → voto a favore pieno
        elif veritas_ctx_wr is not None:
            # non certificato, ma c'è un wr di contesto
            if veritas_ctx_wr < 0.35:   v['veritas'] = 0.3   # cattivo ma non certificato
            elif veritas_ctx_wr >= 0.60: v['veritas'] = 0.8  # buono
            else:                        v['veritas'] = 0.5  # zona neutra
        else:
            v['veritas'] = 0.5          # IGNOTO / dati assenti → neutro

        # CAPSULE — il voto pesato degli anticorpi.
        # block_score alto = voto contro ; boost_score alto = voto a favore.
        # block_score reale osservato nei log: 180 sul contesto tossico.
        # ────────────────────────────────────────────────────────────────
        # INTERRUTTORE (8giu, Roberto): SC_CAPSULE_VOTO_OFF=true → il voto
        # capsule diventa NEUTRO (0.5): non pesa né contro né a favore, come
        # se le capsule tacessero nel voto. Serve a far scendere i segnali
        # oltre la porta 2 (voto) verso CROMO/ritardo, per riempire la
        # telemetria. NON tocca Veritas né gli altri organi. NON tocca il
        # veto FP_TOSSICO (porta 1, suo interruttore separato). NON tocca il
        # block delle capsule altrove (CROMO ecc.). Solo il VOTO.
        # Reversibile: SC_CAPSULE_VOTO_OFF=false (o tolto) → voto com'era.
        # ────────────────────────────────────────────────────────────────
        if os.environ.get("SC_CAPSULE_VOTO_OFF", "false").lower() == "true":
            v['capsule'] = 0.5          # capsule neutralizzate nel voto (da ENV)
        elif capsule_block_score is None and capsule_boost_score is None:
            v['capsule'] = 0.5          # nessun input → neutro
        else:
            _block = capsule_block_score if capsule_block_score is not None else 0.0
            _boost = capsule_boost_score if capsule_boost_score is not None else 0.0
            # block: 0→neutro, 100→inizia a pesare, 180+→pieno contro
            _block_v = max(0.0, 1.0 - max(0.0, _block - 0.0) / 180.0)  # 0→1.0, 180→0.0
            # boost: 0→neutro, 50→inizia, 100+→pieno a favore
            _boost_v = min(1.0, _boost / 100.0)                        # 0→0.0, 100→1.0
            if _block >= 100 and _boost < 50:
                v['capsule'] = round(_block_v, 3)        # domina il block
            elif _boost >= 50 and _block < 100:
                v['capsule'] = round(0.5 + _boost_v * 0.5, 3)  # domina il boost
            elif _block < 100 and _boost < 50:
                v['capsule'] = 0.5                       # nessuno dei due significativo
            else:
                # entrambi significativi: il netto decide
                v['capsule'] = round(max(0.0, min(1.0, 0.5 + (_boost_v - (1.0 - _block_v)) * 0.5)), 3)

        # BREATH — la fase respiratoria del mercato.
        # Voto morbido (contesto, non evidenza diretta). Peso 0.00 in Passo 6.
        if breath_fase in ('INALAZIONE', getattr(self, 'BREATH_INALAZ', 'INALAZIONE')):
            v['breath'] = 0.7 if (breath_energia is None or breath_energia >= 0.5) else 0.6
        elif breath_fase in ('ESALAZIONE', getattr(self, 'BREATH_ESALAZ', 'ESALAZIONE'),
                             'PICCO', getattr(self, 'BREATH_PICCO', 'PICCO')):
            v['breath'] = 0.4
        else:
            v['breath'] = 0.5           # NEUTRO / dati assenti

        # ── PESI DINAMICI — pred_score + OI amplificano campo_carica ──────
        # Veritas: FUOCO|PREVISTO_ENTRA 5579 segnali HIT 56% PnL +$0.32
        # Quando pred_score >= 70% + OI FUOCO: la predizione è fisica, non statistica.
        # campo_carica sale fino a 0.55 (da 0.30 default).
        # Gli altri pesi si ridistribuiscono proporzionalmente.
        # PATCH 0: SOLO se _pred["qualified"]. Senza qualifica, _pred_forza = 0,
        # pesi restano default (no amplificazione finta).
        _pred = self._get_pred_state()
        _ps = _pred["score"]
        _pc = _pred["calib"]
        _pred_forza = 0.0
        if _pred["qualified"] and oi_stato == "FUOCO" and _ps >= 70 and _pc >= 50:
            # Forza predizione: 0.0 (ps=70%) → 1.0 (ps=100%)
            _pred_forza = min(1.0, (_ps - 70) / 30) * min(1.0, oi_carica / 0.80)

        if _pred_forza > 0:
            # campo_carica sale da 0.22 a max 0.55 proporzionalmente alla forza
            _peso_cc_nuovo = self._pesi['campo_carica'] + _pred_forza * 0.25
            _peso_cc_nuovo = min(0.55, _peso_cc_nuovo)
            _delta = _peso_cc_nuovo - self._pesi['campo_carica']
            # Ridistribuisce il delta sottraendo proporzionalmente dagli altri.
            # PASSO 6: la lista include anche veritas e capsule (cedono peso
            # come tutti gli altri). breath è escluso — è a 0.00, non c'è
            # nulla da cedere e dividerebbe per zero.
            _altri = ['oracolo_fp', 'signal_tracker', 'veritas', 'capsule',
                      'matrimonio', 'phantom_ratio']
            _tot_altri = sum(self._pesi[k] for k in _altri)
            pesi_eff = dict(self._pesi)
            pesi_eff['campo_carica'] = _peso_cc_nuovo
            for k in _altri:
                pesi_eff[k] = max(0.03, self._pesi[k] - _delta * (self._pesi[k] / _tot_altri))
            # Rinormalizza
            _tot = sum(pesi_eff.values())
            pesi_eff = {k: round(v/_tot, 4) for k, v in pesi_eff.items()}
        else:
            pesi_eff = self._pesi

        # Score pesato con pesi effettivi (dinamici o default)
        st = sum(v[k] * pesi_eff[k] for k in v)

        # ════════════════════════════════════════════════════════════════
        # INTERRUTTORE B (8giu, Roberto): SC_VOTO_BYPASS=true
        # Salta il VOTO di SC: forza ENTRA così il segnale scende a
        # SEME/CROMO/ritardo, che diventano gli unici filtri. Serve a far
        # lavorare CROMO+ritardo (firma + tempo) senza il vecchio coprifuoco.
        # LOGGA ogni bypass: così nei dati i trade entrati col bypass si
        # distinguono da quelli entrati di diritto (esito misurabile a parte).
        # NON tocca il veto FP_TOSSICO (porta 1, suo interruttore) né i gate
        # a valle. Reversibile: SC_VOTO_BYPASS=false (o tolto) → voto com'era.
        # ⚠ In paper. Da esperimento, non da produzione: salta gli organi
        #   che riconoscono i pattern. Riaccendere il voto dopo la raccolta.
        # ════════════════════════════════════════════════════════════════
        _voto_bypass = os.environ.get("SC_VOTO_BYPASS", "false").lower() == "true"
        if _voto_bypass:
            _st_reale = st
            st = 0.70   # sopra 0.68 → ENTRA (giusto sopra soglia, non forzato al max)
            _now_byp = time.time()
            if _now_byp - getattr(self, "_voto_bypass_last_log", 0.0) > 30:
                self._voto_bypass_last_log = _now_byp
                print(f"[SC] 🔓 SC_VOTO_BYPASS attivo: salto il voto "
                      f"(score reale={_st_reale:.2f}) → ENTRA, decidono CROMO+ritardo")

        if st >= 0.68:
            azione = "ENTRA"
            sm = round(min(2.0, max(0.7, 0.7 + (st-0.68)/0.32*1.3)), 2)
            sa = -3 if st >= 0.80 else 0
        elif st >= 0.50:
            azione = "ATTENDI"; sm = 1.0; sa = 0
        else:
            azione = "BLOCCA";  sm = 0.5; sa = +3

        pro    = sum(1 for x in v.values() if x >= 0.6)
        contro = sum(1 for x in v.values() if x <= 0.3)
        _cc_eff = round(pesi_eff.get('campo_carica', self._pesi['campo_carica']), 2)
        motivo = (f"sc={st:.2f} pro={pro}/8 contro={contro}/8 "
                  f"cc_peso={_cc_eff:.2f} pred={_pred_forza:.2f} "
                  f"[ver={v['veritas']:.1f} cap={v['capsule']:.1f} bre={v['breath']:.1f}]")

        # ════════════════════════════════════════════════════════════════
        # FIX #32 (12mag2026 sera): DIRECTION VOTE
        # ════════════════════════════════════════════════════════════════
        # Se chiamante ha passato WR/samples della direzione opposta E 
        # current_direction, valuta se SHORT (o LONG) opposto ha edge 
        # nettamente migliore. Se sì, emette direction_vote.
        # Soglie: n_opposite>=20, WR_opposite>=0.65, delta>=0.15 vs WR corrente.
        # Confidenza vote: 0.0-1.0 = WR opposite scalato sopra 0.65.
        # ════════════════════════════════════════════════════════════════
        direction_vote = None
        direction_vote_confidence = 0.0
        if (fp_wr_opposite is not None and fp_samples_opposite is not None 
            and current_direction in ('LONG', 'SHORT')):
            try:
                _no = float(fp_samples_opposite or 0)
                _wo = float(fp_wr_opposite or 0)
                _nc = float(fp_samples or 0)
                _wc = float(fp_wr or 0)
                if _no >= 20 and _wo >= 0.65 and (_wo - _wc) >= 0.15:
                    direction_vote = 'SHORT' if current_direction == 'LONG' else 'LONG'
                    # confidenza 0.65→0, 1.0→1.0
                    direction_vote_confidence = round(min(1.0, (_wo - 0.65) / 0.35), 3)
                    motivo += f" | FLIP_VOTE={direction_vote}(WR_opp={_wo:.0%}n={int(_no)})"
            except Exception:
                pass

        # ════════════════════════════════════════════════════════════════════
        # PASSO 5b — SC DECIDE SUL FLIP PROPOSTO NEL VERBALE (14mag2026)
        # ════════════════════════════════════════════════════════════════════
        # Prima: il FLIP veniva deposto nel verbale (proposed_direction,
        # flip_confidence) e SC lo IGNORAVA. Risultato dai log: 20 trade LONG
        # di fila mentre il 10min andava DOWN e il FLIP proponeva SHORT.
        #
        # Ora: SC DECIDE sul FLIP. È una SECONDA via, complementare al
        # direction_vote di FIX #32:
        #  - direction_vote (FIX #32) → flip su evidenza STORICA (fingerprint opposto)
        #  - questo blocco          → flip su impulso FISICO (BYPASS_MAGNITUDE 10min)
        # Se direction_vote NON ha già deciso un flip, e il verbale porta un
        # proposed_direction con flip_confidence alta + coerenza Tsunami,
        # SC accetta il flip. La direzione la decide SC — Articolo 3.
        #
        # Soglie: nessun numero inventato.
        #  - flip_confidence >= 0.7 → il BYPASS_MAGNITUDE setta 1.0 (alta conf.)
        #  - coerenza con tsunami_direction → l'impulso deve essere multi-segnale
        # ════════════════════════════════════════════════════════════════════
        if direction_vote is None and proposed_direction in ('LONG', 'SHORT'):
            _fc2 = flip_confidence if flip_confidence is not None else 0.0
            _td2 = tsunami_direction
            # il FLIP è valido se: confidenza alta E (Tsunami concorda OPPURE
            # Tsunami non si esprime — il BYPASS_MAGNITUDE 10min è già di per sé
            # un segnale strutturato forte)
            _tsunami_concorda = (_td2 is None) or (_td2 == proposed_direction)
            if _fc2 >= 0.7 and _tsunami_concorda:
                direction_vote = proposed_direction
                direction_vote_confidence = round(min(1.0, _fc2), 3)
                motivo += (f" | FLIP_VERBALE={direction_vote}"
                           f"(conf={_fc2:.2f},ts={_td2})")

        return self._out(azione, sm, sa, motivo, st, v,
                         direction_vote=direction_vote,
                         direction_vote_confidence=direction_vote_confidence)

    def registra_esito(self, dec: dict, win: bool):
        """Dopo ogni trade adatta i pesi — gli organi precisi pesano di più."""
        self._storia.append({'voti': dec.get('voti',{}),'win': win})
        if len(self._storia) < 10: return
        ultimi = self._storia[-30:]
        for organo in self._pesi:
            vw = [t['voti'].get(organo,0.5) for t in ultimi if t['win']]
            vl = [t['voti'].get(organo,0.5) for t in ultimi if not t['win']]
            if not vw or not vl: continue
            disc = sum(vw)/len(vw) - sum(vl)/len(vl)
            if disc >= 0.15:
                self._pesi[organo] = min(0.45, self._pesi[organo]*1.05)
            elif disc <= -0.10:
                self._pesi[organo] = max(0.05, self._pesi[organo]*0.95)
        tot = sum(self._pesi.values())
        for k in self._pesi: self._pesi[k] = round(self._pesi[k]/tot, 4)

    def _out(self, azione, size_mult, soglia_adj, motivo, confidenza, voti={},
             direction_vote=None, direction_vote_confidence=0.0):
        # FIX #32: aggiunti direction_vote e direction_vote_confidence (opzionali, 
        # default None/0.0 per retro-compat con chiamate _out interne)
        return {'azione':azione,'size_mult':size_mult,'soglia_adj':soglia_adj,
                'motivo':motivo,'confidenza':round(confidenza,3),
                'voti':voti,'pesi':dict(self._pesi),
                'direction_vote': direction_vote,
                'direction_vote_confidence': direction_vote_confidence}


# ════════════════════════════════════════════════════════════════════════════
# PASSO 13 (15mag2026) — PREDITTORE CONTESTUALE
# ════════════════════════════════════════════════════════════════════════════
# La predizione esistente del bot (pred_score, pred_scostamento) è una
# tautologia: prezzo_attuale + delta_costante. Score 100% perché si confronta
# con sé stessa.
#
# Questo predittore è DIVERSO:
#  - calcola un DELTA DINAMICO ad ogni tick (un numero diverso ogni volta)
#  - basato sul CONTESTO ATTUALE (momentum, accelerazione, OI, regime, vol)
#  - SALVA la predizione, dopo 60s la confronta con il prezzo VERO
#  - misura errore_assoluto, accuracy del segno, calibrazione
#  - si certifica ONESTAMENTE sui dati veri
#
# NON sostituisce niente. Gira in parallelo al pred_* esistente. Espone le
# proprie statistiche come pred_v2_* nell'heartbeat.
# ════════════════════════════════════════════════════════════════════════════
class PredittoreContestuale:
    """Predittore dinamico onesto — numero diverso ogni tick, misura vs realtà."""

    WINDOW = 60          # orizzonte predizione: 60 secondi
    HISTORY_MAX = 300    # storia per momentum/accelerazione
    STATS_MAX = 500      # statistiche aggregate (errore, accuracy)

    def __init__(self):
        from collections import deque
        # Storia prezzi: (timestamp, prezzo) — per calcolare contesto
        self._prezzi = deque(maxlen=self.HISTORY_MAX)
        # Predizioni in volo: (ts_predizione, prezzo_allora, predizione_a_60s,
        #                       momentum, accelerazione, oi_carica, oi_dir)
        self._in_volo = deque(maxlen=self.HISTORY_MAX)
        # Statistiche aggregate (solo dopo verifica a 60s)
        self._errori_abs = deque(maxlen=self.STATS_MAX)
        self._segno_giusti = deque(maxlen=self.STATS_MAX)
        self._delta_predetti = deque(maxlen=self.STATS_MAX)
        self._delta_reali = deque(maxlen=self.STATS_MAX)
        self._n_predizioni = 0
        self._n_verificate = 0
        # Predizione corrente esposta sulla dashboard
        self._ultima_predizione = None
        self._ultimo_delta = 0.0

    def osserva(self, now: float, prezzo: float,
                oi_carica: float, oi_stato: str,
                oi_carica_short: float, oi_stato_short: str):
        """Chiamato ad ogni tick. Aggiorna storia, fa predizione, verifica vecchie."""
        self._prezzi.append((now, prezzo))

        # 1) VERIFICA predizioni vecchie di ~60s
        scaduti = []
        for i, (ts_p, p_allora, pred, mom, acc, oic, odir) in enumerate(self._in_volo):
            if now - ts_p >= self.WINDOW:
                # Tempo di misurare. Prezzo vero adesso vs predizione di 60s fa
                delta_reale = prezzo - p_allora
                delta_predetto = pred - p_allora
                errore = abs(pred - prezzo)
                self._errori_abs.append(errore)
                self._delta_predetti.append(delta_predetto)
                self._delta_reali.append(delta_reale)
                # Segno giusto?
                if abs(delta_predetto) >= 0.5:  # ignora predizioni "flat"
                    segno_ok = (delta_predetto > 0) == (delta_reale > 0)
                    self._segno_giusti.append(1 if segno_ok else 0)
                self._n_verificate += 1
                scaduti.append(i)
        # rimuovi dalla coda i verificati (dal fondo per non sballare gli indici)
        for i in reversed(scaduti):
            del self._in_volo[i]

        # 2) NUOVA PREDIZIONE basata sul contesto attuale
        if len(self._prezzi) >= 30:
            pred, delta, mom, acc = self._calcola_predizione(prezzo,
                                                              oi_carica, oi_stato,
                                                              oi_carica_short, oi_stato_short)
            self._in_volo.append((now, prezzo, pred, mom, acc, oi_carica,
                                  'LONG' if oi_stato == 'FUOCO' else
                                  'SHORT' if oi_stato_short == 'FUOCO' else 'FLAT'))
            self._ultima_predizione = pred
            self._ultimo_delta = delta
            self._n_predizioni += 1

    def _calcola_predizione(self, prezzo, oi_carica, oi_stato,
                            oi_carica_short, oi_stato_short):
        """
        Formula contestuale (prima taratura — i pesi si raffinano sui dati).

        delta_60s = momentum_30tick × 2.0
                  + drift_OI × 1.5
                  + accelerazione × 0.5

        - momentum_30tick: variazione media negli ultimi 30 tick
        - drift_OI: pressione direzionale dell'OI (FUOCO LONG spinge su, ecc.)
        - accelerazione: derivata seconda del prezzo (sta accelerando?)
        """
        prezzi_recenti = [p for _, p in list(self._prezzi)[-30:]]
        # momentum: variazione media tick-by-tick
        if len(prezzi_recenti) >= 2:
            momentum = (prezzi_recenti[-1] - prezzi_recenti[0]) / len(prezzi_recenti)
        else:
            momentum = 0.0
        # accelerazione: confronta momentum recente (ultimi 10) vs precedente (10 prima)
        if len(prezzi_recenti) >= 20:
            mom_recente = (prezzi_recenti[-1] - prezzi_recenti[-10]) / 10
            mom_prec = (prezzi_recenti[-11] - prezzi_recenti[-20]) / 10
            accelerazione = mom_recente - mom_prec
        else:
            accelerazione = 0.0
        # drift OI: se FUOCO LONG → +carica × 10 ; se FUOCO SHORT → -carica × 10
        # PASSO 15.G (15mag2026): peso OI ridotto da 1.5 a 0.3 (5x meno),
        # peso momentum aumentato da 2 a 5 (2.5x più),
        # peso accelerazione aumentato da 0.5 a 2 (4x più).
        # Motivo: V2 crollato al 40% accuracy quando si fidava troppo dell'OI.
        # OI dice "energia in arrivo" ma non DIREZIONE → sbaglia di sistema.
        # Momentum + accelerazione sono misure DIRETTE del prezzo, più affidabili.
        drift_oi = 0.0
        if oi_stato == 'FUOCO':
            drift_oi = oi_carica * 10.0
        elif oi_stato_short == 'FUOCO':
            drift_oi = -oi_carica_short * 10.0
        # formula combinata - V2 ribilanciato
        delta = momentum * 5.0 + drift_oi * 0.3 + accelerazione * 2.0
        pred = prezzo + delta
        return round(pred, 2), round(delta, 2), round(momentum, 3), round(accelerazione, 3)

    def get_stats(self) -> dict:
        """Statistiche aggregate — quelle vere, misurate contro il futuro reale."""
        import statistics as _st
        out = {
            'pred_v2_attiva': self._ultima_predizione,
            'pred_v2_delta':  self._ultimo_delta,
            'pred_v2_n_predizioni': self._n_predizioni,
            'pred_v2_n_verificate': self._n_verificate,
            'pred_v2_in_volo': len(self._in_volo),
        }
        if self._errori_abs:
            out['pred_v2_err_medio'] = round(_st.mean(self._errori_abs), 2)
            out['pred_v2_err_mediano'] = round(_st.median(self._errori_abs), 2)
        if self._segno_giusti:
            out['pred_v2_accuracy_segno'] = round(
                sum(self._segno_giusti) / len(self._segno_giusti) * 100, 1)
            out['pred_v2_n_segno'] = len(self._segno_giusti)
        if self._delta_reali:
            out['pred_v2_mov_reale_medio_abs'] = round(
                _st.mean([abs(x) for x in self._delta_reali]), 2)
        return out


class LibroPesca:
    """
    PASSO 15 (15mag2026) — TATTICA DI PESCA.

    Lenze piantate a orizzonti multipli (10/20/30/60/90s).
    Quando il prezzo entra nella zona di tolleranza (lasco) → CATTURATA.
    Apre trade paper indipendente al SC. Chiude all'orizzonte della lenza.
    Esito: VERA (PnL+) / BARATTOLO (PnL-) / SCADUTA (no trade).

    Persistito su sqlite. Sopravvive ai restart.

    MICRO 15.A: classe presente ma INERTE se LIBRO_PESCA_ENABLED=false (default).
    Per attivare → Render env: LIBRO_PESCA_ENABLED=true
    """

    ORIZZONTI = (60,)
    LASCO_PCT   = 0.15
    LASCO_FLOOR = 3.0
    LASCO_CAP   = 20.0
    COOLDOWN_S = 60.0           # cooldown PER STRATEGIA (3 strategie indipendenti)
    TRADE_SIZE_USD = 1000.0
    LEVERAGE       = 5.0
    FEE_PCT        = 0.0002
    # Filtri grossolani fingerprint (più larghi: cerchiamo edge nel micro)
    FP_WR_MIN_BASE = 0.55
    FP_N_MIN_BASE  = 20
    # ==== PASSO 15.J — 3 STRATEGIE PARALLELE ====
    # P1: WIN SIGNATURE — pattern esatto del WIN reale 23 marzo
    P1_DRIFT_LONG_MIN  = 0.60   # drift_persist >= 60% per LONG
    P1_POS_LONG_MAX    = 0.30   # range_pos <= 30% (bordo basso)
    P1_DRIFT_SHORT_MAX = 0.40   # drift_persist <= 40% per SHORT
    P1_POS_SHORT_MIN   = 0.70   # range_pos >= 70% (bordo alto)
    P1_COMPRESSION_MAX = 0.80   # compression <= 0.80 (molla un po' carica)
    # P2: V2 + CONFERMA DRIFT
    P2_V2_DELTA_MIN    = 30.0   # delta predetto >= $30 in valore assoluto
    P2_V2_N_MIN        = 50     # V2 maturo (n verificate >= 50)
    P2_V2_ACC_MIN      = 0.55   # V2 accuracy_segno >= 55% per fidarsi
    P2_DRIFT_CONFIRM_LONG  = 0.55  # drift_persist conferma LONG se >= 55%
    P2_DRIFT_CONFIRM_SHORT = 0.45  # drift_persist conferma SHORT se <= 45%
    # P3: COMPRESSIONE CHE PARTE
    P3_COMP_MAX        = 0.50   # compression <= 0.50 (molla MOLTO carica)
    P3_COMP_DUR_MIN    = 6      # da almeno 6 tick consecutivi
    P3_SLOPE_MIN_ABS   = 0.0001 # drift_slope > 0.0001 in val assoluto (sta accelerando)

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.enabled = LIBRO_PESCA_ENABLED
        self._in_volo = []
        self._ultima_piantata_long  = 0.0
        self._ultima_piantata_short = 0.0
        # 15.J: cooldown per strategia × direzione (6 contatori)
        self._ultime_piantate = {
            ('p1', 'LONG'): 0.0, ('p1', 'SHORT'): 0.0,
            ('p2', 'LONG'): 0.0, ('p2', 'SHORT'): 0.0,
            ('p3', 'LONG'): 0.0, ('p3', 'SHORT'): 0.0,
        }
        self._next_id = 1
        self._stats_cache = {}
        self._stats_cache_ts = 0.0
        # PASSO 15.B — protezione auto-disable se sqlite fallisce ripetutamente
        self._save_err_count = 0
        self._save_err_threshold = 10  # dopo 10 errori consecutivi, auto-disable
        # Init DB solo se abilitato
        if self.enabled:
            self._init_db()
            self._reload_next_id()
            log.info("[LIBRO_PESCA] ATTIVO")
        else:
            log.info("[LIBRO_PESCA] disattivato (kill-switch). Set LIBRO_PESCA_ENABLED=true per attivare")

    def _init_db(self):
        try:
            conn = _safe_connect(self.db_path, timeout=30)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS libro_pesca (
                    id INTEGER PRIMARY KEY,
                    ts_piantata REAL NOT NULL,
                    prezzo_lenza REAL NOT NULL,
                    direzione TEXT NOT NULL,
                    orizzonte_s INTEGER NOT NULL,
                    lasco REAL NOT NULL,
                    regime TEXT,
                    carica REAL,
                    oi_stato TEXT,
                    delta_atteso REAL,
                    stato TEXT NOT NULL,
                    ts_cattura REAL,
                    prezzo_cattura REAL,
                    ts_chiusura REAL,
                    prezzo_chiusura REAL,
                    pnl_paper REAL,
                    esito_finale TEXT
                )
            """)
            try:
                cur.execute("ALTER TABLE libro_pesca ADD COLUMN versione TEXT DEFAULT 'v15b'")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE libro_pesca ADD COLUMN fp_key TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE libro_pesca ADD COLUMN fp_wr REAL")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE libro_pesca ADD COLUMN fp_n INTEGER")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE libro_pesca ADD COLUMN strategia TEXT")
            except sqlite3.OperationalError:
                pass
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_orizzonte ON libro_pesca(orizzonte_s)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_stato ON libro_pesca(stato)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_esito ON libro_pesca(esito_finale)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_versione ON libro_pesca(versione)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lp_fp ON libro_pesca(fp_key)")
            conn.commit()
            conn.close()
            log.info(f"[LIBRO_PESCA] DB init {self.db_path}")
        except Exception as e:
            log.warning(f"[LIBRO_PESCA_INIT_DB_ERR] {e}")

    def _reload_next_id(self):
        try:
            conn = _safe_connect(self.db_path, timeout=30)
            cur = conn.cursor()
            cur.execute("SELECT MAX(id) FROM libro_pesca")
            row = cur.fetchone()
            conn.close()
            self._next_id = int(row[0]) + 1 if row and row[0] is not None else 1
            log.info(f"[LIBRO_PESCA] next_id={self._next_id}")
        except Exception as e:
            log.warning(f"[LIBRO_PESCA_RELOAD_ERR] {e}")
            self._next_id = 1

    def _save(self, L: dict):
        if not self.enabled:
            return
        try:
            conn = _safe_connect(self.db_path, timeout=30)
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO libro_pesca
                (id, ts_piantata, prezzo_lenza, direzione, orizzonte_s, lasco,
                 regime, carica, oi_stato, delta_atteso, stato, ts_cattura,
                 prezzo_cattura, ts_chiusura, prezzo_chiusura, pnl_paper, esito_finale,
                 versione, fp_key, fp_wr, fp_n, strategia)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                L['id'], L['ts_piantata'], L['prezzo_lenza'], L['direzione'],
                L['orizzonte_s'], L['lasco'], L.get('regime'), L.get('carica'),
                L.get('oi_stato'), L.get('delta_atteso'),
                L['stato'], L.get('ts_cattura'), L.get('prezzo_cattura'),
                L.get('ts_chiusura'), L.get('prezzo_chiusura'),
                L.get('pnl_paper'), L.get('esito_finale'),
                'v15j', L.get('fp_key'), L.get('fp_wr'), L.get('fp_n'),
                L.get('strategia', '?'),
            ))
            conn.commit()
            conn.close()
            if self._save_err_count > 0:
                self._save_err_count = 0
        except Exception as e:
            self._save_err_count += 1
            log.warning(f"[LIBRO_PESCA_SAVE_ERR] {e} (count={self._save_err_count})")
            if self._save_err_count >= self._save_err_threshold:
                self.enabled = False
                log.error(f"[LIBRO_PESCA_AUTO_DISABLE] {self._save_err_count} errori sqlite consecutivi → libro disattivato")

    def pianta_se_fingerprint_vincente(self, now, prezzo,
                                        momentum, volatility, trend,
                                        oracolo_memory, regime,
                                        delta_atteso,
                                        # 15.J: nuovi indicatori micro-contesto
                                        drift_persist=0.5, range_pos=0.5,
                                        compression=1.0, comp_duration=0,
                                        drift_slope=0.0,
                                        # 15.J: V2 stats per strategia P2
                                        v2_delta=0.0, v2_n=0, v2_accuracy=0.0):
        """
        15.J — VALUTA 3 STRATEGIE IN PARALLELO con entry IMMEDIATA.

        Ogni strategia ha:
          - cooldown indipendente (60s per strategia × direzione)
          - etichetta 'p1' / 'p2' / 'p3' salvata nel DB
          - entry immediata (no breakout tardiva)
          - chiusura a 60s dall'entry, PnL paper misurato

        P1 — WIN SIGNATURE: drift_persist + range_pos + compression
             (replica pattern del WIN reale 23 marzo)
        P2 — V2 + CONFERMA: V2 dice direzione, drift conferma
        P3 — COMPRESSIONE CHE PARTE: molla carica + drift_slope che parte

        Filtri comuni a tutte le 3:
          - regime != EXPLOSIVE (i 2 SHORT 23mar in EXPLOSIVE = entrambi LOSS)
          - fingerprint Oracolo abbastanza buono (WR>=55%, n>=20, pnl>0)
        """
        if not self.enabled:
            return
        if not momentum or not volatility or not trend:
            return  # warmup
        # Hard filter comune: no EXPLOSIVE
        if regime == 'EXPLOSIVE':
            return

        # Fingerprint Oracolo (filtro grossolano)
        fp_long  = f"LONG|{momentum}|{volatility}|{trend}"
        fp_short = f"SHORT|{momentum}|{volatility}|{trend}"
        def _stats(k):
            s = oracolo_memory.get(k) if oracolo_memory else None
            if not s:
                return (0.0, 0, 0.0)
            wins = float(s.get('wins', 0))
            samples = float(s.get('samples', 0))
            pnl_sum = float(s.get('pnl_sum', 0))
            wr = wins / samples if samples > 0 else 0.0
            return (wr, int(samples), pnl_sum)
        wr_l, n_l, pnl_l = _stats(fp_long)
        wr_s, n_s, pnl_s = _stats(fp_short)
        long_fp_ok  = (wr_l >= self.FP_WR_MIN_BASE and n_l >= self.FP_N_MIN_BASE and pnl_l > 0)
        short_fp_ok = (wr_s >= self.FP_WR_MIN_BASE and n_s >= self.FP_N_MIN_BASE and pnl_s > 0)
        if not long_fp_ok and not short_fp_ok:
            return

        # ─── P1: WIN SIGNATURE ────────────────────────────────────────
        # LONG:  drift_persist >= 0.60 AND range_pos <= 0.30 AND compression <= 0.80
        # SHORT: drift_persist <= 0.40 AND range_pos >= 0.70 AND compression <= 0.80
        p1_dir = None
        if long_fp_ok:
            if (drift_persist >= self.P1_DRIFT_LONG_MIN and
                range_pos <= self.P1_POS_LONG_MAX and
                compression <= self.P1_COMPRESSION_MAX):
                p1_dir = 'LONG'
        if short_fp_ok and p1_dir is None:
            if (drift_persist <= self.P1_DRIFT_SHORT_MAX and
                range_pos >= self.P1_POS_SHORT_MIN and
                compression <= self.P1_COMPRESSION_MAX):
                p1_dir = 'SHORT'
        if p1_dir:
            self._entra_strategia('p1', p1_dir, now, prezzo, regime,
                                   fp_long if p1_dir=='LONG' else fp_short,
                                   wr_l if p1_dir=='LONG' else wr_s,
                                   n_l if p1_dir=='LONG' else n_s,
                                   ctx=f"drift={drift_persist:.2f} pos={range_pos:.2f} comp={compression:.2f}")

        # ─── P2: V2 + CONFERMA DRIFT ──────────────────────────────────
        # V2 maturo (n>=50, acc>=55%) + |delta|>=$30 + drift conferma direzione
        p2_dir = None
        v2_mature = (v2_n >= self.P2_V2_N_MIN and v2_accuracy >= self.P2_V2_ACC_MIN)
        if v2_mature and abs(v2_delta) >= self.P2_V2_DELTA_MIN:
            v2_direzione = 'LONG' if v2_delta > 0 else 'SHORT'
            if v2_direzione == 'LONG' and long_fp_ok:
                if drift_persist >= self.P2_DRIFT_CONFIRM_LONG:
                    p2_dir = 'LONG'
            elif v2_direzione == 'SHORT' and short_fp_ok:
                if drift_persist <= self.P2_DRIFT_CONFIRM_SHORT:
                    p2_dir = 'SHORT'
        if p2_dir:
            self._entra_strategia('p2', p2_dir, now, prezzo, regime,
                                   fp_long if p2_dir=='LONG' else fp_short,
                                   wr_l if p2_dir=='LONG' else wr_s,
                                   n_l if p2_dir=='LONG' else n_s,
                                   ctx=f"v2_delta=${v2_delta:.1f} v2_acc={v2_accuracy:.0%} drift={drift_persist:.2f}")

        # ─── P3: COMPRESSIONE CHE PARTE ───────────────────────────────
        # compression <= 0.50 + comp_duration >= 6 + |drift_slope| > soglia
        # Direzione = segno del drift_slope
        p3_dir = None
        if compression <= self.P3_COMP_MAX and comp_duration >= self.P3_COMP_DUR_MIN:
            if abs(drift_slope) >= self.P3_SLOPE_MIN_ABS:
                slope_direzione = 'LONG' if drift_slope > 0 else 'SHORT'
                if slope_direzione == 'LONG' and long_fp_ok:
                    p3_dir = 'LONG'
                elif slope_direzione == 'SHORT' and short_fp_ok:
                    p3_dir = 'SHORT'
        if p3_dir:
            self._entra_strategia('p3', p3_dir, now, prezzo, regime,
                                   fp_long if p3_dir=='LONG' else fp_short,
                                   wr_l if p3_dir=='LONG' else wr_s,
                                   n_l if p3_dir=='LONG' else n_s,
                                   ctx=f"comp={compression:.2f} dur={comp_duration} slope={drift_slope:+.5f}")

    def _entra_strategia(self, strategia, direzione, now, prezzo, regime,
                          fp_key, fp_wr, fp_n, ctx=""):
        """15.J: Entry IMMEDIATA per una strategia. Cooldown 60s per strategia×dir."""
        key = (strategia, direzione)
        ultima = self._ultime_piantate.get(key, 0.0)
        if now - ultima < self.COOLDOWN_S:
            return
        L = {
            'id': self._next_id,
            'ts_piantata': now,
            'prezzo_lenza': round(prezzo, 2),    # in 15.J = prezzo ENTRY
            'direzione': direzione,
            'orizzonte_s': 60,
            'lasco': 0.0,
            'regime': regime,
            'carica': round(fp_wr, 3),
            'oi_stato': strategia.upper(),         # P1/P2/P3 nel campo oi_stato
            'delta_atteso': 0.0,
            'stato': 'CATTURATA',                  # entry immediata
            'ts_cattura': now,
            'prezzo_cattura': round(prezzo, 2),
            'ts_chiusura': None,
            'prezzo_chiusura': None,
            'pnl_paper': None,
            'esito_finale': None,
            'fp_key': fp_key,
            'fp_wr': round(fp_wr, 3),
            'fp_n': fp_n,
            'strategia': strategia,
        }
        self._next_id += 1
        self._in_volo.append(L)
        self._save(L)
        self._ultime_piantate[key] = now
        log.info(f"[LIBRO_PESCA_15J:{strategia.upper()}] entry {direzione} @${prezzo:.2f} | {ctx}")


    def pianta_se_radar_acceso(self, *args, **kwargs):
        """Compat 15.B/15.E — disabilitato in 15.F. Non chiamare."""
        return

    def tick(self, now, prezzo):
        """Verifica catture/scadenze/chiusure delle lenze in volo."""
        if not self.enabled:
            return
        rimanenti = []
        for L in self._in_volo:
            t_dalla_piantata = now - L['ts_piantata']
            if L['stato'] == 'ATTESA':
                catturata = False
                if L['direzione'] == "LONG":
                    if prezzo >= (L['prezzo_lenza'] - L['lasco']):
                        catturata = True
                else:
                    if prezzo <= (L['prezzo_lenza'] + L['lasco']):
                        catturata = True
                if catturata:
                    L['stato'] = 'CATTURATA'
                    L['ts_cattura'] = now
                    L['prezzo_cattura'] = round(prezzo, 2)
                    self._save(L)
                    rimanenti.append(L)
                elif t_dalla_piantata >= L['orizzonte_s']:
                    L['stato'] = 'SCADUTA'
                    L['ts_chiusura'] = now
                    L['prezzo_chiusura'] = round(prezzo, 2)
                    L['pnl_paper'] = 0.0
                    L['esito_finale'] = 'SCADUTA'
                    self._save(L)
                else:
                    rimanenti.append(L)
            elif L['stato'] == 'CATTURATA':
                if t_dalla_piantata >= L['orizzonte_s']:
                    L['ts_chiusura'] = now
                    L['prezzo_chiusura'] = round(prezzo, 2)
                    exp = self.TRADE_SIZE_USD * self.LEVERAGE
                    btc_qty = exp / max(1.0, L['prezzo_cattura'])
                    if L['direzione'] == "LONG":
                        delta = prezzo - L['prezzo_cattura']
                    else:
                        delta = L['prezzo_cattura'] - prezzo
                    fee = exp * self.FEE_PCT * 2.0
                    pnl = delta * btc_qty - fee
                    L['pnl_paper'] = round(pnl, 4)
                    L['stato'] = 'CHIUSA'
                    L['esito_finale'] = 'VERA' if pnl > 0 else 'BARATTOLO'
                    self._save(L)
                else:
                    rimanenti.append(L)
        self._in_volo = rimanenti

    def get_stats(self, now):
        """Aggregati dal libro per dashboard (cache 3s)."""
        if not self.enabled:
            return {
                'lp_enabled': False, 'lp_in_volo': 0, 'lp_totale': 0,
                'lp_vere': 0, 'lp_barattoli': 0, 'lp_scadute': 0,
                'lp_pnl_totale': 0.0, 'lp_pnl_vere': 0.0, 'lp_pnl_barattoli': 0.0,
                'lp_orizzonti': {}, 'lp_active': [],
            }
        if (now - self._stats_cache_ts) < 3.0 and self._stats_cache:
            return self._stats_cache
        out = {
            'lp_enabled': True, 'lp_in_volo': len(self._in_volo),
            'lp_totale': 0, 'lp_vere': 0, 'lp_barattoli': 0, 'lp_scadute': 0,
            'lp_pnl_totale': 0.0, 'lp_pnl_vere': 0.0, 'lp_pnl_barattoli': 0.0,
            'lp_orizzonti': {}, 'lp_active': [],
        }
        try:
            conn = _safe_connect(self.db_path, timeout=30)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM libro_pesca WHERE esito_finale='VERA' AND versione='v15j'")
            out['lp_vere'] = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM libro_pesca WHERE esito_finale='BARATTOLO' AND versione='v15j'")
            out['lp_barattoli'] = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM libro_pesca WHERE esito_finale='SCADUTA' AND versione='v15j'")
            out['lp_scadute'] = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM libro_pesca WHERE versione='v15j'")
            out['lp_totale'] = cur.fetchone()[0] or 0
            cur.execute("SELECT SUM(pnl_paper) FROM libro_pesca WHERE esito_finale='VERA' AND versione='v15j'")
            r = cur.fetchone()
            out['lp_pnl_vere'] = round(r[0], 2) if r and r[0] else 0.0
            cur.execute("SELECT SUM(pnl_paper) FROM libro_pesca WHERE esito_finale='BARATTOLO' AND versione='v15j'")
            r = cur.fetchone()
            out['lp_pnl_barattoli'] = round(r[0], 2) if r and r[0] else 0.0
            cur.execute("SELECT SUM(pnl_paper) FROM libro_pesca WHERE esito_finale IN ('VERA','BARATTOLO') AND versione='v15j'")
            r = cur.fetchone()
            out['lp_pnl_totale'] = round(r[0], 2) if r and r[0] else 0.0
            for h_s in self.ORIZZONTI:
                cur.execute("""
                    SELECT
                      SUM(CASE WHEN esito_finale='VERA' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN esito_finale='BARATTOLO' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN esito_finale='SCADUTA' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN stato IN ('ATTESA','CATTURATA') THEN 1 ELSE 0 END),
                      SUM(CASE WHEN esito_finale IN ('VERA','BARATTOLO') THEN pnl_paper ELSE 0 END)
                    FROM libro_pesca WHERE orizzonte_s = ? AND versione='v15j'
                """, (h_s,))
                r = cur.fetchone()
                vere = r[0] or 0
                bar  = r[1] or 0
                sca  = r[2] or 0
                vol  = r[3] or 0
                tot_eventi = vere + bar + sca
                pct = round(vere / tot_eventi * 100, 1) if tot_eventi > 0 else None
                out['lp_orizzonti'][str(h_s)] = {
                    'vere': vere, 'barattoli': bar, 'scadute': sca, 'in_volo': vol,
                    'pct_vincenti': pct,
                    'pnl_totale': round(r[4], 2) if r[4] is not None else 0.0,
                }
            # 15.J — breakdown per strategia (P1/P2/P3)
            out['lp_strategie'] = {}
            for strat in ('p1', 'p2', 'p3'):
                cur.execute("""
                    SELECT
                      SUM(CASE WHEN esito_finale='VERA' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN esito_finale='BARATTOLO' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN esito_finale IN ('VERA','BARATTOLO') THEN pnl_paper ELSE 0 END),
                      SUM(CASE WHEN stato='CATTURATA' THEN 1 ELSE 0 END)
                    FROM libro_pesca WHERE strategia = ? AND versione='v15j'
                """, (strat,))
                r = cur.fetchone()
                vere_s = r[0] or 0
                bar_s = r[1] or 0
                pnl_s = r[2] or 0.0
                in_volo_s = r[3] or 0
                tot_s = vere_s + bar_s
                wr_s_calc = round(vere_s / tot_s * 100, 1) if tot_s > 0 else None
                out['lp_strategie'][strat] = {
                    'vere': vere_s, 'barattoli': bar_s, 'in_volo': in_volo_s,
                    'totale_chiusi': tot_s,
                    'wr_pct': wr_s_calc,
                    'pnl_totale': round(pnl_s, 2),
                }
            conn.close()
        except Exception as e:
            log.debug(f"[LIBRO_PESCA_STATS_ERR] {e}")
        for L in self._in_volo[-30:]:
            out['lp_active'].append({
                'id': L['id'], 'ts_piantata': L['ts_piantata'],
                'prezzo_lenza': L['prezzo_lenza'], 'direzione': L['direzione'],
                'orizzonte_s': L['orizzonte_s'], 'lasco': L['lasco'],
                'stato': L['stato'],
                'ts_cattura': L.get('ts_cattura'),
                'prezzo_cattura': L.get('prezzo_cattura'),
            })
        self._stats_cache = out
        self._stats_cache_ts = now
        return out

    def get_recent_events(self, limit=50):
        """Ultimi N eventi conclusi (per grafico)."""
        if not self.enabled:
            return []
        out = []
        try:
            conn = _safe_connect(self.db_path, timeout=30)
            cur = conn.cursor()
            cur.execute("""
                SELECT id, ts_piantata, prezzo_lenza, direzione, orizzonte_s,
                       ts_cattura, prezzo_cattura, ts_chiusura, prezzo_chiusura,
                       pnl_paper, esito_finale
                FROM libro_pesca
                WHERE esito_finale IS NOT NULL AND versione='v15j'
                ORDER BY id DESC LIMIT ?
            """, (limit,))
            for r in cur.fetchall():
                out.append({
                    'id': r[0], 'ts_piantata': r[1], 'prezzo_lenza': r[2],
                    'direzione': r[3], 'orizzonte_s': r[4],
                    'ts_cattura': r[5], 'prezzo_cattura': r[6],
                    'ts_chiusura': r[7], 'prezzo_chiusura': r[8],
                    'pnl_paper': r[9], 'esito_finale': r[10],
                })
            conn.close()
        except Exception as e:
            log.debug(f"[LIBRO_PESCA_EVENTS_ERR] {e}")
        return out


class OvertopBassanoV16Production:
    """
    Bot BTC/USDC su Binance WebSocket.
    Modalita: PAPER_TRADE (simula) o LIVE (ordini reali).

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
        # ════════════════════════════════════════════════════════════════
        # FIX DATABASE LOCKED (5giu) — WAL + busy_timeout all'avvio.
        # Prima: sqlite3.connect nudo -> "database is locked" appena una query
        # diagnostica o la telemetry toccava il DB mentre il bot scriveva ->
        # il bot moriva (es. 11:27 del 5giu). WAL = letture e scritture non si
        # bloccano piu' a vicenda. busy_timeout = aspetta invece di mollare.
        # WAL e' una proprieta' del FILE: impostato una volta, vale per tutte
        # le connessioni successive del bot.
        try:
            import sqlite3 as _sq
            _c = _sq.connect(DB_PATH, timeout=10)
            _c.execute("PRAGMA journal_mode=WAL;")
            _c.execute("PRAGMA busy_timeout=5000;")
            _c.commit()
            _c.close()
        except Exception as _e:
            try:
                print(f"[DB] setup WAL fallito (non bloccante): {_e}")
            except Exception:
                pass

        self.symbol         = SYMBOL
        self.ws_url         = BINANCE_WS_URL
        self.paper_trade    = PAPER_TRADE

        # CONTATORE TRANS: carico il totale REALE dal DB (non riparte da zero al restart)
        try:
            import sqlite3 as _sqc
            _cc = _sqc.connect(DB_PATH, timeout=10)
            _cc.execute("""CREATE TABLE IF NOT EXISTS trans_bloccati (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                causa TEXT, vpress REAL, comp REAL, prezzo REAL)""")
            _rows = _cc.execute("SELECT causa, COUNT(*) FROM trans_bloccati GROUP BY causa").fetchall()
            _cc.close()
            self._cromo_blocchi = {"vol_basso":0,"vol_isterico":0,"comp_alta":0,"cdur_breve":0,"totale":0}
            for _ca, _n in _rows:
                if _ca in self._cromo_blocchi:
                    self._cromo_blocchi[_ca] = _n
                self._cromo_blocchi["totale"] += _n
        except Exception:
            self._cromo_blocchi = {"vol_basso":0,"vol_isterico":0,"comp_alta":0,"cdur_breve":0,"totale":0}

        self.heartbeat_data = heartbeat_data if heartbeat_data is not None else {}
        self.heartbeat_lock = heartbeat_lock
        self.db_execute     = db_execute

        # -- Persistenza --------------------------------------------------
        self._persist        = PersistenzaStato(db_path=DB_PATH)
        self.capital, self.total_trades = self._persist.load()
        # ── LEGGE FONDAMENTALE — TUTTO IN USDC ──────────────────────────────
        # MAI: pnl = price - entry         ← delta BTC puro, SBAGLIATO
        # SEMPRE:
        #   exposure  = TRADE_SIZE_USD x LEVERAGE   = $5000
        #   btc_qty   = exposure / entry_price
        #   pnl_lordo = delta x btc_qty              (live — fee esclusa)
        #   pnl_netto = pnl_lordo - FEE_TRADE        (al close — una sola volta)
        #   stop live = pnl_lordo < -STOP_LIVE = -$7 (netto ~-$5 dopo fee)
        # ────────────────────────────────────────────────────────────────────
        self.TRADE_SIZE_USD = 1000.0
        self.LEVERAGE       = 5
        self.EXPOSURE       = self.TRADE_SIZE_USD * self.LEVERAGE  # $5000
        self.FEE_PCT        = 0.0002
        self.FEE_TRADE      = self.EXPOSURE * self.FEE_PCT * 2     # $2.00 fissi
        self.STOP_LIVE      = 7.0  # lordo live — $7 lordi = ~$5 netti dopo fee

        # ── MICROSCOPIO VISIBILITA' ESTESA (1giu, opzione B) ──────────────
        # Nascita densa (0-10.5s ogni tick) + evoluzione campionata fino alla
        # chiusura. Regolabili da env senza redeploy.
        #   MICRO_PASSO_S   = ogni quanti secondi campionare DOPO i 10s (def 3)
        #   MICRO_MAX_PUNTI = tetto duro punti/curva → la lista non esplode mai
        self.MICRO_PASSO_S   = float(os.environ.get('MICRO_PASSO_S', '3.0'))
        self.MICRO_MAX_PUNTI = int(os.environ.get('MICRO_MAX_PUNTI', '200'))
        self.wins    = 0
        self.losses  = 0

        # -- Componenti core ----------------------------------------------
        self.analyzer        = ContestoAnalyzer(window=50)
        self.seed_scorer     = SeedScorer(window=50)
        self.oracolo         = OracoloDinamico()
        self.memoria         = MemoriaMatrimoni()
        
        # 🌊 TSUNAMI ENGINE — forza strutturata multi-scala
        if _TSUNAMI_AVAILABLE:
            self.tsunami = TsunamiEngine()
            log.info("[TSUNAMI] 🌊 TsunamiEngine inizializzato (30s + 2min + 10min)")
        else:
            self.tsunami = None

        # FIX VINCOLO B PATCH 0 (16mag2026) — signal_tracker spostato PRIMA del blocco
        # CapsuleManager. Bug latente pre-esistente: riga 6656 ramo fallback referenziava
        # self.signal_tracker prima che fosse inizializzato (riga 6667). In produzione
        # non si manifestava perché _CM_AVAILABLE=True salta il fallback, ma su ambienti
        # senza capsule_manager.py crashava.
        self.signal_tracker  = PreTradeSignalTracker()

        # -- CAPSULE MANAGER UNIFICATO ------------------------------------
        if _CM_AVAILABLE:
            self.capsule_manager = CapsuleManager(db_path=DB_PATH, asset=SYMBOL)
            # Alias per compatibilità con codice esistente
            self.capsule_runtime = self.capsule_manager
            self.config_reloader = self.capsule_manager
            self.realtime_engine = self.capsule_manager
            log.info(f"[CM] ✅ CapsuleManager attivo — asset={SYMBOL}")
        else:
            # Fallback ai sistemi originali
            self.capsule_manager = None
            self.capsule_runtime = CapsuleRuntime(capsule_file="capsule_attive.json")
            self.config_reloader = ConfigHotReloader(capsule_path="capsule_attive.json")
            self.realtime_engine = IntelligenzaAutonoma(capsule_file="capsule_attive.json", db_path=DB_PATH)
            # Collega Signal Tracker all'IA — genera capsule L2 senza aspettare trade reali
            self.realtime_engine._signal_tracker_ref = self.signal_tracker
            log.warning("[CM] ⚠️ Fallback ai sistemi originali")
        # -----------------------------------------------------------------

        # ═══════════════════════════════════════════════════════════════
        # PATCH 15 BUG 22 — Skill esterna CapsulaCanvas
        # Osserva tutto senza toccare il motore. Modulo indipendente.
        # Livelli L1+L2 ON di default, L3-L6 abilitabili via env.
        # ═══════════════════════════════════════════════════════════════
        self.canvas = None
        if _CANVAS_AVAILABLE:
            try:
                # FIX 2giu (Roberto — "l'occhio cieco dal 31mag, la radice"):
                # l'__init__ reale di CapsulaCanvas (capsula_canvas.py riga 205) è
                # `def __init__(self, db_path)` — NON accetta bot_ref. La chiamata
                # con bot_ref=self lanciava TypeError → except → self.canvas=None →
                # canvas mai creato → 1569 valutazioni, 0 scritture dal 31mag.
                # Tolto bot_ref: ora la firma combacia, il canvas nasce, l'occhio vede.
                self.canvas = CapsulaCanvas(db_path=DB_PATH)
                log.info(f"[CANVAS] ✅ skill caricata — stato={self.canvas.status()}")
            except Exception as _e_cv:
                log.warning(f"[CANVAS] init fallita (silenziato): {_e_cv}")
                self.canvas = None

        # ═══════════════════════════════════════════════════════════════
        # PATCH 17 — SKILL REGINA — CapsulaMemoria
        # Memoria viva per Claude tra sessioni. 5 stadi di crescita.
        # Auto-scoperte, narrazione Markdown, dialogo con canvas.
        # ═══════════════════════════════════════════════════════════════
        self.memoria = None
        self._memoria_sessione_id = None
        if _MEMORIA_AVAILABLE:
            try:
                self.memoria = CapsulaMemoria(
                    bot_ref=self,
                    db_path=DB_PATH,
                    canvas_ref=self.canvas
                )
                set_memoria(self.memoria)
                _stato_mem = self.memoria.stato_vitale()
                log.info(f"[MEMORIA] 👑 skill regina caricata — "
                         f"stadio={_stato_mem.get('stadio')} "
                         f"osservazioni={_stato_mem.get('osservazioni')}")
                # Apri sessione di lavoro automatica
                try:
                    self._memoria_sessione_id = self.memoria.apri_sessione(
                        ai_nome="V16_BOT",
                        ai_versione="PATCH17",
                        titolo=f"Sessione bot avviato {time.strftime('%Y-%m-%d %H:%M')}"
                    )
                except Exception as _e_sess:
                    log.debug(f"[MEMORIA] apri_sessione fallita: {_e_sess}")
            except Exception as _e_mem:
                log.warning(f"[MEMORIA] init fallita (silenziato): {_e_mem}")
                self.memoria = None

        # ═══════════════════════════════════════════════════════════════
        # PATCH 18 — CAPSULA FASE (25mag2026)
        # Scoperta Roberto: WIN/LOSS in cluster temporali, la fase del
        # mercato (delta prezzo 2/5/10 min) discrimina meglio dei 32
        # testimoni-opinione. Capsula adattiva con 6 livelli.
        # Default armato: L1 OCCHI + L2 MEMORIA + L3 PROPONE (observer).
        # L4 DECIDE arma blocco vero (default OFF).
        # ═══════════════════════════════════════════════════════════════
        self.capsula_fase = None
        if _FASE_AVAILABLE:
            try:
                self.capsula_fase = CapsulaFase(bot_ref=self, db_path=DB_PATH)
                _stato_fase = self.capsula_fase.get_verdetto()
                log.info(f"[CAPSULA_FASE] 🌊 v{_stato_fase.get('version')} attiva — "
                         f"stato={_stato_fase.get('stato')} "
                         f"soglie d10={_stato_fase.get('soglia_d10')} "
                         f"d5={_stato_fase.get('soglia_d5')} "
                         f"d2={_stato_fase.get('soglia_d2')}")
            except Exception as _e_fase:
                log.warning(f"[CAPSULA_FASE] init fallita (silenziato): {_e_fase}")
                self.capsula_fase = None

        # ═══════════════════════════════════════════════════════════════
        # PATCH 19 — CAPSULA TSUNAMI DISCORDE (27mag2026)
        # ═══════════════════════════════════════════════════════════════
        self.cap_tsunami = None
        if _CAP_TSUNAMI_AVAILABLE:
            try:
                self.cap_tsunami = CapsulaTsunamiDiscorde(db_path=DB_PATH)
                _stato_ts = self.cap_tsunami.get_verdetto()
                log.info(f"[CAP_TSUNAMI] 🌊 v{_stato_ts.get('version')} attiva — "
                         f"stato={_stato_ts.get('stato')} "
                         f"configurazioni={_stato_ts.get('mappa',{}).get('n_configurazioni',0)} "
                         f"bloccanti={_stato_ts.get('mappa',{}).get('n_bloccanti',0)}")
            except Exception as _e_cts:
                log.warning(f"[CAP_TSUNAMI] init fallita (silenziato): {_e_cts}")
                self.cap_tsunami = None

        # ═══════════════════════════════════════════════════════════════
        # PATCH 20 — CAPSULA MATRIGNA / SuperRisponditrice (27mag2026)
        # Genera capsule figlie adattogene per ogni firma di mercato.
        # Al boot legge winning_signatures+trades.data_json e popola.
        # ═══════════════════════════════════════════════════════════════
        # 24giu (Roberto: "togliere quello che non puo' portare al risultato,
        # inutile inseguire fantasmi"). La MATRIGNA decideva dentro
        # _evaluate_shadow_entry, che e' STACCATO (zero chiamate). Quindi non
        # blocca piu' nulla. Ma si caricava ancora all'avvio (64 capsule, log,
        # memoria) = cadavere che si sveglia a vuoto. RIMOSSO del tutto: non si
        # istanzia piu'. self.matrigna resta None. Un solo cervello, il nuovo.
        self.matrigna = None
        if False and _CAP_MATRIGNA_AVAILABLE:  # MATRIGNA MORTA — non caricare
            try:
                self.matrigna = CapsulaMatrigna(db_path=DB_PATH)
                _stato_mat = self.matrigna.get_verdetto()
                log.info(f"[CAP_MATRIGNA] 👵 v{_stato_mat.get('version')} attiva — "
                         f"stato={_stato_mat.get('stato')} "
                         f"capsule figlie={_stato_mat.get('capsule',{}).get('totali',0)} "
                         f"bloccanti={_stato_mat.get('capsule',{}).get('per_verdetto',{}).get('BLOCCA',0)}")
            except Exception as _e_mat:
                log.warning(f"[CAP_MATRIGNA] init fallita (silenziato): {_e_mat}")
                self.matrigna = None

        # ═══════════════════════════════════════════════════════════════
        # CAPSULA REGIME-EDGE (31mag2026) — osservatrice (L3) di default.
        # ═══════════════════════════════════════════════════════════════
        self.cap_regime_edge = None
        if _CAP_REGIME_EDGE_AVAILABLE:
            try:
                self.cap_regime_edge = CapsulaRegimeEdge(db_path=DB_PATH)
                _st_re = self.cap_regime_edge.stato()
                log.info(f"[CAP_REGIME_EDGE] 🧭 attiva — "
                         f"L4_decide={_st_re.get('l4_decide')} (default osservatrice)")
            except Exception as _e_re:
                log.warning(f"[CAP_REGIME_EDGE] init fallita (silenziato): {_e_re}")
                self.cap_regime_edge = None

        self.log_analyzer    = LogAnalyzer()
        self.ai_explainer    = AIExplainer(db_path=NARRATIVES_DB)
        self.calibratore     = AutoCalibratore()
        self.regime_detector = RegimeDetector()
        self.decelero        = MomentumDecelerometer()
        self.position_sizer  = PositionSizer()
        self.telemetry       = StabilityTelemetry()
        # signal_tracker già inizializzato sopra (FIX VINCOLO B)

        # ── L1.1 FIX: Init attributi _trade_peak_* ─────────────────────────
        # Bug pre-esistente latente: _trade_peak_pnl/ts/energia erano definiti
        # solo dentro _open_shadow_position. Se _evaluate_shadow_exit veniva
        # chiamato prima della prima open (caso impossibile con soglia 48 fissa,
        # ma possibile dopo L1 quando il bot inizia a tradare), AttributeError.
        # Soluzione: inizializzati a 0.0/None in __init__.
        self._trade_peak_pnl     = 0.0
        self._trade_peak_ts      = None
        self._trade_peak_energia = 0.0

        # ── CAPSULE EXECUTOR — ciclo di vita capsule eseguibili ──────────────
        if _CE_AVAILABLE:
            self.capsule_executor = CapsuleExecutor(DB_PATH, self)
            log.info("[CE] ✅ CapsuleExecutor attivo — capsule di codice eseguibile")
        else:
            self.capsule_executor = None

        # ── CAPSULE INTELLIGENTE — Sistema Immunitario Predittivo ──────────
        self.ci = CapsuleIntelligente()
        log.info("[CI] ✅ CapsuleIntelligente attiva — sistema immunitario predittivo")

        # -- Ripristina intelligenza accumulata ----------------------------
        self._persist.load_brain(self.oracolo, self.memoria, self.calibratore)
        self._persist.load_signal_tracker(self.signal_tracker)
        self._persist.load_runtime_state(self)
        self._regime_current = 'RANGING'
        self._regime_conf    = 0.0
        self._last_regime_check = time.time()

        # -- 5 Capsule -----------------------------------------------------
        self.capsule1 = Capsule1Coerenza()
        self.capsule2 = Capsule2Trappola()
        self.capsule3 = Capsule3Protezione()
        self.capsule4 = Capsule4Opportunita()
        self.capsule5 = Capsule5Tattica()

        # -- Stato trade ---------------------------------------------------
        self.trade_open         = None   # None = nessun trade aperto
        self.entry_time         = None
        self.entry_momentum     = None   # per divorce trigger 2
        self.entry_volatility   = None   # per divorce trigger 1
        self.entry_fingerprint  = None   # per divorce trigger 4
        self.entry_trend        = None
        self.max_price          = None
        self.current_matrimonio = None

        # -- Timing --------------------------------------------------------
        self.last_heartbeat    = time.time()
        self.last_config_check = time.time()
        self.last_persist      = time.time()
        self.ws                = None

        # -- Stato exit (per capsule reattive) -----------------------------
        self._last_exit_type     = None
        self._last_exit_duration = 0.0
        self._last_entry_seed    = 0.0   # per AutoCalibratore
        self._last_entry_fp_wr   = 0.72  # per AutoCalibratore
        self._trades_since_calib = 0     # contatore per calibrazione

        # -- Log live decisioni (ultimi 20 eventi) -------------------------
        self._live_log = deque(maxlen=20)

        # -- MOTORE 2: CAMPO GRAVITAZIONALE (shadow trading) --------------
        self.campo = CampoGravitazionale()
        self.campo._bot_ref = self  # riferimento al bot per CapsuleManager
        self._shadow = None          # shadow trade aperto (dict o None)
        self._shadow_entry_time = None
        self._shadow_entry_momentum = None
        self._shadow_entry_volatility = None
        self._shadow_entry_trend = None
        self._shadow_entry_fingerprint = None
        self._shadow_max_price = None
        self._shadow_min_price = None
        self._shadow_matrimonio = None

        # HOOK CAPSULE V2.0 — 22mag2026 (filosofia v2.0 Roberto)
        # Quando un'entry shadow viene aperta, chiediamo a /canvas/capsule_v2/per_contesto
        # se c'è una capsula OPERATORE che presidia questo contesto. Se sì, salviamo il suo
        # ID qui. All'exit, attribuiamo il PnL a quella capsula via /attribuisci_trade.
        # Tutto sotto flag env CAPSULE_V2_HOOK_ENABLED. Default OFF — nessun effetto sul bot.
        self._shadow_capsula_v2_attribuita = None

        # PATCH 6 BUG 13 — WinningSignatureLogger (osservatore puro)
        # Registra firma vincente all'exit, calcola match all'entry.
        # Non modifica decisioni del bot. Solo dati per il report.
        try:
            self._winsig = WinningSignatureLogger(DB_PATH)
        except Exception:
            self._winsig = None

        # ════════════════════════════════════════════════════════════════
        # PATCH 11 BUG 18a — Post-Win Rebalance Gate (memoria WIN_NET)
        # ════════════════════════════════════════════════════════════════
        # Diagnosi Roberto (18 mag 2026 mattina): "il secondo deve trovare
        # lo stesso ambiente del primo per agire correttamente". Caso reale:
        #   307  WIN  +$1.18  score 82.0  →
        #   308  LOSS -$3.63  score 76.5  (rientro a 3m18s)
        #   309  LOSS -$2.00  score 71.6  (rientro a poco)
        # Tutti formalmente sopra soglia 40, ma score in deterioramento.
        # PATCH 11 memorizza dato del WIN_NET e blocca rientri post-WIN
        # nello stesso fingerprint se score sta degradando.
        # ════════════════════════════════════════════════════════════════
        self._last_win_score       = None   # score al momento del WIN_NET
        self._last_win_soglia      = None   # soglia al momento del WIN_NET
        self._last_win_fingerprint = None   # mom|vol|trend|dir
        self._last_win_ts          = None   # timestamp chiusura WIN
        self._last_win_pnl         = None   # pnl_netto del WIN
        self._last_win_reason      = None   # reason del WIN

        # -- LATENCY TRACKER — misura slippage decisione→esecuzione -------
        # Registra la differenza tra prezzo al momento della DECISIONE
        # e prezzo al momento dell'APERTURA EFFETTIVA della shadow.
        # In EXPLOSIVE questa differenza rivela quanto la latenza ci costa.
        # Quando slippage_medio_explosive > 0.05% → serve VPS Frankfurt.
        self._latency_stats = {
            'n_total':          0,
            'n_explosive':      0,
            'slippage_sum':     0.0,
            'slippage_sum_exp': 0.0,
            'slippage_max':     0.0,
            'slippage_max_exp': 0.0,
            'costo_usd_tot':    0.0,
            'costo_usd_exp':    0.0,
            'storia':           [],
        }
        self._decision_price  = 0.0
        self._decision_ts     = 0.0
        self._decision_regime = ''

        # -- STATE ENGINE - AGGRESSIVO / NEUTRO / DIFENSIVO ----------------
        # Il tempismo. Non solo COSA fare, ma QUANDO NON FARLO.
        self._state = "NEUTRO"                   # AGGRESSIVO | NEUTRO | DIFENSIVO
        self._state_since = time.time()           # quando è entrato nello stato corrente
        self._state_min_duration = 120            # minimo 2 minuti in ogni stato
        self._m2_recent_trades = deque(maxlen=10) # ultimi 10 trade M2: {'ts', 'pnl', 'is_win', 'duration'}
        self._m2_last_loss_time = 0               # timestamp dell'ultimo loss
        self._m2_loss_streak = 0                  # loss consecutivi correnti
        self._m2_cooldown_until = 0               # non entrare fino a questo timestamp

        # ── L1.5 — VERITAS GATE: stats per contesto (mom|vol|trend) ─────
        # Traccia n, wins, pnl_sum per ogni contesto incontrato.
        # Permette di bloccare entry su contesti che hanno dato WR<20%
        # e pnl_avg<-1.0 dopo almeno 10 trade reali.
        self._m2_ctx_stats = {}   # {ctx_str: {'n', 'wins', 'pnl_sum'}}

        # L1.5 — Caricamento storico ctx_stats dal DB
        # Senza questo, dopo restart il gate Veritas riparte da 0.
        # Con questo, il gate è subito operativo basandosi su tutto lo storico.
        try:
            _conn_v = _safe_connect(DB_PATH, timeout=30)
            _ctx_rows = _conn_v.execute("""
                SELECT json_extract(data_json,'$.momentum') as m,
                       json_extract(data_json,'$.volatility') as v,
                       json_extract(data_json,'$.trend') as t,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
                       SUM(pnl) as pnl_sum
                FROM trades
                WHERE event_type='M2_EXIT' AND data_json IS NOT NULL
                GROUP BY 1,2,3
                HAVING n >= 1
            """).fetchall()
            _conn_v.close()
            for _row in _ctx_rows:
                if _row[0] and _row[1] and _row[2]:
                    _key = f"{_row[0]}|{_row[1]}|{_row[2]}"
                    self._m2_ctx_stats[_key] = {
                        'n':       _row[3],
                        'wins':    _row[4] or 0,
                        'pnl_sum': float(_row[5] or 0),
                    }
            if self._m2_ctx_stats:
                log.info(f"[VERITAS_LOAD] Caricati {len(self._m2_ctx_stats)} contesti dal DB:")
                for _k, _s in self._m2_ctx_stats.items():
                    _wr = _s['wins'] / _s['n'] if _s['n'] > 0 else 0
                    _pnl_avg = _s['pnl_sum'] / _s['n'] if _s['n'] > 0 else 0
                    log.info(f"[VERITAS_LOAD]   {_k}: n={_s['n']} wr={_wr:.0%} pnl_avg=${_pnl_avg:+.2f}")
        except Exception as _ve:
            log.warning(f"[VERITAS_LOAD] Errore caricamento storico: {_ve}")
        # -- AUTO-TUNING SOGLIA - impara dai phantom ----------------------
        # Il sistema legge i propri phantom e aggiusta SOGLIA_MIN automaticamente.
        # Se i phantom bloccati hanno WR > 60% su 10+ campioni → soglia troppo alta.
        # Se WR < 40% → soglia troppo bassa. Rate limit: 1 aggiustamento ogni 15 min.
        self._last_soglia_autotune = 0            # timestamp ultimo aggiustamento
        self._soglia_autotune_interval = 900      # 15 minuti tra aggiustamenti
        self._phantom_stats_snapshot = {}         # snapshot per delta calcolo
        # Stats separate per Motore 2 - ripristina da DB se disponibili
        self._m2_wins    = 0
        self._m2_losses  = 0
        self._m2_pnl     = 0.0
        self._m2_trades  = 0
        try:
            conn = _safe_connect(DB_PATH, timeout=30)
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
        self._last_price  = 0.0               # ultimo prezzo dal WebSocket
        self._last_m2_heartbeat = time.time() # heartbeat M2 - monitora se il thread è vivo

        # -- SUPERCERVELLO: decisore unificato ───────────────────────────
        self.supercervello  = SuperCervello()
        self._last_sc_dec   = None
        # -- VERITAS TRACKER: chi aveva ragione ───────────────────────────
        self.veritas        = VeritatisTracker(sc_ref=self.supercervello)
        self.veritas.load(DB_PATH)  # carica statistiche dal disco al boot

        # -- PASSO 13: PREDITTORE CONTESTUALE V2 (15mag2026) ──────────────
        # Predizione DINAMICA basata sul contesto attuale. Misura sé stesso
        # contro il prezzo reale a 60s. Onesto. Indipendente dal pred_* vecchio.
        self.predittore_v2 = PredittoreContestuale()

        # -- PASSO 15.A: LIBRO DI PESCA (15mag2026) ────────────────────────
        # Default DISABILITATO (kill-switch). Per attivare → Render env:
        # LIBRO_PESCA_ENABLED=true
        # Quando disabilitato la classe esiste ma non fa nulla.
        # Istanziazione protetta da try/except per non rompere mai il bot.
        try:
            self.libro_pesca = LibroPesca(DB_PATH)
        except Exception as _e_lp_init:
            log.error(f"[LIBRO_PESCA_INIT_ERR] {_e_lp_init} - libro_pesca disabilitato")
            self.libro_pesca = None

        # ════════════════════════════════════════════════════════════════
        # PATCH 0 (16mag2026) — VERITÀ PREDITTIVA AL BOOT
        # ════════════════════════════════════════════════════════════════
        # PRIMA: ramo Veritas n>=50 → _boot_pred calcolato; else → 70.0 hardcoded
        # ENTRAMBI i rami davano diritto di voto a un numero non qualificato:
        #   - Veritas calibra pesi SC, non è una predizione a 60s come V2
        #   - 70.0 hardcoded era esplicitamente teatro (commento riga 6906 stesso
        #     diceva "cold start (pred_score=0): soglia parte a 50")
        # ADESSO: entrambi i rami azzerano e marcano BOOT_MUTED.
        # Il valore Veritas storico viene SOLO loggato per diagnostica.
        # La qualifica vera per cella arriva in PATCH 3-4.
        _vt_fuoco = self.veritas._stats.get('FUOCO|PREVISTO_ENTRA', {})
        _vt_n     = _vt_fuoco.get('n', 0)
        _vt_hits  = _vt_fuoco.get('hits', 0) if _vt_n > 0 else 0
        _vt_hit_rate_storico = round(_vt_hits / _vt_n * 100, 1) if _vt_n >= 50 else None
        # Stato predittivo: SEMPRE neutro al boot
        self.supercervello._pred_score_ref = 0.0
        self.supercervello._pred_calib_ref = 0.0
        self.supercervello._pred_source = "BOOT_MUTED"
        self.supercervello._pred_decision_enabled = False
        if _vt_hit_rate_storico is not None:
            log.info(f"[BOOT] PRED_MUTED · Veritas storico hit_rate={_vt_hit_rate_storico}% "
                     f"n={_vt_n} (NON ha diritto di voto, solo diagnostica)")
        else:
            log.info(f"[BOOT] PRED_MUTED · Veritas n={_vt_n} insufficiente "
                     f"(soglia 50). V2 da qualificare prima del voto.")

        # -- ORACOLO INTERNO: sensore predittivo che vive ogni tick -------
        self._oi_carica     = 0.0        # energia accumulata 0→1
        self._oi_stato      = "ATTESA"   # ATTESA / CARICA / FUOCO
        self._oi_tick_pronto = 0         # tick consecutivi sopra soglia
        self._oi_ultimo_log  = 0.0       # timestamp ultimo log narrativo
        self._oi_narrativa   = []        # ultimi 20 messaggi narrativi
        self._oi_carica_history = []   # storia carica per grafico
        self._oi_carica_short   = 0.0  # carica ribassista speculare
        self._pred_trade_n      = 0    # predizioni confermate → trade
        self._pred_trade_pnl    = 0.0  # PnL cumulativo di quei trade

        # ════════════════════════════════════════════════════════════════
        # PATCH 1 (16mag2026) — TELEMETRIA PASSIVA DISTRIBUZIONE
        # Contatori per misurare la NUOVA distribuzione dopo fix percezione.
        # Non votano, non modificano soglie, non creano capsule.
        # Solo conteggio per osservazione 24h.
        # ════════════════════════════════════════════════════════════════
        self._tel_vol_distribution = {"ALTA": 0, "MEDIA": 0, "BASSA": 0}
        self._tel_vp_distribution = {"vp_lt_0.8": 0, "vp_0.8_1.1": 0,
                                       "vp_1.1_1.5": 0, "vp_gte_1.5": 0}
        self._tel_oi_stato_distribution = {"ATTESA": 0, "CARICA": 0, "FUOCO": 0}
        self._tel_ticks_total = 0

        # ════════════════════════════════════════════════════════════════
        # PASSO 10 (15mag2026) — PREDICTION TRACKER
        # ════════════════════════════════════════════════════════════════
        # Salva il prezzo a ogni tick. Dopo 60/90/120s misura il movimento
        # reale e lo confronta con la predizione corrente del bot.
        # NON decide niente. Solo registra. Espone statistiche su /trading/status.
        # ════════════════════════════════════════════════════════════════
        # FIX 15mag2026: deque è già importato globalmente a riga 44.
        # Avere "from collections import deque" qui rende deque LOCALE in
        # tutto __init__, causando UnboundLocalError a riga 6157 dove
        # deque era usata PRIMA di questo punto. Rimosso l'import locale.
        # storico (timestamp, prezzo) ultimi 200 secondi (margine per 120s+buffer)
        self._pt_storico = deque(maxlen=300)
        # statistiche aggregate per orizzonte: errori, accuracy segno
        self._pt_stats = {
            60:  {'n': 0, 'errori_abs': deque(maxlen=200),
                  'segno_giusto': deque(maxlen=200),
                  'movimenti_reali': deque(maxlen=200),
                  'predizioni_disponibili': 0, 'senza_predizione': 0},
            90:  {'n': 0, 'errori_abs': deque(maxlen=200),
                  'segno_giusto': deque(maxlen=200),
                  'movimenti_reali': deque(maxlen=200),
                  'predizioni_disponibili': 0, 'senza_predizione': 0},
            120: {'n': 0, 'errori_abs': deque(maxlen=200),
                  'segno_giusto': deque(maxlen=200),
                  'movimenti_reali': deque(maxlen=200),
                  'predizioni_disponibili': 0, 'senza_predizione': 0},
        }
        # snapshot delle predizioni: timestamp → predizione che il bot
        # avrebbe usato in quel momento (pred_score corrente + delta atteso)
        self._pt_predizioni = deque(maxlen=300)
        self._oi_stato_short    = "ATTESA"
        self._oi_tick_pronto_short = 0

        # -- BRIDGE COMMANDS READER ---------------------------------------
        self._bridge_cmd_file = "bridge_commands.json"
        self._last_bridge_check = time.time()

        # -- PHANTOM TRACKER - "se avessi fatto" -------------------------
        # Traccia i trade bloccati dai 5 livelli di protezione.
        # Per ogni trade bloccato, segue il prezzo e calcola cosa sarebbe successo.
        # Zavorra o protezione? I numeri rispondono.
        self._phantoms_open = []       # trade fantasma aperti (max 5 simultanei)
        self._phantoms_closed = deque(maxlen=100)  # ultimi 100 fantasmi chiusi
        self._phantom_stats = {        # statistiche per livello di blocco
            # 'BLOCK_REASON': {'blocked': N, 'would_win': N, 'would_lose': N, 'pnl_saved': $, 'pnl_missed': $}
        }
        self._phantom_log = deque(maxlen=20)  # log dedicato fantasmi

        # -- Bridge event queue (B4) — eventi urgenti per il bridge ----
        self._bridge_event_queue = []   # lista eventi: {name, payload, ts}
        self._bridge_last_event_check = 0

        # ── V16 ENGINES ────────────────────────────────────────────
        if _V16_ENGINES_OK:
            self._comparto  = CompartoEngine()
            self._nerv      = NervosismoEngine()
            self._breath    = BreathEngine()
            log.info("[V16] CompartoEngine + NervosismoEngine + BreathEngine attivi")

            # ════════════════════════════════════════════════════════════
            # FIX 2026-05-09 OPZIONE C — BOOT cauto + soglia adattiva
            # ════════════════════════════════════════════════════════════
            # Filosofia "volpe":
            # - Cold start (pred_score=0): soglia parte a 50 (cauta)
            # - Pred score sale: soglia floor scende automaticamente
            # - Pred score >= 70%: soglia può andare giù fino al comparto base
            # Il sistema NON è una manopola — si auto-calibra in base ai
            # propri dati Veritas/pred_score, senza intervento esterno.
            # ════════════════════════════════════════════════════════════
            try:
                if hasattr(self, 'campo'):
                    _old_base = getattr(self.campo, 'SOGLIA_BASE', None)
                    _old_min  = getattr(self.campo, 'SOGLIA_MIN',  None)
                    # Boot CAUTO: 50/44 (non scende a 38 finché pred non si è calibrata)
                    self.campo.SOGLIA_BASE = 50
                    self.campo.SOGLIA_MIN  = 44
                    log.info(f"[BOOT_RESET] SOGLIA boot CAUTO (pred_score=0): "
                             f"base {_old_base}→50  min {_old_min}→44")
                    log.info(f"[BOOT_RESET] La soglia floor si abbasserà automaticamente "
                             f"man mano che pred_score sale (50%→44, 70%→38).")
            except Exception as _br_e:
                log.warning(f"[BOOT_RESET] Errore: {_br_e}")
        else:
            self._comparto  = None
            self._nerv      = None
            self._breath    = None
            log.warning("[V16] Engines non disponibili — modalità V15 pura")

        # -- Banner --------------------------------------------------------
        mode_label = "📄 PAPER TRADE" if self.paper_trade else "🔴 LIVE TRADING"
        log.info("=" * 80)
        log.info(f"🚀 OVERTOP BASSANO V16 PRODUCTION - {mode_label}")
        log.info(f"   Capital: ${self.capital:,.2f}  |  Trades totali: {self.total_trades}")
        log.info(f"   SeedScorer threshold: {SEED_ENTRY_THRESHOLD}")
        log.info(f"   Divorce triggers minimi: {DIVORCE_MIN_TRIGGERS}/4")
        log.info(f"   🎯 MOTORE 2 (Campo Gravitazionale): SHADOW ATTIVO - confronto parallelo")
        log.info("=" * 80)
        if self.paper_trade:
            log.info("⚠️  PAPER TRADE ATTIVO - nessun ordine reale verra eseguito")

    # ========================================================================
    # CONNESSIONE BINANCE WEBSOCKET
    # ========================================================================

    def connect_binance(self):
        # Contatore tick per diagnostica (logga ogni N ricevuti)
        self._ws_tick_count = getattr(self, '_ws_tick_count', 0)
        self._ws_reconnect_count = getattr(self, '_ws_reconnect_count', 0)

        def on_message(ws, msg):
            try:
                # DIAG: logga ESPLICITAMENTE i primi 3 messaggi e poi ogni 100
                self._ws_tick_count += 1
                if self._ws_tick_count <= 3 or self._ws_tick_count % 500 == 0:
                    log.info(f"[WS_TICK_DIAG] msg#{self._ws_tick_count} len={len(msg)} preview={msg[:120]}")
                data   = json.loads(msg)
                price  = float(data.get('p', 0))
                volume = float(data.get('q', 1.0))
                if price > 0:
                    self.analyzer.add_price(price)
                    self.seed_scorer.add_tick(price, volume)
                    if self.tsunami is not None:
                        self.tsunami.feed_tick(price, volume)
                    self._last_volume = volume
                    # ════════════════════════════════════════════════════════
                    # FIX 18giu (Roberto: "il bot conta i tick ma non scrive
                    # piu' niente dalle 19:02"). PRIMA _process_tick era nello
                    # stesso try del parsing: se crashava ad ogni tick, l'errore
                    # veniva silenziato e il bot restava cieco (contatore sale,
                    # tabelle ferme). ORA _process_tick ha il SUO try che:
                    #  1) registra il crash COMPLETO (con traceback) nella tabella
                    #     crash_log del DB -> diagnosticabile con una query.
                    #  2) conta i crash consecutivi: se il motore crasha sempre,
                    #     il contatore _proc_crash_streak cresce e si vede.
                    # Cosi' il bot non resta mai "cieco in silenzio".
                    # ════════════════════════════════════════════════════════
                    try:
                        self._process_tick(price)
                        self._proc_crash_streak = 0   # ok: azzero lo streak
                    except Exception as _e_proc:
                        import traceback as _tb_proc
                        self._proc_crash_streak = getattr(self, "_proc_crash_streak", 0) + 1
                        _tb_str = _tb_proc.format_exc()
                        log.error(f"[PROC_TICK_CRASH] streak={self._proc_crash_streak} "
                                  f"{type(_e_proc).__name__}: {_e_proc}")
                        log.error(f"[PROC_TICK_TB] {_tb_str}")
                        # registra nel DB solo i primi crash e poi ogni 200, per non floodare
                        if self._proc_crash_streak <= 5 or self._proc_crash_streak % 200 == 0:
                            try:
                                _cc = _safe_connect(DB_PATH, timeout=10)
                                _cc.execute("""CREATE TABLE IF NOT EXISTS crash_log (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    ts TEXT DEFAULT CURRENT_TIMESTAMP,
                                    streak INTEGER, err_type TEXT, err_msg TEXT, traceback TEXT)""")
                                _cc.execute("""INSERT INTO crash_log (streak, err_type, err_msg, traceback)
                                    VALUES (?,?,?,?)""",
                                    (self._proc_crash_streak, type(_e_proc).__name__,
                                     str(_e_proc), _tb_str[:2000]))
                                _cc.commit()
                                _cc.close()
                            except Exception:
                                pass
                else:
                    log.warning(f"[WS_TICK_NOPRICE] msg#{self._ws_tick_count} data={data}")
            except Exception as e:
                log.error(f"[WS_MSG_ERR] {type(e).__name__}: {e} | msg[:200]={msg[:200] if msg else 'None'}")
                import traceback
                log.error(f"[WS_MSG_TB] {traceback.format_exc()}")


        def on_error(ws, error):
            log.error(f"[WS_ERROR] type={type(error).__name__} err={error}")

        def on_close(ws, code, msg):
            log.warning(f"[WS_CLOSE] code={code} msg={msg} tick_ricevuti={self._ws_tick_count} reconn={self._ws_reconnect_count}")

        def on_open(ws):
            log.info(f"[WS_OPEN] ✓ Connesso a {self.ws_url} (reconn={self._ws_reconnect_count})")

        log.info(f"[WS_CONNECT] Avvio connessione: {self.ws_url}")
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        # FIX 6giu — IL BUCO + riconnessione robusta.
        # 1) ping_interval: senza, run_forever non manda keepalive, Binance
        #    chiude in silenzio, tick congelati (il "fermo a 689").
        # 2) loop nello STESSO thread: run_forever esce -> aspetta 5s ->
        #    ricrea il WS e ripialla. Niente thread annidati. Riparte SEMPRE.
        def _ws_loop():
            while True:
                try:
                    # FIX 20giu — ping/pong timeout: con ping_timeout=10 stretto,
                    # su Render (server condiviso) il pong arriva tardi quando il
                    # thread e' occupato -> Binance chiude (reconn=34 nei log).
                    # Timeout piu' generoso (18s) + interval 20s = keepalive
                    # robusto. Configurabile da ENV se serve ritararlo.
                    _ping_int = int(os.environ.get("WS_PING_INTERVAL", "20"))
                    _ping_to  = int(os.environ.get("WS_PING_TIMEOUT", "18"))
                    self.ws.run_forever(ping_interval=_ping_int, ping_timeout=_ping_to)
                except Exception as _wre:
                    log.error(f"[WS_LOOP_ERR] {_wre}")
                self._ws_reconnect_count = getattr(self, '_ws_reconnect_count', 0) + 1
                log.warning(f"[WS_RECONNECT] WS caduto, riconnessione #{self._ws_reconnect_count} tra 5s "
                            f"(tick ricevuti finora={self._ws_tick_count})")
                time.sleep(5)
                try:
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_message=on_message,
                        on_error=on_error,
                        on_close=on_close,
                        on_open=on_open,
                    )
                except Exception as _rce:
                    log.error(f"[WS_RECREATE_ERR] {_rce}")
                    time.sleep(5)
        threading.Thread(target=_ws_loop, daemon=True, name="ws_thread").start()
        log.info(f"[WS_THREAD] Thread WS avviato (con ping keepalive + auto-reconnect)")

        # ════════════════════════════════════════════════════════════════════
        # WATCHDOG TICK (18giu, debito #1 segnalato da analisi esterna).
        # La riconnessione sopra scatta SOLO se run_forever ESCE (WS cade
        # pulito). Ma se Binance smette di mandare tick SENZA chiudere
        # (connessione "zombie": aperta ma muta), run_forever non esce e il
        # bot resta cieco. Il watchdog veglia: se non arriva un tick da piu'
        # di WS_WATCHDOG_SEC secondi, forza la chiusura del WS -> il loop di
        # riconnessione riparte. Solo sopravvivenza, non tocca il trading.
        # ENV: WS_WATCHDOG_SEC (default 60; 0 = spento).
        # ════════════════════════════════════════════════════════════════════
        self._ws_last_tick_ts = time.time()
        def _ws_watchdog():
            _wd_sec = float(os.environ.get("WS_WATCHDOG_SEC", "60"))
            if _wd_sec <= 0:
                return
            _last_seen_count = -1
            while True:
                time.sleep(_wd_sec / 2.0)
                try:
                    _now = time.time()
                    _cnt = getattr(self, "_ws_tick_count", 0)
                    # se il contatore e' avanzato dall'ultimo controllo, il flusso e' vivo
                    if _cnt != _last_seen_count:
                        _last_seen_count = _cnt
                        self._ws_last_tick_ts = _now
                        continue
                    # contatore fermo: da quanto?
                    _silenzio = _now - getattr(self, "_ws_last_tick_ts", _now)
                    if _silenzio >= _wd_sec:
                        log.error(f"[WS_WATCHDOG] nessun tick da {_silenzio:.0f}s "
                                  f"(contatore fermo a {_cnt}) — forzo riconnessione")
                        self._ws_last_tick_ts = _now
                        try:
                            self.ws.close()   # fa uscire run_forever -> _ws_loop riconnette
                        except Exception as _wce:
                            log.error(f"[WS_WATCHDOG_CLOSE_ERR] {_wce}")
                except Exception as _wde:
                    log.error(f"[WS_WATCHDOG_ERR] {_wde}")
        threading.Thread(target=_ws_watchdog, daemon=True, name="ws_watchdog").start()
        log.info(f"[WS_WATCHDOG] Watchdog tick avviato (soglia {os.environ.get('WS_WATCHDOG_SEC','60')}s)")


    # ========================================================================
    # PROCESS TICK - orchestratore principale
    # ========================================================================

    def _pt_track(self, now: float, price: float):
        """
        PASSO 10 — PREDICTION TRACKER (sola lettura, non decide nulla).

        Salva (ts, prezzo) ad ogni tick. Per ogni orizzonte (60/90/120s),
        cerca lo snapshot di esattamente quel tempo fa e misura:
         - movimento_reale = prezzo_ora - prezzo_allora
         - se c'era una predizione: errore_assoluto, segno_giusto
        Aggrega in self._pt_stats per esposizione su /trading/status.
        """
        # 1) snapshot del momento
        self._pt_storico.append((now, price))
        # snapshot della predizione corrente del bot (pred_score interno)
        try:
            # PATCH 0: diagnostico legge stato completo + salva source per filtri post-hoc
            _pred_st = self.supercervello._get_pred_state() if hasattr(self, 'supercervello') and self.supercervello is not None else {"score": 0.0, "source": "NO_SC", "qualified": False}
            _ps = _pred_st["score"]
            _pt_pred = {
                'ts': now,
                'price': price,
                'pred_score': _ps,
                # PATCH 0: source nel record diagnostico permette di filtrare
                # a posteriori i tick con predizione finta vs qualificata
                'pred_source': _pred_st["source"],
                'pred_qualified': _pred_st["qualified"],
                # delta predetto: se pred_score >= 70 e direzione campo nota,
                # il bot "punta" sulla direzione del campo. Magnitudo: derivata
                # da pred_score (più alto = più convinto, più grande il delta).
                # Stima rozza (best effort) — non è il predittore vero, è
                # quello che il bot ATTUALMENTE crede.
                'campo_dir': getattr(self.campo, '_direction', None),
                'delta_atteso': (_ps - 50.0) * 0.3 if _ps >= 50 else 0.0,
            }
            self._pt_predizioni.append(_pt_pred)
        except Exception:
            pass

        # 2) per ogni orizzonte, cerca lo snapshot di W secondi fa
        for W in (60, 90, 120):
            target_ts = now - W
            # trova lo snapshot più vicino a target_ts (entro ±2s di tolleranza)
            snap_old = None
            for (ts_old, price_old) in self._pt_storico:
                if abs(ts_old - target_ts) <= 2.0:
                    snap_old = (ts_old, price_old)
                    break
            if snap_old is None:
                continue
            # movimento reale a W secondi
            movimento_reale = price - snap_old[1]
            S = self._pt_stats[W]
            S['n'] += 1
            S['movimenti_reali'].append(movimento_reale)

            # c'era una predizione attiva di W secondi fa?
            pred_old = None
            for p in self._pt_predizioni:
                if abs(p['ts'] - target_ts) <= 2.0:
                    pred_old = p
                    break
            if pred_old is None or pred_old['pred_score'] < 50.0:
                S['senza_predizione'] += 1
                continue
            # confronto: la predizione di W fa diceva un delta, il movimento reale è
            S['predizioni_disponibili'] += 1
            delta_atteso = pred_old['delta_atteso']
            if pred_old['campo_dir'] == 'SHORT':
                delta_atteso = -delta_atteso
            errore_abs = abs(delta_atteso - movimento_reale)
            S['errori_abs'].append(errore_abs)
            # segno giusto?
            segno_predetto = 'UP' if delta_atteso > 0 else ('DOWN' if delta_atteso < 0 else 'FLAT')
            segno_reale = 'UP' if movimento_reale > 0 else ('DOWN' if movimento_reale < 0 else 'FLAT')
            if segno_predetto != 'FLAT':
                S['segno_giusto'].append(1 if segno_predetto == segno_reale else 0)

    def _pt_get_stats(self) -> dict:
        """Ritorna le statistiche aggregate del tracker per /trading/status."""
        import statistics as _st
        out = {}
        for W, S in self._pt_stats.items():
            row = {'n_misurati': S['n'],
                   'predizioni_disponibili': S['predizioni_disponibili'],
                   'senza_predizione': S['senza_predizione']}
            if S['errori_abs']:
                row['err_medio'] = round(_st.mean(S['errori_abs']), 2)
                row['err_mediano'] = round(_st.median(S['errori_abs']), 2)
            if S['segno_giusto']:
                row['accuracy_segno_pct'] = round(sum(S['segno_giusto']) / len(S['segno_giusto']) * 100, 1)
                row['n_segno_valutato'] = len(S['segno_giusto'])
            if S['movimenti_reali']:
                row['mov_reale_medio_abs'] = round(_st.mean([abs(x) for x in S['movimenti_reali']]), 2)
            out[f'{W}s'] = row
        return out

    def _process_tick(self, price: float):
        # ════════════════════════════════════════════════════════════════
        # BLINDAGGIO DIAGNOSTICO (18giu2026, notte — Roberto: "si e' bloccato
        # tutto"). I tick salivano (contatore +1 in on_message) ma niente si
        # scriveva: _process_tick crashava ad ogni tick dentro l'except
        # silenzioso di on_message. Qui catturo il crash, lo SCRIVO nel DB
        # (tabella crash_log) con riga esatta, e FAIL-CONTINUE: il bot non si
        # blocca piu'. Domani: SELECT dal crash_log -> riga colpevole -> fix.
        # ════════════════════════════════════════════════════════════════
        try:
            return self._process_tick_body(price)
        except Exception as _tick_err:
            import traceback as _tb
            _tbtxt = _tb.format_exc()
            # ultima riga del traceback nel nostro file = il punto del crash
            _riga = "?"
            try:
                for _ln in reversed(_tbtxt.splitlines()):
                    if 'BOT_VIVO' in _ln or 'OVERTOP_BASSANO' in _ln or '_process_tick_body' in _ln:
                        _riga = _ln.strip()
                        break
            except Exception:
                pass
            try:
                _dbp = getattr(self, 'db_path', None) or os.environ.get('DB_PATH', '/var/data/trading_data.db')
                _cx = _safe_connect(_dbp, timeout=30)
                _cx.execute("CREATE TABLE IF NOT EXISTS crash_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, err_type TEXT, err_msg TEXT, riga TEXT, tb TEXT)")
                _cx.execute("INSERT INTO crash_log (ts, err_type, err_msg, riga, tb) VALUES (?,?,?,?,?)",
                            (time.strftime('%Y-%m-%d %H:%M:%S'), type(_tick_err).__name__, str(_tick_err)[:300], _riga[:300], _tbtxt[:2000]))
                _cx.commit()
                _cx.close()
            except Exception:
                pass
            log.error(f"[TICK_CRASH] {type(_tick_err).__name__}: {_tick_err} @ {_riga}")
            # contatore in RAM per vedere se crasha ad OGNI tick
            self._tick_crash_n = getattr(self, '_tick_crash_n', 0) + 1
            return None


    def _process_tick_body(self, price: float):
        now = time.time()

        # Config hot-reload ogni 30 s
        if now - self.last_config_check > 30:
            if self.config_reloader.check_reload():
                if self.capsule_runtime.reload():
                    caps_attive = [c.get('capsule_id','?') for c in self.capsule_runtime.capsules if c.get('enabled')]
                    log.info(f"[CAPSULE_LOAD] {len(caps_attive)} capsule ricaricate: {caps_attive[:5]}")
                    self._log_m2("💊", f"[CAPSULE_LOAD] {len(caps_attive)} capsule attive")
                    self.telemetry.log_capsule_load(caps_attive)
            self.last_config_check = now

        # Bridge commands check ogni 30 s
        if now - self._last_bridge_check > 30:
            self._read_bridge_commands()
            self._last_bridge_check = now

        # Heartbeat ogni 30 s
        if now - self.last_heartbeat > 30:
            self._update_heartbeat()
            self.last_heartbeat = now

        # Aggiorna prezzo live ad ogni tick
        self._last_price = price

        # ════════════════════════════════════════════════════════════════
        # TRACKER PRIMI SECONDI (9giu, Roberto) — LA LUCE NEI PRIMI 3s
        # Il bot era cieco sotto i 10s. Qui campiono ogni segnale agganciato
        # a 1s/2s/3s dalla nascita: prezzo e variazione $ rispetto all'aggancio.
        # Cosi' si VEDE la curva nei primi secondi dove maschio e dopata si
        # separano (maschio tiene/sale, dopata gia' molla). Solo osservazione,
        # NON decide niente. Scrive in tabella primi_secondi.
        # ENV TRACK_PRIMI_SEC_OFF=true per spegnerlo. try/except: mai rompe il tick.
        # ════════════════════════════════════════════════════════════════
        try:
            if os.environ.get("TRACK_PRIMI_SEC_OFF", "false").lower() != "true":
                _ag_ts = getattr(self, "_rit_aggancio_ts", None)
                # timestamp AUTONOMO del tracker: parte al nuovo aggancio e dura
                # 10s anche dopo che il ritardo ha deciso (entra/scarta a ~4s).
                _ps_nascita = getattr(self, "_ps_nascita_ts", None)
                _ps_prezzo0 = getattr(self, "_ps_prezzo0", None)
                if _ag_ts is not None and _ag_ts != getattr(self, "_ps_last_ag_ts", None):
                    # nuovo aggancio rilevato: faccio nascere una nuova traccia
                    _ps_nascita = now
                    _ps_prezzo0 = price
                    self._ps_nascita_ts = now
                    self._ps_prezzo0 = price
                    self._ps_last_ag_ts = _ag_ts
                if _ps_nascita is not None:
                    _eta = now - _ps_nascita   # secondi di vita (autonomi, fino a 10s+)
                    if _eta > 12:
                        # traccia esaurita: chiudo, aspetto il prossimo aggancio
                        self._ps_nascita_ts = None
                    else:
                        _ag_prezzo = _ps_prezzo0 if _ps_prezzo0 is not None else price
                        if not hasattr(self, "_ps_campionati"):
                            self._ps_campionati = {}
                        _gia = self._ps_campionati.get(_ps_nascita, set())
                        _exp = float(os.environ.get("EXPOSURE_USD", "5000"))
                        for _sec in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                            # campiona al primo tick che supera _sec secondi di vita
                            if _eta >= _sec and _sec not in _gia:
                                _var_usd = ((price - _ag_prezzo) / _ag_prezzo) * _exp if _ag_prezzo else 0.0
                                _ps = None
                                try:
                                    import sqlite3 as _sq3ps
                                    _ps = _sq3ps.connect(DB_PATH, timeout=10)
                                    _ps.execute("PRAGMA busy_timeout=10000;")
                                    _ps.execute("""CREATE TABLE IF NOT EXISTS primi_secondi (
                                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                        aggancio_ts REAL, secondo INTEGER,
                                        prezzo_aggancio REAL, prezzo_ora REAL, var_usd REAL)""")
                                    _ps.execute(
                                        """INSERT INTO primi_secondi
                                           (aggancio_ts, secondo, prezzo_aggancio, prezzo_ora, var_usd)
                                           VALUES (?,?,?,?,?)""",
                                        (_ps_nascita, _sec, float(_ag_prezzo), float(price), float(_var_usd)))
                                    _ps.commit()
                                    _gia.add(_sec)
                                    self._ps_campionati[_ps_nascita] = _gia
                                except Exception:
                                    pass
                                finally:
                                    if _ps is not None:
                                        try: _ps.close()
                                        except Exception: pass
                        # pulizia: tengo solo gli ultimi 50 agganci tracciati
                        if len(self._ps_campionati) > 50:
                            for _k in list(self._ps_campionati.keys())[:-50]:
                                self._ps_campionati.pop(_k, None)
        except Exception:
            pass

        # ════════════════════════════════════════════════════════════════
        # PATCH 18 — CAPSULA FASE: alimentazione buffer prezzi (25mag2026)
        # ════════════════════════════════════════════════════════════════
        # try/except totale: mai bloccare il tick loop per la capsula.
        if self.capsula_fase is not None:
            try:
                self.capsula_fase.feed_tick(now, price)
            except Exception:
                pass

        # ════════════════════════════════════════════════════════════════
        # PASSO 10 — PREDICTION TRACKER (chiamato ogni tick, sola lettura)
        # ════════════════════════════════════════════════════════════════
        try:
            self._pt_track(now, price)
        except Exception as _e_pt:
            log.debug(f"[PT_TRACK_ERR] {_e_pt}")

        # ════════════════════════════════════════════════════════════════
        # PASSO 13 — PREDITTORE CONTESTUALE V2 (dinamico, onesto)
        # ════════════════════════════════════════════════════════════════
        try:
            self.predittore_v2.osserva(
                now, price,
                self._oi_carica, self._oi_stato,
                self._oi_carica_short, self._oi_stato_short
            )
        except Exception as _e_pv2:
            log.debug(f"[PRED_V2_ERR] {_e_pv2}")

        # ════════════════════════════════════════════════════════════════
        # PASSO 15.F — TATTICA DI PESCA SU FINGERPRINT VERITAS
        # ════════════════════════════════════════════════════════════════
        # PASSO 15.J — 3 STRATEGIE PARALLELE
        #   P1 = WIN signature (drift + range_pos + compression)
        #   P2 = V2 + conferma drift_persist
        #   P3 = Compressione che parte (comp_dur + drift_slope)
        # Ogni strategia: cooldown indipendente, etichetta nel DB, entry immediata.
        # ════════════════════════════════════════════════════════════════
        try:
            if self.libro_pesca is not None:
                self.libro_pesca.tick(now, price)
                _regime_now = getattr(self, '_regime_current',
                                      getattr(self, '_regime', 'UNKNOWN'))
                # Fingerprint LIVE
                try:
                    _drift = getattr(self.campo, '_last_drift_pct', 0.0)
                except Exception:
                    _drift = 0.0
                _mom, _vol, _trd = self.analyzer.analyze(regime=_regime_now, drift=_drift)
                _orac_mem = getattr(self.oracolo, '_memory', None)
                # 15.J — SeedScorer fornisce TUTTI gli indicatori micro
                _seed_data = self.seed_scorer.score() if hasattr(self, 'seed_scorer') else {}
                _drift_persist = _seed_data.get('drift_persist', 0.5)
                _range_pos     = _seed_data.get('range_pos', 0.5)
                _compression   = _seed_data.get('compression', 1.0)
                _comp_dur      = _seed_data.get('comp_duration', 0)
                _drift_slope   = _seed_data.get('drift_slope', 0.0)
                # 15.J — V2 stats per strategia P2
                _v2_delta = 0.0
                _v2_n = 0
                _v2_acc = 0.0
                try:
                    _pv2_stats = self.predittore_v2.get_stats()
                    _v2_delta = _pv2_stats.get('pred_v2_delta', 0.0)
                    _v2_n     = _pv2_stats.get('pred_v2_n_segno', 0)
                    _v2_acc   = (_pv2_stats.get('pred_v2_accuracy_segno', 0) or 0) / 100.0
                except Exception:
                    pass
                self.libro_pesca.pianta_se_fingerprint_vincente(
                    now, price,
                    _mom, _vol, _trd,
                    _orac_mem, _regime_now, 0.0,
                    drift_persist=_drift_persist,
                    range_pos=_range_pos,
                    compression=_compression,
                    comp_duration=_comp_dur,
                    drift_slope=_drift_slope,
                    v2_delta=_v2_delta,
                    v2_n=_v2_n,
                    v2_accuracy=_v2_acc,
                )
        except Exception as _e_lp:
            log.debug(f"[LIBRO_PESCA_TICK_ERR] {_e_lp}")

        # Aggiorna prezzo live ad ogni tick (per dashboard)
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["last_price"] = round(price, 2)
                self.heartbeat_data["last_tick"]  = datetime.utcnow().isoformat()
                self.heartbeat_data["tick_count"] = self.heartbeat_data.get("tick_count", 0) + 1
                self.heartbeat_data["symbol"]     = SYMBOL
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

            # ── AUTOCORRETTORE REGIME ─────────────────────────────────────
            # Finestra LUNGA (200 tick ~3-4 min) per trend sostenuto
            # Finestra BREVE (50 tick ~1 min) per reattività
            if regime == 'RANGING' and len(self.regime_detector.prices) >= 50:
                _prezzi_all = list(self.regime_detector.prices)

                # Finestra breve — movimento rapido
                _p50     = _prezzi_all[-50:]
                _move50  = (_p50[-1] - _p50[0]) / _p50[0] * 100
                _up50    = sum(1 for i in range(len(_p50)-1) if _p50[i+1] > _p50[i])
                _dir50   = _up50 / max(len(_p50)-1, 1)

                # Finestra lunga — trend sostenuto (se disponibile)
                _p200    = _prezzi_all[-200:] if len(_prezzi_all) >= 200 else _prezzi_all
                _move200 = (_p200[-1] - _p200[0]) / _p200[0] * 100
                _up200   = sum(1 for i in range(len(_p200)-1) if _p200[i+1] > _p200[i])
                _dir200  = _up200 / max(len(_p200)-1, 1)

                # TRENDING_BULL: movimento sostenuto in entrambe le finestre
                # oppure movimento forte in finestra breve
                _bull = ((_move200 > 0.15 and _dir200 > 0.54) or
                         (_move50  > 0.40 and _dir50  > 0.56))
                _bear = ((_move200 < -0.15 and _dir200 < 0.46) or
                         (_move50  < -0.40 and _dir50  < 0.44))

                if _bull:
                    self._regime_current = 'TRENDING_BULL'
                    self._regime_conf    = min(1.0, abs(_move200) / 0.5)
                    self._log("🔄", f"AUTOCORRETTORE: RANGING→TRENDING_BULL "
                                    f"(m50={_move50:+.2f}% m200={_move200:+.2f}% dir={_dir200:.2f})")
                elif _bear:
                    self._regime_current = 'TRENDING_BEAR'
                    self._regime_conf    = min(1.0, abs(_move200) / 0.5)
                    self._log("🔄", f"AUTOCORRETTORE: RANGING→TRENDING_BEAR "
                                    f"(m50={_move50:+.2f}% m200={_move200:+.2f}% dir={_dir200:.2f})")

        # Persistenza ogni 5 minuti
        if now - self.last_persist > 300:
            self._persist.save(self.capital, self.total_trades)
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)
            self._persist.save_signal_tracker(self.signal_tracker)
            self._persist.save_runtime_state(self)
            self.telemetry.persist_to_db(DB_PATH)
            self.last_persist = now

        # ════════════════════════════════════════════════════════════════
        # AUTO-PULITORE DB (6giu) — il DB si era gonfiato a 442MB da tabelle
        # di log esplose (canvas_snapshots, regime_edge_log, capsule_log,
        # capsula_fase_osservazioni, phantom_forensic = 1.5M righe) -> lock.
        # Questo, ogni 10 min, tiene solo le ultime N righe di ciascuna log.
        # Cosi' il DB NON puo' piu' rigonfiarsi, chiunque le scriva.
        # Le tabelle VERE (trades, signatures) NON sono toccate.
        # Interruttore: AUTOPULITORE_OFF=true lo spegne.
        # ════════════════════════════════════════════════════════════════
        if (os.environ.get("AUTOPULITORE_OFF", "false").lower() != "true"
                and now - getattr(self, "_last_dbclean", 0) > 600):
            self._last_dbclean = now
            _tabelle_log = {
                "canvas_snapshots": 2000, "regime_edge_log": 2000,
                "capsule_log": 2000, "capsula_fase_osservazioni": 2000,
                "phantom_forensic": 2000, "regime_edge_esiti": 2000,
                "capsula_fase_verdetti": 2000, "capsula_tsunami_verdetti": 2000,
                "canvas_shadow_log": 2000, "libro_pesca": 2000,
            }
            _cl = None
            try:
                import sqlite3 as _scl
                _cl = _scl.connect(DB_PATH, timeout=10)
                _cl.execute("PRAGMA busy_timeout=10000;")
                for _tname, _keep in _tabelle_log.items():
                    try:
                        _n = _cl.execute(f"SELECT COUNT(*) FROM {_tname}").fetchone()[0]
                        if _n > _keep * 1.5:  # pulisco solo se ben oltre la soglia
                            _cl.execute(
                                f"DELETE FROM {_tname} WHERE id NOT IN "
                                f"(SELECT id FROM {_tname} ORDER BY id DESC LIMIT {_keep})")
                            _cl.commit()
                            self._log("🧹", f"AUTOPULITORE: {_tname} {_n}->{_keep} righe")
                    except Exception:
                        pass  # tabella inesistente o senza id: salto
            except Exception:
                pass
            finally:
                if _cl is not None:
                    try:
                        _cl.close()
                    except Exception:
                        pass

        # AUTO-TUNE soglia — DISABILITATO in V15+V16
        # L'AutoCalibratore alzava la soglia fino a 83 — il CompartoEngine gestisce le soglie
        # self._auto_tune_soglia()

        # ── PHANTOM SUPERVISOR — loop chiuso ogni 60s ───────────────────
        # Aggiorna _phantom_per_livello dal heartbeat
        if self.heartbeat_data:
            _ph = self.heartbeat_data.get('phantom', {})
            if isinstance(_ph, dict):
                self._phantom_per_livello = _ph.get('per_livello', {})
        self._phantom_supervisor()

        # ── PULIZIA CAPSULE DUPLICATE — ogni 5 minuti ────────────────────
        _now_clean = time.time()
        if _now_clean - getattr(self, '_last_capsule_cleanup', 0) > 300:
            self._last_capsule_cleanup = _now_clean
            try:
                import sqlite3 as _sc
                import json as _jc
                _conn = _sc.connect(DB_PATH, timeout=5)
                _rows = _conn.execute(
                    "SELECT id, trigger_json, azione_json, samples FROM capsule "
                    "WHERE enabled=1 AND livello='AUTO'"
                ).fetchall()
                _gruppi = {}
                for _rid, _trj, _azj, _smp in _rows:
                    try:
                        _tr = _jc.loads(_trj or '[]')
                        _az = _jc.loads(_azj or '{}')
                        _key = (
                            tuple(sorted((t.get('param',''), t.get('op',''), str(t.get('value',''))) for t in _tr)),
                            _az.get('type','')
                        )
                        if _key not in _gruppi:
                            _gruppi[_key] = []
                        _gruppi[_key].append((_rid, _smp or 0))
                    except Exception:
                        pass
                _eliminati = 0
                for _key, _lista in _gruppi.items():
                    if len(_lista) <= 1:
                        continue
                    _lista.sort(key=lambda x: x[1], reverse=True)
                    for _del_id in [r[0] for r in _lista[1:]]:
                        _conn.execute("DELETE FROM capsule WHERE id=?", (_del_id,))
                        _eliminati += 1
                if _eliminati > 0:
                    _conn.commit()
                    self._log_m2("🧹", f"PULIZIA_CAPSULE: {_eliminati} duplicati AUTO eliminati")
                _conn.close()
            except Exception:
                pass

        # ── V16: NervosismoEngine + CompartoEngine ad ogni tick ──────────
        if _V16_ENGINES_OK and self._nerv and self._comparto:
            _regime_now = self._regime_current
            _vol_now    = self._last_volatility if hasattr(self, '_last_volatility') else "MEDIA"
            _trend_now  = self._last_trend if hasattr(self, '_last_trend') else "SIDEWAYS"
            _dir_now    = self.campo._direction if hasattr(self.campo, '_direction') else "LONG"

            # NervosismoEngine — calcola tensione mercato
            _nerv_skills = {}  # non abbiamo skill V16 qui, usa solo per calcolo
            _nerv_stato  = self._nerv.on_tick(price, _regime_now, _dir_now, _nerv_skills)
            _nerv_val    = _nerv_stato.get("nervosismo", 0.3)
            _gomme       = _nerv_stato.get("gomme", "INTER")

            # BreathEngine — fase dell'impulso
            if self._breath:
                self._breath.on_tick(price, self._last_volume if hasattr(self, '_last_volume') else 1.0)

            # CompartoEngine — switcha assetto se il mercato si dichiara
            _comp_skills = {
                "ENTRY_SOGLIA": self.campo,   # il campo gestisce la soglia
            }
            _comp_nome = self._comparto.on_tick(_regime_now, _vol_now, _trend_now, {})

            # Applica soglia del comparto attivo al CampoGravitazionale
            _comp_attivo = COMPARTI.get(_comp_nome)
            if _comp_attivo and hasattr(self.campo, 'SOGLIA_BASE'):
                # Solo se il comparto suggerisce una soglia diversa da quella corrente
                _soglia_comp = _comp_attivo.soglia_base
                if self.campo.SOGLIA_BASE != _soglia_comp:
                    self.campo.SOGLIA_BASE = _soglia_comp
                    self.campo.SOGLIA_MIN  = _comp_attivo.soglia_min

            # Aggiorna heartbeat con dati V16
            if self.heartbeat_lock:
                try:
                    self.heartbeat_lock.acquire()
                    if self.heartbeat_data is not None:
                        self.heartbeat_data["comparto"]    = _comp_nome
                        self.heartbeat_data["nervosismo"]  = _nerv_val
                        self.heartbeat_data["gomme"]       = _gomme
                        self.heartbeat_data["breath_fase"] = self._breath._fase if self._breath else "NEUTRO"
                finally:
                    self.heartbeat_lock.release()

        # ── CAPSULE INTELLIGENTE — tick predittivo ad ogni ciclo ──────────
        try:
            _ci_ctx = {
                'breath_fase':   (self._breath._fase    if _V16_ENGINES_OK and self._breath   else 'NEUTRO'),
                'breath_energia':(self._breath._energia if _V16_ENGINES_OK and self._breath   else 0.5),
                'nervosismo':    (self._nerv._nervosismo if _V16_ENGINES_OK and self._nerv    else 0.3),
                'gomme':         (self._nerv._gomme_attuale if _V16_ENGINES_OK and self._nerv else 'INTER'),
                'comparto':      (self._comparto._attivo if _V16_ENGINES_OK and self._comparto else 'NEUTRO'),
                'oi_stato':      self._oi_stato,
                'oi_carica':     self._oi_carica,
                'regime':        self._regime_current,
                'drift':         _drift_for_classify if '_drift_for_classify' in dir() else 0.0,
                'momentum':      getattr(self, '_last_momentum', 'MEDIO'),
                'phantom_stats': self._phantom_stats if hasattr(self, '_phantom_stats') else {},
                'sc_pesi':       self.supercervello._pesi if hasattr(self, 'supercervello') else {},
                'signal_top':    self.signal_tracker.dump_top(5) if hasattr(self, 'signal_tracker') else [],
            }
            self.ci.tick(_ci_ctx)

            # ── CAPSULE DAL RAGIONATORE AI → CI ──────────────────────
            # Legge capsule generate dal Narratore e le inietta nella CI
            # Gerarchia: capsule CI esistenti hanno sempre precedenza
            _ra_iniettate = []
            _ra_bloccate  = []
            try:
                _caps_ra = (self.heartbeat_data or {}).get("capsule_ragionatore", [])
                _now_ra  = time.time()

                # REGOLA FISICA: BLOCCA_CONTESTO prevale su ABBASSA_SOGLIA stesso contesto
                _contesti_bloccati = set()
                for _cap in _caps_ra:
                    if _cap.get('azione') == 'BLOCCA_CONTESTO':
                        _p = _cap.get('params', {})
                        _key = f"{_p.get('momentum','')}|{_p.get('volatility','')}|{_p.get('trend','')}"
                        _contesti_bloccati.add(_key)
                _caps_ra = [
                    c for c in _caps_ra
                    if not (
                        c.get('azione') == 'ABBASSA_SOGLIA' and
                        f"{c.get('params',{}).get('momentum','')}|{c.get('params',{}).get('volatility','')}|{c.get('params',{}).get('trend','')}" in _contesti_bloccati
                    )
                ]

                for _cap in _caps_ra:
                    _cid = _cap.get('id', '')
                    if not _cid:
                        continue
                    # Non sovrascrive capsule CI già attive
                    if _cid in self.ci._capsule_attive:
                        _ra_bloccate.append(f"{_cid}(già_attiva)")
                        continue
                    # Capsule PERMANENTE_DB non scadono mai
                    _fonte = _cap.get('fonte', '')
                    if _fonte != 'PERMANENTE_DB':
                        try:
                            from datetime import datetime as _dt2
                            _cap_ts = _dt2.fromisoformat(_cap.get('ts','')).timestamp()
                            _vita_cap = max(600, _cap.get('vita', 600))  # minimo 600s
                            if _now_ra - _cap_ts > _vita_cap:
                                _ra_bloccate.append(f"{_cid}(scaduta)")
                                continue
                        except Exception:
                            pass
                    # Forza: permanenti a piena forza, temporanee limitate
                    _forza = min(0.85, _cap.get('forza', 0.65)) if _fonte == 'PERMANENTE_DB' else min(0.65, _cap.get('forza', 0.5))
                    self.ci._attiva_capsula(_cid, {
                        'id':     _cid,
                        'tipo':   'OPPORTUNITA',
                        'azione': _cap.get('azione', 'ABBASSA_SOGLIA'),
                        'params': _cap.get('params', {'delta': -5}),
                        'motivo': f"[RA] {_cap.get('motivo','')[:60]}",
                        'vita':   _cap.get('vita', 300),
                        'ts_nato': _now_ra,
                        'forza':  _forza,
                    })
                    _ra_iniettate.append(_cid)
                    log.info(f"[CI] 🤖 RA capsula→CI: {_cid} forza={_forza:.2f}")
                    _azione_cap = _cap.get('azione', '')
                    _n_trades = (self.heartbeat_data or {}).get('m2_trades', 0)
                    _wr_now = (self.heartbeat_data or {}).get('m2_wr', 1.0)
                    _last_ctx = (self.heartbeat_data or {}).get('narratore_trade_stats', {}).get('last_context', '')
                    _trade_analisi = (self.heartbeat_data or {}).get('trade_analisi', [])
                    _ultima_analisi = _trade_analisi[-1].get('analisi', '') if _trade_analisi else ''
                    # ════════════════════════════════════════════════════════
                    # PASSO A FIX AMNESIA (29mag, Roberto): prima i blocchi NON
                    # venivano salvati ('genera loop vizioso') → ogni riavvio il
                    # bot dimenticava quali firme erano tossiche e ricominciava a
                    # perderci (435 trade RANGING WR16.8% = -$697, costo amnesia).
                    # ORA i blocchi PERSISTONO. Il loop vizioso (blocco eterno) è
                    # evitato NON dall'amnesia ma dalla REGOLA ANTIAEREA:
                    # _pulisci_blocco_se_win() cancella il blocco quando 2 WIN
                    # consecutivi della stessa firma dimostrano che il contesto
                    # è cambiato. Memoria che persiste ma non è prigione perpetua.
                    # ════════════════════════════════════════════════════════
                    _salva = True  # tutte le capsule persistono, inclusi i blocchi
                    if _salva:
                        _prompt_ctx = (
                            f"MEMORIA per {_last_ctx}: {_azione_cap} "
                            f"n={_n_trades} wr={round(_wr_now*100)}% — {_ultima_analisi[:200]}"
                        ) if _ultima_analisi else ''
                        # Firma completa nei params: serve all'antiaerea per
                        # sapere QUALE firma questo blocco protegge.
                        _params_save = dict(_cap.get('params', {}))
                        self._salva_capsula_permanente({
                            'id': _cid, 'azione': _azione_cap,
                            'params': _params_save,
                            'motivo': _cap.get('motivo', ''),
                            'forza': _forza, 'contesto': _last_ctx,
                            'ts': _cap.get('ts', ''),
                            'analisi_causale': _ultima_analisi,
                            'prompt_contestuale': _prompt_ctx,
                        })
            except Exception as _ra_e:
                log.debug(f"[CI_RA_ERR] {_ra_e}")

            # Aggiorna diagnostica nel heartbeat
            if _ra_iniettate or _ra_bloccate:
                try:
                    _diag = (self.heartbeat_data or {}).get("narratore_diagnostica", {
                        "iniettate_tot": 0, "bloccate_tot": 0,
                        "ultima_iniettata": None, "storia": []
                    })
                    _diag["iniettate_tot"] += len(_ra_iniettate)
                    _ra_bloccate_reali = [b for b in _ra_bloccate if "(già_attiva)" not in b]
                    _diag["bloccate_tot"]  += len(_ra_bloccate_reali)
                    _diag["attive_ci"]      = len(_ra_bloccate) - len(_ra_bloccate_reali)
                    if _ra_iniettate:
                        _diag["ultima_iniettata"] = {
                            "ids": _ra_iniettate,
                            "ts":  datetime.utcnow().strftime("%H:%M:%S")
                        }
                        _storia = _diag.get("storia", [])
                        _storia.append({
                            "ts":       datetime.utcnow().strftime("%H:%M:%S"),
                            "iniettate": _ra_iniettate,
                            "bloccate":  _ra_bloccate,
                        })
                        _diag["storia"] = _storia[-20:]
                    if self.heartbeat_data is not None:
                        self.heartbeat_data["narratore_diagnostica"] = _diag
                except Exception:
                    pass

        except Exception as _ci_e:
            log.debug(f"[CI_TICK_ERR] {_ci_e}")

        # Calcola drift per il downgrade momentum in RANGING
        _drift_for_classify = 0.0
        if len(self.campo._prices_long) >= 100:
            _pl = list(self.campo._prices_long)
            _avg_old = sum(_pl[:50]) / 50
            _avg_new = sum(_pl[-50:]) / 50
            _drift_for_classify = (_avg_new - _avg_old) / _avg_old * 100

        # -- MOTORE 2: Feed SEMPRE — buffer prezzi deve crescere ogni tick --
        _seed_quick = self.seed_scorer.score()
        _seed_val = _seed_quick.get('score', 0.0) if _seed_quick.get('reason') != 'insufficient_data' else 0.0
        self.campo.feed_tick(price, self._last_volume, _seed_val)

        contesto = self.analyzer.analyze(regime=self._regime_current, drift=_drift_for_classify)

        # analyzer ritorna (momentum, volatility, trend) oppure (None, None, None)
        _contesto_ok = contesto[0] is not None
        _mom = contesto[0] if _contesto_ok else "MEDIO"
        _vol = contesto[1] if _contesto_ok else "MEDIA"
        _trd = contesto[2] if _contesto_ok else "SIDEWAYS"

        # ════════════════════════════════════════════════════════════════
        # PATCH 1 TELEMETRIA — distribuzione volatility + oi_stato (passiva)
        # ════════════════════════════════════════════════════════════════
        try:
            if _contesto_ok and _vol in self._tel_vol_distribution:
                self._tel_vol_distribution[_vol] += 1
            _stato_now = getattr(self, '_oi_stato', 'ATTESA')
            if _stato_now in self._tel_oi_stato_distribution:
                self._tel_oi_stato_distribution[_stato_now] += 1
        except Exception:
            pass

        # Oracolo e Veritas girano sempre
        self._oracolo_interno_tick(price, _mom, _vol, _trd)
        self.veritas.aggiorna(price, time.time())
        # Salva Veritas su disco ogni 60 secondi
        if not hasattr(self, '_veritas_last_save'):
            self._veritas_last_save = 0
        if time.time() - self._veritas_last_save >= 60:
            self.veritas.save(DB_PATH)
            self._veritas_last_save = time.time()

        if not _contesto_ok:
            return
        momentum, volatility, trend = contesto
        self._last_trend = trend
        self._last_volatility = volatility
        self._last_momentum = momentum

        # -- MOTORE 2: Shadow trade evaluation (parallelo) -----------------
        if self._shadow:
            self._evaluate_shadow_exit(price, momentum, volatility, trend)
        else:
            # ════════════════════════════════════════════════════════════════
            # 🐺 CANCELLO SALITA (18giu2026, Roberto — IL PRIMO E UNICO CANCELLO)
            # ════════════════════════════════════════════════════════════════
            # PRINCIPIO (Roberto): non guardo il VALORE, guardo la SEQUENZA tick
            # by tick a esposizione zero. Il maschio SALE (fa nuovi massimi, anche
            # con respiri in mezzo: 1.20->1.18->1.25). La femmina/trans COLLASSA
            # (smette di fare nuovi massimi e non torna piu' su). Non serve soglia
            # di valore ne' timer fisso: il TEMPO mostra tutto. Finche' rifa' nuovi
            # massimi = vivo, lo lascio passare all'entry. Se passano troppi tick
            # SENZA un nuovo massimo = ha smesso di salire = collassata = BLOCCO,
            # non chiamo nemmeno _evaluate_shadow_entry (il labirinto non parla).
            #
            # Questo cancello sta PRIMA di tutto (P1, P2, campo, gate, mine): e' il
            # primo a decidere. Nessuno lo puo' bypassare perche' chi non sale non
            # arriva nemmeno alla valutazione.
            #
            # ENV:
            #   CANCELLO_SALITA_OFF (default false = attivo)
            #   CANCELLO_RESPIRO_TICK (default 40) = quanti tick di tolleranza
            #       senza nuovo massimo prima di dichiarare collasso. Generoso:
            #       da' spazio al respiro del maschio. Si abbassa se entrano
            #       ancora femmine, si alza se vengono tagliati maschi che respirano.
            #   CANCELLO_MIN_SALITA_USD (default 0.0 = spento) = se >0, il massimo
            #       osservato deve almeno superare questo valore lordo per entrare
            #       (rete opzionale, di default NON usata: conta solo la salita).
            # try/except totale: in caso di errore, FAIL-OPEN (chiama l'entry come
            # prima) per non bloccare mai il bot per colpa del cancello.
            # ════════════════════════════════════════════════════════════════
            _cancello_passa = True
            try:
                # ════════════════════════════════════════════════════════════
                # FIX 19giu2026 (Roberto): IL RITARDO NON ESISTE PIU'.
                # L'osservazione del movimento NON e' piu' ancorata a
                # _rit_aggancio_ts (i candidati veri lo saltavano -> grasso 0 ->
                # maschi a +315 buttati). Adesso l'osservazione parte dal
                # SEGNALE che gira SEMPRE: quando lo score supera la soglia
                # (_last_score >= _last_soglia, calcolati appena sopra a ogni
                # tick), c'e' un candidato LONG da osservare.
                #
                # REGOLA PURA (Roberto): osservo il movimento tick by tick.
                # - Fa nuovi massimi = SALE costante e continuo = MASCHIO = passa.
                #   (anche piano: non serve una soglia di grasso, basta che salga.)
                # - Smette di fare nuovi massimi e si sgonfia (cola, perde) =
                #   FEMMINA/TRANS = TAGLIO, prima del gate.
                # Nessuna soglia di valore, nessun ritardo, nessun timer fisso.
                # Solo: sale continuo -> passa | si sgonfia -> taglia.
                # ════════════════════════════════════════════════════════════
                _score_now  = getattr(self.campo, "_last_score", 0.0) or 0.0
                _soglia_now = getattr(self.campo, "_last_soglia", 0.0) or 0.0
                _segnale_vivo = (_score_now >= _soglia_now and _soglia_now > 0)

                # ════════════════════════════════════════════════════════════
                # FIX 24giu (Roberto: "in short restiamo FUORI dal trade").
                # Il sistema gira campo._direction a SHORT in TRENDING_BEAR (mercato
                # che scende). Il bot e' LONG-ONLY: in fase SHORT NON deve agganciare.
                # MASCHIO_DIRETTO (bypass) saltava questo controllo e apriva LONG anche
                # in ribasso -> 15 LONG mentre il prezzo colava 59847->59313, tutti loss.
                # ORA: aggancia SOLO se la direzione del mercato e' LONG. In fase
                # ribassista resta FUORI = comportamento corretto LONG-only.
                _dir_mercato = getattr(getattr(self, "campo", None), "_direction", "LONG")
                _long_ok = (_dir_mercato == "LONG") or (os.environ.get("LONG_ONLY_GATE_OFF", "false").lower() == "true")
                if _segnale_vivo and _long_ok:
                    # NASCITA candidato: primo tick in cui il segnale e' vivo
                    # dopo un periodo di silenzio -> fisso il prezzo di nascita
                    # e azzero i contatori di comportamento.
                    if not getattr(self, "_canc_in_osservazione", False):
                        self._canc_in_osservazione = True
                        self._canc_prezzo_prec  = price   # prezzo del tick precedente
                        self._canc_su_consec    = 0       # movimenti su consecutivi
                        self._canc_giu_consec   = 0       # movimenti giu consecutivi
                        self._canc_maschio_ok   = False   # ha gia' confermato maschio?
                        # FIX 24giu (Roberto): MASCHIO_DIRETTO si tiene picco e crollo
                        # PROPRI, dal prezzo di nascita, INDIPENDENTI da _rit_picco_pre
                        # che il filtro vecchio dentro _oracolo_interno_tick AZZERA a
                        # None (-> maschi scartati col grasso). Calcolo qui, sul prezzo,
                        # senza dipendere da valori che un altro cervello sporca.
                        self._canc_nascita_prezzo = price  # prezzo di nascita proprio
                        self._canc_nascita_ts     = time.time()  # quando nasce l'osservazione (per regola 12s)
                        self._canc_picco_proprio  = 0.0    # max grasso $ visto (proprio)
                        self._canc_crollo_proprio = 0.0    # min $ sotto nascita (proprio mae)

                    # ════════════════════════════════════════════════════════════
                    # 🐺 REGOLA ROBERTO (19giu2026) — SOLO IL COMPORTAMENTO PRIMA
                    #    DEL GATE DECIDE. NON i numeri (seme/score sono trappole:
                    #    i trans hanno numeri da maschio ma si comportano da
                    #    femmina -> li tagliamo come le femmine).
                    #
                    # Osservo il movimento tick by tick dalla nascita:
                    # - MASCHIO: sale piano, N movimenti consecutivi in SU (fa
                    #   prezzi piu' alti di fila) = ho capito che e' un maschio che
                    #   sale e LASCIA GRASSO -> ENTRA SUBITO, in quel punto, per
                    #   prendere la corsa che resta (entrare tardi = grasso gia'
                    #   andato: se nasce a 10 e va a 14, il +4 e' mio solo se entro
                    #   presto; se aspetto e entro a 13 prendo solo +1).
                    # - FEMMINA/TRANS: N movimenti consecutivi di STORNO (scende
                    #   di fila) = si sta svuotando -> TAGLIO SUBITO, chiuso.
                    #
                    # ASIMMETRIA: il maschio appena conferma la salita ENTRA (non
                    # aspetto oltre, ogni tick e' grasso perso). La femmina appena
                    # conferma lo storno e' TAGLIATA. Default: nessuno passa finche'
                    # non ha confermato salita da maschio.
                    # ENV: CANCELLO_MOSSE (default 3) = movimenti consecutivi per
                    #   confermare la tendenza. 3 = tendenza vera (2 puo' essere
                    #   rumore). Abbassa a 2 per piu' reattivita'.
                    # ════════════════════════════════════════════════════════════
                    if os.environ.get("CANCELLO_SALITA_OFF", "false").lower() != "true":
                        _mosse_n = int(float(os.environ.get("CANCELLO_MOSSE", "3")))
                        _prec    = getattr(self, "_canc_prezzo_prec", price)

                        if price > _prec:
                            # movimento in SU: conto, azzero i giu
                            self._canc_su_consec  = getattr(self, "_canc_su_consec", 0) + 1
                            self._canc_giu_consec = 0
                        elif price < _prec:
                            # movimento in GIU (storno): conto, azzero i su
                            self._canc_giu_consec = getattr(self, "_canc_giu_consec", 0) + 1
                            self._canc_su_consec  = 0
                        # price == _prec: tick piatto, non muove i contatori
                        self._canc_prezzo_prec = price

                        # FIX 24giu: aggiorno picco/crollo PROPRI (grasso $ dal prezzo
                        # di nascita), indipendenti dal filtro vecchio. Questi sono i
                        # valori che le firme leggeranno (non _rit_picco_pre sporcato).
                        _cn_nascita = getattr(self, "_canc_nascita_prezzo", None)
                        if _cn_nascita:
                            _cn_exp = float(os.environ.get("EXPOSURE", "5000"))
                            _cn_grasso = (price - _cn_nascita) * (_cn_exp / _cn_nascita)
                            if _cn_grasso > getattr(self, "_canc_picco_proprio", 0.0):
                                self._canc_picco_proprio = _cn_grasso
                            if _cn_grasso < getattr(self, "_canc_crollo_proprio", 0.0):
                                self._canc_crollo_proprio = _cn_grasso
                            # CONTATORE SALITA DRITTA (firma del maschio, provata su stress test):
                            # tick consecutivi in cui il grasso SALE. Il maschio sale dritto
                            # (contatore alto); il trans sale a strappo e ricade (si azzera).
                            _cn_grasso_prec = getattr(self, "_canc_grasso_prec", None)
                            if _cn_grasso_prec is not None and _cn_grasso > _cn_grasso_prec:
                                self._canc_tick_salita = getattr(self, "_canc_tick_salita", 0) + 1
                            else:
                                self._canc_tick_salita = 0
                            self._canc_grasso_prec = _cn_grasso
                            self._canc_grasso_ora = _cn_grasso

                        # MASCHIO confermato: N movimenti su consecutivi -> entra
                        # FIX 23giu (Roberto): NON basta il conteggio dei tick su.
                        # Una femmina in RANGE_DEAD fa 2 micro-tick su per rumore
                        # (peak 0, sign_flips alti) e bucava il cancello -> entrava
                        # nel trade -> -5. Ora serve ANCHE grasso REALE in mano:
                        # 2 mosse su E grasso_ora >= CANCELLO_GRASSO_MIN. La femmina
                        # che rimbalza a zero grasso NON entra piu'.
                        _md_aggancio = getattr(self, "_rit_prezzo_aggancio", None)
                        if _md_aggancio:
                            _md_exp = float(os.environ.get("EXPOSURE", "5000"))
                            _md_grasso = (price - _md_aggancio) * (_md_exp / _md_aggancio)
                        else:
                            _md_grasso = 0.0
                        _md_grasso_min = float(os.environ.get("CANCELLO_GRASSO_MIN", "1.5"))
                        # FIX 23giu pomeriggio (Roberto: "abbiamo sbloccato i maschi
                        # stamattina, attenzione"): il vincolo grasso QUI strozzava
                        # i maschi in RANGE_DEAD (2 mosse su ma grasso istantaneo
                        # <1.5 al tick -> non entravano, 0 trade per 50min). Le
                        # femmine ora restano fuori grazie a P1_OFF+SCORE_ENTRY_OFF
                        # (le loro porte), NON serve il grasso qui. Default: entra
                        # su 2 mosse su (come stamattina che funzionava). Il vincolo
                        # grasso si riattiva con MD_GRASSO_GATE=true se serve.
                        _md_gate = os.environ.get("MD_GRASSO_GATE", "false").lower() == "true"
                        # ════════════════════════════════════════════════════════
                        # FIX 23giu NOTTE (Roberto, ERRORE TROVATO): MASCHIO_DIRETTO
                        # entrava su 2 mosse su SENZA guardare il MAE. Ma il MAE E'
                        # LA FIRMA: maschio mae basso (sale dritto), TRANS mae alto
                        # (balla). Il trans ballerino faceva comunque 2 mosse su ed
                        # ENTRAVA -> poi -2/-3. ORA: entra solo se NON ha ballato,
                        # cioe' se durante l'osservazione non e' sceso sotto -MAE.
                        # _rit_crollo_min = minimo $ toccato in osservazione (il mae).
                        # ENV MD_MAE_MAX (default 1.0 = soglia certificata sui 2000
                        # trade: mae<=1.1 maschio 85%). 0 = controllo spento.
                        # ════════════════════════════════════════════════════════
                        _md_mae_max = float(os.environ.get("MD_MAE_MAX", "0.5"))
                        # FIX 24giu: leggo il crollo PROPRIO (non _rit_crollo_min che
                        # il filtro vecchio azzera). _canc_crollo_proprio = min $ sotto
                        # nascita, calcolato da MASCHIO_DIRETTO stesso, mai sporcato.
                        _md_crollo = getattr(self, "_canc_crollo_proprio", 0.0)
                        # mae ok se: controllo spento, o il crollo e' sopra -soglia
                        # (non sceso troppo = non ha ballato = maschio)
                        _md_mae_ok = (_md_mae_max <= 0) or (_md_crollo >= -_md_mae_max)
                        # ════════════════════════════════════════════════════════
                        # FEMMINA FUORI (24giu, Roberto: "la femmina e' la piu' chiara,
                        # non sale. Se passa e' perche' il flusso lo consente").
                        # La femmina ha picco ~0 (non sale mai). Entra solo chi ha
                        # fatto un PICCO osservazione >= MD_PICCO_MIN. La femmina,
                        # che non sale, resta fuori dal flusso. Default 2.0 (sui dati:
                        # femmine picco<1.0 = 0 win su 1180; maschi/trans 2.5-3+).
                        # ENV MD_PICCO_MIN. 0 = controllo spento.
                        # ════════════════════════════════════════════════════════
                        # ════════════════════════════════════════════════════════
                        # CAMBIO STRATEGIA 24giu (Roberto, ANAMNESI trade 10:24):
                        # ERRORE: entravo quando picco_proprio >= 3 = IN CIMA. Il
                        # candidato saliva 0->3.2 in osservazione, io aspettavo il 3
                        # ed entravo a 3.2 -> da li' storna -> -1.23 (preso lo
                        # SVUOTAMENTO, persa tutta la salita).
                        # CORREZIONE: il maschio si CONFERMA dal salire DRITTO
                        # (crollo basso/0), che si vede SUBITO, non dall'aver fatto 3.
                        # Entro appena confermato dritto, a grasso BASSO (MD_ENTRY_MIN,
                        # default 1.0 = sopra zero per escludere femmina piatta), e
                        # CAVALCO la salita verso 3+. Il picco 3 e' cio' che il maschio
                        # FARA' dopo che sono dentro, NON il biglietto d'ingresso.
                        # ════════════════════════════════════════════════════════
                        # ════════════════════════════════════════════════════════
                        # ⭐ FILTRO PROVATO SUI DATI (24giu, simula2 su 388 trade reali)
                        # WIN 95 (+190), LOSS 293 (-826), totale ENTRA-TUTTO = -636.
                        # Filtro MFE>=0 (il candidato e' andato in positivo almeno una
                        # volta durante l'osservazione): entra 140, prende TUTTI E 95 i
                        # win (win_persi=0), totale +98. Delta +734$.
                        # La FEMMINA non va MAI in positivo (MFE~0) -> scartata.
                        # SMENTITI e RIMOSSI: i 12s (_md_eta_ok/_md_tiene_ok tagliavano
                        # i maschi lenti, win_persi 33-83) e il gate mosse_su.
                        # Vedi VERITA_BLINDATA_OVERTOP.md. NON reintrodurre i 12s.
                        # ════════════════════════════════════════════════════════
                        _md_mfe_min = float(os.environ.get("MD_MFE_MIN", "2.0"))
                        _md_picco = getattr(self, "_canc_picco_proprio", 0.0)
                        # FILTRO PROVATO SUI DATI REALI (415 trade, 27giu): il picco
                        # d'osservazione (MFE) >= soglia. Con MFE>=2: 50 trade entrano,
                        # 41 win/+157, 9 loss/-17 = 82% WR, +140 totale.
                        # SMENTITI dai dati e RIMOSSI: MAE stretto, "salita dritta".
                        # ─────────────────────────────────────────────────────────
                        # REGOLA ROBERTO 27giu (CONFERMA POST-PICCO): non basta toccare
                        # la soglia. Dopo il picco, osservo ANCORA a esposizione zero:
                        # entro SOLO se il grasso ATTUALE si conferma (non e' crollato
                        # dal picco). Maschio tiene/sale -> entra. Trans crolla dal picco
                        # -> NON entra (visto a costo zero, prima del trade).
                        # Il trade 928 (TRANS picco 3.01, -3.03) entrava a soglia secca:
                        # con la conferma sarebbe stato scartato (era gia' crollato).
                        _md_nascita = getattr(self, "_canc_nascita_prezzo", None)
                        if _md_nascita:
                            _md_grasso_ora = (price - _md_nascita) * (5000.0 / _md_nascita)
                        else:
                            _md_grasso_ora = _md_picco
                        _md_conferma = float(os.environ.get("MD_CONFERMA", "0.7"))
                        # ha raggiunto la soglia di picco?
                        _md_picco_ok = (_md_picco >= _md_mfe_min)
                        # si conferma ORA? (il grasso attuale non e' crollato dal picco)
                        _md_tiene = (_md_grasso_ora >= _md_picco * _md_conferma)
                        _md_mfe_ok = _md_picco_ok and _md_tiene
                        _md_ok = _md_mfe_ok
                        if _md_picco_ok and not _md_tiene:
                            self._log_m2("📉", f"TRANS: picco {_md_picco:.2f}$ ma ORA {_md_grasso_ora:.2f}$ (crollato sotto {_md_picco*_md_conferma:.2f}) = NON conferma, NON entra")
                        elif not _md_picco_ok:
                            self._log_m2("🚺", f"NON entra: MFE {_md_picco:.2f}$ < {_md_mfe_min:.1f} (non ha fatto grasso)")
                        # ════════════════════════════════════════════════════════
                        # PORTA UNICA D'INGRESSO (24giu sera, riscrittura Roberto:
                        # "una porta sola, niente merda stratificata"). UNA lettura
                        # del picco/crollo PROPRI del candidato ATTUALE, UN filtro
                        # (MFE>=soglia E MAE basso), UNA apertura. Niente flag che
                        # sopravvive al tick (era la fessura: P3 e P4 leggevano il
                        # picco in 2 momenti diversi -> femmina 0.664 sfuggita).
                        # ════════════════════════════════════════════════════════
                        if _md_ok:
                            self._canc_maschio_ok = True
                            _MAX_PH = 5
                            _gia_aperto = (len(getattr(self, "_phantoms_open", []) or []) >= _MAX_PH)
                            _dir_ok = (self.campo._direction == "LONG") or (os.environ.get("LONG_ONLY_GATE_OFF", "false").lower() == "true")
                            if (not _gia_aperto) and _dir_ok and (not getattr(self, "_maschio_diretto_in_corso", False)):
                                try:
                                    self._maschio_diretto_in_corso = True
                                    _seed_q = self.seed_scorer.score()
                                    _seed_v = _seed_q.get('score', 0.0) if _seed_q.get('reason') != 'insufficient_data' else 0.0
                                    _fp_wr = self.oracolo.get_wr(momentum, volatility, trend, self.campo._direction)
                                    self._log_m2("🐺", f"MASCHIO CONFERMATO: picco {_md_picco:.2f}$, tiene a {_md_grasso_ora:.2f}$ = ENTRA (confermato dopo il picco)")
                                    # FIX bug picco_oss azzerato (NODO blindato): catturo il
                                    # picco VERO d'ingresso ORA, prima che una nuova osservazione
                                    # resetti _canc_picco_proprio a 0. Cosi la lista/dashboard
                                    # mostra il picco reale e l'etichetta sesso e' corretta.
                                    self._trade_picco_ingresso = float(_md_picco)
                                    self._trade_crollo_ingresso = float(_md_crollo)
                                    # MISURATORE LATENZA (leggero, solo log): ms tra aggancio e ingresso.
                                    # Il numero che dice se il bot entra tardi (grasso gia' evaporato).
                                    try:
                                        _ag_ts_lat = getattr(self, "_rit_aggancio_ts", None)
                                        if _ag_ts_lat:
                                            _lat_ms = (time.time() - _ag_ts_lat) * 1000.0
                                            self._log_m2("⏱️", f"LATENZA aggancio->ingresso: {_lat_ms:.0f}ms (picco oss {_md_picco:.2f}$)")
                                    except Exception:
                                        pass
                                    self._open_shadow_position(price, 99.0, 0.0, _seed_v, 0.3,
                                                               momentum, volatility, trend,
                                                               "MASCHIO_DIRETTO", _fp_wr)
                                except Exception as _e_md:
                                    log.error(f"[MASCHIO_DIRETTO_ERR] {_e_md}")
                                finally:
                                    self._maschio_diretto_in_corso = False
                            elif not _dir_ok:
                                self._log_m2("🛡️", "FUORI: mercato SHORT, porta unica chiusa (LONG-only)")
                            self._canc_maschio_ok = False
                            _cancello_passa = False  # gia' deciso qui, non ripassare

                        # FEMMINA/TRANS: N storni consecutivi -> taglio subito
                        # FIX 24giu (Roberto: "fallo correre, dargli tempo, non
                        # ucciderlo come femmina"). Il maschio vero PARTE ROSSO e
                        # risale TARDI (dati: t_peak WIN ~33s): mentre matura la carne
                        # fa ritracciamenti. Se taglio a 3 storni come le mosse, lo
                        # ammazzo al primo respiro. Lo storno ha soglia PROPRIA piu'
                        # larga (CANCELLO_STORNI, default 5): do tempo al maschio lento
                        # di ritracciare e ripartire, senza ucciderlo come femmina.
                        _storni_n = int(float(os.environ.get("CANCELLO_STORNI", "5")))
                        if self._canc_giu_consec >= _storni_n:
                            _cancello_passa = False
                            self._canc_maschio_ok = False
                            self._log_m2("🐺",
                                f"CANCELLO: STORNA — {self._canc_giu_consec} movimenti "
                                f"giu di fila (>={_storni_n}) = si svuota davvero, TAGLIO")

                        # DEFAULT NEGATO: passa SOLO chi ha confermato maschio.
                        # Chi non ha ancora N mosse su (sta nascendo, o e' piatto)
                        # NON entra ancora -> aspetta. Non e' un taglio: e' attesa.
                        if not getattr(self, "_canc_maschio_ok", False):
                            _cancello_passa = False
                else:
                    # segnale sparito: il candidato e' finito, chiudo l'osservazione
                    self._canc_in_osservazione = False
            except Exception as _e_canc:
                log.debug(f"[CANCELLO_SALITA_ERR] {_e_canc}")
                _cancello_passa = True   # FAIL-OPEN: mai bloccare per errore del cancello

            if _cancello_passa:
                # FIX 23giu sera (Roberto: "tutta roba che non serve piu'") — BYPASS VERO.
                # PROBLEMA dai log: MASCHIO_DIRETTO confermava ("ENTRA bypass veti") ma
                # poi chiamava _evaluate_shadow_entry, che e' PIENA dei veti del vecchio
                # mondo (VERITAS, CAPSULE block_score, SCORE_SOTTO score>=34, constitutional
                # PRE_SC_VETO). In RANGE_DEAD lo score e' 18-29 < 34 -> bloccato SEMPRE ->
                # 0 trade. Il "bypass veti" era finto. ORA: se il maschio e' confermato
                # (_canc_maschio_ok), apre DIRETTO con _open_shadow_position, saltando
                # del tutto _evaluate_shadow_entry e i suoi veti. Bypass VERO.
                # FIX 24giu (Roberto): la Porta B NON si fida piu' del solo flag.
                # Ricontrolla le firme PROPRIE del candidato ATTUALE (picco>=soglia,
                # crollo>=-soglia). Cosi anche se il flag restasse True da un maschio
                # precedente, una femmina/trans magro al tick dopo NON passa: le sue
                # firme proprie la fermano. Metal detector nel muro, non un flag.
                _pb_picco = getattr(self, "_canc_picco_proprio", 0.0)
                _pb_crollo = getattr(self, "_canc_crollo_proprio", 0.0)
                _pb_picco_min = float(os.environ.get("MD_MFE_MIN", "0.0"))
                _pb_mae_max = float(os.environ.get("MD_MAE_MAX", "1.0"))
                _pb_firme_ok = (_pb_picco >= _pb_picco_min) and (_pb_crollo >= -_pb_mae_max)
                # PORTA B DISATTIVATA (24giu sera): era il doppione di P3 che leggeva
                # il picco in un momento diverso -> fessura da cui sfuggiva la femmina
                # 0.664. Ora apre SOLO la porta unica sopra. CHIUSA NEL CODICE
                # (and False) il 26giu: era la 2a porta senza cattura picco ->
                # trade con etichetta FEMMINA falsa (picco azzerato). UNA PORTA SOLA.
                if False and getattr(self, "_canc_maschio_ok", False) and _pb_firme_ok and os.environ.get("MASCHIO_BYPASS_VERO", "false").lower() == "true":
                    try:
                        _seed_v = getattr(self, "_last_seed", 0.5)
                        self._log_m2("🐺", "MASCHIO BYPASS VERO: apro diretto, salto VERITAS/CAPSULE/SCORE/CONST")
                        self._open_shadow_position(price, 99.0, 0.0, _seed_v, 0.3,
                                                    momentum, volatility, trend,
                                                    "MASCHIO_DIRETTO", 0.5)
                    except Exception as _e_mbv:
                        log.error(f"[MASCHIO_BYPASS_ERR] {_e_mbv}")
                # ════════════════════════════════════════════════════════════
                # 24giu (Roberto): CERVELLO VECCHIO STACCATO. Prima qui c'era
                # 'else: self._evaluate_shadow_entry(...)' che faceva cadere ogni
                # candidato non-maschio nel vecchio mondo (score/veti/matrigne/
                # capsule). Era la fessura da cui rispuntavano i fantasmi all'
                # infinito. RIMOSSO. Se MASCHIO_DIRETTO non apre, il candidato
                # MUORE qui. Una sola via: o e' un alfa (MASCHIO_DIRETTO) o niente.
                # ════════════════════════════════════════════════════════════

        # -- PHANTOM TRACKER: aggiorna trade fantasma ogni tick ------------
        if self._phantoms_open:
            self._update_phantoms(price, momentum)

        # -- POST-TRADE TRACKER: monitora cosa succede dopo exit ----------
        if self.oracolo._post_trade_queue:
            self.oracolo.update_post_trade(price)

        # -- PRE-TRADE SIGNAL TRACKER: osservazione continua ogni tick ------
        # score_now() calcola senza decidere — pura mappa del segnale nel tempo.
        # Registra tutto ciò che supera 25, prima di qualsiasi filtro.
        if self.campo._tick_count > self.campo.WARMUP_TICKS and momentum:
            _seed_q = self.seed_scorer.score()
            _seed_v = _seed_q.get('score', 0.0) if _seed_q.get('reason') != 'insufficient_data' else 0.0
            _fp_wr  = self.oracolo.get_wr(momentum, volatility, trend, self.campo._direction)
            _sn     = self.campo.score_now(_seed_v, _fp_wr, momentum, volatility,
                                            trend, self._regime_current, self.campo._direction)
            if _sn['valid']:
                # Salva sempre l'ultimo score per il grafico
                self.campo._last_score  = _sn['score']
                self.campo._last_soglia = _sn['soglia']
                # Registra nel tracker se score >= 25
                # Calcola drift reale — _last_drift non esiste su campo
                _st_drift = 0.0
                if len(self.campo._prices_long) >= 100:
                    _st_p = list(self.campo._prices_long)
                    _st_old = sum(_st_p[:50]) / 50
                    _st_new = sum(_st_p[-50:]) / 50
                    if _st_old > 0:
                        _st_drift = (_st_new - _st_old) / _st_old * 100
                self.signal_tracker.record_signal(
                    price=price,
                    direction=self.campo._direction,
                    score=_sn['score'],
                    soglia=_sn['soglia'],
                    regime=self._regime_current,
                    momentum=momentum,
                    volatility=volatility,
                    trend=trend,
                    rsi=self.campo._last_rsi,
                    macd_hist=self.campo._last_macd_hist,
                    drift=round(_st_drift, 5),
                )
        # Aggiorna segnali aperti
        if self.signal_tracker.get_open_count() > 0:
            self.signal_tracker.update(price)

        # -- BRIDGE EVENTS: rate-limited — max 1 per tipo ogni 10s --------
        _now_ev = time.time()
        if len(self.campo._prices_short) >= 30:
            _pb_f, _pb_d, _pb_sigs = self.campo._pre_breakout_factor()
            if _pb_sigs >= 1:  # abbassato da 2 a 1 — più sensibile
                _last_pb = getattr(self, '_last_pb_event_ts', 0)
                if _now_ev - _last_pb >= 10:
                    _pb_payload = {'signals': _pb_sigs, 'factor': round(_pb_f, 3), 'regime': self._regime_current}
                    self._emit_bridge_event("EVENT_PREBREAKOUT", _pb_payload)
                    self.telemetry.log_event_signal("PREBREAKOUT", _pb_payload)
                    self._last_pb_event_ts = _now_ev
                    self._last_pb_factor   = _pb_f
        if self._oi_stato == "FUOCO" and self._oi_carica >= 0.80:
            _last_fuoco = getattr(self, '_last_fuoco_event_ts', 0)
            if _now_ev - _last_fuoco >= 10:
                _fuoco_payload = {'carica': round(self._oi_carica, 3), 'regime': self._regime_current}
                self._emit_bridge_event("EVENT_FUOCO", _fuoco_payload)
                self.telemetry.log_event_signal("FUOCO", _fuoco_payload)
                self._last_fuoco_event_ts = _now_ev

        # -- HEARTBEAT M2 - ogni 60s conferma che M2 è vivo ---------------
        if now - self._last_m2_heartbeat > 60:
            self._log_m2("💓", f"M2 vivo | shadow={'aperto' if self._shadow else 'chiuso'} "
                              f"| {self._m2_trades}t W={self._m2_wins} L={self._m2_losses}")
            self._last_m2_heartbeat = now

    # ========================================================================
    # ENTRY - catena decisionale completa
    # ========================================================================

    def _log(self, emoji: str, msg: str):
        """Aggiunge una riga al log live e la spinge subito a heartbeat_data."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} {msg}"
        self._live_log.append(entry)
        log.info(entry)
        # Push immediato alla dashboard - non aspetta il ciclo heartbeat da 30s
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

        # -- 1. SEED SCORER ------------------------------------------------
        seed = self.seed_scorer.score()
        dynamic_seed_thresh = self.calibratore.get_params()['seed_threshold']
        if not seed['pass'] or seed['score'] < dynamic_seed_thresh:
            self._log("⚡", f"SEED FAIL score={seed['score']:.3f} | {momentum}/{volatility}/{trend} @ ${price:.1f}")
            return

        # -- 2. ORACOLO DINAMICO -------------------------------------------
        is_fantasma, fantasma_reason = self.oracolo.is_fantasma(momentum, volatility, trend)
        if is_fantasma:
            self._log("👻", f"FANTASMA bloccato: {fantasma_reason}")
            return
        fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend)

        # -- 3. MATRIMONIO -------------------------------------------------
        matrimonio      = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = matrimonio["name"]
        confidence      = matrimonio["confidence"]

        # -- 4. MEMORIA MATRIMONI ------------------------------------------
        can_enter, mem_status = self.memoria.get_status(matrimonio_name)
        if not can_enter:
            self._log("🚫", f"MEMORIA blocca {matrimonio_name}: {mem_status}")
            return

        # -- 5. CATENA 5 CAPSULE - soglie dinamiche dal calibratore ----------
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

        # -- 6. CAPSULE RUNTIME (JSON dinamico) ---------------------------
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
            'regime':           self._regime_current,  # FIX: regime reale per match capsule SC_
            'oi_stato':         self._oi_stato,         # FIX: aggiunto per trigger Oracle
            'oi_carica':        self._oi_carica,        # FIX: aggiunto per trigger Oracle
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
            self._log("💊", f"[DECISION_CHANGED_BY_CAPSULE] capsule_id={caps_check.get('reason','?')} action=blocca_entry matrimonio={matrimonio_name}")
            self.telemetry.log_capsule_load([caps_check.get('reason','?')])
            return

        # -- ENTRY CONFERMATA ----------------------------------------------
        # Position sizing continuo - funzione dell'impulso × regime
        regime_mults = self.regime_detector.get_multipliers()
        sizing = self.position_sizer.calculate(
            seed_score=seed['score'],
            fingerprint_wr=fingerprint_wr,
            confidence=confidence,
            regime_mult=regime_mults['size_mult']
        )
        # Le capsule JSON possono ancora modificare ulteriormente
        caps_size_mult = caps_check.get('size_mult', 1.0)
        if caps_size_mult != 1.0:
            self._log("💊", f"[CAPSULE_APPLY] size_mult={caps_size_mult:.2f} applicato")
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

        _size_usdt_entry = self.TRADE_SIZE_USD  # margine fisso $1000
        self.trade_open = {
            "price_entry":    price,
            "matrimonio":     matrimonio_name,
            "duration_avg":   matrimonio["duration_avg"],
            "size_mult":      size_factor,
            "size_usdt":      _size_usdt_entry,
            "direction":      self.campo._direction,
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

    # ========================================================================
    # EXIT - 4 DIVORCE TRIGGERS + SMORZ + TIMEOUT
    # ========================================================================

    def _evaluate_exit(self, price, momentum, volatility, trend):
        if price > self.max_price:
            self.max_price = price

        # -- HARD STOP LOSS SUL PNL REALE - PRIORITÀ ASSOLUTA -------------
        # Stop sul PnL della posizione, NON sul prezzo BTC.
        # $1000 margine × 5x leva = $5000 esposti.
        # 1% del margine = $10 max loss per trade.
        # Il T3 drawdown 3% sul prezzo BTC è inutile: 3% BTC = ~$2100 movimento.
        # Questo stop ferma il danno PRIMA che arrivi al T3.
        # HARD STOP: PnL LORDO (fee esclusa — il trade respira)
        # Fee pagata solo al close, non monitored live
        _hs_entry = self.trade_open["price_entry"]
        _hs_dir   = self.trade_open.get("direction", "LONG")
        _hs_exp   = self.TRADE_SIZE_USD * self.LEVERAGE     # $5000
        _hs_btc   = _hs_exp / _hs_entry
        _hs_delta = price - _hs_entry if _hs_dir == "LONG" else _hs_entry - price
        _hs_pnl   = _hs_delta * _hs_btc                    # lordo, senza fee
        HARD_STOP_M1 = self.STOP_LIVE  # lordo live ($7 = netto ~$5) $2
        if _hs_pnl < -HARD_STOP_M1:
            self._log("🛑", f"HARD_STOP lordo=${_hs_pnl:.2f} → netto≈${_hs_pnl-2:.2f}")
            self._close_trade(price, momentum, volatility, trend,
                              reason=f"HARD_STOP_${abs(_hs_pnl):.1f}")
            return

        # -- MOMENTUM DECELEROMETER - exit anticipata ----------------------
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

        # -- 4 DIVORCE TRIGGERS - monitorati ogni tick ---------------------
        triggers_attivi = []

        # Trigger 1: volatilita esplode (entry BASSA → ora ALTA) — solo se in perdita
        # Se siamo in profitto, la volatilità alta è movimento a nostro favore
        if self.entry_volatility == "BASSA" and volatility == "ALTA" and _hs_pnl < 0:
            triggers_attivi.append("T1_VOLATILITÀ_ESPLOSA")

        # Trigger 2: trend si inverte (entry UP → ora DOWN)
        if self.entry_trend == "UP" and trend == "DOWN":
            triggers_attivi.append("T2_TREND_INVERTITO")

        # Trigger 3: stop loss 2% sul PnL della posizione
        # Size tipica $500 × 2% = -$10 max per trade
        # NON sul prezzo BTC (3% di BTC = $2050 di movimento — inutile)
        _entry_price  = self.trade_open["price_entry"]
        _size_usdt    = self.trade_open.get("size_usdt", 500.0)
        _direction    = self.trade_open.get("direction", "LONG")
        if _direction == "LONG":
            _pnl_posizione = (price - _entry_price) * (5000.0 / _entry_price)  # lordo
        else:
            _pnl_posizione = (_entry_price - price) * (5000.0 / _entry_price)  # lordo
        _stop_loss_usdt = self.STOP_LIVE
        if _pnl_posizione < -_stop_loss_usdt:
            triggers_attivi.append(f"T3_STOPLOSS_PNL_{_pnl_posizione:.2f}$")
        # Mantieni anche il drawdown % come riferimento (più largo)
        drawdown_pct = ((self.max_price - price) / _entry_price) * 100

        # Trigger 4: fingerprint diverge > 50% dal valore di entry — solo se in perdita
        current_fp = self.oracolo.get_wr(momentum, volatility, trend)
        fp_diverge = abs(current_fp - self.entry_fingerprint) / max(self.entry_fingerprint, 0.001)
        if fp_diverge > DIVORCE_FP_DIVERGE_PCT and _hs_pnl < 0:
            triggers_attivi.append(f"T4_FP_DIVERGE_{fp_diverge:.0%}")

        if len(triggers_attivi) >= DIVORCE_MIN_TRIGGERS:
            self._log("💔", f"DIVORZIO IMMEDIATO {self.current_matrimonio} | {' + '.join(triggers_attivi)}")
            self._close_trade(price, momentum, volatility, trend, reason="DIVORZIO_IMMEDIATO")
            return

        # -- SMORZ - impulso finito ----------------------------------------
        duration     = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]
        # Non uscire per SMORZ se l'Oracolo vede ancora energia
        # L'Oracolo ha dimostrato di avere ragione — rispettalo fino alla fine
        _oracolo_vivo = self._oi_carica >= 0.55 or self._oi_stato in ("FUOCO", "CARICA")
        if duration > duration_avg * 0.5 and momentum == "DEBOLE" and not _oracolo_vivo:
            self._log("🌙", f"SMORZ impulso finito - {self.current_matrimonio} dopo {duration:.0f}s")
            self._close_trade(price, momentum, volatility, trend, reason="SMORZ")
            return

        # -- TIMEOUT adattivo ----------------------------------------------
        if duration > duration_avg * 3:
            self._close_trade(price, momentum, volatility, trend, reason="TIMEOUT_3X")
            return
        if duration > duration_avg and drawdown_pct > 1.0:
            self._close_trade(price, momentum, volatility, trend, reason="TIMEOUT_DD_1%")
            return

    # ========================================================================
    # CLOSE TRADE - registra, impara, aggiorna
    # ========================================================================

    def _close_trade(self, price, momentum, volatility, trend, reason: str):
        # PnL REALE USDC FUTURES
        # Esposizione = $1000 margine × 5 leva = $5000
        # BTC qty     = $5000 / entry_price
        # PnL lordo   = delta × btc_qty
        # Fee         = $5000 × 0.02% × 2 = $2.00 fissi
        _entry   = self.trade_open["price_entry"]
        _dir     = self.trade_open.get("direction", "LONG")
        _exp     = self.TRADE_SIZE_USD * self.LEVERAGE          # $5000
        _btc_qty = _exp / _entry
        _delta   = price - _entry if _dir == "LONG" else _entry - price
        _fee     = _exp * self.FEE_PCT * 2                      # $2.00
        pnl      = round(_delta * _btc_qty - _fee, 4)
        is_win   = pnl > 0
        matrimonio_name = self.current_matrimonio
        matrimonio      = MatrimonioIntelligente.get_by_name(matrimonio_name)
        wr_expected     = matrimonio.get("wr", 0.50)

        # -- Calcola drawdown reale (per AutoCalibratore) ------------------
        if self.max_price and self.trade_open:
            drawdown_pct = ((self.max_price - price) / self.trade_open["price_entry"]) * 100
        else:
            drawdown_pct = 0.0

        # -- Aggiorna tutti i sistemi di apprendimento ---------------------
        self.oracolo.record(self.entry_momentum, self.entry_volatility, self.entry_trend, is_win)
        self.memoria.record_trade(matrimonio_name, is_win, wr_expected)
        # Tracking predizione → trade
        # Se al momento dell'entry l'Oracolo era in FUOCO/CARICA con carica > 0.5
        # il trade era guidato dalla predizione
        if self._oi_carica >= 0.5 or self._oi_stato in ("FUOCO", "CARICA"):
            self._pred_trade_n   += 1
            self._pred_trade_pnl += pnl
        # SuperCervello impara dall'esito — pesi si adattano
        if hasattr(self, '_last_sc_dec') and self._last_sc_dec:
            self.supercervello.registra_esito(self._last_sc_dec, is_win)
            self._last_sc_dec = None
        # ════════════════════════════════════════════════════════════════
        # PATCH 8 BUG 15 — Single registra_trade Append
        # ════════════════════════════════════════════════════════════════
        # Background: prima di PATCH 8, ogni trade chiuso veniva passato
        # DUE VOLTE a self.realtime_engine.registra_trade():
        #   1. qui sotto con dict povero {matrimonio, pnl, is_win}
        #   2. più sotto in _close_shadow_trade (riga ~11524) con dict
        #      ricco di 10 campi (regime, volatility, trend, drift, score, …)
        #
        # Effetto: _trade_buffer (deque maxlen=200) conteneva DUE entries
        # per ogni trade chiuso, falsando _analisi_l3_loss_streak:
        #   - 1 LOSS reale appare come 2 LOSS consecutive → genera capsula
        #     L3_STREAK_2 con boost_soglia +5
        #   - 2 LOSS reali appaiono come 4 → boost più aggressivo
        #
        # Diagnosi di Roberto (17 mag 2026 sera): "esiste il killer che la 5
        # riporta dentro, forse anche la 6 e tutta la filiera". PATCH 5 aveva
        # fixato il doppio append su _m2_recent_trades; lo stesso pattern
        # esisteva su _trade_buffer ed era mai stato visto prima.
        #
        # Fix PATCH 8: rimuovo la chiamata #1 (dict povero). Tengo la #2
        # (dict ricco, 10 campi) che alimenta meglio l'analisi L2/L3.
        # Rimuovo anche analizza_e_genera() qui sotto: registra_trade lo
        # chiama già internamente quando _trade_count % _analisi_interval == 0.
        # ════════════════════════════════════════════════════════════════
        # (PATCH 8: chiamata povera rimossa. La chiamata ricca in
        #  _close_shadow_trade resta come unico append a _trade_buffer.)
        self.log_analyzer.registra({'matrimonio': matrimonio_name, 'pnl': pnl, 'is_win': is_win})

        # -- AutoCalibratore: registra osservazione ------------------------
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

    # ========================================================================
    # STATE ENGINE - TEMPISMO
    # Non solo COSA fare, ma QUANDO NON FARLO.
    # AGGRESSIVO: soglie normali, entra liberamente
    # NEUTRO: soglie normali, entra con cautela
    # DIFENSIVO: cooldown attivo, non entra finché non si calma
    # ========================================================================

    def _state_engine_update(self, pnl, is_win, duration):
        """Chiamato DOPO ogni trade chiuso. Aggiorna lo stato."""
        now = time.time()

        # Registra trade recente
        self._m2_recent_trades.append({
            'ts': now, 'pnl': pnl, 'is_win': is_win, 'duration': duration,
            'soglia': self._shadow.get('soglia', 60) if self._shadow else 60,
            'regime': self._shadow.get('regime_entry', self._regime_current) if self._shadow else self._regime_current,
        })

        # ── L1.5 — Aggiorna VERITAS GATE per contesto ────────────────────
        if self._shadow:
            _ctx_str = (f"{self._shadow.get('momentum_entry', 'MEDIO')}|"
                       f"{self._shadow.get('volatility_entry', 'MEDIA')}|"
                       f"{self._shadow.get('trend_entry', 'SIDEWAYS')}")
            if _ctx_str not in self._m2_ctx_stats:
                self._m2_ctx_stats[_ctx_str] = {'n': 0, 'wins': 0, 'pnl_sum': 0.0}
            self._m2_ctx_stats[_ctx_str]['n']       += 1
            self._m2_ctx_stats[_ctx_str]['wins']    += 1 if is_win else 0
            self._m2_ctx_stats[_ctx_str]['pnl_sum'] += pnl

        # ════════════════════════════════════════════════════════════════
        # DECAY_25MAG2026 — DEADLOCK FIX (intervento di capoprogetto)
        # ════════════════════════════════════════════════════════════════
        # Roberto ha verificato: 8/11 silenzi >= 1h sono causati da
        # SC streak >= 4 che blocca tutto. _m2_loss_streak si azzerava SOLO
        # con un WIN. Ma SC bloccava ogni entry → no WIN possibili → 
        # solo restart manuale del processo liberava (silenzi 40h e 64h).
        #
        # FIX: ogni 30 min senza nuovi LOSS, streak scende di 1.
        # Effetto: streak da 12 scende a 0 in 6h invece di restare 64h.
        # Il bot si auto-guarisce senza restart manuale.
        #
        # NOTA importante: questo NON disabilita SC streak >= 4.
        # SC continua a proteggere dopo 4 LOSS reali consecutivi.
        # Ma quando il mercato si rinfresca, il bot torna operativo.
        # ════════════════════════════════════════════════════════════════
        DECAY_MINUTI = 30  # ogni N min senza LOSS, streak -= 1
        if hasattr(self, '_m2_last_loss_time') and self._m2_last_loss_time and self._m2_loss_streak > 0:
            minuti_senza_loss = (now - self._m2_last_loss_time) / 60.0
            decadimenti = int(minuti_senza_loss / DECAY_MINUTI)
            if decadimenti > 0:
                _streak_prima = self._m2_loss_streak
                self._m2_loss_streak = max(0, self._m2_loss_streak - decadimenti)
                if _streak_prima != self._m2_loss_streak:
                    self._log_m2("⏳", f"DECAY_STREAK: {_streak_prima} → {self._m2_loss_streak} "
                                       f"(dopo {minuti_senza_loss:.0f}min senza LOSS, -{decadimenti})")

        if is_win:
            self._m2_loss_streak = 0
        else:
            self._m2_loss_streak += 1
            self._m2_last_loss_time = now

            # -- COOLDOWN PROPORZIONALE AL DANNO --------------------------
            abs_pnl = abs(pnl)
            if abs_pnl < 1.0:
                base_cooldown = 10
            elif abs_pnl < 20.0:
                base_cooldown = 20
            else:
                base_cooldown = 45

            streak_mult = min(2.0, 0.5 + self._m2_loss_streak * 0.5)
            cooldown = min(120, base_cooldown * streak_mult)
            self._m2_cooldown_until = now + cooldown

        # -- TRANSIZIONE DI STATO ----------------------------------------
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
            self._log_m2("[CFG]️", f"STATO → {self._state} (loss_streak={self._m2_loss_streak} recent_wr={recent_wr:.0%} cooldown={self._m2_cooldown_until - now:.0f}s)")
            self.telemetry.log_state_change(old_state, self._state, self._m2_loss_streak,
                self._regime_current, self.campo._direction, self._shadow is not None)

    def _state_engine_can_enter(self) -> tuple:
        """Ritorna (can_enter: bool, reason: str). Gate PRIMA di qualsiasi entry."""
        now = time.time()

        # ════════════════════════════════════════════════════════════════
        # FIX DEADLOCK STREAK (28mag, Roberto):
        # Il decay dello streak viveva in _state_engine_update(), che gira
        # SOLO alla chiusura di un trade. Ma quando SC blocca per streak>=4,
        # NON si apre nulla → NON si chiude nulla → il decay non scattava MAI.
        # Lo streak restava a 4 all'infinito (osservato: 5h ferme con streak_4,
        # storico: silenzi 40h/64h sbloccati solo da restart manuale).
        # SOLUZIONE: il decay deve girare anche quando il bot è bloccato, cioè
        # QUI, a ogni tentativo di entry, non solo alla chiusura trade.
        # Identica formula del decay originale (30 min/punto). Non disabilita
        # la protezione SC streak>=4: la lascia decadere quando il mercato
        # si rinfresca, esattamente come previsto dal commento a riga ~8646.
        # ════════════════════════════════════════════════════════════════
        DECAY_MINUTI_TICK = 30  # ogni N min senza nuovo LOSS, streak -= 1
        if (getattr(self, '_m2_last_loss_time', None)
                and self._m2_loss_streak > 0):
            _minuti_senza_loss = (now - self._m2_last_loss_time) / 60.0
            _decadimenti = int(_minuti_senza_loss / DECAY_MINUTI_TICK)
            if _decadimenti > 0:
                _streak_prima = self._m2_loss_streak
                self._m2_loss_streak = max(0, self._m2_loss_streak - _decadimenti)
                if _streak_prima != self._m2_loss_streak:
                    # Avanzo il riferimento temporale dei decadimenti consumati,
                    # così il prossimo punto scade dopo altri 30 min reali e
                    # non tutto in un colpo al tick successivo.
                    self._m2_last_loss_time = self._m2_last_loss_time + _decadimenti * DECAY_MINUTI_TICK * 60.0
                    self._log_m2("⏳", f"DECAY_STREAK_TICK: {_streak_prima} → {self._m2_loss_streak} "
                                       f"(dopo {_minuti_senza_loss:.0f}min senza LOSS, gate entry)")

        # -- COOLDOWN ATTIVO → non entrare ------------------------------
        if now < self._m2_cooldown_until:
            remaining = self._m2_cooldown_until - now
            return False, f"COOLDOWN_{remaining:.0f}s (loss_streak={self._m2_loss_streak})"

        # -- DIFENSIVO → non entrare finché non torna NEUTRO o AGGRESSIVO
        # MA: deadlock protection - max 5 minuti in DIFENSIVO
        if self._state == "DIFENSIVO":
            time_in_defensive = now - self._state_since
            if time_in_defensive > 300:  # 5 minuti
                self._state = "NEUTRO"
                self._state_since = now
                self._m2_loss_streak = 0
                self._log_m2("[CFG]️", f"STATO → NEUTRO (auto-reset dopo {time_in_defensive/60:.1f} min in DIFENSIVO)")
                self.telemetry.log_state_change("DIFENSIVO", "NEUTRO", 0,
                    self._regime_current, self.campo._direction, self._shadow is not None)
            else:
                return False, f"DIFENSIVO_{300-time_in_defensive:.0f}s (loss_streak={self._m2_loss_streak})"

        # -- VELOCITÀ: non entrare se ultimo trade chiuso < 30 secondi fa -
        if self._m2_recent_trades:
            last = self._m2_recent_trades[-1]
            if now - last['ts'] < 30:
                return False, f"TROPPO_VELOCE ({now - last['ts']:.1f}s dall'ultimo)"

        # -- LOSS PESANTE: se ultimo loss > $50, pausa 30 secondi -----
        if self._m2_recent_trades:
            last = self._m2_recent_trades[-1]
            if not last['is_win'] and abs(last['pnl']) > 50:
                if now - last['ts'] < 30:
                    return False, f"LOSS_PESANTE_${abs(last['pnl']):.0f}_pausa"

        return True, "OK"

    # ========================================================================
    # AUTO-TUNING SOGLIA - IL SISTEMA IMPARA DAI PROPRI PHANTOM
    # Non servono manopole. I phantom dicono se la soglia è giusta.
    # ========================================================================

    def _auto_tune_soglia(self):
        """
        AUTO-TUNE ADATTIVO - intervallo e step proporzionali alla gravita.
        
        Bilancio phantom < -$500  → intervallo 120s, step 3
        Bilancio phantom < -$200  → intervallo 300s, step 2
        Bilancio phantom < -$50   → intervallo 600s, step 1
        Bilancio phantom ≥ $0     → intervallo 900s, step 1
        
        WR phantom > 75% → step × 2 (molto lontano dall'equilibrio)
        """
        now = time.time()

        phantom_summary = self._get_phantom_summary()
        bilancio = phantom_summary.get('bilancio', 0)

        if bilancio < -500:
            adaptive_interval = 120
            base_step = 3
        elif bilancio < -200:
            adaptive_interval = 300
            base_step = 2
        elif bilancio < -50:
            adaptive_interval = 600
            base_step = 1
        else:
            adaptive_interval = 900
            base_step = 1

        if now - self._last_soglia_autotune < adaptive_interval:
            return

        stats = self._phantom_stats.get("SCORE_INSUFFICIENTE")

        # ════════════════════════════════════════════════════════════════
        # FIX 2026-05-09 — PREDIZIONE COMANDA SU AUTO-DIFESA
        # ════════════════════════════════════════════════════════════════
        # Se pred_score >= 70% E veritas conferma edge positivo,
        # la predizione è "viva" e l'auto-tune NON deve irrigidire la soglia.
        # Altrimenti la difesa storica strangola la predizione attiva.
        # PATCH 0: SOLO se _pred["qualified"]. Senza qualifica, l'auto-tune
        # procede normalmente, la difesa storica vince. Niente skip su pred finta.
        # ════════════════════════════════════════════════════════════════
        if hasattr(self, 'supercervello') and self.supercervello is not None:
            _pred_st = self.supercervello._get_pred_state()
        else:
            _pred_st = {"score": 0.0, "calib": 0.0, "source": "NO_SC",
                        "enabled": False, "qualified": False}
        _pred_score = _pred_st["score"]
        _vt_pnl_avg = 0.0
        if hasattr(self, 'veritas') and self.veritas._stats:
            # Media pnl_avg dei segnali FUOCO|PREVISTO_ENTRA (l'edge predittivo principale)
            _fuoco_pe = self.veritas._stats.get('FUOCO|PREVISTO_ENTRA', {})
            _vt_pnl_avg = _fuoco_pe.get('pnl_avg', 0.0)
        _pred_attiva = _pred_st["qualified"] and _pred_score >= 70.0 and _vt_pnl_avg > 0.0

        # Cap comparto-relativo: la soglia non può salire più di +6 dalla base del comparto attivo
        _comp_attivo_now = None
        if hasattr(self, '_comparto') and self._comparto:
            from comparto_engine import COMPARTI
            _comp_attivo_now = COMPARTI.get(self._comparto._attivo)
        _cap_base_max = (_comp_attivo_now.soglia_base + 6) if _comp_attivo_now else 50
        _cap_min_max  = (_comp_attivo_now.soglia_min  + 6) if _comp_attivo_now else 44

        # FIX: AutoCalibratore guarda anche i trade reali persi consecutivi
        recent = list(self._m2_recent_trades)[-5:] if self._m2_recent_trades else []
        recent_losses = sum(1 for t in recent if not t.get('is_win', False))
        if recent_losses >= 3 and len(recent) >= 3:
            # ── Skip irrigidimento se predizione attiva ─────────────
            if _pred_attiva:
                self._last_soglia_autotune = now
                self._log_m2("🦊", f"AUTO-TUNE LOSS_REAL: {recent_losses}/5 loss reali "
                                  f"MA PRED_ATTIVA ps={_pred_score:.0f}% "
                                  f"vt_pnl={_vt_pnl_avg:+.2f} → NO irrigidimento")
                return
            step = base_step
            # Cap relativo al comparto attivo invece di hardcoded 50/58
            new_min  = min(_cap_min_max,  self.campo.SOGLIA_MIN  + step)
            new_base = min(_cap_base_max, self.campo.SOGLIA_BASE + step)
            old_min  = self.campo.SOGLIA_MIN
            old_base = self.campo.SOGLIA_BASE
            self.campo.SOGLIA_MIN  = new_min
            self.campo.SOGLIA_BASE = new_base
            self._last_soglia_autotune = now
            self._log_m2("🎯", f"AUTO-TUNE LOSS_REAL: {recent_losses}/5 loss reali → ALZA soglia "
                              f"MIN {old_min}→{new_min} BASE {old_base}→{new_base} "
                              f"(cap_base={_cap_base_max})")
            return

        if not stats:
            return

        total_closed = stats['would_win'] + stats['would_lose']
        if total_closed < 10:
            return

        prev = self._phantom_stats_snapshot.get("SCORE_INSUFFICIENTE", {})
        prev_win = prev.get('would_win', 0)
        prev_lose = prev.get('would_lose', 0)
        delta_win = stats['would_win'] - prev_win
        delta_lose = stats['would_lose'] - prev_lose
        delta_total = delta_win + delta_lose

        if delta_total < 3:
            return

        delta_wr = delta_win / delta_total

        self._phantom_stats_snapshot["SCORE_INSUFFICIENTE"] = {
            'would_win': stats['would_win'],
            'would_lose': stats['would_lose'],
        }

        old_min = self.campo.SOGLIA_MIN
        old_base = self.campo.SOGLIA_BASE

        step = base_step * 2 if delta_wr > 0.75 else base_step

        # L1: pavimenti SANITY coerenti con nuovi comparti, non più 50/55 hardcoded
        SOGLIA_MIN_SANITY  = 28   # MIN può scendere fino a 28 (era 50)
        SOGLIA_BASE_SANITY = 34   # BASE può scendere fino a 34 (era 55)

        if delta_wr > 0.60:
            new_min = max(SOGLIA_MIN_SANITY, old_min - step)
            new_base = max(SOGLIA_BASE_SANITY, old_base - step)
            action = "ABBASSA"
        elif delta_wr < 0.40:
            # ── FIX 2026-05-09 — Skip ALZA se predizione attiva ──
            if _pred_attiva:
                self._last_soglia_autotune = now
                self._log_m2("🦊", f"AUTO-TUNE PHANTOM: WR={delta_wr:.0%} basso "
                                  f"MA PRED_ATTIVA → NO ALZA (predizione comanda)")
                return
            # Cap relativo al comparto invece di hardcoded 58/50
            new_min = min(_cap_min_max,  old_min  + step)
            new_base = min(_cap_base_max, old_base + step)
            action = "ALZA"
        elif bilancio < -100:
            # WR nella zona morta (40-60%) MA bilancio molto negativo
            # I WIN phantom sono più grossi dei LOSS → la soglia costa troppo
            # Abbassa con step ridotto (1) - cautela nella zona morta
            new_min = max(SOGLIA_MIN_SANITY, old_min - 1)
            new_base = max(SOGLIA_BASE_SANITY, old_base - 1)
            action = "ABBASSA_PNL"
        else:
            self._last_soglia_autotune = now
            self._log_m2("🎯", f"AUTO-TUNE: soglia OK (phantom WR={delta_wr:.0%} su {delta_total} campioni, bil=${bilancio:.0f})")
            return

        # Auto-tune non supera mai soglia calcolata dinamicamente
        # Il soffitto è determinato dai dati reali, non dall'algoritmo
        soglia_max_permessa = _calcola_soglia_da_signal_tracker(self)
        new_base = min(new_base, soglia_max_permessa['base'] + 5)  # max +5 rispetto al dinamico
        new_min  = min(new_min,  soglia_max_permessa['min']  + 5)
        self.campo.SOGLIA_MIN = new_min
        self.campo.SOGLIA_BASE = new_base
        self._last_soglia_autotune = now

        self._log_m2("🎯", f"AUTO-TUNE: {action} soglia step={step} | phantom WR={delta_wr:.0%} "
                          f"({delta_win}W/{delta_lose}L su {delta_total}) bil=${bilancio:.0f} "
                          f"| MIN {old_min}→{new_min} BASE {old_base}→{new_base} "
                          f"[intervallo={adaptive_interval}s]")

    # ========================================================================
    # MOTORE 2: CAMPO GRAVITAZIONALE - Shadow Entry/Exit/Close
    # ========================================================================

    def _phantom_supervisor(self):
        """
        Loop chiuso Phantom → Capsule correttiva.

        Ogni 60s analizza il Phantom per componente.
        Se un componente blocca con WR anomalo → genera capsule correttiva.

        WR blocco > 45% su 20+ casi → sta bloccando cose buone → AUTO_ALLENTA soglia
        WR blocco < 25% su 20+ casi → sta lasciando passare cose cattive → AUTO_IRRIGIDISCE

        Questo è il sistema che si autocorregge da solo.
        """
        now = time.time()
        if now - getattr(self, '_last_phantom_sup', 0) < 60:
            return
        self._last_phantom_sup = now

        per_livello = getattr(self, '_phantom_per_livello', {})
        if not per_livello:
            return

        for blocco_id, dati in per_livello.items():
            ww  = dati.get('would_win', 0)
            wl  = dati.get('would_lose', 0)
            blk = dati.get('blocked', 0)
            tot = ww + wl
            if tot < 20:
                continue

            wr_blocco = ww / tot

            # Sta bloccando cose che avrebbero vinto — soglia troppo alta
            if wr_blocco > 0.45 and blk > 20:
                cap_id = f"AUTO_ALLENTA_{blocco_id[:30]}"
                existing = [c for c in self.capsule_runtime.capsules
                           if c.get('capsule_id') == cap_id and c.get('enabled')]
                if not existing:
                    # Abbassa soglia di 3 punti per 30 minuti
                    old_base = self.campo.SOGLIA_BASE
                    # L1: floor 32 coerente con sanity (era 40)
                    new_base = max(32, old_base - 3)
                    self.campo.SOGLIA_BASE = new_base
                    self._log_m2("🧠",
                        f"PHANTOM_SUP: {blocco_id} WR={wr_blocco:.0%} blk={blk} "
                        f"→ AUTO_ALLENTA soglia {old_base}→{new_base}")
                    # Salva nella storia per apprendimento
                    if not hasattr(self, '_phantom_sup_log'):
                        self._phantom_sup_log = []
                    self._phantom_sup_log.append({
                        'ts':      time.strftime('%H:%M:%S'),
                        'blocco':  blocco_id,
                        'wr':      round(wr_blocco, 3),
                        'azione':  f'ALLENTA {old_base}→{new_base}',
                    })

            # FIX #30 (12mag2026 sera): RAMO IRRIGIDISCE DISABILITATO
            # ════════════════════════════════════════════════════════════════
            # Bug logico: wr_blocco è il WR DEI BLOCCHI, cioè dei trade BLOCCATI 
            # dal gate. Se wr_blocco è BASSO significa che i trade bloccati 
            # avrebbero PERSO → il gate sta facendo IL SUO LAVORO.
            # 
            # La logica originale "wr_blocco < 0.25 → IRRIGIDISCE" è invertita:
            # alza la soglia proprio quando il gate funziona meglio, 
            # strangolando l'operatività. 
            # 
            # Disabilita il ramo. Si conserva ALLENTA (che è corretto).
            # Se in futuro serve un IRRIGIDISCE, va riscritto su altra metrica
            # (es. WR dei PASSATI, non dei BLOCCATI).
            # ════════════════════════════════════════════════════════════════
            elif wr_blocco < 0.25 and blk > 20:
                # FIX #30: DISABILITATO. Era logica invertita.
                # Log diagnostico per misurare quante volte AVREBBE scattato.
                if not hasattr(self, '_phantom_sup_log'):
                    self._phantom_sup_log = []
                self._phantom_sup_log.append({
                    'ts':      time.strftime('%H:%M:%S'),
                    'blocco':  blocco_id,
                    'wr':      round(wr_blocco, 3),
                    'azione':  f'IRRIGIDISCE_SKIP_FIX30 (gate funziona, wr_blocco={wr_blocco:.0%})',
                })
                pass


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

    def _emit_bridge_event(self, event_name: str, payload: dict):
        """
        B4: Emette evento urgente verso il bridge.
        Il bridge lo legge nel prossimo ciclo (max 5s) invece di aspettare il timer.
        """
        self._bridge_event_queue.append({
            'name': event_name,
            'payload': payload,
            'ts': time.time(),
        })
        # Mantieni solo ultimi 10 eventi
        if len(self._bridge_event_queue) > 10:
            self._bridge_event_queue.pop(0)
        # Scrivi nel heartbeat per il bridge
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data['bridge_events'] = list(self._bridge_event_queue[-5:])
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()
        log.info(f"[BRIDGE_EVENT] {event_name} {payload}")

    def _log_m2(self, emoji: str, msg: str):
        """Log dedicato Motore 2 - separato dal Motore 1."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} [M2] {msg}"
        self._m2_log.append(entry)
        log.info(entry)

    def _log_constitutional(self, verbale: dict, event: str):
        """
        Log costituzionale della scena (Passo 5a, AUDIT ROBERTO V1).

        Registra ogni exit point e ogni decisione del flow _evaluate_shadow_entry.
        NON modifica il comportamento. Solo traccia.

        event:
          PRE_SC_VETO_<name>   — veto fisico legittimo della ZONA 1
          SC_INPUTS_FULL       — il verbale completo quando SC viene chiamato
          SC_DECISION_FINAL    — la decisione di SC (PERCORSO 2)
          SC_DECISION_EXPLOSIVE — la decisione di SC su PERCORSO 1 (5c)
          ENTRY_OPENED         — apertura posizione
        """
        try:
            if not hasattr(self, '_constitutional_log'):
                self._constitutional_log = deque(maxlen=200)

            # Riga compatta per app.log
            tsu   = verbale.get('tsunami_vote') or '-'
            tsu_c = verbale.get('tsunami_confidence')
            ora   = verbale.get('fp_wr')
            verw  = verbale.get('veritas_ctx_wr')
            vern  = verbale.get('veritas_ctx_samples')
            capb  = verbale.get('capsule_block_score', 0)
            sco   = verbale.get('score')
            sog   = verbale.get('soglia')

            parts = [f"tsu={tsu}/{tsu_c if tsu_c is not None else '-'}"]
            parts.append(f"ora={ora:.2f}" if ora is not None else "ora=-")
            parts.append(f"ver={verw:.2f}/{vern}" if verw is not None else "ver=-")
            parts.append(f"capB={capb:.0f}")
            parts.append(f"score={sco:.1f}/{sog:.1f}" if sco is not None and sog is not None else "score=-")
            compact = " ".join(parts)

            self._constitutional_log.append({
                "ts":      verbale.get('tick_ts'),
                "event":   event,
                "compact": compact,
                "verbale": dict(verbale),
            })

            ts_str = datetime.utcnow().strftime('%H:%M:%S')
            log.info(f"{ts_str} 🏛️ [CONST] {event} | {compact}")
        except Exception as _ce:
            # L'osservatorio non deve MAI rompere il flow
            log.debug(f"[CONST] errore log: {_ce}")

    def _sc_osserva_explosive(self, price, momentum, volatility, trend,
                              _dir, score, soglia, verbale, seed):
        """
        PASSO 5c (14mag2026) — SC DECISORE su PERCORSO 1 (EXPLOSIVE).

        Prima (MODO 2, Passo 5a): SC era solo OSSERVATORE — calcolava la
        decisione ma non la applicava, l'EXPLOSIVE entrava come oggi. I log
        live di 5b hanno dato il verdetto: PERCORSO 1 apriva LONG con il
        mercato giù su 3 scale e Tsunami che diceva SHORT. L'ultimo dittatore.

        Ora: SC riceve il verbale completo e DECIDE. Questo metodo RITORNA
        il dict di decide() (azione, direction_vote, size_mult, ...).
        PERCORSO 1 rispetta la decisione: BLOCCA → non apre; direction_vote
        diverso → flippa; size_mult → modula. Stesso schema del blocco
        riga ~8964 di PERCORSO 2 (5b), replicato in PERCORSO 1.

        RITORNA: dict decide() oppure None se eccezione (PERCORSO 1 gestisce
        il None come "decisione non disponibile → entra come fallback").
        """
        try:
            _mat     = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
            _st_ctx  = self._get_signal_tracker_context(self._regime_current, score)
            _ph_st   = self._phantom_stats.get('SCORE_INSUFFICIENTE', {})
            _fp_smp  = self.oracolo._memory.get(
                self.oracolo._fp(momentum, volatility, trend, _dir), {}
            ).get('real_samples', 0)

            _sc_obs = self.supercervello.decide(
                fp_wr            = verbale.get('fp_wr', 0.5),
                fp_samples       = _fp_smp,
                st_hit_rate      = _st_ctx['hit_rate'],
                st_n             = _st_ctx['n'],
                st_pnl           = _st_ctx['pnl_sim'],
                oi_carica        = self._oi_carica,
                oi_stato         = self._oi_stato,
                # PASSO 11: passa anche l'OI SHORT per il pred_boost bilaterale
                oi_carica_short  = self._oi_carica_short,
                oi_stato_short   = self._oi_stato_short,
                score            = score,
                soglia           = soglia,
                matrimonio_wr    = _mat.get('wr', 0.5),
                matrimonio_trust = self.memoria.get_trust(_mat.get('name', '')),
                ph_protezione    = _ph_st.get('would_lose', 0),
                ph_zavorra       = _ph_st.get('would_win', 0),
                regime           = self._regime_current,
                midzone          = False,
                loss_streak      = self._m2_loss_consecutivi(),
                # ── verbale costituzionale ──
                tsunami_vote          = verbale.get('tsunami_vote'),
                tsunami_confidence    = verbale.get('tsunami_confidence'),
                tsunami_direction     = verbale.get('tsunami_direction'),
                tsunami_reason        = verbale.get('tsunami_reason'),
                veritas_ctx_wr        = verbale.get('veritas_ctx_wr'),
                veritas_ctx_samples   = verbale.get('veritas_ctx_samples'),
                veritas_ctx_pnl_avg   = verbale.get('veritas_ctx_pnl_avg'),
                veritas_last_judgement= verbale.get('veritas_last_judgement'),
                capsule_block_score   = verbale.get('capsule_block_score'),
                capsule_boost_score   = verbale.get('capsule_boost_score'),
                capsule_reasons       = verbale.get('capsule_reasons'),
                capsule_oracolo_override = verbale.get('capsule_oracolo_override'),
                proposed_direction    = verbale.get('proposed_direction'),
                flip_confidence       = verbale.get('flip_confidence'),
                breath_fase           = verbale.get('breath_fase'),
                breath_energia        = verbale.get('breath_energia'),
                sc_inputs_full        = True,
            )
            # PASSO 5c: SC DECIDE. Logga la decisione e la RITORNA.
            log.info(f"🏛️ [SC_DECISION_EXPLOSIVE] PERCORSO1 → "
                     f"azione={_sc_obs.get('azione')} "
                     f"dir_vote={_sc_obs.get('direction_vote')} "
                     f"size_mult={_sc_obs.get('size_mult')} "
                     f"(motivo: {_sc_obs.get('motivo','')})")
            verbale["sc_decision_explosive"] = _sc_obs.get('azione')
            return _sc_obs
        except Exception as e:
            log.debug(f"[SC_DECIDE_EXPLOSIVE] {e}")
            return None

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
        
        # ===============================================================
        # FLIP INTELLIGENTE - non reagisce al passato, anticipa il futuro
        #
        # Il drift misura cosa È SUCCESSO. Il momentum misura cosa STA SUCCEDENDO.
        # Lo SHORT deve entrare all'INIZIO del calo, non alla fine.
        #
        # Per LONG → SHORT servono 3 condizioni SIMULTANEE:
        #   1. Momentum attuale indica calo (non solo drift passato)
        #   2. MACD conferma (histogram negativo)
        #   3. Decelerazione bassa (l'impulso ribassista è FRESCO, non esaurito)
        #
        # Per SHORT → LONG:
        #   1. Momentum non più ribassista
        #   2. Drift torna positivo O MACD gira positivo
        # ===============================================================
        
        # Analizza l'energia ribassista ATTUALE
        decel = self.decelero.analyze()
        decel_score = decel.get('decel_score', 0)
        mom_fast = decel.get('mom_fast', 0)  # momentum veloce (ultimi 5 tick)
        
        bearish_energy = 0
        
        if campo._direction == "LONG":
            # Per andare SHORT: serve impulso ribassista FRESCO
            # 1. Momentum veloce negativo (il prezzo sta scendendo ORA)
            if mom_fast < -0.5:
                bearish_energy += 1
            if mom_fast < -1.0:
                bearish_energy += 1  # impulso forte
            
            # 2. MACD conferma tendenza ribassista
            if macd_hist < -2.0:
                bearish_energy += 1
            
            # 3. Decelerazione BASSA = impulso fresco (non esaurito)
            # Se decel è alta, il calo sta gia finendo → NON flippare
            if decel_score < 0.4:
                bearish_energy += 1  # impulso ancora vivo
            
            # 4. Drift come conferma (non come trigger primario)
            if drift < -0.08:
                bearish_energy += 1
        else:
            # Per restare SHORT: basta che l'impulso ribassista non sia morto
            if mom_fast < 0:
                bearish_energy += 1
            if drift < -0.03:
                bearish_energy += 1
            if macd_hist < 0:
                bearish_energy += 1
        
        # Conferma: conta tick consecutivi con energia bearish alta
        if bearish_energy >= 3:
            campo._direction_bearish_streak += 1
        else:
            campo._direction_bearish_streak = 0
        
        # Cooldown: minimo 120 secondi tra flip normali
        # MA: OI SHORT FUOCO >= 0.85 bypassa il cooldown — il mercato ha dichiarato
        now = time.time()
        _oi_short_fuoco = (getattr(self, '_oi_stato_short', '') == "FUOCO" and
                           getattr(self, '_oi_carica_short', 0) >= 0.85)
        cooldown_ok = (now - campo._direction_last_change) >= 120 or _oi_short_fuoco

        old_direction = campo._direction

        # -- EXPLOSIVE GATE: in EXPLOSIVE flip SHORT con meno energia ------
        # Signal Tracker: SHORT EXPLOSIVE hit 89% su 36 segnali — gate permissivo
        # OI SHORT FUOCO bypassa anche lo streak — entra subito al primo tick
        _short_streak_ok = campo._direction_bearish_streak >= 1 or _oi_short_fuoco
        if self._regime_current == "EXPLOSIVE" and campo._direction == "LONG" and bearish_energy >= 2 and cooldown_ok and _short_streak_ok:
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            _motivo = "OI_SHORT_FUOCO" if _oi_short_fuoco else f"bearish_energy={bearish_energy}"
            self._log_m2("🔄", f"FLIP → SHORT in EXPLOSIVE ({_motivo} drift={drift:+.3f}%)")

        # FLIP LONG in EXPLOSIVE — speculare al SHORT
        # OI LONG FUOCO >= 0.80 + momentum positivo → flippa a LONG
        # LONG|FORTE|BASSA|UP WR=78%, LONG|FORTE|MEDIA|UP WR=68%
        _oi_long_fuoco = (getattr(self, '_oi_stato', '') == "FUOCO" and
                          getattr(self, '_oi_carica', 0) >= 0.65)
        _bullish_energy = 0
        if mom_fast > 0.5:  _bullish_energy += 1
        if mom_fast > 1.0:  _bullish_energy += 1
        if macd_hist > 2.0: _bullish_energy += 1
        if drift > 0.08:    _bullish_energy += 1

        if (self._regime_current == "EXPLOSIVE" and
                campo._direction == "SHORT" and
                _oi_long_fuoco and
                _bullish_energy >= 2 and
                cooldown_ok):
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"FLIP → LONG in EXPLOSIVE (bullish_energy={_bullish_energy} drift={drift:+.3f}%)")

        # ════════════════════════════════════════════════════════════════
        # TRENDING_BEAR GATE — LA PORTA FISICA ALLO SHORT (31mag, Roberto)
        # ════════════════════════════════════════════════════════════════
        # Finora il campo poteva andare SHORT solo in EXPLOSIVE o RANGING.
        # In TRENDING_BEAR — il regime dove i dati storici danno allo SHORT
        # il WR più alto (SHORT|*|*|DOWN 48-55%) — non c'era nessuna porta:
        # in trend ribassista il campo restava LONG e non si girava mai.
        #
        # Principio (Roberto): la capsula/gate AGISCE SUBITO dove la fisica
        # dice profitto. Qui la fisica è già dichiarata dal REGIME stesso:
        # TRENDING_BEAR = il mercato sta scendendo in modo strutturato, non
        # è uno spike. Quindi NON serve una soglia di drift inventata: riuso
        # bearish_energy, la misura fisica composita già calcolata sopra
        # (momentum veloce giù, MACD giù, decel bassa, drift giù). Soglia >=2
        # = stessa permissività del gate EXPLOSIVE, coerente.
        # Lo STREAK non è richiesto: il regime BEAR è già la conferma. "Subito".
        #
        # NB: questo apre SOLO la nascita del flip (infrastruttura). La
        # decisione se il trade nasce davvero resta a valle (SC + capsule).
        # L'antiaerea sulle firme perdenti è il passo successivo, non qui.
        if (self._regime_current == "TRENDING_BEAR" and campo._direction == "LONG"
                and bearish_energy >= 2 and cooldown_ok):
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"FLIP → SHORT in TRENDING_BEAR "
                              f"(bearish_energy={bearish_energy} drift={drift:+.3f}%)")

        # -- RANGING GATE: in laterale NON flippare a SHORT --------------
        # ECCEZIONE VERITAS: se il Veritas vede movimento ribassista reale
        # con delta_60s < -20 su almeno 5 segnali → lo SHORT è legittimo
        # ECCEZIONE OI SHORT FUOCO: mercato ha dichiarato la direzione
        _veritas_short_ok = False
        # ════════════════════════════════════════════════════════════════
        # SHORT_VIA_DRIFT (30mag, Roberto) — LA VIA PULITA ALLO SHORT
        # ════════════════════════════════════════════════════════════════
        # Prima: _drift_short_ok = drift < -0.005 AND bearish_energy >= 3
        #   Due cadaveri occupavano le altre vie (RSI fisso 50, pred mai
        #   qualificata) e questa era strozzata da bearish_energy>=3 (3 segnali
        #   fisici insieme). Risultato: 20/20 trade LONG, mai uno SHORT.
        # Ora: la fisica di Roberto — "il mercato scende sostenuto → vado short".
        #   Soglia -0.02% = 4x il rumore tipico del RANGING (±0.05% lo dice il
        #   commento del codice stesso). Persistenza 3 tick = non è uno spike,
        #   è un calo vero. Speculare a come il LONG segue il drift positivo.
        #   Niente RSI, niente pred: vie morte rimosse dal blocco.
        SHORT_DRIFT_THRESHOLD = -0.02   # drift % sotto cui conta come calo vero
        SHORT_DRIFT_TICKS     = 3       # tick consecutivi di conferma
        if drift < SHORT_DRIFT_THRESHOLD:
            campo._drift_neg_streak += 1
        else:
            campo._drift_neg_streak = 0
        _drift_short_ok = campo._drift_neg_streak >= SHORT_DRIFT_TICKS
        if _drift_short_ok:
            self._log_m2("📉", f"SHORT_VIA_DRIFT: drift={drift:+.3f}% sostenuto "
                              f"{campo._drift_neg_streak} tick (soglia {SHORT_DRIFT_THRESHOLD}%)")
        _rsi_ipercomprato = _rsi_now >= 75 and bearish_energy >= 3
        # Predizione indica DOWN: energia short > energia long + pred_score alto
        # PATCH 0: SOLO se _pred["qualified"]. Senza qualifica, _pred_down=False,
        # SHORT in RANGING resta bloccato dalla regola standard.
        _pred_st = self.supercervello._get_pred_state() if hasattr(self, 'supercervello') and self.supercervello is not None else {"score": 0.0, "calib": 0.0, "qualified": False}
        _ps = _pred_st["score"]
        _pc = _pred_st["calib"]
        _oi_short_maggiore = getattr(self, '_oi_carica_short', 0) > getattr(self, '_oi_carica', 0)
        _pred_down = (_pred_st["qualified"]
                      and _ps >= 70 and _pc >= 50
                      and _oi_short_maggiore and bearish_energy >= 2)
        if _oi_short_fuoco:
            _veritas_short_ok = True
        if _rsi_ipercomprato:
            _veritas_short_ok = True
        if _pred_down:
            _veritas_short_ok = True
            self._log_m2("📉", f"PRED_DOWN: short={getattr(self,'_oi_carica_short',0):.2f} > long={getattr(self,'_oi_carica',0):.2f} pred={_ps:.0f}%")
        if hasattr(self, 'veritas') and self.veritas._stats:
            for k, s in self.veritas._stats.items():
                if 'FUOCO' in k or 'CARICA' in k:
                    deltas = s.get('deltas', [])
                    if len(deltas) >= 5:
                        avg_delta = sum(deltas) / len(deltas)
                        if avg_delta < -20:
                            _veritas_short_ok = True
                            break

        # ════════════════════════════════════════════════════════════════
        # FIX #23 (12mag2026): SBLOCCO SHORT in RANGING via TSUNAMI 2min
        # ════════════════════════════════════════════════════════════════
        # Diagnosi 12mag pomeriggio: bot bloccato LONG in 545/545 phantom 
        # (1h30 di osservazione) mentre tsunami 2min DOWN in 84% dei casi.
        # Roberto: "Bot sempre LONG = anomalo".
        #
        # Logica fisica: se il TSUNAMI 2min (filtro low-pass strutturale)
        # vede DOWN con forza >= 0.45 e coerenza >= 0.60, allora c'è un 
        # trend ribassista strutturato — lo SHORT è legittimo anche in 
        # RANGING. Le altre eccezioni restano (drift, veritas, OI fuoco).
        #
        # Soglie scelte:
        #  - strength 0.45  → corrisponde a WIN più bassi osservati (0.42-0.43)
        #  - coerenza 0.60  → discrimina tra LOSS (~0.55) e WIN (~0.63)
        # ════════════════════════════════════════════════════════════════
        _tsunami_short_ok = False
        try:
            if hasattr(self, 'tsunami') and self.tsunami is not None:
                _ts_last = self.tsunami.last_decision()
                if _ts_last:
                    _v2 = _ts_last.get('verdetti', {}).get('2min', {})
                    if (_v2.get('direction') == 'DOWN' and 
                        _v2.get('strength', 0) >= 0.45 and 
                        _v2.get('coerenza', 0) >= 0.60):
                        _tsunami_short_ok = True
                        self._log_m2("🌊", f"TSUNAMI_SHORT_OK: 2min DOWN "
                                          f"str={_v2.get('strength',0):.2f} "
                                          f"coe={_v2.get('coerenza',0):.2f}")
        except Exception:
            pass

        # ════════════════════════════════════════════════════════════════
        # SHORT_VIA_DRIFT — RAMO DEDICATO (30mag, Roberto)
        # ════════════════════════════════════════════════════════════════
        # Questa via NON dipende da bearish_streak >= 3: il drift negativo
        # sostenuto per 3 tick È GIÀ la conferma fisica. È la porta pulita
        # allo SHORT che il LONG ha sempre avuto col drift positivo.
        # Sta PRIMA del gate "evita short" così non viene intrappolata.
        if (self._regime_current == "RANGING" and campo._direction == "LONG"
                and cooldown_ok and _drift_short_ok):
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            campo._drift_neg_streak = 0
            self._log_m2("🔄", f"FLIP → SHORT in RANGING via DRIFT "
                              f"(drift={drift:+.3f}% sostenuto)")

        elif self._regime_current == "RANGING" and campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok and not _veritas_short_ok and not _drift_short_ok and not _tsunami_short_ok:
            # NON flippare - logga come SHORT evitato
            if not hasattr(self, '_shadow_short_log'):
                self._shadow_short_log = []
            if not hasattr(self, '_shadow_short_phantoms'):
                self._shadow_short_phantoms = []       # phantom SHORT aperti
            if not hasattr(self, '_shadow_short_results'):
                self._shadow_short_results = deque(maxlen=100)  # risultati chiusi
            
            current_price = self._last_price if hasattr(self, '_last_price') else 0
            
            self._shadow_short_log.append({
                'ts': now,
                'drift': drift,
                'macd_hist': macd_hist,
                'bearish_energy': bearish_energy,
                'mom_fast': decel.get('mom_fast', 0),
                'decel_score': decel_score,
                'regime': self._regime_current,
                'price': current_price,
            })
            
            # Apri phantom SHORT per simulare l'outcome
            if len(self._shadow_short_phantoms) < 3 and current_price > 0:
                self._shadow_short_phantoms.append({
                    'price_entry': current_price,
                    'entry_time': now,
                    'drift': drift,
                    'macd_hist': macd_hist,
                    'bearish_energy': bearish_energy,
                    'max_price': current_price,
                    'min_price': current_price,
                })
            
            self._log_m2("🔇", f"SHORT EVITATO in RANGING (drift={drift:+.3f}% macd={macd_hist:+.2f} energy={bearish_energy})")
            campo._direction_bearish_streak = 0
            # Non flippa - resta LONG
        
        # FIX #23 (12mag2026): FLIP a SHORT in RANGING quando tsunami 2min autorizza
        # Se _tsunami_short_ok è scattato → flippa effettivamente a SHORT.
        # (Altrimenti l'IF sopra ha bloccato il flip, ma _veritas/_drift non sono attivi)
        elif self._regime_current == "RANGING" and campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok and _tsunami_short_ok and not _veritas_short_ok and not _drift_short_ok:
            campo._direction = "SHORT"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"FLIP → SHORT via TSUNAMI 2min "
                              f"(drift={drift:+.3f}% bearish={bearish_energy})")
        
        # RSI OVERRIDE: ipervenduto/ipercomprato sovrasta tutto
        # RSI < 30 = mercato caduto troppo → LONG obbligatorio
        # RSI > 75 = mercato salito troppo → SHORT permesso solo se già SHORT
        _rsi_now = campo._last_rsi if hasattr(campo, '_last_rsi') else 50.0
        if _rsi_now < 30 and campo._direction == "SHORT" and cooldown_ok:
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
            self._log_m2("🔄", f"RSI OVERRIDE → LONG (RSI={_rsi_now:.0f} ipervenduto)")
        elif _rsi_now < 30 and campo._direction == "LONG":
            # Già LONG e ipervenduto — blocca flip SHORT
            # ECCEZIONE: OI SHORT FUOCO molto forte (>=0.90) + drift negativo + RANGING
            # = il mercato sta dichiarando la direzione nonostante RSI basso
            _oi_short_forte = (getattr(self, '_oi_carica_short', 0) >= 0.90 and
                               getattr(self, '_oi_stato_short', '') == "FUOCO")
            if _oi_short_forte and drift < -0.003 and self._regime_current == "RANGING":
                campo._direction = "SHORT"
                campo._direction_last_change = now
                campo._direction_bearish_streak = 0
                self._log_m2("🔄", f"FLIP → SHORT in RANGING (OI_SHORT_FORTE={getattr(self,'_oi_carica_short',0):.2f} drift={drift:+.3f}% RSI={_rsi_now:.0f})")
            else:
                campo._direction_bearish_streak = 0

        # In NON-RANGING: flip normale LONG → SHORT
        elif campo._direction == "LONG" and campo._direction_bearish_streak >= 3 and cooldown_ok:
            # Non flippare SHORT se RSI ipervenduto
            if _rsi_now < 35:
                self._log_m2("🔇", f"SHORT BLOCCATO da RSI={_rsi_now:.0f} ipervenduto")
                campo._direction_bearish_streak = 0
            else:
                campo._direction = "SHORT"
                campo._direction_last_change = now
                campo._direction_bearish_streak = 0
        # SHORT → LONG: energia bearish scesa sotto 2 + cooldown
        elif campo._direction == "SHORT" and bearish_energy < 2 and cooldown_ok:
            campo._direction = "LONG"
            campo._direction_last_change = now
            campo._direction_bearish_streak = 0
        
        # SHORT in RANGING: mantenuto se il drift è negativo
        # Non forzare LONG quando il mercato scende
        
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
                bearish_energy,
                regime=self._regime_current, direction=campo._direction,
                open_position=self._shadow is not None,
                active_threshold=getattr(campo, 'SOGLIA_MAX', 0),
                drift=drift, macd=macd_hist, trend=trend,
                volatility=getattr(self, '_last_volatility', 'UNKNOWN'))

    def _get_signal_tracker_context(self, regime: str, score: float) -> dict:
        """Legge il contesto rilevante dal Signal Tracker per il SuperCervello."""
        if score >= 75:   band = "FORTE_75+"
        elif score >= 65: band = "BUONO_65-75"
        elif score >= 58: band = "BASE_58-65"
        else:             band = "DEBOLE_<58"
        key = f"{regime}|LONG|{band}"
        stats = getattr(self.signal_tracker, '_stats', {}).get(key, {})
        hits = stats.get('hit_60', [])
        pnls = stats.get('pnl_sim', [])
        n = len(hits)
        return {
            'hit_rate': sum(hits)/n if n>0 else 0.5,
            'pnl_sim':  sum(pnls)/len(pnls) if pnls else 0.0,
            'n': n,
        }

    def _oracolo_interno_tick(self, price: float, momentum: str,
                              volatility: str, trend: str):
        """
        Oracolo predittivo interno — vive ogni tick grezzo.
        
        Calcola le feature sequenziali sul buffer prezzi reale,
        accumula la carica tick per tick, e genera narrativa
        che racconta il momento presente — non il passato.
        
        NON decide l'entry — prepara il terreno per _evaluate_shadow_entry.
        La narrativa viene esposta nel heartbeat per la dashboard.
        """
        now = time.time()

        # Feature sequenziali sul buffer prezzi reale
        pp = list(self.campo._prices_short)
        dd = []
        if len(self.campo._prices_long) >= 20:
            pl = list(self.campo._prices_long)[-20:]
            dd = [pl[i]-pl[i-1] for i in range(1, len(pl))]

        if len(pp) < 20 or len(dd) < 10:
            return

        r5  = max(pp[-5:])  - min(pp[-5:])
        r10 = max(pp[-10:]) - min(pp[-10:])
        r20 = max(pp[-20:]) - min(pp[-20:])

        if r20 == 0:
            return

        # L1: geometria
        pos     = (pp[-1] - min(pp[-20:])) / r20
        cr      = r5 / (r10 + 0.01)
        midzone = 0.40 <= pos <= 0.60
        bordo   = pos >= 0.80

        # L2: sequenza
        dp = sum(1 for d in dd[-10:] if d > 0) / 10
        sf = sum(1 for i in range(1, len(dd)) if dd[i]*dd[i-1] < 0)
        ds = sum(dd[-5:])/5 - sum(dd[-15:])/15 if len(dd) >= 15 else 0

        # Volume pressure dal seed scorer
        _sv = self.seed_scorer.score()
        # ════════════════════════════════════════════════════════════════
        # PATCH 1 (16mag2026) — VOL_PRESSURE OPZIONE B
        # PRIMA: vp = _sv.get('vol_accel', 0.5) + 1.0
        #   Bug pre-esistente: SeedScorer ritorna 'vol_pressure' NON 'vol_accel'.
        #   La chiave 'vol_accel' non esisteva → fallback 0.5 + 1.0 = 1.5 sempre.
        #   Risultato: vp >= 1.1 sempre True → +0.15 sempre alla carica OI.
        #   Spiega FUOCO costante.
        # ADESSO: chiave giusta + niente pezza +1.0 (era patch per fallback drogato).
        #   vp ora rappresenta davvero il rapporto vol5/vol15 (0.3-2.5 tipico).
        #   Soglia 1.1 sopravvive con significato reale: volume +10% su media.
        # ════════════════════════════════════════════════════════════════
        if _sv.get('reason') != 'insufficient_data':
            vp = _sv.get('vol_pressure', 0.5)
        else:
            vp = 0.5

        # ════════════════════════════════════════════════════════════════
        # PATCH 1 TELEMETRIA — distribuzione vp (passiva, no impatto)
        # ════════════════════════════════════════════════════════════════
        try:
            if vp < 0.8:
                self._tel_vp_distribution["vp_lt_0.8"] += 1
            elif vp < 1.1:
                self._tel_vp_distribution["vp_0.8_1.1"] += 1
            elif vp < 1.5:
                self._tel_vp_distribution["vp_1.1_1.5"] += 1
            else:
                self._tel_vp_distribution["vp_gte_1.5"] += 1
            self._tel_ticks_total += 1
        except Exception:
            pass

        # L3: memoria dal Signal Tracker
        _mem_hit = 0.5
        if hasattr(self, 'signal_tracker'):
            stats = getattr(self.signal_tracker, '_stats', {})
            regime_key = f"{self._regime_current}|LONG"
            for k, v in stats.items():
                if regime_key in k:
                    hits = v.get('hit_60', [])
                    if len(hits) >= 5:
                        _mem_hit = sum(hits) / len(hits)
                        break

        # Calcola nuova carica
        nc = 0.0
        if not midzone:
            if cr   < 0.80: nc += 0.20
            if bordo:        nc += 0.25
            if dp  >= 0.60: nc += 0.20
            if sf  <= 4:    nc += 0.15
            if vp  >= 1.1:  nc += 0.15
            if ds  >  0:    nc += 0.05
            if _mem_hit >= 0.65: nc = min(1.0, nc * 1.20)
        else:
            nc = 0.0  # midzone: carica si azzera

        self._oi_carica = self._oi_carica * 0.75 + nc * 0.25
        self._oi_carica_history.append(round(self._oi_carica, 3))
        if len(self._oi_carica_history) > 200: self._oi_carica_history.pop(0)

        # Stato macchina LONG
        vecchio_stato = self._oi_stato
        if midzone:
            self._oi_stato       = "ATTESA"
            self._oi_tick_pronto = 0
            self._oi_fuoco_ts    = 0
        elif self._oi_carica >= 0.65:
            self._oi_tick_pronto += 1
            if self._oi_tick_pronto >= 2:
                self._oi_stato   = "FUOCO"
                if not getattr(self, '_oi_fuoco_ts', 0):
                    self._oi_fuoco_ts = time.time()
            else:
                self._oi_stato   = "CARICA"
        elif self._oi_carica >= 0.40:
            # FUOCO sticky: se era FUOCO da meno di 30s e carica ancora > 0.40 → mantieni FUOCO
            _fuoco_age = time.time() - getattr(self, '_oi_fuoco_ts', 0)
            if getattr(self, '_oi_stato', '') == "FUOCO" and _fuoco_age < 30:
                pass  # mantieni FUOCO — finestra di 30s
            else:
                self._oi_stato       = "CARICA"
                self._oi_tick_pronto = 0
                self._oi_fuoco_ts    = 0
        else:
            self._oi_stato       = "ATTESA"
            self._oi_tick_pronto = 0
            self._oi_fuoco_ts    = 0
            self._oi_tick_pronto = 0

        # ── CARICA SHORT speculare ────────────────────────────────────
        # Stessa logica ma invertita: bordo inferiore, drift negativo
        if len(pp) >= 20:
            pos_short  = 1.0 - pos            # bordo inferiore
            dp_short   = 1.0 - dp             # drift persistente negativo
            ds_short   = -ds if ds < 0 else 0 # drift accelera verso il basso
            bordo_short = pos_short >= 0.80

            nc_short = 0.0
            if not midzone:
                if cr       < 0.80:   nc_short += 0.20
                if bordo_short:       nc_short += 0.25
                if dp_short >= 0.60:  nc_short += 0.20
                if sf       <= 4:     nc_short += 0.15
                if vp       >= 1.1:   nc_short += 0.15
                if ds_short > 0:      nc_short += 0.05

            self._oi_carica_short = self._oi_carica_short * 0.75 + nc_short * 0.25

            # Stato SHORT
            vecchio_short = self._oi_stato_short
            _carica_estrema_short = self._oi_carica_short >= 0.90
            if midzone and not _carica_estrema_short:
                # midzone azzera solo se carica < 0.90 — carica estrema override midzone
                self._oi_stato_short = "ATTESA"
                self._oi_tick_pronto_short = 0
            elif self._oi_carica_short >= 0.65:
                self._oi_tick_pronto_short += 1
                self._oi_stato_short = "FUOCO" if self._oi_tick_pronto_short >= 2 else "CARICA"
            elif self._oi_carica_short >= 0.40:
                self._oi_stato_short = "CARICA"
                self._oi_tick_pronto_short = 0
            else:
                self._oi_stato_short = "ATTESA"
                self._oi_tick_pronto_short = 0

            # Registra FUOCO SHORT nel Veritas
            if self._oi_stato_short == "FUOCO" and vecchio_short != "FUOCO":
                if hasattr(self, 'veritas'):
                    _p = list(self.campo._prices_short)[-1] if self.campo._prices_short else 0
                    self.veritas.registra(
                        price=_p,
                        oi_stato="FUOCO_SHORT",
                        oi_carica=self._oi_carica_short,
                        sc_decisione="BLOCCA",  # default blocca fino a verifica
                        sc_confidenza=0.5,
                        regime=self._regime_current,
                        ts=time.time()
                    )

        # VERITAS: registra ogni transizione a FUOCO o ogni CARICA >= 0.60
        # Non aspetta lo score — registra il segnale fisico e misura la verità a 60s
        if hasattr(self, 'veritas'):
            if self._oi_stato == "FUOCO" and vecchio_stato != "FUOCO":
                # Nuova transizione a FUOCO — registra immediatamente
                sc_dec = "SCONOSCIUTO"
                sc_conf = 0.0
                if hasattr(self, 'supercervello'):
                    # Stima decisione SC con dati correnti
                    sc_conf = self.supercervello._pesi.get('campo_carica', 0.2)
                    sc_dec = "PREVISTO_ENTRA" if self._oi_carica >= 0.70 else "PREVISTO_CARICA"
                self.veritas.registra(
                    price=price,
                    oi_stato=self._oi_stato,
                    oi_carica=self._oi_carica,
                    sc_decisione=sc_dec,
                    sc_confidenza=sc_conf,
                    regime=self._regime_current,
                    ts=time.time()
                )
            elif self._oi_stato == "CARICA" and self._oi_carica >= 0.55:
                # Carica alta — registra anche senza FUOCO completo
                if not hasattr(self, '_veritas_last_carica_ts'):
                    self._veritas_last_carica_ts = 0
                if time.time() - self._veritas_last_carica_ts >= 30:
                    self._veritas_last_carica_ts = time.time()
                    self.veritas.registra(
                        price=price,
                        oi_stato="CARICA",
                        oi_carica=self._oi_carica,
                        sc_decisione="ATTESA_SC",
                        sc_confidenza=0.0,
                        regime=self._regime_current,
                        ts=time.time()
                    )

        # Narrativa — aggiorna ogni 2 secondi max
        if now - self._oi_ultimo_log >= 2.0:
            self._oi_ultimo_log = now
            msg = self._oi_narrativa_tick(
                pos, cr, dp, sf, vp, ds, _mem_hit, midzone, vecchio_stato)
            if msg:
                self._oi_narrativa.append(f"{datetime.utcnow().strftime('%H:%M:%S')} {msg}")
                if len(self._oi_narrativa) > 20:
                    self._oi_narrativa.pop(0)

        # Registra nel Veritas ogni volta che l'Oracolo scatta FUOCO
        if self._oi_stato == "FUOCO" and vecchio_stato != "FUOCO":
            # Nuovo FUOCO — registra per verifica 60s
            sc_dec = getattr(self, '_last_sc_dec', None)
            sc_decisione = "ENTRA" if sc_dec and sc_dec.get('azione')=="ENTRA" else "BLOCCA"
            sc_conf = sc_dec.get('confidenza', 0.5) if sc_dec else 0.5
            if hasattr(self, 'veritas'):
                self.veritas.registra(
                    price=list(self.campo._prices_short)[-1] if self.campo._prices_short else 0,
                    oi_stato="FUOCO",
                    oi_carica=self._oi_carica,
                    sc_decisione=sc_decisione,
                    sc_confidenza=sc_conf,
                    regime=self._regime_current,
                    ts=time.time()
                )

        # Esponi nel heartbeat
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["oi_stato"]       = self._oi_stato
                self.heartbeat_data["oi_carica"]      = round(self._oi_carica, 3)
                self.heartbeat_data["oi_stato_short"] = self._oi_stato_short
                self.heartbeat_data["oi_carica_short"]= round(self._oi_carica_short, 3)
                self.heartbeat_data["oi_narrativa"] = self._oi_narrativa[-5:]
                # Storia prezzi e carica per grafico SC — ultimi 120 tick
                _ph = list(self.campo._prices_short)[-120:]
                self.heartbeat_data["sc_price_history"] = [round(p,2) for p in _ph]
                _ch = list(self._oi_carica_history)[-120:] if hasattr(self,'_oi_carica_history') else []
                self.heartbeat_data["sc_carica_history"] = _ch

                # Metriche predizione vs mercato reale
                if len(_ph) >= 10 and len(_ch) >= 10:
                    # Predizione dai delta reali del Veritas — non fattore inventato
                    # Usa il delta medio misurato per ogni livello di carica
                    _vt_stats = self.veritas._stats if hasattr(self.veritas, '_stats') else {}
                    _delta_fuoco  = 0.0
                    _delta_carica = 0.0
                    _n_fuoco = 0
                    for k, s in _vt_stats.items():
                        if 'FUOCO' in k and s.get('n', 0) >= 5:
                            deltas = s.get('deltas', [])
                            if deltas:
                                _delta_fuoco += sum(deltas) / len(deltas)
                                _n_fuoco += 1
                        elif 'CARICA' in k and s.get('n', 0) >= 5:
                            deltas = s.get('deltas', [])
                            if deltas:
                                _delta_carica += sum(deltas) / len(deltas)
                    if _n_fuoco > 0:
                        _delta_fuoco /= _n_fuoco
                    # Fallback se Veritas non ha ancora dati sufficienti
                    if _delta_fuoco == 0 and _ph:
                        _vt_closed = self.veritas._closed[-200:] if self.veritas._closed else []
                        _fuoco_d = [s['delta_60'] for s in _vt_closed if s.get('oi_carica',0)>=0.65 and 'delta_60' in s]
                        _carica_d = [s['delta_60'] for s in _vt_closed if 0.40<=s.get('oi_carica',0)<0.65 and 'delta_60' in s]
                        if len(_fuoco_d) >= 3: _delta_fuoco = sum(_fuoco_d) / len(_fuoco_d)
                        if len(_carica_d) >= 3: _delta_carica = sum(_carica_d) / len(_carica_d)
                    # Predizione: prezzo + delta atteso in base alla carica
                    preds = []
                    for i in range(min(len(_ph), len(_ch))):
                        c = _ch[i]
                        _price_ref = _ph[i] if _ph[i] > 0 else 100.0
                        if c >= 0.65:
                            delta = _delta_fuoco if _delta_fuoco != 0 else 0.0
                        elif c >= 0.40:
                            delta = _delta_carica if _delta_carica != 0 else 0.0
                        else:
                            delta = 0.0
                        preds.append(round(_ph[i] + delta, 2))
                    # Scostamento medio assoluto
                    scost = [abs(preds[i] - _ph[i]) for i in range(len(preds))]
                    scost_avg = round(sum(scost) / len(scost), 2)
                    # Conferme: predizione indicava direzione giusta?
                    conferme = 0
                    totale = 0
                    # Soglia = 10% del delta medio della predizione
                    # Se _delta_fuoco=0 (Veritas non ancora pronto) → soglia basata su movimento tick
                    _pred_range = max(abs(preds[i] - preds[i-1]) for i in range(1, len(preds))) if len(preds) > 1 else 0
                    _sig_threshold = max(_pred_range * 0.10, _ph[0] * 0.00005) if _ph and _pred_range > 0 else max(0.05, _ph[0] * 0.00005) if _ph else 0.05
                    for i in range(1, len(preds)):
                        dir_pred   = preds[i] - preds[i-1]
                        dir_reale  = _ph[i]   - _ph[i-1]
                        if abs(dir_pred) > _sig_threshold:    # solo segnali significativi
                            totale += 1
                            if (dir_pred > 0) == (dir_reale > 0):
                                conferme += 1
                    conf_pct = round(conferme / totale * 100, 1) if totale > 0 else 0

                    # ════════════════════════════════════════════════════════════
                    # PASSO 14 (15mag2026) — DASHBOARD ONESTA
                    # Le card SCOSTAMENTO/CONFERMATE/SCORE PRED ora usano i valori
                    # del PredittoreContestuale V2 (misurato vs realtà 60s) quando
                    # ha raccolto ≥30 verificate. Prima di allora restano i valori
                    # tautologici (fallback cold start) per non mostrare card vuote.
                    # ════════════════════════════════════════════════════════════
                    _pv2_ready = False
                    _pv2_score = conf_pct       # default cold = tautologico
                    _pv2_scost = scost_avg
                    _pv2_conf  = conferme
                    _pv2_tot   = totale
                    try:
                        _pv2_stats = self.predittore_v2.get_stats()
                        _pv2_nver  = _pv2_stats.get('pred_v2_n_verificate', 0)
                        if _pv2_nver >= 30:
                            _pv2_ready = True
                            _pv2_score = _pv2_stats.get('pred_v2_accuracy_segno', conf_pct)
                            _pv2_scost = _pv2_stats.get('pred_v2_err_medio', scost_avg)
                            _pv2_conf  = sum(self.predittore_v2._segno_giusti) \
                                         if hasattr(self.predittore_v2, '_segno_giusti') else 0
                            _pv2_tot   = _pv2_stats.get('pred_v2_n_segno', 0)
                    except Exception as _e_pv2_card:
                        log.debug(f"[PRED_V2_CARD_ERR] {_e_pv2_card}")
                    self.heartbeat_data["pred_scostamento"] = _pv2_scost
                    self.heartbeat_data["pred_conferme"]    = _pv2_conf
                    self.heartbeat_data["pred_totale"]      = _pv2_tot
                    # PATCH 0 — pred_score nella dashboard SOLO se qualified.
                    # Altrimenti: None + label testuale. Mai numero senza source.
                    _pred_st_hb = self.supercervello._get_pred_state()
                    if _pred_st_hb["qualified"]:
                        self.heartbeat_data["pred_score"] = _pv2_score
                        self.heartbeat_data["pred_status_label"] = f"QUALIFICATA · {_pv2_score:.0f}%"
                    elif _pred_st_hb["source"] == "V2_OBSERVING":
                        self.heartbeat_data["pred_score"] = None
                        self.heartbeat_data["pred_status_label"] = f"OSSERVAZIONE · n={_pv2_tot}"
                    elif _pred_st_hb["source"] == "BOOT_MUTED":
                        self.heartbeat_data["pred_score"] = None
                        self.heartbeat_data["pred_status_label"] = "MUTA · BOOT"
                    else:
                        self.heartbeat_data["pred_score"] = None
                        self.heartbeat_data["pred_status_label"] = "MUTA"
                    self.heartbeat_data["pred_source"] = _pred_st_hb["source"]
                    self.heartbeat_data["pred_qualified"] = _pred_st_hb["qualified"]
                    self.heartbeat_data["pred_v2_ready"]    = _pv2_ready
                    self.heartbeat_data["pred_trade_n"]     = self._pred_trade_n
                    self.heartbeat_data["pred_trade_pnl"]   = round(self._pred_trade_pnl, 2)
                    self.heartbeat_data["pred_delta_fuoco"]  = round(_delta_fuoco, 4)
                    self.heartbeat_data["pred_delta_carica"] = round(_delta_carica, 4)

                    # ════════════════════════════════════════════════════════════════
                    # PATCH 1 TELEMETRIA — distribuzioni esposte (read-only)
                    # ════════════════════════════════════════════════════════════════
                    self.heartbeat_data["tel_ticks_total"] = self._tel_ticks_total
                    self.heartbeat_data["tel_vol_distribution"] = dict(self._tel_vol_distribution)
                    self.heartbeat_data["tel_vp_distribution"] = dict(self._tel_vp_distribution)
                    self.heartbeat_data["tel_oi_stato_distribution"] = dict(self._tel_oi_stato_distribution)

                    # ════════════════════════════════════════════════════════════
                    # PASSO 12 (15mag2026) — LENZA visibile sulla dashboard
                    # Il pescatore pianta la lenza dove passerà il pesce a 60s.
                    # delta_fuoco = movimento medio osservato a 60s quando OI=FUOCO
                    # lenza_long  = dove arriverà il prezzo se sale (FUOCO LONG)
                    # lenza_short = dove arriverà se scende (FUOCO SHORT)
                    # Espone anche QUALE lenza è attiva (FUOCO da che lato).
                    # ════════════════════════════════════════════════════════════
                    _prezzo_attuale = _ph[-1] if _ph else 0
                    _delta_pred = _delta_fuoco if _delta_fuoco != 0 else _delta_carica
                    _delta_abs = abs(_delta_pred)
                    self.heartbeat_data["lenza_long"]    = round(_prezzo_attuale + _delta_abs, 2)
                    self.heartbeat_data["lenza_short"]   = round(_prezzo_attuale - _delta_abs, 2)
                    self.heartbeat_data["lenza_delta"]   = round(_delta_abs, 2)
                    # Quale lenza è ATTIVA (FUOCO acceso da quel lato + predizione certificata)
                    # PATCH 0: SOLO se _pred["qualified"]. Senza qualifica → "NONE_PRED_NON_QUALIFICATA"
                    _pred_st_lenza = self.supercervello._get_pred_state()
                    _ps_now = _pred_st_lenza["score"]
                    _pc_now = _pred_st_lenza["calib"]
                    _pred_ok = _pred_st_lenza["qualified"] and _ps_now >= 70 and _pc_now >= 50
                    _lenza_attiva = "NONE"
                    if _pred_ok:
                        if self._oi_stato_short == "FUOCO" and self._oi_carica_short >= 0.75:
                            _lenza_attiva = "SHORT"
                        elif self._oi_stato == "FUOCO" and self._oi_carica >= 0.75:
                            _lenza_attiva = "LONG"
                    elif _pred_st_lenza["source"] in ("BOOT_MUTED", "V2_OBSERVING"):
                        _lenza_attiva = "NONE_PRED_NON_QUALIFICATA"
                    self.heartbeat_data["lenza_attiva"]  = _lenza_attiva

                    # ════════════════════════════════════════════════════════════
                    # PASSO 13 — PREDITTORE V2 ONESTO (statistiche misurate)
                    # ════════════════════════════════════════════════════════════
                    try:
                        _pv2 = self.predittore_v2.get_stats()
                        for _k, _v in _pv2.items():
                            self.heartbeat_data[_k] = _v
                    except Exception as _e_pv2:
                        log.debug(f"[PRED_V2_HB_ERR] {_e_pv2}")

                    # ════════════════════════════════════════════════════════════
                    # PASSO 15.B — LIBRO DI PESCA (stats per diagnosi)
                    # Le stats vanno nell'heartbeat. UI nuova arriverà in MICRO 15.C.
                    # ════════════════════════════════════════════════════════════
                    try:
                        if self.libro_pesca is not None:
                            _lp = self.libro_pesca.get_stats(time.time())
                            for _k, _v in _lp.items():
                                self.heartbeat_data[_k] = _v
                            # PASSO 15.C — eventi recenti per il grafico palle colorate
                            self.heartbeat_data["lp_eventi_recenti"] = \
                                self.libro_pesca.get_recent_events(50)
                    except Exception as _e_lp_hb:
                        log.debug(f"[LIBRO_PESCA_HB_ERR] {_e_lp_hb}")

                    # Ratio magnitudine: predizione vs movimento reale
                    # Misura quanto la predizione sovra/sottostima il mercato
                    movimenti_pred  = []
                    movimenti_reali = []
                    # Soglia adattiva — proporzionale al movimento reale, non al prezzo assoluto
                    _dp_thresh_mag = max(0.01, (_ph[0] * 0.00003)) if _ph else 0.01
                    for i in range(1, min(len(preds), len(_ph))):
                        dp = preds[i] - preds[i-1]
                        dr = _ph[i]   - _ph[i-1]
                        if abs(dp) > _dp_thresh_mag and abs(dr) > 0.001:
                            movimenti_pred.append(abs(dp))
                            movimenti_reali.append(abs(dr))

                    if movimenti_pred and movimenti_reali:
                        avg_pred  = sum(movimenti_pred)  / len(movimenti_pred)
                        avg_reale = sum(movimenti_reali) / len(movimenti_reali)
                        ratio = round(avg_reale / avg_pred * 100, 1) if avg_pred > 0 else 100.0
                        # Fattore di correzione — usato per calibrare la magnitudine
                        # < 100% = predizione troppo aggressiva
                        # > 100% = predizione troppo conservativa
                        # 100% = perfettamente calibrata
                        if not hasattr(self, '_pred_ratio_history'):
                            self._pred_ratio_history = []
                        self._pred_ratio_history.append(ratio)
                        if len(self._pred_ratio_history) > 50:
                            self._pred_ratio_history.pop(0)
                        ratio_smooth_taut = round(
                            sum(self._pred_ratio_history) / len(self._pred_ratio_history), 1)

                        # ════════════════════════════════════════════════════════════
                        # PASSO 14 — CALIBRAZIONE ONESTA DAL V2
                        # Se V2 maturo: usa rapporto medio |delta_predetti|/|delta_reali|
                        # misurato contro la realtà 60s. Altrimenti fallback tautologico.
                        # ════════════════════════════════════════════════════════════
                        ratio_v2 = ratio_smooth_taut
                        try:
                            _pv2_dp = getattr(self.predittore_v2, '_delta_predetti', None)
                            _pv2_dr = getattr(self.predittore_v2, '_delta_reali', None)
                            if _pv2_dp and _pv2_dr and len(_pv2_dr) >= 30:
                                _ap = sum(abs(x) for x in _pv2_dp) / len(_pv2_dp)
                                _ar = sum(abs(x) for x in _pv2_dr) / len(_pv2_dr)
                                if _ap > 0:
                                    ratio_v2 = round(_ar / _ap * 100, 1)
                        except Exception as _e_rv2:
                            log.debug(f"[PRED_V2_RATIO_ERR] {_e_rv2}")

                        self.heartbeat_data["pred_ratio"]      = ratio_v2
                        self.heartbeat_data["pred_ratio_raw"]  = ratio
                        self.heartbeat_data["pred_ratio_taut"] = ratio_smooth_taut

                        # PASSO 14 — _pred_score_ref / _pred_calib_ref dal V2 solo se maturo
                        # PATCH 0: V2 può OSSERVARE ma NON GOVERNARE finché non c'è qualifica
                        # per cella (PATCH 3-4). Anche con n>=30 e accuracy alta, decision_enabled
                        # resta False. Il valore è conservato per dashboard ma il SC non lo legge
                        # come predizione qualificata.
                        if hasattr(self, 'supercervello'):
                            if _pv2_ready:
                                self.supercervello._pred_score_ref = _pv2_score
                                self.supercervello._pred_calib_ref = ratio_v2
                                self.supercervello._pred_source = "V2_OBSERVING"
                                # NON: _pred_decision_enabled = True
                                # → qualifica vera arriva in PATCH 3-4 (cella SQLite n>=200, acc>=58%)
                                self.supercervello._pred_decision_enabled = False
                            self.supercervello._veritas_stats_ref = self.veritas._stats

                # FIX: _ctx aggiornato SEMPRE ad ogni tick dell'Oracolo Interno,
                # indipendentemente da movimenti_pred/supercervello.
                # drift calcolato realmente da _prices_long (non _last_drift che non esiste).
                _ia_drift_ctx = 0.0
                if len(self.campo._prices_long) >= 100:
                    _p_ctx = list(self.campo._prices_long)
                    _avg_old_ctx = sum(_p_ctx[:50]) / 50
                    _avg_new_ctx = sum(_p_ctx[-50:]) / 50
                    if _avg_old_ctx > 0:
                        _ia_drift_ctx = (_avg_new_ctx - _avg_old_ctx) / _avg_old_ctx * 100
                self.realtime_engine._ctx = {
                    'sc_pesi': self.supercervello._pesi.copy() if hasattr(self, 'supercervello') else {},
                    'oi_carica': self._oi_carica,
                    'oi_stato': self._oi_stato,
                    'drift': round(_ia_drift_ctx, 5),
                    'macd_hist': self.campo._last_macd_hist,
                    'regime': self._regime_current,
                    'signal_tracker_stats': self.signal_tracker._stats,
                    'veritas_stats': self.veritas._stats,
                    'phantom_stats': self._phantom_stats,
                }
                # Pesi SuperCervello
                if hasattr(self,'supercervello'):
                    self.heartbeat_data["sc_pesi"] = self.supercervello._pesi
                # Veritas dashboard
                self.heartbeat_data["veritas"] = self.veritas.dump_dashboard()
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def _oi_narrativa_tick(self, pos, cr, dp, sf, vp, ds,
                            mem_hit, midzone, vecchio_stato) -> str:
        """Genera narrativa che racconta il momento fisico presente."""
        carica = self._oi_carica
        stato  = self._oi_stato

        # Transizioni di stato — eventi importanti
        if vecchio_stato != stato:
            if stato == "FUOCO":
                return f"🚀 FUOCO — carica {carica:.2f} confermata. Bordo {pos:.0%}, compressione {cr:.2f}, drift persistente {dp:.0%}"
            elif stato == "CARICA" and vecchio_stato == "ATTESA":
                return f"⚡ Carica {carica:.2f} — molla si carica"
            elif stato == "ATTESA" and vecchio_stato in ("CARICA","FUOCO"):
                return f"💤 Energia cade {carica:.2f} — aspetto"

        # Narrativa continua per stato CARICA
        if stato == "CARICA" and carica >= 0.50:
            parts = []
            if cr < 0.70:    parts.append(f"compressione {cr:.2f}")
            if dp >= 0.70:   parts.append(f"drift {dp:.0%}")
            if vp >= 1.2:    parts.append(f"volume +{(vp-1)*100:.0f}%")
            if mem_hit>=0.65: parts.append(f"memoria {mem_hit:.0%}")
            if parts:
                return f"⚡ Carica {carica:.2f} — {', '.join(parts)}"

        # Midzone
        if midzone and vecchio_stato != "ATTESA":
            return f"🚫 Midzone pos={pos:.0%} — zero trade"

        # FUOCO attivo — segue la posizione
        if stato == "FUOCO":
            return f"🔥 Carica {carica:.2f} — energia viva"

        return ""

    def _evaluate_shadow_entry(self, price, momentum, volatility, trend):
        """
        MOTORE ENTRY V16 — SCENA COSTITUZIONALE A 4 ZONE (Passo 5a, 14mag2026)
        ════════════════════════════════════════════════════════════════════
        AUDIT ROBERTO V1 / SC SOVRANO — riforma costituzionale del flow.

        Le 4 zone:
          ZONA 1 — Filtro fisico: i 6 veti legittimi restano return.
          ZONA 2 — Deposizioni: ogni organo depone nel verbale _collected_inputs.
                   Nessun return, nessun break, nessuna modifica di stato.
          ZONA 3 — SC esamina: riceve il verbale completo e conclude.
          ZONA 4 — La Regina esegue: solo dopo SC.

        MODO 2 per PERCORSO 1 (EXPLOSIVE) — decisione capoprogetto:
          In 5a SC viene CHIAMATO anche per gli EXPLOSIVE, logga cosa vede e
          cosa deciderebbe (SC_INPUTS_FULL / SC_DECISION_FINAL), MA per gli
          EXPLOSIVE la sua decisione è solo OSSERVATA — l'EXPLOSIVE entra
          come oggi. Il potere pieno a SC sugli EXPLOSIVE arriva in 5b, dopo
          aver letto i log reali.

        5a — SC decide con la LOGICA DI OGGI. I nuovi input li riceve e li
        logga, non li usa ancora. L'attivazione è 5b.
        ════════════════════════════════════════════════════════════════════
        """
        # ── IL VERBALE COSTITUZIONALE ────────────────────────────────────────
        # Si popola durante la ZONA 2. Ogni organo depone qui. SC lo legge.
        _verbale = {
            "tick_ts":          time.time(),
            "tick_price":       price,
            "momentum":         momentum,
            "volatility":       volatility,
            "trend":            trend,
            # FIX TRACCIATURA (31mag): direction mancava del tutto nel verbale
            # → la fingerprint canvas usciva "?|?|?|?" senza direzione. Uso lo
            # stato del campo, sempre disponibile (LONG o SHORT). Copre P1 e P2.
            "direction":        self.campo._direction,
            # TSUNAMI (statuto Passo 3 — tsunami.vota())
            "tsunami_vote":         None,
            "tsunami_confidence":   None,
            "tsunami_direction":    None,
            "tsunami_reason":       None,
            "tsunami_size_mult":    None,
            "tsunami_discorde":     False,
            # FLIP proposto (era modifica di stato, ora proposta)
            "proposed_direction":   None,
            "flip_confidence":      None,
            "flip_reason":          None,
            # ORACOLO
            "fp_wr":                None,
            "fp_wr_opposite":       None,
            "fp_samples":           None,
            # VERITAS (statuto Passo 2 — get_ctx_stats())
            "veritas_ctx_wr":       None,
            "veritas_ctx_samples":  None,
            "veritas_ctx_pnl_avg":  None,
            "veritas_last_judgement": None,
            # CAPSULE (statuto Passo 1 — consulta())
            "capsule_block_score":  0.0,
            "capsule_boost_score":  0.0,
            "capsule_threshold_delta": 0.0,
            "capsule_size_delta":   1.0,
            "capsule_reasons":      [],
            "capsule_oracolo_override": False,
            # BREATH — popolato subito (era None → null nel canvas, vedi fix sensori sopra)
            "breath_fase":          (self._breath._fase    if getattr(self, '_breath', None) else None),
            "breath_energia":       (self._breath._energia if getattr(self, '_breath', None) else None),
            # CONTESTO BASE
            # FIX TRACCIATURA (31mag): era None alla nascita e veniva riempito
            # DOPO la chiamata a observe_entry → la snapshot registrava regime
            # mancante. Popolato subito col regime corrente.
            "regime":               self._regime_current,
            # FIX TRACCIATURA SENSORI (2giu, Roberto — "l'indizio prima del seme"):
            # PRIMA: oi_carica/breath/fp_wr nascevano None e venivano riempiti DOPO
            # la chiamata a observe_entry (righe ~10823) → il canvas registrava i
            # sensori del campo TUTTI null. L'indizio pre-nascita (carica, respiro,
            # energia del campo nell'istante della valutazione) veniva BUTTATO.
            # ADESSO: popolati SUBITO coi valori che il bot già ha in mano, così la
            # scatola nera (canvas_snapshots.sensori_json) registra lo stato del campo
            # PRIMA che il trade nasca. È il dato da cui cercare il segnale subdolo:
            # ciò che distingue chi nascerà femmina-e-resta da chi diventerà maschio.
            "oi_carica":            getattr(self, '_oi_carica', None),
            "oi_stato":             getattr(self, '_oi_stato', None),
            "oi_carica_short":      getattr(self, '_oi_carica_short', None),
            "score":                None,
            "soglia":               None,
            # CAMPO GRAVITAZIONALE (Passo 5a-bis — 5° testimone)
            "campo_veto":           None,
            "score_ricostruito":    False,
            # ESITO (per il log finale)
            "percorso":             None,   # 'P1_EXPLOSIVE' | 'P2_NORMALE'
            "blocked_by":           None,
        }

        # ════════════════════════════════════════════════════════════════
        # HOOK CANVAS ANTICIPATO (2giu, Roberto — "la signora respira ma è cieca")
        # ════════════════════════════════════════════════════════════════
        # Il vecchio hook (più sotto, ~10810) era DOPO tutti i veti/return. Ma
        # il guardiano FP_TOSSICO blocca col `return` PRIMA di arrivarci → il
        # canvas vedeva SOLO i trade che passavano il guardiano (quasi nessuno).
        # 54 valutazioni bloccate, canvas=0. La signora respira (phantom scrive)
        # ma è cieca (canvas vuoto).
        # FIX: registro QUI, subito dopo la nascita del verbale, PRIMA di ogni
        # veto. Il verbale ha già i sensori del campo (oi_carica, breath, regime,
        # momentum, volatility) = lo stato PRE-DECISIONE, che è esattamente il
        # "prima del seme" che cerchiamo. Score/voti sono il "durante", non
        # servono qui. Così il canvas vede OGNI valutazione — anche (soprattutto)
        # quelle che il guardiano blocca, dove si nascondono i maschi-lingotti.
        # Try/except totale: mai blocca il motore.
        # ════════════════════════════════════════════════════════════════
        try:
            if getattr(self, "canvas", None) is not None:
                # PRIMA DEL SEME (2giu, Roberto): l'hook canvas è anticipato,
                # ma range_pos/drift_slope (vita dell'energia) si calcolano più
                # in basso. Li anticipo qui così l'occhio li registra alla
                # nascita. Calcolo leggero e già usato altrove, nessun effetto
                # sulle decisioni: solo per riempire il verbale dell'occhio.
                try:
                    _seed_early = self.seed_scorer.score()
                    _verbale["range_pos"]   = _seed_early.get("range_pos")
                    _verbale["drift_slope"] = _seed_early.get("drift_slope")
                    _verbale["seed_score"]  = _seed_early.get("score")
                except Exception:
                    pass
                _canvas_tid = f"t_{int(time.time()*1000)}"
                _verbale["_canvas_tid"] = _canvas_tid
                # DISATTIVATO 4giu (Roberto): observe_entry scriveva ~60k
                # valutazioni/giorno → lock DB → EXIT perse (contatore che
                # perdeva trade). _canvas_tid resta per agganciare nascita→esito.
                # self.canvas.observe_entry(_verbale, trade_id=_canvas_tid)
        except Exception as _ce_early:
            log.debug(f"[CANVAS_HOOK_EARLY_ERR] {_ce_early}")

        # ════════════════════════════════════════════════════════════════
        # CAPSULA REGIME-EDGE (31mag) — consulta. In L3 (default) OSSERVA e
        # registra, ritorna None → non tocca nulla. In L4 (armata) può
        # bloccare il SIDEWAYS-merda. Fail-open: se assente o crasha, prosegue.
        # ════════════════════════════════════════════════════════════════
        if getattr(self, 'cap_regime_edge', None):
            try:
                _re_verdetto = self.cap_regime_edge.consulta(
                    regime=self._regime_current,
                    momentum=momentum,
                    trend=trend,
                    direction=self.campo._direction,
                )
                if _re_verdetto and _re_verdetto[0] == "BLOCCA":
                    self._log_m2("🧭", f"REGIME_EDGE BLOCCA: {_re_verdetto[1]}")
                    return
            except Exception as _e_re_consulta:
                pass  # fail-open: la capsula non deve mai fermare il bot per un errore

        try:
            # ════════════════════════════════════════════════════════════════
            # ZONA 1 — FILTRO FISICO ALL'INGRESSO
            # I 6 veti legittimi. Restano return. Sono il nastro della polizia:
            # non sono opinioni, sono "non c'è la scena da esaminare".
            # ════════════════════════════════════════════════════════════════

            # ── ANTI-DUPLICATE (veto fisico 1) ─────────────────────────────
            _now_tick = round(time.time(), 1)
            if getattr(self, '_last_entry_tick', 0) == _now_tick:
                self._log_m2("🔇", "ANTI_DUPLICATE tick")
                _verbale["blocked_by"] = "ZONA1_ANTI_DUPLICATE"
                self._log_constitutional(_verbale, "PRE_SC_VETO_ANTI_DUPLICATE")
                return
            self._last_entry_tick = _now_tick

            # ════════════════════════════════════════════════════════════════
            # PATCH 18 — CAPSULA FASE (25mag2026)
            # Veto fisico 1.5: consulta la capsula fase.
            # 
            # In modalità OBSERVER (L4 DECIDE OFF, default):
            #   - la capsula registra il suo verdetto (BLOCCA/PASSA)
            #   - ma NON blocca il trade (lascia decidere il sistema vecchio)
            #   - serve per raccogliere dati e validare la regola
            #
            # In modalità BLOCCANTE (L4 DECIDE ON):
            #   - la capsula blocca davvero se verdetto = BLOCCA
            #   - usata SOLO dopo che L3 ha dimostrato precisione ≥70%
            # ════════════════════════════════════════════════════════════════
            if self.capsula_fase is not None:
                try:
                    _cf_dir = getattr(self.campo, '_direction', 'LONG')
                    _cf_tid = f"trade_{int(_now_tick * 10)}"
                    _cf_ok, _cf_motivo = self.capsula_fase.consulta(direction=_cf_dir, trade_id=_cf_tid)
                    _verbale["capsula_fase_motivo"] = _cf_motivo
                    _verbale["capsula_fase_blocking"] = self.capsula_fase.is_blocking()
                    if not _cf_ok and self.capsula_fase.is_blocking():
                        self._log_m2("🌊", f"CAPSULA_FASE BLOCCA: {_cf_motivo}")
                        _verbale["blocked_by"] = f"ZONA1_CAPSULA_FASE:{_cf_motivo}"
                        self._log_constitutional(_verbale, "PRE_SC_VETO_CAPSULA_FASE")
                        return
                    elif not _cf_ok:
                        # L3 OBSERVER: logga ma non blocca
                        self._log_m2("👁", f"CAPSULA_FASE suggerirebbe blocco (OBSERVER): {_cf_motivo}")
                except Exception as _e_cf:
                    # Fail-open totale: mai bloccare per errore della capsula
                    log.debug(f"[CAPSULA_FASE_ERR_CONSULTA] {_e_cf}")

            # ── STATE ENGINE (veto fisico 2) ───────────────────────────────
            can_enter, gate_reason = self._state_engine_can_enter()
            if not can_enter:
                self._log_m2("🔇", f"STATE_ENGINE: {gate_reason}")
                _verbale["blocked_by"] = f"ZONA1_STATE_ENGINE:{gate_reason}"
                self._log_constitutional(_verbale, "PRE_SC_VETO_STATE_ENGINE")
                return

            # ── SEED (veto fisico 3) ───────────────────────────────────────
            seed = self.seed_scorer.score()
            if seed.get('reason') == 'insufficient_data':
                self._log_m2("🔇", f"SEED_INSUFFICIENTE score={seed.get('score',0):.2f}")
                _verbale["blocked_by"] = "ZONA1_SEED_INSUFFICIENT"
                self._log_constitutional(_verbale, "PRE_SC_VETO_SEED_INSUFFICIENT")
                return

            # ── PRIMA DEL SEME: vita dell'energia (2giu, Roberto) ───────────
            # Intuizione: il bot misura QUANTA energia c'è (livello) ma non se
            # quell'energia è VIVA (sale) o MORTA (picco che cola). range_pos
            # alto + drift_slope negativo = comprato sul picco morto = femmina.
            # Registriamo questi due nel verbale così il canvas (l'occhio) li
            # salva alla nascita. Domani confrontiamo: le femmine nascono con
            # slope<0 (energia morta) e i maschi con slope>0 (energia viva)?
            # NON cambia nessuna decisione: aggiunge solo l'elemento oggettivo
            # che mancava per provare/smentire l'intuizione sui dati.
            _verbale["range_pos"]   = seed.get("range_pos")
            _verbale["drift_slope"] = seed.get("drift_slope")
            _verbale["seed_score"]  = seed.get("score")

            # ════════════════════════════════════════════════════════════════
            # PATCH 9 BUG 16 — Disable Operational BYPASS_ORACOLO
            # ════════════════════════════════════════════════════════════════
            # Background: oltre a BYPASS_MAGNITUDE_v2 (neutralizzato in
            # PATCH 7), esiste un secondo bypass operativo: BYPASS_ORACOLO_v1.
            # Quando il fingerprint storico ha _pre_samples >= 20 e
            # _pre_wr >= 0.65, settava _bypass_oracolo = True, disinnescando
            # TSUNAMI_NO_ENTRY (riga ~10059).
            #
            # Diagnosi di Roberto (18 maggio 2026 mattina, alle 07:05):
            # 3 ZONA_MORTA consecutive in 2 minuti con PATCH 7 già live.
            # "qualcosa di vecchio è rientrato nel flusso operativo".
            # Conferma: la memoria Oracolo contiene fingerprint storici
            # pre-PATCH 3 quando molti WIN erano WIN_+1 finti o classificati
            # male sotto fee. Quei WR storici falsi autorizzavano oggi
            # entry bypassando Tsunami → ZONA_MORTA su mercato fermo.
            #
            # Fix PATCH 9:
            #  - BYPASS_ORACOLO_OPERATIVO = False (flag, default OFF)
            #  - Il calcolo Oracolo (fingerprint, WR, samples) resta intatto
            #  - _bypass_oracolo_observed = True quando WR>=65% e n>=20
            #  - _bypass_oracolo (variabile operativa) resta False
            #  - TSUNAMI_NO_ENTRY non viene più bypassato dall'Oracolo
            #  - La memoria storica contaminata pre-PATCH non ha più
            #    diritto operativo
            #
            # Cosa NON tocchiamo:
            #  - Oracolo come oggetto (memoria, _fp, _memory): integro
            #  - Logging diagnostico FIX #31b: integro
            #  - Voto Oracolo nelle deposizioni SC: integro
            #
            # In futuro: si potrà reintrodurre BYPASS_ORACOLO con memoria
            # pulita SOLO post-PATCH 3/4/5/6/7/8/9, dopo aver invalidato
            # le voci contaminate dai WIN_+1 finti.
            # ════════════════════════════════════════════════════════════════
            BYPASS_ORACOLO_OPERATIVO = False  # PATCH 9: flag OFF di default

            # ── PREFETCH ORACOLO (calcolo invariato, USO ridotto a osservazione)
            _bypass_oracolo = False             # OPERATIVO: resta False
            _bypass_oracolo_observed = False    # OSSERVATIVO: nuovo
            _bypass_oracolo_dir = None
            _bypass_oracolo_wr = 0.0
            _bypass_oracolo_n = 0
            _pre_fp_key = None
            _pre_wr_dbg = 0.0
            _pre_n_dbg = 0
            try:
                _pre_dir = self.campo._direction
                _pre_fp_key = self.oracolo._fp(momentum, volatility, trend, _pre_dir)
                _pre_fp_data = self.oracolo._memory.get(_pre_fp_key, None)
                if _pre_fp_data:
                    _pre_samples = float(_pre_fp_data.get('samples', 0))
                    _pre_wins    = float(_pre_fp_data.get('wins', 0))
                    _pre_n_dbg = int(_pre_samples)
                    if _pre_samples > 0:
                        _pre_wr_dbg = _pre_wins / _pre_samples
                    if _pre_samples >= 20:
                        _pre_wr = _pre_wins / _pre_samples
                        if _pre_wr >= 0.65:
                            _bypass_oracolo_observed = True
                            _bypass_oracolo_dir = _pre_dir
                            _bypass_oracolo_wr = _pre_wr
                            _bypass_oracolo_n = int(_pre_samples)
                            # PATCH 9: setta _bypass_oracolo operativo SOLO se flag attivo
                            if BYPASS_ORACOLO_OPERATIVO:
                                _bypass_oracolo = True
                                self._log_m2("📜", f"BYPASS_ORACOLO_v1: fp={_pre_fp_key} "
                                                   f"WR={_pre_wr:.0%} n={int(_pre_samples)} "
                                                   f"→ Tsunami disinnescato")
                            else:
                                # PATCH 9: solo osservativo. Non autorizza entry.
                                self._log_m2("👁️", f"BYPASS_ORACOLO_OBSERVED: fp={_pre_fp_key} "
                                                   f"WR={_pre_wr:.0%} n={int(_pre_samples)} "
                                                   f"reason=BYPASS_OPERATIVO_OFF_PATCH9")
            except Exception as _e_bo:
                pass

            # FIX #31b (12mag2026 sera): DIAGNOSTIC LOG CAMBIO FINGERPRINT
            # Logga UNA VOLTA SOLA quando il fingerprint corrente cambia, così 
            # si vede esattamente cosa il bot incontra senza spammare i log.
            # Usato per capire perché BYPASS_ORACOLO_v1 non scatta.
            try:
                _last_fp_seen = getattr(self, '_last_fp_diagnostic', None)
                if _pre_fp_key and _pre_fp_key != _last_fp_seen:
                    self._last_fp_diagnostic = _pre_fp_key
                    _whitelist_status = "WHITELIST ✓" if _bypass_oracolo else (
                        f"no-whitelist (n={_pre_n_dbg}, WR={_pre_wr_dbg:.0%})" 
                        if _pre_n_dbg > 0 else "fp NON in memoria"
                    )
                    self._log_m2("🔍", f"FP_NUOVO: {_pre_fp_key} → {_whitelist_status}")
            except Exception:
                pass

            # ════════════════════════════════════════════════════════════════
            # ════════════════════════════════════════════════════════════════
            # ZONA 2 — DEPOSIZIONE TSUNAMI (statuto Passo 3)
            # ════════════════════════════════════════════════════════════════
            # Tsunami NON è più un dittatore. NON fa return. NON modifica lo
            # stato del campo. Depone il suo voto nel verbale. SC deciderà.
            # La logica di calcolo (bypass magnitude, coerenza direzione,
            # size_mult) resta — cambia solo che il risultato va nel VERBALE
            # invece di fare return.
            # ════════════════════════════════════════════════════════════════
            if self.tsunami is not None:
                _ts_decision = self.tsunami.evaluate()
                _campo_dir = self.campo._direction  # LONG o SHORT corrente

                # Deposizione Tsunami nel verbale (statuto: tsunami.vota())
                try:
                    _ts_voto = self.tsunami.vota()
                    _verbale["tsunami_vote"]       = _ts_voto.get("tsunami_vote")
                    _verbale["tsunami_confidence"] = _ts_voto.get("tsunami_confidence")
                    _verbale["tsunami_direction"]  = _ts_voto.get("tsunami_direction")
                    _verbale["tsunami_reason"]     = _ts_voto.get("tsunami_reason")
                    _verbale["tsunami_size_mult"]  = _ts_voto.get("tsunami_size_mult")
                except Exception:
                    pass

                # ════════════════════════════════════════════════════════════════
                # PATCH 7 BUG 14 — Disable Operational BYPASS_MAGNITUDE_v2
                # ════════════════════════════════════════════════════════════════
                # Background: BYPASS_MAGNITUDE_v2 si attivava quando BTC si era
                # mosso più di ~0.05% in 10 minuti (soglia $40 a BTC=80k). Quando
                # attivo:
                #   1. bypassava TSUNAMI_NO_ENTRY (l'NO_ENTRY non bloccava più)
                #   2. iniettava proposed_direction + flip_confidence=1.0 nel verbale
                #   3. SC accettava il flip (perché flip_confidence>=0.7)
                #   4. il bot entrava su rincorsa post-movimento
                #
                # Diagnosi di Roberto (17 maggio 2026): osservando i trade dopo
                # un doppio WIN, i LOSS_FEE consecutivi avevano score=0.0
                # soglia=0.0 nel data_json — segno di entry su path che bypassa
                # SC. La firma del WIN era ritrovata, ma il bot entrava ciecamente
                # con BYPASS_MAGNITUDE attivo, perdendo le fee.
                #
                # Fix PATCH 7:
                #  - BYPASS_MAGNITUDE_OPERATIVO = False (flag, default OFF)
                #  - Il calcolo continua (utile come log osservativo)
                #  - _bypass_magnitude_observed = True quando calcolo supera soglia
                #  - _bypass_magnitude (variabile operativa) resta False
                #  - TSUNAMI_NO_ENTRY non viene più bypassato da magnitude
                #  - Nessun flip_confidence=1.0 derivante dal bypass
                #  - Nessun _tsunami_size_mult=1.0 forzato da bypass
                #
                # In futuro: si potrà riattivare BYPASS_MAGNITUDE solo con
                # condizioni più strette (es. score SC valido prima del bypass).
                # ════════════════════════════════════════════════════════════════
                BYPASS_MAGNITUDE_OPERATIVO = False  # PATCH 7: flag OFF di default

                # ── BYPASS MAGNITUDE v2 (calcolo invariato, USO ridotto a osservazione)
                _bypass_magnitude = False           # OPERATIVO: resta False
                _bypass_magnitude_observed = False  # OSSERVATIVO: nuovo
                _bypass_dir = None
                try:
                    _ts_verd = _ts_decision.verdetti if hasattr(_ts_decision, 'verdetti') else {}
                    _v10 = _ts_verd.get('10min', None)
                    if _v10 and hasattr(_v10, 'direction') and hasattr(_v10, 'coerenza'):
                        if _v10.coerenza >= 0.65 and _v10.strength >= 0.40 and _v10.direction in ('UP', 'DOWN'):
                            if hasattr(self, 'campo') and hasattr(self.campo, '_prices_ta'):
                                _prices_recent = list(self.campo._prices_ta)
                                if len(_prices_recent) >= 50:
                                    _p_start = _prices_recent[0]
                                    _p_now = _prices_recent[-1]
                                    _delta_dollar = abs(_p_now - _p_start)
                                    # ════════════════════════════════════════════════
                                    # PASSO 9 (15mag2026) — SOGLIA PROPORZIONALE
                                    # 0.05% del prezzo corrente.
                                    #   BTC=80k → $40 ; BTC=100k → $50 ; BTC=50k → $25
                                    # ════════════════════════════════════════════════
                                    _bypass_threshold = max(15.0, _p_now * 0.0005)
                                    if _delta_dollar >= _bypass_threshold:
                                        _bypass_dir = 'LONG' if _v10.direction == 'UP' else 'SHORT'
                                        _bypass_magnitude_observed = True
                                        # PATCH 7: setta _bypass_magnitude operativo SOLO se flag attivo
                                        if BYPASS_MAGNITUDE_OPERATIVO:
                                            _bypass_magnitude = True
                                            self._log_m2("⚡", f"BYPASS_MAGNITUDE_v2: 10min={_v10.direction} "
                                                               f"coe={_v10.coerenza:.2f} str={_v10.strength:.2f} "
                                                               f"delta=${_delta_dollar:.0f}/${_bypass_threshold:.0f} → {_bypass_dir}")
                                        else:
                                            # PATCH 7: solo osservativo. Non autorizza entry.
                                            self._log_m2("👁️", f"BYPASS_MAGNITUDE_OBSERVED: 10min={_v10.direction} "
                                                               f"coe={_v10.coerenza:.2f} str={_v10.strength:.2f} "
                                                               f"delta=${_delta_dollar:.0f}/${_bypass_threshold:.0f} → {_bypass_dir} "
                                                               f"reason=BYPASS_OPERATIVO_OFF_PATCH7")
                except Exception as _e_bm:
                    pass

                # ── DEPOSIZIONE: TSUNAMI_NO_ENTRY (era return — ora verbale) ──
                # PATCH 7: _bypass_magnitude resta sempre False (a meno di flag),
                # quindi TSUNAMI_NO_ENTRY non viene più scavalcato da magnitude.
                # Resta possibile lo scavalco da _bypass_oracolo (path separato, non toccato).
                if _ts_decision.azione == 'NO_ENTRY' and not _bypass_magnitude and not _bypass_oracolo:
                    self._log_m2("🌊", f"TSUNAMI dep: NO_ENTRY — {_ts_decision.motivo}")
                    # NESSUN return. Tsunami ha deposto. SC valuterà.

                # FIX #31: se bypass oracolo attivo, log (path separato, non toccato da PATCH 7)
                if _bypass_oracolo and _ts_decision.azione == 'NO_ENTRY':
                    self._log_m2("📜", f"TSUNAMI_BYPASSED_BY_ORACOLO: fp WR={_bypass_oracolo_wr:.0%} "
                                       f"n={_bypass_oracolo_n}")
                    self._tsunami_size_mult = 0.7

                # ── PROPOSTA DI FLIP (era modifica di stato — ora verbale) ────
                # PATCH 7: _bypass_magnitude=False di default, quindi questo ramo
                # non si attiva. Nessun flip_confidence=1.0 da bypass magnitude.
                if _bypass_magnitude:
                    _ts_dir = _bypass_dir
                    if _ts_dir != _campo_dir:
                        # NON modifico più self.campo._direction.
                        # Depongo la PROPOSTA nel verbale. SC deciderà il flip.
                        _verbale["proposed_direction"] = _ts_dir
                        _verbale["flip_confidence"]    = 1.0  # bypass magnitude = alta confidenza
                        _verbale["flip_reason"]        = "FLIP_MAGNITUDE_10min_strutturato"
                        self._log_m2("🔄", f"FLIP proposto: {_campo_dir} → {_ts_dir} (deposto, SC decide)")
                    self._tsunami_size_mult = 1.0
                else:
                    _ts_dir = 'LONG' if _ts_decision.azione == 'ENTRA_LONG' else 'SHORT'
                    if _ts_dir != _campo_dir:
                        if _bypass_oracolo:
                            self._log_m2("📜", f"TSUNAMI_DISCORDE_BYPASSED: oracolo WR={_bypass_oracolo_wr:.0%}")
                            self._tsunami_size_mult = 0.7
                        else:
                            # ── DEPOSIZIONE: TSUNAMI_DISCORDE (era return) ────
                            self._log_m2("🌊", f"TSUNAMI dep: DISCORDE campo={_campo_dir} vs tsunami={_ts_dir}")
                            _verbale["tsunami_discorde"] = True
                            # NESSUN return. Tsunami ha deposto la discordanza.
                            self._tsunami_size_mult = _ts_decision.size_mult
                    else:
                        self._log_m2("🌊", f"TSUNAMI dep: OK {_ts_dir} conf={_ts_decision.confidenza}/3")
                        self._tsunami_size_mult = _ts_decision.size_mult
            else:
                self._tsunami_size_mult = 1.0  # fallback se modulo non disponibile

            # ── L1.5 — VERITAS GATE: blocca contesti tossici ───────────────
            # ════════════════════════════════════════════════════════════════
            # ZONA 2 — DEPOSIZIONE VERITAS (statuto Passo 2)
            # ════════════════════════════════════════════════════════════════
            # Veritas NON è più un gate. NON fa return. Depone le statistiche
            # del contesto nel verbale. SC deciderà se il contesto è tossico.
            # ════════════════════════════════════════════════════════════════
            _ctx_key = f"{momentum}|{volatility}|{trend}"
            try:
                _ver_stats = self.veritas.get_ctx_stats(self._m2_ctx_stats, _ctx_key)
                _verbale["veritas_ctx_wr"]        = _ver_stats.get("ctx_wr")
                _verbale["veritas_ctx_samples"]   = _ver_stats.get("ctx_samples")
                _verbale["veritas_ctx_pnl_avg"]   = _ver_stats.get("ctx_pnl_avg")
                _verbale["veritas_last_judgement"]= _ver_stats.get("last_judgement")
                if _ver_stats.get("last_judgement") == "TOSSICO":
                    self._log_m2("🚫", f"VERITAS dep: {_ctx_key} TOSSICO "
                                       f"wr={_ver_stats['ctx_wr']:.0%} n={_ver_stats['ctx_samples']} "
                                       f"pnl=${_ver_stats['ctx_pnl_avg']:+.2f}")
                # NESSUN return. Veritas ha deposto. SC valuterà.
            except Exception as _ve:
                log.debug(f"[VERITAS_DEP_ERR] {_ve}")

            _dir           = self.campo._direction
            fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend, _dir)
            self._last_fingerprint_wr = fingerprint_wr
            _verbale["fp_wr"] = fingerprint_wr
            # ════════════════════════════════════════════════════════════════
            # PATCH 14 BUG 21 — Gate Oracolo fp_wr_raw_min (OPERATIVO)
            # ════════════════════════════════════════════════════════════════
            # DIAGNOSI FORENSE (18 mag 2026 sera):
            #   Sui 237 trade storici (tabella trades), 93 trade (39%) sono entrati
            #   con fp_wr_raw < 0.10 producendo WR 3.2% e -$165.65 netti.
            #   L'Oracolo SAPEVA, ma il bot ignorava il dato e continuava a entrare.
            #
            # COMPORTAMENTO:
            #   - Se il fingerprint ha samples >= 30 (memoria storica solida)
            #     E fp_wr_raw < 0.10 (Oracolo dice "qui non si vince")
            #   - BLOCCA l'entry, log ENTRY_BLOCKED_FP_WR_LOW, return
            #   - Fingerprint NUOVI (samples < 30): non blocca (esplorazione consentita)
            #
            # SIMULAZIONE SU 237 TRADE STORICI:
            #   - 93 trade bloccati (39% di evitamento)
            #   - 3 WIN persi (-$6.76), 90 LOSS evitati (+$172.41)
            #   - DELTA NETTO: +$165.65 (riduzione 42% del danno)
            #
            # GUARDRAIL:
            #   - Soglia 0.10 (conservativa, scelta da Roberto)
            #   - Min samples 30 (evita falsi positivi su fp poveri)
            #   - BLOCK puro (no entry, no fee)
            #   - Sentinella log: ENTRY_BLOCKED_FP_WR_LOW
            #
            # NON TOCCA: SC, soglia, breath/nerv/comparto, capsule, V2, Sinapsi
            # ════════════════════════════════════════════════════════════════
            FP_WR_MIN_THRESHOLD   = 0.10   # PATCH 14: WR storico minimo per entry
            FP_WR_MIN_SAMPLES     = 30     # PATCH 14: minimo n samples per applicare gate
            try:
                _fp_key_p14 = self.oracolo._fp(momentum, volatility, trend, _dir)
                _fp_mem_p14 = self.oracolo._memory.get(_fp_key_p14)
                _fp_samples_p14 = float(_fp_mem_p14.get('samples', 0)) if _fp_mem_p14 else 0.0
                if _fp_samples_p14 >= FP_WR_MIN_SAMPLES and fingerprint_wr < FP_WR_MIN_THRESHOLD:
                    self._log_m2("🛑",
                        f"ENTRY_BLOCKED_FP_WR_LOW fp={_fp_key_p14} "
                        f"wr={fingerprint_wr:.3f} < {FP_WR_MIN_THRESHOLD:.2f} "
                        f"samples={_fp_samples_p14:.0f} (>={FP_WR_MIN_SAMPLES})")
                    # ════════════════════════════════════════════════════════════
                    # PATCH 14 TRACEFIX (firma ChatGPT 18 mag 2026)
                    # Tracciabilità investigativa nel verbale costituzionale.
                    # Ogni blocco entry DEVE essere visibile nel verbale, non solo
                    # nei log Render volatili. Senza questo, Roberto torna cieco
                    # sui blocchi PATCH 14.
                    # ════════════════════════════════════════════════════════════
                    _verbale["blocked_by"] = "PATCH14_FP_WR_LOW"
                    _verbale["fp_key"]     = _fp_key_p14
                    _verbale["fp_samples"] = int(_fp_samples_p14)
                    _verbale["fp_wr_raw"]  = round(float(fingerprint_wr), 4)
                    self._log_constitutional(_verbale, "PRE_OPEN_VETO_FP_WR_LOW")
                    return
            except Exception as _p14e:
                # Guardrail anti-regressione: in caso di errore, NON blocchiamo
                # (preferiamo perdere il filtro che bloccare per bug)
                log.debug(f"[P14_GATE_ERR] {_p14e}")
            # ════════════════════════════════════════════════════════════════
            matrimonio_name = MatrimonioIntelligente.get_marriage(
                momentum, volatility, trend).get("name", "WEAK_NEUTRAL")

            # ════════════════════════════════════════════════════════════════
            # ZONA 2 — DEPOSIZIONE CAPSULE (statuto Passo 1)
            # ════════════════════════════════════════════════════════════════
            # Le capsule NON sono più un gate. NON fanno return, NON fanno break.
            # consulta() itera TUTTE le capsule e accumula i voti pesati.
            # Il verbale riceve block_score, boost_score, reasons. SC deciderà.
            # ════════════════════════════════════════════════════════════════
            if hasattr(self, 'capsule_manager') and self.capsule_manager:
                try:
                    _learned_ctx = {
                        'momentum':   momentum,
                        'volatility': volatility,
                        'trend':      trend,
                        'direction':  _dir,
                        'regime':     self._regime_current,
                        'matrimonio': matrimonio_name,
                        'oi_carica':  getattr(self, '_oi_carica', 0.0),
                        'oi_short':   getattr(self, '_oi_carica_short', 0.0),
                        'breath_fase':    (self._breath._fase    if self._breath else 'NEUTRO'),
                        'breath_energia': (self._breath._energia if self._breath else 0.0),
                    }
                    _capsule_voto = self.capsule_manager.consulta(_learned_ctx)
                    _verbale["capsule_block_score"]     = _capsule_voto.get("block_score", 0.0)
                    _verbale["capsule_boost_score"]     = _capsule_voto.get("boost_score", 0.0)
                    _verbale["capsule_threshold_delta"] = _capsule_voto.get("threshold_delta", 0.0)
                    _verbale["capsule_size_delta"]      = _capsule_voto.get("size_delta", 1.0)
                    _verbale["capsule_reasons"]         = _capsule_voto.get("reasons", [])
                    _verbale["capsule_oracolo_override"]= _capsule_voto.get("oracolo_override", False)
                    if _capsule_voto.get("block_score", 0) > 0:
                        self._log_m2("💊", f"CAPSULE dep: block_score={_capsule_voto['block_score']:.0f} "
                                           f"reasons={_capsule_voto.get('reasons', [])}")
                    # NESSUN return, NESSUN break. Le capsule hanno deposto.
                except Exception as _le:
                    log.debug(f"[CAPSULE_DEP_ERR] {_le}")

            # NOTA (2giu): l'hook canvas observe_entry è stato SPOSTATO in alto,
            # subito dopo la nascita del verbale (~riga 10355), PRIMA dei veti,
            # così registra anche le valutazioni bloccate dal guardiano. Qui era
            # ridondante e avrebbe causato doppia scrittura → rimosso.

            # ── CALCOLA EFFECTIVE REGIME ─────────────────────────────────────
            _now_eo    = time.time()
            _fuoco_age = _now_eo - getattr(self, '_last_fuoco_event_ts', 0)
            _pb_age    = _now_eo - getattr(self, '_last_pb_event_ts', 0)
            _eo_carica = getattr(self, '_oi_carica', 0.0)
            _pb_factor = getattr(self, '_last_pb_factor', 0.0)
            _pb_solo   = _pb_age < 30 and _pb_factor >= 0.90 and _eo_carica >= 0.80
            _fuoco_pb  = _fuoco_age < 30 and _pb_age < 30 and _eo_carica >= 0.80
            if (_pb_solo or _fuoco_pb) and self._regime_current == 'RANGING':
                _effective_regime = 'EXPLOSIVE'
                self._log_m2("⚡", f"EXPLOSIVE_OVERRIDE fuoco={_fuoco_age:.1f}s "
                                   f"pb={_pb_age:.1f}s factor={_pb_factor:.2f} carica={_eo_carica:.2f}")
            else:
                _effective_regime = self._regime_current

            # ═══════════════════════════════════════════════════════════════
            # PERCORSO 1: EXPLOSIVE con carica alta
            # ═══════════════════════════════════════════════════════════════
            # PASSO 5c (decisione capoprogetto, 14mag): in PERCORSO 1, SC è
            # DECISORE — riceve il verbale completo e decide (BLOCCA / FLIP /
            # ENTRA con size modulata). PERCORSO 1 rispetta la decisione.
            # MODO 2 (osservatore) è finito: i log live di 5b hanno dato il
            # verdetto — PERCORSO 1 era l'ultimo dittatore.
            # I veti FISICI di PERCORSO 1 (RANGE check, dati insufficienti)
            # restano return — sono ZONA 1, matematica del trade.
            # Il veto CAPSULE diventa deposizione.
            # ═══════════════════════════════════════════════════════════════
            # P1_EXPLOSIVE_OFF (18giu2026, Roberto — IL GRANDE BUCO).
            # La corsia P1 EXPLOSIVE apriva il trade e faceva return, SALTANDO
            # tutto il Percorso 2 (ritardo, osservazione, GATE PEAK 2.50). Per 15
            # mesi gli EXPLOSIVE (trans inclusi) entravano da questa porta laterale
            # scavalcando ogni logica sana. Con P1_EXPLOSIVE_OFF=true la porta si
            # chiude: l'EXPLOSIVE NON prende la corsia, cade nel Percorso 2 e passa
            # dal gate peak come tutti. Reversibile: false (o non settata) = vecchio
            # comportamento. NON tocca la logica interna di P1, solo l'ingresso.
            # ═══════════════════════════════════════════════════════════════
            _p1_off = os.environ.get("P1_EXPLOSIVE_OFF", "false").lower() == "true"
            if (_effective_regime == 'EXPLOSIVE' and _eo_carica >= 0.80
                    and not _p1_off):
                _verbale["percorso"] = "P1_EXPLOSIVE"
                _verbale["regime"]   = _effective_regime
                _verbale["oi_carica"]= _eo_carica
                _verbale["oi_stato"] = self._oi_stato
                _verbale["oi_carica_short"] = getattr(self, '_oi_carica_short', 0.0)

                # ── DEPOSIZIONE CAPSULE in PERCORSO 1 (era return — ora verbale)
                if self.capsule_manager:
                    _cm_ctx_p1 = {
                        'momentum':        momentum,
                        'volatility':      volatility,
                        'trend':           trend,
                        'direction':       _dir,
                        'regime':          _effective_regime,
                        'oi_carica':       _eo_carica,
                        'oi_stato':        self._oi_stato,
                        'loss_consecutivi': self._m2_loss_consecutivi(),
                        'matrimonio':      matrimonio_name,
                        'oi_short':        getattr(self, '_oi_carica_short', 0.0),
                        'breath_fase':     (self._breath._fase    if self._breath else 'NEUTRO'),
                        'breath_energia':  (self._breath._energia if self._breath else 0.0),
                    }
                    try:
                        _cm_p1_voto = self.capsule_manager.consulta(_cm_ctx_p1)
                        # aggiorno il verbale con i voti capsule di PERCORSO 1
                        _verbale["capsule_block_score"]      = _cm_p1_voto.get("block_score", 0.0)
                        _verbale["capsule_boost_score"]      = _cm_p1_voto.get("boost_score", 0.0)
                        _verbale["capsule_reasons"]          = _cm_p1_voto.get("reasons", [])
                        _verbale["capsule_oracolo_override"] = _cm_p1_voto.get("oracolo_override", False)
                        if _cm_p1_voto.get("block_score", 0) > 0:
                            self._log_m2("💊", f"PERCORSO1 CAPSULE dep: block_score={_cm_p1_voto['block_score']:.0f}")
                        # NESSUN return — le capsule hanno deposto
                    except Exception as _le1:
                        log.debug(f"[P1_CAPSULE_DEP_ERR] {_le1}")

                # ── VETI FISICI PERCORSO 1 (ZONA 1 — restano return) ──────────
                # Questi sono matematica del trade: il movimento atteso non
                # copre le fee. Non sono opinioni, sono impossibilità fisiche.
                if trend == 'SIDEWAYS':
                    _prices_buf = list(self.campo._prices_ta) if hasattr(self.campo, '_prices_ta') else []
                    if len(_prices_buf) >= 10:
                        _expected_move = max(_prices_buf[-20:]) - min(_prices_buf[-20:])
                        _breakeven = self._calcola_breakeven_dinamico(momentum, volatility, trend)
                        if _expected_move < _breakeven:
                            self._log_m2("🚫", f"EXPLOSIVE_SIDEWAYS_BLOCK: move=${_expected_move:.1f} < breakeven=${_breakeven:.1f}")
                            _verbale["blocked_by"] = "ZONA1_EXPLOSIVE_SIDEWAYS_breakeven"
                            self._log_constitutional(_verbale, "PRE_SC_VETO_EXPLOSIVE_SIDEWAYS")
                            return
                        self._log_m2("✅", f"EXPLOSIVE_SIDEWAYS_OK: move=${_expected_move:.1f} >= breakeven=${_breakeven:.1f}")
                    else:
                        self._log_m2("🚫", f"EXPLOSIVE_SIDEWAYS_BLOCK: dati insufficienti per RANGE CHECK")
                        _verbale["blocked_by"] = "ZONA1_EXPLOSIVE_SIDEWAYS_dati_insuff"
                        self._log_constitutional(_verbale, "PRE_SC_VETO_EXPLOSIVE_SIDEWAYS_DATI")
                        return

                _prices_buf = list(self.campo._prices_ta) if hasattr(self.campo, '_prices_ta') else []
                if len(_prices_buf) >= 10:
                    _expected_move = max(_prices_buf[-20:]) - min(_prices_buf[-20:])
                    _breakeven = self._calcola_breakeven_dinamico(momentum, volatility, trend)
                    if _expected_move < _breakeven:
                        self._log_m2("🚫", f"RANGE_INSUFFICIENTE: move=${_expected_move:.1f} < breakeven=${_breakeven:.1f}")
                        _verbale["blocked_by"] = "ZONA1_RANGE_INSUFFICIENTE"
                        self._log_constitutional(_verbale, "PRE_SC_VETO_RANGE_INSUFFICIENTE")
                        return
                    self._log_m2("✅", f"RANGE_OK: move=${_expected_move:.1f} >= breakeven=${_breakeven:.1f}")

                # Size proporzionale alla carica
                size = round(min(1.0, max(0.30, _eo_carica)), 2)

                # ── CAMPO GRAVITAZIONALE REALE ───────────────────────────────
                _fantasma_p1 = self.oracolo.is_fantasma(momentum, volatility, trend, _dir)
                # PATCH 0 VINCOLO 2: pred_score a Campo SOLO se qualified
                _pred_st_p1 = self.supercervello._get_pred_state()
                pred_score_ops_p1 = _pred_st_p1["score"] if _pred_st_p1["qualified"] else 0.0
                _result_p1 = self.campo.evaluate(
                    seed_score        = seed['score'],
                    fingerprint_wr    = fingerprint_wr,
                    momentum          = momentum,
                    volatility        = volatility,
                    trend             = trend,
                    regime            = _effective_regime,
                    matrimonio_name   = matrimonio_name,
                    divorzio_set      = self.memoria.divorzio,
                    fantasma_info     = _fantasma_p1,
                    loss_consecutivi  = self._m2_loss_consecutivi(),
                    soglia_boost      = self._get_ia_soglia_boost(momentum, volatility, trend),
                    pred_score        = pred_score_ops_p1,
                )
                # ════════════════════════════════════════════════════════════════
                # PATCH 10 BUG 17a — Fresh Vote Required (P1_EXPLOSIVE bypass off)
                # ════════════════════════════════════════════════════════════════
                # Background: prima di PATCH 10, il veto del Campo veniva
                # rispettato solo sotto OI carica < 0.80. Sopra 0.80, il bot
                # entrava comunque con score=0 soglia=0 (ereditati da _veto()).
                # Era il terzo bypass nascosto: EXPLOSIVE_OVERRIDE.
                #
                # Diagnosi di Roberto (18 mag mattina): "qualcosa di sporco entra
                # dopo il WIN, gatto che si morde la coda". Su 15 trade post-PATCH 9,
                # 9 avevano score=0 soglia=0 → entry forzate da carica OI alta.
                #
                # Fix PATCH 10: veto = veto, sempre. Carica OI è log, non è
                # autorizzazione. La filosofia "se la carica è alta entro lo
                # stesso" è il vizio architetturale madre.
                # ════════════════════════════════════════════════════════════════
                if _result_p1['veto']:
                    self._log_m2("🚫", f"P1_EXPLOSIVE_VETO_RESPECTED: {_result_p1['veto']} "
                                       f"carica={_eo_carica:.2f} {momentum}|{volatility}|{trend} "
                                       f"reason=PATCH10_NO_BYPASS_FROM_OI")
                    _verbale["blocked_by"] = f"ZONA1_PERCORSO1_VETO:{_result_p1['veto']}"
                    self._log_constitutional(_verbale, "PRE_SC_VETO_PERCORSO1_CAMPO")
                    return
                score  = _result_p1['score']
                soglia = _result_p1['soglia']
                _verbale["score"]  = score
                _verbale["soglia"] = soglia
                self._log_m2("📊", f"PERCORSO1_CAMPO: score={score:.1f} soglia={soglia:.1f} "
                                   f"carica={_eo_carica:.2f} {momentum}|{volatility}|{trend}")

                # CapsuleManager può modificare size (consulta, non valuta)
                if self.capsule_manager:
                    try:
                        _cm_size = self.capsule_manager.consulta({
                            'momentum': momentum, 'volatility': volatility,
                            'trend': trend, 'direction': _dir,
                            'regime': _effective_regime, 'oi_carica': _eo_carica,
                        })
                        size = round(min(1.0, max(0.30, size * _cm_size.get('size_delta', 1.0))), 2)
                    except Exception:
                        pass

                # ════════════════════════════════════════════════════════════
                # PASSO 5c — SC DECISORE su PERCORSO 1 (14mag2026)
                # L'ULTIMO DITTATORE ABDICA. SC riceve il verbale completo
                # e DECIDE. PERCORSO 1 rispetta la decisione:
                #   azione==BLOCCA        → non apre (record phantom + return)
                #   direction_vote != _dir → flippa la direzione prima di aprire
                #   size_mult             → modula la size
                # Stesso schema del blocco riga ~8964 di PERCORSO 2 (5b).
                # ════════════════════════════════════════════════════════════
                _sc_p1 = self._sc_osserva_explosive(
                    price, momentum, volatility, trend,
                    _dir, score, soglia, _verbale, seed)

                if _sc_p1 is not None:
                    # 1) SC BLOCCA → PERCORSO 1 non apre
                    if _sc_p1.get('azione') == 'BLOCCA':
                        self._log_m2("🚫", f"SC_BLOCCA_EXPLOSIVE: {_sc_p1.get('motivo','')}")
                        self._record_phantom(price, f"SC_BLOCCA_EXPLOSIVE_{_sc_p1.get('motivo','')[:20]}",
                                             seed['score'], momentum, volatility, trend)
                        _verbale["blocked_by"] = f"SC_EXPLOSIVE:{_sc_p1.get('motivo','')[:30]}"
                        self._log_constitutional(_verbale, "SC_BLOCCA_EXPLOSIVE")
                        if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                            return
                        self._log_m2("👁", "OBSERVER: SC_BLOCCA_EXPLOSIVE avrebbe bloccato — LASCIO PASSARE")

                    # 2) SC propone un FLIP → PERCORSO 1 flippa prima di aprire
                    _sc_dv   = _sc_p1.get('direction_vote')
                    _sc_dvc  = _sc_p1.get('direction_vote_confidence', 0.0)
                    if _sc_dv and _sc_dv != _dir and _sc_dvc >= 0.30:
                        self._log_m2("🔄", f"FLIP_BY_SC_EXPLOSIVE: {_dir} → {_sc_dv} "
                                           f"(conf={_sc_dvc:.2f})")
                        self.campo._direction = _sc_dv
                        _dir = _sc_dv
                        # re-fetch fingerprint per la nuova direzione (coerenza)
                        try:
                            fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend, _dir)
                            self._last_fingerprint_wr = fingerprint_wr
                        except Exception:
                            pass

                    # 3) SC modula la size
                    size = round(min(1.0, max(0.30, size * _sc_p1.get('size_mult', 1.0))), 2)
                # se _sc_p1 is None (eccezione in decide) → fallback: entra come prima

                self._log_m2("🚀", f"EXPLOSIVE ENTRY {_dir} carica={_eo_carica:.2f} "
                                   f"size={size:.2f} {momentum}|{volatility}|{trend}")
                _verbale["blocked_by"] = None  # è un'entrata
                self._log_constitutional(_verbale, "ENTRY_OPENED_P1_EXPLOSIVE")
                # ════════════════════════════════════════════════════════════════
                # PATCH 10 BUG 17b — Fresh Vote Guard (P1)
                # ════════════════════════════════════════════════════════════════
                # Legge madre: NESSUNA ENTRY SENZA VOTO FRESCO.
                # Se score<=0 o soglia<=0, qualunque path ci abbia portati qui,
                # il trade non apre. Cattura anche quarti/quinti bypass futuri.
                # ════════════════════════════════════════════════════════════════
                if score <= 0 or soglia <= 0:
                    self._log_m2("🛑", f"ENTRY_BLOCKED_NO_FRESH_SCORE score={score} "
                                       f"soglia={soglia} path=P1_EXPLOSIVE")
                    _verbale["blocked_by"] = "PATCH10_NO_FRESH_SCORE_P1"
                    self._log_constitutional(_verbale, "PRE_OPEN_VETO_NO_FRESH_SCORE_P1")
                    return
                # ════════════════════════════════════════════════════════════════
                # PATCH 11 BUG 18c — Post-Win Rebalance Gate (P1)
                # ════════════════════════════════════════════════════════════════
                # Regola: dopo un WIN_NET significativo (>=$1 netto), per 5min,
                # se il fingerprint corrente è uguale a quello del WIN, allora
                # blocca rientri se score_now < last_win_score - 5.
                # Cambio fingerprint = ambiente nuovo = entra libero (loggato).
                # ════════════════════════════════════════════════════════════════
                POST_WIN_REBALANCE_ENABLED = True
                POST_WIN_WINDOW_SEC        = 300    # 5 minuti
                POST_WIN_SCORE_TOLERANCE   = 5.0    # punti
                try:
                    if (POST_WIN_REBALANCE_ENABLED
                        and self._last_win_score is not None
                        and self._last_win_fingerprint is not None
                        and self._last_win_ts is not None):
                        _now_pw = time.time()
                        _dt_pw  = _now_pw - self._last_win_ts
                        if _dt_pw <= POST_WIN_WINDOW_SEC:
                            _curr_fp = f"{momentum}|{volatility}|{trend}|{_dir}"
                            if _curr_fp == self._last_win_fingerprint:
                                _gate_score = self._last_win_score - POST_WIN_SCORE_TOLERANCE
                                if score < _gate_score:
                                    self._log_m2("🚧",
                                        f"POST_WIN_REENTRY_BLOCKED score_now={score:.1f} "
                                        f"last_win_score={self._last_win_score:.1f} "
                                        f"gate={_gate_score:.1f} fp={_curr_fp} "
                                        f"dt={_dt_pw:.0f}s reason=ENV_NOT_REBUILT path=P1")
                                    _verbale["blocked_by"] = "PATCH11_POST_WIN_ENV_NOT_REBUILT_P1"
                                    self._log_constitutional(_verbale, "PRE_OPEN_VETO_POST_WIN_REBALANCE_P1")
                                    return
                            else:
                                self._log_m2("🆕",
                                    f"POST_WIN_REENTRY_ALLOWED_NEW_FINGERPRINT "
                                    f"curr={_curr_fp} last={self._last_win_fingerprint} "
                                    f"dt={_dt_pw:.0f}s path=P1")
                except Exception as _e_pwg:
                    pass
                # ════════════════════════════════════════════════════════════════

                # ════════════════════════════════════════════════════════════════
                # PATCH 12 BUG 19a — Drift Magnitude Entry Filter (P1)
                # ════════════════════════════════════════════════════════════════
                # Diagnosi certificata sui 41 trade reali nella tabella
                # winning_signatures (analisi 18 mag 2026 mattina):
                #   - 8/8 WIN_NET hanno |drift| >= 0.0063 (minimo osservato)
                #   - 10/33 LOSS hanno |drift| < 0.006 (zona morta = punto morto)
                # Filtro |drift| >= 0.006 cattura 100% dei WIN e blocca 30% delle LOSS.
                # Falsi positivi: 0. PnL salvato stimato: +$24.83 su 41 trade.
                # ════════════════════════════════════════════════════════════════
                DRIFT_MIN_MAGNITUDE = 0.006   # PATCH 12: soglia minima drift
                try:
                    _p12_drift = 0.0
                    if len(self.campo._prices_long) >= 100:
                        _p_p12 = list(self.campo._prices_long)
                        _avg_old_p12 = sum(_p_p12[:50]) / 50
                        _avg_new_p12 = sum(_p_p12[-50:]) / 50
                        if _avg_old_p12 > 0:
                            _p12_drift = (_avg_new_p12 - _avg_old_p12) / _avg_old_p12 * 100
                    if abs(_p12_drift) < DRIFT_MIN_MAGNITUDE:
                        self._log_m2("🚧",
                            f"ENTRY_BLOCKED_FLAT_DRIFT drift={_p12_drift:+.4f} "
                            f"threshold={DRIFT_MIN_MAGNITUDE} path=P1_EXPLOSIVE "
                            f"reason=ZONA_MORTA_MERCATO")
                        _verbale["blocked_by"] = "PATCH12_FLAT_DRIFT"
                        self._log_constitutional(_verbale, "PRE_OPEN_VETO_FLAT_DRIFT_P1")
                        if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                            return
                        self._log_m2("👁", "OBSERVER: FLAT_DRIFT avrebbe bloccato — LASCIO PASSARE")
                except Exception as _e_p12_p1:
                    # Se errore nel calcolo drift, NON blocchiamo (conservativo)
                    pass
                # ════════════════════════════════════════════════════════════════
                # FIX TRACCIATURA 27mag2026 (Roberto):
                # Percorso 1 EXPLOSIVE apriva trade SENZA registrare firma né canvas.
                # Risultato: 179 trade su 386 invisibili nelle analisi (46%).
                # Include i WIN più grossi (+$15.38). Fix: replico gli hook che
                # Percorso 2 ha già a riga ~10500 e ~11425, ma SOLO QUI ed in modo
                # minimo (senza i 32 campi della firma — non sono tutti calcolati
                # a questo punto del codice, ma i campi base sì).
                # ════════════════════════════════════════════════════════════════
                # Hook 1/2: canvas observe_entry per Percorso 1
                try:
                    if getattr(self, "canvas", None) is not None:
                        # PRIMA DEL SEME (2giu): vita dell'energia anche su P1
                        try:
                            _seed_p1 = self.seed_scorer.score()
                            _verbale["range_pos"]   = _seed_p1.get("range_pos")
                            _verbale["drift_slope"] = _seed_p1.get("drift_slope")
                            _verbale["seed_score"]  = _seed_p1.get("score")
                        except Exception:
                            pass
                        _canvas_tid_p1 = f"t_{int(time.time()*1000)}_P1"
                        _verbale["_canvas_tid"] = _canvas_tid_p1
                        _verbale["_path"] = "P1_EXPLOSIVE"
                        # Salvo per transfer in _open_shadow_position
                        self._pending_canvas_tid_p1 = _canvas_tid_p1
                        # DISATTIVATO 4giu (Roberto): vedi nota punto 1.
                        # self.canvas.observe_entry(_verbale, trade_id=_canvas_tid_p1)
                except Exception as _ce_p1:
                    log.debug(f"[CANVAS_HOOK_ENTRY_P1_ERR] {_ce_p1}")

                # Hook 2/2: winsig per Percorso 1 (firma minimale - campi disponibili)
                try:
                    if hasattr(self, '_winsig') and self._winsig is not None:
                        # Estraggo Tsunami se disponibile (stessa logica P2)
                        _ts30_p1 = _ts2m_p1 = _ts10_p1 = None
                        _ts_conf_p1 = None
                        try:
                            if hasattr(self, 'tsunami') and self.tsunami is not None:
                                _last_ts_p1 = self.tsunami.last_decision()
                                if _last_ts_p1 is not None:
                                    _v_ts_p1 = _last_ts_p1.get('verdetti', {})
                                    _ts30_p1 = _v_ts_p1.get('30s', {}).get('direction')
                                    _ts2m_p1 = _v_ts_p1.get('2min', {}).get('direction')
                                    _ts10_p1 = _v_ts_p1.get('10min', {}).get('direction')
                                    _ts_conf_p1 = _last_ts_p1.get('confidenza')
                        except Exception:
                            pass

                        _sig_p1 = WinningSignatureLogger.build_signature_from_context(
                            momentum=momentum,
                            volatility=volatility,
                            trend=trend,
                            direction=self.campo._direction,
                            regime=self._regime_current,
                            oi_stato=self._oi_stato,
                            oi_carica=self._oi_carica,
                            vol_pressure=getattr(self.campo, '_last_vol_pressure', None),
                            rsi=None,
                            drift=_p12_drift if '_p12_drift' in dir() else None,
                            score=score,
                            soglia=soglia,
                            pred_delta_fuoco=(self.heartbeat_data.get('pred_delta_fuoco', 0) if hasattr(self, 'heartbeat_data') else 0),
                            pred_delta_carica=(self.heartbeat_data.get('pred_delta_carica', 0) if hasattr(self, 'heartbeat_data') else 0),
                            pred_v2_delta=(self.heartbeat_data.get('pred_v2_delta', 0) if hasattr(self, 'heartbeat_data') else 0),
                            pred_source="P1_EXPLOSIVE",
                            ts_30s_direction=_ts30_p1,
                            ts_2min_direction=_ts2m_p1,
                            ts_10min_direction=_ts10_p1,
                            ts_confidenza=_ts_conf_p1,
                        )
                        # Salvo per close — uso un attributo separato che _open_shadow
                        # transferirà nello _shadow (riga 11220+)
                        self._pending_winsig_p1 = _sig_p1
                except Exception as _we_p1:
                    log.debug(f"[WINSIG_HOOK_ENTRY_P1_ERR] {_we_p1}")

                # ════════════════════════════════════════════════════════════════
                # PATCH 20 — CONSULTO CAPSULA MATRIGNA (Percorso 1)
                # La matrigna consulta la capsula figlia per questa firma.
                # In OBSERVER L4 OFF: registra ma non blocca.
                # ════════════════════════════════════════════════════════════════
                if getattr(self, "matrigna", None) is not None:
                    try:
                        _mat_tid = self._pending_canvas_tid_p1 or f"t_{int(time.time()*1000)}_P1"
                        _mat_ok, _mat_motivo, _mat_info = self.matrigna.consulta(
                            momentum=momentum,
                            volatility=volatility,
                            trend=trend,
                            regime=self._regime_current,
                            direction=self.campo._direction,
                            trade_id=_mat_tid,
                        )
                        _verbale["matrigna_motivo"] = _mat_motivo
                        _verbale["matrigna_stato"] = _mat_info.get('stato')
                        _verbale["matrigna_verdetto"] = _mat_info.get('verdetto')
                        if not _mat_ok and self.matrigna.is_blocking():
                            self._log_m2("👵", f"MATRIGNA BLOCCA P1: {_mat_motivo}")
                            _verbale["blocked_by"] = f"MATRIGNA:{_mat_info.get('firma_key','?')}"
                            self._log_constitutional(_verbale, "PRE_OPEN_VETO_MATRIGNA_P1")
                            if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                                return
                            self._log_m2("👁", "OBSERVER: MATRIGNA P1 avrebbe bloccato — LASCIO PASSARE")
                        elif "OBSERVER_WOULD_BLOCK" in _mat_motivo:
                            self._log_m2("👁", f"MATRIGNA suggerirebbe blocco (OBSERVER P1): {_mat_motivo}")
                    except Exception as _e_mat_p1:
                        log.debug(f"[MATRIGNA_HOOK_P1_ERR] {_e_mat_p1}")

                # FIX 23giu (Roberto): P1 (vecchio mondo, apre su score/pattern)
                # fa entrare FEMMINE peak-0 che vanno a -5 (KILLER_FEMMINA). I dati
                # mostrano: ogni loss e' peak 0.0 entrato da P1, ogni win e' peak>0.6.
                # Interruttore P1_OFF=true -> P1 non apre, entra SOLO MASCHIO_DIRETTO
                # (che ora richiede grasso reale >= soglia). Reversibile.
                # FIX 24giu (Roberto "metal detector"): P1 e' una PORTA LATERALE.
                # Con MACCHINA_PURA attiva NON deve aprire MAI, cablato — non un ENV
                # che ti dimentichi (default era false=APERTA, il buco). Le femmine
                # entravano da qui scavalcando MASCHIO_DIRETTO.
                _pura = os.environ.get("MACCHINA_PURA", "true").lower() == "true"
                if _pura or os.environ.get("P1_OFF", "true").lower() == "true":
                    self._log_m2("🚫", "P1 CHIUSA (macchina pura): entra solo MASCHIO_DIRETTO")
                    return
                self._open_shadow_position(price, score, soglia, seed, size,
                                            momentum, volatility, trend,
                                            matrimonio_name, fingerprint_wr)
                return

            # ═══════════════════════════════════════════════════════════════
            # PERCORSO 2: tutto il resto — gate normali
            # ═══════════════════════════════════════════════════════════════
            # NOTA L2: Il PRECURSORE_P2 era qui sopra ma usava variabili
            # score/soglia/size non ancora definite (bug latente). Spostato
            # DOPO `result = evaluate()` quando le variabili esistono.

            fantasma_info = self.oracolo.is_fantasma(momentum, volatility, trend, _dir)

            # PATCH 0 VINCOLO 2: pred_score a Campo SOLO se qualified
            _pred_st_p2 = self.supercervello._get_pred_state()
            _pred_score_ops_p2 = _pred_st_p2["score"] if _pred_st_p2["qualified"] else 0.0

            result = self.campo.evaluate(
                seed_score        = seed['score'],
                fingerprint_wr    = fingerprint_wr,
                momentum          = momentum,
                volatility        = volatility,
                trend             = trend,
                regime            = _effective_regime,
                matrimonio_name   = matrimonio_name,
                divorzio_set      = self.memoria.divorzio,
                fantasma_info     = fantasma_info,
                loss_consecutivi  = self._m2_loss_consecutivi(),
                soglia_boost      = self._get_ia_soglia_boost(momentum, volatility, trend),
                # PATCH 0 VINCOLO 2: pred_score SOLO se qualified (calcolato sotto)
                pred_score        = _pred_score_ops_p2,
            )

            _verbale["percorso"] = "P2_NORMALE"
            _verbale["regime"]   = _effective_regime
            _verbale["oi_carica"]= getattr(self, '_oi_carica', 0.0)
            _verbale["oi_stato"] = getattr(self, '_oi_stato', '')
            _verbale["oi_carica_short"] = getattr(self, '_oi_carica_short', 0.0)
            _verbale["breath_fase"]    = (self._breath._fase    if self._breath else 'NEUTRO')
            _verbale["breath_energia"] = (self._breath._energia if self._breath else 0.0)

            # ════════════════════════════════════════════════════════════════
            # ZONA 2 — DEPOSIZIONE CAMPO GRAVITAZIONALE (Passo 5a-bis, 14mag2026)
            # ════════════════════════════════════════════════════════════════
            # SCOPERTA dai log live 5a: result['veto'] del CampoGravitazionale
            # era il QUINTO dittatore — non mappato nello scheletro originale.
            # Conteneva 5 veti diversi (audit in campo.evaluate L4391-4443):
            #   - CM_TOSSICO / TOSSICO_*  → giudizio di merito  → DEPONE
            #   - DIVORZIO_PERMANENTE     → giudizio di merito  → DEPONE
            #   - DRIFT_VETO_*            → giudizio di merito  → DEPONE
            #   - WARMUP_*                → fisica (dati insuff) → RESTA return
            #
            # Distinzione per prefisso: solo WARMUP_ è veto fisico ZONA 1.
            # Tutto il resto è merito → diventa deposizione, SC decide.
            # campo.evaluate() NON è toccato — cambia solo chi lo ascolta.
            # ════════════════════════════════════════════════════════════════
            if result['veto']:
                _campo_veto = result['veto']

                # WARMUP_* — veto FISICO legittimo (ZONA 1): resta return.
                # Non ci sono abbastanza dati per analizzare. Non è un'opinione.
                if _campo_veto.startswith('WARMUP_'):
                    self._log_m2("⏳", f"CAMPO_WARMUP (veto fisico): {_campo_veto}")
                    _verbale["blocked_by"] = f"ZONA1_CAMPO_WARMUP:{_campo_veto}"
                    self._log_constitutional(_verbale, "PRE_SC_VETO_CAMPO_WARMUP")
                    return

                # ════════════════════════════════════════════════════════════
                # MERCATO MORTO — il campo dice la VERITA' (11giu, Roberto).
                # Il campo riconosce il contesto tossico (CTX_TOSSICO_DEBOLE_BASSA)
                # e predice WR ~13%. I dati lo confermano: 10 morti su 13 trade in
                # mercato morto. Era stato declassato a deposizione scavalcabile
                # dall'SC (col VOLPE_BOOST che entrava lo stesso). GLI RIDIAMO IL
                # POTERE DI BLOCCARE: se il veto è tossico E il mercato è morto
                # PURO (momentum DEBOLE + volatility BASSA), torna a essere return
                # FISICO come il WARMUP. Nessuno lo scavalca. Se il campo dice
                # morto, NON si opera. STRETTO: solo il morto puro (DEBOLE+BASSA),
                # i borderline passano (non strangoliamo i maschi).
                # ENV: MERCATO_MORTO_OFF=true per spegnere/tarare al volo.
                # ════════════════════════════════════════════════════════════
                if os.environ.get("MERCATO_MORTO_OFF", "false").lower() != "true":
                    _mom = str(momentum).upper()
                    _vol = str(volatility).upper()
                    _tox = ("TOSSICO" in _campo_veto) or ("CTX_" in _campo_veto)
                    if _tox and _mom == "DEBOLE" and _vol == "BASSA":
                        self._log_m2("⚰️", f"MERCATO MORTO (campo dice verità): "
                                            f"{_campo_veto} | {_mom}/{_vol} — NON si opera (BLOCCO)")
                        _verbale["blocked_by"] = f"MERCATO_MORTO:{_campo_veto}"
                        self._log_constitutional(_verbale, "PRE_SC_VETO_MERCATO_MORTO")
                        return

                # Tutto il resto (CM_TOSSICO, TOSSICO_, DIVORZIO, DRIFT_VETO)
                # è giudizio di merito → DEPOSIZIONE, niente return.
                # Il CampoGravitazionale depone il suo veto nel verbale.
                # SC lo leggerà e deciderà.
                _verbale["campo_veto"] = _campo_veto
                self._log_m2("🏛️", f"CAMPO dep: veto={_campo_veto} (deposto, SC decide)")
                # NESSUN return. Il CampoGravitazionale ha deposto.
                # NB: score/soglia da result restano validi e vengono usati sotto.

            # ── Warmup (veti fisici ZONA 1) ────────────────────────────────
            # BOOT_GUARD basato su _prices_ta = serie RSI/MACD. RSI/MACD sono
            # MORTI nel modello di Roberto. Questo warmup e' un residuo: aspetta
            # 20 campioni di un indicatore che non si usa piu'. Il warmup VERO
            # (prezzi, _prices_long) resta nel motore a monte. ATTRITO_OFF=true
            # spegne questo doppione e il grace post-warmup. REVERSIBILE.
            _attrito_warmup = os.environ.get("ATTRITO_OFF","false").lower() == "true"
            _warmup_rsi = len(self.campo._prices_ta) if hasattr(self.campo, '_prices_ta') else 0
            if _warmup_rsi < 20 and not _attrito_warmup:
                self._log_m2("⏳", f"BOOT_GUARD: warmup RSI {_warmup_rsi}/20")
                _verbale["blocked_by"] = "ZONA1_BOOT_GUARD"
                self._log_constitutional(_verbale, "PRE_SC_VETO_BOOT_GUARD")
                return
            if not hasattr(self, '_warmup_done_time'):
                self._warmup_done_time = time.time()
            if time.time() - self._warmup_done_time < 10 and not _attrito_warmup:
                _verbale["blocked_by"] = "ZONA1_POST_WARMUP_GRACE"
                self._log_constitutional(_verbale, "PRE_SC_VETO_POST_WARMUP_GRACE")
                return

            score  = result['score']
            soglia = result['soglia']
            _verbale["score"]  = score
            _verbale["soglia"] = soglia

            # ════════════════════════════════════════════════════════════════
            # SCORE_SOTTO — 5a-bis (14mag2026): distinzione costituzionale
            # ════════════════════════════════════════════════════════════════
            # I log live di 5a hanno dimostrato che result['veto'] del Campo e
            # SCORE_SOTTO sono LO STESSO NODO: _veto() ritorna score=0.0, che
            # fa scattare `not result['enter']` → SCORE_SOTTO. Liberare il Campo
            # senza toccare SCORE_SOTTO è impossibile — è una serratura sola.
            #
            # Due casi DISTINTI:
            #
            #  CASO A — il Campo ha DEPOSTO un veto di merito (campo_veto valorizzato).
            #    score=0.0 NON è "score genuino sotto soglia": è l'artefatto di
            #    _veto(). Il flow NON muore qui. Ricostruisco score/soglia/size
            #    di base e procedo a SC, che leggerà campo_veto nel verbale e
            #    deciderà. Questo COMPLETA la liberazione del 5° dittatore.
            #
            #  CASO B — NESSUN veto del Campo, ma score genuinamente < soglia.
            #    Questo è il dittatore SCORE_SOTTO che Roberto ha rinviato.
            #    RESTA return. Invariato. Sarà convertito in 5b.
            # ════════════════════════════════════════════════════════════════
            _campo_ha_deposto = bool(_verbale.get("campo_veto"))

            if not result['enter']:
                if _campo_ha_deposto:
                    # CASO A — il Campo ha deposto: score=0 è artefatto del veto.
                    # Ricostruisco numeri di base così SC ha qualcosa da valutare.
                    # seed['score'] è 0-1 → scala a 0-100 come score di partenza.
                    score  = round(float(seed.get('score', 0.0)) * 100.0, 1)
                    soglia = float(getattr(self, '_m2_soglia_base', 40))
                    _verbale["score"]  = score
                    _verbale["soglia"] = soglia
                    _verbale["score_ricostruito"] = True
                    self._log_m2("🏛️", f"CAMPO_VETO_A_SC: campo_veto={_verbale['campo_veto']} "
                                       f"score_ricostruito={score:.1f}/{soglia:.1f} → SC decide")
                    # NESSUN return — il flow prosegue a SC (ZONA 3)
                else:
                    # CASO B — score genuinamente sotto soglia, nessun veto campo.
                    # ════════════════════════════════════════════════════════════
                    # SCAVALCO MASCHIO (21giu, Roberto: "togliamo lo score, non
                    # ha più lo stesso valore nel nuovo modello"). Se il CANCELLO
                    # ha confermato il maschio (N mosse su consecutive), lo score
                    # NON deve bloccarlo: lo score è del vecchio mondo, eroso da
                    # VERITAS/capsule/CM_LEARNED a 20-29 sotto soglia. Il maschio
                    # entra sul MOVIMENTO confermato, non sullo score. Il return
                    # sotto uccideva i maschi (log: SCORE_SOTTO 20-29 vs 34).
                    # ENV SCORE_SCAVALCA_MASCHIO (default true) per spegnerlo.
                    # ════════════════════════════════════════════════════════════
                    _scava = os.environ.get("SCORE_SCAVALCA_MASCHIO", "true").lower() in ("true","1","yes")
                    _e_maschio = bool(getattr(self, "_canc_maschio_ok", False))
                    if _scava and _e_maschio:
                        self._log_m2("🐺", f"MASCHIO SCAVALCA SCORE_SOTTO: {score:.1f} vs {soglia:.1f} "
                                           f"— maschio confermato, lo score NON lo ferma → prosegue")
                        # NESSUN return — il maschio prosegue verso l'entrata
                    else:
                        # Dittatore SCORE_SOTTO — rinviato a 5b da decisione Roberto.
                        self._log_m2("🔇", f"SCORE_SOTTO: {score:.1f} vs {soglia:.1f}")
                        if score > 50 and len(self._phantoms_open) < 5:
                            self._record_phantom(price, f"SCORE_{score:.0f}_vs_{soglia:.0f}",
                                                 seed['score'], momentum, volatility, trend)
                        _verbale["blocked_by"] = "DITTATORE_SCORE_SOTTO_rinviato_5b"
                        self._log_constitutional(_verbale, "PRE_SC_VETO_SCORE_SOTTO_5B_PENDING")
                        return

            size = result.get('size', 0.3)
            # Se il Campo ha deposto, result['size'] è 0.0 (artefatto _veto).
            # Uso una size di base prudente — SC potrà modularla.
            if _campo_ha_deposto and size <= 0.0:
                size = 0.3

            # ════════════════════════════════════════════════════════════════
            # VOLPE BOOST — riconosci i contesti dove devi colpire forte
            # ════════════════════════════════════════════════════════════════
            # Dai trade vincenti storici (STATUS.md):
            # - Trade 764: FORTE|ALTA|SIDEWAYS pnl=+$2.05 → bonus moderato
            # - Trade 516: DEBOLE|BASSA|SIDEWAYS pnl=+$6.22 → bonus alto
            # - Trade 557/655: MEDIO|ALTA|SIDEWAYS pnl=+$1.37/$1.47 → standard
            #
            # Pattern profittevoli identificati:
            # 1. FORTE + UP/MEDIA (impulso vivo + volume)        → +30% size
            # 2. DEBOLE + BASSA + SIDEWAYS (range dead, lungo)   → +50% size
            # 3. FORTE + ALTA + SIDEWAYS (squeeze breakout)      → +20% size
            #
            # Veritas storico permette il boost SOLO se WR>=20% o n<5 (no abusi)
            _volpe_boost = 1.0
            _volpe_reason = ""
            _ctx_v = self._m2_ctx_stats.get(f"{momentum}|{volatility}|{trend}", {})
            _v_n = _ctx_v.get('n', 0)
            _v_wr = (_ctx_v.get('wins', 0) / _v_n) if _v_n > 0 else 0.5
            
            # Boost solo se contesto non è dimostrato perdente
            if _v_n < 5 or _v_wr >= 0.20:
                if momentum == "FORTE" and trend == "UP" and volatility in ("MEDIA","ALTA"):
                    _volpe_boost = 1.30
                    _volpe_reason = "FORTE_UP_VOL"
                elif momentum == "DEBOLE" and volatility == "BASSA" and trend == "SIDEWAYS":
                    _volpe_boost = 1.50
                    _volpe_reason = "RANGE_DEAD_LONG"
                elif momentum == "FORTE" and volatility == "ALTA" and trend == "SIDEWAYS":
                    _volpe_boost = 1.20
                    _volpe_reason = "SQUEEZE_BREAK"
            
            if _volpe_boost > 1.0:
                _new_size = round(min(2.0, size * _volpe_boost), 2)
                self._log_m2("🦊", f"VOLPE_BOOST {_volpe_reason} {size:.2f}→{_new_size:.2f}")
                size = _new_size

            # ── SUPERCERVELLO: sintesi finale prima dell'entry ──────────
            # È il giudice che legge tutti gli organi simultaneamente.
            # Se dice BLOCCA → non entra. Se dice ENTRA → può modificare size.
            _ph_stats  = self._phantom_stats.get('SCORE_INSUFFICIENTE', {})
            _ph_prot   = _ph_stats.get('would_lose', 0)
            _ph_zav    = _ph_stats.get('would_win', 0)
            _mat       = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
            _st_ctx    = self._get_signal_tracker_context(self._regime_current, score)
            _midzone   = False
            if len(self.campo._prices_long) >= 200:
                _pr = list(self.campo._prices_long)[-200:]
                _rh, _rl = max(_pr), min(_pr)
                if _rh > _rl:
                    _rpos = (price - _rl) / (_rh - _rl)
                    _midzone = 0.40 <= _rpos <= 0.60

            # ════════════════════════════════════════════════════════════════
            # FIX #32 (12mag2026 sera): pre-fetch WR direzione OPPOSTA per SC
            # ════════════════════════════════════════════════════════════════
            # Permette a SC di proporre flip se direzione opposta ha edge 
            # statistico nettamente migliore (delta>=15% WR, n_opp>=20).
            # ════════════════════════════════════════════════════════════════
            _opposite_dir = 'SHORT' if _dir == 'LONG' else 'LONG'
            _fp_wr_opp = 0.0
            _fp_n_opp = 0
            try:
                _opp_fp_key = self.oracolo._fp(momentum, volatility, trend, _opposite_dir)
                _opp_data = self.oracolo._memory.get(_opp_fp_key, None)
                if _opp_data:
                    _opp_samples = float(_opp_data.get('samples', 0))
                    _opp_wins    = float(_opp_data.get('wins', 0))
                    if _opp_samples > 0:
                        _fp_wr_opp = _opp_wins / _opp_samples
                        _fp_n_opp  = int(_opp_samples)
            except Exception:
                pass

            # ════════════════════════════════════════════════════════════════
            # ZONA 3 — L'ESPERTO ESAMINA (SC, e solo SC)
            # SC riceve il verbale costituzionale completo. In 5a SC decide
            # ancora con la LOGICA DI OGGI — i nuovi input li riceve e li
            # logga (SC_INPUTS_FULL), non li usa. L'attivazione è 5b.
            # ════════════════════════════════════════════════════════════════
            _sc_dec = self.supercervello.decide(
                fp_wr          = fingerprint_wr,
                fp_samples     = self.oracolo._memory.get(
                                   self.oracolo._fp(momentum, volatility, trend, _dir),
                                   {}).get('real_samples', 0),
                st_hit_rate    = _st_ctx['hit_rate'],
                st_n           = _st_ctx['n'],
                st_pnl         = _st_ctx['pnl_sim'],
                oi_carica      = self._oi_carica,
                oi_stato       = self._oi_stato,
                # PASSO 11: passa anche l'OI SHORT per il pred_boost bilaterale
                oi_carica_short= self._oi_carica_short,
                oi_stato_short = self._oi_stato_short,
                score          = score,
                soglia         = soglia,
                matrimonio_wr  = _mat.get('wr', 0.5),
                matrimonio_trust = self.memoria.get_trust(_mat.get('name', '')),
                ph_protezione  = _ph_prot,
                ph_zavorra     = _ph_zav,
                regime         = self._regime_current,
                midzone        = _midzone,
                loss_streak    = self._m2_loss_consecutivi(),
                fp_wr_opposite     = _fp_wr_opp,    # FIX #32
                fp_samples_opposite= _fp_n_opp,     # FIX #32
                current_direction  = _dir,          # FIX #32
                # ── VERBALE COSTITUZIONALE (Passo 5a — ricevuto e loggato) ──
                tsunami_vote          = _verbale.get('tsunami_vote'),
                tsunami_confidence    = _verbale.get('tsunami_confidence'),
                tsunami_direction     = _verbale.get('tsunami_direction'),
                tsunami_reason        = _verbale.get('tsunami_reason'),
                veritas_ctx_wr        = _verbale.get('veritas_ctx_wr'),
                veritas_ctx_samples   = _verbale.get('veritas_ctx_samples'),
                veritas_ctx_pnl_avg   = _verbale.get('veritas_ctx_pnl_avg'),
                veritas_last_judgement= _verbale.get('veritas_last_judgement'),
                capsule_block_score   = _verbale.get('capsule_block_score'),
                capsule_boost_score   = _verbale.get('capsule_boost_score'),
                capsule_reasons       = _verbale.get('capsule_reasons'),
                capsule_oracolo_override = _verbale.get('capsule_oracolo_override'),
                proposed_direction    = _verbale.get('proposed_direction'),
                flip_confidence       = _verbale.get('flip_confidence'),
                breath_fase           = _verbale.get('breath_fase'),
                breath_energia        = _verbale.get('breath_energia'),
                sc_inputs_full        = True,
            )
            self._last_sc_dec = _sc_dec

            # Log costituzionale: la decisione finale di SC
            _verbale["sc_decision"] = _sc_dec['azione']
            self._log_constitutional(_verbale, f"SC_DECISION_FINAL_{_sc_dec['azione']}")

            if _sc_dec['azione'] == 'BLOCCA':
                self._log_m2("🚫", f"SC_BLOCCA: {_sc_dec['motivo']}")
                self._record_phantom(price, f"SC_BLOCCA_{_sc_dec['motivo'][:20]}",
                                     seed['score'], momentum, volatility, trend)
                if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                    return
                self._log_m2("👁", "OBSERVER: SC_BLOCCA avrebbe bloccato — LASCIO PASSARE")

            # ════════════════════════════════════════════════════════════════
            # FIX #32 (12mag2026 sera): FLIP_BY_SUPERCERVELLO
            # ════════════════════════════════════════════════════════════════
            # Se SC ha emesso direction_vote opposto con confidenza alta, 
            # forza flip del campo PRIMA dell'open. Soglia confidenza: 0.30 
            # (= WR_opposite >= ~75%).
            # Re-fetch fingerprint per la nuova direzione (size/WR coerenti).
            # ════════════════════════════════════════════════════════════════
            _sc_dir_vote = _sc_dec.get('direction_vote')
            _sc_dir_conf = _sc_dec.get('direction_vote_confidence', 0.0)
            if _sc_dir_vote and _sc_dir_vote != _dir and _sc_dir_conf >= 0.30:
                self._log_m2("🔄", f"FLIP_BY_SC: {_dir} → {_sc_dir_vote} "
                                   f"(conf={_sc_dir_conf:.2f}) — {_sc_dec.get('motivo','')[-40:]}")
                self.campo._direction = _sc_dir_vote
                _dir = _sc_dir_vote
                # Re-fetch fingerprint_wr per la NUOVA direzione
                try:
                    fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend, _dir)
                    self._last_fingerprint_wr = fingerprint_wr
                except Exception:
                    pass

            # SC può modificare size
            size = round(min(2.0, max(0.30, size * _sc_dec.get('size_mult', 1.0))), 2)

            self._log_m2("🚀", f"ENTRY {_dir} score={score:.1f}/{soglia:.1f} "
                               f"size={size:.2f} sc={_sc_dec['azione']} "
                               f"regime={self._regime_current}")

            # ════════════════════════════════════════════════════════════════
            # PATCH 10 BUG 17c — Fresh Vote Guard (P2)
            # ════════════════════════════════════════════════════════════════
            # Legge madre: NESSUNA ENTRY SENZA VOTO FRESCO.
            # Anche nel PERCORSO 2 (standard SC), se per qualche motivo
            # arriviamo qui con score<=0 o soglia<=0, blocchiamo.
            # ════════════════════════════════════════════════════════════════
            if score <= 0 or soglia <= 0:
                self._log_m2("🛑", f"ENTRY_BLOCKED_NO_FRESH_SCORE score={score} "
                                   f"soglia={soglia} path=P2_STANDARD")
                _verbale["blocked_by"] = "PATCH10_NO_FRESH_SCORE_P2"
                self._log_constitutional(_verbale, "PRE_OPEN_VETO_NO_FRESH_SCORE_P2")
                return
            # ════════════════════════════════════════════════════════════════
            # PATCH 11 BUG 18d — Post-Win Rebalance Gate (P2)
            # ════════════════════════════════════════════════════════════════
            # Stessa logica di P1: dopo un WIN_NET significativo, per 5min,
            # se fingerprint uguale al WIN e score in degrado → blocca.
            # ════════════════════════════════════════════════════════════════
            POST_WIN_REBALANCE_ENABLED = True
            POST_WIN_WINDOW_SEC        = 300
            POST_WIN_SCORE_TOLERANCE   = 5.0
            try:
                if (POST_WIN_REBALANCE_ENABLED
                    and self._last_win_score is not None
                    and self._last_win_fingerprint is not None
                    and self._last_win_ts is not None):
                    _now_pw = time.time()
                    _dt_pw  = _now_pw - self._last_win_ts
                    if _dt_pw <= POST_WIN_WINDOW_SEC:
                        _curr_fp = f"{momentum}|{volatility}|{trend}|{_dir}"
                        if _curr_fp == self._last_win_fingerprint:
                            _gate_score = self._last_win_score - POST_WIN_SCORE_TOLERANCE
                            if score < _gate_score:
                                self._log_m2("🚧",
                                    f"POST_WIN_REENTRY_BLOCKED score_now={score:.1f} "
                                    f"last_win_score={self._last_win_score:.1f} "
                                    f"gate={_gate_score:.1f} fp={_curr_fp} "
                                    f"dt={_dt_pw:.0f}s reason=ENV_NOT_REBUILT path=P2")
                                _verbale["blocked_by"] = "PATCH11_POST_WIN_ENV_NOT_REBUILT_P2"
                                self._log_constitutional(_verbale, "PRE_OPEN_VETO_POST_WIN_REBALANCE_P2")
                                return
                        else:
                            self._log_m2("🆕",
                                f"POST_WIN_REENTRY_ALLOWED_NEW_FINGERPRINT "
                                f"curr={_curr_fp} last={self._last_win_fingerprint} "
                                f"dt={_dt_pw:.0f}s path=P2")
            except Exception as _e_pwg2:
                pass
            # ════════════════════════════════════════════════════════════════

            # ════════════════════════════════════════════════════════════════
            # PATCH 12 BUG 19b — Drift Magnitude Entry Filter (P2)
            # ════════════════════════════════════════════════════════════════
            # Stessa logica di P1: se |drift| < 0.006 = zona morta = blocca.
            # Filtro certificato su 41 trade reali da winning_signatures.
            # ════════════════════════════════════════════════════════════════
            DRIFT_MIN_MAGNITUDE = 0.006
            try:
                _p12_drift = 0.0
                if len(self.campo._prices_long) >= 100:
                    _p_p12 = list(self.campo._prices_long)
                    _avg_old_p12 = sum(_p_p12[:50]) / 50
                    _avg_new_p12 = sum(_p_p12[-50:]) / 50
                    if _avg_old_p12 > 0:
                        _p12_drift = (_avg_new_p12 - _avg_old_p12) / _avg_old_p12 * 100
                if abs(_p12_drift) < DRIFT_MIN_MAGNITUDE:
                    self._log_m2("🚧",
                        f"ENTRY_BLOCKED_FLAT_DRIFT drift={_p12_drift:+.4f} "
                        f"threshold={DRIFT_MIN_MAGNITUDE} path=P2_STANDARD "
                        f"reason=ZONA_MORTA_MERCATO")
                    _verbale["blocked_by"] = "PATCH12_FLAT_DRIFT"
                    self._log_constitutional(_verbale, "PRE_OPEN_VETO_FLAT_DRIFT_P2")
                    if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                        return
                    self._log_m2("👁", "OBSERVER: FLAT_DRIFT P2 avrebbe bloccato — LASCIO PASSARE")
            except Exception as _e_p12_p2:
                pass
            # ════════════════════════════════════════════════════════════════

            # ════════════════════════════════════════════════════════════════
            # PATCH 20 — CONSULTO CAPSULA MATRIGNA (Percorso 2)
            # ════════════════════════════════════════════════════════════════
            if getattr(self, "matrigna", None) is not None:
                try:
                    _mat_tid_p2 = _verbale.get("_canvas_tid") or f"t_{int(time.time()*1000)}_P2"
                    _mat_ok, _mat_motivo, _mat_info = self.matrigna.consulta(
                        momentum=momentum,
                        volatility=volatility,
                        trend=trend,
                        regime=self._regime_current,
                        direction=self.campo._direction,
                        trade_id=_mat_tid_p2,
                    )
                    _verbale["matrigna_motivo"] = _mat_motivo
                    _verbale["matrigna_stato"] = _mat_info.get('stato')
                    _verbale["matrigna_verdetto"] = _mat_info.get('verdetto')
                    if not _mat_ok and self.matrigna.is_blocking():
                        self._log_m2("👵", f"MATRIGNA BLOCCA P2: {_mat_motivo}")
                        _verbale["blocked_by"] = f"MATRIGNA:{_mat_info.get('firma_key','?')}"
                        self._log_constitutional(_verbale, "PRE_OPEN_VETO_MATRIGNA_P2")
                        if os.environ.get("CANCELLI_OBSERVER", "false").lower() != "true":
                            return
                        self._log_m2("👁", "OBSERVER: MATRIGNA P2 avrebbe bloccato — LASCIO PASSARE")
                    elif "OBSERVER_WOULD_BLOCK" in _mat_motivo:
                        self._log_m2("👁", f"MATRIGNA suggerirebbe blocco (OBSERVER P2): {_mat_motivo}")
                except Exception as _e_mat_p2:
                    log.debug(f"[MATRIGNA_HOOK_P2_ERR] {_e_mat_p2}")

            # ════════════════════════════════════════════════════════════════
            # SINAPSI LOOKUP — FASE 2 (13giu2026, Roberto)
            # ════════════════════════════════════════════════════════════════
            # Phantom non e' post-mortem: e' tessuto sinaptico. Qui, prima
            # dell'apertura del trade, interroghiamo phantom_forensic per
            # vedere come sono andati casi storici con firma simile.
            # SHADOW PURO: logga e basta, NON modifica la decisione.
            # ENV control: SINAPSI_LOOKUP_ENABLED (default false)
            # Documento audit: /mnt/user-data/outputs/AUDIT_SINAPSI_13GIU2026.md
            # ════════════════════════════════════════════════════════════════
            try:
                if os.environ.get("SINAPSI_LOOKUP_ENABLED", "false").lower() == "true":
                    _sin_days = int(os.environ.get("SINAPSI_LOOKUP_DAYS", "7"))
                    _sin_min_n = int(os.environ.get("SINAPSI_LOOKUP_MIN_N", "20"))
                    _sin_dir = self.campo._direction
                    _sin_reg = self._regime_current
                    _sin_cutoff = time.time() - (_sin_days * 86400)
                    _sin_conn = None
                    try:
                        import sqlite3 as _sql_sin
                        _sin_conn = _sql_sin.connect(self.db_path, timeout=5)
                        _sin_conn.execute("PRAGMA busy_timeout=5000;")
                        # Lookup: casi storici con firma identica (regime/direction/momentum/volatility/trend)
                        _sin_q = """SELECT COUNT(*) AS n,
                                           SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) AS wins,
                                           AVG(mfe_usd) AS peak_medio,
                                           AVG(mae_usd) AS crollo_medio
                                    FROM phantom_forensic
                                    WHERE ts_entry >= ?
                                      AND regime = ?
                                      AND direction = ?
                                      AND momentum = ?
                                      AND volatility = ?
                                      AND trend = ?
                                      AND mfe_usd IS NOT NULL"""
                        _sin_cur = _sin_conn.execute(_sin_q, (_sin_cutoff, _sin_reg, _sin_dir,
                                                              momentum, volatility, trend))
                        _sin_row = _sin_cur.fetchone()
                        _sin_n = _sin_row[0] if _sin_row else 0
                        _sin_wins = _sin_row[1] if _sin_row else 0
                        _sin_peak = _sin_row[2] if _sin_row else None
                        _sin_crollo = _sin_row[3] if _sin_row else None
                        _sin_wr = (_sin_wins / _sin_n) if _sin_n > 0 else None
                        _sin_firma = f"{_sin_reg}|{_sin_dir}|{momentum}|{volatility}|{trend}"
                        # Logga sempre se ha trovato campione minimo
                        if _sin_n >= _sin_min_n:
                            self._log_m2("🧬",
                                f"SINAPSI: firma={_sin_firma} n={_sin_n} "
                                f"WR_storico={_sin_wr:.0%} peak_medio={_sin_peak:.2f}$ "
                                f"crollo_medio={_sin_crollo:.2f}$")
                            # Avviso speciale se contesto storicamente sfavorevole
                            if _sin_wr is not None and _sin_wr < 0.25 and _sin_n >= 10:
                                self._log_m2("⚠️",
                                    f"SINAPSI_AVVISO: contesto storicamente sfavorevole "
                                    f"WR={_sin_wr:.0%} su n={_sin_n} casi (peak medio {_sin_peak:.2f}$)")
                        # Registra l'osservazione su tabella dedicata
                        _sin_conn.execute("""CREATE TABLE IF NOT EXISTS sinapsi_observations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ts REAL,
                            firma TEXT,
                            n_storici INTEGER,
                            wr_storico REAL,
                            peak_medio_storico REAL,
                            crollo_medio_storico REAL,
                            bot_decisione TEXT,
                            entry_price REAL,
                            score REAL,
                            soglia REAL
                        )""")
                        _sin_conn.execute("""INSERT INTO sinapsi_observations
                            (ts, firma, n_storici, wr_storico, peak_medio_storico,
                             crollo_medio_storico, bot_decisione, entry_price, score, soglia)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (time.time(), _sin_firma, _sin_n, _sin_wr, _sin_peak,
                             _sin_crollo, "ENTERING", float(price), float(score), float(soglia)))
                        _sin_conn.commit()
                    except Exception as _e_sin:
                        log.debug(f"[SINAPSI_ERR] {_e_sin}")
                    finally:
                        if _sin_conn is not None:
                            try:
                                _sin_conn.close()
                            except Exception:
                                pass
            except Exception:
                pass  # mai bloccare il bot per errori sinapsi

            # FIX 23giu (Roberto): questo e' l'ingresso SCORE-BASED (vecchio mondo):
            # apre se score > soglia, SENZA guardare il grasso. La femmina delle
            # 12:00 (score 36.84>34, peak 0.0, -2.49, build 14a78c48) e' entrata
            # DA QUI, non da P1 (gia' spento). Interruttore SCORE_ENTRY_OFF=true
            # -> entra SOLO MASCHIO_DIRETTO (grasso reale). Reversibile.
            # FIX 24giu (Roberto "metal detector"): porta SCORE-BASED laterale.
            # Da QUI entrava la femmina delle 12:00 (score 36.84, peak 0). Default
            # era false=APERTA. Con MACCHINA_PURA NON apre MAI, cablato.
            _pura_sc = os.environ.get("MACCHINA_PURA", "true").lower() == "true"
            if _pura_sc or os.environ.get("SCORE_ENTRY_OFF", "true").lower() == "true":
                # log silenziato 24giu (Roberto): stampava a OGNI candidato debole,
                # decine di righe inutili che intasavano i log e nascondevano gli
                # eventi veri (maschi, trans, trade). La porta e' chiusa per design.
                return
            self._open_shadow_position(price, score, soglia, seed, size,
                                        momentum, volatility, trend,
                                        matrimonio_name, fingerprint_wr)

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE shadow_entry: {e}")
            log.error(f"[M2_ENTRY_ERROR] {e}\n{traceback.format_exc()}")
            try:
                self._last_entry_tick = 0
                if self._shadow:
                    self._shadow = None
            except Exception:
                pass

    def _open_shadow_position(self, price, score, soglia, seed, size,
                               momentum, volatility, trend,
                               matrimonio_name, fingerprint_wr):
        """
        Apre una shadow position (paper trade M2).
        Registra tutti i dati necessari per l'exit e il tracking.
        """
        try:
            # FIX 24giu (Roberto): MASCHIO_DIRETTO passa seed come FLOAT (0.3), ma
            # questa funzione fa seed.get(...) in vari punti -> CRASH 'float object
            # has no attribute get' -> il trade NON si apriva (OPEN_SHADOW_ERROR).
            # Normalizzo: se seed e' un float/numero, lo avvolgo in dict.
            if not isinstance(seed, dict):
                seed = {"score": float(seed) if seed is not None else 0.0}
            if getattr(self, "_maschio_diretto_in_corso", False):
                self._log_m2("🔍", f"OPEN chiamata (maschio): price={price:.1f} score={score} — entro nella funzione")
            # ════════════════════════════════════════════════════════════════
            # 🐺 CANCELLO SALITA @ APERTURA (18giu2026, Roberto — IL VERO COLLO
            #     DI BOTTIGLIA, blindato). PRIMA di TUTTO, anche del Grande Fratello.
            # ════════════════════════════════════════════════════════════════
            # SCOPERTA DAI DATI (18giu, trans 17:11 picco 0.0 dur 8.9s entrato e
            # morto -2.05): il cancello messo PRIMA della chiamata a
            # _evaluate_shadow_entry NON bastava — l'evaluate apriva dai suoi
            # percorsi interni (P1/P2) prima che il collasso si manifestasse.
            # QUI invece passano TUTTI i trade per nascere, qualunque percorso
            # (P1, P2, explosive, ranging): e' l'UNICO collo di bottiglia.
            #
            # REGOLA (Roberto): il maschio SALE. Al momento di aprire ha gia' un
            # picco POSITIVO osservato (_rit_picco_pre, lordo, esposizione zero).
            # La femmina/trans NON e' mai salita: picco osservato ~0. Se in tutta
            # l'osservazione il candidato non ha mai messo grasso sopra la soglia,
            # NON e' un maschio -> NON apre. Niente timer: o e' salito o no.
            #
            # ENV:
            #   CANCELLO_APERTURA_OFF (default false = attivo)
            #   CANCELLO_PICCO_MIN_USD (default 0.30) = grasso LORDO minimo che il
            #       candidato deve aver toccato in osservazione per essere maschio.
            #       Basso apposta: non e' il 2.50, e' solo "ha dato un segno di
            #       vita salendo". Chi ha picco 0.0 (capra piatta) viene tagliato.
            #       Si alza se passano ancora piatte, si abbassa se taglia maschi.
            # try/except totale, FAIL-OPEN: mai bloccare per errore del cancello.
            # ════════════════════════════════════════════════════════════════
            if os.environ.get("CANCELLO_APERTURA_OFF", "false").lower() != "true" and not getattr(self, "_maschio_diretto_in_corso", False):
                try:
                    # ════════════════════════════════════════════════════════════
                    # FILTRO MASCHIO/FEMMINA (19giu2026, Roberto) — REGOLA PURA.
                    # "Il ritardo non esiste piu'. C'e' solo l'osservazione del
                    #  movimento nel tempo. Il maschio sale costante e continuo
                    #  (anche piano, non serve un grasso minimo): fa nuovi massimi
                    #  -> passa. La femmina/trans magari sale o sta ferma, ma poi
                    #  si sgonfia, comincia a perdere -> la taglio, prima del gate."
                    #
                    # La decisione e' GIA' stata presa dal CANCELLO SALITA (in
                    # _process_tick_body), che a ogni tick osserva il candidato dal
                    # segnale: nuovo massimo = sale (vivo), troppi tick senza nuovo
                    # massimo = sgonfiata (morta). Qui leggo quello stato:
                    #   _canc_max_usd     = grasso massimo salito in osservazione
                    #   _canc_tick_da_max = tick senza nuovo massimo (sgonfiamento)
                    # Fonte SEMPRE valida (gira dal segnale, non dal ritardo): e'
                    # quella che vede i maschi a +6/+27/+315 — NON piu' _rit_picco_pre
                    # (cieca quando il candidato salta il ritardo -> buttava maschi).
                    #
                    # TAGLIO se: non e' mai salito (_canc_max_usd ~0) OPPURE si e'
                    #            sgonfiato (tick senza nuovo massimo >= respiro).
                    # PASSA se:  sta salendo (ha messo grasso e fa ancora nuovi massimi).
                    # Niente soglia di valore: basta che salga e non si sgonfi.
                    # try/except FAIL-OPEN: mai bloccare per errore del filtro.
                    # ════════════════════════════════════════════════════════════
                    _grasso  = getattr(self, "_canc_max_usd", None) or getattr(self, "_rit_picco_pre", None) or 0.0
                    _sgonfio = getattr(self, "_canc_tick_da_max", None) or 0
                    _respiro = int(float(os.environ.get("CANCELLO_RESPIRO_TICK", "40")))
                    # grasso CORRENTE ora (per misurare la discesa vera dal picco)
                    try:
                        _delta_ora = (price - self._rit_prezzo_aggancio) if getattr(self, "_rit_prezzo_aggancio", 0) else 0.0
                        _exp_ora   = float(os.environ.get("EXPOSURE", "5000"))
                        _grasso_ora = _delta_ora * (_exp_ora / self._rit_prezzo_aggancio) if getattr(self, "_rit_prezzo_aggancio", 0) else 0.0
                    except Exception:
                        _grasso_ora = _grasso
                    # mai salito: nessun grasso positivo in tutta l'osservazione
                    _mai_salito = (_grasso <= 0.0)
                    # ════════════════════════════════════════════════════════════
                    # FIX 21giu (Roberto): un MASCHIO salito a grasso alto (+8/+12)
                    # che fa PAUSA in alto NON e' una femmina — e' un maschio a cui
                    # va preso il grasso. Il vecchio "_sgonfio >= respiro" lo
                    # tagliava solo perche' fermo N tick, anche se restava a +12.
                    # ORA: si sgonfia (femmina) SOLO se il grasso CORRENTE e' sceso
                    # sotto una frazione del picco (discesa vera). Un maschio che
                    # tiene il grasso in alto PASSA. ENV CANCELLO_TIENI_PCT (default
                    # 0.60): finche' il grasso corrente >= 60% del picco, e' vivo.
                    # ════════════════════════════════════════════════════════════
                    _tieni_pct = float(os.environ.get("CANCELLO_TIENI_PCT", "0.60"))
                    _e_sceso = (_grasso > 0.0 and _grasso_ora < _grasso * _tieni_pct)
                    _si_sgonfia = (_sgonfio >= _respiro) and _e_sceso
                    # ════════════════════════════════════════════════════════════
                    # RISCHIA (21giu, Roberto: "anche se sono trans vanno portati
                    # dentro e preso il grasso, è inconcepibile lasciarlo"). Un
                    # candidato salito a un grasso minimo (CANCELLO_GRASSO_MIN, def
                    # 2.0$) NON va tagliato: va portato dentro e gli si prende il
                    # grasso col trailing, qualunque etichetta abbia. Non si
                    # distingue maschio da trans all'entrata (firma ambigua): se
                    # sale e tiene il grasso minimo, ENTRA. Il trailing fa il resto.
                    # ════════════════════════════════════════════════════════════
                    # FIX 23giu sera (Roberto): questo cancello usava CANCELLO_GRASSO_MIN,
                    # ma quel valore ora vale 3 (gate FIRMA in osservazione). Usandolo
                    # QUI strozzava l'apertura: il maschio al tick d'ingresso ha grasso
                    # istantaneo piccolo (deve ancora salire) -> tagliato -> 0 trade.
                    # La MACCHINA filtra in USCITA (PRESA_TRANS +3, stop -1), NON qui.
                    # Quindi cancello apertura usa soglia PROPRIA bassa: entra chi ha
                    # un minimo di grasso vivo, il resto lo decide l'uscita.
                    _grasso_min = float(os.environ.get("CANCELLO_APERTURA_MIN", "0.0"))
                    # ════════════════════════════════════════════════════════════
                    # REGOLA ROBERTO (22giu sera): NON guardare il PICCO. Guarda il
                    # GRASSO CHE HA ADESSO IN MANO (_grasso_ora). Se adesso ha grasso
                    # >= soglia (def 2.0$, sopra le fee) -> ENTRA SUBITO e castra,
                    # PROPRIO mentre si svuota dal massimo: e' il momento di prenderlo,
                    # non di buttarlo. Il vecchio 75% faceva il contrario (vedeva
                    # calare e tagliava) = buttava il grasso. ELIMINATO.
                    # Si taglia SOLO chi ADESSO ha grasso sotto soglia o e' a zero.
                    # ════════════════════════════════════════════════════════════
                    _ha_grasso_vivo = (_grasso_ora >= _grasso_min)
                    if _ha_grasso_vivo:
                        # ha grasso vivo in mano ADESSO -> ENTRA, castra subito
                        _si_sgonfia = False
                        _mai_salito = False
                        _taglia = False
                    else:
                        # adesso non ha grasso sopra soglia -> taglia (femmina/piatta
                        # o gia' svuotato sotto la soglia che vale le fee)
                        _taglia = True
                    if _taglia:
                        _motivo_f = (f"grasso ora {_grasso_ora:.2f}$ < {_grasso_min:.2f}$ (sotto soglia/svuotato)")
                        self._log_m2("🐺",
                            f"FILTRO M/F: max salita +{_grasso:.2f}$ — "
                            f"{_motivo_f} = femmina/trans, NON apre")
                        try:
                            self._ritardo_stats["cancello_apertura_scartati"] = \
                                self._ritardo_stats.get("cancello_apertura_scartati", 0) + 1
                        except Exception:
                            pass
                        try:
                            self._record_phantom(price, "MINA_CANCELLO_SALITA",
                                                 float(seed.get("score", 0) or 0) if isinstance(seed, dict) else 0.0,
                                                 str(momentum), str(volatility), str(trend))
                        except Exception:
                            pass
                        return  # NON APRE — femmina/trans
                except Exception as _e_canc_ap:
                    log.debug(f"[CANCELLO_APERTURA_ERR] {_e_canc_ap}")
                    # FAIL-OPEN: in caso di errore prosegue (non blocca)

            # ════════════════════════════════════════════════════════════════
            # 🛡️ GRANDE FRATELLO DIREZIONE (GF) — 8giu, Roberto
            # ════════════════════════════════════════════════════════════════
            # Regola UNICA, sopra a tutto: se la spinta del mercato è giù
            # (drift < soglia) e il campo vuole entrare LONG → NON SI ENTRA.
            # Punto. Non importa maschio/femmina/trans: se la direzione è
            # sbagliata, si sta fuori. Filtro di DIREZIONE, non di qualità.
            # È PRIMA di tutto (SEME/CROMO/ritardo) e NON viene deposto.
            # Drift calcolato sul momento da _prices_long (stessa formula del
            # campo, riga ~4975) per non dipendere da _last_drift (che a volte
            # non esiste). Reversibile e tarabile da ENV:
            #   GF_DIREZIONE_OFF=true  → guardiano spento (default false=attivo)
            #   GF_DRIFT_SOGLIA=-0.05  → soglia % (più vicino a 0 = più severo)
            # ════════════════════════════════════════════════════════════════
            # FIX 24giu (Roberto): GF_DIREZIONE e' un guardiano VECCHIO dentro
            # _open_shadow_position che bloccava il maschio DOPO "ENTRA SUBITO"
            # (logga ma non apre). Con MACCHINA_PURA non interviene.
            # FIX 24giu: il Grande Fratello (blocco LONG-only + spia FUORI_SHORT/LIBERO)
            # gira SEMPRE, anche in MACCHINA_PURA. La regola "in short si sta fuori"
            # NON e' vecchio cervello: e' fondamentale (Roberto). Prima _pura_gf la
            # saltava -> il bot apriva LONG mentre il mercato colava. Ora no.
            if os.environ.get("GF_DIREZIONE_OFF", "false").lower() != "true":
                _gf_dir = getattr(self.campo, "_direction", "LONG")
                if _gf_dir == "LONG":
                    _gf_soglia = float(os.environ.get("GF_DRIFT_SOGLIA", "-0.05"))
                    _gf_prices = list(getattr(self.campo, "_prices_long", []))
                    if len(_gf_prices) >= 100:
                        _gf_old = sum(_gf_prices[:50]) / 50
                        _gf_new = sum(_gf_prices[-50:]) / 50
                        _gf_drift = (_gf_new - _gf_old) / _gf_old * 100 if _gf_old else 0.0
                        if _gf_drift < _gf_soglia:
                            _now_gf = time.time()
                            if _now_gf - getattr(self, "_gf_last_log", 0.0) > 30:
                                self._gf_last_log = _now_gf
                                print(f"[GF] 🛡️ FUORI: spinta ribassista "
                                      f"drift={_gf_drift:+.3f}% < {_gf_soglia}% "
                                      f"(LONG bloccato — direzione sbagliata)")
                            try:
                                self._ritardo_stats["gf_fuori"] = self._ritardo_stats.get("gf_fuori", 0) + 1
                            except Exception:
                                pass
                            # espone lo stato alla dashboard: "fuori perché SHORT"
                            try:
                                if self.heartbeat_data is not None:
                                    self.heartbeat_data["gf_stato"] = "FUORI_SHORT"
                                    self.heartbeat_data["gf_drift"] = round(_gf_drift, 3)
                                    self.heartbeat_data["gf_soglia"] = _gf_soglia
                                    self.heartbeat_data["gf_ts"] = datetime.utcnow().isoformat()
                                    self.heartbeat_data["gf_fuori_count"] = self._ritardo_stats.get("gf_fuori", 0)
                            except Exception:
                                pass
                            return  # NON ENTRA — direzione giù
                        else:
                            # direzione OK in questo aggancio: segnala "campo libero"
                            try:
                                if self.heartbeat_data is not None:
                                    self.heartbeat_data["gf_stato"] = "LIBERO"
                                    self.heartbeat_data["gf_drift"] = round(_gf_drift, 3)
                                    self.heartbeat_data["gf_ts"] = datetime.utcnow().isoformat()
                            except Exception:
                                pass

            # ════════════════════════════════════════════════════════════════
            # SEME_GATE (3giu, Roberto) — EVITARE LE FEMMINE PRIMA DELLA NASCITA
            # ════════════════════════════════════════════════════════════════
            # Scoperta sui dati (44 trade reali, 1-2giu): il "sesso" del trade è
            # già nel SEME prima che nasca. Maschi (WIN) nascono da seme medio
            # ~0.62 e tengono; femmine (LOSS) da ~0.49 e si sgonfiano.
            # I maschi fanno +42$; le femmine tolgono -105$ → netto -63$.
            # Il bot SA guadagnare: sono le femmine che cancellano i maschi.
            #
            # Simulazione retrospettiva su quei 44 trade, filtro per soglia:
            #   0.50 → -2.44 | 0.55 → +0.23 | 0.60 → +6.94 (PICCO, 15/15 maschi
            #   tenuti) | 0.65 → +0.50 (qui inizia a uccidere maschi).
            # → soglia 0.60 = ultimo gradino prima di perdere maschi. PARAMETRICA.
            #
            # seme medio = (primo + ultimo degli ultimi 5 seed_score), come la
            # colonna seed_traj usata nella simulazione (campo._seed_history[-5:]).
            # Sotto soglia → NON nasce (femmina evitata). Sopra → passa (maschio).
            # NOTA: filtro probabilistico, non perfetto (una femmina con seme alto
            # esiste). Logga ogni blocco → sui dati nuovi si verifica e si ritara.
            # SEME_GATE_OFF (20giu, Roberto): il SEME_GATE giudica la femmina
            # sul SEME a priori (< 0.60 = femmina). Ma il modello giudica sul
            # MOVIMENTO (cancello), non sul seme. Dimostrato sbagliato su dati
            # larghi (398 false-female). SEME_GATE_OFF=true lo spegne: lascia
            # passare al cancello, che decide sul movimento. REVERSIBILE.
            SEME_GATE_SOGLIA = float(getattr(self, "SEME_GATE_SOGLIA", 0.60))
            _sh = list(getattr(self.campo, "_seed_history", []))[-5:]
            # FIX 24giu (Roberto): SEME_GATE guardiano VECCHIO (398 false-female sui
            # dati, narrazione smentita). Bloccava il maschio dopo "ENTRA SUBITO".
            # Con MACCHINA_PURA non interviene.
            _pura_seme = os.environ.get("MACCHINA_PURA", "true").lower() == "true"
            if not _pura_seme and len(_sh) >= 2 and os.environ.get("SEME_GATE_OFF","false").lower() != "true":
                _seme_medio = (_sh[0] + _sh[-1]) / 2.0
                if _seme_medio < SEME_GATE_SOGLIA:
                    self._log("🚫", f"SEME_GATE BLOCCO femmina: seme={_seme_medio:.3f} "
                                    f"< {SEME_GATE_SOGLIA} | {momentum}/{volatility}/{trend} "
                                    f"seed={seed.get('score', 0):.3f} @ ${price:.1f}")
                    return

            matrimonio = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
            pb_signals = self.campo._pre_breakout_factor()[2] \
                         if len(self.campo._prices_short) >= 30 else 0

            # ════════════════════════════════════════════════════════════════
            # GATE CROMOSOMA v2 — CORRETTO 5giu (Roberto: "lavoravi in modo inverso")
            # ════════════════════════════════════════════════════════════════
            # ERRORE v1 (4giu): avevo messo compression >= 0.18 ("manca la carica
            # = femmina"). SBAGLIATO, segno invertito. Su 24 trade sobri reali:
            # le FEMMINE hanno compression ALTA (0.92/0.95/0.99/1.0), i MASCHI bassa.
            # Cartesiana 2 geni (la piu' robusta, no overfitting):
            #   MASCHIO = vol_pressure >= 0.50  E  compression <= 0.70
            #   -> tiene 10 maschi su 11, blocca 10 femmine su 13.
            # Le regole a 3-4 geni (cdur, flips) davano 1-2 femmine in meno ma erano
            # cucite su 1-2 casi (overfitting su 24 trade) -> NON usate.
            # ⚠ SPERIMENTALE: base 24 trade. Sospetto solido, non legge. Parametrico
            # e reversibile. Logga ogni blocco. Resta 1 maschio "gemello" non preso
            # (stesso profilo di una femmina) -> baratto accettato (10/11 vs 10/13).
            # ════════════════════════════════════════════════════════════════
            # GATE CROMOSOMA v4 — linea ritarata su 28 trade nati-sotto-gate (5giu)
            # ════════════════════════════════════════════════════════════════
            # Griglia su 28 trade (13 maschi peak>0, 15 trans peak=0/perdono):
            # miglior separazione M/T = finestra vp + tetto comp:
            #   MASCHIO se  0.46 <= vp <= 1.13  E  comp <= 0.64
            #   -> tiene 10/13 maschi, blocca 13/15 trans (ne passano 2 "gemelli").
            # Differenze dal v3: comp 0.70->0.64 (stretta), aggiunto tetto vp 1.13
            #   (i trans isterici stavano sopra), cdur DISATTIVATO di default
            #   (era tarato su 2 casi, non confermato sui dati nuovi -> CDUR_MIN=0).
            # ⚠ I 2 trans che restano (vp~0.95/cp~0.58, vp~0.72/cp~0.46) hanno firma
            #   da maschio PURO: il gate NON puo' vederli. Si prendono solo col
            #   "primo respiro" (peak nei primi tick), 2o cancello DA COSTRUIRE.
            # ⚠ Costa 2 maschi grossi tagliati (vp1.48/+7.74, cp0.74/+6.48). Baratto
            #   accettato da Roberto: blocca ~90$ di trans, sacrifica ~14$ di maschi.
            # ⚠ TUTTO tarato su 28 trade. Validare alla prossima raccolta sui NUOVI.
            # FIX 24giu (Roberto): CROMO guardiano VECCHIO (anti-fee non anti-trans,
            # 2638 trade bloccati 0 crash evitati). Bloccava il maschio dopo
            # "ENTRA SUBITO". Con MACCHINA_PURA non interviene.
            _pura_cromo = os.environ.get("MACCHINA_PURA", "true").lower() == "true"
            CROMO_GATE_ON   = (not _pura_cromo) and os.environ.get("CROMO_GATE_ON", "true").lower() == "true"
            CROMO_VPRESS_MIN = float(os.environ.get("CROMO_VPRESS_MIN", "0.46"))
            CROMO_VPRESS_MAX = float(os.environ.get("CROMO_VPRESS_MAX", "1.13"))
            CROMO_COMP_MAX   = float(os.environ.get("CROMO_COMP_MAX", "0.64"))
            CROMO_CDUR_MIN   = float(os.environ.get("CROMO_CDUR_MIN", "0"))
            if CROMO_GATE_ON:
                _vp = seed.get('vol_pressure')
                _cp = seed.get('compression')
                _cd = seed.get('comp_duration')
                if _vp is not None and _cp is not None:
                    _cd_breve = (_cd is not None and CROMO_CDUR_MIN > 0 and _cd < CROMO_CDUR_MIN)
                    if (_vp < CROMO_VPRESS_MIN or _vp > CROMO_VPRESS_MAX
                            or _cp > CROMO_COMP_MAX or _cd_breve):
                        _causa = ("vol_basso" if _vp < CROMO_VPRESS_MIN else
                                  "vol_isterico" if _vp > CROMO_VPRESS_MAX else
                                  "comp_alta" if _cp > CROMO_COMP_MAX else "cdur_breve")
                        # CONTATORE TRANS BLOCCATI (in memoria, si azzera al riavvio)
                        if not hasattr(self, "_cromo_blocchi"):
                            self._cromo_blocchi = {"vol_basso": 0, "vol_isterico": 0,
                                                   "comp_alta": 0, "cdur_breve": 0, "totale": 0}
                        self._cromo_blocchi[_causa] = self._cromo_blocchi.get(_causa, 0) + 1
                        self._cromo_blocchi["totale"] = self._cromo_blocchi.get("totale", 0) + 1
                        # PERSISTENZA 6giu: scrivo il blocco nel DB (non solo in RAM).
                        # Cosi' NON si azzera al restart e si fanno i conti veri:
                        # quando e' stato bloccato, con che firma, a che prezzo.
                        _cn = None
                        try:
                            import sqlite3 as _sq3
                            _cn = _sq3.connect(DB_PATH, timeout=15)
                            _cn.execute("PRAGMA busy_timeout=15000;")
                            _cn.execute("""CREATE TABLE IF NOT EXISTS trans_bloccati (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                causa TEXT, vpress REAL, comp REAL, prezzo REAL)""")
                            _cn.execute("INSERT INTO trans_bloccati (causa,vpress,comp,prezzo) VALUES (?,?,?,?)",
                                        (_causa, float(_vp), float(_cp), float(price)))
                            _cn.commit()
                        except Exception:
                            pass
                        finally:
                            if _cn is not None:
                                try:
                                    _cn.close()
                                except Exception:
                                    pass
                        self._log("🚫", f"CROMO_GATE v4 BLOCCO femmina ({_causa}) "
                                        f"[tot: {self._cromo_blocchi['totale']}]: "
                                        f"vpress={_vp:.3f}[{CROMO_VPRESS_MIN}-{CROMO_VPRESS_MAX}] "
                                        f"comp={_cp:.3f}(<={CROMO_COMP_MAX}) | "
                                        f"seed={seed.get('score', 0):.3f} @ ${price:.1f}")
                        # ════════════════════════════════════════════════════════
                        # OSSERVA IL TAGLIATO (9giu, Roberto): invece di buttarlo
                        # via, lo registro come PHANTOM così il sistema esistente
                        # lo segue (max_price/peak, is_win a 30s/2min) e dopo
                        # sappiamo: era un MASCHIO ucciso per sbaglio (sarebbe
                        # salito) o una dopata (giusto tagliarla)? Misura, non naso.
                        # NON entra in trade: solo osservazione in ombra.
                        # ENV CROMO_OSSERVA_SEC>0 = attivo (default 30); 0 = spento.
                        # block_reason marcato CROMO_ così si filtra in query.
                        # ════════════════════════════════════════════════════════
                        try:
                            if float(os.environ.get("CROMO_OSSERVA_SEC", "30")) > 0:
                                self._record_phantom(
                                    price,
                                    f"CROMO_{_causa}_vp{_vp:.2f}_cp{_cp:.2f}",
                                    float(seed.get("score", 0) or 0),
                                    str(momentum), str(volatility), str(trend))
                        except Exception:
                            pass
                        return

            # ════════════════════════════════════════════════════════════════
            # RITARDO INGRESSO (prova 7giu, Roberto) — non entrare di getto.
            # ════════════════════════════════════════════════════════════════
            # Quando il sistema vuole entrare NON apre subito: aspetta N secondi
            # che il segnale REGGA. Il maschio (anche lento) continua a chiamare
            # l'ingresso tick dopo tick -> dopo N secondi entra. Il trans si
            # sgonfia: smette di chiamare l'ingresso -> il conteggio si azzera e
            # non entra mai. Si entra SOLO sui segnali che durano N secondi, al
            # prezzo CORRENTE (di adesso, non di N secondi fa).
            # Interruttore: RITARDO_INGRESSO_SEC (0 = spento, default 4).
            #   RITARDO_RESET_GAP = pausa oltre cui il segnale e' "interrotto".
            # ⚠ PROVA: guardare dal vivo se i maschi lenti salgono e i trans
            #   calano. Reversibile in un colpo: RITARDO_INGRESSO_SEC=0.
            # ════════════════════════════════════════════════════════════════
            _rit_sec = float(os.environ.get("RITARDO_INGRESSO_SEC", "4"))
            # FIX 24giu (Roberto): TUTTO questo blocco "ritardo" e' il vecchio sistema
            # di mine (MINA_MAE, ANTIISTERIA, ANTICORDATA, CONTRO, RIPIENO, GATE...)
            # che bloccavano il maschio DOPO "ENTRA SUBITO" (logga ma non apre).
            # Con MACCHINA_PURA il maschio e' gia' stato giudicato dalle firme
            # (picco/mae propri): NON deve incontrare nessuna di queste mine vecchie.
            # Le salto TUTTE in un colpo.
            _pura_rit = os.environ.get("MACCHINA_PURA", "true").lower() == "true"
            if _pura_rit:
                _rit_sec = 0   # macchina pura: nessuna mina del vecchio sistema ritardo
            if _rit_sec > 0:
                _rit_now      = time.time()
                _rit_gap      = float(os.environ.get("RITARDO_RESET_GAP", "3.0"))
                _rit_aggancio = getattr(self, "_rit_aggancio_ts", None)
                _rit_last     = getattr(self, "_rit_last_call", 0.0)
                if not hasattr(self, "_ritardo_stats"):
                    self._ritardo_stats = {"sec": _rit_sec, "agganciati": 0, "entrati": 0}
                self._ritardo_stats["sec"] = _rit_sec
                # primo aggancio o segnale interrotto (pausa > gap) -> riparte il conteggio
                if _rit_aggancio is None or (_rit_now - _rit_last) > _rit_gap:
                    _rit_aggancio = _rit_now
                    self._rit_aggancio_ts = _rit_now
                    self._rit_prezzo_nascita = price   # FISSO il prezzo di NASCITA (10giu fix radice)
                    self._rit_picco_pre = None
                    self._rit_picco_pre_t = None   # reset tempo del picco (filtro M/F)
                    self._rit_crollo_min = None   # reset minimo attesa (anti-falsa-ripartenza)
                    # ANTI-MAGO — RI-AGGANCIO SOSPETTO (11giu, Roberto: "sono maghi").
                    # Il mago crolla (aggancio scartato), poi si ri-aggancia sul
                    # RIMBALZO con fedina pulita (17:30: crollo 17:30:06 scartato,
                    # rimbalzo 17:30:16 entrato → -9). Se è crollato da poco e a
                    # prezzo vicino, il nuovo aggancio NON parte pulito: eredita
                    # il minimo del crollo, così l'anti-falsa-ripartenza lo becca.
                    # ENV: RIAGGANCIO_MEMORIA_SEC (default 20; 0 = spento).
                    _riag_sec = float(os.environ.get("RIAGGANCIO_MEMORIA_SEC", "20"))
                    _ult_crollo_ts = getattr(self, "_rit_ultimo_crollo_ts", None)
                    if _riag_sec > 0 and _ult_crollo_ts is not None and (_rit_now - _ult_crollo_ts) <= _riag_sec:
                        # ri-aggancio dopo un crollo recente -> eredita il sospetto
                        self._rit_crollo_min = -999.0   # marchio: parte già "crollato"
                        self._log("🎭", f"RI-AGGANCIO SOSPETTO: nuovo aggancio "
                                        f"{_rit_now - _ult_crollo_ts:.0f}s dopo un crollo — "
                                        f"eredita il sospetto (mago travestito)")
                    self._ritardo_stats["agganciati"] = self._ritardo_stats.get("agganciati", 0) + 1
                    # ════════════════════════════════════════════════════════════
                    # VEDERE DENTRO CHI VIENE SCANSATO (8giu, Roberto)
                    # Ad OGNI aggancio scrivo nel DB i numeri del segnale in QUESTO
                    # istante. Poi, quando/se entra, aggiorno entrato=1 (sotto).
                    # Cosi' dopo si vede: gli agganci NON entrati (entrato=0) che
                    # numeri avevano? vp/comp/peak da maschio (deglutito = male) o
                    # da trans (giusto)? Risponde a "i maschi vengono scansati?".
                    # Solo scrittura: NON tocca la logica di entrata/blocco.
                    # ════════════════════════════════════════════════════════════
                    _ag = None
                    try:
                        import sqlite3 as _sq3ag
                        _ag = _sq3ag.connect(DB_PATH, timeout=15)
                        _ag.execute("PRAGMA busy_timeout=15000;")
                        _ag.execute("""CREATE TABLE IF NOT EXISTS ritardo_agganci (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                            aggancio_ts REAL, direction TEXT,
                            vpress REAL, comp REAL, seed REAL,
                            momentum TEXT, volatility TEXT, trend TEXT,
                            prezzo REAL, peak_pnl REAL, entrato INTEGER DEFAULT 0)""")
                        _cur_ag = _ag.execute(
                            """INSERT INTO ritardo_agganci
                               (aggancio_ts, direction, vpress, comp, seed,
                                momentum, volatility, trend, prezzo, peak_pnl, entrato)
                               VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
                            (_rit_now,
                             getattr(self.campo, "_direction", "LONG"),
                             (float(seed.get("vol_pressure")) if seed.get("vol_pressure") is not None else None),
                             (float(seed.get("compression")) if seed.get("compression") is not None else None),
                             float(seed.get("score", 0) or 0),
                             str(momentum), str(volatility), str(trend),
                             float(price),
                             float((self._shadow or {}).get("peak_pnl", 0) or 0)))
                        self._rit_aggancio_rowid = _cur_ag.lastrowid
                        _ag.commit()
                    except Exception:
                        self._rit_aggancio_rowid = None
                    finally:
                        if _ag is not None:
                            try: _ag.close()
                            except Exception: pass
                self._rit_last_call = _rit_now
                # ════════════════════════════════════════════════════════════
                # FILTRO MAE (9giu, Roberto) — IL MASCHIO NON SCENDE, LA DOPATA SI'
                # Scoperto sui dati (1077 segnali firma-maschio): i 138 maschi
                # hanno mae medio -0.08 (non scendono), le 939 dopate -1.06
                # (crollano). Separazione 13x. Durante l'attesa del ritardo
                # traccio quanto scende il prezzo dall'aggancio: se scende oltre
                # soglia -> e' una dopata che si sta sgonfiando -> NON entra,
                # azzero. Se tiene -> maschio -> prosegue verso l'ingresso.
                # ENV: MAE_FILTRO_USD (default 0.50; 0 = spento). E' in $ sul
                # movimento prezzo proporzionale a size/exposure. Reversibile.
                # ════════════════════════════════════════════════════════════
                _mae_soglia = float(os.environ.get("MAE_FILTRO_USD", "0.50"))
                if _mae_soglia > 0:
                    # uso il prezzo di NASCITA fisso (10giu fix radice): non si
                    # resetta a ogni ripartenza del conteggio. Prima il TRANS che
                    # scendeva a gradini resettava il riferimento e fregava il MAE.
                    _prezzo_aggancio = getattr(self, "_rit_prezzo_nascita", None)
                    if _prezzo_aggancio is None:
                        self._rit_prezzo_nascita = price
                        _prezzo_aggancio = price
                    _exposure = float(os.environ.get("EXPOSURE_USD", "5000"))
                    # POSIZIONE ASSOLUTA rispetto all'aggancio (10giu, logica corretta):
                    # i dati di Roberto dicono maschi SEMPRE sopra zero (mae -0.08),
                    # dopate SEMPRE sotto (mae -1.06). Quindi NON misuro la caduta dal
                    # massimo (sbagliato: scartava maschi che respirano e lasciava
                    # passare dopate che scendono dritte). Misuro DOVE SONO ORA
                    # rispetto al prezzo di partenza. Sotto soglia negativa = dopata.
                    _direction = getattr(self.campo, "_direction", "LONG")
                    _delta = (price - _prezzo_aggancio) / _prezzo_aggancio
                    if str(_direction).upper().startswith("SHORT"):
                        _delta = -_delta   # per SHORT, scendere è guadagno
                    _pos_usd = _delta * _exposure
                    if _pos_usd < -_mae_soglia:
                        # è SOTTO l'aggancio oltre soglia -> dopata -> fuori, azzero
                        self._log("📉", f"MAE FILTRO: dopata scartata "
                                        f"(posizione {_pos_usd:+.2f}$ < -{_mae_soglia}$ "
                                        f"vs aggancio ${_prezzo_aggancio:.1f}) — NON entra")
                        self._rit_aggancio_ts = None
                        self._rit_prezzo_nascita = None
                        self._rit_picco_pre = None
                        try:
                            self._ritardo_stats["mae_scartati"] = self._ritardo_stats.get("mae_scartati", 0) + 1
                        except Exception:
                            pass
                        # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                        try:
                            self._record_phantom(price, "MINA_MAE",
                                                 float(seed.get("score", 0) or 0),
                                                 str(momentum), str(volatility), str(trend))
                        except Exception:
                            pass
                        return

                    # ════════════════════════════════════════════════════════
                    # ANTI-FALSA-RIPARTENZA (11giu, Roberto — il -9.19 del 17:30).
                    # Il segnale crolla nei secondi gratis (-5.21 al s3), poi
                    # RIMBALZA (+1.85 al s5). Il bot lo aggancia SUL RIMBALZO
                    # (vede +1.85, sembra buono) ed entra. Poi ricrolla a -9.
                    # Il MAE guarda solo l'ISTANTE del giudizio, non il MINIMO
                    # toccato durante l'attesa. Fix: traccio il minimo; se ha
                    # toccato sotto -CROLLO_MAX in qualsiasi momento dei secondi
                    # gratis, è MARCIO -> non entra MAI, anche se rimbalza. Una
                    # ripartenza vera non crolla a -5 prima di partire.
                    # ENV: CROLLO_MAX_USD (default 2.0; 0 = spento).
                    # ════════════════════════════════════════════════════════
                    _crollo_min = getattr(self, "_rit_crollo_min", None)
                    if _crollo_min is None or _pos_usd < _crollo_min:
                        self._rit_crollo_min = _pos_usd
                        _crollo_min = _pos_usd
                    _crollo_max = float(os.environ.get("CROLLO_MAX_USD", "2.0"))
                    if _crollo_max > 0 and _crollo_min is not None and _crollo_min <= -_crollo_max:
                        # ha toccato il fondo (crollo) durante l'attesa -> marcio anche se rimbalza
                        self._log("💀", f"FALSA RIPARTENZA scartata "
                                        f"(ha toccato {_crollo_min:+.2f}$ nei secondi gratis "
                                        f"< -{_crollo_max}$ — crolla e rimbalza, marcio) — NON entra")
                        # MEMORIA DEL CROLLO (anti-mago): salvo quando e a che prezzo
                        # è crollato, così il ri-aggancio sul rimbalzo (entro
                        # RIAGGANCIO_MEMORIA_SEC) non parte con fedina pulita.
                        self._rit_ultimo_crollo_ts = _rit_now
                        self._rit_ultimo_crollo_prezzo = price
                        self._rit_aggancio_ts = None
                        self._rit_prezzo_nascita = None
                        self._rit_picco_pre = None
                        self._rit_crollo_min = None
                        try:
                            self._ritardo_stats["crollo_scartati"] = self._ritardo_stats.get("crollo_scartati", 0) + 1
                        except Exception:
                            pass
                        # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic.
                        # Distingue se il crollo era ORIGINALE (osservato in questi secondi gratis)
                        # oppure EREDITATO dal RI-AGGANCIO (flag _crollo_min == -999.0, impostato
                        # dal blocco RI-AGGANCIO SOSPETTO ~r.12258 quando il nuovo aggancio arriva
                        # entro RIAGGANCIO_MEMORIA_SEC da un crollo precedente). Così il tribunale
                        # può giudicare il RI-AGGANCIO separatamente.
                        try:
                            _etichetta_mina = ("MINA_RIAGGANCIO_EREDITATO"
                                               if _crollo_min == -999.0
                                               else "MINA_ANTIFALSA_RIPARTENZA")
                            self._record_phantom(price, _etichetta_mina,
                                                 float(seed.get("score", 0) or 0),
                                                 str(momentum), str(volatility), str(trend))
                        except Exception:
                            pass
                        return

                    # ════════════════════════════════════════════════════════
                    # ANTI-MAGO — VOL ISTERICO (11giu, Roberto: "sono maghi").
                    # Il 17:30 aveva vol_pressure 1.1 + momentum DEBOLE: isteria
                    # (sbatte su-giù con forza apparente) ma NESSUNA corsa vera
                    # (momentum debole). Il mago crea isteria per sembrare vivo.
                    # Se vol_pressure è alto MA momentum è DEBOLE → movimento
                    # sbattuto senza forza → non entra. Il maschio vero ha vol E
                    # momentum coerenti. ENV: VOL_ISTERICO_MAX (default 0 = spento,
                    # accendere a ~1.0 per bloccare vp oltre soglia con mom DEBOLE).
                    # ════════════════════════════════════════════════════════
                    _vol_ist = float(os.environ.get("VOL_ISTERICO_MAX", "0"))
                    if _vol_ist > 0:
                        _vp_now = seed.get("vol_pressure")
                        if (_vp_now is not None and float(_vp_now) >= _vol_ist
                                and str(momentum).upper() == "DEBOLE"):
                            self._log("🌀", f"VOL ISTERICO scartato "
                                            f"(vol_pressure {float(_vp_now):.2f} >= {_vol_ist} "
                                            f"ma momentum DEBOLE — isteria senza forza) — NON entra")
                            self._rit_aggancio_ts = None
                            self._rit_prezzo_nascita = None
                            self._rit_picco_pre = None
                            self._rit_crollo_min = None
                            try:
                                self._ritardo_stats["isterico_scartati"] = self._ritardo_stats.get("isterico_scartati", 0) + 1
                            except Exception:
                                pass
                            # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                            try:
                                self._record_phantom(price, "MINA_ANTIISTERIA",
                                                     float(seed.get("score", 0) or 0),
                                                     str(momentum), str(volatility), str(trend))
                            except Exception:
                                pass
                            return

                    # ════════════════════════════════════════════════════════
                    # CONTROSCATTO (11giu, Roberto — "facciamo anche noi un
                    # controscatto :)"). I TRANS-forbice fanno un FALSO SCATTO
                    # nei secondi gratis (15:47: picco +1.10 a s2-4) e poi si
                    # SGONFIANO (+0.26 a s5), ingannando il filtro che li fa
                    # entrare perché ancora positivi. Poi nascono morti (peak 0).
                    # Il maschio vero invece SALE e TIENE/CRESCE (+0.19→+1.62
                    # costante, non si sgonfia mai). Controscatto: traccio il
                    # PICCO dell'attesa; se al giudizio il segnale si è sgonfiato
                    # dal picco oltre SCATTO_SGONFIO_PCT, è una falsa partenza ->
                    # SCHIVO (non entro). Il maschio (vicino al picco) entra.
                    # ENV: SCATTO_SGONFIO_PCT (default 0.50 = sgonfiato >50% dal
                    #      picco -> fuori). 0 = spento. Picco minimo per valutare:
                    #      SCATTO_PICCO_MIN (default 0.6, sotto è rumore).
                    # ════════════════════════════════════════════════════════
                    # ANTI-CORDA (11giu, Roberto: "non subire più il trade che si
                    # mette la corda al collo piano piano"). Il 18:10: piatto
                    # +0.74 (s1-3), poi GIRA sotto zero (-0.52 al s4 = giudizio),
                    # cola a -4. Al momento del giudizio stava GIÀ scendendo sotto
                    # l'aggancio. Una corda che inizia a stringersi. Regola secca:
                    # se al giudizio _pos_usd < 0 (è sotto l'aggancio, sta girando
                    # giù) → NON entra. Il maschio al giudizio è POSITIVO (sale).
                    # Becca le morte piatte-calanti che né crollo né sgonfio né
                    # isteria coprono. ENV: ANTICORDA_OFF=true per spegnere.
                    # ════════════════════════════════════════════════════════
                    # aggiorno il picco col tick corrente (serve al controscatto sotto
                    # e alla logica 4+2). L'anti-corda vera decide all'ingresso (4+2).
                    _pp = getattr(self, "_rit_picco_pre", None)
                    if _pp is None or _pos_usd > _pp:
                        self._rit_picco_pre = _pos_usd
                        # FIX 19giu (Roberto): lego il picco all'aggancio CORRENTE.
                        # Cosi' il cancello @apertura sa se il picco e' di QUESTO
                        # trade o un residuo ereditato da un candidato precedente
                        # (era il buco: le capre passavano col picco di un altro).
                        self._rit_picco_pre_ag_ts = getattr(self, "_rit_aggancio_ts", None)
                        # FILTRO MASCHIO/FEMMINA (19giu, da 316 curve reali):
                        # salvo QUANDO (secondi dall'aggancio) e' stato toccato il picco.
                        # Dato vero: maschio tocca il picco a ~14s e fa grasso ~4.3;
                        # femmina tocca a ~1.5s e fa solo ~0.45.
                        _ag0 = getattr(self, "_rit_aggancio_ts", None)
                        self._rit_picco_pre_t = (_rit_now - _ag0) if _ag0 else 0.0

                    # ── CORSIA MASCHIO RIMOSSA (17giu, Roberto). Era rotta:
                    # azzerava lo stato del ritardo e poi cadeva nei cancelli a
                    # valle (ANTICORDA, GATE PEAK) che lo rileggevano "piatto", e
                    # scriveva entrato=1 falso senza mai aprire il trade. Tolta.
                    # Niente ENV nuove. Comportamento = vivo: guardiani sempre attivi.
                    _maschio_entra_ora = False


                    # ════════════════════════════════════════════════════════
                    # ── guardiani del purgatorio: SOLO se NON e' un maschio confermato
                    if not _maschio_entra_ora:
                        # traccio il picco dell'attesa (sempre, costa nulla)
                        _picco_pre = getattr(self, "_rit_picco_pre", None)
                        if _picco_pre is None or _pos_usd > _picco_pre:
                            self._rit_picco_pre = _pos_usd
                            _picco_pre = _pos_usd
                            self._rit_picco_pre_ag_ts = getattr(self, "_rit_aggancio_ts", None)
                        _sgonfio_pct = float(os.environ.get("SCATTO_SGONFIO_PCT", "0.50"))
                        _picco_min = float(os.environ.get("SCATTO_PICCO_MIN", "0.6"))
                        if _sgonfio_pct > 0 and _picco_pre is not None and _picco_pre >= _picco_min:
                            # quanto si è sgonfiato dal picco dell'attesa?
                            _sgonfio = (_picco_pre - _pos_usd) / _picco_pre if _picco_pre > 0 else 0
                            if _sgonfio >= _sgonfio_pct:
                                # falso scatto: ha fatto picco e si è sgonfiato -> schivo
                                self._log("🦊", f"CONTROSCATTO: falsa partenza schivata "
                                                f"(picco +{_picco_pre:.2f}$ → ora {_pos_usd:+.2f}$, "
                                                f"sgonfio {_sgonfio*100:.0f}%) — NON entra")
                                self._rit_aggancio_ts = None
                                self._rit_prezzo_nascita = None
                                self._rit_picco_pre = None
                                try:
                                    self._ritardo_stats["controscatto_scartati"] = self._ritardo_stats.get("controscatto_scartati", 0) + 1
                                except Exception:
                                    pass
                                # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                                try:
                                    self._record_phantom(price, "MINA_CONTROSCATTO",
                                                         float(seed.get("score", 0) or 0),
                                                         str(momentum), str(volatility), str(trend))
                                except Exception:
                                    pass
                                return

                        # ════════════════════════════════════════════════════════
                        # TAGLIO TRANS PIATTI (11giu, Roberto: "vanno tagliati punto").
                        # Dai dati pre-ingresso: i TRANS piatti stanno a +0.2/0.0/-0.001
                        # nei secondi gratis, NON salgono. I maschi veri salgono sopra
                        # +0.5 e ci restano (es. +0.749 costante). Quindi: per entrare
                        # NON basta "non scendere" — bisogna SALIRE da maschio. Se dopo
                        # almeno SALITA_DOPO_SEC secondi di attesa il segnale non ha
                        # superato +SALITA_MIN, è un trans piatto -> NON entra.
                        # ENV: SALITA_MIN_USD (default 0.40; 0 = spento).
                        #      SALITA_DOPO_SEC (default 3 = do 3s al maschio per salire).
                        # ════════════════════════════════════════════════════════
                        _salita_min = float(os.environ.get("SALITA_MIN_USD", "0.40"))
                        _salita_dopo = float(os.environ.get("SALITA_DOPO_SEC", "3"))
                        # CANCELLO_TIENI_PCT (18giu, Roberto): quanto deve stare VICINO
                        # al suo picco nel momento della decisione per essere maschio.
                        # 0.75 = deve tenere almeno il 75% del massimo raggiunto.
                        # Il rimbalzo-femmina (sale a +1.1, crolla a +0.1 = 9% del picco)
                        # viene tagliato. Il maschio che respira (+1.5 -> +1.4 = 93%) passa.
                        # Cosi' guardiamo il FILM (la sequenza), non la FOTO (l'istante).
                        _tieni_pct = float(os.environ.get("CANCELLO_TIENI_PCT", "0.75"))
                        if _salita_min > 0:
                            _eta_attesa = _rit_now - _rit_aggancio
                            _picco_osservato = getattr(self, "_rit_picco_pre", None) or 0.0
                            # FIX 18giu (Roberto: "il film non morde piu' da ieri"):
                            # PRIMA il taglio scattava solo se _eta_attesa >= _salita_dopo
                            # (cronometro). Ma l'aggancio si resetta ad ogni segnale
                            # intermittente -> _eta_attesa ripartiva e NON arrivava mai
                            # a 3s al momento giusto -> il film NON giudicava MAI e le
                            # capre passavano. ORA il film giudica sul PICCO, non sul
                            # cronometro: conta CHE sale, non quando. Diamo comunque un
                            # minimo di osservazione (almeno _salita_dopo s OPPURE il
                            # picco si e' gia' espresso) per non tagliare al primo tick.
                            _ha_osservato = (_eta_attesa >= _salita_dopo) or (_picco_osservato >= _salita_min)
                            if _ha_osservato:
                                # DUE condizioni (il film):
                                # 1) ha dato segno di vita: il PICCO ha superato SALITA_MIN
                                # 2) ci sta ancora vicino: ORA >= picco * TIENI_PCT (non crollato)
                                _ha_salito   = _picco_osservato >= _salita_min
                                _tiene_ora   = (_picco_osservato > 0 and
                                                _pos_usd >= _picco_osservato * _tieni_pct)
                                if (not _ha_salito) or (not _tiene_ora):
                                    # o non e' mai salito (trans piatto), o e' salito e poi
                                    # crollato sotto il 75% del picco (femmina che si svuota) -> fuori
                                    _motivo_taglio = ("non sale da maschio" if not _ha_salito
                                                      else f"crollato a {(_pos_usd/_picco_osservato*100 if _picco_osservato>0 else 0):.0f}% del picco +{_picco_osservato:.2f}$")
                                    self._log("🟰", f"TRANS PIATTO scartato "
                                                    f"(dopo {_eta_attesa:.1f}s sta a {_pos_usd:+.2f}$, "
                                                    f"picco +{_picco_osservato:.2f}$ — {_motivo_taglio}) — NON entra")
                                    self._rit_aggancio_ts = None
                                    self._rit_prezzo_nascita = None
                                    self._rit_picco_pre = None
                                    try:
                                        self._ritardo_stats["piatti_scartati"] = self._ritardo_stats.get("piatti_scartati", 0) + 1
                                    except Exception:
                                        pass
                                    # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                                    try:
                                        self._record_phantom(price, "MINA_TRANS_PIATTI",
                                                             float(seed.get("score", 0) or 0),
                                                             str(momentum), str(volatility), str(trend))
                                    except Exception:
                                        pass
                                    return

                        # ════════════════════════════════════════════════════════
                        # RIPIEGAMENTO PRE-INGRESSO (10giu, Roberto) — il gioco vero:
                        # i secondi del ritardo sono GRATIS (non siamo nel trade, no
                        # fee). Lì la dopata si dichiara: SALE e poi SI GIRA. Il MAE
                        # assoluto becca solo chi nasce sotto zero; questo becca la
                        # dopata "lenta" che sale verde (+2, +2.8) e poi ripiega —
                        # PRIMA di entrare. Il maschio sale e TIENE (non ripiega) ->
                        # entra. Traccio il picco durante l'attesa: se il segnale
                        # ripiega di RIPIEG_USD dal suo picco mentre aspetta, è una
                        # dopata che si svuota -> NON entra, non paghiamo fee.
                        # ENV: RIPIEG_PRE_USD (default 0.60; 0 = spento). Reversibile.
                        # ════════════════════════════════════════════════════════
                        _ripieg_soglia = float(os.environ.get("RIPIEG_PRE_USD", "0.60"))
                        if _ripieg_soglia > 0:
                            _picco_pre = getattr(self, "_rit_picco_pre", None)
                            if _picco_pre is None or self._rit_aggancio_ts == _rit_now:
                                _picco_pre = _pos_usd
                            if _pos_usd > _picco_pre:
                                _picco_pre = _pos_usd          # nuovo massimo durante attesa
                            self._rit_picco_pre = _picco_pre
                            # ripiega solo se era salito davvero (picco sopra soglia)
                            if _picco_pre >= _ripieg_soglia and (_picco_pre - _pos_usd) >= _ripieg_soglia:
                                self._log("🔄", f"RIPIEGAMENTO pre-ingresso: dopata si svuota "
                                                f"(picco {_picco_pre:+.2f}$ -> ora {_pos_usd:+.2f}$, "
                                                f"giù {_picco_pre - _pos_usd:.2f}$) — NON entra, no fee")
                                self._rit_aggancio_ts = None
                                self._rit_prezzo_nascita = None
                                self._rit_picco_pre = None
                                try:
                                    self._ritardo_stats["ripieg_scartati"] = self._ritardo_stats.get("ripieg_scartati", 0) + 1
                                except Exception:
                                    pass
                                # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                                try:
                                    self._record_phantom(price, "MINA_RIPIEGAMENTO",
                                                         float(seed.get("score", 0) or 0),
                                                         str(momentum), str(volatility), str(trend))
                                except Exception:
                                    pass
                                return

                    _rit_atteso = _rit_now - _rit_aggancio
                    if _rit_atteso < _rit_sec:
                        self._log("⏳", f"RITARDO ingresso: segnale agganciato, "
                                        f"atteso {_rit_atteso:.1f}/{_rit_sec:.0f}s @ ${price:.1f}")
                        return
                    # ════════════════════════════════════════════════════════
                    # STRATEGIA 4+2 (11giu, Roberto: "non 4 d'ufficio, ma 4+2 e
                    # vediamo chi crolla — i due solo per i resistenti"). A 4s:
                    #  - CHIARO positivo (sopra +zona) → maschio → entra subito
                    #  - CHIARO negativo (sotto -zona) → corda → fuori subito
                    #  - INCERTO (tra -zona e +zona, lì lì) → NON decido d'ufficio:
                    #      proroga di RITARDO_EXTRA_SEC e guardo chi crolla. Il
                    #      maschio tiene/corre, la dopata si smaschera crollando.
                    # Il tempo è il giudice. ENV: ANTICORDA_ZONA (default 1.0 = zona
                    #  incerta ±1$), RITARDO_EXTRA_SEC (default 2). ANTICORDA_OFF spegne.
                    # ════════════════════════════════════════════════════════
                    if os.environ.get("ANTICORDA_OFF", "false").lower() != "true":
                        _zona = float(os.environ.get("ANTICORDA_ZONA", "1.0"))
                        _extra = float(os.environ.get("RITARDO_EXTRA_SEC", "2"))
                        _exp_fin = float(os.environ.get("EXPOSURE_USD", "5000"))
                        _ep_fin = getattr(self, "_rit_prezzo_nascita", None) or price
                        _dir_fin = getattr(self.campo, "_direction", "LONG")
                        _delta_fin = (price - _ep_fin) / _ep_fin
                        if str(_dir_fin).upper().startswith("SHORT"):
                            _delta_fin = -_delta_fin
                        _pos_fin = _delta_fin * _exp_fin

                        # CORDA CHIARA: sotto -zona → fuori subito (a 4s o a 6s)
                        if _pos_fin <= -_zona:
                            self._log("🪢", f"ANTI-CORDA (s{_rit_atteso:.0f}): "
                                            f"{_pos_fin:+.2f}$ <= -{_zona} — crollato, NON entra")
                            self._rit_aggancio_ts = None
                            self._rit_prezzo_nascita = None
                            self._rit_picco_pre = None
                            self._rit_crollo_min = None
                            try:
                                self._ritardo_stats["anticorda_scartati"] = self._ritardo_stats.get("anticorda_scartati", 0) + 1
                            except Exception:
                                pass
                            # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                            try:
                                self._record_phantom(price, "MINA_ANTICORDA_CROLLO",
                                                     float(seed.get("score", 0) or 0),
                                                     str(momentum), str(volatility), str(trend))
                            except Exception:
                                pass
                            return

                        # INCERTO: tra -zona e +zona → do 2 secondi extra (solo se non
                        # li ho già dati). A 4+2=6s rivaluto: se ancora qui sotto/incerto
                        # crollerà nel ramo sopra, se è salito sopra +zona entra sotto.
                        if _pos_fin < _zona and _rit_atteso < (_rit_sec + _extra):
                            self._log("⏳", f"INCERTO a s{_rit_atteso:.0f} ({_pos_fin:+.2f}$ "
                                            f"dentro ±{_zona}) — +{_extra:.0f}s di prova: vediamo chi crolla")
                            return
                        # passati i 2 extra ed è ancora sotto +zona ma sopra -zona →
                        # resta incerto/debole → non entra (non si è dichiarato maschio)
                        if _pos_fin < _zona:
                            self._log("🪢", f"ANTI-CORDA (s{_rit_atteso:.0f}, dopo +{_extra:.0f}s): "
                                            f"{_pos_fin:+.2f}$ ancora incerto, non corre — NON entra")
                            self._rit_aggancio_ts = None
                            self._rit_prezzo_nascita = None
                            self._rit_picco_pre = None
                            self._rit_crollo_min = None
                            try:
                                self._ritardo_stats["anticorda_scartati"] = self._ritardo_stats.get("anticorda_scartati", 0) + 1
                            except Exception:
                                pass
                            # TELECAMERA TRIBUNALE (12giu): registra il taglio in phantom_forensic
                            try:
                                self._record_phantom(price, "MINA_ANTICORDA_INCERTO",
                                                     float(seed.get("score", 0) or 0),
                                                     str(momentum), str(volatility), str(trend))
                            except Exception:
                                pass
                            return
                        # _pos_fin >= +zona → maschio chiaro/resistente → entra

                    # ════════════════════════════════════════════════════════════════
                    # GATE PEAK CONFERMATIVO (13giu2026, Roberto — IL KILLER)
                    # ════════════════════════════════════════════════════════════════
                    # SCOPERTA dimostrata su 154 trade (tabella curva_nascita):
                    # il distintivo maschi/fiacchi NON e' nella firma (ambigua) ne' nei
                    # primi 3s (indistinguibili). E' nel PICCO raggiunto entro una
                    # finestra LUNGA di osservazione. I maschi partono rossi e risalgono
                    # TARDI (t_peak medio WIN 33s vs LOSS 5s). Simulazione ONESTA
                    # (guadagno = pnl_finale - pnl_ingresso): gate "picco >= +1.0$ entro
                    # 15s" porta da -247$ (tutti entrano, WR 30%) a +60$ reali (WR 62%).
                    # Il picco dell'attesa e' gia' tracciato in _rit_picco_pre.
                    #
                    # MODALITA': GATE_PEAK_OBSERVER (default true = SOLO OSSERVA, logga
                    # ma NON blocca). Mettere false per farlo DECIDERE (bloccare chi non
                    # ha raggiunto il picco). ENV:
                    #   GATE_PEAK_USD (default 1.0)  = soglia picco minima per entrare
                    #   GATE_PEAK_FINESTRA_SEC (15)  = entro quanti secondi dall'aggancio
                    #   GATE_PEAK_OBSERVER (true)    = true osserva, false decide
                    #   GATE_PEAK_OFF (false)        = true spegne del tutto
                    # ════════════════════════════════════════════════════════════════
                    if os.environ.get("GATE_PEAK_OFF", "false").lower() != "true":
                        try:
                            _gp_soglia = float(os.environ.get("GATE_PEAK_USD", "1.0"))
                            _gp_finestra = float(os.environ.get("GATE_PEAK_FINESTRA_SEC", "15"))
                            _gp_observer = os.environ.get("GATE_PEAK_OBSERVER", "true").lower() == "true"
                            _gp_picco = getattr(self, "_rit_picco_pre", None)
                            _gp_picco = _gp_picco if _gp_picco is not None else -999.0
                            # il picco e' valido solo se osservato entro la finestra
                            _gp_entro_finestra = (_rit_atteso <= _gp_finestra)
                            _gp_passa = (_gp_picco >= _gp_soglia) and _gp_entro_finestra
                            # ════════════════════════════════════════════════
                            # OSSERVA-GATE (17giu, Roberto: "vai").
                            # Registra cosa VEDE GATE PEAK a ogni aggancio, senza
                            # toccare la decisione. UNA riga per aggancio. Serve a
                            # simulare la soglia sui dati veri: il picco pre-ingresso
                            # non era salvato da nessuna parte -> senza questo non si
                            # puo' simulare l'ingresso (il 97% del sangue). Solo INSERT,
                            # try/except totale: non puo' mai fermare un trade.
                            # Tabella usa-e-getta: DROP quando la taratura e' fatta.
                            # ════════════════════════════════════════════════
                            try:
                                _go = _safe_connect(DB_PATH, timeout=5)
                                _go.execute("PRAGMA busy_timeout=5000;")
                                _go.execute("""CREATE TABLE IF NOT EXISTS gate_peak_osserva (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    ts REAL DEFAULT (strftime('%s','now')),
                                    picco_pre REAL, soglia REAL, t_atteso REAL,
                                    passa INTEGER, observer INTEGER,
                                    regime TEXT, direction TEXT, prezzo REAL)""")
                                _go.execute("""INSERT INTO gate_peak_osserva
                                    (picco_pre, soglia, t_atteso, passa, observer, regime, direction, prezzo)
                                    VALUES (?,?,?,?,?,?,?,?)""",
                                    (float(_gp_picco), float(_gp_soglia), float(_rit_atteso),
                                     1 if _gp_passa else 0, 1 if _gp_observer else 0,
                                     str(getattr(self, "_regime_current", "") or ""),
                                     str(getattr(getattr(self, "campo", None), "_direction", "") or ""),
                                     float(price)))
                                _go.commit()
                                _go.close()
                            except Exception:
                                pass
                            if _gp_passa:
                                self._log_m2("✅",
                                    f"GATE PEAK: picco attesa {_gp_picco:+.2f}$ >= {_gp_soglia}$ "
                                    f"entro {_rit_atteso:.1f}s (<= {_gp_finestra:.0f}s) — MASCHIO confermato")
                            else:
                                _gp_motivo = (f"picco {_gp_picco:+.2f}$ < {_gp_soglia}$" if _gp_picco < _gp_soglia
                                              else f"fuori finestra ({_rit_atteso:.1f}s > {_gp_finestra:.0f}s)")
                                if _gp_observer:
                                    # OSSERVATORE: logga cosa AVREBBE fatto, ma lascia entrare
                                    self._log_m2("👁️",
                                        f"GATE PEAK [OSSERVA]: avrebbe SCARTATO ({_gp_motivo}) "
                                        f"ma observer attivo — entra comunque")
                                else:
                                    # DECISORE: blocca chi non ha confermato il picco
                                    self._log_m2("🚫",
                                        f"GATE PEAK: SCARTATO ({_gp_motivo}) — non si e' "
                                        f"dichiarato maschio nell'osservazione, NON entra")
                                    self._rit_aggancio_ts = None
                                    self._rit_prezzo_nascita = None
                                    self._rit_picco_pre = None
                                    self._rit_crollo_min = None
                                    try:
                                        self._ritardo_stats["gate_peak_scartati"] = self._ritardo_stats.get("gate_peak_scartati", 0) + 1
                                    except Exception:
                                        pass
                                    try:
                                        self._record_phantom(price, "MINA_GATE_PEAK",
                                                             float(seed.get("score", 0) or 0),
                                                             str(momentum), str(volatility), str(trend))
                                    except Exception:
                                        pass
                                    return
                        except Exception as _e_gp:
                            log.debug(f"[GATE_PEAK_ERR] {_e_gp}")

                    # passato (maschio chiaro o resistente dopo i 2s) -> entro ORA, azzero
                    self._rit_aggancio_ts = None
                    self._rit_prezzo_nascita = None
                    self._rit_picco_pre = None
                    self._ritardo_stats["entrati"] = self._ritardo_stats.get("entrati", 0) + 1
                    # VEDERE DENTRO (8giu): marco entrato=1 sulla riga di questo aggancio.
                    # Cosi' nel DB: entrato=0 = scansato, entrato=1 = passato. Solo update.
                    _rid = getattr(self, "_rit_aggancio_rowid", None)
                    if _rid is not None:
                        _up = None
                        try:
                            import sqlite3 as _sq3up
                            _up = _sq3up.connect(DB_PATH, timeout=15)
                            _up.execute("PRAGMA busy_timeout=15000;")
                            _up.execute("UPDATE ritardo_agganci SET entrato=1 WHERE id=?", (_rid,))
                            _up.commit()
                        except Exception:
                            pass
                        finally:
                            if _up is not None:
                                try: _up.close()
                                except Exception: pass
                        self._rit_aggancio_rowid = None
                    self._log("⏱️", f"RITARDO ingresso: confermato dopo "
                                    f"{_rit_atteso:.1f}s, entro @ ${price:.1f}")

            if getattr(self, "_maschio_diretto_in_corso", False):
                self._log_m2("✅", f"OPEN: arrivato all'apertura, creo il trade ORA (price={price:.1f})")
            self._shadow = {
                "price_entry":   price,
                # FIX picco azzerato (per-posizione, non variabile condivisa):
                # catturo il picco/crollo PROPRI di QUESTO trade dentro _shadow,
                # immune a sovrascritture da osservazioni/posizioni successive.
                "picco_ingresso_reale":  round(float(getattr(self, "_trade_picco_ingresso", None) or getattr(self, "_canc_picco_proprio", 0.0) or 0.0), 3),
                "crollo_ingresso_reale": round(float(getattr(self, "_canc_crollo_proprio", 0.0) or 0.0), 3),
                "nato_ts":       time.time(),   # istante di nascita, per filmato 10s/20s
                "pnl_10s":       None,           # fotografia PnL a 10 secondi (riempita dopo)
                "pnl_20s":       None,           # fotografia PnL a 20 secondi (riempita dopo)
                "direction":     self.campo._direction,
                "duration_avg":  matrimonio.get("duration_avg", 20),
                "score":         round(score, 2),
                "soglia":        round(soglia, 2),
                "size":          round(size, 3),
                "pb_signals":    pb_signals,
                "regime_entry":  self._regime_current,
                "matrimonio":    matrimonio_name,
                "fingerprint_wr": round(fingerprint_wr, 3),
                "seed":          round(seed.get('score', 0), 3),
                # ════════════════════════════════════════════════════════════
                # SEME D'INGRESSO (4giu, Roberto: "tutto questo PRIMA del trade")
                # ════════════════════════════════════════════════════════════
                # Il valore ESATTO su cui il gate ha deciso all'ENTRY — stessa
                # finestra e formula della riga ~11714/11716. _seed_history non
                # e' cambiata tra il gate e qui (stessa chiamata sincrona), quindi
                # seme_entry == _seme_medio del gate. NON contaminato dall'exit.
                # Da qui si misura il cromosoma per quello che e': un PRE-segnale.
                "seme_entry":      (lambda s: round((s[0] + s[-1]) / 2.0, 4) if len(s) >= 2 else None)(list(getattr(self.campo, "_seed_history", []))[-5:]),
                "seed_traj_entry": list(getattr(self.campo, "_seed_history", []))[-5:],
                "ts_entry":      time.time(),
                # L1.5: contesto entry per VERITAS GATE
                "momentum_entry":   momentum,
                "volatility_entry": volatility,
                "trend_entry":      trend,
                # ════════════════════════════════════════════════════════════
                # SENSORI DI NASCITA — "il prima del seme" (3giu, Roberto)
                # ════════════════════════════════════════════════════════════
                # Salvo nello shadow la firma del campo ALLA NASCITA del trade
                # VERO (lo shadow esiste solo per i trade reali, non per le 30k
                # valutazioni abortite). Alla chiusura observe_exit li scrive nel
                # canvas insieme all'esito → ogni riga = un trade completo
                # (nascita+morte) SENZA aggancio temporale fragile.
                # Questi vengono dal seed, l'unico calcolo non saturo verificato.
                "nascita_range_pos":   seed.get('range_pos'),
                "nascita_drift_slope": seed.get('drift_slope'),
                "nascita_seed_score":  seed.get('score'),
                "nascita_compression": seed.get('compression'),
                "nascita_drift_persist": seed.get('drift_persist'),
                "nascita_vol_pressure": seed.get('vol_pressure'),
                "nascita_comp_duration": seed.get('comp_duration'),
                "nascita_sign_flips":   seed.get('sign_flips'),
                # ════════════════════════════════════════════════════════════
                # PICCO PRE-INGRESSO (15giu2026, Roberto) — TRACCIO IL BYPASS
                # ════════════════════════════════════════════════════════════
                # Salvo il picco massimo raggiunto durante il ritardo di
                # osservazione (i secondi gratis PRIMA dell'apertura). Permette
                # di vedere quale picco_pre aveva il trade quando GATE PEAK
                # ha dato il via libera. Se trade morti hanno picco_pre>=1.0$
                # è il fenomeno "maschio svuotato". Se picco_pre<1.0$ allora
                # GATE PEAK ha un bypass: vanno trovati e chiusi.
                # Letto da self._rit_picco_pre (popolato dal blocco
                # CONTROSCATTO/RIPIEGAMENTO durante l'attesa).
                "rit_picco_pre":    getattr(self, "_rit_picco_pre", None),
            }
            self._shadow_entry_time        = time.time()
            self._shadow_max_price         = price
            self._shadow_min_price         = price
            self._shadow_matrimonio        = matrimonio_name

            # FIX TRACCIATURA 27mag2026: trasferisco la firma P1 nel _shadow.
            # Setattata in _evaluate_shadow_entry Percorso 1 PRIMA di chiamare
            # _open_shadow_position. Alla chiusura (riga ~12104+) il check
            # "_winsig_entry in self._shadow" sarà True e la firma viene salvata.
            if hasattr(self, '_pending_winsig_p1') and self._pending_winsig_p1 is not None:
                self._shadow['_winsig_entry'] = self._pending_winsig_p1
                self._shadow['_winsig_match_at_entry'] = None  # non calcolato in P1
                self._pending_winsig_p1 = None  # consumato

            # FIX TRACCIATURA 27mag2026: trasferisco _canvas_tid P1 nel _shadow.
            # Per Percorso 2 il transfer avviene in _evaluate_shadow_entry riga 11513.
            # Per Percorso 1 NON c'era. Aggiunto qui: leggo l'attributo temporaneo
            # _pending_canvas_tid_p1 settato prima della chiamata e lo trasferisco.
            if hasattr(self, '_pending_canvas_tid_p1') and self._pending_canvas_tid_p1:
                self._shadow['_canvas_tid'] = self._pending_canvas_tid_p1
                self._pending_canvas_tid_p1 = None  # consumato

            # ═════════════════════════════════════════════════════════════════
            # HOOK CAPSULE V2.0 — Entry Side (22mag2026)
            # ─────────────────────────────────────────────────────────────────
            # Se è attivo il flag CAPSULE_V2_HOOK_ENABLED, chiediamo a /canvas
            # se c'è una capsula OPERATORE che presidia questo (regime, direction).
            # Salviamo l'eventuale ID per attribuirgli il PnL all'exit.
            # NOTA: NON modifica la decisione del bot. V16 ha già deciso di entrare.
            # Questa è SOLO una telefonata "ehi, chi presidia? me lo segno".
            # ═════════════════════════════════════════════════════════════════
            self._shadow_capsula_v2_attribuita = None
            if os.environ.get("CAPSULE_V2_HOOK_ENABLED", "false").lower() == "true":
                try:
                    import requests as _rq_cv2
                    _direction = self._shadow.get('direction', 'LONG')
                    _r_cv2 = _rq_cv2.get(
                        "http://localhost:10000/canvas/capsule_v2/per_contesto",
                        params={"regime": self._regime_current,
                                "direction": _direction,
                                "tipo": "OPERATORE"},
                        timeout=2
                    )
                    if _r_cv2.status_code == 200:
                        _cap_v2 = _r_cv2.json().get("capsula")
                        if _cap_v2 and _cap_v2.get("stato") == "FUNZIONA":
                            self._shadow_capsula_v2_attribuita = _cap_v2.get("id")
                            self._log_m2("🧬", f"CAPSULE_V2_ENTRY: trade attribuito a {self._shadow_capsula_v2_attribuita} "
                                              f"(ruolo={_cap_v2.get('ruolo')})")
                except Exception as _e_cv2:
                    # Niente blocchi. V16 procede come se l'hook non esistesse.
                    pass

            # ════════════════════════════════════════════════════════════════
            # PATCH 6 BUG 13 — Cattura firma ambientale + match WIN precedenti
            # ════════════════════════════════════════════════════════════════
            # Fotografia del contesto al momento dell'entry. Verrà salvata al
            # close come "winning_signature" SOLO se il trade chiude WIN_NET.
            # Inoltre calcoliamo il match con l'ultima firma WIN viva: se
            # l'ambiente attuale assomiglia a quello che ha vinto prima, il
            # match sarà alto. Solo dato — nessuna decisione presa qui.
            try:
                if hasattr(self, '_winsig') and self._winsig is not None:
                    # Sensori vivi al momento dell'entry
                    _pred_st_entry = self.supercervello._get_pred_state() \
                        if hasattr(self, 'supercervello') and self.supercervello is not None \
                        else {'source': 'NO_SC'}
                    _rsi_entry = getattr(self.campo, '_last_rsi', 50)
                    _drift_entry = 0.0
                    if len(self.campo._prices_long) >= 100:
                        _p = list(self.campo._prices_long)
                        _drift_entry = (sum(_p[-50:])/50 - sum(_p[:50])/50) \
                                       / (sum(_p[:50])/50) * 100
                    # ════════════════════════════════════════════════════════════════
                    # PATCH 13 BUG 20b — Cattura segnali pre-entry (OSSERVATIVO)
                    # ════════════════════════════════════════════════════════════════
                    # Estrae i 4 segnali di _pre_breakout_factor:
                    #   - pb_signals (int 0-3)
                    #   - pb_compression / pb_volume_acc / pb_seed_directed (bool)
                    # Derivati dal 'details' testuale ritornato dalla funzione esistente:
                    #   COMPRESS= → pb_compression
                    #   VOL_ACC=  → pb_volume_acc
                    #   SEED_DIR  → pb_seed_directed  (cattura sia SEED_DIR= che SEED_DIR4=)
                    #
                    # Guardrail anti-regressione: se il calcolo fallisce → tutti None,
                    # firma salvata comunque, nessun blocco entry.
                    # ════════════════════════════════════════════════════════════════
                    _pb_signals_entry = None
                    _pb_compression_entry = None
                    _pb_volume_acc_entry = None
                    _pb_seed_directed_entry = None
                    try:
                        _pb_factor, _pb_details, _pb_sig_count = self.campo._pre_breakout_factor()
                        _pb_signals_entry = int(_pb_sig_count)
                        _pb_compression_entry = ("COMPRESS=" in _pb_details)
                        _pb_volume_acc_entry = ("VOL_ACC=" in _pb_details)
                        _pb_seed_directed_entry = ("SEED_DIR" in _pb_details)
                    except Exception:
                        # Lasciali a None — non blocca nulla
                        pass
                    # ════════════════════════════════════════════════════════════════
                    # PATCH 13 BUG 20c — Cattura storico post-trade del fingerprint
                    # ════════════════════════════════════════════════════════════════
                    # Letti AL MOMENTO DELL'ENTRY usando lo storico già presente.
                    # NON dal post-trade del trade corrente (anti-leakage).
                    #
                    #   fp_exit_too_early_rate: get_exit_too_early_rate(fp) — % storica
                    #     che il bot ha chiuso troppo presto su questo fingerprint
                    #   fp_post_delta_avg: media dei post_delta storici per questo fp
                    #     (quanto si è mosso il prezzo dopo l'exit in passato)
                    # ════════════════════════════════════════════════════════════════
                    _fp_too_early_entry = None
                    _fp_post_delta_avg_entry = None
                    try:
                        _fp_key = self.oracolo._fp(
                            momentum, volatility, trend, self.campo._direction
                        )
                        # exit_too_early_rate: default 0.5 (neutro) se dati < 3
                        _fp_too_early_entry = float(self.oracolo.get_exit_too_early_rate(_fp_key))
                        # post_delta_avg: media storica dal _memory del fingerprint
                        _mem_fp = self.oracolo._memory.get(_fp_key)
                        if _mem_fp and 'post_delta' in _mem_fp and len(_mem_fp['post_delta']) > 0:
                            _pd_list = list(_mem_fp['post_delta'])
                            _fp_post_delta_avg_entry = sum(_pd_list) / len(_pd_list)
                        # Se < 1 sample → None (nessun dato storico ancora)
                    except Exception:
                        # Lasciali a None — non blocca nulla
                        pass
                    # ════════════════════════════════════════════════════════════════
                    # CABLAGGIO_25MAG2026 — Estrazione TESTIMONI TSUNAMI per firma trade
                    # ════════════════════════════════════════════════════════════════
                    # Replica esatta del pattern di _record_phantom (riga 12696-12713).
                    # I valori vivono in self.tsunami.last_decision()['verdetti'][tf]
                    # con tf in {'30s', '2min', '10min'} e last_decision()['confidenza'].
                    # Default None se Tsunami non istanziato o decisione assente:
                    # backward-compat garantita.
                    # ════════════════════════════════════════════════════════════════
                    _ts_30s_str = _ts_30s_dir = _ts_30s_coe = None
                    _ts_2_str = _ts_2_dir = _ts_2_coe = None
                    _ts_10_str = _ts_10_dir = _ts_10_coe = None
                    _ts_conf = None
                    try:
                        if hasattr(self, 'tsunami') and self.tsunami is not None:
                            _last_ts = self.tsunami.last_decision()
                            if _last_ts is not None:
                                _v_ts = _last_ts.get('verdetti', {})
                                _t30 = _v_ts.get('30s', {})
                                _ts_30s_str = _t30.get('strength')
                                _ts_30s_dir = _t30.get('direction')
                                _ts_30s_coe = _t30.get('coerenza')
                                _t2 = _v_ts.get('2min', {})
                                _ts_2_str = _t2.get('strength')
                                _ts_2_dir = _t2.get('direction')
                                _ts_2_coe = _t2.get('coerenza')
                                _t10 = _v_ts.get('10min', {})
                                _ts_10_str = _t10.get('strength')
                                _ts_10_dir = _t10.get('direction')
                                _ts_10_coe = _t10.get('coerenza')
                                _ts_conf = _last_ts.get('confidenza')
                    except Exception:
                        # Mai bloccare l'entry per un errore di lettura Tsunami
                        pass
                    # ════════════════════════════════════════════════════════════════
                    _sig_entry = WinningSignatureLogger.build_signature_from_context(
                        momentum=momentum,
                        volatility=volatility,
                        trend=trend,
                        direction=self.campo._direction,
                        regime=self._regime_current,
                        oi_stato=self._oi_stato,
                        oi_carica=self._oi_carica,
                        vol_pressure=getattr(self.campo, '_last_vol_pressure', None),
                        rsi=_rsi_entry,
                        drift=_drift_entry,
                        score=score,
                        soglia=soglia,
                        # CABLAGGIO_25MAG2026: leggi da heartbeat_data (dove vengono scritti)
                        # PRIMA: getattr(self, '...', 0) → sempre 0 (attributo inesistente, bug cablaggio)
                        # DOPO: self.heartbeat_data.get(...) → valori reali calcolati a riga 9688-9689 / 6318
                        pred_delta_fuoco=(self.heartbeat_data.get('pred_delta_fuoco', 0) if hasattr(self, 'heartbeat_data') else 0),
                        pred_delta_carica=(self.heartbeat_data.get('pred_delta_carica', 0) if hasattr(self, 'heartbeat_data') else 0),
                        pred_v2_delta=(self.heartbeat_data.get('pred_v2_delta', 0) if hasattr(self, 'heartbeat_data') else 0),
                        pred_source=_pred_st_entry.get('source', 'UNKNOWN'),
                        # PATCH 13: 6 nuovi campi osservativi
                        pb_signals=_pb_signals_entry,
                        pb_compression=_pb_compression_entry,
                        pb_volume_acc=_pb_volume_acc_entry,
                        pb_seed_directed=_pb_seed_directed_entry,
                        fp_exit_too_early_rate=_fp_too_early_entry,
                        fp_post_delta_avg=_fp_post_delta_avg_entry,
                        # CABLAGGIO_25MAG2026: passo i 10 testimoni Tsunami estratti sopra
                        ts_30s_strength=_ts_30s_str,
                        ts_30s_direction=_ts_30s_dir,
                        ts_30s_coerenza=_ts_30s_coe,
                        ts_2min_strength=_ts_2_str,
                        ts_2min_direction=_ts_2_dir,
                        ts_2min_coerenza=_ts_2_coe,
                        ts_10min_strength=_ts_10_str,
                        ts_10min_direction=_ts_10_dir,
                        ts_10min_coerenza=_ts_10_coe,
                        ts_confidenza=_ts_conf,
                    )
                    _match_at_entry = self._winsig.compute_match_at_entry(_sig_entry)
                    # Salviamo dentro lo shadow per uso al close
                    self._shadow['_winsig_entry'] = _sig_entry
                    self._shadow['_winsig_match_at_entry'] = _match_at_entry
                    if _match_at_entry is not None:
                        self._log_m2("🔬",
                            f"WINSIG entry match_with_last_WIN={_match_at_entry:.2f} "
                            f"(0=diverso, 1=identico)")
            except Exception as _wse:
                # Mai bloccare l'entry per un problema del logger osservativo
                pass

            # ═══════════════════════════════════════════════════════════
            # PATCH 15 BUG 22 — Trasferisco _canvas_tid da verbale a shadow
            # _verbale["_canvas_tid"] è stato settato nell'hook di entry.
            # Lo salvo dentro self._shadow per recuperarlo a close.
            # ═══════════════════════════════════════════════════════════
            try:
                _ct = _verbale.get("_canvas_tid") if isinstance(_verbale, dict) else None
                if _ct and self._shadow:
                    self._shadow["_canvas_tid"] = _ct
            except Exception as _csv:
                log.debug(f"[CANVAS_TID_TRANSFER_ERR] {_csv}")
            # ════════════════════════════════════════════════════════════════

            # ── LATENCY TRACKER: misura slippage decisione→esecuzione ────
            # Il prezzo della decisione è stato registrato in _decision_price.
            # Il prezzo qui è quello effettivo dell'apertura.
            # La differenza è il costo della latenza.
            try:
                if self._decision_price > 0:
                    _lat_elapsed  = time.time() - self._decision_ts
                    _slip_abs     = abs(price - self._decision_price)
                    _slip_pct     = _slip_abs / self._decision_price * 100
                    _is_explosive = self._decision_regime == "EXPLOSIVE"
                    _exposure     = self.TRADE_SIZE_USD * self.LEVERAGE
                    _btc_qty      = _exposure / self._decision_price
                    _costo_usd    = _slip_abs * _btc_qty

                    # Aggiorna stats
                    ls = self._latency_stats
                    ls['n_total']       += 1
                    ls['slippage_sum']  += _slip_pct
                    ls['costo_usd_tot'] += _costo_usd
                    if _slip_pct > ls['slippage_max']:
                        ls['slippage_max'] = round(_slip_pct, 4)

                    if _is_explosive:
                        ls['n_explosive']      += 1
                        ls['slippage_sum_exp'] += _slip_pct
                        ls['costo_usd_exp']    += _costo_usd
                        if _slip_pct > ls['slippage_max_exp']:
                            ls['slippage_max_exp'] = round(_slip_pct, 4)

                    # Log evento
                    _avg_slip = ls['slippage_sum'] / ls['n_total']
                    _evento = {
                        'ts':        time.strftime('%H:%M:%S'),
                        'regime':    self._decision_regime,
                        'slip_pct':  round(_slip_pct, 4),
                        'costo_usd': round(_costo_usd, 3),
                        'elapsed_ms':round(_lat_elapsed * 1000, 1),
                        'allarme':   _slip_pct > 0.05,
                    }
                    ls['storia'].append(_evento)
                    if len(ls['storia']) > 20:
                        ls['storia'] = ls['storia'][-20:]

                    # Log visibile se slippage significativo
                    if _slip_pct > 0.02:
                        _tag = "🔴 LATENZA CRITICA" if _slip_pct > 0.05 else "🟡 LATENZA"
                        self._log_m2("⏱", f"{_tag}: slip={_slip_pct:.3f}% "
                                         f"costo=${_costo_usd:.2f} "
                                         f"elapsed={_lat_elapsed*1000:.0f}ms "
                                         f"regime={self._decision_regime}")
            except Exception as _lt_e:
                log.debug(f"[LATENCY_TRACK] {_lt_e}")

            # FIX CRITICO: setta contesto entry per Oracolo e divorzi
            self._shadow_entry_momentum    = momentum
            self._shadow_entry_volatility  = volatility
            self._shadow_entry_trend       = trend
            self._shadow_entry_fingerprint = fingerprint_wr




            self._m2_trades += 1
            self._log_m2("📈", f"SHADOW APERTA {self.campo._direction} "
                              f"price={price:.2f} size={size:.3f} "
                              f"score={score:.1f}/{soglia:.1f} "
                              f"matrimonio={matrimonio_name}")

            # Telemetry
            try:
                ctx = self._tele_ctx()
                self.telemetry.log_trade_entry(
                    trade_direction=self.campo._direction,
                    score=score, soglia=soglia,
                    matrimonio=matrimonio_name,
                    **{k: ctx[k] for k in ('regime','direction','open_position',
                       'active_threshold','drift','macd','trend','volatility')}
                )
            except Exception as _te:
                log.debug(f"[TELEMETRY_OPEN] {_te}")

        except Exception as e:
            log.error(f"[OPEN_SHADOW_ERROR] {e}")
            self._shadow = None


    def _save_curva_nascita(self, trade_ts, firma, curva):
        """
        CURVA DI NASCITA — MICROSCOPIO (31mag, Roberto).
        Salva la curva di vita del trade: lista di (secondi_da_nascita,
        pnl_live, peak_so_far). VISIBILITA' ESTESA (1giu, opzione B):
        nascita densa 0-10.5s (ogni tick) + evoluzione ogni 3s fino alla
        chiusura. Una riga per trade, flush alla chiusura. Solo osserva.
          - pnl_a_10s   = PnL al punto piu' vicino a 10s (confrontabile con lo storico)
          - pnl_finale  = PnL all'ultimo punto della curva (alla chiusura)
        """
        try:
            if not curva:
                return
            n_punti = len(curva)
            # pnl_a_10s: il punto piu' vicino a 10s (non l'ultimo: la curva va oltre)
            _p10 = min(curva, key=lambda p: abs(p[0] - 10.0))
            pnl_a_10s = _p10[1]
            pnl_finale = curva[-1][1]
            peak_nascita = max((p[2] for p in curva), default=0.0)
            t_peak = next((p[0] for p in curva if p[2] >= peak_nascita), None)
            conn = _safe_connect(DB_PATH, timeout=30)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS curva_nascita (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_ts     REAL,
                    firma        TEXT,
                    n_punti      INTEGER,
                    peak_nascita REAL,
                    t_peak_s     REAL,
                    pnl_a_10s    REAL,
                    curva_json   TEXT,
                    created_ts   REAL DEFAULT (strftime('%s','now'))
                )
            """)
            # Migrazione dolce: aggiunge pnl_finale se la tabella e' vecchia
            try:
                _cols = [r[1] for r in conn.execute("PRAGMA table_info(curva_nascita)").fetchall()]
                if 'pnl_finale' not in _cols:
                    conn.execute("ALTER TABLE curva_nascita ADD COLUMN pnl_finale REAL")
            except Exception as _ae:
                log.debug(f"[CURVA_NASCITA_MIGRATE] {_ae}")
            conn.execute("""
                INSERT INTO curva_nascita
                    (trade_ts, firma, n_punti, peak_nascita, t_peak_s, pnl_a_10s, curva_json, pnl_finale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_ts, firma, n_punti, round(peak_nascita, 4),
                  t_peak, round(pnl_a_10s, 4), json.dumps(curva), round(pnl_finale, 4)))
            conn.commit()
            conn.close()
        except Exception as _e:
            log.debug(f"[SAVE_CURVA_NASCITA_ERR] {_e}")

    def _evaluate_shadow_exit(self, price, momentum, volatility, trend):
        """Stessa logica di uscita V15 + BreathEngine V16 per timing ottimale."""
        try:
            if not self._shadow:
                return
            if price > self._shadow_max_price:
                self._shadow_max_price = price
            if price < self._shadow_min_price:
                self._shadow_min_price = price


            # ── V16: TRACKING PICCO INTRA-TRADE ────────────────────────
            _ep  = self._shadow.get('price_entry', price)
            _bq  = (self.TRADE_SIZE_USD * self.LEVERAGE) / max(_ep, 1)
            _ed  = self._shadow.get('direction', 'LONG')
            _pnl_live = ((price - _ep) if _ed == 'LONG' else (_ep - price)) * _bq - self.FEE_TRADE
            if _pnl_live > self._trade_peak_pnl:
                self._trade_peak_pnl  = _pnl_live
                self._trade_peak_ts   = time.time()

            duration     = time.time() - self._shadow_entry_time
            duration_avg = self._shadow["duration_avg"]

            # CRITICO: direzione al momento dell'ENTRY, non quella attuale
            entry_direction = self._shadow.get("direction", "LONG")

            # ════════════════════════════════════════════════════════════
            # CURVA DI NASCITA — MICROSCOPIO (31mag, Roberto)
            # ════════════════════════════════════════════════════════════
            # NON 4 fotografie (occhiali) — OGNI TICK nei primi 10s di vita
            # (microscopio). Cattura lo SCHIZZO iniziale e il momento esatto
            # in cui il trade matura o muore, che tra due checkpoint fissi si
            # perdeva. Su stream @aggTrade arrivano più tick/sec → curva densa.
            #
            # VISIBILITA' ESTESA (1giu, Roberto, opzione B):
            #   - 0-10.5s: OGNI TICK (nascita densa — qui si decide il sesso).
            #   - dopo 10.5s: UN CAMPIONE ogni 3s fino alla chiusura (evoluzione).
            #     Niente piu' buco cieco tra i 10s e l'uscita.
            #   - flush UNICO alla chiusura (in _close_shadow_trade), NON a 10.5s.
            #     Una scrittura per trade → niente raffica, niente disco pieno.
            #   - tetto duro MICRO_MAX_PUNTI: la lista non esplode mai.
            # Solo osserva: zero impatto sulle decisioni del bot.
            try:
                _micro = self._shadow.setdefault('_micro_nascita', [])
                if len(_micro) < self.MICRO_MAX_PUNTI:
                    if duration <= 10.5:
                        # Nascita: ogni tick
                        _micro.append((round(duration, 3), round(_pnl_live, 4),
                                       round(self._trade_peak_pnl, 4)))
                    else:
                        # Evoluzione: un campione ogni MICRO_PASSO_S secondi
                        _last_t = _micro[-1][0] if _micro else 0.0
                        if (duration - _last_t) >= self.MICRO_PASSO_S:
                            _micro.append((round(duration, 3), round(_pnl_live, 4),
                                           round(self._trade_peak_pnl, 4)))
            except Exception as _ne:
                log.debug(f"[CURVA_NASCITA_ERR] {_ne}")



            # ── CAPSULE INTELLIGENTE EXIT — uscita anticipata predittiva ────
            if duration > 5:
                try:
                    _ci_exit_ctx = {
                        'breath_fase':    (self._breath._fase    if _V16_ENGINES_OK and self._breath else 'NEUTRO'),
                        'nervosismo':     (self._nerv._nervosismo if _V16_ENGINES_OK and self._nerv  else 0.3),
                        'comparto':       (self._comparto._attivo if _V16_ENGINES_OK and self._comparto else 'NEUTRO'),
                        'oi_stato':       self._oi_stato,
                        'oi_carica':      self._oi_carica,
                        'regime':         self._regime_current,
                        'drift':          0.0,
                        'gomme':          (self._nerv._gomme_attuale if _V16_ENGINES_OK and self._nerv else 'INTER'),
                    }
                    _ci_exit = self.ci.get_exit_signal(_ci_exit_ctx)
                    if _ci_exit['esci']:
                        self._log_m2("💊", f"CI_EXIT: {_ci_exit['motivo']}")
                        self._close_shadow_position(
                            price, momentum, volatility, trend,
                            reason="CI_EXIT_ANTICIPATA"
                        )
                        return
                except Exception as _ci_ex:
                    log.debug(f"[CI_EXIT_ERR] {_ci_ex}")

            # ── BREATH EXIT — priorità alta + L2 medium-protect ─────────────
            if _V16_ENGINES_OK and self._breath and duration > 10:
                _nerv_val = getattr(self._nerv, '_nervosismo', 0.3) if self._nerv else 0.3

                class _FakePos:
                    direction   = entry_direction
                    entry_price = self._shadow.get("entry_price", price)
                    entry_time  = self._shadow_entry_time

                b_exit = self._breath.segnale_exit(_FakePos(), _nerv_val)

                # Calcolo profit lordo per decidere se attivare anche urgenza MEDIA
                if entry_direction == "SHORT":
                    _pl_brth = (self._shadow["price_entry"] - price) * (5000.0 / self._shadow["price_entry"])
                else:
                    _pl_brth = (price - self._shadow["price_entry"]) * (5000.0 / self._shadow["price_entry"])

                # ALTA/CRITICA → exit sempre (ESALAZIONE forte, peak superato)
                # MEDIA → exit SOLO se profit nel range calibrato per momentum:
                #   FORTE: range $1.50-$3.00 (lascia correre fino $1.50, poi proteggi)
                #   MEDIO: range $1.00-$2.50 (protezione standard)
                #   DEBOLE: range $0.50-$2.00 (esci appena puoi)
                # Sopra il range superiore: lascia gestire al PROFIT_LOCK
                # Sotto il range inferiore: aspetta che il trade respiri
                _entry_mom_breath = self._shadow_entry_momentum or momentum
                if _entry_mom_breath == "FORTE":
                    _br_min, _br_max = 1.50, 3.00
                elif _entry_mom_breath == "MEDIO":
                    _br_min, _br_max = 1.00, 2.50
                else:  # DEBOLE
                    _br_min, _br_max = 0.50, 2.00
                
                _b_urg = b_exit.get("urgenza", "")
                _b_ok  = b_exit.get("ok", False)

                _exit_now = False
                if _b_ok:
                    if _b_urg in ("ALTA", "CRITICA"):
                        _exit_now = True
                    elif _b_urg == "MEDIA" and _br_min < _pl_brth < _br_max:
                        _exit_now = True
                        b_exit["motivo"] = f"VOLPE_BREATH_{_entry_mom_breath}+pf ${_pl_brth:+.2f} | {b_exit.get('motivo','')}"

                if _exit_now:
                    self._log_m2("🌬", f"BREATH_EXIT: {b_exit['motivo']}")
                    self._close_shadow_position(
                        price, momentum, volatility, trend,
                        reason=f"BREATH_{_b_urg}"
                    )
                    return

            # -- HARD STOP LOSS 2% SUL PNL REALE --------------------------
            # Stop sul PnL della posizione, non sul prezzo.
            # Formula: delta% × esposizione
            # 2% di $5000 esposizione = $100 max loss
            # Su SOL $130: $100 / 38.46 = $2.60 movimento — ragionevole
            exposure_sl = self.TRADE_SIZE_USD * self.LEVERAGE
            btc_qty_sl = exposure_sl / self._shadow["price_entry"]
            if entry_direction == "SHORT":
                current_pnl_real = (self._shadow["price_entry"] - price) * btc_qty_sl
            else:
                current_pnl_real = (price - self._shadow["price_entry"]) * btc_qty_sl

            # ════════════════════════════════════════════════════════════════
            # FIX #28 (12mag2026 sera): ZONA_MORTA EXIT da V13.5
            # ════════════════════════════════════════════════════════════════
            # Logica trapiantata dal sistema vincente V13.5 (23feb2026) che
            # nel PDF "OVERTOP_ANALISI_PREDITTIVA_V1" (marzo 2026) ha generato
            # +$62.54 in 16h LIVE reali su BTCUSDC.
            #
            # Scoperta PDF: 17 trade con durata 1.2s medio in ZONA_MORTA →
            # WR 0%, perdita garantita -$28.42 totali. Tutti in NORMAL mode.
            #
            # Regola V13.5 (riga 601-603 di OVERTOP_BASSANO_VEDO_proattivo_23_02.py):
            #   if modalita == 'NORMAL' and 2.0 <= duration < 10.0:
            #       if pnl_corrente < 0: return "ZONA_MORTA"
            #
            # Adattamento V16: non abbiamo modalità NORMAL/FLAT esplicita →
            # applichiamo SEMPRE (più conservativo, blocca perdite garantite).
            # ════════════════════════════════════════════════════════════════
            # PATCH 2 BUG 5 (16mag2026): ZONA_MORTA adattiva al regime.
            # Prima: SEMPRE 2-10s → tagliava anche movimenti vivi in
            # EXPLOSIVE/TRENDING. Tutti i ZONA_MORTA del 16mag erano 2-9s
            # con PnL -$2/-$3. Coerente con 17 trade del PDF (V13.5 NORMAL).
            # Adesso: finestra ZONA_MORTA dipende dal regime corrente.
            #   RANGING (mercato fermo) → 2-10s come prima (originale V13.5)
            #   EXPLOSIVE/TRENDING_BULL/TRENDING_BEAR → lascia respirare ≥15s
            # In regimi vivi 3-9s è respirazione, non morte del trade.
            # ════════════════════════════════════════════════════════════════
            # REGOLA TIMEFRAME LUNGO (29mag, Roberto): "non blocchiamo il trade
            # se non abbiamo guadagnato. Se abbiamo guadagnato lo chiudiamo, se
            # non abbiamo guadagnato andiamo avanti." A 60s la fee ($2) uccide:
            # il movimento medio non la copre. Il trade DEVE poter maturare.
            # ZONA_MORTA chiudeva i trade giovani (2-10s) in perdita per TEMPO —
            # DISATTIVATA. Il tempo non chiude più. Chiudono solo:
            #  - il PROFITTO (sopra, quando supera la fee → incassa)
            #  - lo STOP LOSS 2% (HARD_STOP qui sotto, INTATTO → il freno se frana)
            # ════════════════════════════════════════════════════════════════
            _regime_now = getattr(self, "_regime_current", "RANGING") or "RANGING"
            # ZONA_MORTA disattivata: non si taglia per tempo un trade in perdita.
            # (Lo stop loss 2% qui sotto resta il solo freno sulle perdite.)

            HARD_STOP_USD = float(os.environ.get("HARD_STOP_USD", str(self.STOP_LIVE)))
            if current_pnl_real < -HARD_STOP_USD:
                self._close_shadow_trade(price, f"HARD_STOP_${abs(current_pnl_real):.1f}_max${HARD_STOP_USD:.0f}")
                return

            # ════════════════════════════════════════════════════════════════
            # FRENO-COLATA (1giu, Roberto) — "poche e tante sberle in faccia".
            # ════════════════════════════════════════════════════════════════
            # CASO DIMOSTRATO (trade 14:00, curva_nascita): femmina conclamata
            # (energia/peak 0.64 FISSO, mai manifestata) colata LENTA da +0.64
            # a -7.99 a gradini: -3 a 52s, -4 a 57s, -5 a 65s, HARD_STOP a 88s.
            # Il KILLER E40 NON l'ha presa: vive dentro `if exit_energy<exit_soglia`
            # (cancello del decadimento-energia) che tra 52-88s non si è aperto.
            # Così la femmina è scivolata indisturbata fino all'HARD_STOP -9.
            #
            # Questo freno sta FUORI da quel cancello (valutato a OGNI tick come
            # l'HARD_STOP) e taglia la femmina conclamata PRIMA che arrivi a -7:
            #   - peak basso (mai manifestata maschio: peak < FRENO_PEAK)
            #   - durata oltre il respiro (> KILLER_TEMPO: il maschio ha già preso E)
            #   - perdita oltre metà strada verso l'HARD_STOP (< -FRENO_PNL)
            # Il MASCHIO (peak alto) NON rientra → il suo respiro è intatto.
            # NON tocca i primi 35s (respiro). Regolabile da env, disarmabile.
            # ⚠️ TARATO SU 1 TRADE: -3.5 è metà strada verso HARD_STOP -7; peak
            # 1.5 è la soglia maschio/femmina già vista nel microscopio. Da
            # riverificare su più femmine-colata coi dati nuovi.
            # ⚠️ RITARATO 5giu su 20 trade (non più 1): le femmine che passano il
            # gate v4 hanno peak MAX 2.74; i maschi (tranne 2 piccoli) peak >= 3.6.
            # Soglia peak 1.5 -> 3.0: prende TUTTE le 9 femmine (0 sfuggite), costa
            # solo 2 maschietti (peak 1.84/+1.65 e 1.24/+1.10). Baratto: blocca 9
            # femmine (2 da -9) per 2 maschi da +1. Il +22 (peak 22) non e' toccato.
            _freno_off   = os.environ.get("FRENO_COLATA_OFF", "false").lower() == "true"
            _freno_pnl   = float(os.environ.get("FRENO_COLATA_PNL", "3.5"))
            _freno_peak  = float(os.environ.get("FRENO_COLATA_PEAK", "3.0"))
            _freno_tempo = float(os.environ.get("KILLER_TEMPO", "35"))
            if (not _freno_off
                    and current_pnl_real < -_freno_pnl
                    and self._trade_peak_pnl < _freno_peak
                    and duration >= _freno_tempo):
                self._close_shadow_trade(price,
                    f"FRENO_COLATA_peak{self._trade_peak_pnl:.1f}_dur{duration:.0f}s_{current_pnl_real:+.1f}")
                self._log_m2("🩸",
                    f"FRENO_COLATA: femmina conclamata in colata "
                    f"(peak{self._trade_peak_pnl:.1f}<{_freno_peak} dur{duration:.0f}s "
                    f"pnl{current_pnl_real:+.1f}) → tagliata prima dell'HARD_STOP")
                return

            # ════════════════════════════════════════════════════════════════
            # SPIA DIAGNOSTICA FRENO (2giu, Roberto — "perché i loss non sono
            # castrati al minimo"). Una femmina è arrivata a -9 (HARD_STOP) il
            # 14:26 nonostante peak 0.0 e 10min di colata: il freno NON è
            # scattato e non sappiamo perché. Questa spia NON cambia il
            # comportamento: scrive solo PERCHÉ il freno non scatta quando il
            # PnL è già oltre la soglia. Al prossimo loss-colata il log dirà
            # quale condizione manca (peak troppo alto? durata? off?), così
            # diagnostichiamo dai dati invece di indovinare.
            # ════════════════════════════════════════════════════════════════
            if (current_pnl_real < -_freno_pnl and not _freno_off):
                # il PnL è già oltre soglia ma il freno sopra NON ha tagliato:
                # quindi è fallita peak<soglia oppure durata>=tempo. Lo dico.
                _why = []
                if not (self._trade_peak_pnl < _freno_peak):
                    _why.append(f"peak={self._trade_peak_pnl:.2f}>={_freno_peak}(NON-femmina?)")
                if not (duration >= _freno_tempo):
                    _why.append(f"dur={duration:.0f}s<{_freno_tempo}(troppo-giovane)")
                if _why:
                    self._log_m2("🔍",
                        f"FRENO_SPIA: pnl{current_pnl_real:+.1f} oltre soglia ma NON taglio → "
                        f"{' '.join(_why)}")

            # -- MINIMUM HOLD TIME ---------------------------------------------
            # FIX: MIN_HOLD_SECONDS era dichiarato ma mai applicato.
            # Nessun divorzio nei primi 10 secondi — il trade deve respirare.
            MIN_HOLD_SECONDS = 10

            # FIX: drawdown_pct calcolato sempre — serve anche per TIMEOUT_DD
            if entry_direction == "SHORT":
                drawdown_pct = ((price - self._shadow_min_price) / self._shadow["price_entry"]) * 100
            else:
                drawdown_pct = ((self._shadow_max_price - price) / self._shadow["price_entry"]) * 100

            # ════════════════════════════════════════════════════════════════
            # SALVA_VERDE ANTICIPATO (11giu, Roberto: "non lascio grasso a
            # nessuno"). I TRANS-obiettivo fanno picco a 8-10s e crollano; il
            # MIN_HOLD di 10s impediva al SALVA_VERDE (più sotto) di incassarli
            # (15:38: picco +2.71 lordo a 10s → perso a -0.12). Qui il SALVA_VERDE
            # agisce PRIMA di ogni cancello temporale: appena c'è grasso vero che
            # cede dal picco, incassa — in qualsiasi secondo. Il maschio lento che
            # sale e NON cede non scatta (resta protetto, corre). Stessa logica
            # del SALVA_VERDE sotto, solo anticipata. Spegnibile TRAIL_OFF.
            # ════════════════════════════════════════════════════════════════
            if os.environ.get("TRAIL_OFF", "false").lower() != "true":
                _ep_sv = self._shadow["price_entry"]
                _sdelta_sv = (price - _ep_sv) if entry_direction != "SHORT" else (_ep_sv - price)
                _cur_sv = _sdelta_sv * (5000.0 / _ep_sv)   # lordo
                if entry_direction == "SHORT":
                    _max_sv = (_ep_sv - self._shadow_min_price) * (5000.0 / _ep_sv)
                else:
                    _max_sv = (self._shadow_max_price - _ep_sv) * (5000.0 / _ep_sv)
                _tg_sv = float(os.environ.get("TRAIL_GIU_USD", "0.5"))
                _vm_sv = float(os.environ.get("VERDE_MIN_USD", "2.5"))
                if _tg_sv > 0 and _cur_sv >= _vm_sv and (_max_sv - _cur_sv) >= _tg_sv:
                    _sc_sv = _max_sv - _cur_sv
                    _st_sv = "CRESC" if _sc_sv < 0.4 else "CEDE"
                    self._close_shadow_trade(price,
                        f"SALVA_VERDE*{_cur_sv:+.1f}_dapicco{_max_sv:+.1f}_{_st_sv}")
                    return

            if duration >= MIN_HOLD_SECONDS:
                # -- 4 DIVORCE TRIGGERS (attivi solo dopo MIN_HOLD) ---------------
                triggers = []
                if self._shadow_entry_volatility == "BASSA" and volatility == "ALTA" and current_pnl_real < 0:
                    triggers.append("T1_VOL")
                # T2: trend inverte CONTRO la nostra direzione
                if entry_direction == "LONG" and self._shadow_entry_trend == "UP" and trend == "DOWN":
                    triggers.append("T2_TREND")
                elif entry_direction == "SHORT" and self._shadow_entry_trend == "DOWN" and trend == "UP":
                    triggers.append("T2_TREND")
                # T3: drawdown dal migliore raggiunto
                if drawdown_pct > DIVORCE_DRAWDOWN_PCT:
                    triggers.append("T3_DD")
                # T4: FIX — scatta solo se in perdita, non se stai guadagnando
                current_fp = self.oracolo.get_wr(momentum, volatility, trend, entry_direction)
                _entry_fp = self._shadow_entry_fingerprint or 0.0
                if _entry_fp > 0:
                    fp_div = abs(current_fp - _entry_fp) / max(_entry_fp, 0.001)
                    if fp_div > DIVORCE_FP_DIVERGE_PCT and current_pnl_real < 0:
                        triggers.append("T4_FP")
                if len(triggers) >= DIVORCE_MIN_TRIGGERS:
                    self._close_shadow_trade(price, f"DIVORZIO|{'|'.join(triggers)}")
                    return

            # ===============================================================
            # EXIT INTELLIGENTE - CAMPO GRAVITAZIONALE DI USCITA
            # Stessa filosofia dell'entry: legge l'energia, non l'orologio.
            #
            # L'impulso nasce (entry), vive (hold), muore (exit).
            # L'exit misura l'energia RESIDUA dell'impulso:
            #   - Momentum ancora vivo? → resta
            #   - Prezzo ancora nella direzione? → resta  
            #   - Decelerazione forte? → prepara uscita
            #   - Inversione confermata? → esci
            #
            # Score di uscita 0-100:
            #   0  = impulso morto, esci subito
            #   50 = neutro, monitora
            #   100 = impulso ancora forte, resta dentro
            # ===============================================================
            
            if entry_direction == "LONG":
                _sdelta    = price - self._shadow["price_entry"]
                max_profit = (self._shadow_max_price - self._shadow["price_entry"]) * (5000.0 / self._shadow["price_entry"])
                retreat    = self._shadow_max_price - price
            else:
                _sdelta    = self._shadow["price_entry"] - price
                max_profit = (self._shadow["price_entry"] - self._shadow_min_price) * (5000.0 / self._shadow["price_entry"])
                retreat    = price - self._shadow_min_price
            current_pnl = _sdelta * (5000.0 / self._shadow["price_entry"])  # lordo — fee al close

            # ════════════════════════════════════════════════════════════════
            # ⭐ LA MACCHINA (23giu2026, Roberto) — FIRMA CERTIFICATA su 2000 trade.
            # ════════════════════════════════════════════════════════════════
            # SCOPERTA dai dati phantom_forensic (2000 candidati osservati):
            #
            #  MASCHIO = sopravvive OLTRE 12s con grasso -> 27/27 WIN (100%).
            #            sale dritto (mae basso), corre fino a 176s. LASCIALO CORRERE.
            #  TRANS   = grasso >=3 ma molla ENTRO 12s (nessun trans supera 12s).
            #            ballerino (mae alto), MA tutti i 116 trans (anche i 32 loss)
            #            avevano toccato picco >=3.04 PRIMA di mollare. Il grasso
            #            C'ERA -> STRAPPALO appena ha +3 lordo (=+1 netto, fee pagate),
            #            non aspettare che molli -> i loss diventano win.
            #  FEMMINA = picco <3 -> non entra nemmeno (gate ingresso).
            #
            # REGOLA UNICA in uscita: appena il grasso lordo tocca PRESA_TRANS_USD
            # (default 3.0 = +1 netto dopo 2$ fee) -> INCASSA SUBITO. Questo strappa
            # il trans mentre ha il grasso (entro i suoi 12s) e prende anche il
            # maschio veloce. Il maschio LENTO che invece continua a salire SENZA
            # ritracciare (mae basso) viene lasciato correre dal trailing sotto.
            # ════════════════════════════════════════════════════════════════
            _fee_tot = self.TRADE_SIZE_USD * self.LEVERAGE * self.FEE_PCT * 2
            # ════════════════════════════════════════════════════════════════
            # ⭐ DISTINZIONE MASCHIO/TRANS (24giu, Roberto) — CON ETICHETTA.
            # A +3 NON strappo subito (era l'errore: castravo i maschi veri che
            # correvano). Aspetto di vedere il COMPORTAMENTO dopo +3:
            #   - se CONTINUA a fare nuovi massimi (sale ancora) = MASCHIO VERO
            #     -> NON strappo, lo lascio correre al trailing (corsa massima)
            #   - se CEDE dal picco (storna oltre margine) = TRANS che molla
            #     -> STRAPPO ORA il grasso che resta, etichetta TRANS_STRAPPATO
            # L'etichetta nel reason ci dice CHE COSA era: cosi leggiamo i trade
            # senza errori (maschio corso vs trans strappato).
            # ════════════════════════════════════════════════════════════════
            if os.environ.get("PRESA_TRANS_OFF", "false").lower() != "true":
                _presa_trans = float(os.environ.get("PRESA_TRANS_USD", "3.0"))  # lordo soglia attivazione
                # soglia che separa TRANS da MASCHIO: se cede da un picco BASSO
                # (<= TRANS_PICCO_MAX) = trans che molla subito. Se cede da picco
                # ALTO (oltre) = maschio che ha corso -> lascia al trailing.
                _trans_picco_max = float(os.environ.get("TRANS_PICCO_MAX", "4.5"))
                _cede_dal_picco = max_profit - current_pnl
                _trans_cede = float(os.environ.get("TRANS_CEDE_USD", "0.5"))
                if max_profit >= _presa_trans:
                    # ha superato +3. Distinguo dal PICCO raggiunto + se cede:
                    if _cede_dal_picco >= _trans_cede and max_profit <= _trans_picco_max:
                        # cede da picco BASSO (~3-4.5) -> TRANS che molla -> strappo
                        _netto = current_pnl - _fee_tot
                        self._log_m2("🔁",
                            f"TRANS: picco basso +{max_profit:.2f}$ cede a +{current_pnl:.2f}$ "
                            f"= STRAPPO trans (+{_netto:.2f}net)")
                        return self._close_shadow_trade(price, f"TRANS_STRAPPATO_+{_netto:.1f}net_picco{max_profit:.1f}")
                    else:
                        # picco ALTO (maschio che corre) o non cede ancora -> LASCIA
                        # CORRERE. Il trailing sotto lo chiude quando storna davvero.
                        self._log_m2("🐺",
                            f"MASCHIO: picco +{max_profit:.2f}$ a +{current_pnl:.2f}$ "
                            f"= LASCIO CORRERE (no strappo, picco alto o sale ancora)")
                        # nessun return: prosegue al trailing

            # ════════════════════════════════════════════════════════════════
            # TRAILING MAE — per il maschio che corre oltre la presa: se sale dritto
            # (mae basso) lo lascia correre; appena storna (retreat >= margine) chiude.
            # Cosi' il maschio lento da +5/+8 non viene tagliato dalla presa a +3 ma
            # accompagnato fino allo storno. ENV: TRAILING_MAE_OFF, TRAILING_MARGINE.
            # ════════════════════════════════════════════════════════════════
            if os.environ.get("TRAILING_MAE_OFF", "false").lower() != "true":
                _tr_attiva  = float(os.environ.get("TRAILING_ATTIVA", "1.0"))
                _tr_margine = float(os.environ.get("TRAILING_MARGINE", "1.1"))
                _retreat_grasso = retreat * (5000.0 / self._shadow["price_entry"])
                if max_profit >= _tr_attiva and _retreat_grasso >= _tr_margine:
                    self._log_m2("✂️",
                        f"TRAILING MAE: picco +{max_profit:.2f}$ retreat {_retreat_grasso:.2f}$ "
                        f">= margine {_tr_margine:.1f} = STORNA, incasso +{current_pnl:.2f}$ lordo")
                    _esito_tr = "CORSA_VINTA" if current_pnl > 0 else "TRANS_SVUOTATO"
                    return self._close_shadow_trade(price, f"{_esito_tr}_picco{max_profit:.1f}_inc{current_pnl:.1f}")


            # ════════════════════════════════════════════════════════════════
            # ⭐ MACCHINA PURA (24giu, Roberto) — INTERRUTTORE UNICO.
            # Roberto: "non esistono antiprecipizio o cagate varie". Sotto questo
            # punto c'erano 20 uscite vecchie (CASSA_GRASSO, ANTIPRECIPIZIO,
            # PRESA_SECCA, INCASSO_10S, TRANELLO_TRANS, DECEL, LOCK, EVAPORATION,
            # KILLER, FRENO, DIVORZIO, TIMEOUT...) che chiudevano i trade al posto
            # della logica vera e la DEVIAVANO ("fa tutto tranne quello che voglio").
            # Con MACCHINA_PURA=true (default), NESSUNA di queste interviene.
            # Restano SOLO le 3 uscite della macchina, gia' valutate SOPRA:
            #   - HARD_STOP  -> rete (mai -5)
            #   - PRESA_TRANS -> strappa il trans/grasso SUBITO, fee pagate
            #   - TRAILING_MAE -> corsa del maschio (mae basso)
            # ════════════════════════════════════════════════════════════════
            if os.environ.get("MACCHINA_PURA", "true").lower() == "true":
                return None  # nessuna uscita vecchia: la macchina ha gia' deciso sopra



            # ════════════════════════════════════════════════════════════════
            # MINA CASSA_GRASSO (12giu2026, Roberto) — CATTURA DEI FURBI.
            # ════════════════════════════════════════════════════════════════
            # Sui dati (574 trade chiusi, di cui 440 LOSS):
            #   - Fascia 4 "furbi" (durata 20s+, n=190 LOSS): peak medio +0.36$,
            #     35 hanno toccato +0.5$, 26 +1.0$, 19 +1.5$, 10 +2.0$.
            #     Vivono in media 69s. Salgono, oscillano, toccano grasso reale,
            #     POI mollano. Oggi muoiono a -2.97$ medio.
            #   - Le altre fasce (rapide/medie/lente, n=250 LOSS): peak medio
            #     +0.01/+0.10/+0.12 — non c'e' grasso da catturare li'.
            # IDEA: quando un trade ha toccato un PICCO sopra soglia E sta cedendo
            # dal picco oltre una distanza, CHIUDI SUBITO. Non aspettare che torni
            # in rosso (li' interviene ANTIPRECIPIZIO che pero' non vede grasso
            # mai preso). Non aspettare TRANELLO_TRANS a 20s (troppo tardi per
            # certi furbi). La cassa scatta SOPRA ZERO, prima che il grasso evapori.
            #
            # PARAMETRI ENV (default sicuri, reversibili):
            #   CASSA_GRASSO_OFF=true   -> spenta (default)
            #   CASSA_PEAK_MIN=1.5      -> sotto questo peak, non armare (no fee mangiate)
            #   CASSA_GIU_USD=0.5       -> se cede di 0.5$ dal peak, esci
            #   CASSA_MIN_ETA=10        -> non scattare prima di 10s (lascia respirare i maschi)
            #
            # NB: ENV default OFF. Si accende solo deliberatamente con
            # CASSA_GRASSO_OFF=false. Numeri tarati sui 190 furbi visti.
            # Da validare nelle 48h successive con: SELECT COUNT(*),
            # AVG(pnl) FROM trades WHERE reason='CASSA_GRASSO';
            # ════════════════════════════════════════════════════════════════
            try:
                if os.environ.get("CASSA_GRASSO_OFF", "true").lower() != "true":
                    _nato_cg = self._shadow.get("nato_ts")
                    if _nato_cg is not None:
                        _eta_cg = time.time() - _nato_cg
                        _cg_min_eta  = float(os.environ.get("CASSA_MIN_ETA",  "10.0"))
                        _cg_peak_min = float(os.environ.get("CASSA_PEAK_MIN", "1.5"))
                        _cg_giu_usd  = float(os.environ.get("CASSA_GIU_USD",  "0.5"))
                        # max_profit e' gia' calcolato sopra (riga 13442). E' il PICCO storico del trade.
                        _peak_ok    = (max_profit >= _cg_peak_min)
                        _eta_ok     = (_eta_cg >= _cg_min_eta)
                        _ceduto     = (max_profit - current_pnl) >= _cg_giu_usd
                        _ancora_verde = (current_pnl > 0)  # solo se ANCORA in verde, no doppione con ANTIPRECIPIZIO
                        if (_peak_ok and _eta_ok and _ceduto and _ancora_verde
                                and not self._shadow.get("_cg_gia_tagliato")):
                            self._shadow["_cg_gia_tagliato"] = True
                            self._log("💰", f"CASSA_GRASSO chiude @ {_eta_cg:.0f}s: "
                                            f"peak={max_profit:+.2f} ora={current_pnl:+.2f} "
                                            f"(ceduto {max_profit-current_pnl:.2f}$ dal picco) @ ${price:.1f}")
                            if not hasattr(self, "_cassa_grasso_tagli"):
                                self._cassa_grasso_tagli = 0
                            self._cassa_grasso_tagli += 1
                            _cg = None
                            try:
                                import sqlite3 as _sqcg
                                _cg = _sqcg.connect(self.db_path, timeout=15)
                                _cg.execute("PRAGMA busy_timeout=15000;")
                                _cg.execute("""CREATE TABLE IF NOT EXISTS cassa_grasso_tagli (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                    eta_s REAL, peak_max REAL, pnl_taglio REAL,
                                    ceduto_usd REAL, prezzo REAL)""")
                                _cg.execute("""INSERT INTO cassa_grasso_tagli 
                                    (eta_s,peak_max,pnl_taglio,ceduto_usd,prezzo) 
                                    VALUES (?,?,?,?,?)""",
                                    (float(_eta_cg), float(max_profit), float(current_pnl),
                                     float(max_profit - current_pnl), float(price)))
                                _cg.commit()
                            except Exception:
                                pass
                            finally:
                                if _cg is not None:
                                    try:
                                        _cg.close()
                                    except Exception:
                                        pass
                            return self._close_shadow_trade(price, "CASSA_GRASSO")
            except Exception:
                pass

            # ════════════════════════════════════════════════════════════════
            # TRANELLO ANTI-PRECIPIZIO (6giu, Roberto) — taglio AL MINIMO.
            # ════════════════════════════════════════════════════════════════
            # I dati (103 curve_nascita) hanno mostrato:
            #  - TRANS che precipitano: scendono e basta, peak quasi zero,
            #    a 10s gia' a -0.46/-2.66 e poi giu' fino a -7.75 (peggiorano
            #    di 4-7$ DOPO i 10s). Il tranello a 20s li prende troppo tardi.
            #  - MASCHI LENTI: a 10s sono in perdita (-1.0/-1.4) MA fanno un
            #    peak verde e poi risalgono a +4/+5 (t_peak 20-40s).
            # DISTINZIONE: non il tempo (a 10s si assomigliano), ma il PEAK.
            #   Il trans non vede MAI verde. Il maschio lento SI'.
            # REGOLA: dopo 8s, se in perdita E non ha MAI fatto un peak verde
            #   (max_profit < soglia) E sta scendendo -> taglio AL MINIMO ora,
            #   senza aspettare che precipiti. Chi ha visto verde -> lo salvo
            #   (e' un possibile maschio lento), lo gestisce il tranello 20s.
            # Interruttore: ANTIPREC_OFF=true lo spegne. Reversibile.
            # ⚠ Tarato su 103 curve storiche. Da validare sui nuovi trade.
            # ════════════════════════════════════════════════════════════════
            try:
                if os.environ.get("ANTIPREC_OFF", "false").lower() != "true":
                    _nato_ap = self._shadow.get("nato_ts")
                    if _nato_ap is not None:
                        _eta_ap = time.time() - _nato_ap
                        _ap_min_eta  = float(os.environ.get("ANTIPREC_MIN_ETA", "8.0"))
                        _ap_peak_min = float(os.environ.get("ANTIPREC_PEAK_MIN", "0.5"))
                        # registro il PnL precedente per capire se sta scendendo
                        _pnl_prec = self._shadow.get("_ap_pnl_prec")
                        self._shadow["_ap_pnl_prec"] = round(current_pnl, 3)
                        _sta_scendendo = (_pnl_prec is not None and current_pnl < _pnl_prec)
                        _mai_verde     = (max_profit < _ap_peak_min)
                        _in_perdita    = (current_pnl < 0)
                        # 17giu2026 (Roberto) — TOLTA la condizione _sta_scendendo.
                        # Un trans col picco ~0 che cola a gradini (micro-rimbalzi
                        # di un centesimo) sfuggiva: nell'istante del rimbalzo
                        # "_sta_scendendo" era falso e lo lasciava vivere fino a
                        # TRANELLO a 47s. Ora: mai-verde + in-perdita + oltre 8s =
                        # CAPRA, taglio SUBITO senza aspettare il tick perfetto.
                        # Il maschio lento (max_profit >= ANTIPREC_PEAK_MIN) NON
                        # rientra: ha visto verde, resta protetto.
                        if (_eta_ap >= _ap_min_eta and _in_perdita
                                and _mai_verde
                                and os.environ.get("ANTIPRECIPIZIO_OFF", "false").lower() != "true"
                                and not self._shadow.get("_ap_gia_tagliato")):
                            self._shadow["_ap_gia_tagliato"] = True
                            self._log("✂️", f"ANTIPRECIPIZIO taglia TRANS @ {_eta_ap:.0f}s: "
                                            f"pnl={current_pnl:+.2f} peak_max={max_profit:+.2f} "
                                            f"(mai verde, in perdita) @ ${price:.1f}")
                            if not hasattr(self, "_antiprec_tagli"):
                                self._antiprec_tagli = 0
                            self._antiprec_tagli += 1
                            _ca = None
                            try:
                                import sqlite3 as _sqa
                                _ca = _sqa.connect(self.db_path, timeout=15)
                                _ca.execute("PRAGMA busy_timeout=15000;")
                                _ca.execute("""CREATE TABLE IF NOT EXISTS antiprec_tagli (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                    eta_s REAL, pnl_taglio REAL, peak_max REAL, prezzo REAL)""")
                                _ca.execute("INSERT INTO antiprec_tagli (eta_s,pnl_taglio,peak_max,prezzo) VALUES (?,?,?,?)",
                                            (float(_eta_ap), float(current_pnl), float(max_profit), float(price)))
                                _ca.commit()
                            except Exception:
                                pass
                            finally:
                                if _ca is not None:
                                    try:
                                        _ca.close()
                                    except Exception:
                                        pass
                            return self._close_shadow_trade(price, "ANTIPRECIPIZIO")
            except Exception:
                pass

            # ════════════════════════════════════════════════════════════════
            # FILMATO PRIMI TICK (5giu) — fotografia PnL a 10s e 20s di vita.
            # Serve PER DOPO: costruire il "primo respiro" (2o cancello).
            # NON tocca il trading, solo registra. Il maschio respira (verde subito),
            # il trans resta a zero. Confronteremo pnl_10s/pnl_20s alla raccolta.
            try:
                _nato = self._shadow.get("nato_ts")
                if _nato is not None:
                    _eta = time.time() - _nato
                    # ════════════════════════════════════════════════════════════════
                    # PRESA SECCA (16giu2026, Roberto) — PORTA A CASA IL GRASSO.
                    # ════════════════════════════════════════════════════════════════
                    # REGOLA ASSOLUTA: appena il trade tocca la soglia di profitto,
                    # CHIUDO SUBITO. Non importa se maschio, femmina o trans. Non
                    # aspetto che ceda. Non aspetto i 10 secondi. Se hai +1$ in mano
                    # a qualunque secondo, lo metto al sicuro e basta.
                    # ENV: PRESA_SECCA_OFF (default false = attiva),
                    #      PRESA_SECCA_USD (default 1.0 = chiudi appena tocchi +1$).
                    # ════════════════════════════════════════════════════════════════
                    if os.environ.get("PRESA_SECCA_OFF", "false").lower() != "true":
                        _presa_usd = float(os.environ.get("PRESA_SECCA_USD", "1.0"))
                        # current_pnl e' LORDO. Tolgo la fee (2$) per avere il NETTO.
                        # Cosi' PRESA_SECCA_USD=1.0 significa "+1$ NETTO in tasca".
                        _presa_netto = current_pnl - (self.TRADE_SIZE_USD * self.LEVERAGE * self.FEE_PCT * 2)
                        if _presa_netto >= _presa_usd:
                            self._log_m2("💰",
                                f"PRESA SECCA: +{_presa_netto:.2f}$ NETTO a {_eta:.0f}s "
                                f"(lordo +{current_pnl:.2f}$) — porto a casa SUBITO")
                            _ps = None
                            try:
                                import sqlite3 as _sqps
                                _ps = _sqps.connect(self.db_path, timeout=15)
                                _ps.execute("PRAGMA busy_timeout=15000;")
                                _ps.execute("""CREATE TABLE IF NOT EXISTS presa_secca_tagli (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                    eta_s REAL, pnl_presa REAL, prezzo REAL)""")
                                _ps.execute("""INSERT INTO presa_secca_tagli
                                    (eta_s, pnl_presa, prezzo) VALUES (?, ?, ?)""",
                                    (float(_eta), float(_presa_netto), float(price)))
                                _ps.commit()
                            except Exception:
                                pass
                            finally:
                                if _ps is not None:
                                    try:
                                        _ps.close()
                                    except Exception:
                                        pass
                            return self._close_shadow_trade(price, "PRESA_SECCA")
                    if _eta >= 10 and self._shadow.get("pnl_10s") is None:
                        self._shadow["pnl_10s"] = round(current_pnl, 3)
                    # ════════════════════════════════════════════════════════════════
                    # INCASSO AL MOMENTO GIUSTO (15giu2026, Roberto)
                    # ════════════════════════════════════════════════════════════════
                    # OSSERVAZIONE: 5 trade su 14 fascia D hanno pnl_10s >= +0.6$ ma
                    # peak_post-ingresso = 0 (picco già fatto nei secondi gratis).
                    # CASSA_GRASSO classico non li intercetta (guarda peak post).
                    # Erosione media: +1.30$ a 10s diventano -2.32$ a fine.
                    #
                    # REGOLA: dopo i 10s, se pnl_10s era "rispettabile" (>= MIN),
                    # quel valore diventa il riferimento da difendere. Se il PnL
                    # attuale scende di più di GIU_USD sotto pnl_10s, chiudo subito.
                    #
                    # ENV: INCASSO_10S_OFF (default false), INCASSO_10S_MIN (0.6),
                    # INCASSO_GIU_USD (0.4). Reversibile.
                    # ════════════════════════════════════════════════════════════════
                    if (_eta >= 10 and
                        os.environ.get("INCASSO_10S_OFF", "false").lower() != "true"):
                        _i10_min = float(os.environ.get("INCASSO_10S_MIN", "0.6"))
                        _i10_giu = float(os.environ.get("INCASSO_GIU_USD", "0.4"))
                        _p10_inc = self._shadow.get("pnl_10s")
                        if (_p10_inc is not None and _p10_inc >= _i10_min and
                            current_pnl < (_p10_inc - _i10_giu) and
                            not self._shadow.get("_i10_gia_tagliato")):
                            self._shadow["_i10_gia_tagliato"] = True
                            self._log_m2("💰",
                                f"INCASSO 10s: pnl_10s era +{_p10_inc:.2f}$ ora "
                                f"{current_pnl:+.2f}$ (ceduto {_p10_inc - current_pnl:.2f}$) "
                                f"— il momento giusto, chiudo")
                            # Tabella tracciamento (creata al primo taglio)
                            _i10c = None
                            try:
                                import sqlite3 as _sqi10
                                _i10c = _sqi10.connect(self.db_path, timeout=15)
                                _i10c.execute("PRAGMA busy_timeout=15000;")
                                _i10c.execute("""CREATE TABLE IF NOT EXISTS incasso_10s_tagli (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                    eta_s REAL, pnl_10s REAL, pnl_taglio REAL,
                                    ceduto_usd REAL, prezzo REAL)""")
                                _i10c.execute("""INSERT INTO incasso_10s_tagli
                                    (eta_s, pnl_10s, pnl_taglio, ceduto_usd, prezzo)
                                    VALUES (?, ?, ?, ?, ?)""",
                                    (float(_eta), float(_p10_inc), float(current_pnl),
                                     float(_p10_inc - current_pnl), float(price)))
                                _i10c.commit()
                            except Exception:
                                pass
                            finally:
                                if _i10c is not None:
                                    try:
                                        _i10c.close()
                                    except Exception:
                                        pass
                            return self._close_shadow_trade(price, "INCASSO_10S")
                    if _eta >= 20 and self._shadow.get("pnl_20s") is None:
                        self._shadow["pnl_20s"] = round(current_pnl, 3)
                        # ════════════════════════════════════════════════════════
                        # TRANELLO (6giu) — 2o cancello sul PRIMO RESPIRO.
                        # A 20s confronto col 10s. Regola tarata su 6 trade:
                        #   MASCHIO: cresce 10s->20s ED e' in verde -> LASCIO CORRERE
                        #   TRANS:   evapora (20s<10s) o resta rosso -> CHIUDO SUBITO
                        # Cosi' il trans muore a ~zero invece di -4/-9. I maschi
                        # sani che scendono POCO ma restano forti (>= soglia) li
                        # salvo, per non ammazzare i grassi lenti (es. 06:09).
                        # Interruttore: TRANELLO_OFF=true lo spegne. Reversibile.
                        # ⚠ TARATO SU 6 TRADE. Da validare sui nuovi.
                        # ════════════════════════════════════════════════════════
                        if os.environ.get("TRANELLO_OFF", "false").lower() != "true":
                            _p10 = self._shadow.get("pnl_10s")
                            _p20 = self._shadow.get("pnl_20s")
                            # soglia "ancora forte": se a 20s e' sopra questo, e' un
                            # maschio sano anche se sceso un po' (salva i 06:09).
                            _tr_forte = float(os.environ.get("TRANELLO_FORTE", "2.0"))
                            if _p10 is not None and _p20 is not None:
                                _cresce   = _p20 > _p10
                                _in_verde = _p20 > 0
                                _forte    = _p20 >= _tr_forte
                                # MASCHIO se: cresce ED in verde, OPPURE ancora forte
                                _e_maschio = (_cresce and _in_verde) or _forte
                                if not _e_maschio:
                                    self._log("🎯", f"TRANELLO chiude TRANS: "
                                                    f"10s={_p10:+.2f} 20s={_p20:+.2f} "
                                                    f"(non cresce/non forte) @ ${price:.1f}")
                                    if not hasattr(self, "_tranello_tagli"):
                                        self._tranello_tagli = 0
                                    self._tranello_tagli += 1
                                    _ct = None
                                    try:
                                        import sqlite3 as _sqt
                                        _ct = _sqt.connect(self.db_path, timeout=15)
                                        _ct.execute("PRAGMA busy_timeout=15000;")
                                        _ct.execute("""CREATE TABLE IF NOT EXISTS tranello_tagli (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                                            pnl_10s REAL, pnl_20s REAL, prezzo REAL)""")
                                        _ct.execute("INSERT INTO tranello_tagli (pnl_10s,pnl_20s,prezzo) VALUES (?,?,?)",
                                                    (float(_p10), float(_p20), float(price)))
                                        _ct.commit()
                                    except Exception:
                                        pass
                                    finally:
                                        if _ct is not None:
                                            try:
                                                _ct.close()
                                            except Exception:
                                                pass
                                    return self._close_shadow_trade(price, "TRANELLO_TRANS")
            except Exception:
                pass

            # -- COMPONENTE 1: MOMENTUM (peso 30) ---------------------
            # FORTE=30, MEDIO=20, DEBOLE=5
            # In direzione giusta = punteggio pieno
            if entry_direction == "LONG":
                mom_score = {'FORTE': 30, 'MEDIO': 20, 'DEBOLE': 5}.get(momentum, 15)
            else:
                mom_score = {'DEBOLE': 30, 'MEDIO': 20, 'FORTE': 5}.get(momentum, 15)
            
            # -- COMPONENTE 2: TREND (peso 20) -------------------------
            if entry_direction == "LONG":
                trend_score = {'UP': 20, 'SIDEWAYS': 10, 'DOWN': 0}.get(trend, 10)
            else:
                trend_score = {'DOWN': 20, 'SIDEWAYS': 10, 'UP': 0}.get(trend, 10)
            
            # -- COMPONENTE 3: DECELERAZIONE (peso 25) -----------------
            # Derivata seconda: l'impulso sta frenando?
            decel = self.decelero.analyze()
            decel_score_val = decel.get('decel_score', 0)
            # Bassa decelerazione = alto punteggio (resta)
            decel_comp = int((1.0 - decel_score_val) * 25)
            
            # -- COMPONENTE 4: PROFITTO PROTETTO (peso 25) -------------
            # La tolleranza al retreat non è fissa. Dipende dalla volatilità
            # del fingerprint: pattern volatile → retreat normale è alto.
            # L'Oracolo conosce la volatilità media dei WIN su questo pattern.
            #
            # Se non ci sono dati → usa 50% come neutro (nessun numero fisso).
            if max_profit > 0:
                retreat_pct = retreat / max_profit
                # Tolleranza adattiva: deriva dal PnL medio dei WIN su questo pattern.
                # PnL win alto → il trade ha ampio respiro → tolleranza alta.
                # PnL win basso → trade stretto → tolleranza bassa.
                pnl_win_avg = abs(self.oracolo.get_pnl_avg(
                    self._shadow_entry_momentum or momentum,
                    self._shadow_entry_volatility or volatility,
                    self._shadow_entry_trend or trend,
                    direction=entry_direction
                )) or 5.0
                # Tolleranza: da 40% (trade stretto) a 70% (trade ampio)
                # Calibrata sui dati reali, non su un numero fisso
                tolleranza = min(0.70, max(0.40, 0.40 + (pnl_win_avg / 50.0) * 0.30))
                penalized = max(0.0, (retreat_pct - tolleranza) / (1.0 - tolleranza))
                profit_comp = int((1.0 - min(1.0, penalized)) * 25)
            elif current_pnl < 0:
                profit_comp = 5
            else:
                profit_comp = 15
            
            # -- SCORE TOTALE EXIT -------------------------------------
            exit_energy = mom_score + trend_score + decel_comp + profit_comp
            
            # -- EXIT INTELLIGENTE: la soglia nasce dai dati, non da manopole --
            #
            # Il sistema misura tre cose reali:
            #   1. Quanto durano i WIN su questo pattern (Oracolo duration memory)
            #   2. Quanto spesso esce troppo presto (post-trade tracker)
            #   3. Come si muove il prezzo dopo l'uscita (delta post-trade)
            #
            # Da questi tre dati emerge la soglia giusta — non da un numero fisso.

            fp_entry = self.oracolo._fp(
                self._shadow_entry_momentum or momentum,
                self._shadow_entry_volatility or volatility,
                self._shadow_entry_trend or trend,
                entry_direction
            )

            # MIN_HOLD: 70% della durata media dei WIN su questo fingerprint.
            # Se non ci sono dati sufficienti → usa la durata media del regime corrente
            # dai trade reali in memoria. Zero default fisso.
            MIN_HOLD = self.oracolo.get_dynamic_min_hold(
                self._shadow_entry_momentum or momentum,
                self._shadow_entry_volatility or volatility,
                self._shadow_entry_trend or trend,
                direction=entry_direction,
                regime=self._regime_current
            )

            if duration < MIN_HOLD:
                return  # il tempo minimo non è ancora scaduto

            # Soglia base: deriva dal rapporto tra durata corrente e durata media WIN.
            # Se duriamo già il 120% della durata media WIN → soglia sale (chiudi presto).
            # Se duriamo il 50% → soglia bassa (lascia correre ancora).
            avg_win_dur = self.oracolo.get_avg_duration(
                self._shadow_entry_momentum or momentum,
                self._shadow_entry_volatility or volatility,
                self._shadow_entry_trend or trend,
                direction=entry_direction, is_win=True
            ) or 60.0

            # Quanto siamo nella vita del trade rispetto alla durata media WIN
            time_ratio = duration / avg_win_dur  # 0.5 = a metà vita, 2.0 = doppio del normale

            # PATCH 3 BUG 10 — Time-Ratio Exit Killer
            # Background: exit_soglia cresce linearmente con time_ratio.
            # Combinato con il BUG 8 (WIN classification senza fee), questo crea
            # un feedback loop velenoso: i falsi-WIN entrano in avg_win_dur,
            # abbassano la durata media, fanno salire time_ratio sui trade
            # successivi, alzano exit_soglia, chiudono ancora prima.
            # Osservato live: soglie 45→46→53 in 3 trade consecutivi.
            #
            # Fix: se il trade NON ha ancora pagato la fee (pnl_lordo < fee*1.5),
            # il time_ratio NON deve alzare la soglia oltre il floor di 35.
            # Significato fisico: "non strangolare un trade che non è ancora
            # economicamente maturo". Solo i trade che hanno già attraversato
            # la zona-fee possono essere chiusi per calo energia con soglia alta.
            _fee_rt_soglia = float(getattr(self, "FEE_TRADE", 2.00))
            _trade_maturo = current_pnl >= (_fee_rt_soglia * 1.5)  # +$3 lordo = breakeven netto
            # Soglia che sale con il tempo proporzionalmente alla vita del trade
            # Quando siamo a metà vita (ratio=0.5): soglia 32
            # Quando siamo alla fine normale (ratio=1.0): soglia 45
            # Quando siamo oltre (ratio=2.0): soglia 58
            exit_soglia_base = int(25 + time_ratio * 30)
            if not _trade_maturo:
                # Sotto fee: cap soglia a 35 (no strangolamento prematuro)
                exit_soglia_base = min(35, exit_soglia_base)
            exit_soglia_base = max(28, min(65, exit_soglia_base))

            # EXIT_TOO_EARLY FEEDBACK: il post-trade dice quanto spesso usciamo presto.
            # Rate alto → il sistema abbassa la soglia → più difficile uscire → resta più a lungo.
            # Rate basso → soglia normale → esce quando l'energia cala.
            # Questo è un ciclo chiuso: il sistema si autocorregge sui propri errori.
            too_early_rate = self.oracolo.get_exit_too_early_rate(fp_entry)

            # Correzione proporzionale: da 0 (rate=50%) a -15pt (rate=100%)
            if too_early_rate > 0.5:
                correzione = int((too_early_rate - 0.5) * 30)  # 0→15 punti di abbassamento
                exit_soglia = max(20, exit_soglia_base - correzione)
                if correzione >= 5:
                    self._log_m2("⏳", f"EXIT_FEEDBACK: early={too_early_rate:.0%} "
                                      f"ratio={time_ratio:.1f} soglia={exit_soglia} "
                                      f"(senza feedback sarebbe {exit_soglia_base})")
            else:
                exit_soglia = exit_soglia_base
            
            # -- PROFIT LOCK: mai perdere un profitto acquisito ----------
            # L1.5 FIX: protezione anti-evaporazione su 2 livelli
            # Livello 1 (PROTECT): profit grande raggiunto → retreat 40%
            # Livello 2 (LOCK_LOW): profit medio raggiunto → retreat 25% (più aggressivo)
            # Livello 3 (EVAPORATION): max raggiunto era WIN ma ora torna sotto fee → exit subito
            #
            # Background: il 56% dei loss recenti aveva max_profit > 0 (WIN_+0/+3)
            # ma poi è evaporato. Servono trigger di exit più aggressivi.
            _cm_profit_min = 0.0
            if hasattr(self, 'capsule_manager') and self.capsule_manager:
                _veto_ctx = {
                    'momentum':   getattr(self, '_last_momentum', 'MEDIO'),
                    'volatility': getattr(self, '_last_volatility', 'MEDIA'),
                    'trend':      getattr(self, '_last_trend', 'SIDEWAYS'),
                    'direction':  entry_direction,
                    'regime':     self._regime_current,
                }
                _cm_exit = self.capsule_manager.valuta(_veto_ctx)
                _cm_profit_min = _cm_exit.get('profit_lock_min', 0.0)
                _cm_retreat    = _cm_exit.get('profit_lock_retreat', 0.40)
            else:
                _cm_retreat = 0.40

            # FEE = $2.00 — soglie di profitto in lordo
            FEE = 2.00
            
            # ════════════════════════════════════════════════════════════════
            # CALIBRAZIONE VOLPE — soglie scalate per momentum entry
            # ════════════════════════════════════════════════════════════════
            # Il momentum dell'entry dice quanto può durare il trade.
            # FORTE: lascia correre, può fare $4-8
            # MEDIO: protezione bilanciata
            # DEBOLE: chiudi presto, ogni $0.50 vale oro
            # ════════════════════════════════════════════════════════════════
            _entry_mom = self._shadow_entry_momentum or momentum
            
            # ════════════════════════════════════════════════════════════════
            # PATCH 2 BUG 6 (16mag2026): PROFIT_FLOOR scalato su exposure/prezzo.
            # Prima: 15 costanti in dollari assoluti tarate per BTC ~$50k.
            # Su BTC $78k con fee $2.00 round-trip, il floor $2.10-$2.30
            # corrispondeva a $0.10-$0.30 netto: vittorie sterili.
            # Adesso: floor = fee + margine_pct * exposure.
            #   exposure = $5000 (size base bot)
            #   margine: DEBOLE 0.04% / MEDIO 0.06% / FORTE 0.10% del notional
            #   → floor lordo: ~$4 / ~$5 / ~$7 (netto: ~$2 / ~$3 / ~$5)
            # Tutte le soglie scalano con exposure ed entry price reali.
            # ════════════════════════════════════════════════════════════════
            # FIX 16mag late: usa self.EXPOSURE reale del bot
            # (TRADE_SIZE_USD * LEVERAGE = $5000), non variabile inesistente.
            _exp_usd = float(getattr(self, "EXPOSURE", None) or
                             (getattr(self, "TRADE_SIZE_USD", 1000.0) *
                              getattr(self, "LEVERAGE", 5.0)))
            _fee_rt = float(getattr(self, "FEE_TRADE", 2.00))  # fee round-trip reale
            
            if _entry_mom == "FORTE":
                # FORTE = trade che corre. Soglie alte, retreat permissivo.
                _margine_pct = 0.0010   # 0.10% di $5k = $5 netto target
                PROFIT_FLOOR_LOW     = _fee_rt + (_exp_usd * _margine_pct * 0.5)   # ~$4.50 lordo
                PROFIT_FLOOR_HIGH    = _fee_rt + (_exp_usd * _margine_pct * 1.0)   # ~$7.00 lordo
                PROFIT_BIG_THRESHOLD = _fee_rt + (_exp_usd * _margine_pct * 1.6)   # ~$10.00 lordo
                LOCK_LOW_RETREAT     = 0.35   # 35% retreat (più tollerante)
                BREATH_MEDIA_RANGE   = (PROFIT_FLOOR_LOW * 0.5, PROFIT_FLOOR_HIGH * 0.7)
            elif _entry_mom == "MEDIO":
                # MEDIO = trade ordinario. Soglie standard.
                _margine_pct = 0.0006   # 0.06% di $5k = $3 netto target
                PROFIT_FLOOR_LOW     = _fee_rt + (_exp_usd * _margine_pct * 0.5)   # ~$3.50 lordo
                PROFIT_FLOOR_HIGH    = _fee_rt + (_exp_usd * _margine_pct * 1.0)   # ~$5.00 lordo
                PROFIT_BIG_THRESHOLD = _fee_rt + (_exp_usd * _margine_pct * 1.6)   # ~$6.80 lordo
                LOCK_LOW_RETREAT     = 0.25
                BREATH_MEDIA_RANGE   = (PROFIT_FLOOR_LOW * 0.5, PROFIT_FLOOR_HIGH * 0.7)
            else:  # DEBOLE
                # DEBOLE = trade fragile. Soglie basse, prendi quello che dà.
                # ════════════════════════════════════════════════════════════
                # FRONTIERA MASCHI DEBOLE (1giu, Roberto — dai dati reali):
                # phantom_forensic, is_win=1, DEBOLE|BASSA: mfe_min=2.01.
                # NESSUN maschio DEBOLE fa picco sotto 2$ → sotto 2 = femmina.
                # Fasce: 2-3$ (344), 3-4$ (176), 4-6$ (219), >6$ (482, media 10.36).
                # Il floor LOW era 3.00 → tagliava fuori i 344 maschi della
                # fascia 2-3: SMORZ_TAKE non si armava mai su di loro e il grasso
                # evaporava. Portato a 2.20 (appena sopra la frontiera 2.01) così
                # la presa sul picco si arma appena il maschio si manifesta.
                # SMORZ_TAKE scatta solo CON decelerometro → non taglia il maschio
                # che ancora corre, solo quello che sta gia' rallentando.
                # Regolabile da env senza redeploy.
                # ════════════════════════════════════════════════════════════
                _margine_pct = 0.0004   # 0.04% di $5k = $2 netto target
                _floor_low_deb = float(os.environ.get('FLOOR_LOW_DEBOLE', '2.20'))
                PROFIT_FLOOR_LOW     = _floor_low_deb                              # ~$2.20 lordo (era 3.00)
                PROFIT_FLOOR_HIGH    = _fee_rt + (_exp_usd * _margine_pct * 1.0)   # ~$4.00 lordo
                PROFIT_BIG_THRESHOLD = _fee_rt + (_exp_usd * _margine_pct * 1.6)   # ~$5.20 lordo
                LOCK_LOW_RETREAT     = 0.18   # retreat aggressivo (chiudi presto)
                BREATH_MEDIA_RANGE   = (PROFIT_FLOOR_LOW * 0.5, PROFIT_FLOOR_HIGH * 0.7)
            
            # Override da SuperCapsule Oracle se presente
            PROFIT_FLOOR_HIGH = max(_cm_profit_min, PROFIT_FLOOR_HIGH)

            # ════════════════════════════════════════════════════════════════
            # PRENDI ALLO SMORZAMENTO (29mag, Roberto):
            # "Appena sente lo smorzamento deve prendere — non attendere
            #  l'inversione." Quando il profitto è GIÀ BUONO e il decelerometro
            #  sente che l'impulso rallenta FORTE, prende subito il massimo,
            #  SENZA aspettare il ritracciamento (retreat) come fa la VOLPE.
            # ARMATA SOLO sopra PROFIT_FLOOR_LOW: su trade piccoli o in perdita
            #  NON scatta → non ricrea il problema dell'uscita precoce sui loss.
            # Il decelerometro misura il RALLENTAMENTO (mom_fast < mom_slow),
            #  non il momentum morto: prende mentre il prezzo è ancora alto.
            # ════════════════════════════════════════════════════════════════
            DECEL_TAKE_PROFIT = 0.65  # rallentamento forte (= soglia decelerometro)
            if (current_pnl >= PROFIT_FLOOR_LOW
                    and decel_score_val >= DECEL_TAKE_PROFIT):
                self._close_shadow_trade(price,
                    f"SMORZ_TAKE_E{exit_energy}_{_entry_mom}_decel{decel_score_val:.2f}_WIN_{current_pnl:+.1f}")
                return

            if current_pnl > 0 and max_profit > 0:
                # ═══════════════════════════════════════════════════════════
                # TRAILING SUL MASSIMO (10giu, Roberto) — la sintesi:
                # "sono tutti ibridi all'ingresso. Se hanno grasso e lo tengono
                #  (corrono) li lasciamo correre. Se si sgonfiano, strappiamo
                #  il grasso che c'è prima che evapori."
                # NON serve sapere se è maschio o femmina: seguo il MASSIMO.
                #  - finché fa nuovi massimi (sale) -> TENGO, corre
                #  - appena ripiega di TRAIL_GIU$ dal massimo -> STRAPPO e incasso
                # Maschio +11 che torna +10 -> strappo +10 (jackpot preso).
                # Dopata peak +1.71 che si gira -> strappo +0.9 (grasso preso,
                #  invece di -0.62 evaporato).
                # Attivo solo sopra TRAIL_MIN$ di profitto (lascia formare il
                # grasso). Spegnibile con TRAIL_OFF=true. Reversibile.
                # ═══════════════════════════════════════════════════════════
                if os.environ.get("TRAIL_OFF", "false").lower() != "true":
                    # ═══════════════════════════════════════════════════════
                    # SALVA IL VERDE (11giu, Roberto) — niente soglie di profitto!
                    # Errore precedente: PRENDI_SUBITO a +2.5 e TRAIL_MIN a 1.5
                    # NON prendevano mai i TRANS-obiettivo, che fanno grasso a
                    # +1.3/+2.3 e poi si svuotano (16:49 +2.29->-0.62, 03:56
                    # +1.39->-5.06). Quel grasso, con le FEE GIA' PAGATE, è
                    # denaro puro che buttavamo via.
                    # Logica giusta: appena il trade è in VERDE (qualunque,
                    # sopra il costo già speso) traccio il picco. Appena CEDE
                    # dal picco di TRAIL_GIU$, STRAPPO il verde che c'è — non
                    # aspetto nessuna soglia. Il maschio che sale e NON cede
                    # resta dentro (corre). Il TRANS/dopata che si gira lo
                    # incasso mentre è ancora verde.
                    # ENV: TRAIL_GIU_USD (default 0.6 = cede 0.6$ dal picco).
                    #      VERDE_MIN_USD = profitto LORDO minimo in mano per
                    #      strappare. ATTENZIONE: current_pnl qui è LORDO (il
                    #      trade respira). Fee = 0.75/lato = 1.50 a giro (Roberto).
                    #      Quindi LORDO 2.0 = NETTO +0.50 dopo fee. Sotto 2.0
                    #      lordo si chiude in PERDITA netta. Default prudente 2.0.
                    # FIX (Roberto): NON strappo se il lordo in mano non copre le
                    # fee + margine. Strappo solo se current_pnl (lordo) >=
                    # VERDE_MIN_USD, cosi' il NETTO resta positivo. Mai fottuti.
                    # 0 = spento.
                    # ═══════════════════════════════════════════════════════
                    _trail_giu  = float(os.environ.get("TRAIL_GIU_USD", "0.5"))
                    _verde_min  = float(os.environ.get("VERDE_MIN_USD", "2.5"))
                    if _trail_giu > 0 and current_pnl >= _verde_min:
                        # lordo in mano copre le fee + margine E ho ceduto dal
                        # picco -> salvo il verde NETTO che ho, vicino al picco
                        if (max_profit - current_pnl) >= _trail_giu:
                            _scarto = max_profit - current_pnl
                            _stato = "CRESC" if _scarto < 0.4 else "CEDE"
                            self._close_shadow_trade(price,
                                f"SALVA_VERDE*{current_pnl:+.1f}_dapicco{max_profit:+.1f}_{_stato}")
                            return

                # ═══════════════════════════════════════════════════════════
                # VOLPE — PROFIT_LOCK A 4 LIVELLI calibrati per momentum
                # ───────────────────────────────────────────────────────────
                # DISATTIVATO DI DEFAULT (11giu, Roberto). Questi 4 livelli
                # lasciavano ritirare il 40-55% dal picco prima di agire. Prova
                # sui dati: 16:07 picco +7.2 → SALVAGENTE a pct0.55 → chiuso
                # -0.77. Il grasso evaporava. Ora l'uscita in profitto è UNA
                # sola: il SALVA_VERDE sopra (strappa appena cedi TRAIL_GIU dal
                # picco, vicino al massimo). Niente più ritirate percentuali.
                # Riattivabile: env PROFIT_LOCK_VECCHIO_ON=true
                # ═══════════════════════════════════════════════════════════
                if os.environ.get("PROFIT_LOCK_VECCHIO_ON", "false").lower() == "true":
                    # ─ Livello 4 PROTECT_HI: WIN grosso, lascia correre ─
                    if max_profit >= PROFIT_BIG_THRESHOLD:
                        retreat_pct_now = retreat / max_profit
                        _big_retreat = 0.40 if _entry_mom != "DEBOLE" else 0.35
                        if retreat_pct_now > _big_retreat:
                            self._close_shadow_trade(price,
                                f"PROTECT_HI_E{exit_energy}_{_entry_mom}_max{max_profit:+.1f}_keep{current_pnl:+.1f}")
                            return

                    # ─ Livello 3 EVAPORATION: max stava sopra, ora torna sotto floor low ─
                    elif max_profit >= (PROFIT_FLOOR_LOW + 0.10) and current_pnl < PROFIT_FLOOR_LOW:
                        self._close_shadow_trade(price,
                            f"LOCK_EVAP_E{exit_energy}_{_entry_mom}_max{max_profit:+.1f}_now{current_pnl:+.1f}")
                        return

                    # ─ Livello 2 LOCK_LOW: profit medio, retreat calibrato ─
                    elif current_pnl >= PROFIT_FLOOR_LOW and current_pnl < PROFIT_FLOOR_HIGH:
                        retreat_pct_now = retreat / max_profit
                        if retreat_pct_now > LOCK_LOW_RETREAT:
                            self._close_shadow_trade(price,
                                f"LOCK_LOW_E{exit_energy}_{_entry_mom}_WIN_{current_pnl:+.1f}")
                            return

                    # ─ Livello 1 PROTECT: profit alto, retreat normale ─
                    elif current_pnl >= PROFIT_FLOOR_HIGH:
                        retreat_pct_now = retreat / max_profit
                        if retreat_pct_now > _cm_retreat:
                            self._close_shadow_trade(price, 
                                f"PROFIT_LOCK_E{exit_energy}_{_entry_mom}_WIN_{max_profit:+.0f}")
                            return


            # -- DECISIONE ---------------------------------------------
            # PATCH 3 BUG 8 — Honest WIN Classification
            # Background: current_pnl è LORDO (delta_price * btc_qty).
            # La fee round-trip ($2.00) viene applicata solo in _close_shadow_trade.
            # Senza correzione, il bot etichettava come "WIN_+1" trade economicamente
            # in perdita ($1 lordo → -$1 netto). Veleno per la memoria e per l'occhio
            # umano che legge i log.
            #
            # Regola nuova:
            #  - WIN vero solo se pnl_netto_stimato > 0 (lordo > fee)
            #  - Sotto fee con energy < soglia: NON CHIUDERE (lascia respirare)
            #    perché chiudere significherebbe registrare un loss travestito.
            #    Aspettare che il movimento maturi o muoia decisamente.
            #  - Loss vera (pnl <= 0): chiude come prima ma con label LOSS
            if exit_energy < exit_soglia:
                _fee_rt = float(getattr(self, "FEE_TRADE", 2.00))
                pnl_netto_stimato = current_pnl - _fee_rt

                # ════════════════════════════════════════════════════════
                # KILLER E40 (31mag, Roberto) — "tutti nascono femmina, il
                # maschio si manifesta entro un tempo preciso."
                # ════════════════════════════════════════════════════════
                # PROVA SUI DATI (204 trade reali):
                #   energia >= 40  → 88% WIN (71 win / 10 loss) = il MASCHIO
                #   energia <  40  → 28% WIN (34 win / 89 loss)  = la FEMMINA
                # Frontiera NETTA a 40 (non graduale: 30-40 e <30 hanno stesso
                # 28% — o sei sopra 40 o sei femmina).
                #
                # Regola: do al trade un TEMPO per manifestarsi maschio
                # (raggiungere energia 40). Se passato quel tempo è ancora
                # sotto 40 ED è in perdita → è femmina che non si manifesta,
                # la MOLLO subito invece di tenerla aperta a bruciare la fee.
                # Questo CORREGGE il "timeframe lungo" che teneva aperte le
                # femmine sperando respirassero (dati: peggiorava i loss).
                #
                # KILLER_E_SOGLIA=40 (la frontiera dei dati).
                # KILLER_TEMPO=35s  (i win medi vivono ~100s, i loss ~35s:
                #   dopo 35s un maschio vero ha già preso energia).
                # Disarmabile: env KILLER_E40_OFF=true
                _killer_off = os.environ.get("KILLER_E40_OFF", "false").lower() == "true"
                _killer_e_soglia = float(os.environ.get("KILLER_E_SOGLIA", "40"))
                _killer_tempo    = float(os.environ.get("KILLER_TEMPO", "35"))
                if (not _killer_off
                        and exit_energy < _killer_e_soglia
                        and duration >= _killer_tempo
                        and current_pnl < 0):
                    self._close_shadow_trade(price,
                        f"KILLER_E40_FEMMINA_E{exit_energy}_dur{duration:.0f}s_{current_pnl:+.1f}")
                    self._log_m2("⚰️",
                        f"KILLER_E40: femmina non manifestata "
                        f"(E{exit_energy}<{_killer_e_soglia:.0f} dur{duration:.0f}s) → mollata")
                    return

                if pnl_netto_stimato > 0:
                    # WIN vero: lordo > fee
                    self._close_shadow_trade(price,
                        f"EXIT_E{exit_energy}_S{exit_soglia}_WIN_NET_{pnl_netto_stimato:+.1f}")
                    return
                elif current_pnl > 0:
                    # SUBFEE: lordo positivo ma netto negativo.
                    # NON chiudere per calo energia se sotto fee.
                    # Loggiamo per visibilità ma lasciamo che il trade prosegua.
                    # Si chiuderà naturalmente con altri trigger (HARD_STOP,
                    # divorzio, TIMEOUT_DD, PROTECT_HI se torna sopra) o quando
                    # current_pnl diventa decisamente negativo.
                    self._log_m2("🟡",
                        f"SUBFEE_HOLD E{exit_energy} S{exit_soglia} "
                        f"lordo=${current_pnl:+.2f} netto=${pnl_netto_stimato:+.2f}")
                    # NON return: continua agli altri controlli (timeout, drawdown)
                else:
                    # ════════════════════════════════════════════════════════
                    # TIMEFRAME LUNGO (30mag, Roberto) — "se non ha guadagnato,
                    # vai avanti; se ha guadagnato, chiudi."
                    # ════════════════════════════════════════════════════════
                    # PROVA SUI DATI (403 trade): in RANGING LONG il lordo medio
                    # è +0.47 (la DIREZIONE è giusta), ma -587$ netti perché il
                    # bot chiudeva i trade a calo-energia (S35) appena andavano
                    # un soffio sotto zero (-0.04, -0.19, -0.10...) senza dare
                    # tempo di risalire e pagare la fee.
                    # ORA: una perdita LIEVE (entro -fee) NON chiude per energia.
                    # Il trade respira come il SUBFEE qui sopra. Lo chiudono solo
                    # i freni VERI già attivi più sotto:
                    #   - HARD_STOP 2% (frana)
                    #   - TIMEOUT_DD (drawdown >1% oltre durata media)
                    #   - TIMEOUT_MAX_30M (assoluto)
                    #   - o torna in profitto e incassa
                    # PALETTO SICUREZZA: se la perdita è già SERIA (oltre -fee,
                    # cioè il trade sta franando davvero) → chiude subito, non si
                    # aspetta. Così non trasformiamo tanti -0.20 in pochi -10.
                    _fee_loss_floor = float(getattr(self, "FEE_TRADE", 2.00))
                    if current_pnl <= -_fee_loss_floor:
                        # Perdita seria: chiudi (freno energia legittimo)
                        self._close_shadow_trade(price,
                            f"EXIT_E{exit_energy}_S{exit_soglia}_LOSS_{current_pnl:+.1f}")
                        return
                    else:
                        # Perdita lieve: NON chiudere per energia. Lascia maturare.
                        self._log_m2("🟢",
                            f"HOLD_IMMATURO E{exit_energy} S{exit_soglia} "
                            f"lordo=${current_pnl:+.2f} (sotto fee, lascio respirare)")
                        # NON return: prosegue ai freni veri (HARD_STOP, TIMEOUT_DD)

            # -- TIMEOUT SAFETY - solo se l'exit intelligente non chiude -----
            # Niente TIMEOUT_3X - l'exit intelligente decide.
            # Solo TIMEOUT_DD: se in drawdown > 1% dopo duration_avg → esci
            if duration > duration_avg * 5 and drawdown_pct > 1.0:
                self._close_shadow_trade(price, "TIMEOUT_DD")
                return
            # ════════════════════════════════════════════════════════════════
            # VINCOLO CASSA — MAX 8 MINUTI (30mag, Roberto)
            # ════════════════════════════════════════════════════════════════
            # Capitale 10k$, esposizione 5k$/trade. Se i trade restano aperti
            # 30 min, la coda accumula esposizione (10 trade × 5k = 50k su 10k
            # di cassa) → la cassa si satura e blocca le opportunità nuove.
            # Roberto: "massimo tempo 8 minuti". 8 min = 480s.
            # Il fix timeframe-lungo (sopra) lascia respirare i trade immaturi,
            # ma il tetto cassa resta INVALICABILE: oltre 480s si chiude e basta.
            TIMEOUT_MAX_CASSA = 480   # 8 minuti — vincolo cassa, non negoziabile
            if duration > TIMEOUT_MAX_CASSA:
                self._close_shadow_trade(price, f"TIMEOUT_MAX_8M_dur{int(duration)}s")
                return

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE shadow_exit: {e}")
            log.error(f"[M2_EXIT_ERROR] {e}\n{traceback.format_exc()}")

    def _close_shadow_trade(self, price, reason):
        """Chiude il shadow trade e registra stats M2.
        CRITICO: insegna all'Oracolo e persiste su DB - altrimenti il sistema non impara MAI.
        
        NOTA FEE: Il PnL paper NON include fee Binance.
        In live con BNB: 0.075% per lato + ~0.01% slippage = 0.17% round trip.
        Su BTC a $70k = ~$119 per trade su 1 BTC.
        Lo scalping a 10-15s con PnL $5-17 NON è profittevole in spot.
        Serve: futures (fee 0.07% RT) con leva, oppure trade più lunghi con PnL > $150.
        """
        if getattr(self, "_shadow_closing", False):
            return
        self._shadow_closing = True
        try:
            if not self._shadow:
                self._shadow_closing = False
                return

            # ── MICROSCOPIO: FLUSH CURVA ALLA CHIUSURA (1giu, opzione B) ──────
            # La curva (nascita densa + evoluzione ogni 3s) si salva QUI, alla
            # chiusura reale, NON piu' a 10.5s. Una scrittura per trade.
            # Isolato: se fallisce non deve mai impedire la chiusura del trade.
            try:
                if not self._shadow.get('_micro_flushed'):
                    self._shadow['_micro_flushed'] = True
                    _firma_n = (f"{self._shadow.get('momentum_entry','?')}|"
                                f"{self._shadow.get('volatility_entry','?')}|"
                                f"{self._shadow.get('trend_entry','?')}|"
                                f"{self._shadow.get('regime_entry', self._regime_current)}|"
                                f"{self._shadow.get('direction','LONG')}")
                    self._save_curva_nascita(
                        trade_ts=self._shadow_entry_time,
                        firma=_firma_n,
                        curva=self._shadow.get('_micro_nascita', []),
                    )
            except Exception as _mfe:
                log.debug(f"[MICRO_FLUSH_CLOSE_ERR] {_mfe}")

            # PnL REALE FUTURES = delta_prezzo × quantita BTC nella posizione
            # CRITICO: usa la direzione al momento dell'ENTRY, non quella attuale
            # Se il campo ha flippato durante il trade, la direzione attuale è sbagliata
            entry_direction = self._shadow.get("direction", "LONG")
            delta_price = (price - self._shadow["price_entry"]) if entry_direction == "LONG" \
                  else (self._shadow["price_entry"] - price)
            exposure_usd = self.TRADE_SIZE_USD * self.LEVERAGE
            btc_qty = exposure_usd / self._shadow["price_entry"]
            pnl_gross = delta_price * btc_qty
            
            # FEE FUTURES: 0.02% maker × 2 (andata + ritorno) sulla esposizione
            # $5000 × 0.02% × 2 = $2.00 per trade
            total_fees = exposure_usd * self.FEE_PCT * 2
            
            pnl = pnl_gross - total_fees
            is_win = pnl > 0

            # ════════════════════════════════════════════════════════════════
            # PATCH 6 BUG 13 — Salva firma con classificazione outcome
            # ════════════════════════════════════════════════════════════════
            # Classifica il trade in WIN_NET / LOSS_FEE / LOSS_REAL e salva
            # la firma catturata all'entry, con il match calcolato all'entry.
            # Nessuna decisione presa qui. Solo log per il report diagnostico.
            try:
                if hasattr(self, '_winsig') and self._winsig is not None \
                   and self._shadow and '_winsig_entry' in self._shadow:
                    _sig = self._shadow.get('_winsig_entry')
                    _match = self._shadow.get('_winsig_match_at_entry')
                    # Classificazione coerente con post_patch5_report.py
                    if is_win:
                        _outcome = 'WIN_NET'
                    elif pnl_gross >= -0.50:
                        # lordo vicino a zero = entry in punto morto
                        _outcome = 'LOSS_FEE'
                    else:
                        _outcome = 'LOSS_REAL'
                    self._winsig.save_signature(
                        trade_outcome=_outcome,
                        signature=_sig,
                        pnl_netto=pnl,
                        pnl_lordo=pnl_gross,
                        match_at_entry=_match,
                    )
                    if _match is not None:
                        self._log_m2("🔬",
                            f"WINSIG close outcome={_outcome} "
                            f"match_at_entry={_match:.2f} pnl_n=${pnl:+.2f}")
            except Exception:
                pass
            # ════════════════════════════════════════════════════════════════

            # ════════════════════════════════════════════════════════════════
            # PATCH 11 BUG 18b — Salva memoria WIN_NET per gate post-win
            # ════════════════════════════════════════════════════════════════
            # Memorizza score/soglia/fingerprint/ts del WIN_NET appena chiuso,
            # solo se WIN significativo (pnl_netto >= +$1.00).
            # Verrà letto dal gate POST_WIN_REBALANCE alla prossima entry.
            # ════════════════════════════════════════════════════════════════
            POST_WIN_MIN_NETTO = 1.00   # PATCH 11: soglia "WIN significativo"
            try:
                if is_win and pnl >= POST_WIN_MIN_NETTO and self._shadow:
                    _wsc = self._shadow.get('score')
                    _wso = self._shadow.get('soglia')
                    _wmo = (getattr(self, '_shadow_entry_momentum', '') or '')
                    _wvo = (self._shadow_entry_volatility or '')
                    _wtr = (self._shadow_entry_trend or '')
                    _wdi = entry_direction or 'LONG'
                    _wfp = f"{_wmo}|{_wvo}|{_wtr}|{_wdi}" if (_wmo and _wvo and _wtr) else None
                    if _wsc is not None and _wso is not None and _wfp:
                        self._last_win_score       = float(_wsc)
                        self._last_win_soglia      = float(_wso)
                        self._last_win_fingerprint = _wfp
                        self._last_win_ts          = time.time()
                        self._last_win_pnl         = float(pnl)
                        self._last_win_reason      = reason
                        self._log_m2("💎",
                            f"POST_WIN_MEMO_SAVED score={self._last_win_score:.1f} "
                            f"soglia={self._last_win_soglia:.1f} fp={_wfp} "
                            f"pnl=${pnl:+.2f} window=300s")
            except Exception as _e_pwm:
                pass
            # ════════════════════════════════════════════════════════════════

            # -- TELEMETRY: registra trade ------------------------------------
            trade_duration = time.time() - self._shadow_entry_time if self._shadow_entry_time else 0
            ctx = self._tele_ctx()
            self.telemetry.log_trade_close(
                trade_direction=self._shadow.get("direction", "LONG"),
                pnl=pnl, is_win=is_win, exit_reason=reason, duration=trade_duration,
                **{k: ctx[k] for k in ('regime','direction','open_position',
                   'active_threshold','drift','macd','trend','volatility')}
            )

            # -- STATE ENGINE: aggiorna stato dopo ogni trade -------------
            self._state_engine_update(pnl, is_win, trade_duration)

            self.campo.record_result(is_win, exit_reason=reason, 
                                     pb_signals=self._shadow.get("pb_signals", 0),
                                     pnl=pnl)

            # -- INSEGNA ALL'ORACOLO 2.0 - il cervello impara TUTTO ---------
            if self._shadow_entry_momentum and self._shadow_entry_volatility and self._shadow_entry_trend:
                # Calcola range_position e drift per contesto
                _rp = 0.5
                _dr = 0.0
                if len(self.campo._prices_long) >= 200:
                    _recent = list(self.campo._prices_long)[-200:]
                    _rh, _rl = max(_recent), min(_recent)
                    if _rh > _rl:
                        _rp = (price - _rl) / (_rh - _rl)
                if len(self.campo._prices_long) >= 100:
                    _p = list(self.campo._prices_long)
                    _dr = (sum(_p[-50:])/50 - sum(_p[:50])/50) / (sum(_p[:50])/50) * 100

                self.oracolo.record(
                    self._shadow_entry_momentum,
                    self._shadow_entry_volatility,
                    self._shadow_entry_trend,
                    is_win,
                    direction=entry_direction,
                    pnl=pnl,
                    duration=trade_duration,
                    rsi=getattr(self.campo, '_last_rsi', 50),
                    drift=_dr,
                    range_position=_rp,
                    regime=self._regime_current,
                    hour=datetime.utcnow().hour,
                )
                
                # Avvia post-trade tracker
                fp = self.oracolo._fp(self._shadow_entry_momentum,
                                       self._shadow_entry_volatility,
                                       self._shadow_entry_trend,
                                       entry_direction)
                self.oracolo.start_post_trade(fp, price, entry_direction)

            # -- AGGIORNA MEMORIA MATRIMONI - anche M2 conta ------------------
            if self._shadow_matrimonio:
                matrimonio = MatrimonioIntelligente.get_by_name(self._shadow_matrimonio)
                wr_expected = matrimonio.get("wr", 0.50)
                self.memoria.record_trade(self._shadow_matrimonio, is_win, wr_expected)

            # -- CALIBRATORE - M2 insegna anche a lui -------------------------
            self.calibratore.registra_osservazione(
                seed_score=self._shadow.get("score", 0) / 100.0,
                fingerprint_wr=self._shadow_entry_fingerprint or 0.72,
                is_win=is_win,
                divorce_drawdown_usato=((self._shadow_max_price - price) / self._shadow["price_entry"] * 100)
                                       if self._shadow_max_price and self._shadow.get("price_entry") else 0.0
            )

            # -- INTELLIGENZA AUTONOMA - M2 registra con contesto completo ----
            # Calcola drift corrente per le capsule L2_DRIFT
            _ia_drift = 0.0
            if len(self.campo._prices_long) >= 100:
                _p = list(self.campo._prices_long)
                _ia_drift = (sum(_p[-50:])/50 - sum(_p[:50])/50) / (sum(_p[:50])/50) * 100

            # FIX 24giu (Roberto: "un maschio vero che ha perso NON va etichettato
            # come femmina"). Salvo il picco PROPRIO raggiunto in osservazione + il
            # sesso, cosi la lista distingue:
            #   MASCHIO-LOSS = ha fatto picco da maschio (>=3) ma ha perso (sfortuna)
            #   FEMMINA      = non e' mai salita (picco <3)
            # Sono morti DIVERSI: confonderli rende la lista cieca.
            # FIX: leggo il picco VERO catturato all'ingresso (_trade_picco_ingresso),
            # NON _canc_picco_proprio che e' azzerato dalla nuova osservazione.
            _picco_oss = round(float((self._shadow.get("picco_ingresso_reale", 0.0) if isinstance(getattr(self, "_shadow", None), dict) else 0.0) or 0.0), 2)
            # soglia sesso = soglia d'ingresso (chi e' ENTRATO ha picco>=soglia, NON e' femmina)
            _md_picco_min_cls = float(os.environ.get("MD_MFE_MIN", "1.0"))
            if _picco_oss >= _md_picco_min_cls:
                _sesso = "MASCHIO" if is_win else "TRANS"
            else:
                _sesso = "FEMMINA"
            self.realtime_engine.registra_trade({
                'matrimonio': self._shadow_matrimonio,
                'pnl':        pnl,
                'is_win':     is_win,
                'regime':     self._regime_current,
                'volatility': self._shadow_entry_volatility or 'MEDIA',
                'trend':      self._shadow_entry_trend or 'SIDEWAYS',
                'direction':  entry_direction,
                'drift':      round(_ia_drift, 4),
                'score':      self._shadow.get('score', 0) if self._shadow else 0,
                'exit_reason': reason,
                'picco_oss':  _picco_oss,   # picco proprio raggiunto in osservazione
                'sesso':      _sesso,        # MASCHIO(vince) / TRANS(entra ma perde) / FEMMINA(non sale)
            })

            # -- LOG ANALYZER - stats per matrimonio includono M2 -------------
            self.log_analyzer.registra({
                'matrimonio': self._shadow_matrimonio, 'pnl': pnl, 'is_win': is_win
            })

            if is_win:
                self._m2_wins  += 1
            else:
                self._m2_losses += 1
                # ════════════════════════════════════════════════════════════════
                # PATCH 4 BUG 11 — Pattern Suspension Cascade
                # ════════════════════════════════════════════════════════════════
                # Background: prima di PATCH 4, OGNI perdita (anche -$2 di sola fee)
                # chiamava capsule_manager.sospendi_pattern() sul pattern appena
                # usato. Cascata osservata:
                #   1. Trade #1 entra su pattern fresco → WIN
                #   2. Trade #1 chiude → pattern X1 sospeso
                #   3. Trade #2 forzato su pattern X2 (marginale) → LOSS da fee
                #   4. Trade #2 chiude → pattern X2 sospeso
                #   5. ... cascata fino a esaurimento pattern utili
                #
                # Conseguenza: il bot punisce un pattern operativo per UNA SOLA
                # perdita, anche se quella perdita è solo fee. Pattern strangolati
                # a catena. Il "primo trade vince, gli altri muoiono" osservato
                # da Roberto è esattamente questo meccanismo.
                #
                # Fix PATCH 4 (Suspension Sanity):
                #  - AUTO_SUSPEND_PATTERN_ON_LOSS = False (flag, default OFF)
                #  - Nessuna sospensione automatica dopo singola loss
                #  - Log osservativo SUSPEND_SKIP_SINGLE_LOSS al posto della
                #    sospensione, per visibilità senza azione
                #  - In futuro PATCH separata: riattivare sospensione SOLO se
                #    stesso pattern perde 3 volte su 5, non per singola loss
                #    e mai per fee-only loss
                # ════════════════════════════════════════════════════════════════
                AUTO_SUSPEND_PATTERN_ON_LOSS = False  # PATCH 4: flag OFF di default
                try:
                    _mom = getattr(self, '_shadow_entry_momentum', '') or ''
                    _vol = self._shadow_entry_volatility or ''
                    _trd = self._shadow_entry_trend or ''
                    _dir = entry_direction or 'LONG'
                    _pattern = f"{_mom}|{_vol}|{_trd}|{_dir}" if (_mom and _vol and _trd) else "?|?|?|?"

                    if AUTO_SUSPEND_PATTERN_ON_LOSS:
                        # Comportamento legacy — disattivato per default in PATCH 4.
                        # Lasciato qui per attivazione futura con condizioni più strette.
                        if hasattr(self, 'capsule_manager') and self.capsule_manager:
                            if _mom and _vol and _trd:
                                self.capsule_manager.sospendi_pattern(
                                    pattern   = _pattern,
                                    oi_carica = self._oi_carica,
                                    regime    = self._regime_current,
                                    motivo    = f"Perdita netta ${pnl:.2f} — in osservazione shadow"
                                )
                    else:
                        # PATCH 4: solo log, nessuna sospensione automatica.
                        # Distinguiamo fee-only loss da loss strutturale per visibilità.
                        _fee_rt_p4 = float(getattr(self, "FEE_TRADE", 2.00))
                        _is_fee_loss = abs(pnl) <= (_fee_rt_p4 * 1.5)  # entro $3 netto
                        _loss_kind = "FEE_LOSS" if _is_fee_loss else "REAL_LOSS"
                        self._log_m2("🟦",
                            f"SUSPEND_SKIP_SINGLE_LOSS pattern={_pattern} "
                            f"pnl=${pnl:+.2f} kind={_loss_kind} "
                            f"reason=AUTO_SUSPEND_OFF_PATCH4")
                except Exception as _sp_e:
                    pass
            self._m2_pnl += pnl

            # ════════════════════════════════════════════════════════════════
            # PATCH 5 BUG 12 — Single Recent Trade Append
            # ════════════════════════════════════════════════════════════════
            # Background: prima di PATCH 5, lo stesso trade chiuso entrava DUE
            # VOLTE consecutive in self._m2_recent_trades:
            #   1. dentro _state_engine_update (riga ~8136), chiamato sopra
            #   2. di nuovo qui sotto (append orfano)
            #
            # Questo falsava:
            #   - recent[-5] usato da State Engine (AGGRESSIVO/NEUTRO/DIFENSIVO)
            #   - recent_wr, recent_pnl, recent_losses, _m2_loss_streak percepito
            #   - auto_tune_soglia che guarda recent[-5]
            #   - tutti i gate di entrata basati sulla memoria breve
            #
            # Il commento del vecchio append diceva:
            #   "FIX: aggiorna _m2_recent_trades — usato dal gate [GATE_OBSOLETO]"
            # Ma il gate citato non esiste più nel codice (grep conferma 0
            # occorrenze del nome come simbolo operativo). Era codice morto
            # orfano da una versione precedente.
            #
            # Fix PATCH 5: rimosso il secondo append. L'append in
            # _state_engine_update resta unico e corretto, ed è quello che
            # State Engine effettivamente legge.
            # ════════════════════════════════════════════════════════════════
            # (PATCH 5: secondo append rimosso. State Engine usa l'unico
            #  append eseguito poco sopra dentro _state_engine_update.)

            # ════════════════════════════════════════════════════════════════
            # FIX 2026-05-09 — Aggancio CompartoEngine + Engine V16 al close
            # ════════════════════════════════════════════════════════════════
            # I metodi on_trade_closed di Comparto/Nervosismo/Breath erano orfani:
            # esistono ma non venivano mai chiamati. Li aggancio qui.
            # Comparto riceve anche pred_score e veritas_pnl_avg per skip
            # irrigidimento se la predizione è viva.
            # ════════════════════════════════════════════════════════════════
            try:
                # PATCH 0 VINCOLO 2: Comparto riceve pred_score SOLO se qualified
                if hasattr(self, 'supercervello') and self.supercervello is not None:
                    _pred_st_close = self.supercervello._get_pred_state()
                    _ps_close = _pred_st_close["score"] if _pred_st_close["qualified"] else 0.0
                else:
                    _ps_close = 0.0
                _vt_pnl_close = 0.0
                if hasattr(self, 'veritas') and self.veritas._stats:
                    _fpe = self.veritas._stats.get('FUOCO|PREVISTO_ENTRA', {})
                    _vt_pnl_close = _fpe.get('pnl_avg', 0.0)

                if hasattr(self, '_comparto') and self._comparto:
                    self._comparto.on_trade_closed(pnl,
                                                    pred_score=_ps_close,
                                                    veritas_pnl_avg=_vt_pnl_close)
                if hasattr(self, '_nerv') and self._nerv:
                    self._nerv.on_trade_closed(pnl)
                if hasattr(self, '_breath') and self._breath:
                    self._breath.on_trade_close(pnl)
            except Exception as _eng_e:
                log.debug(f"[ENGINES_CLOSE] {_eng_e}")
            # ════════════════════════════════════════════════════════════════

            m2_tot = self._m2_wins + self._m2_losses
            m2_wr  = (self._m2_wins / m2_tot * 100) if m2_tot > 0 else 0

            self._log_m2(
                "🟢" if is_win else "🔴",
                f"EXIT {self._shadow.get('direction', 'LONG')} {self._shadow_matrimonio} {'WIN' if is_win else 'LOSS'} "
                f"PnL=${pnl:+.4f} WR={m2_wr:.0f}% score={self._shadow['score']:.1f} "
                f"soglia={self._shadow['soglia']:.1f} [{reason}]"
            )

            # -- SCRIVI NEL DATABASE - sopravvive ai restart -------------------
            try:
                conn = _safe_connect(DB_PATH, timeout=30)
                conn.execute("""
                    INSERT INTO trades (event_type, asset, price, size, pnl, direction, reason, data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("M2_EXIT", SYMBOL, price, self._shadow.get("size", 0.5), pnl,
                      f"{self._shadow.get('direction', 'LONG')}_SHADOW", reason,
                      json.dumps({
                          "motore": "M2",
                          "matrimonio": self._shadow_matrimonio,
                          "score": round(self._shadow.get("score", 0), 2),
                          "soglia": round(self._shadow.get("soglia", 0), 2),
                          "entry_price": self._shadow.get("price_entry", 0),
                          "momentum": self._shadow_entry_momentum,
                          "volatility": self._shadow_entry_volatility,
                          "trend": self._shadow_entry_trend,
                          "regime": self._regime_current,
                          "direction": self._shadow.get("direction", "LONG"),
                          "is_win": is_win,
                          "pnl_netto": round(pnl, 4),
                          "pnl_lordo": round(pnl_gross, 4),
                          "duration": round(trade_duration, 1),
                          # V16: forensic score components
                          "sc_seed":   round(min(1.0,max(0.0,(self._shadow.get("seed",0)-0.20)/0.60))*25, 2),
                          "fp_wr_raw": round(self._shadow.get("fingerprint_wr", 0), 4),
                          "rsi_val":   round(getattr(self.campo, "_last_rsi", 50), 1),
                          "macd_val":  round(getattr(self.campo, "_last_macd_hist", 0), 4),
                          # V16: peak intra-trade
                          "peak_pnl":     round(self._trade_peak_pnl, 4),
                          "build":        BUILD_MD5,   # marchio di versione (quale codice ha prodotto il trade)
                          # ⭐ I NOSTRI ELEMENTI (24giu, Roberto): la FIRMA all'ingresso.
                          # Il nostro sistema si basa su QUESTI, non su momentum/regime/peak_pnl.
                          # picco_oss = grasso massimo in osservazione (doveva essere >=3 = alfa)
                          # crollo_oss = quanto sceso sotto nascita (il MAE = la firma)
                          "picco_oss":   round(float((self._shadow.get("picco_ingresso_reale", 0.0) if isinstance(getattr(self, "_shadow", None), dict) else 0.0) or 0.0), 3),
                          "sesso":       _sesso,
                          "crollo_oss":  round(float((self._shadow.get("crollo_ingresso_reale", 0.0) if isinstance(getattr(self, "_shadow", None), dict) else 0.0) or 0.0), 3),
                          "peak_delta_s": round(self._trade_peak_ts - self._shadow_entry_time, 1)
                                         if self._trade_peak_ts and self._shadow_entry_time else 0,
                          # CARICA VIVA (29mag, Roberto): traiettoria del seed
                          # PRIMA dell'entrata. seed_dir>0 = carica viva (saliva,
                          # impulso che nasce), <=0 = carica morta/piatta.
                          # Da qui si dimostra: il vincente ha la carica viva prima.
                          "seed_traj": list(getattr(self.campo, "_seed_history", []))[-5:],
                          "seed_dir":  round((lambda s: (s[-1]-s[0])/max(len(s)-1,1) if len(s)>=2 else 0.0)(list(getattr(self.campo,"_seed_history",[]))[-5:]), 4),
                          # SEME D'INGRESSO (4giu): il valore PRIMA del trade su cui
                          # il gate ha deciso. Stipato nello shadow alla nascita.
                          # Il pannello legge QUESTO, non il seed_traj dell'exit.
                          "seme_entry":      self._shadow.get("seme_entry"),
                          "seed_traj_entry": self._shadow.get("seed_traj_entry"),
                          # ════════════════════════════════════════════════════
                          # CARTA D'IDENTITA' DI NASCITA (4giu, Roberto: la
                          # "femmina travestita da maschio"). Questi sensori sono
                          # gia' calcolati dal seed alla nascita e gia' nello
                          # shadow; finora finivano SOLO nel canvas (che perde
                          # righe). Qui entrano nel trade vero: ogni femmina nasce
                          # con la firma completa, per smascherare chi ha seme da
                          # maschio ma un carattere di nascita da femmina.
                          # NB onesta': nascita_range_pos e' ROTTO (saturo 0/1);
                          # drift_slope/seed_dir e' lead debole. I non testati
                          # (compression, drift_persist, vol_pressure,
                          # comp_duration) sono i veri candidati. Decide il dato.
                          "n_range_pos":     self._shadow.get("nascita_range_pos"),
                          "n_compression":   self._shadow.get("nascita_compression"),
                          "n_drift_persist": self._shadow.get("nascita_drift_persist"),
                          "n_vol_pressure":  self._shadow.get("nascita_vol_pressure"),
                          "n_drift_slope":   self._shadow.get("nascita_drift_slope"),
                          "n_comp_duration": self._shadow.get("nascita_comp_duration"),
                          "n_sign_flips":    self._shadow.get("nascita_sign_flips"),
                          "pnl_10s":         self._shadow.get("pnl_10s"),
                          "pnl_20s":         self._shadow.get("pnl_20s"),
                      })))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[M2_DB] Errore salvataggio trade: {e}")

            # ═════════════════════════════════════════════════════════════════
            # HOOK CAPSULE V2.0 — Exit Side (22mag2026)
            # ─────────────────────────────────────────────────────────────────
            # Se all'entry abbiamo salvato una capsula da attribuire, le diciamo
            # ora "prenditi questo PnL". La capsula aggiorna pnl_cumulativo
            # e n_trade_attribuiti. Auto-valutazione FUNZIONA/NON_FUNZIONA
            # avverrà al prossimo tick del thread auto_verifica.
            # ═════════════════════════════════════════════════════════════════
            if (self._shadow_capsula_v2_attribuita and
                os.environ.get("CAPSULE_V2_HOOK_ENABLED", "false").lower() == "true"):
                try:
                    import requests as _rq_cv2x
                    _cap_id = self._shadow_capsula_v2_attribuita
                    _r_cv2x = _rq_cv2x.post(
                        f"http://localhost:10000/canvas/capsule_v2/{_cap_id}/attribuisci_trade",
                        json={
                            "pnl": round(pnl, 4),
                            "is_win": bool(is_win),
                            "direction": self._shadow.get('direction', 'LONG'),
                            "regime": self._regime_current,
                            "reason": reason,
                        },
                        timeout=2
                    )
                    if _r_cv2x.status_code == 200:
                        _d = _r_cv2x.json()
                        self._log_m2("🧬", f"CAPSULE_V2_EXIT: pnl {pnl:+.2f}$ attribuito a {_cap_id} | "
                                          f"cum={_d.get('pnl_cumulativo_totale', 0):+.2f}$ "
                                          f"n={_d.get('n_trade_attribuiti_totale', 0)}")
                    else:
                        log.warning(f"[CAPSULE_V2_EXIT] attribuzione fallita status={_r_cv2x.status_code}")
                except Exception as _e_cv2x:
                    log.debug(f"[CAPSULE_V2_EXIT] errore: {_e_cv2x}")
                finally:
                    self._shadow_capsula_v2_attribuita = None

            # ═════════════════════════════════════════════════════════════════
            # HOOK LOSS SFUGGITI (22mag2026) — Roberto: "se il loss passa
            # comunque, lì va riguardato il tutto"
            # ─────────────────────────────────────────────────────────────────
            # Se il trade è in LOSS e nessuna capsula v2 lo aveva preso in
            # carico (cioè self._shadow_capsula_v2_attribuita era None
            # all'entry), allora è un LOSS SFUGGITO: lo registriamo nella
            # tabella loss_sfuggiti con TUTTO il contesto, perché Roberto/AI
            # possano analizzarlo e modificare le capsule esistenti per
            # vederlo la prossima volta.
            # ═════════════════════════════════════════════════════════════════
            if (not is_win and pnl < 0 and
                os.environ.get("CAPSULE_V2_HOOK_ENABLED", "false").lower() == "true"):
                try:
                    import requests as _rq_loss
                    _payload_loss = {
                        "pnl": round(pnl, 4),
                        "regime": self._regime_current,
                        "direction": f"{self._shadow.get('direction', 'LONG')}_SHADOW",
                        "exit_reason": reason,
                        "momentum": self._shadow_entry_momentum,
                        "volatility": self._shadow_entry_volatility,
                        "trend": self._shadow_entry_trend,
                        "matrimonio": self._shadow_matrimonio,
                        "capsule_attive_count": 0,  # Per ora 0 — nessuna capsula v2 ha bloccato
                        "contesto_completo": {
                            "score": round(self._shadow.get("score", 0), 2),
                            "soglia": round(self._shadow.get("soglia", 0), 2),
                            "entry_price": self._shadow.get("price_entry", 0),
                            "duration": round(trade_duration, 1),
                            "peak_pnl": round(self._trade_peak_pnl, 4),
                            "build": BUILD_MD5,
                            "fp_wr": round(self._shadow.get("fingerprint_wr", 0), 4),
                        }
                    }
                    _rq_loss.post(
                        "http://localhost:10000/canvas/loss_sfuggiti/registra",
                        json=_payload_loss,
                        timeout=2
                    )
                    self._log_m2("📋", f"LOSS_SFUGGITO registrato: {pnl:+.2f}$ regime={self._regime_current} reason={reason}")
                except Exception as _e_loss:
                    log.debug(f"[LOSS_SFUGGITO] errore: {_e_loss}")

            # -- PERSISTI IL CERVELLO - Oracolo, Memoria, Calibratore ----------
            self._persist.save_brain(self.oracolo, self.memoria, self.calibratore)

            # ── CAPSULE EXECUTOR: monitora performance LIVE ────────────────
            try:
                if self.capsule_executor:
                    self.capsule_executor.record_live_result(pnl, is_win)
            except Exception as _ce_e:
                log.debug(f"[CE_RECORD] {_ce_e}")

            # -- PERSISTI STATS M2 - sopravvivono ai restart ------------------
            try:
                conn = _safe_connect(DB_PATH, timeout=30)
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_wins', ?)", (str(self._m2_wins),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_losses', ?)", (str(self._m2_losses),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_pnl', ?)", (str(self._m2_pnl),))
                conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('m2_trades', ?)", (str(self._m2_trades),))
                conn.commit()
                conn.close()
            except Exception as e:
                log.error(f"[M2_PERSIST] {e}")

            # -- LOG NARRATIVO -------------------------------------------------
            self.ai_explainer.log_decision("M2_EXIT",
                f"M2 shadow {self._shadow_matrimonio} | PnL=${pnl:+.4f} | {reason}",
                {'pnl': pnl, 'is_win': is_win, 'reason': reason,
                 'score': self._shadow.get('score', 0), 'soglia': self._shadow.get('soglia', 0)})

            # -- PASSA RISULTATO AL NARRATORE — impara dal dissanguamento ----
            # Il Narratore riceve ogni trade chiuso con contesto completo.
            # Questo rompe il secondo paradosso: il Ragionatore non può
            # imparare se non vede i risultati reali delle sue capsule.
            try:
                _trade_result = {
                    'ts':         datetime.utcnow().strftime('%H:%M:%S'),
                    'pnl':        round(pnl, 2),
                    'is_win':     is_win,
                    'reason':     reason,
                    'matrimonio': self._shadow_matrimonio,
                    'momentum':   self._shadow_entry_momentum,
                    'volatility': self._shadow_entry_volatility,
                    'trend':      self._shadow_entry_trend,
                    'regime':     self._regime_current,
                    'score':      self._shadow.get('score', 0) if self._shadow else 0,
                    'soglia':     self._shadow.get('soglia', 0) if self._shadow else 0,
                    'direction':  entry_direction,
                }
                if self.heartbeat_data is not None:
                    _storia = self.heartbeat_data.get('narratore_trade_storia', [])
                    _storia.append(_trade_result)
                    if len(_storia) > 20:
                        _storia = _storia[-20:]
                    self.heartbeat_data['narratore_trade_storia'] = _storia
                    # Aggiorna anche stats aggregate per il Narratore
                    _ns = self.heartbeat_data.get('narratore_trade_stats', {
                        'n': 0, 'wins': 0, 'pnl_tot': 0.0,
                        'last_context': '', 'consecutive_losses': 0
                    })
                    _ns['n'] += 1
                    _ns['pnl_tot'] = round(_ns['pnl_tot'] + pnl, 2)
                    if is_win:
                        _ns['wins'] += 1
                        _ns['consecutive_losses'] = 0
                    else:
                        _ns['consecutive_losses'] = _ns.get('consecutive_losses', 0) + 1
                    _ns['last_context'] = f"{self._shadow_entry_momentum}|{self._shadow_entry_volatility}|{self._shadow_entry_trend}"
                    _ns['wr'] = round(_ns['wins'] / _ns['n'], 3)
                    self.heartbeat_data['narratore_trade_stats'] = _ns
            except Exception as _nr_e:
                log.debug(f"[NARRATORE_TRADE] {_nr_e}")

            # ════════════════════════════════════════════════════════════════
            # PASSO A — REGOLA ANTIAEREA (29mag, Roberto):
            # Su ogni trade chiuso, se è WIN, verifica se la firma esatta ha
            # accumulato abbastanza WIN consecutivi da PULIRE un eventuale blocco
            # permanente. È il meccanismo che impedisce la prigione perpetua:
            # un contesto bloccato può sempre redimersi dimostrando di vincere.
            # Firma = momentum|volatility|trend|regime|direction (5 dimensioni).
            # ════════════════════════════════════════════════════════════════
            try:
                _aa_firma = (f"{self._shadow_entry_momentum}|{self._shadow_entry_volatility}|"
                             f"{self._shadow_entry_trend}|{self._regime_current}|{entry_direction}")
                self._pulisci_blocco_se_win(_aa_firma, is_win)
            except Exception as _aa_e:
                log.debug(f"[ANTIAEREA] {_aa_e}")

            # ── ORACLE WIN TRIGGER — cattura pattern vincente ────────────
            # Ogni trade vincente genera snapshot e trigger per Oracle Auto.
            # Oracle analizza perché ha vinto e genera capsule che amplifica quel pattern.
            if is_win and self.heartbeat_data is not None:
                try:
                    _win_snapshot = {
                        'ts':           datetime.utcnow().isoformat(),
                        'pnl':          round(pnl, 4),
                        'reason':       reason,
                        'matrimonio':   self._shadow_matrimonio,
                        'momentum':     self._shadow_entry_momentum,
                        'volatility':   self._shadow_entry_volatility,
                        'trend':        self._shadow_entry_trend,
                        'regime':       self._regime_current,
                        'oi_stato':     self._oi_stato,
                        'oi_carica':    round(self._oi_carica, 3),
                        'score':        round(self._shadow.get('score', 0) if self._shadow else 0, 1),
                        'soglia':       round(self._shadow.get('soglia', 0) if self._shadow else 0, 1),
                        'rsi':          round(getattr(self.campo, '_last_rsi', 50), 1),
                        'macd_hist':    round(getattr(self.campo, '_last_macd_hist', 0), 4),
                        'direction':    entry_direction,
                        'duration_s':   round(trade_duration, 1),
                    }
                    # Conserva storico vincite (max 50)
                    _win_history = self.heartbeat_data.get('oracle_win_history', [])
                    _win_history.append(_win_snapshot)
                    if len(_win_history) > 50:
                        _win_history = _win_history[-50:]
                    self.heartbeat_data['oracle_win_history'] = _win_history

                    # Trigger Oracle solo per profit significativi (> $1 netto)
                    # I micro-profit non hanno pattern solido da amplificare
                    if pnl > 1.0:
                        _fingerprint = f"{self._shadow_entry_momentum}|{self._shadow_entry_volatility}|{self._shadow_entry_trend}"
                        _trigger = f"WIN_PATTERN_{_fingerprint}_pnl{pnl:.2f}_reason{reason[:20]}"
                        self.heartbeat_data['oracle_trigger'] = _trigger
                        log.info(f"[ORACLE_TRIGGER] 🟢 WIN_PATTERN: {_fingerprint} PnL={pnl:.2f}")
                except Exception as _owt_e:
                    log.debug(f"[ORACLE_WIN_TRIGGER] {_owt_e}")

            # ── ORACLE LOSS TRIGGER — cattura pattern perdita ─────────────
            # Ogni trade in perdita genera uno snapshot completo del contesto
            # e scrive oracle_trigger nel heartbeat per svegliare Oracle Auto.
            # Oracle analizza, capisce il pattern, genera capsule correttiva permanente.
            if not is_win and self.heartbeat_data is not None:
                try:
                    _loss_snapshot = {
                        'ts':           datetime.utcnow().isoformat(),
                        'pnl':          round(pnl, 4),
                        'pnl_gross':    round(pnl_gross, 4),
                        'reason':       reason,
                        'matrimonio':   self._shadow_matrimonio,
                        'momentum':     self._shadow_entry_momentum,
                        'volatility':   self._shadow_entry_volatility,
                        'trend':        self._shadow_entry_trend,
                        'regime':       self._regime_current,
                        'oi_stato':     self._oi_stato,
                        'oi_carica':    round(self._oi_carica, 3),
                        'score':        round(self._shadow.get('score', 0) if self._shadow else 0, 1),
                        'soglia':       round(self._shadow.get('soglia', 0) if self._shadow else 0, 1),
                        'rsi':          round(getattr(self.campo, '_last_rsi', 50), 1),
                        'macd_hist':    round(getattr(self.campo, '_last_macd_hist', 0), 4),
                        'direction':    entry_direction,
                        'duration_s':   round(trade_duration, 1),
                        'm2_state':     self._state,
                        'loss_streak':  self._m2_loss_streak,
                    }
                    # Conserva storico perdite (max 50)
                    _loss_history = self.heartbeat_data.get('oracle_loss_history', [])
                    _loss_history.append(_loss_snapshot)
                    if len(_loss_history) > 50:
                        _loss_history = _loss_history[-50:]
                    self.heartbeat_data['oracle_loss_history'] = _loss_history

                    # Trigger Oracle — pattern da analizzare
                    _fingerprint = f"{self._shadow_entry_momentum}|{self._shadow_entry_volatility}|{self._shadow_entry_trend}"
                    _trigger = f"LOSS_PATTERN_{_fingerprint}_pnl{pnl:.2f}_reason{reason[:20]}"
                    self.heartbeat_data['oracle_trigger'] = _trigger
                    log.info(f"[ORACLE_TRIGGER] 🔴 LOSS_PATTERN: {_fingerprint} PnL={pnl:.2f} reason={reason}")
                except Exception as _olt_e:
                    log.debug(f"[ORACLE_LOSS_TRIGGER] {_olt_e}")

            # -- EXPORT LATENCY STATS all'heartbeat ----------------------
            try:
                if self.heartbeat_data is not None:
                    ls = self._latency_stats
                    _avg_tot = round(ls['slippage_sum'] / ls['n_total'], 4) if ls['n_total'] > 0 else 0
                    _avg_exp = round(ls['slippage_sum_exp'] / ls['n_explosive'], 4) if ls['n_explosive'] > 0 else 0
                    _allarme = _avg_exp > 0.05 or _avg_tot > 0.03
                    self.heartbeat_data['latency_stats'] = {
                        'n_total':          ls['n_total'],
                        'n_explosive':      ls['n_explosive'],
                        'slippage_medio':   _avg_tot,
                        'slippage_medio_exp': _avg_exp,
                        'slippage_max':     ls['slippage_max'],
                        'slippage_max_exp': ls['slippage_max_exp'],
                        'costo_usd_tot':    round(ls['costo_usd_tot'], 2),
                        'costo_usd_exp':    round(ls['costo_usd_exp'], 2),
                        'allarme':          _allarme,
                        'storia':           ls['storia'][-5:],
                        'verdetto':         '🔴 SERVE VPS' if _allarme else '🟢 LATENZA OK',
                    }
            except Exception as _lte:
                log.debug(f"[LATENCY_EXPORT] {_lte}")

            # ═══════════════════════════════════════════════════════════
            # PATCH 15 BUG 22 — Hook CapsulaCanvas exit (osservativo)
            # Chiude il cerchio con outcome reale del trade.
            # Try/except totale: mai blocca il close.
            # ═══════════════════════════════════════════════════════════
            try:
                if getattr(self, "canvas", None) is not None:
                    _canvas_tid_close = (self._shadow.get("_canvas_tid")
                                         if self._shadow else None) or f"unk_{int(time.time()*1000)}"
                    # Outcome: WIN_NET / LOSS_FEE / LOSS_REAL / fallback
                    if is_win:
                        _canvas_outcome = "WIN_NET"
                    elif pnl_gross > 0:
                        _canvas_outcome = "LOSS_FEE"
                    else:
                        _canvas_outcome = "LOSS_REAL"
                    self.canvas.observe_exit(
                        trade_id=_canvas_tid_close,
                        outcome=_canvas_outcome,
                        pnl_netto=float(pnl),
                        durata_s=float(trade_duration) if trade_duration else 0.0,
                        reason=str(reason),
                        nascita={
                            "range_pos":     self._shadow.get("nascita_range_pos") if self._shadow else None,
                            "drift_slope":   self._shadow.get("nascita_drift_slope") if self._shadow else None,
                            "seed_score":    self._shadow.get("nascita_seed_score") if self._shadow else None,
                            "compression":   self._shadow.get("nascita_compression") if self._shadow else None,
                            "drift_persist": self._shadow.get("nascita_drift_persist") if self._shadow else None,
                            "vol_pressure":  self._shadow.get("nascita_vol_pressure") if self._shadow else None,
                            "comp_duration": self._shadow.get("nascita_comp_duration") if self._shadow else None,
                            "regime_entry":  self._shadow.get("regime_entry") if self._shadow else None,
                            "momentum":      self._shadow.get("momentum_entry") if self._shadow else None,
                            "volatility":    self._shadow.get("volatility_entry") if self._shadow else None,
                            "trend":         self._shadow.get("trend_entry") if self._shadow else None,
                            "direction":     self._shadow.get("direction") if self._shadow else None,
                            "score":         self._shadow.get("score") if self._shadow else None,
                        }
                    )
            except Exception as _cxe:
                log.debug(f"[CANVAS_HOOK_EXIT_ERR] {_cxe}")

            # ═══════════════════════════════════════════════════════════
            # PATCH 17 — Hook CapsulaMemoria exit (SKILL REGINA)
            # Alimenta la memoria viva con ogni trade chiuso.
            # Scatena auto-scoperte interne se MEMORIA_AUTOSCOPERTE=on.
            # ═══════════════════════════════════════════════════════════
            try:
                if getattr(self, "memoria", None) is not None:
                    _mem_tid = (self._shadow.get("_canvas_tid")
                                if self._shadow else None) or f"unk_{int(time.time()*1000)}"
                    # Calcolo outcome se non già fatto sopra
                    try:
                        _mem_outcome = _canvas_outcome
                    except NameError:
                        if is_win:
                            _mem_outcome = "WIN_NET"
                        elif pnl_gross > 0:
                            _mem_outcome = "LOSS_FEE"
                        else:
                            _mem_outcome = "LOSS_REAL"
                    # Fingerprint da self._shadow (entry context)
                    _mem_fp = (
                        f"{self._shadow_entry_momentum or '?'}|"
                        f"{self._shadow_entry_volatility or '?'}|"
                        f"{self._shadow_entry_trend or '?'}|"
                        f"{entry_direction or '?'}"
                    ) if self._shadow_entry_momentum else "?|?|?|?"
                    # Capsule voto se disponibile dal verbale
                    _mem_capvoto = {}
                    try:
                        if isinstance(_verbale, dict):
                            _mem_capvoto = {
                                "block_score": _verbale.get("capsule_block_score", 0),
                                "boost_score": _verbale.get("capsule_boost_score", 0),
                                "reasons":     _verbale.get("capsule_reasons", []),
                            }
                    except Exception:
                        pass
                    self.memoria.osserva_trade_chiuso({
                        "trade_id":     _mem_tid,
                        "ts_open":      self._shadow_entry_time,
                        "ts_close":     time.time(),
                        "outcome":      _mem_outcome,
                        "pnl_netto":    float(pnl),
                        "durata_s":     float(trade_duration) if trade_duration else 0.0,
                        "fingerprint":  _mem_fp,
                        "capsule_voto": _mem_capvoto,
                        "reason_close": str(reason),
                        "contesto":     {
                            "regime": self._regime_current,
                            "score":  getattr(self, '_last_score', None),
                        },
                    })
            except Exception as _mem_e:
                log.debug(f"[MEMORIA_HOOK_EXIT_ERR] {_mem_e}")

            # ═══════════════════════════════════════════════════════════
            # PATCH 18 — Hook CapsulaFase exit (osserva outcome)
            # Lega la fase osservata all'entry con l'esito reale del trade.
            # Aggiorna statistiche di precisione e auto-quarantena (L6).
            # ═══════════════════════════════════════════════════════════
            try:
                if getattr(self, "capsula_fase", None) is not None:
                    # trade_id deve essere lo stesso usato in consulta()
                    _cf_tid = (self._shadow.get("_cf_tid")
                              if self._shadow else None) or f"unk_{int(time.time()*1000)}"
                    try:
                        _cf_outcome = _canvas_outcome
                    except NameError:
                        if is_win:
                            _cf_outcome = "WIN_NET"
                        elif pnl_gross > 0:
                            _cf_outcome = "LOSS_FEE"
                        else:
                            _cf_outcome = "LOSS_REAL"
                    self.capsula_fase.observe_outcome(
                        trade_id=_cf_tid,
                        outcome=_cf_outcome,
                        pnl_netto=float(pnl),
                        durata_s=float(trade_duration) if trade_duration else 0.0
                    )
            except Exception as _cf_oe:
                log.debug(f"[CAPSULA_FASE_HOOK_EXIT_ERR] {_cf_oe}")

            # ═══════════════════════════════════════════════════════════
            # PATCH 20 — CapsulaMatrigna observe_outcome
            # Alimenta la capsula figlia con l'esito del trade.
            # Questa è la cosa fondamentale: ogni trade chiuso fa imparare
            # la capsula relativa. La SuperRisponditrice cresce con ogni
            # osservazione.
            # ═══════════════════════════════════════════════════════════
            try:
                if getattr(self, "matrigna", None) is not None:
                    _mat_tid_ex = (self._shadow.get("_canvas_tid")
                                   if self._shadow else None) or f"unk_{int(time.time()*1000)}"
                    self.matrigna.observe_outcome(
                        trade_id=_mat_tid_ex,
                        momentum=self._shadow_entry_momentum or '?',
                        volatility=self._shadow_entry_volatility or '?',
                        trend=self._shadow_entry_trend or '?',
                        regime=self._regime_current or '?',
                        direction=getattr(self.campo, '_direction', '?'),
                        pnl_netto=float(pnl),
                        duration=float(trade_duration) if trade_duration else 0.0,
                        peak_pnl=float(self._trade_peak_pnl) if hasattr(self, '_trade_peak_pnl') else 0.0,
                        peak_delta_s=0.0,
                    )
            except Exception as _mat_oe:
                log.debug(f"[CAP_MATRIGNA_HOOK_EXIT_ERR] {_mat_oe}")

            # ═══════════════════════════════════════════════════════════
            # CAPSULA REGIME-EDGE (31mag) — observe: verifica se i suoi
            # verdetti erano giusti. Serve a raccogliere le prove REALI
            # (oggi UP è simulato) per poter armare L4 con dati veri.
            # ═══════════════════════════════════════════════════════════
            try:
                if getattr(self, "cap_regime_edge", None) is not None:
                    self.cap_regime_edge.observe_outcome(
                        momentum=self._shadow_entry_momentum or '?',
                        trend=self._shadow_entry_trend or '?',
                        pnl_reale=float(pnl),
                    )
            except Exception as _re_oe:
                log.debug(f"[CAP_REGIME_EDGE_HOOK_EXIT_ERR] {_re_oe}")

        except Exception as e:
            import traceback
            self._log_m2("💥", f"ERRORE close_shadow: {e}")
            log.error(f"[M2_CLOSE_ERROR] {e}\n{traceback.format_exc()}")
        finally:
            self._shadow_closing = False
            # Reset shadow SEMPRE - anche se c'è un errore, non lasciare trade fantasma
            self._shadow                   = None
            self._shadow_entry_time        = None
            self._shadow_entry_momentum    = None
            self._shadow_entry_volatility  = None
            self._shadow_entry_trend       = None
            self._shadow_entry_fingerprint = None
            self._shadow_max_price         = None
            self._shadow_min_price         = None
            self._shadow_matrimonio        = None
            self._trade_peak_pnl     = 0.0
            self._trade_peak_ts      = None
            self._trade_peak_energia = 0.0
        self._trade_peak_pnl      = 0.0   # V16: massimo PnL intra-trade
        self._trade_peak_ts       = None
        self._trade_peak_energia  = 0.0

    def _get_ia_soglia_boost(self, momentum: str, volatility: str, trend: str) -> float:
        """
        Legge le capsule L3 attive di tipo boost_soglia e ritorna il delta totale.
        Il campo.evaluate lo applica sopra la soglia calcolata (con floor=48 invariato).
        """
        try:
            caps = self.capsule_runtime.capsules
            ora  = time.time()
            boost = 0.0
            for c in caps:
                if not c.get('enabled'):
                    continue
                if c.get('scade_ts') and c['scade_ts'] < ora:
                    continue
                azione = c.get('azione', {})
                if azione.get('type') != 'boost_soglia':
                    continue
                # Verifica trigger (può essere vuoto = sempre attivo)
                triggers = c.get('trigger', [])
                ctx = {'momentum': momentum, 'volatility': volatility, 'trend': trend}
                if triggers and not all(self.capsule_runtime._check_trigger(t, ctx) for t in triggers):
                    continue
                boost += azione.get('params', {}).get('delta', 0.0)
            # V16: cap massimo boost soglia = +5 punti
            # La soglia non può essere alzata di più di 5 punti dalle capsule streak
            return min(boost, 5.0)
        except Exception:
            return 0.0

    def _m2_loss_consecutivi(self) -> int:
        """Loss consecutivi del Motore 2."""
        count = 0
        for r in reversed(list(self.campo._recent_results)):
            if not r:
                count += 1
            else:
                break
        return count

    def _calcola_breakeven_dinamico(self, momentum: str, volatility: str, trend: str) -> float:
        """
        Calcola il breakeven minimo dinamico dal DB reale.
        Legge i trade vinti in questo contesto e usa il PnL medio come soglia.
        Zero hardcode — il sistema impara da solo dai trade reali.
        """
        try:
            import sqlite3, json
            db_path = getattr(self, '_db_path', '/var/data/trading_data.db')
            with _safe_connect(db_path, timeout=30) as conn:
                rows = conn.execute("""
                    SELECT pnl, data_json FROM trades
                    WHERE event_type='EXIT' AND pnl > 0
                    ORDER BY timestamp DESC LIMIT 200
                """).fetchall()

                wins_contesto = []
                wins_simile = []

                for pnl, dj in rows:
                    try:
                        d = json.loads(dj)
                        m, v, t = d.get('momentum'), d.get('volatility'), d.get('trend')
                        if m == momentum and v == volatility and t == trend:
                            wins_contesto.append(pnl)
                        elif v == volatility and t == trend:
                            wins_simile.append(pnl)
                    except Exception:
                        pass

                if len(wins_contesto) >= 5:
                    breakeven = max(0.5, sum(wins_contesto) / len(wins_contesto) * 0.5)
                    self._log_m2("📊", f"BREAKEVEN contesto={momentum}|{volatility}|{trend} n={len(wins_contesto)} val=${breakeven:.1f}")
                    return breakeven

                if len(wins_simile) >= 5:
                    breakeven = max(0.5, sum(wins_simile) / len(wins_simile) * 0.5)
                    self._log_m2("📊", f"BREAKEVEN simile={volatility}|{trend} n={len(wins_simile)} val=${breakeven:.1f}")
                    return breakeven

                # Nessun dato — usa range prezzi recenti come proxy
                _prices_buf = list(self.campo._prices_ta) if hasattr(self.campo, '_prices_ta') else []
                if len(_prices_buf) >= 10:
                    _range = max(_prices_buf[-20:]) - min(_prices_buf[-20:])
                    breakeven = max(0.5, _range * 0.3)
                    self._log_m2("📊", f"BREAKEVEN proxy range=${_range:.1f} val=${breakeven:.1f}")
                    return breakeven

                return 0.5  # fallback — non blocca mai per mancanza dati

        except Exception as e:
            self._log_m2("⚠️", f"BREAKEVEN_ERROR: {e}")
            return 0.5

    # ========================================================================
    # PHANTOM TRACKER - "SE AVESSI FATTO"
    # Traccia i trade bloccati e calcola cosa sarebbe successo.
    # Zavorra o protezione? I numeri rispondono.
    # ========================================================================

    def _record_phantom(self, price, block_reason, seed_score, momentum, volatility, trend):
        """Registra un trade fantasma - bloccato da un livello di protezione."""
        # ════════════════════════════════════════════════════════════════
        # FIX #21 (12mag2026): FINGERPRINT FISICO al momento del blocco
        # Cattura snapshot di TUTTI gli organi per analisi forensica futura.
        # ════════════════════════════════════════════════════════════════
        _ts_30s_str = _ts_30s_dir = _ts_30s_coe = None
        _ts_2_str = _ts_2_dir = _ts_2_coe = None
        _ts_10_str = _ts_10_dir = _ts_10_coe = None
        _ts_conf = None
        try:
            if hasattr(self, 'tsunami') and self.tsunami is not None:
                _last = self.tsunami.last_decision()
                if _last is not None:
                    _v = _last.get('verdetti', {})
                    _t30 = _v.get('30s', {})
                    _ts_30s_str = _t30.get('strength')
                    _ts_30s_dir = _t30.get('direction')
                    _ts_30s_coe = _t30.get('coerenza')
                    _t2 = _v.get('2min', {})
                    _ts_2_str = _t2.get('strength')
                    _ts_2_dir = _t2.get('direction')
                    _ts_2_coe = _t2.get('coerenza')
                    _t10 = _v.get('10min', {})
                    _ts_10_str = _t10.get('strength')
                    _ts_10_dir = _t10.get('direction')
                    _ts_10_coe = _t10.get('coerenza')
                    _ts_conf = _last.get('confidenza')
        except Exception:
            pass
        
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
            # FIX #21: fingerprint fisico per analisi forensica
            '_fp_ts_30s_str':   _ts_30s_str,
            '_fp_ts_30s_dir':   _ts_30s_dir,
            '_fp_ts_30s_coe':   _ts_30s_coe,
            '_fp_ts_2_str':     _ts_2_str,
            '_fp_ts_2_dir':     _ts_2_dir,
            '_fp_ts_2_coe':     _ts_2_coe,
            '_fp_ts_10_str':    _ts_10_str,
            '_fp_ts_10_dir':    _ts_10_dir,
            '_fp_ts_10_coe':    _ts_10_coe,
            '_fp_ts_conf':      _ts_conf,
            '_fp_oi_carica':    getattr(self, '_oi_carica', None),
            '_fp_rsi':          getattr(self.campo, '_last_rsi', None) if hasattr(self, 'campo') else None,
            '_fp_macd':         getattr(self.campo, '_last_macd', None) if hasattr(self, 'campo') else None,
            '_fp_matrimonio':   getattr(self, '_shadow_matrimonio', '') or '',
            '_fp_score':        getattr(self.campo, '_last_score', None) if hasattr(self, 'campo') else None,
            '_fp_soglia':       getattr(self.campo, '_last_soglia', None) if hasattr(self, 'campo') else None,
        }
        self._phantoms_open.append(phantom)

        # Classifica il blocco per statistiche
        reason_key = block_reason.split("_")[0] if "_" in block_reason else block_reason
        if "DRIFT" in block_reason:    reason_key = "DRIFT_VETO"
        elif "TOSSICO" in block_reason: reason_key = "VETO_TOSSICO"
        elif "LOSS_CONSEC" in block_reason: reason_key = "LOSS_CONSECUTIVI"
        elif "SCORE_SOTTO" in block_reason: reason_key = "SCORE_INSUFFICIENTE"
        elif "ENERGY_BOTH" in block_reason: reason_key = "ENERGY_BOTH"
        elif "ENERGY_SCORE" in block_reason: reason_key = "ENERGY_SCORE"
        elif "ENERGY_TREND" in block_reason: reason_key = "ENERGY_TREND"
        elif "RANGE_MIDZONE" in block_reason: reason_key = "RANGE_MIDZONE"
        elif "OC1" in block_reason: reason_key = "OC1_MIDZONE"
        elif "OC2" in block_reason: reason_key = "OC2_RSI"
        elif "OC3" in block_reason: reason_key = "OC3_DRIFT"
        elif "OC4" in block_reason: reason_key = "OC4_FALSO_FORTE"
        elif "OC5" in block_reason: reason_key = "OC5_LOSS_STREAK"
        elif "CTX_MATCH" in block_reason: reason_key = "CTX_MATCH"
        elif "FANTASMA" in block_reason: reason_key = "FANTASMA"
        else: reason_key = block_reason

        if reason_key not in self._phantom_stats:
            self._phantom_stats[reason_key] = {
                'blocked': 0, 'would_win': 0, 'would_lose': 0,
                'pnl_saved': 0.0, 'pnl_missed': 0.0
            }
        self._phantom_stats[reason_key]['blocked'] += 1

    def _update_phantoms(self, price, momentum):
        """Aggiorna tutti i fantasmi aperti - chiamato ad ogni tick."""
        to_close = []
        for i, ph in enumerate(self._phantoms_open):
            if price > ph['max_price']:
                ph['max_price'] = price
                ph['max_price_ts'] = time.time()   # FIX 19giu: QUANDO ha toccato il picco
            if price < ph['min_price']:
                ph['min_price'] = price

            duration = time.time() - ph['entry_time']
            # PnL LORDO USDC: fee esclusa dal monitoring (come il bot reale)
            _ph_exp = 5000.0
            _ph_btc = _ph_exp / ph['price_entry']
            _ph_d   = price - ph['price_entry'] if ph.get('direction','LONG') == 'LONG' else ph['price_entry'] - price
            pnl     = round(_ph_d * _ph_btc, 4)  # lordo — fee solo al close
            pnl_pct = (pnl / ph['price_entry']) * 100

            # -- Stesse regole di uscita del bot reale --
            # Stop live lordo
            if pnl < -self.STOP_LIVE:
                to_close.append((i, price, "HARD_STOP"))
                continue
            # DECEL (semplificato: dopo 15s se in perdita)
            if duration > 15 and pnl < 0:
                to_close.append((i, price, "DECEL_SIM"))
                continue
            # SMORZ - direzione-aware
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
        
        # -- SHADOW SHORT PHANTOMS - SHORT evitati in RANGING ----------
        if hasattr(self, '_shadow_short_phantoms'):
            to_close_ss = []
            for i, ph in enumerate(self._shadow_short_phantoms):
                if price > ph['max_price']:
                    ph['max_price'] = price
                if price < ph['min_price']:
                    ph['min_price'] = price
                
                duration = time.time() - ph['entry_time']
                # PnL SHORT: guadagna se prezzo scende
                delta = ph['price_entry'] - price
                exposure = self.TRADE_SIZE_USD * self.LEVERAGE
                btc_qty = exposure / ph['price_entry']
                pnl_gross = delta * btc_qty
                pnl = pnl_gross - (exposure * self.FEE_PCT * 2)
                
                close_reason = None
                if pnl < -self.STOP_LIVE:  # stop lordo $7
                    close_reason = "HARD_STOP_SIM"
                elif duration > 15 and pnl < 0:
                    close_reason = "DECEL_SIM"
                elif duration > 10 and momentum == "FORTE":  # SHORT esce su FORTE
                    close_reason = "SMORZ_SIM"
                elif duration > 20 and pnl > 0:
                    close_reason = "WIN_SIM"
                elif duration > 60:
                    close_reason = "TIMEOUT_SIM"
                
                if close_reason:
                    to_close_ss.append((i, pnl, duration, close_reason))
            
            for i, pnl, dur, reason in reversed(to_close_ss):
                ph = self._shadow_short_phantoms.pop(i)
                if not hasattr(self, '_shadow_short_results'):
                    self._shadow_short_results = deque(maxlen=100)
                self._shadow_short_results.append({
                    'pnl': round(pnl, 2),
                    'duration': round(dur, 1),
                    'is_win': pnl > 0,
                    'exit_reason': reason,
                    'drift': ph['drift'],
                    'macd_hist': ph['macd_hist'],
                    'bearish_energy': ph['bearish_energy'],
                    'price_entry': ph['price_entry'],
                })

    def _close_phantom(self, idx, price, reason):
        """Chiude un fantasma e registra il risultato."""
        try:
            ph = self._phantoms_open.pop(idx)
            # PnL REALE FUTURES - stessa formula dei trade veri
            if ph.get('direction', 'LONG') == 'SHORT':
                delta_price = ph['price_entry'] - price
            else:
                delta_price = price - ph['price_entry']
            exposure = self.TRADE_SIZE_USD * self.LEVERAGE
            btc_qty = exposure / ph['price_entry']
            pnl_gross = delta_price * btc_qty
            total_fees = exposure * self.FEE_PCT * 2
            pnl = pnl_gross - total_fees
            is_win = pnl > 0

            # Aggiorna statistiche per livello di blocco
            block = ph['block_reason']
            reason_key = block.split("_")[0] if "_" in block else block
            if "DRIFT" in block:    reason_key = "DRIFT_VETO"
            elif "TOSSICO" in block: reason_key = "VETO_TOSSICO"
            elif "LOSS_CONSEC" in block: reason_key = "LOSS_CONSECUTIVI"
            elif "SCORE_SOTTO" in block: reason_key = "SCORE_INSUFFICIENTE"
            elif "ENERGY_BOTH" in block: reason_key = "ENERGY_BOTH"
            elif "ENERGY_SCORE" in block: reason_key = "ENERGY_SCORE"
            elif "ENERGY_TREND" in block: reason_key = "ENERGY_TREND"
            elif "RANGE_MIDZONE" in block: reason_key = "RANGE_MIDZONE"
            elif "OC1" in block: reason_key = "OC1_MIDZONE"
            elif "OC2" in block: reason_key = "OC2_RSI"
            elif "OC3" in block: reason_key = "OC3_DRIFT"
            elif "OC4" in block: reason_key = "OC4_FALSO_FORTE"
            elif "OC5" in block: reason_key = "OC5_LOSS_STREAK"
            elif "CTX_MATCH" in block: reason_key = "CTX_MATCH"
            elif "FANTASMA" in block: reason_key = "FANTASMA"
            else: reason_key = block

            if reason_key not in self._phantom_stats:
                self._phantom_stats[reason_key] = {
                    'blocked': 0, 'would_win': 0, 'would_lose': 0,
                    'pnl_saved': 0.0, 'pnl_missed': 0.0
                }

            stats = self._phantom_stats[reason_key]
            # FIX 11MAG: rimossa doppia-fee.
            # `pnl` ha GIÀ le fee tolte: `total_fees = exposure * FEE_PCT * 2 = $2.00`
            # sottratte alla riga `pnl = pnl_gross - total_fees` (vedi sopra).
            # Prima qui si sottraeva ANCORA self.FEE_TRADE ($2.00) → fee contata 2 volte.
            # Effetto del bug: phantom WR=0% sistematico in RANGING, pnl_saved gonfiato,
            # Phantom Supervisor irrigidiva i gate basandosi su numeri fasulli.
            pnl_netto = pnl
            is_win_netto = pnl_netto > 0
            if is_win_netto:
                stats['would_win'] += 1
                stats['pnl_missed'] += pnl_netto
            else:
                stats['would_lose'] += 1
                stats['pnl_saved'] += abs(pnl_netto)

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

            # ════════════════════════════════════════════════════════════════
            # FIX #21 (12mag2026): SALVATAGGIO FORENSICO PHANTOM
            # ════════════════════════════════════════════════════════════════
            # Salva fingerprint fisico completo + esito WIN/LOSS su DB.
            # Solo i phantom bloccati da TSUNAMI_* (focus dell'analisi).
            # Roberto: "Sono gli unici soldi che possiamo prendere".
            # ════════════════════════════════════════════════════════════════
            try:
                # FIX 29mag (Roberto): il post-mortem registrava SOLO i blocchi
                # con "TSUNAMI" nel nome. Dal 14mag i blocchi sono diventati
                # CTX_TOSSICO/SC_BLOCCA/REGIME_TOSSICO (mai "TSUNAMI") → il filtro
                # era sempre falso → forensic MORTO dal 14 maggio. Ora registra
                # TUTTI i blocchi: il sistema di verifica torna a vedere il dopo.
                if block:  # qualunque blocco, non solo TSUNAMI
                    import sqlite3 as _sql
                    _duration = time.time() - ph.get('entry_time', time.time())

                    # ── FIX MFE/MAE (28mag, Roberto) ─────────────────────────
                    # max_price/min_price sono tracciati a ogni tick in
                    # _update_phantoms. Da quelli derivo MFE (movimento
                    # favorevole) e MAE (movimento avverso) IN DOLLARI per
                    # LONG e SHORT. Esposizione = $5000 (come PnL lordo nel
                    # tracking, riga ~13234). Da questi 4 numeri la firma
                    # vera del trade emerge: dove andava il prezzo, non
                    # solo dove abbiamo chiuso.
                    # ──────────────────────────────────────────────────────────
                    _ph_max   = ph.get('max_price', ph['price_entry'])
                    _ph_min   = ph.get('min_price', ph['price_entry'])
                    _ph_pe    = ph['price_entry']
                    _ph_exp   = 5000.0
                    _ph_btc   = _ph_exp / _ph_pe if _ph_pe else 0.0
                    _ph_dir   = ph.get('direction', 'LONG')
                    if _ph_dir == 'LONG':
                        _mfe_usd = round((_ph_max - _ph_pe) * _ph_btc, 4)  # quanto è salito a favore
                        _mae_usd = round((_ph_pe - _ph_min) * _ph_btc, 4)  # quanto è sceso contro
                    else:  # SHORT
                        _mfe_usd = round((_ph_pe - _ph_min) * _ph_btc, 4)  # favorevole se scende
                        _mae_usd = round((_ph_max - _ph_pe) * _ph_btc, 4)  # avverso se sale

                    _conn = _sql.connect(DB_PATH)
                    _conn.execute("""
                        INSERT INTO phantom_forensic (
                            ts_entry, ts_close, block_reason, direction,
                            price_entry, price_close, pnl_netto, is_win, duration_sec,
                            ts_30s_strength, ts_30s_direction, ts_30s_coerenza,
                            ts_2min_strength, ts_2min_direction, ts_2min_coerenza,
                            ts_10min_strength, ts_10min_direction, ts_10min_coerenza,
                            ts_confidenza,
                            seed_score, oi_carica, rsi, macd,
                            momentum, volatility, trend, regime, matrimonio,
                            score, soglia,
                            max_price, min_price, mfe_usd, mae_usd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  ?, ?,
                                  ?, ?, ?, ?)
                    """, (
                        ph.get('entry_time'), time.time(), block, ph.get('direction','LONG'),
                        ph['price_entry'], price, round(pnl_netto, 4), 1 if is_win_netto else 0, _duration,
                        ph.get('_fp_ts_30s_str'), ph.get('_fp_ts_30s_dir'), ph.get('_fp_ts_30s_coe'),
                        ph.get('_fp_ts_2_str'),   ph.get('_fp_ts_2_dir'),   ph.get('_fp_ts_2_coe'),
                        ph.get('_fp_ts_10_str'),  ph.get('_fp_ts_10_dir'),  ph.get('_fp_ts_10_coe'),
                        ph.get('_fp_ts_conf'),
                        ph.get('seed_score'), ph.get('_fp_oi_carica'),
                        ph.get('_fp_rsi'), ph.get('_fp_macd'),
                        ph.get('momentum',''), ph.get('volatility',''),
                        ph.get('trend',''), ph.get('regime',''),
                        ph.get('_fp_matrimonio',''),
                        ph.get('_fp_score'), ph.get('_fp_soglia'),
                        _ph_max, _ph_min, _mfe_usd, _mae_usd,
                    ))
                    _conn.commit()
                    _conn.close()
            except Exception as _fe:
                pass  # mai bloccare per errori di logging

            # ── SOSPENSIONE: collega Phantom al meccanismo di osservazione ──
            # Ogni fantasma chiuso aggiorna lo shadow della sospensione attiva
            try:
                if hasattr(self, 'capsule_manager') and self.capsule_manager:
                    _ph_momentum  = ph.get('momentum', '')
                    _ph_volatility = ph.get('volatility', '')
                    _ph_trend     = ph.get('trend', '')
                    _ph_dir       = ph.get('direction', 'LONG')
                    if _ph_momentum and _ph_volatility and _ph_trend:
                        _ph_pattern = f"{_ph_momentum}|{_ph_volatility}|{_ph_trend}|{_ph_dir}"
                        self.capsule_manager.registra_shadow(
                            pattern   = _ph_pattern,
                            pnl       = round(pnl_netto, 2),
                            oi_carica = self._oi_carica
                        )
            except Exception as _ph_e:
                pass  # non bloccare mai per errori shadow

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
            verdetto = f"ZAVORRA (-${pnl_missed - pnl_saved:.0f} persi in opportunita)"
        else:
            verdetto = "NEUTRO"

        # Energy filter summary - per capire se il problema è score o trend
        energy_keys = ['ENERGY_SCORE', 'ENERGY_TREND', 'ENERGY_BOTH']
        energy_summary = {}
        for ek in energy_keys:
            if ek in stats:
                s = stats[ek]
                total = s['would_win'] + s['would_lose']
                energy_summary[ek] = {
                    'blocked': s['blocked'],
                    'would_win': s['would_win'],
                    'would_lose': s['would_lose'],
                    'pnl_missed': round(s['pnl_missed'], 2),
                    'pnl_saved': round(s['pnl_saved'], 2),
                    'net': round(s['pnl_missed'] - s['pnl_saved'], 2),
                    'wr_simulated': round(s['would_win'] / total * 100, 1) if total > 0 else 0,
                }

        return {
            'total':       total_blocked,
            'protezione':  protezione,
            'zavorra':     zavorra,
            'pnl_saved':   round(pnl_saved, 2),
            'pnl_missed':  round(pnl_missed, 2),
            'bilancio':    round(pnl_saved - pnl_missed, 2),
            'verdetto':    verdetto,
            'per_livello': dict(stats),
            'energy_filter_summary': energy_summary,
            'log':         list(self._phantom_log),
            'open':        len(self._phantoms_open),
        }

    def _read_bridge_commands(self):
        """
        Legge comandi bridge da SQLite (key: bridge_commands) E da bridge_commands.json.
        Protocollo unificato — bridge nuovo scrive su DB, bridge vecchio su file.
        """
        try:
            commands = []
            # -- PROTOCOLLO NUOVO: legge da DB (bridge predittivo V48+) ----
            try:
                conn = _safe_connect(DB_PATH, timeout=30)
                rows = conn.execute(
                    "SELECT value FROM bot_state WHERE key='bridge_cmd'"
                ).fetchall()
                conn.close()
                if rows:
                    db_cmds = json.loads(rows[0][0])
                    # Bridge predittivo scrive oggetto singolo {type, data, ts}
                    # Normalizza a lista per compatibilità
                    if isinstance(db_cmds, dict):
                        db_cmds = [db_cmds]
                    if isinstance(db_cmds, list):
                        for cmd in db_cmds:
                            # Normalizza formato bridge predittivo → formato bot
                            if 'type' in cmd and 'data' in cmd and 'executed' not in cmd:
                                cmd['executed'] = False
                        commands.extend(db_cmds)
            except Exception:
                pass
            # -- PROTOCOLLO VECCHIO: legge da file (bridge legacy) ---------
            if os.path.exists(self._bridge_cmd_file):
                with open(self._bridge_cmd_file) as f:
                    file_cmds = json.load(f)
                    if isinstance(file_cmds, list):
                        commands.extend(file_cmds)

            modified = False
            for cmd in commands:
                if cmd.get("executed"):
                    continue

                cmd_type = cmd.get("type", "")
                data     = cmd.get("data", {})

                if cmd_type == "modify_weight":
                    param = data.get("param", "")
                    value = data.get("value")
                    # -- PARAMETRI PROTETTI - calibrati sui dati reali ------
                    # Il bridge NON può toccarli. Solo noi dopo analisi phantom.
                    # Solo i parametri fisici restano protetti
                    # I pesi SC sono gestiti dal Veritas — il bridge può agire
                    PROTECTED_PARAMS = {
                        "SOGLIA_BASE",   # auto-tune la gestisce
                        "SOGLIA_MIN",    # auto-tune la gestisce
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

                elif cmd_type == "entry_signal":
                    # Bridge predittivo segnala momento di entrata
                    carica = data.get("carica", 0.0)
                    motivo = data.get("motivo", "bridge")
                    self._log("🌉", f"BRIDGE entry_signal — carica={carica:.2f} {motivo}")
                    cmd["executed"] = True
                    modified = True

                elif cmd_type == "adjust_soglia":
                    param = data.get("param", "")
                    value = data.get("value")
                    # -- GUARDRAIL: SOGLIA_BASE è calibrata su 37,112 candele --
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

    # ========================================================================
    # ORDINI BINANCE (solo LIVE)
    # ========================================================================

    def _place_order(self, side: str, price: float, size_mult: float = 1.0):
        """
        🚨 PLACEHOLDER NON IMPLEMENTATO 🚨
        Fix Bug #1 (12mag2026): solleva NotImplementedError invece di
        mentire silenziosamente. Se chiamata in LIVE → bot crasha visibilmente.
        Per implementare: python-binance + API keys + test micro-size.
        """
        log.error(
            f"🚨 [ORDER_NOT_IMPLEMENTED] {side} {SYMBOL} @ {price:.2f} "
            f"size_mult={size_mult:.1f} — FUNZIONE NON IMPLEMENTATA"
        )
        raise NotImplementedError(
            f"_place_order è un placeholder. Implementare con python-binance prima di LIVE."
        )

    # ========================================================================
    # HEARTBEAT → app.py (Mission Control)
    # ========================================================================

    def _build_diagnosis(self) -> dict:
        """Catena causale completa — pronta per DeepSeek."""
        sc        = getattr(self, 'm2_score_components', {})
        score     = getattr(self, '_last_score', self.m2_last_score if hasattr(self,'m2_last_score') else 0)
        soglia    = getattr(self, '_last_soglia', self.m2_last_soglia if hasattr(self,'m2_last_soglia') else 48)
        regime    = self._regime_current
        direction = self.campo._direction if hasattr(self.campo, '_direction') else '?'

        warmup_rsi    = len(self.campo._prices_ta) if hasattr(self.campo, '_prices_ta') else 0
        fp_score      = sc.get('fp', 0)
        rsi_score     = sc.get('rsi', 0)
        seed_score    = sc.get('seed', 0)
        macd_score    = sc.get('macd', 0)
        gap           = round(soglia - score, 1) if score and soglia else None

        blocco = None
        motivo = None
        azione = None

        # Controlla gate post-soglia — quelli silenziosi
        oi_carica   = getattr(self, '_oi_carica', 0)
        oi_stato    = getattr(self, '_oi_stato', '')
        last_fp_wr  = getattr(self, '_last_fingerprint_wr', 0)
        fuoco_ok    = (oi_stato == 'FUOCO' and oi_carica >= 0.65)

        if warmup_rsi < 50:
            blocco = 'WARMUP_RSI_INCOMPLETO'
            motivo = (f"RSI buffer {warmup_rsi}/50 — contribuisce {rsi_score:.0f}/10 "
                      f"invece di 10 — perdo {10-rsi_score:.0f} punti score")
            azione = f"Attendere {50-warmup_rsi} campioni (~{max(1,(50-warmup_rsi)//10)} min)"

        elif gap is not None and gap <= 0 and fp_score == 0 and not fuoco_ok:
            # Score sopra soglia MA FP=0 e no FUOCO → gate post-soglia attivi
            blocco = 'GATE_POST_SOGLIA_FP_ZERO'
            motivo = (f"Score {score:.1f} > soglia {soglia:.1f} MA FP=0 e OI={oi_stato} carica={oi_carica:.2f}. "
                      f"MIDZONE/DRIFT bloccano silenziosamente perché _fuoco_ok=False (FP_WR basso)")
            azione = "Fix: _fuoco_ok non deve richiedere FP_WR — OI FUOCO è sufficiente"

        elif gap is not None and gap <= 0 and fp_score == 0 and fuoco_ok:
            # Score sopra soglia, FUOCO ok, FP=0 → dovrebbe entrare ma non lo fa
            blocco = 'GATE_SILENZIOSO_SCONOSCIUTO'
            motivo = (f"Score {score:.1f} > soglia {soglia:.1f}, OI FUOCO {oi_carica:.2f} — "
                      f"sistema non entra. Gate silenzioso non identificato.")
            azione = "Controllare MIDZONE, DRIFT DEBOLE, HARD GUARD nel log M2"

        elif fp_score == 0 and gap and gap > 0:
            blocco = 'FINGERPRINT_ZERO'
            motivo = (f"FP=0 — fingerprint {direction} senza real_samples. "
                      f"Score {score:.1f} vs soglia {soglia:.1f} gap={gap}")
            azione = "real_samples=5 già iniettati — verificare se regime è EXPLOSIVE"

        elif gap and gap > 0 and gap <= 8:
            blocco = 'SCORE_VICINO_SOGLIA'
            motivo = (f"Score {score:.1f} vs soglia {soglia:.1f} — gap={gap} punti. "
                      f"seed={seed_score:.1f} fp={fp_score:.1f} rsi={rsi_score:.1f} macd={macd_score:.1f}")
            azione = "In EXPLOSIVE il gap si chiude — attendere regime favorevole"

        elif regime == 'RANGING':
            blocco = 'RANGING_NO_EDGE'
            motivo = f"RANGING — score {score:.1f} correttamente sotto soglia. Mercato senza direzionalità."
            azione = "Attendere EXPLOSIVE o TRENDING_BULL"

        # Veritas: chi sbaglia sistematicamente
        veritas_err = None
        if hasattr(self, 'veritas'):
            for k, s in self.veritas._stats.items():
                if s.get('verdetto') == 'SBAGLIATO' and s.get('n',0) > 200 and s.get('pnl_avg',0) < -1.5:
                    veritas_err = {'chiave': k, 'n': s['n'],
                                   'pnl_avg': round(s['pnl_avg'],2),
                                   'diagnosi': f"SC sistematicamente sbagliato in {k}"}
                    break

        # Phantom: bilancio economico decisioni
        phantom_summary = None
        if hasattr(self, '_phantom_per_livello'):
            missed  = sum(v.get('pnl_missed',0) for v in self._phantom_per_livello.values())
            saved   = sum(v.get('pnl_saved',0)  for v in self._phantom_per_livello.values())
            phantom_summary = {
                'mancati':  round(missed,0),
                'salvati':  round(saved,0),
                'bilancio': round(saved-missed,0),
                'alert':    'TROPPO DIFENSIVO' if missed > saved*0.25 else 'OK'
            }

        # Controlla errori critici nel log M2 — visibili a DeepSeek
        m2_log = getattr(self, '_m2_log', [])
        errori_recenti = [l for l in list(m2_log)[-20:] if 'ERRORE' in str(l) or 'ERROR' in str(l)]
        if errori_recenti:
            blocco = 'CRASH_M2'
            motivo = f"CRASH in _evaluate_shadow_entry: {errori_recenti[-1]}"
            azione = "STOP — sistema in crash, non valuta entry. Deploy immediato richiesto."

        return {
            'blocco':           blocco,
            'motivo':           motivo,
            'azione':           azione,
            'score':            round(score,1),
            'soglia':           round(soglia,1),
            'gap':              gap,
            'regime':           regime,
            'direction':        direction,
            'warmup_rsi':       f"{warmup_rsi}/50",
            'warmup_pronto':    warmup_rsi >= 50,
            'veritas_errore':   veritas_err,
            'phantom':          phantom_summary,
            'pronto_entry':     warmup_rsi >= 50 and (not gap or gap <= 0),
            'crash_attivo':     len(errori_recenti) > 0,
            'errori_recenti':   errori_recenti[-3:] if errori_recenti else [],
        }


    def _update_heartbeat(self):
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                # ─── PATCH 10MAG: scritture singole protette ────────────
                # Prima era un .update({...}) monolitico: se UNA chiave crashava,
                # TUTTE le altre saltavano (phantom, telemetry, m2_*, ecc. spariscono
                # dall'heartbeat). Ora ogni chiave è isolata: se una crasha, logga
                # [HB_KEY:nome] {errore}, le altre passano lo stesso.
                # Volpe non mucca: zero refactor, zero numeri arbitrari, solo robustezza.
                def _hb_set(key, fn):
                    try:
                        self.heartbeat_data[key] = fn()
                    except Exception as _e:
                        log.error(f"[HB_KEY:{key}] {_e}")

                tot = self.wins + self.losses

                _hb_set("status",              lambda: "RUNNING")
                _hb_set("mode",                lambda: "PAPER" if self.paper_trade else "LIVE")
                _hb_set("capital",             lambda: round(self.capital, 2))
                _hb_set("trades",              lambda: self.total_trades)
                _hb_set("wins",                lambda: self.wins)
                _hb_set("losses",              lambda: self.losses)
                _hb_set("wr",                  lambda: round(self.wins / tot, 4) if tot > 0 else 0)
                _hb_set("last_seen",           lambda: datetime.utcnow().isoformat())
                _hb_set("cromo_blocchi",       lambda: dict(getattr(self, "_cromo_blocchi", {"vol_basso":0,"vol_isterico":0,"comp_alta":0,"cdur_breve":0,"totale":0})))
                _hb_set("ritardo_stats",       lambda: dict(getattr(self, "_ritardo_stats", {"sec":0,"agganciati":0,"entrati":0})))
                _hb_set("matrimoni_divorzio",  lambda: list(self.memoria.divorzio))
                _hb_set("oracolo_snapshot",    lambda: self.oracolo.dump())
                _hb_set("posizione_aperta",    lambda: self.trade_open is not None)
                _hb_set("live_log",            lambda: list(self._live_log))
                _hb_set("calibra_params",      lambda: self.calibratore.get_params())
                _hb_set("calibra_log",         lambda: self.calibratore.get_log())
                _hb_set("regime",              lambda: self._regime_current)
                _hb_set("regime_conf",         lambda: round(self._regime_conf, 3))
                # -- MOTORE 2: CAMPO GRAVITAZIONALE stats ----------
                _hb_set("m2_trades",           lambda: self._m2_trades)
                _hb_set("m2_wins",             lambda: self._m2_wins)
                _hb_set("m2_losses",           lambda: self._m2_losses)
                _hb_set("m2_wr",               lambda: round(self._m2_wins / max(1, self._m2_wins + self._m2_losses), 4))
                _hb_set("m2_pnl",              lambda: round(self._m2_pnl, 4))
                _hb_set("m2_shadow_open",      lambda: self._shadow is not None)
                _hb_set("m2_direction",        lambda: self.campo._direction)
                _hb_set("m2_entry_price",      lambda: round(self._shadow["price_entry"], 4) if self._shadow else 0)
                _hb_set("m2_state",            lambda: self._state)
                _hb_set("m2_loss_streak",      lambda: self._m2_loss_streak)
                _hb_set("m2_cooldown",         lambda: max(0, self._m2_cooldown_until - time.time()))
                _hb_set("m2_log",              lambda: list(self._m2_log))
                _hb_set("m2_campo_stats",      lambda: self.campo.get_stats())
                # ─── PASSO 10 — Prediction Tracker (sola lettura, diagnostica) ───
                _hb_set("pt_stats",            lambda: self._pt_get_stats())
                _hb_set("m2_last_score",       lambda: round(getattr(self.campo, '_last_score', 0), 1))
                _hb_set("m2_last_soglia",      lambda: round(getattr(self.campo, '_last_soglia', 60), 1))
                # gf_drift VIVO (20giu, Roberto): la dashboard mostrava gf_drift
                # MORTO (0.000), aggiornato solo dentro il guardiano GF_DIREZIONE.
                # Qui lo calcolo dal drift VERO (_prices_long) a OGNI heartbeat,
                # cosi' il pannello vede il mercato muoversi davvero.
                def _drift_vivo():
                    try:
                        _pl = list(getattr(self.campo, "_prices_long", []))
                        if len(_pl) >= 100:
                            _o = sum(_pl[:50]) / 50
                            _n = sum(_pl[-50:]) / 50
                            return round((_n - _o) / _o * 100, 3) if _o else 0.0
                    except Exception:
                        pass
                    return 0.0
                _hb_set("gf_drift",            _drift_vivo)
                # gf_stato VIVO (20giu, Roberto): lo stato LIBERO/FUORI si
                # scriveva SOLO dentro _evaluate_shadow_entry (cioe' solo se
                # c'era un aggancio). Senza agganci restava "—" per sempre,
                # anche con drift positivo. Qui lo calcolo a OGNI heartbeat dal
                # drift vivo, cosi' la dashboard mostra sempre lo stato vero:
                # LIBERO (drift sopra soglia, direzione ok) / FUORI_SHORT (drift
                # sotto soglia, ribassista) / PIATTO (vicino a zero).
                def _stato_vivo():
                    try:
                        _d = _drift_vivo()
                        _soglia = float(os.environ.get("GF_DRIFT_SOGLIA", "-0.05"))
                        if _d < _soglia:
                            return "FUORI_SHORT"
                        return "LIBERO"
                    except Exception:
                        return "—"
                _hb_set("gf_stato",            _stato_vivo)
                _hb_set("m2_buy_distance",     lambda: round(getattr(self.campo, '_last_soglia', 60) - getattr(self.campo, '_last_score', 0), 1))
                _hb_set("m2_score_components", lambda: {
                    "seed":   round(min(1.0,max(0.0,(self.seed_scorer.score().get('score',0)-0.20)/0.60))*25, 1),
                    "fp":     round(getattr(self.campo, '_last_fp_score', 0), 1),
                    "rsi":    round(self.campo._rsi_score()*10, 1),
                    "macd":   round(self.campo._macd_score()*10, 1),
                    "regime": self._regime_current,
                    "warmup_rsi": len(self.campo._prices_ta),
                    "warmup_needed": max(0, 50 - len(self.campo._prices_ta)),
                })
                _hb_set("oi_stato",            lambda: self._oi_stato)
                _hb_set("oi_carica",           lambda: round(self._oi_carica, 3))
                _hb_set("veritas",             lambda: self.veritas.dump_dashboard())
                # -- PHANTOM TRACKER - zavorra o protezione? -------
                _hb_set("phantom",             lambda: self._get_phantom_summary())
                # -- INTELLIGENZA AUTONOMA - capsule vive -----------
                _hb_set("ia_stats",            lambda: (self.capsule_manager.get_stats()
                                                       if self.capsule_manager
                                                       else self.realtime_engine.get_stats()))
                _hb_set("ce_stats",            lambda: (self.capsule_executor.get_dashboard_data()
                                                       if self.capsule_executor else {}))
                # -- PRE-TRADE SIGNAL TRACKER ---------------------------
                _hb_set("signal_tracker",      lambda: {
                    "open":      self.signal_tracker.get_open_count(),
                    "closed":    len(self.signal_tracker._closed),
                    "top":       self.signal_tracker.dump_top(8),
                    "stats_keys": list(self.signal_tracker._stats.keys()),
                    "stats_n":   {k: v['n'] for k,v in self.signal_tracker._stats.items()},
                })
                # -- SOGLIA DINAMICA MONITOR -----------------------
                _hb_set("m2_soglia_min",       lambda: self.campo.SOGLIA_MIN)
                _hb_set("m2_soglia_base",      lambda: self.campo.SOGLIA_BASE)
                _hb_set("diagnosis",           lambda: self._build_diagnosis())
                # -- SHORT EVITATI IN RANGING ----------------------
                _hb_set("shadow_short_ranging", lambda: self._get_shadow_short_report())
                # -- DATI GRANULARI PER BRIDGE (B3) -------------
                # -- HEARTBEAT ENRICHED telemetria --------
                _hb_set("bridge_feed",         lambda: {
                    "drift_history":   list(self.campo._prices_long)[-20:] and [
                        round((list(self.campo._prices_long)[-20:][i+1] - list(self.campo._prices_long)[-20:][i]) /
                              max(list(self.campo._prices_long)[-20:][i], 1) * 100, 4)
                        for i in range(len(list(self.campo._prices_long)[-20:])-1)
                    ] if len(self.campo._prices_long) >= 20 else [],
                    "compression_now": round((max(list(self.campo._prices_short)[-5:]) - min(list(self.campo._prices_short)[-5:])) /
                                              max(max(list(self.campo._prices_short)[-20:]) - min(list(self.campo._prices_short)[-20:]), 0.01), 4)
                                       if len(self.campo._prices_short) >= 20 else 1.0,
                    "seed_history":    list(self.campo._seed_history),
                    "oi_carica":       round(self._oi_carica, 3),
                    "oi_stato":        self._oi_stato,
                    "regime":          self._regime_current,
                    "rsi":             round(self.campo._last_rsi, 1),
                    "macd_hist":       round(self.campo._last_macd_hist, 4),
                    "pb_signals":      self.campo._pre_breakout_factor()[2] if len(self.campo._prices_short) >= 30 else 0,
                })
                # -- STABILITY TELEMETRY ------------------------
                _hb_set("telemetry",           lambda: self.telemetry.generate_report())
        except Exception as e:
            log.error(f"[HEARTBEAT_ERROR] {e}")
        finally:
            if self.heartbeat_lock:
                
                # ── Phantom Supervisor log ──────────────────────────────
                _sup_log = getattr(self, '_phantom_sup_log', [])
                self.heartbeat_data["phantom_sup_log"] = _sup_log[-10:]

                # ── V16 data ────────────────────────────────────────────
                if _V16_ENGINES_OK:
                    if self._nerv:
                        self.heartbeat_data["nervosismo"] = round(self._nerv._nervosismo, 3)
                        self.heartbeat_data["gomme"]      = self._nerv._gomme_attuale
                    if self._comparto:
                        self.heartbeat_data["comparto"]   = self._comparto._attivo
                        try:
                            _sw_log = getattr(self._comparto, '_switch_log', [])
                            self.heartbeat_data["switch_log"] = list(_sw_log)[-10:]
                        except Exception:
                            self.heartbeat_data["switch_log"] = []
                        try:
                            _tutti = getattr(self._comparto, '_comparti_stats', {})
                            if not _tutti and hasattr(self._comparto, '_attivo'):
                                _nomi = ['DIFENSIVO','NEUTRO','ATTACCO','TRENDING_BULL','TRENDING_BEAR']
                                _tutti = {n: {'attivo': n == self._comparto._attivo} for n in _nomi}
                            self.heartbeat_data["comparti_tutti"] = _tutti
                        except Exception:
                            self.heartbeat_data["comparti_tutti"] = {}
                    if self._breath:
                        self.heartbeat_data["breath"] = {
                            "fase":    self._breath._fase,
                            "energia": round(self._breath._energia, 2),
                        }

                # ── CapsuleIntelligente — stato predittivo live ──────────
                try:
                    _ci_dash = self.ci.get_status_dashboard()
                    self.heartbeat_data["ci_stato"]   = _ci_dash["stato"]
                    self.heartbeat_data["ci_capsule"] = _ci_dash["capsule_attive"]
                    self.heartbeat_data["ci_storia"]  = _ci_dash["storia"]
                    self.heartbeat_data["ci_n"]       = _ci_dash["n_capsule"]
                except Exception:
                    pass

                # ── ia_capsule_attive — lista capsule vive per dashboard ──
                # Include: CapsuleManager + capsule_ragionatore (RA) + CI attive
                try:
                    _caps_all = []
                    _now_ts   = time.time()

                    # 1. CapsuleManager (file JSON o get_active_capsules)
                    _cm = self.capsule_manager
                    if _cm and hasattr(_cm, 'get_active_capsules'):
                        _caps_all += _cm.get_active_capsules()
                    elif _cm and hasattr(_cm, 'capsule_file'):
                        import json as _json
                        if os.path.exists(_cm.capsule_file):
                            with open(_cm.capsule_file) as _f:
                                _cj = _json.load(_f)
                            _caps_all += [
                                {
                                    'id':          c.get('id', c.get('capsule_id', '?')),
                                    'tipo':        c.get('tipo', c.get('type', 'CM')),
                                    'ttl_seconds': max(0, int(c.get('scade_ts', _now_ts) - _now_ts))
                                                   if c.get('scade_ts') else 0,
                                    'enabled':     c.get('enabled', True),
                                    'fonte':       'CM',
                                }
                                for c in _cj if c.get('enabled', True)
                            ]

                    # 2. Capsule Ragionatore (RA) — tutte, nessun limite
                    _caps_ra_hb = self.heartbeat_data.get('capsule_ragionatore', [])
                    _ids_gia    = {c.get('id') for c in _caps_all}
                    for _c in _caps_ra_hb:
                        _cid  = _c.get('id', '')
                        if not _cid or _cid in _ids_gia:
                            continue
                        # Calcola TTL
                        _fonte = _c.get('fonte', '')
                        if _fonte == 'PERMANENTE_DB':
                            _ttl = 86400  # permanenti: 24h
                        else:
                            try:
                                from datetime import datetime as _dt3
                                _ts_cap = _dt3.fromisoformat(_c.get('ts', '')).timestamp()
                                _ttl = max(0, int(_c.get('vita', 600) - (_now_ts - _ts_cap)))
                            except Exception:
                                _ttl = _c.get('vita', 600)
                        if _fonte == 'PERMANENTE_DB' or _ttl > 0:
                            _caps_all.append({
                                'id':          _cid,
                                'tipo':        f"RA_{_c.get('azione','?')[:8]}",
                                'ttl_seconds': _ttl,
                                'enabled':     True,
                                'fonte':       _fonte or 'RA',
                                'motivo':      _c.get('motivo', '')[:60],
                                'forza':       _c.get('forza', 0.65),
                            })
                            _ids_gia.add(_cid)

                    # 3. CI capsule attive
                    try:
                        _ci_dash = self.ci.get_dashboard()
                        for _c in _ci_dash.get('capsule_attive', []):
                            _cid = _c.get('id', '')
                            if _cid and _cid not in _ids_gia:
                                _caps_all.append({
                                    'id':          _cid,
                                    'tipo':        'CI_' + _c.get('tipo', '?')[:6],
                                    'ttl_seconds': _c.get('vita', 0),
                                    'enabled':     True,
                                    'fonte':       'CI',
                                })
                    except Exception:
                        pass

                    self.heartbeat_data["ia_capsule_attive"] = _caps_all
                except Exception:
                    self.heartbeat_data["ia_capsule_attive"] = []

            # ── ORACLE TRIGGER — event-driven anomaly detection ─────────
            # Scrive oracle_trigger nel heartbeat quando rileva un'anomalia.
            # Oracle Auto legge questo campo ogni 30s e si sveglia solo se non vuoto.
            try:
                _ot = None
                _loss_streak = self._m2_loss_streak
                _trades      = self._m2_trades
                _pnl         = self._m2_pnl
                _regime      = self._regime_current
                _oi_stato    = self._oi_stato
                _oi_carica   = self._oi_carica
                _soglia      = self.campo.SOGLIA_BASE
                _state       = self._state
                _hc          = getattr(self, '_hardening_cycles', {})
                _last_trade_ts = getattr(self, '_last_shadow_close_ts', 0)
                _now_ts      = time.time()

                # P1: Loss streak >= 2 — priorità massima
                if _loss_streak >= 2 and not _ot:
                    _ot = f"LOSS_STREAK_{_loss_streak}"

                # P2: OI FUOCO ma 0 trade da più di 15 minuti
                elif _oi_stato == "FUOCO" and _oi_carica >= 0.70 and _trades == 0 and not _ot:
                    if _now_ts - getattr(self, '_boot_time', _now_ts) > 900:
                        _ot = f"OI_FUOCO_ZERO_TRADE_carica{_oi_carica:.2f}"

                # P3: Phantom Supervisor sta irrigidendo (cicli >= 3)
                elif any(v >= 3 for v in _hc.values()) and not _ot:
                    _max_cicli = max(_hc.values()) if _hc else 0
                    _ot = f"PHANTOM_IRRIGIDISCE_cicli{_max_cicli}"

                # P4: PnL sessione negativo oltre -$15
                elif _pnl < -15.0 and not _ot:
                    _ot = f"PNL_DRAWDOWN_{abs(_pnl):.0f}usd"

                # P5: Regime EXPLOSIVE ma 0 trade
                elif _regime == "EXPLOSIVE" and _trades == 0 and not _ot:
                    if _now_ts - getattr(self, '_boot_time', _now_ts) > 300:
                        _ot = "EXPLOSIVE_ZERO_TRADE"

                # P6: Stato DIFENSIVO da più di 10 minuti senza trade
                elif _state == "DIFENSIVO" and _trades == 0 and not _ot:
                    _state_since = getattr(self, '_state_since', _now_ts)
                    if _now_ts - _state_since > 600:
                        _ot = f"DIFENSIVO_BLOCCATO_{int((_now_ts-_state_since)/60)}min"

                # Scrivi trigger (o cancella se nessuna anomalia)
                if _ot:
                    _existing = self.heartbeat_data.get('oracle_trigger', '')
                    # Non sovrascrivere se stesso trigger — evita loop
                    if _ot != _existing:
                        self.heartbeat_data['oracle_trigger'] = _ot
                        log.info(f"[ORACLE_TRIGGER] 🔔 {_ot}")
                else:
                    self.heartbeat_data['oracle_trigger'] = ''

            except Exception as _ote:
                log.warning(f"[ORACLE_TRIGGER] error: {_ote}")

            self.heartbeat_lock.release()

    def _get_shadow_short_report(self):
        """Report aggregato degli SHORT evitati in RANGING."""
        results = list(getattr(self, '_shadow_short_results', []))
        log_entries = getattr(self, '_shadow_short_log', [])
        
        if not results:
            return {
                'blocked_count': len(log_entries),
                'simulated_count': 0,
                'message': 'Nessun phantom SHORT chiuso ancora',
                'recent_blocked': log_entries[-5:],
            }
        
        wins = [r for r in results if r['is_win']]
        losses = [r for r in results if not r['is_win']]
        total_pnl = sum(r['pnl'] for r in results)
        
        report = {
            'blocked_count': len(log_entries),
            'simulated_count': len(results),
            'would_win': len(wins),
            'would_lose': len(losses),
            'wr_simulated': round(len(wins)/len(results)*100, 1) if results else 0,
            'pnl_total': round(total_pnl, 2),
            'avg_pnl': round(total_pnl / len(results), 2) if results else 0,
            'avg_duration': round(sum(r['duration'] for r in results) / len(results), 1) if results else 0,
            'verdict': 'EDGE' if total_pnl > 0 else 'RUMORE',
            'recent_results': results[-5:],
            'recent_blocked': log_entries[-5:],
        }
        
        by_energy = {}
        for r in results:
            be = r.get('bearish_energy', 0)
            key = f"{be}"
            if key not in by_energy:
                by_energy[key] = {'count': 0, 'pnl': 0, 'wins': 0}
            by_energy[key]['count'] += 1
            by_energy[key]['pnl'] += r['pnl']
            if r['is_win']:
                by_energy[key]['wins'] += 1
        report['by_bearish_energy'] = by_energy
        
        return report

    # ========================================================================
    # RUN
    # ========================================================================

    def _loss_consecutivi(self) -> int:
        """Conta i loss consecutivi dalla coda del log_analyzer."""
        count = 0
        for t in reversed(list(self.log_analyzer.trades)):
            if t.get('pnl', 0) < 0:
                count += 1
            else:
                break
        return count

    def _read_deepseek_commands(self):
        """Legge e applica i comandi DeepSeek da heartbeat_data ogni tick."""
        if not self.heartbeat_data:
            return
        try:
            # heartbeat_lock potrebbe essere None se non inizializzato — usa fallback
            if self.heartbeat_lock is not None:
                with self.heartbeat_lock:
                    hb = dict(self.heartbeat_data)
            else:
                hb = dict(self.heartbeat_data)

            now = time.time()

            # ABBASSA_SOGLIA — valida per 60 secondi
            if hb.get("ds_soglia_override"):
                ts = hb.get("ds_soglia_ts", 0)
                if now - ts < 60:
                    val = float(hb["ds_soglia_override"])
                    self.campo._soglia_min_override = val
                    self.campo._soglia_base_override = val
                else:
                    with self.heartbeat_lock:
                        self.heartbeat_data.pop("ds_soglia_override", None)
                        self.heartbeat_data.pop("ds_soglia_ts", None)
                    self.campo._soglia_min_override = None
                    self.campo._soglia_base_override = None

            # RESET_PESI
            if hb.get("ds_reset_pesi"):
                self.supercervello._pesi = dict(self.supercervello.PESI_DEFAULT)
                log.info("[DS] ✅ Pesi SC resettati ai default")
                with self.heartbeat_lock:
                    self.heartbeat_data["ds_reset_pesi"] = False

            # FORZA_ENTRY — valido per 30 secondi
            if hb.get("ds_forza_entry"):
                ts = hb.get("ds_forza_ts", 0)
                if now - ts < 30:
                    self._ds_forza_entry = True
                else:
                    with self.heartbeat_lock:
                        self.heartbeat_data["ds_forza_entry"] = False
                    self._ds_forza_entry = False

            # BLOCCA_SC — valido per 180 secondi (3 minuti)
            if hb.get("ds_blocca_sc"):
                ts = hb.get("ds_blocca_sc_ts", 0)
                if now - ts < 180:
                    self._ds_blocca_sc = True
                else:
                    with self.heartbeat_lock:
                        self.heartbeat_data["ds_blocca_sc"] = False
                    self._ds_blocca_sc = False

        except Exception as e:
            log.warning(f"[DS_CMD] Errore lettura comandi: {e}")


    def _watchdog_autorepair(self):
        """
        Watchdog autonomo — controlla e ripara errori senza fermare il bot.
        Gira ogni 30 secondi nel loop principale.
        """
        try:
            m2_log = list(getattr(self, '_m2_log', []))
            errori = [l for l in m2_log[-10:] if 'ERRORE' in str(l)]
            if not errori:
                return

            ultimo_errore = errori[-1]
            log.warning(f"[WATCHDOG] Errore rilevato: {ultimo_errore}")

            # REPAIR 1: _fuoco_ok non definita → reset closure
            if '_fuoco_ok' in ultimo_errore:
                # Forza reset dello stato interno per far ricreare la closure
                self._last_entry_tick = 0
                log.info("[WATCHDOG] ✅ REPAIR: reset _last_entry_tick per forzare nuova closure")

            # REPAIR 2: heartbeat_lock None → ricrea il lock
            if 'heartbeat_lock' in ultimo_errore or 'NoneType' in ultimo_errore:
                import threading
                if self.heartbeat_lock is None:
                    self.heartbeat_lock = threading.RLock()
                    log.info("[WATCHDOG] ✅ REPAIR: heartbeat_lock ricreato")

            # REPAIR 3: shadow corrotto → chiudi posizione aperta
            if 'shadow' in ultimo_errore.lower() and self._shadow:
                self._shadow = None
                self._shadow_entry_time = None
                log.info("[WATCHDOG] ✅ REPAIR: shadow position resettata")

            # REPAIR 4: errore generico → reset stato entry
            if errori and len(errori) >= 3:
                # 3+ errori consecutivi → reset completo stato entry
                self._last_entry_tick = 0
                self._m2_loss_consecutivi_cache = 0
                if hasattr(self, '_phantoms_open'):
                    self._phantoms_open.clear()
                log.info("[WATCHDOG] ✅ REPAIR: reset completo stato entry dopo 3+ errori")

            # Svuota gli errori dal log per evitare loop infiniti
            if hasattr(self, '_m2_log'):
                # FIX 18giu (debito #4 analisi esterna): copia difensiva PRIMA
                # di iterare. _m2_log e' una deque scritta dal thread del tick
                # (_log_m2); iterarla qui mentre l'altro thread ci scrive puo'
                # dare RuntimeError: deque mutated during iteration. list() e'
                # atomica in CPython -> snapshot sicuro, poi filtro lo snapshot.
                _snap_m2 = list(self._m2_log)
                cleaned = [l for l in _snap_m2 if 'ERRORE' not in str(l)]
                self._m2_log = type(self._m2_log)(cleaned, maxlen=getattr(self._m2_log, 'maxlen', 200))

        except Exception as e:
            log.error(f"[WATCHDOG] Errore nel watchdog stesso: {e}")

    def _carica_capsule_permanenti(self):
        """Al boot carica le capsule permanenti dal DB."""
        try:
            conn = _safe_connect(DB_PATH, timeout=30)
            rows = conn.execute("""
                SELECT id, azione, params_json, motivo, forza, creata_ts,
                       analisi_causale, prompt_contestuale
                FROM capsule_permanenti ORDER BY creata_ts DESC
            """).fetchall()
            conn.close()
            if not rows:
                return
            import json as _j
            caps = []
            for r in rows:
                try: params = _j.loads(r[2]) if r[2] else {}
                except Exception: params = {}
                caps.append({
                    'id': r[0], 'azione': r[1], 'params': params,
                    'motivo': r[3] or '', 'forza': float(r[4] or 0.55),
                    'ts': r[5] or '', 'vita': 86400, 'fonte': 'PERMANENTE_DB',
                    'analisi_causale': r[6] or '',
                    'prompt_contestuale': r[7] or '',
                })
            if self.heartbeat_data is not None:
                with self.heartbeat_lock:
                    existing = self.heartbeat_data.get('capsule_ragionatore', [])
                    ids_ex = {c.get('id') for c in existing}
                    for c in caps:
                        if c['id'] not in ids_ex:
                            existing.append(c)
                    self.heartbeat_data['capsule_ragionatore'] = existing
            log.info(f"[BOOT] Caricate {len(caps)} capsule permanenti dal DB")
        except Exception as e:
            log.error(f"[BOOT_CAPSULE] {e}")

    def _salva_capsula_permanente(self, capsula: dict):
        """Salva una capsula nel DB come permanente."""
        try:
            import json as _j
            conn = _safe_connect(DB_PATH, timeout=30)
            conn.execute("""
                INSERT OR REPLACE INTO capsule_permanenti
                (id, azione, params_json, motivo, forza, contesto, creata_ts,
                 analisi_causale, prompt_contestuale, n_attivazioni)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                    (SELECT n_attivazioni+1 FROM capsule_permanenti WHERE id=?), 1
                ))
            """, (
                capsula.get('id'), capsula.get('azione'),
                _j.dumps(capsula.get('params', {})),
                capsula.get('motivo', '')[:200],
                float(capsula.get('forza', 0.55)),
                capsula.get('contesto', ''), capsula.get('ts', ''),
                capsula.get('analisi_causale', '')[:500],
                capsula.get('prompt_contestuale', '')[:1000],
                capsula.get('id'),
            ))
            conn.commit()
            conn.close()
            log.info(f"[CAPSULA_PERM] Salvata: {capsula.get('id')} {capsula.get('azione')}")
        except Exception as e:
            log.error(f"[CAPSULA_PERM_SAVE] {e}")

    # ════════════════════════════════════════════════════════════════════
    # PASSO A — REGOLA ANTIAEREA (29mag, Roberto)
    # ════════════════════════════════════════════════════════════════════
    # Un blocco permanente protegge una firma tossica. Ma se il mercato
    # cambia e quella firma torna a vincere, il blocco deve cadere — altrimenti
    # è prigione perpetua (il "loop vizioso" che prima si evitava con l'amnesia).
    # REGOLA: 2 WIN CONSECUTIVI della STESSA firma cancellano il blocco.
    #   - perché 2 e non 1: in RANGING un singolo win può essere rumore/fortuna;
    #     due di fila sulla stessa firma è molto meno probabile sia caso.
    #   - un LOSS della firma azzera il contatore (deve essere consecutivi).
    # NOTA PASSO B (futuro): "win" qui usa is_win del simulatore. Quando i dati
    # MFE/MAE saranno maturi, sostituire con "win = MFE reale >= soglia fee".
    SOGLIA_WIN_PULIZIA = 2  # win consecutivi stessa firma per pulire un blocco

    def _pulisci_blocco_se_win(self, firma: str, is_win: bool):
        """Regola antiaerea: 2 win consecutivi stessa firma → cancella blocco."""
        if not hasattr(self, '_aa_win_consecutivi'):
            self._aa_win_consecutivi = {}

        if not is_win:
            # Loss → azzera il contatore di QUESTA firma (devono essere consecutivi)
            self._aa_win_consecutivi[firma] = 0
            return

        # Win → incrementa il contatore della firma
        _n = self._aa_win_consecutivi.get(firma, 0) + 1
        self._aa_win_consecutivi[firma] = _n

        if _n < self.SOGLIA_WIN_PULIZIA:
            return  # non ancora abbastanza win consecutivi

        # ── Soglia raggiunta: cerca e cancella blocchi permanenti di questa firma ──
        try:
            import json as _j
            _mom, _vol, _tr, _reg, _dir = (firma.split('|') + ['', '', '', '', ''])[:5]
            conn = _safe_connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            _rows = conn.execute("""
                SELECT id, azione, params_json, contesto FROM capsule_permanenti
                WHERE azione IN ('blocca_entry','BLOCCA_ENTRY','BLOCCA_CONTESTO')
            """).fetchall()
            _puliti = []
            for _r in _rows:
                # Matcha la firma: i blocchi salvano la firma nei params o nel contesto
                try:
                    _p = _j.loads(_r['params_json']) if _r['params_json'] else {}
                except Exception:
                    _p = {}
                # Match se momentum|volatility|trend coincidono (le 3 dimensioni
                # sempre presenti nei blocchi CTX_TOSSICO) ed eventualmente dir/regime
                _match = (
                    str(_p.get('momentum', '')) == _mom and
                    str(_p.get('volatility', '')) == _vol and
                    str(_p.get('trend', '')) == _tr
                ) or (f"{_mom}|{_vol}|{_tr}" in str(_r['contesto'] or ''))
                if _match:
                    conn.execute("DELETE FROM capsule_permanenti WHERE id=?", (_r['id'],))
                    _puliti.append(_r['id'])
                    # Rimuovi anche dalla RAM (capsule_ragionatore + CI attiva)
                    try:
                        if self.heartbeat_data is not None:
                            _cr = self.heartbeat_data.get('capsule_ragionatore', [])
                            self.heartbeat_data['capsule_ragionatore'] = [
                                c for c in _cr if c.get('id') != _r['id']
                            ]
                        if hasattr(self, 'ci') and _r['id'] in getattr(self.ci, '_capsule_attive', {}):
                            self.ci._capsule_attive.pop(_r['id'], None)
                    except Exception:
                        pass
            conn.commit()
            conn.close()
            if _puliti:
                self._aa_win_consecutivi[firma] = 0  # reset dopo pulizia
                log.info(f"[ANTIAEREA] 🧹 firma {firma} → {self.SOGLIA_WIN_PULIZIA} WIN consecutivi: "
                         f"PULITI {len(_puliti)} blocchi {_puliti}")
        except Exception as e:
            log.error(f"[ANTIAEREA_PULIZIA] {e}")


    def _carica_storia_dal_db(self):
        """Al boot resetta narratore — non eredita storia di sessioni precedenti.
        
        FIX 10 MAG 2026 (Roberto): il narratore alimenta DeepSeek (oracle_auto.py)
        con la storia recente. Caricare 20 trade vecchi dal DB significava
        raccontare a DeepSeek pattern di V15 come se fossero attuali, generando
        capsule LEARNED basate su un sistema che non esiste piu'.
        
        Veritas/CapsuleManager mantengono memoria storica via altre tabelle
        (veritas_stats, capsule, _m2_ctx_stats). Il narratore racconta SOLO
        i trade della sessione attiva, accumulati naturalmente in run-time.
        """
        try:
            if self.heartbeat_data is not None:
                with self.heartbeat_lock:
                    self.heartbeat_data['narratore_trade_storia'] = []
                    self.heartbeat_data['narratore_trade_stats'] = {
                        'n': 0,
                        'wins': 0,
                        'pnl_tot': 0.0,
                        'last_context': '',
                        'consecutive_losses': 0,
                        'wr': 0.0,
                    }
            log.info("[BOOT] Narratore VERGINE - racconterà solo i trade di questa sessione")
        except Exception as e:
            log.error(f"[BOOT_STORIA] {e}")

    def run(self):
        log.info("[START] Bot avviato - connessione Binance WS...")
        self._boot_time = time.time()
        self._carica_storia_dal_db()
        self._carica_capsule_permanenti()
        self.connect_binance()
        _watchdog_last = time.time()
        try:
            while True:
                time.sleep(1)
                self._read_deepseek_commands()
                # Watchdog ogni 30 secondi
                if time.time() - _watchdog_last >= 30:
                    self._watchdog_autorepair()
                    _watchdog_last = time.time()
        except KeyboardInterrupt:
            log.info("[STOP] Bot fermato da utente")
            self._persist.save(self.capital, self.total_trades)

# ===========================================================================
# MAIN (standalone - Render lo avvia tramite bot_launcher.py)
# ===========================================================================

if __name__ == '__main__':
    bot = OvertopBassanoV16Production()
    bot.run()
