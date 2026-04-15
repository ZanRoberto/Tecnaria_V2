"""
OVERTOP V16 — BREATH ENGINE
==============================
Entra quando il mercato ha ancora fiato.
Esci quando il fiato finisce — non un secondo dopo.

Il "respiro" è l'energia dell'impulso corrente:
  - Nasce (accelerazione)
  - Vive (momentum sostenuto)
  - Muore (decelerazione, inversione micro)

REGOLA: entri nell'inalazione, esci nell'esalazione.
Non aspetti il minuto successivo — senti quando l'energia cade.

Entry trigger:
  - Impulso nasce (accelerazione positiva)
  - Volume conferma
  - Non sei già in zona di esaurimento

Exit trigger:
  - Decelerazione > soglia (il fiato sta finendo)
  - Micro-inversione (direzione locale cambia)
  - Peak detection (hai già preso il meglio)
"""

import collections
import time
import logging

log = logging.getLogger(__name__)


class BreathEngine:
    """
    Misura il respiro del mercato tick per tick.
    Fornisce segnali di entry e exit basati sull'energia reale.
    """

    def __init__(self):
        self._prices  = collections.deque(maxlen=30)
        self._volumes = collections.deque(maxlen=30)
        self._times   = collections.deque(maxlen=30)

        # Stato corrente
        self._energia:       float = 0.0
        self._accelerazione: float = 0.0
        self._fase:          str   = "NEUTRO"  # INALAZIONE|PICCO|ESALAZIONE|NEUTRO
        self._fase_durata:   int   = 0

        # Per peak detection
        self._max_energia_trade: float = 0.0
        self._in_trade:          bool  = False

        # Storia impulsi
        self._impulsi: list = []

    # ─────────────────────────────────────────────────────────
    # TICK
    # ─────────────────────────────────────────────────────────

    def on_tick(self, price: float, volume: float = 1.0) -> dict:
        now = time.time()
        self._prices.append(price)
        self._volumes.append(volume)
        self._times.append(now)

        if len(self._prices) < 5:
            return self._stato()

        prices = list(self._prices)
        vols   = list(self._volumes)

        # ── Energia = velocità del movimento ─────────────────
        # Quanto si muove per tick — normalizzato
        moves = [abs(prices[i] - prices[i-1]) / prices[i-1]
                 for i in range(-5, 0)]
        energia_now = sum(moves) / len(moves) * 10000  # in basis points

        # ── Accelerazione = variazione dell'energia ───────────
        if self._energia > 0:
            self._accelerazione = energia_now - self._energia
        self._energia = round(energia_now, 4)

        # ── Direzione locale (ultimi 5 tick) ──────────────────
        dir_locale = sum(1 if prices[i] > prices[i-1] else -1
                        for i in range(-5, 0))

        # ── Volume conferma ───────────────────────────────────
        vol_media = sum(vols[-10:]) / max(1, len(vols[-10:]))
        vol_ratio = vols[-1] / max(vol_media, 0.001)

        # ── Fase del respiro ──────────────────────────────────
        vecchia_fase = self._fase
        if self._accelerazione > 0.5 and vol_ratio > 1.1:
            self._fase = "INALAZIONE"
        elif self._energia > 3.0 and abs(self._accelerazione) < 0.3:
            self._fase = "PICCO"
        elif self._accelerazione < -0.3:
            self._fase = "ESALAZIONE"
        else:
            self._fase = "NEUTRO"

        if self._fase == vecchia_fase:
            self._fase_durata += 1
        else:
            self._fase_durata = 0

        # ── Peak tracking durante trade ───────────────────────
        if self._in_trade:
            if self._energia > self._max_energia_trade:
                self._max_energia_trade = self._energia

        return self._stato(dir_locale, vol_ratio)

    # ─────────────────────────────────────────────────────────
    # SEGNALI ENTRY / EXIT
    # ─────────────────────────────────────────────────────────

    def segnale_entry(self, direction: str, nervosismo: float) -> dict:
        """
        Valuta se è il momento giusto per entrare.
        Restituisce: ok, score, motivo
        """
        if len(self._prices) < 10:
            return {"ok": False, "score": 0, "motivo": "WARMUP"}

        prices = list(self._prices)
        score  = 0
        motivi = []

        # 1. Siamo in INALAZIONE o PICCO → energia presente
        if self._fase == "INALAZIONE":
            score += 40
            motivi.append("INALAZIONE")
        elif self._fase == "PICCO":
            score += 20
            motivi.append("PICCO")
        elif self._fase == "ESALAZIONE":
            return {"ok": False, "score": 0, "motivo": "ESALAZIONE — fiato finito"}
        else:
            score += 10

        # 2. Energia sopra soglia minima
        soglia_energia = 1.5 if nervosismo < 0.3 else 2.5
        if self._energia >= soglia_energia:
            score += 30
            motivi.append(f"ENERGIA_{self._energia:.1f}")
        else:
            score -= 20
            motivi.append(f"ENERGIA_BASSA_{self._energia:.1f}")

        # 3. Accelerazione positiva (impulso che nasce)
        if self._accelerazione > 0.3:
            score += 20
            motivi.append("ACCEL+")
        elif self._accelerazione < -0.5:
            score -= 30
            motivi.append("DECEL-")

        # 4. Direzione locale coerente con direzione trade
        prices = list(self._prices)
        dir_locale = sum(1 if prices[i] > prices[i-1] else -1
                        for i in range(-3, 0))
        if direction == "LONG" and dir_locale > 0:
            score += 10
            motivi.append("DIR_OK")
        elif direction == "SHORT" and dir_locale < 0:
            score += 10
            motivi.append("DIR_OK")
        else:
            score -= 15
            motivi.append("DIR_CONTRO")

        ok = score >= 50
        return {
            "ok":     ok,
            "score":  score,
            "motivo": " | ".join(motivi),
            "fase":   self._fase,
            "energia":self._energia,
        }

    def segnale_exit(self, position, nervosismo: float) -> dict:
        """
        Valuta se è il momento giusto per uscire.
        Priorità: non perdere profitto già acquisito.
        """
        if not position:
            return {"ok": False, "motivo": "NO_POSITION"}

        price_now   = list(self._prices)[-1] if self._prices else 0
        entry_price = position.entry_price
        direction   = position.direction
        dur         = time.time() - position.entry_time

        # PnL corrente
        if direction == "LONG":
            pnl_pct = (price_now - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - price_now) / entry_price

        motivi = []

        # ── Exit priorità 1: ESALAZIONE con profitto ──────────
        if self._fase == "ESALAZIONE" and pnl_pct > 0.0001:
            return {"ok": True, "motivo": f"ESALAZIONE+PROFIT pnl={pnl_pct:.3%}",
                    "urgenza": "ALTA"}

        # ── Exit priorità 2: Peak superato e stiamo scendendo ─
        peak_ratio = self._energia / max(self._max_energia_trade, 0.001)
        if (self._in_trade and self._max_energia_trade > 2.0 and
                peak_ratio < 0.5 and pnl_pct > 0):
            return {"ok": True, "motivo": f"PEAK_SUPERATO energia={self._energia:.1f}/{self._max_energia_trade:.1f}",
                    "urgenza": "ALTA"}

        # ── Exit priorità 3: Esalazione lunga anche in perdita ─
        if self._fase == "ESALAZIONE" and self._fase_durata > 5:
            return {"ok": True, "motivo": f"ESALAZIONE_LUNGA dur={self._fase_durata}",
                    "urgenza": "MEDIA"}

        # ── Exit priorità 4: Stop loss dinamico ───────────────
        sl = 0.012 if nervosismo > 0.6 else 0.020
        if pnl_pct < -sl:
            return {"ok": True, "motivo": f"STOP_LOSS pnl={pnl_pct:.3%} sl={sl:.1%}",
                    "urgenza": "CRITICA"}

        # ── Exit priorità 5: Timeout adattivo ─────────────────
        timeout = 60 if nervosismo > 0.6 else 300
        if dur > timeout:
            return {"ok": True, "motivo": f"TIMEOUT {dur:.0f}s/{timeout}s",
                    "urgenza": "BASSA"}

        return {"ok": False, "motivo": f"HOLD fase={self._fase} energia={self._energia:.1f} pnl={pnl_pct:.3%}"}

    # ─────────────────────────────────────────────────────────
    # TRADE TRACKING
    # ─────────────────────────────────────────────────────────

    def on_trade_open(self):
        self._in_trade           = True
        self._max_energia_trade  = self._energia

    def on_trade_close(self, pnl: float):
        if self._in_trade:
            self._impulsi.append({
                "ts":           time.strftime("%H:%M:%S"),
                "pnl":          round(pnl, 3),
                "max_energia":  round(self._max_energia_trade, 2),
                "fase_exit":    self._fase,
            })
            if len(self._impulsi) > 50:
                self._impulsi.pop(0)
        self._in_trade          = False
        self._max_energia_trade = 0.0

    # ─────────────────────────────────────────────────────────
    # STATO
    # ─────────────────────────────────────────────────────────

    def _stato(self, dir_locale: int = 0, vol_ratio: float = 1.0) -> dict:
        return {
            "energia":       self._energia,
            "accelerazione": round(self._accelerazione, 3),
            "fase":          self._fase,
            "fase_durata":   self._fase_durata,
            "dir_locale":    dir_locale,
            "vol_ratio":     round(vol_ratio, 2),
        }

    def get_stato(self) -> dict:
        return {
            **self._stato(),
            "in_trade":          self._in_trade,
            "max_energia_trade": round(self._max_energia_trade, 2),
            "impulsi_recenti":   self._impulsi[-5:],
        }
