#!/usr/bin/env python3
"""
OVERTOP BASSANO V13.6f – MECCANICO A BORDO + CAPSULE ENGINE
Report ogni 1 minuto, eventi live (entry/exit), zero spam di barre/tick.
Integrato con Mission Control Bridge (https://tecnaria-v2.onrender.com)
V13.6m: Fix URL bridge v3 + Watchdog timeout 3loss indipendente da tick WS
Capsule Engine integrato: logica dinamica generata dai dati, attivabile in volo.
"""

import numpy as np
import json
import os
import pickle
import queue
import threading
import time
import shutil
import asyncio
import websockets
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque, defaultdict
from datetime import datetime
import logging
import sys

# [BRIDGE] Import del ponte verso il Mission Control
from mission_control_bridge import MissionControlBridge

# ============================================================
# CAPSULE ENGINE — Logica dinamica generata dai dati
# V1.0 — integrato in V13.6e
# ============================================================

import operator as _op

_OPS = {
    '>':      _op.gt,
    '>=':     _op.ge,
    '<':      _op.lt,
    '<=':     _op.le,
    '==':     _op.eq,
    '!=':     _op.ne,
    'in':     lambda a, b: a in b,
    'not_in': lambda a, b: a not in b,
}

@dataclass
class Capsula:
    capsule_id:  str
    version:     int
    descrizione: str
    trigger:     list          # [{"param": str, "op": str, "value": any}]
    azione:      dict          # {"type": str, "params": dict}
    priority:    int   = 5
    enabled:     bool  = True
    lifetime:    str   = "permanent"
    source:      str   = "analyzer"
    created_at:  float = field(default_factory=time.time)
    hits:        int   = 0
    wins_after:  int   = 0
    losses_after:int   = 0

    def efficacia(self) -> float:
        tot = self.wins_after + self.losses_after
        return self.wins_after / tot if tot >= 5 else -1.0


# Capsule default generate da 10.058 trade storici (SINAPSI_STORICA.jsonl)
CAPSULE_DEFAULT = [
    Capsula(
        capsule_id="BLOCCO_REGIME_LATERAL_001", version=1,
        descrizione="Regime lateral — WR=17% su 2820 trade storici. SEMPRE tossico.",
        trigger=[{"param": "regime", "op": "==", "value": "lateral"}],
        azione={"type": "blocca_entry", "params": {"reason": "regime_lateral_tossico"}},
        priority=1, source="analyzer"
    ),
    Capsula(
        capsule_id="BLOCCO_REGIME_CHOPPY_001", version=1,
        descrizione="Regime choppy — WR=19% su 1353 trade storici. SEMPRE tossico.",
        trigger=[{"param": "regime", "op": "==", "value": "choppy"}],
        azione={"type": "blocca_entry", "params": {"reason": "regime_choppy_tossico"}},
        priority=1, source="analyzer"
    ),
    Capsula(
        capsule_id="BOOST_REGIME_TRENDING_001", version=1,
        descrizione="Regime trending — WR=80% su 843 trade. Aumenta size +30%.",
        trigger=[{"param": "regime", "op": "==", "value": "trending"}],
        azione={"type": "modifica_size", "params": {"mult": 1.30}},
        priority=3, source="analyzer"
    ),
    # [V13.6m] Capsula notte ELIMINATA — crypto 24/7, America e Asia tradano di notte
    Capsula(
        capsule_id="BLOCCO_DOPO3LOSS_001", version=1,
        descrizione="Dopo 3 loss consecutivi WR crolla a 13-20%. Pausa obbligatoria.",
        trigger=[{"param": "loss_consecutivi", "op": ">=", "value": 3}],
        azione={"type": "blocca_entry", "params": {"reason": "drawdown_sequenziale_3loss"}},
        priority=2, source="analyzer"
    ),
    Capsula(
        capsule_id="BLOCCO_FUNDING_ALTO_001", version=1,
        descrizione="Funding rate > 0.05% — mercato ipercomprato, long pericolosi.",
        trigger=[{"param": "funding_rate", "op": ">", "value": 0.0005}],
        azione={"type": "blocca_entry", "params": {"reason": "funding_rate_elevato"}},
        priority=1, source="analyzer"
    ),
    Capsula(
        capsule_id="BLOCCO_FUNDING_NEGATIVO_001", version=1,
        descrizione="Funding rate < -0.03% — mercato ipervenduto, short aggressivi in atto.",
        trigger=[{"param": "funding_rate", "op": "<", "value": -0.0003}],
        azione={"type": "blocca_entry", "params": {"reason": "funding_rate_negativo"}},
        priority=1, source="analyzer"
    ),
]


class CapsuleRuntime:
    """Valuta e applica capsule attive. Vive dentro il bot."""

    CAPSULE_FILE = "capsule_attive.json"

    def __init__(self, mission_control_url: str = ""):
        self.url = mission_control_url
        self.capsule: List[Capsula] = []
        self._carica()

    def _carica(self):
        try:
            with open(self.CAPSULE_FILE) as f:
                data = json.load(f)
            # [V13.8] Elimina capsule notturne — crypto 24/7
            data = [c for c in data if 'notte' not in c.get('capsule_id','').lower() 
                    and 'notte' not in str(c.get('azione','')).lower()
                    and 'notte' not in str(c.get('trigger','')).lower()]
            self.capsule = [Capsula(**c) for c in data]
            log(f"[CAPSULE] Caricate {len(self.capsule)} capsule da file locale (filtrate notturne)")
        except FileNotFoundError:
            self.capsule = [Capsula(**{k: v for k, v in vars(c).items()}) 
                           for c in CAPSULE_DEFAULT]
            log(f"[CAPSULE] File non trovato — caricate {len(self.capsule)} capsule default")
            self._salva()
        except Exception as e:
            log(f"[CAPSULE] Errore caricamento: {e} — uso default")
            self.capsule = list(CAPSULE_DEFAULT)

    def _salva(self):
        try:
            import dataclasses
            with open(self.CAPSULE_FILE, 'w') as f:
                json.dump([dataclasses.asdict(c) for c in self.capsule], f, indent=2)
        except Exception as e:
            log(f"[CAPSULE] Errore salvataggio: {e}")

    def sync_dal_bridge(self, nuove: list):
        """Riceve capsule nuove dal Mission Control (lista di dict)."""
        if not nuove:
            return
        ids = {c.capsule_id for c in self.capsule}
        aggiunte = 0
        for cd in nuove:
            try:
                if cd['capsule_id'] not in ids:
                    self.capsule.append(Capsula(**cd))
                    aggiunte += 1
                else:
                    for i, c in enumerate(self.capsule):
                        if c.capsule_id == cd['capsule_id'] and cd.get('version', 0) > c.version:
                            self.capsule[i] = Capsula(**cd)
            except Exception as e:
                log(f"[CAPSULE] Errore caricamento capsula {cd.get('capsule_id')}: {e}")
        if aggiunte:
            log(f"[CAPSULE] {aggiunte} nuove capsule dal Mission Control")
            self._salva()

    def valuta(self, contesto: dict) -> dict:
        """
        Valuta tutte le capsule attive.
        Ritorna override con: blocca, blocca_ragione, size_mult, exit_ora, capsule_attivate
        """
        override = {
            'blocca': False,
            'blocca_ragione': None,
            'size_mult': 1.0,
            'exit_ora': False,
            'capsule_attivate': [],
        }
        for cap in sorted([c for c in self.capsule if c.enabled], key=lambda c: c.priority):
            if self._trigger_vero(cap.trigger, contesto):
                cap.hits += 1
                self._applica(cap.azione, override)
                override['capsule_attivate'].append(cap.capsule_id)
                if cap.lifetime == 'once':
                    cap.enabled = False
                if override['blocca']:
                    break
        return override

    def _trigger_vero(self, regole: list, ctx: dict) -> bool:
        for r in regole:
            val = ctx.get(r['param'])
            if val is None:
                return False
            try:
                if not _OPS[r['op']](val, r['value']):
                    return False
            except Exception:
                return False
        return True

    def _applica(self, azione: dict, override: dict):
        t = azione.get('type')
        p = azione.get('params', {})
        if t == 'blocca_entry':
            override['blocca'] = True
            override['blocca_ragione'] = p.get('reason', 'capsula')
        elif t == 'modifica_size':
            override['size_mult'] *= p.get('mult', 1.0)
        elif t == 'exit_ora':
            override['exit_ora'] = True
        elif t == 'composto':
            for sotto in p.get('azioni', []):
                self._applica(sotto, override)

    def registra_esito(self, capsule_ids: list, win: bool):
        for cid in capsule_ids:
            for c in self.capsule:
                if c.capsule_id == cid:
                    if win:
                        c.wins_after += 1
                    else:
                        c.losses_after += 1
                    if c.hits >= 20 and c.efficacia() < 0.30:
                        c.enabled = False
                        log(f"[CAPSULE] {cid} auto-disabilitata (efficacia {c.efficacia():.0%} su {c.hits} hit)")


# ============================================================
# CONFIGURAZIONE (identica alla V13.6d, ma con oracolo artificiale)
# ============================================================

