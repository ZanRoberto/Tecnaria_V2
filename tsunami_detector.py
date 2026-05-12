"""
═══════════════════════════════════════════════════════════════════════════
🌊 TSUNAMI DETECTOR — INVENZIONE DI ROBERTO ZANARDO (12 maggio 2026)
═══════════════════════════════════════════════════════════════════════════

PRINCIPIO FISICO:
  "Lo tsunami si misura dalla sua prima manifestazione. Se era un urto
   piccolo saranno onde piccole e molta schiuma. Ma se man mano che si
   muove accumula energia, alla fine arriva un'onda da 11 metri."
                                                  — Roberto Zanardo

ARCHITETTURA:
  Il problema del bot precedente: misurava "forza" su 5 secondi di tick.
  In 5 secondi BTC si muove $0.50-$2 random — TUTTO È RUMORE.
  
  Soluzione: misurare la forza SIMULTANEAMENTE su 3 scale temporali.
    - 30 secondi  (micro-pattern)
    - 2 minuti    (struttura)
    - 10 minuti   (macro-tendenza)
  
  Uno TSUNAMI è coerente a TUTTE le scale.
  Una SCHIUMA appare solo alla scala più piccola.

3 MODULI:
  1. CandelaAggregator    — costruisce candele OHLCV virtuali
  2. TsunamiDetector      — misura forza fisica su candele
  3. TsunamiGate          — concordanza multi-scala → verdetto

INTEGRAZIONE:
  Funziona AFFIANCO al SeedScorer.
  Diventa un VETO addizionale prima dell'entry.
  Persiste su DB come buffer prezzi (fix #18).

═══════════════════════════════════════════════════════════════════════════
"""

import time
import math
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# MODULO 1: CandelaAggregator
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Candela:
    """Una candela OHLCV virtuale (aggregata da tick)."""
    timestamp_start: float
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float
    n_tick: int  # numero di tick aggregati
    
    def to_dict(self):
        return {
            'ts': self.timestamp_start,
            'o':  round(self.open, 2),
            'h':  round(self.high, 2),
            'l':  round(self.low, 2),
            'c':  round(self.close, 2),
            'v':  round(self.volume, 4),
            'n':  self.n_tick,
        }
    
    @classmethod
    def from_dict(cls, d):
        return cls(
            timestamp_start=d['ts'],
            open=d['o'], high=d['h'], low=d['l'], close=d['c'],
            volume=d['v'], n_tick=d['n'],
        )


