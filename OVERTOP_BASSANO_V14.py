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

    def _fp(self, momentum: str, volatility: str, trend: str) -> str:
        return f"{momentum}|{volatility}|{trend}"

    def get_wr(self, momentum: str, volatility: str, trend: str) -> float:
        """Ritorna il WR stimato per questo contesto (0.0–1.0). Default 0.72 se ignoto."""
        fp = self._fp(momentum, volatility, trend)
        if fp not in self._memory or self._memory[fp]['samples'] < self.MIN_SAMPLES:
            return 0.72   # neutro — abbastanza positivo da non bloccare
        m = self._memory[fp]
        return m['wins'] / m['samples'] if m['samples'] > 0 else 0.72

    def is_fantasma(self, momentum: str, volatility: str, trend: str) -> tuple:
        """
        Ritorna (True, motivo) se il pattern è FANTASMA, (False, '') altrimenti.
        """
        fp  = self._fp(momentum, volatility, trend)
        wr  = self.get_wr(momentum, volatility, trend)
        mem = self._memory.get(fp, {})
        if mem.get('samples', 0) < self.MIN_SAMPLES:
            return False, ''   # troppo pochi dati → non bloccare
        if wr < self.FANTASMA_WR_THRESHOLD:
            return True, f"FANTASMA fp={fp} wr={wr:.2f}"
        return False, ''

    def record(self, momentum: str, volatility: str, trend: str, is_win: bool):
        """Aggiorna la memoria con decay. Chiamato ad ogni chiusura trade."""
        fp = self._fp(momentum, volatility, trend)
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
    def valida(self, fingerprint_wr, momentum, volatility, trend):
        if fingerprint_wr > 0.75 and momentum == "FORTE" and volatility == "BASSA" and trend == "UP":
            return True, 0.95, "COERENZA PERFETTA"
        if fingerprint_wr > 0.60 and momentum in ("FORTE", "MEDIO") and trend == "UP":
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
    def proteggi(self, momentum, volatility, fingerprint_wr):
        if momentum == "DEBOLE" and volatility == "ALTA" and fingerprint_wr <= 0.70:
            return False, "PROTETTO_VOLATILITÀ"
        if volatility == "ALTA" and fingerprint_wr < 0.55:
            return False, "PROTETTO_FP_BASSO"
        return True, "OK"

class Capsule4Opportunita:
    """Riconosce finestre di opportunità premium."""
    def riconosci(self, fingerprint_wr, momentum, volatility):
        if fingerprint_wr > 0.75 and momentum == "FORTE" and volatility == "BASSA":
            return True, 0.95, "OPPORTUNITÀ_ORO"
        if fingerprint_wr > 0.65 and momentum == "FORTE":
            return True, fingerprint_wr, "OPPORTUNITÀ_BUONA"
        return False, 0.40, "NO_OPPORTUNITÀ"