CONFIG = {
    # ========== DNA SACRO ==========
    'BAR_INTERVAL'       : 1.0,
    'WINDOW_CURV'        : 15,
    'WINDOW_PRESS'       : 30,
    'REGIME_MIN_RANGE'   : 0.0025,
    'REGIME_WINDOW'      : 60,
    'REVERSAL_THRESHOLD' : 0.50,
    'RISCHIO_PER_TRADE'  : 0.015,
    'MAX_EXPOSURE_PCT'   : 1.0,
    'EARLY_WINDOW'       : 5,
    'EA_MIN'             : 0.8,
    'DIR_COH_MIN'        : 0.5,
    'DAMPING_THRESHOLD'  : 0.30,

    # ========== PARAMETRI MODALITÀ ==========
    'NORMAL_MIN_FORZA'   : 0.55,
    'NORMAL_MAX_FORZA'   : 0.80,
    'NORMAL_HARD_SL'     : 0.25,
    'NORMAL_VETO_WR'     : 0.50,
    'NORMAL_BOOST_WR'    : 0.68,
    'NORMAL_MULT_MAX'    : 1.3,
    'FLAT_MIN_FORZA'     : 0.65,
    'FLAT_MAX_FORZA'     : 0.75,
    'FLAT_HARD_SL'       : 0.20,
    'FLAT_VETO_WR'       : 0.30,
    'FLAT_BOOST_WR'      : 0.68,
    'FLAT_MULT_MAX'      : 1.2,

    # ========== SWITCH BINARIO ==========
    'SW_CONFIRM_BARS'    : 100,
    'SW_EMA_ALPHA'       : 0.05,
    'SW_FLAT_THRESHOLD'  : 0.0028,

    # ========== SEED SCORE ==========
    'SEED_THRESH_FLAT'        : 0.50,
    'SEED_THRESH_NORMAL'      : 0.45,
    'BOOST_SEED_MIN_NORMAL'   : 0.58,
    'SEED_WINDOW_RANGE'       : 60,
    'SEED_WINDOW_VOL'         : 10,
    'SEED_WINDOW_DIR'         : 10,
    'SEED_WINDOW_BRK'         : 30,
    'SEED_W_RANGE'            : 0.25,
    'SEED_W_VOL'              : 0.40,
    'SEED_W_DIR'              : 0.20,
    'SEED_W_BRK'              : 0.15,

    # ========== ORACOLO DINAMICO ==========
    'OR_MIN_SAMPLES'     : 5,
    'OR_DECAY_TRADES'    : 1_000_000,
    'OR_MULT_MIN'        : 0.7,
    'VIP_S_MIN'          : 5,
    'VIP_PNL_POS'        : 0.0,
    'VIP_PNL_NEG'        : -2.0,
    'VIP_FACTOR'         : 1.6,
    'VIP_MULT_MAX'       : 2.0,
    'VIP_UPDATE_EVERY'   : 50,
    'VIP_CLASS_FILE'     : 'v18_vip_tossici_classification.json',
    'OR_MEMORY_FILE'        : 'oracolo_memoria_v13.json',   # oracolo base
    'OR_MEMORY_FILE_FLAT'   : 'v17_oracolo_memory_NORMAL_CLEAN.json',  # oracolo FLAT
    'OR_MEMORY_FILE_NORMAL' : 'oracolo_memoria_v13.json',   # oracolo NORMAL
    'OR_SINAPSI_FILE'    : 'SINAPSI_STORICA_V15.jsonl',
    'OR_WARMUP_FILE'     : 'v16_warmup_log.json',
    'MIN_HOLD_TIME_SL'   : 2.0,

    # ========== FISIOLOGIA PER ASSET ==========
    'ASSET_FISIOLOGIA'   : {
        'BTCUSDC': {
            'tau_spike'     : 1.2,
            'tau_loss'      : 3.0,
            'tau_critico'   : 3.0,
            'tau_respiro'   : 3.4,
            'potenza'       : 0.1718,
            'sl_rischio_pct': 0.28,
            'sl_min'        : 50.0,
            'sl_max'        : 200.0,
        },
        'ETHUSDC': {
            'tau_spike'     : 0.9,
            'tau_loss'      : 2.4,
            'tau_critico'   : 2.0,
            'tau_respiro'   : 3.4,
            'potenza'       : 0.2105,
            'sl_rischio_pct': 0.40,
            'sl_min'        : 20.0,
            'sl_max'        : 80.0,
        },
        'SOLUSDC': {
            'tau_spike'     : 1.3,
            'tau_loss'      : 3.3,
            'tau_critico'   : 5.0,
            'tau_respiro'   : 5.8,
            'potenza'       : 0.2388,
            'sl_rischio_pct': 0.32,
            'sl_min'        : 10.0,
            'sl_max'        : 40.0,
        },
    },
    'R_VITA'            : -0.30,
    'R_MORTE'           : -0.50,
    'CIRCUIT_BREAKER': {
        'SOLUSDC': {'window': 10, 'ratio_max': 0.75, 'wr_min': 0.35, 'pausa': 25},
        'ETHUSDC': {'window': 10, 'ratio_max': 0.55, 'wr_min': 0.35, 'pausa': 25},
        'BTCUSDC': {'window': 10, 'ratio_max': 0.75, 'wr_min': 0.35, 'pausa': 20},
    },
    'ASSET_VETO_RULES'  : {
        'SOLUSDC': {'FLAT': ['LONG']},
    },
    'NORMAL_SIZE_MULT'  : 0.30,

    # ========== NUOVI PARAMETRI V13.5 ==========
    'DECAY_FACTOR'       : 0.99,
    'META_WINDOW'        : 50,
    'META_ACC_THRESHOLD' : 0.55,
    'META_REDUCTION'     : 0.8,
    'TAKE_PROFIT_R'      : 0.65,
    'TRAILING_STEP'      : 0.25,
    'ATR_PERIOD'         : 10,
    'ATR_MULT'           : 1.5,
    'MAX_MULT'           : 2.0,
    'FANTASMA_WR'        : 0.30,  # [V13.6i] abbassato — blocca solo pattern davvero tossici
    'FANTASMA_PNL'       : -0.20,  # [V13.6i] abbassato — richiede perdita media significativa
    'MIN_SAMPLES_CONF'   : 15,

    # ========== SISTEMA ==========
    'CAPITAL_START'     : 10000.0,
    'STATE_FILE'        : 'v18_state.pkl',
    'LOG_FILE'          : 'V13_5_HYBRID.log',
    'JSONL_FILE'        : 'V13_5_trades.jsonl',
    'RUN_LOG_FILE'      : 'V13_5_RUN.log',
    'ASSETS'            : ['BTCUSDC'],
    'WS_BASE'           : 'wss://stream.binance.com:9443',
    'REPORT_EVERY'      : 60,                # <-- report ogni 1 minuto (60 secondi)
    'DEBUG'             : False,             # <-- DEBUG = False per evitare spam di tick
}

# ============================================================
# LOGGING ASINCRONO (identico)
# ============================================================

_log_queue = queue.Queue()

def _logger_thread():
    while True:
        msg = _log_queue.get()
        if msg is None:
            break
        try:
            print(msg, flush=True)
        except:
            pass

threading.Thread(target=_logger_thread, daemon=True).start()

def log(msg):
    _log_queue.put(str(msg))

# ============================================================
# DATA STRUCTURES (identiche)
# ============================================================

@dataclass
class Tick:
    price: float
    volume: float
    timestamp: float

@dataclass
class Outcome:
    pnl_pct: float
    pnl_dollari: float
    duration: float
    reason: str
    timestamp: float

@dataclass
class Posizione:
    asset: str
    direction: int
    entry_price: float
    entry_imp: float
    entry_pressure: float
    size: float
    entry_time: float
    rischio_assunto: float
    oracolo_mult: float
    fingerprint: str
    modalita: str
    seed_score: float
    isvip: bool = False
    r_timeline: list = field(default_factory=list)
    hard_sl_din: float = None
    vedo_cls: Optional[str] = None
    vedo_mult: float = 1.0
    vedo_fp: Optional[str] = None
    vedo_early_exit: float = 0.0
    max_r: float = 0.0
    partial_profit_taken: bool = False
    partial_price: Optional[float] = None
    entry_regime: str = 'unknown'   # [V13.6h] regime al momento dell'entry

# ============================================================
# LOGGER JSONL (identico)
# ============================================================

