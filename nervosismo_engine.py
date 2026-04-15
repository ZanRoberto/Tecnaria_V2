"""
OVERTOP V16 — NERVOSISMO ENGINE
==================================
Indicatore di tensione di mercato — aggiornato ad ogni tick.

GOMME:
  SLICK    (nervosismo < 0.3) → mercato fluido, posso spingere
  INTER    (nervosismo 0.3-0.6) → attenzione, mercato misto
  RAIN     (nervosismo > 0.6) → pista bagnata, proteggo tutto

Il cambio gomme avviene ad ogni tick se necessario.
La storia registra ogni cambio e il risultato — calibra il punto di switch.

Nervosismo = f(regime_flip_rate, direction_flip_rate, vol_spike, price_range)
"""

import time
import collections
import logging

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ASSETTI GOMME
# ═══════════════════════════════════════════════════════════════

ASSETTI = {
    "SLICK": {
        "colore":        "#00d97a",
        "soglia_base":   44,
        "soglia_min":    40,
        "size_default":  0.5,
        "stop_loss_pct": 0.025,
        "exit_max_secs": 300,
        "descrizione":   "Pista asciutta — spingo forte",
    },
    "INTER": {
        "colore":        "#f59e0b",
        "soglia_base":   50,
        "soglia_min":    46,
        "size_default":  0.3,
        "stop_loss_pct": 0.020,
        "exit_max_secs": 180,
        "descrizione":   "Misto — attenzione, reagisco veloce",
    },
    "RAIN": {
        "colore":        "#3b82f6",
        "soglia_base":   56,
        "soglia_min":    52,
        "size_default":  0.15,
        "stop_loss_pct": 0.012,
        "exit_max_secs": 60,
        "descrizione":   "Pista bagnata — proteggo, esco veloce",
    },
}