class CandelaAggregator:
    """
    Aggrega tick in candele virtuali su 3 timeframes.
    Ogni tick aggiorna la candela corrente; quando il tempo della candela
    scade, la archivia e ne apre una nuova.
    """
    
    # Configurazione 3 timeframes (in secondi)
    TF_30S  = 30
    TF_2MIN = 120
    TF_10MIN = 600
    
    # Quante candele storiche tenere per timeframe
    HIST_30S  = 30   # 15 minuti
    HIST_2MIN = 30   # 1 ora
    HIST_10MIN = 30  # 5 ore
    
    def __init__(self):
        # Candele archiviate (storiche) per timeframe
        self.candele_30s   = deque(maxlen=self.HIST_30S)
        self.candele_2min  = deque(maxlen=self.HIST_2MIN)
        self.candele_10min = deque(maxlen=self.HIST_10MIN)
        
        # Candele correnti (in costruzione)
        self._current_30s:   Optional[Candela] = None
        self._current_2min:  Optional[Candela] = None
        self._current_10min: Optional[Candela] = None
    
    def feed_tick(self, price: float, volume: float = 1.0, ts: Optional[float] = None):
        """Aggiorna le 3 candele correnti con un nuovo tick."""
        if ts is None:
            ts = time.time()
        
        self._update_candela(price, volume, ts, self.TF_30S,   '_current_30s',   self.candele_30s)
        self._update_candela(price, volume, ts, self.TF_2MIN,  '_current_2min',  self.candele_2min)
        self._update_candela(price, volume, ts, self.TF_10MIN, '_current_10min', self.candele_10min)
    
    def _update_candela(self, price: float, volume: float, ts: float,
                        tf_seconds: int, current_attr: str, archive: deque):
        """Aggiorna o ruota la candela del timeframe specificato."""
        current = getattr(self, current_attr)
        
        # Calcola l'inizio della candela teorica per questo tick
        tf_start = (int(ts) // tf_seconds) * tf_seconds
        
        if current is None:
            # Prima candela
            setattr(self, current_attr, Candela(
                timestamp_start=tf_start,
                open=price, high=price, low=price, close=price,
                volume=volume, n_tick=1,
            ))
            return
        
        if tf_start != current.timestamp_start:
            # La candela corrente è scaduta → archivia e apri nuova
            archive.append(current)
            setattr(self, current_attr, Candela(
                timestamp_start=tf_start,
                open=price, high=price, low=price, close=price,
                volume=volume, n_tick=1,
            ))
        else:
            # Aggiorna la candela corrente
            current.high = max(current.high, price)
            current.low = min(current.low, price)
            current.close = price
            current.volume += volume
            current.n_tick += 1
    
    def get_candele(self, timeframe: str) -> list:
        """Restituisce le candele archiviate del timeframe richiesto."""
        if timeframe == '30s':
            return list(self.candele_30s)
        elif timeframe == '2min':
            return list(self.candele_2min)
        elif timeframe == '10min':
            return list(self.candele_10min)
        else:
            return []
    
    def status(self) -> dict:
        """Stato attuale dei buffer per dashboard."""
        return {
            '30s':   {'archiviate': len(self.candele_30s),   'current': self._current_30s.to_dict()   if self._current_30s   else None},
            '2min':  {'archiviate': len(self.candele_2min),  'current': self._current_2min.to_dict()  if self._current_2min  else None},
            '10min': {'archiviate': len(self.candele_10min), 'current': self._current_10min.to_dict() if self._current_10min else None},
        }
    
    # ── PERSISTENZA ─────────────────────────────────────────────────────
    
    def to_persist(self) -> dict:
        """Serializza le candele per salvarle nel DB (fix #18 estensione)."""
        return {
            '30s':   [c.to_dict() for c in self.candele_30s],
            '2min':  [c.to_dict() for c in self.candele_2min],
            '10min': [c.to_dict() for c in self.candele_10min],
        }
    
    def from_persist(self, data: dict):
        """Ripristina le candele dal DB al boot."""
        try:
            if '30s' in data:
                for d in data['30s']:
                    self.candele_30s.append(Candela.from_dict(d))
            if '2min' in data:
                for d in data['2min']:
                    self.candele_2min.append(Candela.from_dict(d))
            if '10min' in data:
                for d in data['10min']:
                    self.candele_10min.append(Candela.from_dict(d))
            log.info(f"[CANDELE_LOAD] Ripristinate: 30s={len(self.candele_30s)} "
                    f"2min={len(self.candele_2min)} 10min={len(self.candele_10min)}")
        except Exception as e:
            log.error(f"[CANDELE_LOAD] Errore ripristino: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# MODULO 2: TsunamiDetector
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TsunamiVerdict:
    """Verdetto di un timeframe."""
    timeframe: str
    is_tsunami: bool       # vero tsunami strutturato?
    direction: str         # 'UP', 'DOWN', 'NONE'
    strength: float        # 0.0 - 1.0 forza complessiva
    
    # Componenti misurate
    velocita: float        # $ / candela
    accelerazione: float   # $ / candela²
    coerenza: float        # % candele direzione concorde
    varianza_calante: bool # std recente < std passata
    energia_crescente: bool # energia cumulata cresce
    
    # Diagnostica
    n_candele: int
    motivo: str            # spiegazione testuale
    
    def to_dict(self):
        return {
            'tf': self.timeframe,
            'tsunami': self.is_tsunami,
            'direction': self.direction,
            'strength': round(self.strength, 3),
            'velocita': round(self.velocita, 3),
            'accelerazione': round(self.accelerazione, 3),
            'coerenza': round(self.coerenza, 3),
            'varianza_calante': self.varianza_calante,
            'energia_crescente': self.energia_crescente,
            'n_candele': self.n_candele,
            'motivo': self.motivo,
        }


class TsunamiDetector:
    """
    Misura la forza strutturata su un timeframe specifico usando candele
    OHLCV virtuali. Distingue TSUNAMI da SCHIUMA in base a coerenza,
    accelerazione, varianza in calo, energia crescente.
    """
    
    # Configurazione soglie
    MIN_CANDELE        = 6      # almeno 6 candele per analisi affidabile
    SOGLIA_COERENZA    = 0.55   # >= 55% candele direzione concorde
    SOGLIA_FORZA       = 0.50   # >= 50% per dichiarare tsunami
    
    def analyze(self, candele: list, timeframe: str) -> TsunamiVerdict:
        """Analizza una lista di candele e produce verdetto."""
        n = len(candele)
        
        # Default: dati insufficienti
        if n < self.MIN_CANDELE:
            return TsunamiVerdict(
                timeframe=timeframe, is_tsunami=False, direction='NONE',
                strength=0.0, velocita=0.0, accelerazione=0.0,
                coerenza=0.0, varianza_calante=False, energia_crescente=False,
                n_candele=n, motivo=f"Dati insufficienti ({n}<{self.MIN_CANDELE})",
            )
        
        # ── A) VELOCITÀ media (derivata prima sui close)
        closes = [c.close for c in candele]
        delta_total = closes[-1] - closes[0]
        velocita = delta_total / (n - 1)  # $/candela media
        
        # ── B) ACCELERAZIONE (derivata seconda)
        # Velocità nella prima metà vs seconda metà
        half = n // 2
        v_prima = (closes[half] - closes[0]) / max(1, half)
        v_seconda = (closes[-1] - closes[half]) / max(1, n - 1 - half)
        accelerazione = v_seconda - v_prima
        # Per essere tsunami: accelerazione nello STESSO senso della velocità
        accel_positiva = (velocita > 0 and accelerazione > 0) or \
                         (velocita < 0 and accelerazione < 0)
        
        # ── C) COERENZA DIREZIONALE
        # % di candele consecutive che si muovono nella stessa direzione della media
        direction = 'UP' if velocita > 0 else ('DOWN' if velocita < 0 else 'NONE')
        candele_concordi = 0
        for c in candele:
            mov = c.close - c.open  # movimento intra-candela
            if (direction == 'UP' and mov > 0) or (direction == 'DOWN' and mov < 0):
                candele_concordi += 1
        coerenza = candele_concordi / n if n > 0 else 0.0
        
        # ── D) VARIANZA IN CALO (acqua si compatta)
        # Std dei movimenti recenti vs passati
        movimenti = [c.close - c.open for c in candele]
        if n >= 6:
            mov_recenti = movimenti[-3:]
            mov_passati = movimenti[:3]
            std_recente = self._std(mov_recenti)
            std_passata = self._std(mov_passati)
            varianza_calante = std_recente < std_passata * 0.95
        else:
            varianza_calante = False
        
        # ── E) ENERGIA CUMULATA CRESCENTE
        # Energia = somma dei quadrati dei movimenti
        # Confronto prima metà vs seconda metà
        e_prima = sum(m*m for m in movimenti[:half])
        e_seconda = sum(m*m for m in movimenti[half:])
        energia_crescente = e_seconda > e_prima
        
        # ── VERDETTO COMPLESSIVO
        # Score pesato delle componenti
        score = 0.0
        comp = []
        
        # Coerenza (peso 35%)
        score_coerenza = max(0.0, (coerenza - 0.4) / 0.4)  # 0.4 → 0.0, 0.8 → 1.0
        score += score_coerenza * 0.35
        comp.append(f"coerenza={coerenza:.0%}")
        
        # Accelerazione corretta (peso 25%)
        if accel_positiva:
            score += 0.25
            comp.append("accel+")
        else:
            comp.append("accel-")
        
        # Varianza calante (peso 15%)
        if varianza_calante:
            score += 0.15
            comp.append("var↓")
        
        # Energia crescente (peso 25%)
        if energia_crescente:
            score += 0.25
            comp.append("E↑")
        
        score = min(1.0, max(0.0, score))
        is_tsunami = score >= self.SOGLIA_FORZA and coerenza >= self.SOGLIA_COERENZA
        
        if direction == 'NONE':
            is_tsunami = False
        
        motivo = f"score={score:.2f} {' '.join(comp)}"
        
        return TsunamiVerdict(
            timeframe=timeframe,
            is_tsunami=is_tsunami,
            direction=direction,
            strength=score,
            velocita=velocita,
            accelerazione=accelerazione,
            coerenza=coerenza,
            varianza_calante=varianza_calante,
            energia_crescente=energia_crescente,
            n_candele=n,
            motivo=motivo,
        )
    
    @staticmethod
    def _std(values: list) -> float:
        """Deviazione standard."""
        if not values:
            return 0.0
        m = sum(values) / len(values)
        var = sum((v - m) ** 2 for v in values) / len(values)
        return math.sqrt(var)


# ═══════════════════════════════════════════════════════════════════════════
# MODULO 3: TsunamiGate (concordanza multi-scala)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TsunamiDecision:
    """Decisione finale del Gate."""
    azione: str            # 'ENTRA_LONG', 'ENTRA_SHORT', 'NO_ENTRY'
    confidenza: int        # 0, 1, 2, 3 (quanti TF concordano)
    size_mult: float       # 1.0 = full, 0.5 = ridotta, 0.0 = no entry
    motivo: str            
    verdetti: dict         # i 3 verdetti per timeframe
    
    def to_dict(self):
        return {
            'azione': self.azione,
            'confidenza': self.confidenza,
            'size_mult': self.size_mult,
            'motivo': self.motivo,
            'verdetti': {tf: v.to_dict() for tf, v in self.verdetti.items()},
        }


class TsunamiGate:
    """
    Gate finale che valuta la concordanza dei 3 timeframes.
    REGOLE:
      - 3/3 TSUNAMI nella stessa direzione → ENTRY full size
      - 2/3 TSUNAMI nella stessa direzione → ENTRY ridotta (0.5x)
      - 0/3 o 1/3 → NO ENTRY (schiuma)
      - Direzioni discordi → NO ENTRY (rumore strutturale)
    """
    
    def __init__(self, aggregator: CandelaAggregator, detector: TsunamiDetector):
        self.aggregator = aggregator
        self.detector = detector
    
    def evaluate(self) -> TsunamiDecision:
        """Valuta i 3 timeframes e prende decisione."""
        verdetti = {}
        for tf, candele in [
            ('30s',   self.aggregator.get_candele('30s')),
            ('2min',  self.aggregator.get_candele('2min')),
            ('10min', self.aggregator.get_candele('10min')),
        ]:
            verdetti[tf] = self.detector.analyze(candele, tf)
        
        # Conta quanti TF sono in TSUNAMI e in quale direzione
        ts_up   = sum(1 for v in verdetti.values() if v.is_tsunami and v.direction == 'UP')
        ts_down = sum(1 for v in verdetti.values() if v.is_tsunami and v.direction == 'DOWN')
        
        if ts_up >= 3:
            return TsunamiDecision(
                azione='ENTRA_LONG', confidenza=3, size_mult=1.0,
                motivo=f"TSUNAMI UP confermato 3/3 timeframes",
                verdetti=verdetti,
            )
        elif ts_down >= 3:
            return TsunamiDecision(
                azione='ENTRA_SHORT', confidenza=3, size_mult=1.0,
                motivo=f"TSUNAMI DOWN confermato 3/3 timeframes",
                verdetti=verdetti,
            )
        elif ts_up >= 2:
            return TsunamiDecision(
                azione='ENTRA_LONG', confidenza=2, size_mult=0.5,
                motivo=f"TSUNAMI UP parziale 2/3 → size ridotta",
                verdetti=verdetti,
            )
        elif ts_down >= 2:
            return TsunamiDecision(
                azione='ENTRA_SHORT', confidenza=2, size_mult=0.5,
                motivo=f"TSUNAMI DOWN parziale 2/3 → size ridotta",
                verdetti=verdetti,
            )
        else:
            # Direzioni discordi o nessun tsunami → schiuma
            motivo_parts = []
            for tf, v in verdetti.items():
                tag = "TSUNAMI" if v.is_tsunami else "SCHIUMA"
                motivo_parts.append(f"{tf}:{tag}({v.direction})")
            return TsunamiDecision(
                azione='NO_ENTRY', confidenza=max(ts_up, ts_down), size_mult=0.0,
                motivo=f"NO TSUNAMI multi-scala — {' '.join(motivo_parts)}",
                verdetti=verdetti,
            )


# ═══════════════════════════════════════════════════════════════════════════
# FACADE: classe unica che ingloba i 3 moduli
# ═══════════════════════════════════════════════════════════════════════════

class TsunamiEngine:
    """
    Facade del sistema TsunamiDetector.
    Espone interfaccia semplice al resto del bot.
    """
    
    def __init__(self):
        self.aggregator = CandelaAggregator()
        self.detector = TsunamiDetector()
        self.gate = TsunamiGate(self.aggregator, self.detector)
        self._last_decision: Optional[TsunamiDecision] = None
        self._last_decision_ts: float = 0.0
    
    def feed_tick(self, price: float, volume: float = 1.0, ts: Optional[float] = None):
        """Aggiorna le candele con un nuovo tick."""
        self.aggregator.feed_tick(price, volume, ts)
    
    def evaluate(self) -> TsunamiDecision:
        """Decide se c'è uno tsunami in arrivo."""
        decision = self.gate.evaluate()
        self._last_decision = decision
        self._last_decision_ts = time.time()
        return decision
    
    def last_decision(self) -> Optional[dict]:
        """Ultima decisione per dashboard."""
        if self._last_decision is None:
            return None
        return self._last_decision.to_dict()
    
    def status(self) -> dict:
        """Stato completo per dashboard."""
        return {
            'aggregator': self.aggregator.status(),
            'last_decision': self.last_decision(),
            'last_decision_age': round(time.time() - self._last_decision_ts, 1) if self._last_decision_ts else None,
        }
    
    # Persistenza
    def to_persist(self) -> dict:
        return self.aggregator.to_persist()
    
    def from_persist(self, data: dict):
        self.aggregator.from_persist(data)