class JsonlLogger:
    def __init__(self, filename: str):
        self.filename = filename
        self.file = open(filename, 'a', encoding='utf-8')
        self.lock = threading.Lock()

    def _convert(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: self._convert(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._convert(i) for i in obj]
        return obj

    def write(self, record: dict):
        record = self._convert(record)
        with self.lock:
            self.file.write(json.dumps(record) + '\n')
            self.file.flush()

    def close(self):
        self.file.close()

# ============================================================
# RUN LOGGER (con metodi per i report)
# ============================================================

class RunLogger:
    def __init__(self, filename: str):
        self.filename = filename
        self.file = open(filename, 'a', encoding='utf-8')
        self.lock = threading.Lock()
        self.term_width = shutil.get_terminal_size().columns

    def write(self, msg: str):
        with self.lock:
            self.file.write(msg + '\n')
            self.file.flush()

    def report(self, capital: float, roi: float, trades: int, wins: int, losses: int,
               pos: Optional[Posizione] = None, price: float = 0.0):
        """Stampa un report periodico (ogni 1 minuto)."""
        lines = []
        lines.append("\n" + "="*60)
        lines.append(f"📊 REPORT {datetime.now().strftime('%H:%M:%S')}")
        lines.append(f"💰 Capitale: ${capital:,.2f}  |  ROI: {roi:+.2f}%")
        lines.append(f"📈 Trade totali: {trades}  |  Vinti: {wins}  |  Persi: {losses}  |  WR: {wins/trades:.1%}" if trades>0 else "📈 Ancora nessun trade")
        if pos:
            pnl_corrente = (price - pos.entry_price) * pos.direction * pos.size
            if pos.rischio_assunto > 0:
                R_corrente = pnl_corrente / pos.rischio_assunto
            else:
                R_corrente = 0.0
            dur = time.time() - pos.entry_time
            lines.append(f"🟢 POSIZIONE APERTA: {pos.modalita} {'LONG' if pos.direction>0 else 'SHORT'} @ {pos.entry_price:.2f}")
            lines.append(f"   PnL: ${pnl_corrente:+.2f}  |  R: {R_corrente:+.2f}  |  Durata: {dur:.0f}s")
        lines.append("="*60)
        for line in lines:
            self.write(line)
            log(line)   # anche su console

    def entry(self, asset: str, price: float, forza: float, size: float, mult: float,
              modalita: str, seed: float, msg: str):
        line = (f"\n🚀 ENTRY {('LONG' if size>0 else 'SHORT')} @ ${price:.4f} | "
                f"Size:{size:.4f} | Force: {forza:.3f} | Mult:{mult:.2f}x | Mode:{modalita} | Seed:{seed:.3f} | {msg}")
        self.write(line)
        log(line)

    def exit(self, asset: str, price: float, reason: str, pnl: float, r_r: float,
             duration: float, modalita: str, capital: float, partial: bool = False):
        emoji = "🟢 PROFIT" if pnl > 0 else ("🔴 LOSS" if pnl < 0 else "⚪ BE")
        line = (f"\n🏁 EXIT {reason} | {emoji} | PnL: ${pnl:+.4f} | R: {r_r:+.3f} | "
                f"Dur: {duration:.1f}s | Mode:{modalita} | Cap: ${capital:.2f}")
        if partial:
            line += " [partial]"
        self.write(line)
        log(line)

    def close(self):
        self.file.close()

# ============================================================
# ORACOLO DINAMICO (identico, ma con meno log)
# ============================================================

class OracoloDinamico:
    def __init__(self, jsonl_logger: Optional[JsonlLogger] = None, run_logger: Optional[RunLogger] = None):
        self.memoria: Dict[str, List[Outcome]] = defaultdict(list)
        self.trade_count = 0
        self.decay = CONFIG['DECAY_FACTOR']
        self.stats = {
            'consultazioni': 0,
            'fantasmi': 0,
            'confermati': 0,
            'neutri': 0,
            'ignoti': 0,
        }
        self.meta_history = deque(maxlen=CONFIG['META_WINDOW'])
        self.global_mult = 1.0
        self._last_wr = 0.5
        self.jsonl = jsonl_logger
        self.run = run_logger
        self._carica()

    def _fingerprint(self, asset: str, forza: float, direzione: str, modalita: str = 'NORMAL') -> str:
        force_bucket = int(abs(forza) / 0.05)
        return f"{asset}_F{force_bucket}_{direzione.upper()}_{modalita}"

    def _calcola_win_rate_pesato(self, outcomes: List[Outcome]) -> Tuple[float, float, float]:
        if not outcomes:
            return 0.5, 0.0, 0
        sorted_out = sorted(outcomes, key=lambda o: o.timestamp)
        total_weight = 0.0
        weighted_wins = 0.0
        weighted_pnl = 0.0
        for i, o in enumerate(sorted_out):
            w = self.decay ** (len(sorted_out) - i - 1)
            total_weight += w
            if o.pnl_dollari > 0:
                weighted_wins += w
            weighted_pnl += o.pnl_dollari * w
        win_rate = weighted_wins / total_weight if total_weight > 0 else 0.5
        avg_pnl = weighted_pnl / total_weight if total_weight > 0 else 0.0
        return win_rate, avg_pnl, total_weight

    def consulta(self, asset: str, forza: float, direzione: str, modalita: str) -> Tuple[float, str, str, bool]:
        self.stats['consultazioni'] += 1
        fp = self._fingerprint(asset, forza, direzione, modalita)
        # [V13.6l] Usa oracolo specifico per modalità
        if modalita == 'FLAT' and hasattr(self, 'memoria_flat'):
            outcomes = self.memoria_flat.get(fp, self.memoria.get(fp, []))
        elif modalita == 'NORMAL' and hasattr(self, 'memoria_normal'):
            outcomes = self.memoria_normal.get(fp, self.memoria.get(fp, []))
        else:
            outcomes = self.memoria.get(fp, [])
        win_rate, avg_pnl, eff_samples = self._calcola_win_rate_pesato(outcomes)
        n = len(outcomes)

        conf = min(n / CONFIG['MIN_SAMPLES_CONF'], 1.0)

        if n < 3:
            tipo = 'IGNOTO'
            mult = 1.0  # [V13.6i] IGNOTO passa sempre — non ha abbastanza dati per bloccare
            msg = f"❓ Ignoto ({n} campioni)"
            self.stats['ignoti'] += 1
        elif win_rate < CONFIG['FANTASMA_WR'] and avg_pnl < CONFIG['FANTASMA_PNL']:
            tipo = 'FANTASMA'
            mult = 0.5
            msg = f"⚠️ FANTASMA WR={win_rate:.0%} n={n}"
            self.stats['fantasmi'] += 1
        elif win_rate > 0.6 and avg_pnl > 0.05:
            tipo = 'CONFERMATO'
            mult = 1.0 + (win_rate - 0.5) * 2 * conf
            mult = min(mult, CONFIG['MAX_MULT'])
            msg = f"✅ CONFERMATO WR={win_rate:.0%} n={n}"
            self.stats['confermati'] += 1
        else:
            tipo = 'NEUTRO'
            mult = 1.0
            msg = f"➖ Neutro WR={win_rate:.0%} n={n}"
            self.stats['neutri'] += 1

        mult *= self.global_mult
        self._last_wr = win_rate

        if self.jsonl:
            record = {
                'tipo': 'oracolo',
                'timestamp': time.time(),
                'asset': asset,
                'modalita': modalita,
                'fingerprint': fp,
                'forza': round(forza, 3),
                'direzione': direzione,
                'win_rate': round(win_rate, 3),
                'avg_pnl': round(avg_pnl, 4),
                'n_campioni': n,
                'confidenza': round(conf, 3),
                'tipo_class': tipo,
                'moltiplicatore': round(mult, 3),
                'msg': msg,
            }
            self.jsonl.write(record)

        return mult, f"{tipo}: {msg}", fp, False

    def apprendi(self, asset: str, forza: float, direzione: str, modalita: str, outcome: Outcome):
        fp = self._fingerprint(asset, forza, direzione, modalita)
        self.memoria[fp].append(outcome)
        self.trade_count += 1
        if len(self.memoria[fp]) > 200:
            self.memoria[fp] = self.memoria[fp][-200:]

    def aggiorna_meta(self, corretto: bool):
        self.meta_history.append(1 if corretto else 0)
        if len(self.meta_history) == self.meta_history.maxlen:
            accuracy = sum(self.meta_history) / len(self.meta_history)
            if accuracy < CONFIG['META_ACC_THRESHOLD']:
                self.global_mult = CONFIG['META_REDUCTION']
            else:
                self.global_mult = 1.0

    def _salva(self):
        data = {}
        for fp, outcomes in self.memoria.items():
            data[fp] = [{'pnl': o.pnl_dollari, 'dur': o.duration, 'ts': o.timestamp} for o in outcomes]
        with open('oracolo_memoria_v13.json', 'w') as f:
            json.dump(data, f)

    def _carica(self):
        """[V13.6l] Oracolo doppio: memoria separata per FLAT e NORMAL."""
        self.memoria_flat   = defaultdict(list)  # oracolo dedicato FLAT
        self.memoria_normal = defaultdict(list)  # oracolo dedicato NORMAL

        piani = [
            ('FLAT',   CONFIG.get('OR_MEMORY_FILE_FLAT',   'v17_oracolo_memory_NORMAL_CLEAN.json'), self.memoria_flat),
            ('NORMAL', CONFIG.get('OR_MEMORY_FILE_NORMAL', 'oracolo_memoria_v13.json'),             self.memoria_normal),
        ]

        for nome_piano, filepath, mem_target in piani:
            files_da_tentare = [filepath, 'oracolo_memoria_v13.json', 'v17_oracolo_memory_NORMAL_CLEAN.json']
            for f_path in files_da_tentare:
                try:
                    with open(f_path, 'r') as f:
                        data = json.load(f)
                    if not data:
                        continue
                    primo = next(iter(data.values()))
                    ts_base = time.time()

                    if isinstance(primo, list):
                        for fp, lista in data.items():
                            mem_target[fp] = [
                                Outcome(pnl_dollari=o['pnl'], duration=o.get('dur', 1.0),
                                        timestamp=o.get('ts', ts_base), pnl_pct=0, reason='loaded')
                                for o in lista if isinstance(o, dict)
                            ]
                    elif isinstance(primo, dict) and 'w' in primo:
                        for fp, info in data.items():
                            outcomes = []
                            n_w = info.get('w', 0)
                            n_l = info.get('l', 0)
                            total = max(n_w + n_l, 1)
                            pnl_medio = info.get('pnl', 0) / total
                            for i in range(n_w):
                                outcomes.append(Outcome(
                                    pnl_dollari=max(abs(pnl_medio), 0.5),
                                    duration=2.0, timestamp=ts_base - (total - i),
                                    pnl_pct=0, reason='loaded'))
                            for i in range(n_l):
                                outcomes.append(Outcome(
                                    pnl_dollari=-0.5,
                                    duration=2.0, timestamp=ts_base - (n_l - i),
                                    pnl_pct=0, reason='loaded'))
                            if outcomes:
                                mem_target[fp] = outcomes
                    else:
                        continue

                    log(f"[ORACOLO-{nome_piano}] ✅ Caricati {len(mem_target)} pattern da {f_path}")
                    break
                except FileNotFoundError:
                    continue
                except Exception as e:
                    log(f"[ORACOLO-{nome_piano}] Errore {f_path}: {e}")
                    continue

        # Memoria unificata = unione dei due (per compatibilità)
        self.memoria = defaultdict(list)
        self.memoria.update(self.memoria_normal)
        for fp, outcomes in self.memoria_flat.items():
            if fp not in self.memoria:
                self.memoria[fp] = outcomes

# ============================================================
# VERITÀ DNA (identica)
# ============================================================

class VeritaDNA:
    def __init__(self):
        self.bars_1s      : Dict[str, deque] = {}
        self.last_bar_time: Dict[str, float] = {}
        self.tick_buffer  : Dict[str, List[Tick]] = {}
        self.forza_storia : Dict[str, deque] = {}
        self.prices       : Dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
        self.term_width = max(80, shutil.get_terminal_size().columns)

    def aggrega_barra_1s(self, asset: str, tick: Tick) -> Optional[dict]:
        now = tick.timestamp
        bar_start = int(now)

        if asset not in self.last_bar_time:
            self.last_bar_time[asset] = bar_start
            if asset not in self.tick_buffer:
                self.tick_buffer[asset] = []
            self.tick_buffer[asset].append(tick)
            return None

        if bar_start > self.last_bar_time[asset]:
            if asset in self.tick_buffer and self.tick_buffer[asset]:
                ticks = self.tick_buffer[asset]
                prices = [t.price for t in ticks]
                volumes = [t.volume for t in ticks]
                bar = {
                    'timestamp': self.last_bar_time[asset],
                    'open' : prices[0],
                    'high' : max(prices),
                    'low'  : min(prices),
                    'close': prices[-1],
                    'volume': sum(volumes),
                }
                if asset not in self.bars_1s:
                    self.bars_1s[asset] = deque(maxlen=CONFIG['WINDOW_PRESS'] * 4)
                self.bars_1s[asset].append(bar)
                self.tick_buffer[asset] = []
                self.last_bar_time[asset] = bar_start
                if asset not in self.tick_buffer:
                    self.tick_buffer[asset] = []
                self.tick_buffer[asset].append(tick)
                return bar

        if asset not in self.tick_buffer:
            self.tick_buffer[asset] = []
        self.tick_buffer[asset].append(tick)
        return None

    def check_regime(self, asset: str) -> Tuple[bool, float]:
        if asset not in self.bars_1s or len(self.bars_1s[asset]) < CONFIG['REGIME_WINDOW']:
            return False, 0.0
        bars = list(self.bars_1s[asset])[-CONFIG['REGIME_WINDOW']:]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        closes = [b['close'] for b in bars]
        mean_p = np.mean(closes)
        if mean_p <= 0:
            return False, 0.0
        range_pct = (max(highs) - min(lows)) / mean_p
        return range_pct >= CONFIG['REGIME_MIN_RANGE'], range_pct

    def calcola_regime_range(self, asset: str) -> float:
        if asset not in self.bars_1s or len(self.bars_1s[asset]) < 10:
            return 0.01
        bars = list(self.bars_1s[asset])[-CONFIG['REGIME_WINDOW']:]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        closes = [b['close'] for b in bars]
        mean_p = np.mean(closes)
        if mean_p <= 0:
            return 0.01
        return (max(highs) - min(lows)) / mean_p

    def pressione(self, asset: str) -> float:
        if asset not in self.bars_1s or len(self.bars_1s[asset]) < 5:
            return 0.0
        bars = list(self.bars_1s[asset])[-CONFIG['WINDOW_PRESS']:]
        closes  = np.array([b['close']  for b in bars])
        volumes = np.array([b['volume'] for b in bars])
        delta = np.diff(closes)
        press = np.sum(abs(delta) * volumes[1:]) / (np.sum(volumes[1:]) + 1e-9)
        return np.tanh(press * 100)

    def curvatura(self, asset: str) -> float:
        if asset not in self.bars_1s or len(self.bars_1s[asset]) < CONFIG['WINDOW_CURV'] + 2:
            return 0.0
        bars = list(self.bars_1s[asset])[-CONFIG['WINDOW_CURV']:]
        closes = np.array([b['close'] for b in bars])
        vel = np.diff(closes)
        if len(vel) < 2:
            return 0.0
        acc = np.diff(vel)
        curv = np.mean(acc) if len(acc) > 0 else 0.0
        vol = np.std(closes) + 1e-9
        return np.tanh(curv / vol * 10)

    def forza(self, asset: str) -> Tuple[float, float, float]:
        press = self.pressione(asset)
        curv = self.curvatura(asset)
        f = curv * press
        if len(self.bars_1s.get(asset, [])) >= 2:
            last = list(self.bars_1s[asset])[-3:]
            if len(last) >= 2:
                direction = np.sign(last[-1]['close'] - last[-2]['close'])
                f *= direction
        return f, press, curv

    def calcola_atr(self, asset: str, price: float) -> float:
        self.prices[asset].append(price)
        if len(self.prices[asset]) < CONFIG['ATR_PERIOD'] + 1:
            return 0.0
        prices = list(self.prices[asset])
        tr_list = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        atr = np.mean(tr_list[-CONFIG['ATR_PERIOD']:])
        return atr

    def calcola_size_risk_based(self, forza: float, asset: str, price: float, capital: float) -> Tuple[float, float, float]:
        rischio_max = capital * CONFIG['RISCHIO_PER_TRADE']
        fisio = CONFIG['ASSET_FISIOLOGIA'].get(asset, {})

        atr = self.calcola_atr(asset, price)
        sl_atr = atr * CONFIG['ATR_MULT'] if atr > 0 else float('inf')

        sl_min = fisio.get('sl_min', 10.0)
        sl_max = fisio.get('sl_max', 100.0)

        stop_dollari = max(sl_min, min(sl_max, sl_atr))

        size = rischio_max / stop_dollari

        max_exposure = capital * CONFIG['MAX_EXPOSURE_PCT']
        max_size = max_exposure / price
        size = min(size, max_size)

        rischio_effettivo = size * stop_dollari

        return size, rischio_effettivo, stop_dollari

    def check_exit_fisico(self, asset: str, forza_corrente: float, posizione: Posizione,
                          current_price: float, hard_sl: float, current_time: float) -> Optional[str]:
        forza_ass = abs(forza_corrente)
        entry_imp_ass = abs(posizione.entry_imp)
        duration = current_time - posizione.entry_time
        pnl_corrente = (current_price - posizione.entry_price) * posizione.direction * posizione.size

        if hard_sl > 0 and pnl_corrente <= -hard_sl:
            return "HARD_SL"

        # ---- MAL1: STOP LOSS DI EMERGENZA ----
        # Se il PnL supera la soglia massima di perdita tollerabile → esce immediatamente
        MAL1_PNL_MAX   = -8.0   # max perdita in $ per trade
        MAL1_TIME_MAX  = 20.0   # max secondi in posizione se in perdita
        MAL1_PNL_TIME  = -3.0   # soglia perdita per attivare il timeout rapido

        if pnl_corrente <= MAL1_PNL_MAX:
            log(f"[{asset}] 🚨 MAL1-STOP: perdita ${pnl_corrente:.2f} oltre soglia ${MAL1_PNL_MAX}. Uscita di protezione.")
            return "MAL1_STOP"

        if duration >= MAL1_TIME_MAX and pnl_corrente <= MAL1_PNL_TIME:
            log(f"[{asset}] 🚨 MAL1-TIMEOUT: {duration:.0f}s in perdita ${pnl_corrente:.2f}. Uscita di protezione.")
            return "MAL1_TIMEOUT"
        # ---------------------------------------

        if posizione.modalita == 'NORMAL' and 2.0 <= duration < 10.0:
            if pnl_corrente < 0:
                return "ZONA_MORTA"

        if (forza_corrente * posizione.entry_imp < 0
                and forza_ass > entry_imp_ass * CONFIG['REVERSAL_THRESHOLD']):
            return "FORCE_REVERSAL"

        # ---- FINESTRA DAMPING DINAMICA ----
        if entry_imp_ass < 0.6:
            min_duration = 4.0 + (0.6 - entry_imp_ass) * 5.0
            min_duration = max(2.0, min_duration)
        else:
            min_duration = 3.0

        if duration >= min_duration and forza_ass < entry_imp_ass * CONFIG['DAMPING_THRESHOLD']:
            return "DAMPING"
        # -----------------------------------

        return None

    def check_persistenza(self, asset: str, forza: float) -> bool:
        if asset not in self.forza_storia:
            self.forza_storia[asset] = deque(maxlen=3)
        self.forza_storia[asset].append(forza)
        if len(self.forza_storia[asset]) < 2:
            return False
        forze = list(self.forza_storia[asset])[-2:]
        min_f = CONFIG['NORMAL_MIN_FORZA']
        if all(abs(f) > min_f for f in forze):
            if all(f > 0 for f in forze) or all(f < 0 for f in forze):
                return True
        return False

# ============================================================
# SWITCH BINARIO (identico)
# ============================================================

class SwitchBinario:
    def __init__(self):
        self.ema_range = None
        self.current = 'NORMAL'
        self.candidate = 'NORMAL'
        self.cand_count = 0
        self.switches = 0
        self.bars_in_state = 0

    def update(self, range_60s: float) -> str:
        a = CONFIG['SW_EMA_ALPHA']
        self.ema_range = (range_60s if self.ema_range is None
                          else a * range_60s + (1 - a) * self.ema_range)

        raw = 'FLAT' if self.ema_range < CONFIG['SW_FLAT_THRESHOLD'] else 'NORMAL'

        if raw == self.candidate:
            self.cand_count += 1
        else:
            self.candidate = raw
            self.cand_count = 1

        if (self.cand_count >= CONFIG['SW_CONFIRM_BARS']
                and self.candidate != self.current):
            old = self.current
            self.current = self.candidate
            self.cand_count = 0
            self.bars_in_state = 0
            self.switches += 1
            log_msg = f"[SWITCH] {old} → {self.current} (EMA_range={self.ema_range:.5f} | switch#{self.switches})"
            log(log_msg)
        else:
            self.bars_in_state += 1

        return self.current

    def get_params(self) -> dict:
        if self.current == 'FLAT':
            return {
                'min_forza' : CONFIG['FLAT_MIN_FORZA'],
                'max_forza' : CONFIG['FLAT_MAX_FORZA'],
                'hard_sl'   : CONFIG['FLAT_HARD_SL'],
                'veto_wr'   : CONFIG['FLAT_VETO_WR'],
                'boost_wr'  : CONFIG['FLAT_BOOST_WR'],
                'mult_max'  : CONFIG['FLAT_MULT_MAX'],
                'use_seed'  : True,
            }
        else:
            return {
                'min_forza' : CONFIG['NORMAL_MIN_FORZA'],
                'max_forza' : CONFIG['NORMAL_MAX_FORZA'],
                'hard_sl'   : CONFIG['NORMAL_HARD_SL'],
                'veto_wr'   : CONFIG['NORMAL_VETO_WR'],
                'boost_wr'  : CONFIG['NORMAL_BOOST_WR'],
                'mult_max'  : CONFIG['NORMAL_MULT_MAX'],
                'use_seed'  : False,
            }

# ============================================================
# SEED SCORER (identico)
# ============================================================

class SeedScorer:
    def calcola(self, bars: list, side: int) -> Tuple[float, dict]:
        if len(bars) < 30:
            return 0.5, {'insufficiente': True}

        closes  = np.array([b['close']  for b in bars])
        highs   = np.array([b['high']   for b in bars])
        lows    = np.array([b['low']    for b in bars])
        volumes = np.array([b['volume'] for b in bars])
        price_now = closes[-1]

        w60 = min(CONFIG['SEED_WINDOW_RANGE'], len(bars))
        h60 = np.max(highs[-w60:])
        l60 = np.min(lows[-w60:])
        r60 = h60 - l60
        if r60 > 1e-9:
            rp = (price_now - l60) / r60
        else:
            rp = 0.5
        rp_score = (1.0 - rp) if side > 0 else rp

        w_vol = min(CONFIG['SEED_WINDOW_VOL'], len(bars))
        if w_vol >= 6 and np.mean(volumes[-30:]) > 0:
            vol_recente = np.mean(volumes[-w_vol//2:])
            vol_base    = np.mean(volumes[-w_vol:])
            vol_ratio   = vol_recente / (vol_base + 1e-9)
            vol_score   = (np.tanh((vol_ratio - 1.0) * 2) + 1) / 2
        else:
            vol_score = 0.5

        w_dir = min(CONFIG['SEED_WINDOW_DIR'], len(bars))
        if w_dir >= 3:
            diffs = np.diff(closes[-w_dir:])
            n_correct = np.sum(np.sign(diffs) == side)
            dir_score = n_correct / max(len(diffs), 1)
        else:
            dir_score = 0.5

        w_brk = min(CONFIG['SEED_WINDOW_BRK'], len(bars))
        h30 = np.max(highs[-w_brk:-1]) if len(bars) > w_brk else np.max(highs[:-1])
        l30 = np.min(lows[-w_brk:-1])  if len(bars) > w_brk else np.min(lows[:-1])
        r30 = h30 - l30
        if r30 > 1e-9:
            if side > 0:
                brk_raw = (price_now - h30) / r30
            else:
                brk_raw = (l30 - price_now) / r30
            brk_score = (np.tanh(brk_raw * 3) + 1) / 2
        else:
            brk_score = 0.5

        seed_score = (
            CONFIG['SEED_W_RANGE'] * rp_score +
            CONFIG['SEED_W_VOL']   * vol_score +
            CONFIG['SEED_W_DIR']   * dir_score +
            CONFIG['SEED_W_BRK']   * brk_score
        )

        componenti = {
            'range_pos'  : round(rp_score, 3),
            'vol_accel'  : round(vol_score, 3),
            'dir_consist': round(dir_score, 3),
            'breakout'   : round(brk_score, 3),
            'seed_score' : round(seed_score, 3),
            'range_pct'  : round(rp, 3),
        }

        return float(seed_score), componenti

# ============================================================
# ENGINE V13.6d (con report periodico)
# ============================================================

class OvertopV13_5:
    VERSIONE = "V13.8-SINAPSI-GUARD"

    def __init__(self):
        self.capital = CONFIG['CAPITAL_START']
        self.capital_iniziale = CONFIG['CAPITAL_START']

        self.jsonl_logger = JsonlLogger(CONFIG['JSONL_FILE'])
        self.run_logger   = RunLogger(CONFIG['RUN_LOG_FILE'])

        self.verita = VeritaDNA()
        self.oracolo = OracoloDinamico(self.jsonl_logger, self.run_logger)
        self.switches: Dict[str, SwitchBinario] = {}
        self.seed_scorer = SeedScorer()

        self.posizioni: Dict[str, Posizione] = {}
        self.trades: List[dict] = []
        self.equity_curve: List[Tuple[float, float]] = [(datetime.now().timestamp(), self.capital)]
        self.early_data: Dict[str, dict] = {}
        self.trade_count = 0
        self.tick_count = 0
        self.bar_count = 0
        self.last_report = 0
        self.emergency_stop = False

        self.cb_history: Dict[str, list] = {a: [] for a in CONFIG['ASSETS']}
        self.cb_pausa:   Dict[str, int]  = {a: 0  for a in CONFIG['ASSETS']}

        self.seed_stats = {'calcolati': 0, 'filtrati': 0, 'passati': 0}
        self.modalita_osservativa = False
        self.soglia_carica = 0.30

        self._setup_logging()
        self._load_state()

        # Statistiche per il report
        self.wins = 0
        self.losses = 0
        self.loss_consecutivi = 0   # [CAPSULE] counter sequenze negative
        self._ts_pausa_3loss:  float = 0.0  # [V13.6j] timestamp inizio blocco 3loss
        self._PAUSA_3LOSS_SEC: int   = 120  # [V13.6m] 2 minuti di pausa poi si riprova

        # [V13.6k] Dati mercato esogeni
        self.funding_rate:   float = 0.0    # funding rate corrente (futures)
        self.open_interest:  float = 0.0    # open interest corrente
        self.bid_wall:       float = 0.0    # muro bid più grande (order book)
        self.ask_wall:       float = 0.0    # muro ask più grande (order book)
        self.bid_wall_price: float = 0.0    # prezzo muro bid
        self.ask_wall_price: float = 0.0    # prezzo muro ask
        self._last_market_update: float = 0.0

        # [CAPSULE ENGINE] Inizializzazione
        self.capsule_runtime = CapsuleRuntime(mission_control_url="https://tecnaria-v2.onrender.com")
        log(f"[CAPSULE] Runtime attivo con {len(self.capsule_runtime.capsule)} capsule")

        # [BRIDGE] Inizializza il ponte verso il Mission Control
        self.bridge = MissionControlBridge("https://tecnaria-v2.onrender.com")

        # ============================================================
        # [V13.7] SINAPSI GUARD — rilevatore tempeste di mercato
        # Se vede N loss consecutivi su pattern CONFERMATI,
        # riconosce una TRAPPOLA e sospende il trading.
        # Riprende automaticamente quando la tempesta passa.
        # ============================================================
        self._sg_storia: deque = deque(maxlen=8)   # ultimi 8 trade
        self._sg_tempesta: bool = False             # flag tempesta attiva
        self._sg_ts: float = 0.0                   # timestamp inizio tempesta
        self._sg_SOGLIA: int = 3                   # 3 loss CONFERMATI = tempesta
        self._sg_PAUSA_MIN: float = 180.0           # pausa minima 3 minuti
        self._sg_PAUSA_MAX: float = 600.0           # pausa massima 10 minuti
        # ============================================================

        # [V13.7] QUARANTENA POST-RECONNECT
        # Dopo ogni reconnect WS il bot non sa cosa e successo nel gap.
        # Come un pugile che torna in piedi: deve prima rialzare le braccia.
        self._ws_reconnect_ts: float = 0.0        # timestamp ultimo reconnect
        self._ws_QUARANTENA_SEC: float = 15.0     # 15 secondi di osservazione prima di entrare
        # ============================================================

        # [BRIDGE] Legge la configurazione iniziale e sovrascrive i parametri locali
        config = self.bridge.get_config()
        if config:
            CONFIG['FANTASMA_WR'] = config.get('FANTASMA_WR', CONFIG['FANTASMA_WR'])
            CONFIG['FANTASMA_PNL'] = config.get('FANTASMA_PNL', CONFIG['FANTASMA_PNL'])
            CONFIG['SEED_THRESH_NORMAL'] = config.get('SEED_THRESH_NORMAL', CONFIG['SEED_THRESH_NORMAL'])
            CONFIG['SEED_THRESH_FLAT'] = config.get('SEED_THRESH_FLAT', CONFIG['SEED_THRESH_FLAT'])
            print(f"[Bridge] Config caricata: FANTASMA_WR={CONFIG['FANTASMA_WR']}")
        else:
            print("[Bridge] Usata config locale di default")

        # [BRIDGE] Contatore per aggiornamento periodico della config
        self.config_check_counter = 0

        header = "\n" + "="*80 + "\n"
        header += f"  OVERTOP BASSANO {self.VERSIONE}\n"
        header += "="*80 + "\n"
        header += " 🧠 Oracolo artificiale (BTC, 923k trade)\n"
        header += " 📈 Report ogni 1 minuto, eventi live (entry/exit)\n"
        header += " 🔧 Modalità MECCANICO A BORDO\n"
        header += "="*80 + "\n"
        log(header)
        self.run_logger.write(header)

    def _setup_logging(self):
        handlers = [logging.FileHandler(CONFIG['LOG_FILE'], mode='a')]
        if CONFIG['DEBUG']:
            handlers.append(logging.StreamHandler(sys.stdout))
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(message)s',
            datefmt='%H:%M:%S',
            handlers=handlers
        )

    def processa_tick(self, asset: str, tick: Tick) -> Optional[dict]:
        current_time = tick.timestamp

        self.tick_count += 1
        # NON stampiamo i tick per evitare spam

        if self.emergency_stop:
            return None

        bar = self.verita.aggrega_barra_1s(asset, tick)
        if bar is None:
            return None

        self.bar_count += 1

        # [BRIDGE] Aggiornamento periodico della configurazione (ogni 30 tick ~30 sec)
        self.config_check_counter += 1
        if self.config_check_counter % 30 == 0:
            new_config = self.bridge.get_config()
            if new_config:
                CONFIG['FANTASMA_WR'] = new_config.get('FANTASMA_WR', CONFIG['FANTASMA_WR'])
                CONFIG['FANTASMA_PNL'] = new_config.get('FANTASMA_PNL', CONFIG['FANTASMA_PNL'])
                CONFIG['SEED_THRESH_NORMAL'] = new_config.get('SEED_THRESH_NORMAL', CONFIG['SEED_THRESH_NORMAL'])
                CONFIG['SEED_THRESH_FLAT'] = new_config.get('SEED_THRESH_FLAT', CONFIG['SEED_THRESH_FLAT'])
                # Se volessi aggiornare anche RISK_PER_TRADE, aggiungilo qui
                # [CAPSULE] Sync capsule nuove dal Mission Control
                nuove_capsule = new_config.get('capsules', [])
                if nuove_capsule:
                    self.capsule_runtime.sync_dal_bridge(nuove_capsule)

        regime_valido, range_pct = self.verita.check_regime(asset)
        forza, press, curv = self.verita.forza(asset)
        range_60s = self.verita.calcola_regime_range(asset)

        if asset not in self.switches:
            self.switches[asset] = SwitchBinario()
        modalita = self.switches[asset].update(range_60s)
        params = self.switches[asset].get_params()

        bars_list = list(self.verita.bars_1s.get(asset, []))
        side_int = 1  # solo LONG
        seed_score, seed_comp = self.seed_scorer.calcola(bars_list, side_int)

        # Log barra su JSONL (sempre, ma non in console)
        if self.jsonl_logger:
            bar_record = {
                'tipo': 'bar',
                'timestamp': bar['timestamp'],
                'asset': asset,
                'open': bar['open'],
                'high': bar['high'],
                'low': bar['low'],
                'close': bar['close'],
                'volume': bar['volume'],
                'forza': round(forza, 4),
                'pressione': round(press, 4),
                'curvatura': round(curv, 4),
                'regime_valido': bool(int(regime_valido)),
                'range_pct': round(range_pct, 4),
                'range_60s': round(range_60s, 4),
                'modalita': modalita,
                'seed_score': round(seed_score, 3),
                'seed_componenti': seed_comp,
            }
            self.jsonl_logger.write(bar_record)

        # Gestione posizione aperta
        if asset in self.posizioni:
            if asset in self.early_data:
                early_result = self._check_early_validation(asset, forza, regime_valido, bar)
                if early_result:
                    return early_result

            pos = self.posizioni[asset]

            duration = current_time - pos.entry_time
            R_now = forza / pos.entry_imp if abs(pos.entry_imp) > 0.001 else 0.0
            t_int = int(duration)
            if not pos.r_timeline or pos.r_timeline[-1][0] < t_int:
                pos.r_timeline.append((t_int, round(R_now, 4)))
                if R_now > pos.max_r:
                    pos.max_r = R_now

            exit_signal = self._gestisci_exit_dinamica(asset, pos, current_price=bar['close'], current_time=current_time)
            if exit_signal:
                return self._esegui_uscita(asset, bar['close'], exit_signal, forza, ts=bar['timestamp'])

            exit_reason = self.verita.check_exit_fisico(
                asset, forza, pos, bar['close'],
                hard_sl=pos.hard_sl_din,
                current_time=current_time
            )
            if exit_reason:
                return self._esegui_uscita(asset, bar['close'], exit_reason, forza, ts=bar['timestamp'])

        # Report periodico (ogni 1 minuto)
        if current_time - self.last_report > CONFIG['REPORT_EVERY']:
            self.last_report = current_time
            self._report()

        # Entry logic
        # [V13.7] QUARANTENA POST-RECONNECT — braccia alzate prima di combattere
        if self._ws_reconnect_ts > 0:
            elapsed_reconnect = time.time() - self._ws_reconnect_ts
            if elapsed_reconnect < self._ws_QUARANTENA_SEC:
                rimanenti = int(self._ws_QUARANTENA_SEC - elapsed_reconnect)
                log(f"[{asset}] 🛡️ QUARANTENA: {rimanenti}s alla fine — osservo mercato, non entro.")
                return None
            elif elapsed_reconnect >= self._ws_QUARANTENA_SEC and self._ws_reconnect_ts > 0:
                log(f"[{asset}] ✅ QUARANTENA TERMINATA — bot operativo, braccia alzate.")
                self._ws_reconnect_ts = 0.0  # reset: quarantena finita

        # [V13.7] SINAPSI GUARD check — blocca entry durante tempesta
        if self._sg_check():
            elapsed = int(time.time() - self._sg_ts)
            rimanenti = max(0, int((self._sg_PAUSA_MIN - elapsed) / 60))
            log(f"[{asset}] 🌩️ SINAPSI-GUARD: tempesta attiva — entry bloccata. Riprende tra ~{rimanenti} min.")
            return None

        if not self.posizioni and regime_valido and forza > 0:
            forza_ass = abs(forza)
            seed_score, seed_comp = self.seed_scorer.calcola(bars_list, side_int)
            self.seed_stats['calcolati'] += 1

            # ---- [CAPSULE ENGINE] Valutazione contesto ----
            _ora_utc = datetime.utcnow().hour
            _regime_str = 'normal'
            if range_60s < 0.0015:
                _regime_str = 'lateral'
            elif range_60s < 0.0025:
                _regime_str = 'choppy'
            elif range_60s > 0.006:
                _regime_str = 'trending'

            # [V13.6j] Timeout pausa 3loss: dopo 20 min si riprova
            if self.loss_consecutivi >= 3 and self._ts_pausa_3loss == 0.0:
                self._ts_pausa_3loss = time.time()
            if self.loss_consecutivi >= 3 and self._ts_pausa_3loss > 0:
                elapsed = time.time() - self._ts_pausa_3loss
                if elapsed >= self._PAUSA_3LOSS_SEC:
                    log(f"[{asset}] ✅ PAUSA SEQUENZA TERMINATA ({elapsed/60:.0f}min) — contatore loss azzerato. Bot torna operativo.")
                    self.loss_consecutivi = 0
                    self._ts_pausa_3loss = 0.0

            _ctx_capsule = {
                'regime':           _regime_str,
                'ora_utc':          _ora_utc,
                'modalita':         modalita,
                'forza':            abs(forza),
                'seed':             seed_score,
                'loss_consecutivi': self.loss_consecutivi,
                'asset':            asset,
                # [V13.6k] Dati mercato esogeni
                'funding_rate':     self.funding_rate,
                'open_interest':    self.open_interest,
                'bid_wall':         self.bid_wall,
                'ask_wall':         self.ask_wall,
                'bid_wall_price':   self.bid_wall_price,
                'ask_wall_price':   self.ask_wall_price,
            }
            _cap_override = self.capsule_runtime.valuta(_ctx_capsule)
            self._last_capsule_ids = _cap_override['capsule_attivate']

            if _cap_override['blocca']:
                ragione = _cap_override['blocca_ragione']
                # [V13.6l] Narrativa ricca per ogni tipo di blocco
                if 'drawdown_sequenziale_3loss' in ragione:
                    elapsed_min = int((time.time() - self._ts_pausa_3loss) / 60) if self._ts_pausa_3loss > 0 else 0
                    rimanenti   = max(0, self._PAUSA_3LOSS_SEC // 60 - elapsed_min)
                    log(f"[{asset}] 🛑 PAUSA SEQUENZA NEGATIVA — {self.loss_consecutivi} loss consecutivi."
                        f" Il bot si ferma per proteggere il capitale. Riprende tra ~{rimanenti} min.")
                elif 'regime_lateral' in ragione:
                    log(f"[{asset}] 🚫 REGIME LATERAL — mercato piatto e tossico (WR storico 17%). Entry bloccata.")
                elif 'regime_choppy' in ragione:
                    log(f"[{asset}] 🚫 REGIME CHOPPY — mercato frenetico senza direzione (WR storico 19%). Entry bloccata.")
                elif 'funding_rate' in ragione:
                    log(f"[{asset}] ⚠️ FUNDING RATE ANOMALO ({self.funding_rate:.4%}) — mercato sbilanciato. Entry bloccata.")
                elif 'notte' in ragione:
                    log(f"[{asset}] 🌙 ORA NOTTURNA — fuori da regime trending. WR storico 40%. Entry bloccata.")
                elif 'muro_liquidita' in ragione:
                    log(f"[{asset}] 🧱 MURO LIQUIDITÀ ASK — {self.ask_wall:.1f} BTC a ${self.ask_wall_price:.0f}. Spazio ridotto. Entry bloccata.")
                else:
                    log(f"[{asset}] 🚫 CAPSULA ATTIVA: {ragione} (regime={_regime_str} ora={_ora_utc})")

                self.bridge.log_event(
                    event_type="BLOCK",
                    asset=asset,
                    block_reason=f"CAPSULA:{ragione}",
                    regime=_regime_str,
                    ora=_ora_utc,
                    modalita=modalita,
                    capsule=_cap_override['capsule_attivate'],
                    loss_consecutivi=self.loss_consecutivi,
                    funding_rate=self.funding_rate,
                )
                return None
            # ---- fine CAPSULE ENGINE ----

            base_thresh = CONFIG['SEED_THRESH_FLAT'] if modalita == 'FLAT' else CONFIG['SEED_THRESH_NORMAL']
            if modalita == 'NORMAL':
                seed_thresh = base_thresh
            else:
                seed_thresh = base_thresh

            if seed_score < seed_thresh:
                self.seed_stats['filtrati'] += 1
                return None

            self.seed_stats['passati'] += 1

            direzione = 'LONG'
            veto_rules = CONFIG.get('ASSET_VETO_RULES', {})
            if asset in veto_rules:
                blocked_dirs = veto_rules[asset].get(modalita, [])
                if 'LONG' in blocked_dirs:
                    return None

            size_verita, rischio, hard_sl_din = self.verita.calcola_size_risk_based(
                forza, asset, bar['close'], self.capital
            )
            if size_verita <= 0:
                return None

            # ---- CONSULTAZIONE ORACOLO ----
            oracolo_mult, msg, fingerprint, isvip = self.oracolo.consulta(
                asset, forza, direzione, modalita
            )
            # [BRIDGE] Log blocco FANTASMA
            if "FANTASMA" in msg:
                log(f"[{asset}] 👻 ORACOLO: fingerprint TOSSICA rilevata ({msg}). WR < 30% su questo pattern. Entry bloccata.")
                self.bridge.log_event(
                    event_type="BLOCK",
                    asset=asset,
                    block_reason="FANTASMA",
                    forza=round(forza, 3),
                    modalita=modalita,
                    seed=seed_score,
                    msg=msg
                )
                return None

            veto_wr = CONFIG['NORMAL_VETO_WR'] if modalita == 'NORMAL' else CONFIG['FLAT_VETO_WR']
            if hasattr(self.oracolo, '_last_wr') and self.oracolo._last_wr < veto_wr:
                log(f"[{asset}] 🔴 VETO-WR: win rate recente troppo basso — entry bloccata per proteggere il capitale.")
                self.bridge.log_event(
                    event_type="BLOCK",
                    asset=asset,
                    block_reason="VETO-WR",
                    forza=round(forza, 3),
                    modalita=modalita,
                    seed=seed_score,
                    last_wr=self.oracolo._last_wr
                )
                return None

            # [CAPSULE] Applica moltiplicatore size dalle capsule (es. BOOST_TRENDING)
            _cap_size_mult = _cap_override.get('size_mult', 1.0) if '_cap_override' in dir() else 1.0
            size_finale = size_verita * oracolo_mult * _cap_size_mult
            rischio_finale = rischio * oracolo_mult * _cap_size_mult
            if _cap_size_mult != 1.0:
                if _cap_size_mult > 1.0:
                    log(f"[{asset}] 🚀 BOOST SIZE x{_cap_size_mult:.2f} — regime {_regime_str} favorevole (WR storico >70%). Size aumentata.")
                else:
                    log(f"[{asset}] ⬇️ SIZE RIDOTTA x{_cap_size_mult:.2f} — condizioni sfavorevoli rilevate.")

            if size_finale <= 0:
                return None

            # ---- FILTRO FORZA MINIMA ----
            if modalita == 'FLAT':
                if forza_ass < CONFIG['FLAT_MIN_FORZA'] or seed_score < CONFIG['SEED_THRESH_FLAT']:
                    return None
            elif modalita == 'NORMAL':
                if forza_ass < CONFIG['NORMAL_MIN_FORZA']:
                    return None   # [V13.6m] FIX: blocca trade forza < 0.55 in NORMAL
            # --------------------------------

            return self._esegui_ingresso(
                asset, bar, forza, press,
                size_finale, rischio_finale,
                oracolo_mult, fingerprint, msg,
                modalita, seed_score,
                hard_sl_din, isvip,
                regime=_regime_str
            )

        return None

    def _gestisci_exit_dinamica(self, asset: str, pos: Posizione, current_price: float, current_time: float) -> Optional[str]:
        pnl_corrente = (current_price - pos.entry_price) * pos.direction * pos.size
        if pos.rischio_assunto > 0:
            R_corrente = pnl_corrente / pos.rischio_assunto
        else:
            R_corrente = 0.0

        tp_threshold = CONFIG['TAKE_PROFIT_R']
        if abs(pos.entry_imp) > 0.6:
            tp_threshold = 0.65

        if not pos.partial_profit_taken and R_corrente >= tp_threshold:
            pos.partial_profit_taken = True
            pos.partial_price = current_price
            pnl_parziale = pnl_corrente * 0.5
            self.capital += pnl_parziale
            pos.size /= 2
            pos.rischio_assunto /= 2
            log_msg = f"[{asset}] [TAKE PROFIT PARZIALE] chiuso 50% a {current_price:.2f}, PnL parziale ${pnl_parziale:+.2f}"
            log(log_msg)
            self.run_logger.write(log_msg)
            return None

        if pos.partial_profit_taken and pos.max_r > 0:
            if R_corrente < pos.max_r * (1 - CONFIG['TRAILING_STEP']):
                return "TRAILING_STOP"

        return None

    def _esegui_ingresso(self, asset, bar, forza, pressione, size_finale, rischio_finale,
                         mult, fingerprint, msg, modalita, seed_score, hard_sl_din, isvip,
                         regime='unknown') -> dict:
        direction = 1
        pos = Posizione(
            entry_regime   = regime,
            asset          = asset,
            direction      = direction,
            entry_price    = bar['close'],
            entry_imp      = forza,
            entry_pressure = pressione,
            size           = size_finale,
            entry_time     = bar['timestamp'],
            rischio_assunto= rischio_finale,
            oracolo_mult   = mult,
            fingerprint    = fingerprint,
            modalita       = modalita,
            seed_score     = seed_score,
            isvip          = isvip,
            hard_sl_din    = hard_sl_din,
            r_timeline     = [],
            max_r          = 0.0,
            partial_profit_taken = False,
        )
        self.posizioni[asset] = pos
        self.trade_count += 1

        self.early_data[asset] = {
            'direction'    : direction,
            'start_time'   : bar['timestamp'],
            'F_values'     : [],
            'regime_values': [],
            'validated'    : False,
        }

        self._save_state()

        # Usa il logger per l'entry (stampa in console)
        self.run_logger.entry(asset, bar['close'], forza, size_finale, mult,
                              modalita, seed_score, msg)

        if self.jsonl_logger:
            record = {
                'tipo': 'entry',
                'timestamp': bar['timestamp'],
                'asset': asset,
                'direction': 'LONG',
                'price': bar['close'],
                'size': size_finale,
                'forza': round(forza, 3),
                'pressione': round(pressione, 3),
                'oracolo_mult': mult,
                'fingerprint': fingerprint,
                'modalita': modalita,
                'seed_score': seed_score,
                'msg': msg,
            }
            self.jsonl_logger.write(record)

        # [BRIDGE] Log dell'entry
        self.bridge.log_event(
            event_type="ENTRY",
            asset=asset,
            direction="LONG",
            seed=seed_score,
            forza=round(forza, 3),
            modalita=modalita,
            price=bar['close'],
            size=size_finale,
            mult=mult,
            fingerprint=fingerprint
        )

        return {
            'type'       : 'ENTRY',
            'asset'      : asset,
            'direction'  : 'LONG',
            'price'      : bar['close'],
            'size'       : size_finale,
            'forza'      : forza,
            'oracolo_mult': mult,
            'fingerprint': fingerprint,
            'modalita'   : modalita,
            'seed_score' : seed_score,
            'isvip'      : isvip,
            'time'       : bar['timestamp'],
        }

    def _esegui_uscita(self, asset, price, reason, forza_uscita, ts=None) -> dict:
        pos = self.posizioni.pop(asset)
        if asset in self.early_data:
            del self.early_data[asset]

        pnl = (price - pos.entry_price) * pos.direction * pos.size
        self.capital += pnl
        exit_time = ts if ts is not None else time.time()
        self.equity_curve.append((exit_time, self.capital))
        r_r = pnl / pos.rischio_assunto if pos.rischio_assunto > 0 else 0
        duration = exit_time - pos.entry_time

        # Aggiorna statistiche
        if pnl > 0:
            self.wins += 1
            self.loss_consecutivi = 0   # reset sequenza
            self._ts_pausa_3loss  = 0.0 # [V13.6j] reset timer pausa
            # [CAPSULE] Registra esito per aggiornare statistiche capsule
            if hasattr(self, '_last_capsule_ids'):
                self.capsule_runtime.registra_esito(self._last_capsule_ids, win=True)
        elif pnl < 0:
            self.losses += 1
            self.loss_consecutivi += 1  # incrementa sequenza
            # [CAPSULE] Registra esito per aggiornare statistiche capsule
            if hasattr(self, '_last_capsule_ids'):
                self.capsule_runtime.registra_esito(self._last_capsule_ids, win=False)

        trade = {
            'type'        : 'EXIT',
            'asset'       : asset,
            'direction'   : 'LONG',
            'entry_price' : pos.entry_price,
            'exit_price'  : price,
            'pnl'         : pnl,
            'risk_reward' : r_r,
            'reason'      : reason,
            'duration'    : duration,
            'oracolo_mult': pos.oracolo_mult,
            'fingerprint' : pos.fingerprint,
            'modalita'    : pos.modalita,
            'seed_score'  : pos.seed_score,
            'isvip'       : pos.isvip,
            'time'        : exit_time,
            'r_timeline'  : pos.r_timeline,
            'partial_profit_taken': pos.partial_profit_taken,
        }
        self.trades.append(trade)
        self._save_state()

        outcome = Outcome(
            pnl_pct=pnl / pos.size * 100,
            pnl_dollari=pnl,
            duration=duration,
            reason=reason,
            timestamp=pos.entry_time
        )
        self.oracolo.apprendi(asset, pos.entry_imp, 'LONG', pos.modalita, outcome)

        # ---- MAL1 APPRENDIMENTO: pattern che hanno causato MAL1 vengono penalizzati ----
        if reason in ('MAL1_STOP', 'MAL1_TIMEOUT') and pos.fingerprint:
            log(f"[{asset}] 🧠 MAL1-LEARN: fingerprint {pos.fingerprint[:20]}... segnata come TOSSICA nell'oracolo.")
            # Forza l'oracolo a registrare questo come loss pesante
            outcome_mal1 = Outcome(
                pnl_pct    = -5.0,
                pnl_dollari= pnl,
                duration   = duration,
                reason     = reason,
                timestamp  = pos.entry_time
            )
            self.oracolo.apprendi(asset, pos.entry_imp, 'LONG', pos.modalita, outcome_mal1)
        # ---------------------------------------------------------------------------------

        corretto = False
        if pnl > 0 and pos.oracolo_mult > 1.0:
            corretto = True
        elif pnl < 0 and pos.oracolo_mult < 1.0:
            corretto = True
        self.oracolo.aggiorna_meta(corretto)

        # [V13.7] SINAPSI GUARD: aggiorna storia trade dopo ogni uscita
        # Estrae tipo oracolo dalla stringa (CONFERMATO/NEUTRO/FANTASMA/IGNOTO)
        _sg_tipo = 'IGNOTO'
        if hasattr(pos, 'oracolo_mult'):
            if pos.oracolo_mult > 1.1:
                _sg_tipo = 'CONFERMATO'
            elif pos.oracolo_mult == 1.0:
                _sg_tipo = 'NEUTRO'
            elif pos.oracolo_mult < 1.0:
                _sg_tipo = 'FANTASMA'
        self._sg_aggiorna(pnl, _sg_tipo, reason)
        # -------------------------------------------------------

        if self.jsonl_logger:
            record = {
                'tipo': 'exit',
                'timestamp': exit_time,
                'asset': asset,
                'direction': 'LONG',
                'entry_price': pos.entry_price,
                'exit_price': price,
                'pnl': round(pnl, 4),
                'R': round(r_r, 4),
                'reason': reason,
                'duration': round(duration, 2),
                'oracolo_mult': pos.oracolo_mult,
                'fingerprint': pos.fingerprint,
                'modalita': pos.modalita,
                'seed_score': pos.seed_score,
                'r_timeline': pos.r_timeline,
                'partial_profit': pos.partial_profit_taken,
            }
            self.jsonl_logger.write(record)

        self.run_logger.exit(asset, price, reason, pnl, r_r, duration,
                             pos.modalita, self.capital, pos.partial_profit_taken)

        # [BRIDGE] Log dell'exit
        self.bridge.log_event(
            event_type="EXIT",
            asset=asset,
            direction="LONG",
            pnl=round(pnl, 4),
            r_r=round(r_r, 3),
            reason=reason,
            duration=round(duration, 1),
            modalita=pos.modalita,
            exit_price=price,
            # [V13.6h] Campi contestuali per analisi capsule
            regime=getattr(pos, 'entry_regime', 'unknown'),
            forza=round(abs(pos.entry_imp), 3),
            seed=round(pos.seed_score, 3),
            loss_consecutivi=self.loss_consecutivi,
            entry_ts=pos.entry_time,
            ora=__import__('datetime').datetime.utcnow().hour,
        )

        return trade

    def aggiorna_dati_mercato(self, funding: float, oi: float,
                               bid_wall: float, bid_price: float,
                               ask_wall: float, ask_price: float):
        """[V13.6k] Aggiorna dati mercato esogeni dal secondo WebSocket."""
        self.funding_rate   = funding
        self.open_interest  = oi
        self.bid_wall       = bid_wall
        self.bid_wall_price = bid_price
        self.ask_wall       = ask_wall
        self.ask_wall_price = ask_price
        self._last_market_update = time.time()

        # Manda al Mission Control ogni aggiornamento
        self.bridge.log_event(
            event_type   = "MARKET_DATA",
            funding_rate = round(funding, 6),
            open_interest= round(oi, 2),
            bid_wall     = round(bid_wall, 2),
            bid_wall_price=round(bid_price, 2),
            ask_wall     = round(ask_wall, 2),
            ask_wall_price=round(ask_price, 2),
        )

    def _report(self):
        """Stampa un report riassuntivo (chiamato ogni REPORT_EVERY secondi)."""
        roi = ((self.capital - self.capital_iniziale) / self.capital_iniziale * 100)
        trades_total = len([t for t in self.trades if t['type'] == 'EXIT'])
        if trades_total == 0:
            self.run_logger.report(self.capital, roi, 0, 0, 0,
                                   self.posizioni.get('BTCUSDC'), self.verita.bars_1s.get('BTCUSDC', [{}])[-1].get('close', 0) if self.verita.bars_1s.get('BTCUSDC') else 0)
        else:
            self.run_logger.report(self.capital, roi, trades_total, self.wins, self.losses,
                                   self.posizioni.get('BTCUSDC'), self.verita.bars_1s.get('BTCUSDC', [{}])[-1].get('close', 0) if self.verita.bars_1s.get('BTCUSDC') else 0)

        # [BRIDGE] Log del report
        self.bridge.log_event(
            event_type="REPORT",
            capital=round(self.capital, 2),
            trades_total=trades_total,
            wins=self.wins,
            losses=self.losses,
            win_rate=round(self.wins/trades_total*100,1) if trades_total>0 else 0
        )

    def _log_stats(self, asset: str):
        # Non usiamo più questo metodo per evitare doppi report
        pass

    def _print_dashboard(self, asset, price, forza, regime, range_pct, modalita):
        # Disabilitata per evitare spam
        pass

    def _log_sinapsi_status(self):
        pass

    # ============================================================
    # [V13.7] SINAPSI GUARD — rilevatore tempeste di mercato
    # ============================================================

    def _sg_aggiorna(self, pnl: float, oracolo_tipo: str, reason: str):
        """Aggiorna la storia trade e rileva tempeste di mercato."""
        self._sg_storia.append({
            'pnl': pnl,
            'tipo': oracolo_tipo,
            'reason': reason,
            'ts': time.time(),
        })
        # Conta loss consecutivi su CONFERMATI/NEUTRI (pattern che dovevano vincere)
        loss_confermati = 0
        for r in reversed(list(self._sg_storia)):
            if r['pnl'] < 0 and r['tipo'] in ('CONFERMATO', 'NEUTRO'):
                loss_confermati += 1
            else:
                break
        if not self._sg_tempesta and loss_confermati >= self._sg_SOGLIA:
            self._sg_tempesta = True
            self._sg_ts = time.time()
            log(f"[SINAPSI-GUARD] 🌩️ TEMPESTA RILEVATA — {loss_confermati} loss su pattern confermati. "
                f"Trading sospeso. Riprende in max {self._sg_PAUSA_MAX/60:.0f} min.")

    def _sg_check(self) -> bool:
        """True se il trading e BLOCCATO dalla tempesta."""
        if not self._sg_tempesta:
            return False
        elapsed = time.time() - self._sg_ts
        if elapsed < self._sg_PAUSA_MIN:
            return True
        # Dopo pausa minima: un trade vincente = tempesta finita
        storia = list(self._sg_storia)
        if storia and storia[-1]['pnl'] > 0:
            self._sg_tempesta = False
            self._sg_ts = 0.0
            log("[SINAPSI-GUARD] ✅ TEMPESTA PASSATA — trading ripreso.")
            return False
        # Pausa massima scaduta: forza ripresa
        if elapsed >= self._sg_PAUSA_MAX:
            self._sg_tempesta = False
            self._sg_ts = 0.0
            log("[SINAPSI-GUARD] ⏰ PAUSA MASSIMA SCADUTA — ripresa forzata.")
            return False
        return True

    # ============================================================

    def _check_early_validation(self, asset, forza, regime_valido, bar):
        early = self.early_data[asset]
        if not early['validated']:
            early['F_values'].append(forza)
            early['regime_values'].append(regime_valido)
            if len(early['F_values']) >= CONFIG['EARLY_WINDOW']:
                direction = early['direction']
                F_vals = early['F_values']
                EA5 = sum(direction * f for f in F_vals)
                dir_coh = sum(1 for f in F_vals if np.sign(f) == direction) / CONFIG['EARLY_WINDOW']
                regime_break = not all(early['regime_values'])
                if (EA5 < CONFIG['EA_MIN'] or dir_coh < CONFIG['DIR_COH_MIN'] or regime_break):
                    result = self._esegui_uscita(asset, bar['close'], 'KILL_EARLY', forza, ts=bar['timestamp'])
                    if asset in self.verita.forza_storia:
                        self.verita.forza_storia[asset].clear()
                    return result
                early['validated'] = True
        return None

    def _save_state(self):
        self.oracolo._salva()
        state = {
            'capital'         : self.capital,
            'capital_iniziale': self.capital_iniziale,
            'posizioni'       : {
                k: {
                    'asset'          : v.asset,
                    'direction'      : v.direction,
                    'entry_price'    : v.entry_price,
                    'entry_imp'      : v.entry_imp,
                    'entry_pressure' : v.entry_pressure,
                    'size'           : v.size,
                    'entry_time'     : v.entry_time,
                    'rischio_assunto': v.rischio_assunto,
                    'oracolo_mult'   : v.oracolo_mult,
                    'fingerprint'    : v.fingerprint,
                    'modalita'       : v.modalita,
                    'seed_score'     : v.seed_score,
                    'isvip'          : v.isvip,
                    'hard_sl_din'    : v.hard_sl_din,
                    'r_timeline'     : v.r_timeline,
                    'max_r'          : v.max_r,
                    'partial_profit_taken': v.partial_profit_taken,
                } for k, v in self.posizioni.items()
            },
            'bars_1s'      : {k: list(v) for k, v in self.verita.bars_1s.items()},
            'trades'       : self.trades[-2000:],
            'equity_curve' : self.equity_curve[-5000:],
            'trade_count'  : self.trade_count,
            'emergency_stop': self.emergency_stop,
            'switch_state' : {
                asset: {
                    'current'  : sw.current,
                    'ema_range': sw.ema_range,
                    'switches' : sw.switches,
                }
                for asset, sw in self.switches.items()
            },
            'timestamp': datetime.now().isoformat(),
            'wins': self.wins,
            'losses': self.losses,
        }
        temp = CONFIG['STATE_FILE'] + '.tmp'
        with open(temp, 'wb') as f:
            pickle.dump(state, f)
        os.replace(temp, CONFIG['STATE_FILE'])

    def _load_state(self):
        if not os.path.exists(CONFIG['STATE_FILE']):
            return
        try:
            with open(CONFIG['STATE_FILE'], 'rb') as f:
                state = pickle.load(f)
            self.capital = state['capital']
            self.capital_iniziale = state.get('capital_iniziale', self.capital)
            for k, v in state.get('posizioni', {}).items():
                pos_data = v.copy()
                pos_data['isvip'] = pos_data.get('isvip', False)
                pos_data['hard_sl_din'] = pos_data.get('hard_sl_din', None)
                pos_data['r_timeline'] = pos_data.get('r_timeline', [])
                pos_data['max_r'] = pos_data.get('max_r', 0.0)
                pos_data['partial_profit_taken'] = pos_data.get('partial_profit_taken', False)
                self.posizioni[k] = Posizione(**pos_data)
            self.trades = state.get('trades', [])
            self.equity_curve = state.get('equity_curve', [])
            self.wins = state.get('wins', 0)
            self.losses = state.get('losses', 0)
            for asset, bars in state.get('bars_1s', {}).items():
                self.verita.bars_1s[asset] = deque(bars, maxlen=CONFIG['WINDOW_PRESS'] * 4)

            self.switches = {}
            log(f"[V13.6] Stato caricato: ${self.capital:.2f} | {len(self.trades)} trade")
        except Exception as e:
            log(f"[V13.6] Errore caricamento stato: {e}")

    def emergency_close_all(self, current_prices: Dict[str, float]):
        self.emergency_stop = True
        for asset, pos in list(self.posizioni.items()):
            if asset in current_prices:
                price = current_prices[asset]
                self._esegui_uscita(asset, price, 'EMERGENCY', 0.0, ts=time.time())
        self._save_state()
        log("[EMERGENCY] Tutte le posizioni chiuse")

# ============================================================
# WEBSOCKET HANDLER (identico)
# ============================================================

async def market_data_handler(bot: OvertopV13_5):
    """[V13.6k] Stream secondario: funding rate, open interest, order book."""
    import aiohttp

    async def fetch_funding_oi():
        """Fetch funding rate e open interest via REST ogni 30s."""
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    # Funding rate
                    async with session.get(
                        "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            d = await r.json()
                            funding = float(d.get('lastFundingRate', 0))
                            bot.funding_rate = funding
                            if abs(funding) > 0.0003:
                                log(f"[MARKET] ⚠️ Funding rate elevato: {funding:.4%}")

                    # Open Interest
                    async with session.get(
                        "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            d = await r.json()
                            oi = float(d.get('openInterest', 0))
                            bot.open_interest = oi

            except Exception as e:
                if CONFIG.get('DEBUG'):
                    log(f"[MARKET] Errore fetch: {e}")
            await asyncio.sleep(15)

    async def fetch_orderbook():
        """Fetch order book top 20 livelli ogni 10s — cerca i muri."""
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.binance.com/api/v3/depth?symbol=BTCUSDC&limit=20",
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            d = await r.json()
                            bids = [(float(p), float(q)) for p, q in d.get('bids', [])]
                            asks = [(float(p), float(q)) for p, q in d.get('asks', [])]

                            if bids:
                                max_bid = max(bids, key=lambda x: x[1])
                                bot.bid_wall       = max_bid[1]
                                bot.bid_wall_price = max_bid[0]

                            if asks:
                                max_ask = max(asks, key=lambda x: x[1])
                                bot.ask_wall       = max_ask[1]
                                bot.ask_wall_price = max_ask[0]

                            bot._last_market_update = time.time()

            except Exception as e:
                if CONFIG.get('DEBUG'):
                    log(f"[ORDERBOOK] Errore: {e}")
            await asyncio.sleep(5)

    # Lancia entrambi i task in parallelo
    await asyncio.gather(
        fetch_funding_oi(),
        fetch_orderbook(),
    )


async def websocket_handler(bot: OvertopV13_5):
    assets = CONFIG['ASSETS']
    if len(assets) == 1:
        uri = f"{CONFIG['WS_BASE']}/ws/{assets[0].lower()}@aggTrade"
    else:
        streams = "/".join(f"{a.lower()}@aggTrade" for a in assets)
        uri = f"{CONFIG['WS_BASE']}/stream?streams={streams}"
    log(f"[WS] Connessione a: {uri}")

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                log("[WS] Connesso — streaming attivo")
                # [V13.7] QUARANTENA: segnala al bot che c'e' stato un reconnect
                bot._ws_reconnect_ts = time.time()
                log(f"[WS] 🛡️ Quarantena post-reconnect attiva per {bot._ws_QUARANTENA_SEC:.0f}s — osservazione mercato.")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if 'data' in data:
                            data = data['data']
                        sym = data.get('s', '')
                        if sym not in assets:
                            continue
                        price = float(data['p'])
                        volume = float(data.get('q', 0))
                        ts = data.get('T', time.time() * 1000) / 1000.0
                        tick = Tick(price=price, volume=volume, timestamp=ts)
                        bot.processa_tick(sym, tick)
                    except Exception as e:
                        if CONFIG['DEBUG']:
                            log(f"[WS] Errore processing: {e}")
        except websockets.exceptions.ConnectionClosed:
            log("[WS] Connessione chiusa — retry in 2s...")
            await asyncio.sleep(2)
        except Exception as e:
            log(f"[WS] Errore: {e} — retry in 10s...")
            await asyncio.sleep(5)

# ============================================================
# SHUTDOWN
# ============================================================

def signal_handler(bot: OvertopV13_5):
    log("\n[SHUTDOWN] Salvataggio stato finale...")
    bot._save_state()
    bot.oracolo._salva()
    bot.jsonl_logger.close()
    bot.run_logger.close()
    sys.exit(0)

# ============================================================
# MAIN
# ============================================================

def main():
    bot = OvertopV13_5()

    import signal
    signal.signal(signal.SIGINT,  lambda s, f: signal_handler(bot))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(bot))

    async def timeout_watchdog():
        """[V13.6m] Watchdog indipendente: controlla timeout 3loss ogni 30s.
        Non dipende dai tick di mercato — garantisce reset anche in mercati fermi."""
        while True:
            await asyncio.sleep(10)
            try:
                if bot.loss_consecutivi >= 3 and bot._ts_pausa_3loss > 0:
                    elapsed = time.time() - bot._ts_pausa_3loss
                    if elapsed >= bot._PAUSA_3LOSS_SEC:
                        log(f"[WATCHDOG] ✅ PAUSA SEQUENZA TERMINATA ({elapsed/60:.0f}min) "
                            f"— contatore loss azzerato. Bot torna operativo.")
                        bot.loss_consecutivi = 0
                        bot._ts_pausa_3loss  = 0.0
                    else:
                        rimanenti = int((bot._PAUSA_3LOSS_SEC - elapsed) / 60)
                        log(f"[WATCHDOG] 🛑 Pausa 3loss — riprende tra ~{rimanenti} min")
            except Exception as e:
                log(f"[WATCHDOG] Errore: {e}")

    async def main_async():
        await asyncio.gather(
            websocket_handler(bot),
            market_data_handler(bot),
            timeout_watchdog(),
        )

    # [V13.8] Loop infinito — restart automatico dopo qualsiasi crash
    while True:
        try:
            asyncio.run(main_async())
        except KeyboardInterrupt:
            signal_handler(bot)
            break
        except Exception as e:
            log(f"[MAIN] ⚠️ Crash asyncio: {e} — restart in 15s...")
            import traceback
            traceback.print_exc()
            time.sleep(15)
            log("[MAIN] 🔄 Riavvio loop principale...")

if __name__ == "__main__":
    main()
