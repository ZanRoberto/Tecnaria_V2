"""
OVERTOP V16 — COMPARTO ENGINE
================================
Il mercato si dichiara → assetto completo in 1 tick.

Non capsule singole. Comparti precalibrati.
Ogni comparto è un setup completo: soglie, size, direzioni, capsule.
Il sistema non insegue — aspetta, riconosce, switcha.

Flusso:
  RegimeDetector → dichiara mercato
  CompartoEngine → switcha assetto completo
  Skills         → leggono parametri del comparto attivo
  Capsule        → già calibrate per quel comparto
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# DEFINIZIONE COMPARTI
# ═══════════════════════════════════════════════════════════════

@dataclass
class Comparto:
    """
    Un assetto completo per un tipo di mercato dichiarato.
    Contiene tutto: soglie, size, direzioni permesse, capsule da attivare.
    """
    nome:           str
    descrizione:    str
    colore:         str    # per dashboard

    # Entry
    soglia_base:    float
    soglia_min:     float
    size_default:   float
    size_max:       float

    # Direzioni permesse
    long_ok:        bool = True
    short_ok:       bool = False   # default: no SHORT finché no dati

    # Exit
    exit_min_secs:  int   = 30
    exit_max_secs:  int   = 1800
    stop_loss_pct:  float = 0.02

    # Condizioni di attivazione
    regimi:         list  = field(default_factory=list)   # regimi che attivano
    volatilita:     list  = field(default_factory=list)   # BASSA|MEDIA|ALTA
    trend:          list  = field(default_factory=list)   # UP|DOWN|SIDEWAYS

    # Apprendimento
    trades_osservati: int  = 0
    wins:             int  = 0
    losses:           int  = 0
    pnl_totale:       float = 0.0
    ts_attivazione:   float = 0.0
    ts_switch:        float = 0.0

    def wr(self) -> float:
        tot = self.wins + self.losses
        return self.wins / tot if tot > 0 else 0.0

    def registra_trade(self, pnl: float):
        self.trades_osservati += 1
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.pnl_totale += pnl

    def to_dict(self) -> dict:
        return {
            "nome":            self.nome,
            "descrizione":     self.descrizione,
            "colore":          self.colore,
            "soglia_base":     self.soglia_base,
            "soglia_min":      self.soglia_min,
            "size_default":    self.size_default,
            "long_ok":         self.long_ok,
            "short_ok":        self.short_ok,
            "exit_max_secs":   self.exit_max_secs,
            "stop_loss_pct":   self.stop_loss_pct,
            "trades":          self.trades_osservati,
            "wr":              round(self.wr() * 100, 1),
            "pnl":             round(self.pnl_totale, 2),
            "attivo_da":       round(time.time() - self.ts_attivazione) if self.ts_attivazione else 0,
        }


# ═══════════════════════════════════════════════════════════════
# COMPARTI PREDEFINITI
# ═══════════════════════════════════════════════════════════════

COMPARTI = {

    "DIFENSIVO": Comparto(
        nome         = "DIFENSIVO",
        descrizione  = "RANGING alta volatilità — aspetto, non gioco",
        colore       = "#6b7280",
        soglia_base  = 55,
        soglia_min   = 52,
        size_default = 0.2,
        size_max     = 0.3,
        long_ok      = True,
        short_ok     = False,
        exit_min_secs= 20,
        exit_max_secs= 300,
        stop_loss_pct= 0.015,
        regimi       = ["RANGING"],
        volatilita   = ["ALTA"],
        trend        = ["SIDEWAYS"],
    ),

    "NEUTRO": Comparto(
        nome         = "NEUTRO",
        descrizione  = "RANGING normale — entro solo su setup pulito",
        colore       = "#3b82f6",
        soglia_base  = 50,
        soglia_min   = 48,
        size_default = 0.3,
        size_max     = 0.5,
        long_ok      = True,
        short_ok     = False,
        exit_min_secs= 30,
        exit_max_secs= 900,
        stop_loss_pct= 0.02,
        regimi       = ["RANGING"],
        volatilita   = ["BASSA", "MEDIA"],
        trend        = ["SIDEWAYS", "UP", "DOWN"],
    ),

    "ATTACCO": Comparto(
        nome         = "ATTACCO",
        descrizione  = "EXPLOSIVE — colpisco forte e veloce",
        colore       = "#00d97a",
        soglia_base  = 44,
        soglia_min   = 40,
        size_default = 0.5,
        size_max     = 0.8,
        long_ok      = True,
        short_ok     = False,
        exit_min_secs= 15,
        exit_max_secs= 180,
        stop_loss_pct= 0.025,
        regimi       = ["EXPLOSIVE"],
        volatilita   = ["ALTA", "MEDIA"],
        trend        = ["UP", "SIDEWAYS", "DOWN"],
    ),

    "TRENDING_BULL": Comparto(
        nome         = "TRENDING_BULL",
        descrizione  = "Trend rialzista — cavalco l'onda",
        colore       = "#00ff88",
        soglia_base  = 46,
        soglia_min   = 42,
        size_default = 0.4,
        size_max     = 0.7,
        long_ok      = True,
        short_ok     = False,
        exit_min_secs= 60,
        exit_max_secs= 1800,
        stop_loss_pct= 0.02,
        regimi       = ["TRENDING_BULL"],
        volatilita   = ["BASSA", "MEDIA", "ALTA"],
        trend        = ["UP"],
    ),

    "TRENDING_BEAR": Comparto(
        nome         = "TRENDING_BEAR",
        descrizione  = "Trend ribassista — SHORT solo con dati reali",
        colore       = "#ff3355",
        soglia_base  = 50,
        soglia_min   = 46,
        size_default = 0.3,
        size_max     = 0.5,
        long_ok      = False,
        short_ok     = True,   # abilitato SOLO se fp_real >= 5
        exit_min_secs= 30,
        exit_max_secs= 600,
        stop_loss_pct= 0.02,
        regimi       = ["TRENDING_BEAR"],
        volatilita   = ["BASSA", "MEDIA", "ALTA"],
        trend        = ["DOWN"],
    ),
}


# ═══════════════════════════════════════════════════════════════
# COMPARTO ENGINE
# ═══════════════════════════════════════════════════════════════

class CompartoEngine:
    """
    Il mercato si dichiara → assetto completo in 1 tick.

    Responsabilità:
    1. Legge regime + volatilità + trend dal kernel
    2. Seleziona il comparto giusto
    3. Switcha i parametri di tutte le skill in 1 operazione
    4. Logga ogni switch con motivo
    5. Impara dai risultati — se un comparto perde → si irrigidisce
    """

    def __init__(self):
        self._attivo:   str      = "NEUTRO"
        self._lock      = __import__("threading").RLock()
        self._switch_log: list   = []
        self._last_regime: str   = ""
        self._last_vol:    str   = ""
        self._last_trend:  str   = ""
        self._switch_cooldown: float = 0.0  # evita switch troppo rapidi

    def on_tick(self, regime: str, volatilita: str, trend: str, skills: dict) -> str:
        """
        Chiamato ogni tick con il contesto di mercato.
        Restituisce il nome del comparto attivo.
        """
        with self._lock:
            # Cooldown switch — minimo 60 secondi tra switch
            if time.time() - self._switch_cooldown < 60:
                return self._attivo

            # Stessa condizione → nessun switch
            if (regime == self._last_regime and
                volatilita == self._last_vol and
                trend == self._last_trend):
                return self._attivo

            # Trova il comparto giusto
            nuovo = self._seleziona(regime, volatilita, trend)

            if nuovo != self._attivo:
                self._switcha(nuovo, regime, volatilita, trend, skills)

            self._last_regime = regime
            self._last_vol    = volatilita
            self._last_trend  = trend
            return self._attivo

    def _seleziona(self, regime: str, volatilita: str, trend: str) -> str:
        """Seleziona il comparto migliore per il contesto attuale."""

        # Priorità: regime specifico prima di generici
        for nome, comp in COMPARTI.items():
            regime_ok = not comp.regimi or regime in comp.regimi
            vol_ok    = not comp.volatilita or volatilita in comp.volatilita
            trend_ok  = not comp.trend or trend in comp.trend
            if regime_ok and vol_ok and trend_ok:
                return nome

        return "NEUTRO"

    def _switcha(self, nuovo: str, regime: str, vol: str, trend: str, skills: dict):
        """Switcha assetto completo — applica parametri a tutte le skill."""
        vecchio = self._attivo
        comp    = COMPARTI.get(nuovo, COMPARTI["NEUTRO"])

        # Applica parametri alle skill
        entry = skills.get("ENTRY")
        if entry:
            entry.set_param("soglia_base",    comp.soglia_base,    fonte=f"COMPARTO_{nuovo}")
            entry.set_param("soglia_min",     comp.soglia_min,     fonte=f"COMPARTO_{nuovo}")
            entry.set_param("size_default",   comp.size_default,   fonte=f"COMPARTO_{nuovo}")
            entry.set_param("long_ok",        comp.long_ok,        fonte=f"COMPARTO_{nuovo}")
            entry.set_param("short_ok",       comp.short_ok,       fonte=f"COMPARTO_{nuovo}")

        exit_sk = skills.get("EXIT")
        if exit_sk:
            exit_sk.set_param("min_hold_secs",  comp.exit_min_secs,  fonte=f"COMPARTO_{nuovo}")
            exit_sk.set_param("max_hold_secs",  comp.exit_max_secs,  fonte=f"COMPARTO_{nuovo}")
            exit_sk.set_param("stop_loss_pct",  comp.stop_loss_pct,  fonte=f"COMPARTO_{nuovo}")

        self._attivo = nuovo
        comp.ts_attivazione = time.time()
        self._switch_cooldown = time.time()

        log_entry = {
            "ts":     time.strftime("%H:%M:%S"),
            "da":     vecchio,
            "a":      nuovo,
            "motivo": f"{regime}|{vol}|{trend}",
            "col":    comp.colore,
        }
        self._switch_log.append(log_entry)
        if len(self._switch_log) > 50:
            self._switch_log.pop(0)

        log.info(f"[COMPARTO] {vecchio} → {nuovo} | {regime}|{vol}|{trend} "
                 f"soglia={comp.soglia_base} size={comp.size_default} "
                 f"long={comp.long_ok} short={comp.short_ok}")

    def on_trade_closed(self, pnl: float):
        """Registra risultato nel comparto attivo."""
        with self._lock:
            comp = COMPARTI.get(self._attivo)
            if comp:
                comp.registra_trade(pnl)
                # Se comparto perde troppo → irrigidisce soglia
                if comp.trades_osservati >= 5 and comp.wr() < 0.30:
                    comp.soglia_base = min(comp.soglia_base + 2, 65)
                    log.warning(f"[COMPARTO] {self._attivo} WR={comp.wr():.0%} → "
                                f"soglia alzata a {comp.soglia_base}")
                # Se comparto funziona → ammorbidisce soglia
                elif comp.trades_osservati >= 10 and comp.wr() >= 0.60:
                    comp.soglia_base = max(comp.soglia_base - 1, 38)
                    log.info(f"[COMPARTO] {self._attivo} WR={comp.wr():.0%} → "
                             f"soglia abbassata a {comp.soglia_base}")

    def get_attivo(self) -> dict:
        with self._lock:
            comp = COMPARTI.get(self._attivo, COMPARTI["NEUTRO"])
            return comp.to_dict()

    def get_switch_log(self) -> list:
        with self._lock:
            return list(reversed(self._switch_log[-20:]))

    def get_tutti(self) -> list:
        with self._lock:
            return [c.to_dict() for c in COMPARTI.values()]