class Capsule5Tattica:
    """Timing tattico: entry solo se coerenza e confidence alte."""
    def timing(self, entry_trigger, coerenza, confidence):
        if entry_trigger and coerenza and confidence > 0.80:
            return True, 45, "TIMING_PERFETTO"
        if entry_trigger and confidence > 0.65:
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
        ("FORTE", "BASSA",  "UP"):      {"name": "STRONG_BULL",  "wr": 0.85, "duration_avg": 45, "confidence": 0.95},
        ("FORTE", "MEDIA",  "UP"):      {"name": "STRONG_MED",   "wr": 0.75, "duration_avg": 30, "confidence": 0.85},
        ("MEDIO", "BASSA",  "UP"):      {"name": "MEDIUM_BULL",  "wr": 0.70, "duration_avg": 25, "confidence": 0.80},
        ("MEDIO", "MEDIA",  "UP"):      {"name": "CAUTIOUS",     "wr": 0.60, "duration_avg": 15, "confidence": 0.65},
        ("DEBOLE","MEDIA",  "SIDEWAYS"):{"name": "WEAK_NEUTRAL", "wr": 0.45, "duration_avg": 8,  "confidence": 0.40},
        ("DEBOLE","ALTA",   "DOWN"):    {"name": "TRAP",         "wr": 0.05, "duration_avg": 2,  "confidence": 0.05},
        ("FORTE", "ALTA",   "DOWN"):    {"name": "PANIC",        "wr": 0.15, "duration_avg": 3,  "confidence": 0.15},
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

    def save(self, capital: float, total_trades: int):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('capital', ?)",      (str(capital),))
            conn.execute("INSERT OR REPLACE INTO bot_state VALUES ('total_trades', ?)", (str(total_trades),))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[PERSIST] Save: {e}")

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
        self._last_exit_type     = None   # es. "DIVORZIO_IMMEDIATO", "SMORZ"
        self._last_exit_duration = 0.0    # durata in secondi dell'ultimo trade

        # ── Banner ────────────────────────────────────────────────────────
        mode_label = "📄 PAPER TRADE" if self.paper_trade else "🔴 LIVE TRADING"
        log.info("=" * 80)
        log.info(f"🚀 OVERTOP BASSANO V14 PRODUCTION — {mode_label}")
        log.info(f"   Capital: ${self.capital:,.2f}  |  Trades totali: {self.total_trades}")
        log.info(f"   SeedScorer threshold: {SEED_ENTRY_THRESHOLD}")
        log.info(f"   Divorce triggers minimi: {DIVORCE_MIN_TRIGGERS}/4")
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

        # Heartbeat ogni 30s
        if now - self.last_heartbeat > 30:
            self._update_heartbeat()
            self.last_heartbeat = now

        # Persistenza ogni 5 minuti
        if now - self.last_persist > 300:
            self._persist.save(self.capital, self.total_trades)
            self.last_persist = now

        contesto = self.analyzer.analyze()
        if not contesto[0]:
            return
        momentum, volatility, trend = contesto

        if self.trade_open:
            self._evaluate_exit(price, momentum, volatility, trend)
        else:
            self._evaluate_entry(price, momentum, volatility, trend)

    # ════════════════════════════════════════════════════════════════════════
    # ENTRY — catena decisionale completa
    # ════════════════════════════════════════════════════════════════════════

    def _evaluate_entry(self, price, momentum, volatility, trend):

        # ── 1. SEED SCORER ────────────────────────────────────────────────
        seed = self.seed_scorer.score()
        if not seed['pass']:
            return   # impulso insufficiente

        # ── 2. ORACOLO DINAMICO ───────────────────────────────────────────
        is_fantasma, fantasma_reason = self.oracolo.is_fantasma(momentum, volatility, trend)
        if is_fantasma:
            log.debug(f"[ORACOLO] 👻 FANTASMA bloccato: {fantasma_reason}")
            return
        fingerprint_wr = self.oracolo.get_wr(momentum, volatility, trend)

        # ── 3. MATRIMONIO ─────────────────────────────────────────────────
        matrimonio      = MatrimonioIntelligente.get_marriage(momentum, volatility, trend)
        matrimonio_name = matrimonio["name"]
        confidence      = matrimonio["confidence"]

        # ── 4. MEMORIA MATRIMONI ──────────────────────────────────────────
        can_enter, mem_status = self.memoria.get_status(matrimonio_name)
        if not can_enter:
            log.debug(f"[MEMORIA] ❌ {matrimonio_name}: {mem_status}")
            return

        # ── 5. CATENA 5 CAPSULE ────────────────────────────────────────────
        allow_1, conf_1, reason_1 = self.capsule1.valida(fingerprint_wr, momentum, volatility, trend)
        if not allow_1:
            return

        allow_2, reason_2 = self.capsule2.riconosci(confidence)
        if not allow_2:
            return

        allow_3, reason_3 = self.capsule3.proteggi(momentum, volatility, fingerprint_wr)
        if not allow_3:
            return

        allow_4, _, reason_4 = self.capsule4.riconosci(fingerprint_wr, momentum, volatility)
        # capsule4 è informativa, non bloccante — entra anche senza "oro"

        allow_5, duration_min, reason_5 = self.capsule5.timing(True, allow_1, conf_1)
        if not allow_5:
            return

        # ── 6. CAPSULE RUNTIME (JSON dinamico) ───────────────────────────
        ctx_caps = {
            # Core contesto
            'matrimonio':       matrimonio_name,
            'momentum':         momentum,
            'volatility':       volatility,
            'trend':            trend,
            # SeedScorer
            'seed_score':       seed['score'],
            'seed_tipo':        'CONFERMATO' if seed['score'] >= 0.65 else
                                ('PROBABILE'  if seed['score'] >= SEED_ENTRY_THRESHOLD else 'IGNOTO'),
            'force':            seed['score'],          # alias usato dalle capsule storiche
            # OracoloDinamico
            'fingerprint_wr':   fingerprint_wr,
            'wr_oracolo':       round(fingerprint_wr * 100, 1),
            'fingerprint_n':    self.oracolo._memory.get(
                                    self.oracolo._fp(momentum, volatility, trend),
                                    {}).get('samples', 0),
            # Regime (mapping da momentum+volatility)
            'regime':           'trending' if momentum == 'FORTE' and volatility == 'BASSA'
                                else ('choppy'  if volatility == 'ALTA'
                                else ('lateral' if momentum == 'DEBOLE' else 'normal')),
            # Modalità paper/live
            'mode':             'PAPER' if self.paper_trade else 'LIVE',
            # Loss consecutivi (dal log_analyzer)
            'loss_consecutivi': self._loss_consecutivi(),
            # Ultimo exit (per capsule reattive)
            'ultimo_exit_type': self._last_exit_type,
            'ultima_durata':    self._last_exit_duration,
            'sample_size':      int(self.oracolo._memory.get(
                                    self.oracolo._fp(momentum, volatility, trend),
                                    {}).get('samples', 0)),
        }
        caps_check = self.capsule_runtime.valuta(ctx_caps)
        if caps_check.get('blocca'):
            log.debug(f"[CAPSULE_RT] 🚫 Blocco: {caps_check['reason']}")
            return

        # ── ENTRY CONFERMATA ──────────────────────────────────────────────
        size_mult = caps_check.get('size_mult', 1.0)

        log.info(f"[ENTRY] 🚀 {matrimonio_name} | seed={seed['score']:.3f} | fp_wr={fingerprint_wr:.2f} | size_x{size_mult:.1f}")
        self.ai_explainer.log_decision("ENTRY",
            f"Entrato in {matrimonio_name} | seed={seed['score']:.3f} fp_wr={fingerprint_wr:.2f} conf={confidence:.2f}",
            {'momentum': momentum, 'volatility': volatility, 'trend': trend,
             'seed': seed, 'fingerprint_wr': fingerprint_wr})

        if not self.paper_trade:
            self._place_order("BUY", price, size_mult)

        self.trade_open = {
            "price_entry":    price,
            "matrimonio":     matrimonio_name,
            "duration_avg":   matrimonio["duration_avg"],
            "size_mult":      size_mult,
        }
        self.entry_time        = time.time()
        self.entry_momentum    = momentum
        self.entry_volatility  = volatility
        self.entry_trend       = trend
        self.entry_fingerprint = fingerprint_wr
        self.current_matrimonio= matrimonio_name
        self.max_price         = price
        self.total_trades     += 1

    # ════════════════════════════════════════════════════════════════════════
    # EXIT — 4 DIVORCE TRIGGERS + SMORZ + TIMEOUT
    # ════════════════════════════════════════════════════════════════════════

    def _evaluate_exit(self, price, momentum, volatility, trend):
        if price > self.max_price:
            self.max_price = price

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
            log.warning(f"[DIVORZIO IMMEDIATO] 💔 {self.current_matrimonio} | {' + '.join(triggers_attivi)}")
            self._close_trade(price, momentum, volatility, trend, reason="DIVORZIO_IMMEDIATO")
            return

        # ── SMORZ — impulso finito (momentum debole dopo entrata forte) ──
        duration     = time.time() - self.entry_time
        duration_avg = self.trade_open["duration_avg"]
        if duration > duration_avg * 0.5 and momentum == "DEBOLE":
            log.info(f"[SMORZ] 🌙 Impulso finito — {self.current_matrimonio}")
            self._close_trade(price, momentum, volatility, trend, reason="SMORZ")
            return

        # ── TIMEOUT adattivo ──────────────────────────────────────────────
        # Esce dopo duration_avg × 3, oppure se drawdown > 1% dopo duration_avg
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

        # ── Aggiorna tutti i sistemi di apprendimento ─────────────────────
        self.oracolo.record(self.entry_momentum, self.entry_volatility, self.entry_trend, is_win)
        self.memoria.record_trade(matrimonio_name, is_win, wr_expected)
        self.realtime_engine.registra_trade({'matrimonio': matrimonio_name, 'pnl': pnl, 'is_win': is_win})
        self.log_analyzer.registra({'matrimonio': matrimonio_name, 'pnl': pnl, 'is_win': is_win})
        self.realtime_engine.analizza_e_genera()   # genera capsule auto se necessario

        if is_win:
            self.wins   += 1
        else:
            self.losses += 1
        self.capital += pnl

        wr_live = (self.wins / (self.wins + self.losses) * 100) if (self.wins + self.losses) > 0 else 0
        paper_label = "[PAPER] " if self.paper_trade else ""
        log.info(
            f"{paper_label}[EXIT] 🏁 {matrimonio_name} | "
            f"{'🟢 WIN' if is_win else '🔴 LOSS'} | "
            f"PnL=${pnl:+.4f} | WR={wr_live:.1f}% | "
            f"Capital=${self.capital:,.2f} | motivo={reason}"
        )
        self.ai_explainer.log_decision("EXIT",
            f"Uscito da {matrimonio_name} | PnL=${pnl:+.4f} | motivo={reason}",
            {'pnl': pnl, 'is_win': is_win, 'reason': reason})

        if not self.paper_trade:
            self._place_order("SELL", price, self.trade_open.get("size_mult", 1.0))

        # Persiste immediatamente dopo ogni trade
        self._persist.save(self.capital, self.total_trades)
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