class NervosismoEngine:
    """
    Calcola il nervosismo di mercato ad ogni tick.
    Switcha assetto (gomme) immediatamente quando serve.
    Tiene storia per calibrare il punto di cambio.
    """

    def __init__(self):
        # Finestra breve — reattiva
        self._regime_history    = collections.deque(maxlen=20)   # ultimi 20 tick regime
        self._direction_history = collections.deque(maxlen=20)
        self._price_history     = collections.deque(maxlen=50)
        self._vol_history       = collections.deque(maxlen=100)  # per baseline vol

        self._gomme_attuale:  str   = "INTER"
        self._nervosismo:     float = 0.3
        self._last_switch:    float = 0.0
        self._switch_min_ticks: int = 10   # minimo tick tra switch (evita oscillazione)
        self._tick_since_switch: int = 0

        # Storia cambi gomme
        self._storia: list = []
        self._trade_corrente_pnl: float = 0.0

        # Calibrazione punto di switch — impara dalla storia
        self._soglia_rain:  float = 0.60   # default
        self._soglia_slick: float = 0.25   # default

        log.info("[NERVOSISMO] Engine avviato — assetto INTER")

    # ─────────────────────────────────────────────────────────
    # TICK PRINCIPALE
    # ─────────────────────────────────────────────────────────

    def on_tick(self, price: float, regime: str, direction: str, skills: dict) -> dict:
        """
        Chiamato ad ogni tick.
        Restituisce stato nervosismo e assetto corrente.
        """
        self._price_history.append(price)
        self._regime_history.append(regime)
        self._direction_history.append(direction)
        self._tick_since_switch += 1

        if len(self._price_history) < 5:
            return self._stato()

        # Calcola nervosismo
        nerv = self._calcola_nervosismo()
        self._nervosismo = nerv

        # Determina gomma giusta
        gomma_giusta = self._gomma_per_nervosismo(nerv)

        # Switcha se necessario (con protezione anti-oscillazione)
        if (gomma_giusta != self._gomme_attuale and
                self._tick_since_switch >= self._switch_min_ticks):
            self._switcha(gomma_giusta, nerv, skills)

        return self._stato()

    # ─────────────────────────────────────────────────────────
    # CALCOLO NERVOSISMO
    # ─────────────────────────────────────────────────────────

    def _calcola_nervosismo(self) -> float:
        """
        Nervosismo composito — 4 componenti:
        1. Flip regime: quante volte cambia regime negli ultimi 20 tick
        2. Flip direzione: quanto spesso flippa
        3. Spike volatilità: vol attuale vs baseline
        4. Range prezzo: escursione % negli ultimi 50 tick
        """
        scores = []

        # 1. Flip regime
        regimi = list(self._regime_history)
        flips_regime = sum(1 for i in range(1, len(regimi)) if regimi[i] != regimi[i-1])
        flip_rate_regime = flips_regime / max(1, len(regimi) - 1)
        scores.append(min(1.0, flip_rate_regime * 3))

        # 2. Flip direzione
        dirs = list(self._direction_history)
        flips_dir = sum(1 for i in range(1, len(dirs)) if dirs[i] != dirs[i-1])
        flip_rate_dir = flips_dir / max(1, len(dirs) - 1)
        scores.append(min(1.0, flip_rate_dir * 4))

        # 3. Spike volatilità
        prices = list(self._price_history)
        if len(prices) >= 10:
            # Vol ultimi 5 tick vs media
            recent_moves = [abs(prices[i] - prices[i-1]) / prices[i-1]
                           for i in range(-5, 0)]
            vol_now = sum(recent_moves) / len(recent_moves)
            self._vol_history.append(vol_now)
            if len(self._vol_history) >= 20:
                vol_base = sum(list(self._vol_history)[:-5]) / max(1, len(self._vol_history) - 5)
                spike = vol_now / max(vol_base, 0.00001)
                scores.append(min(1.0, (spike - 1) / 3))  # spike 4x = nervosismo 1.0
            else:
                scores.append(0.3)
        else:
            scores.append(0.3)

        # 4. Range prezzo (escursione relativa)
        if len(prices) >= 20:
            recent = prices[-20:]
            rng = (max(recent) - min(recent)) / min(recent) * 100  # % escursione
            # 0.5% in 20 tick = nervosismo 0.5
            scores.append(min(1.0, rng / 1.0))
        else:
            scores.append(0.2)

        # Media pesata: flip regime e dir contano di più
        pesi = [0.30, 0.30, 0.25, 0.15]
        nerv = sum(s * p for s, p in zip(scores, pesi))
        return round(nerv, 3)

    def _gomma_per_nervosismo(self, nerv: float) -> str:
        if nerv >= self._soglia_rain:
            return "RAIN"
        elif nerv <= self._soglia_slick:
            return "SLICK"
        else:
            return "INTER"

    # ─────────────────────────────────────────────────────────
    # SWITCH GOMME
    # ─────────────────────────────────────────────────────────

    def _switcha(self, nuova: str, nerv: float, skills: dict):
        vecchia   = self._gomme_attuale
        assetto   = ASSETTI[nuova]

        # Applica parametri alle skill immediatamente
        entry = skills.get("ENTRY")
        if entry:
            entry.set_param("soglia_base",  assetto["soglia_base"],  fonte=f"GOMME_{nuova}")
            entry.set_param("soglia_min",   assetto["soglia_min"],   fonte=f"GOMME_{nuova}")
            entry.set_param("size_default", assetto["size_default"], fonte=f"GOMME_{nuova}")

        exit_sk = skills.get("EXIT")
        if exit_sk:
            exit_sk.set_param("stop_loss_pct", assetto["stop_loss_pct"], fonte=f"GOMME_{nuova}")
            exit_sk.set_param("max_hold_secs", assetto["exit_max_secs"], fonte=f"GOMME_{nuova}")

        self._gomme_attuale    = nuova
        self._tick_since_switch = 0

        # Registra nella storia
        entry_storia = {
            "ts":       time.strftime("%H:%M:%S"),
            "da":       vecchia,
            "a":        nuova,
            "nerv":     nerv,
            "soglia_r": self._soglia_rain,
            "soglia_s": self._soglia_slick,
            "pnl_post": None,  # verrà aggiornato dopo
        }
        self._storia.append(entry_storia)
        if len(self._storia) > 200:
            self._storia.pop(0)

        log.info(f"[GOMME] {vecchia} → {nuova} | nerv={nerv:.2f} "
                 f"soglia={assetto['soglia_base']} size={assetto['size_default']}")

    # ─────────────────────────────────────────────────────────
    # APPRENDIMENTO — calibra soglie dai risultati
    # ─────────────────────────────────────────────────────────

    def on_trade_closed(self, pnl: float):
        """
        Registra il PnL dell'ultimo trade nella storia.
        Impara se il cambio gomme era al momento giusto.
        """
        if not self._storia:
            return

        # Associa PnL all'ultimo cambio gomme senza risultato
        for entry in reversed(self._storia):
            if entry["pnl_post"] is None:
                entry["pnl_post"] = pnl
                break

        # Calibra dopo 20+ switch con risultato
        storia_con_risultato = [e for e in self._storia if e["pnl_post"] is not None]
        if len(storia_con_risultato) < 20:
            return

        self._calibra_soglie(storia_con_risultato)

    def _calibra_soglie(self, storia: list):
        """
        Analizza la storia degli switch e calibra le soglie.

        Se switch a RAIN con nerv=0.55 → trade successivo vince
        → la soglia rain può essere abbassata (reagisci prima)

        Se switch a SLICK con nerv=0.28 → trade successivo perde
        → la soglia slick deve alzarsi (non tornare troppo presto)
        """
        # Switch a RAIN che hanno portato a win
        rain_wins  = [e for e in storia if e["a"] == "RAIN" and (e["pnl_post"] or 0) > 0]
        rain_loss  = [e for e in storia if e["a"] == "RAIN" and (e["pnl_post"] or 0) < 0]

        # Switch a SLICK che hanno portato a win
        slick_wins = [e for e in storia if e["a"] == "SLICK" and (e["pnl_post"] or 0) > 0]
        slick_loss = [e for e in storia if e["a"] == "SLICK" and (e["pnl_post"] or 0) < 0]

        # Aggiusta soglia RAIN
        if len(rain_wins) >= 5 and len(rain_loss) >= 5:
            avg_nerv_win  = sum(e["nerv"] for e in rain_wins) / len(rain_wins)
            avg_nerv_loss = sum(e["nerv"] for e in rain_loss) / len(rain_loss)
            # Se switch vincente avviene con nerv più basso → abbassa soglia
            nuova_soglia_rain = round((avg_nerv_win + self._soglia_rain) / 2, 2)
            nuova_soglia_rain = max(0.40, min(0.75, nuova_soglia_rain))
            if nuova_soglia_rain != self._soglia_rain:
                log.info(f"[GOMME_CALIBRA] soglia_rain: {self._soglia_rain} → {nuova_soglia_rain}")
                self._soglia_rain = nuova_soglia_rain

        # Aggiusta soglia SLICK
        if len(slick_wins) >= 5 and len(slick_loss) >= 5:
            avg_nerv_win  = sum(e["nerv"] for e in slick_wins) / len(slick_wins)
            nuova_soglia_slick = round((avg_nerv_win + self._soglia_slick) / 2, 2)
            nuova_soglia_slick = max(0.10, min(0.40, nuova_soglia_slick))
            if nuova_soglia_slick != self._soglia_slick:
                log.info(f"[GOMME_CALIBRA] soglia_slick: {self._soglia_slick} → {nuova_soglia_slick}")
                self._soglia_slick = nuova_soglia_slick

    # ─────────────────────────────────────────────────────────
    # API PUBBLICA
    # ─────────────────────────────────────────────────────────

    def _stato(self) -> dict:
        assetto = ASSETTI.get(self._gomme_attuale, ASSETTI["INTER"])
        return {
            "gomme":        self._gomme_attuale,
            "nervosismo":   self._nervosismo,
            "colore":       assetto["colore"],
            "descrizione":  assetto["descrizione"],
            "soglia_base":  assetto["soglia_base"],
            "size":         assetto["size_default"],
            "soglia_rain":  self._soglia_rain,
            "soglia_slick": self._soglia_slick,
            "tick_dal_switch": self._tick_since_switch,
        }

    def get_storia(self, n: int = 20) -> list:
        return list(reversed(self._storia[-n:]))

    def get_stato(self) -> dict:
        return self._stato()
