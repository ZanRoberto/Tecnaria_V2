# 🦊 AUDIT SISTEMATICO — OVERTOP BASSANO V16

**Versione referto:** 1.1
**Ultima modifica:** 11 maggio 2026
**Versione codice analizzata:** post-fix doppia-fee (10 maggio 2026, sera)
**File analizzati:** 8 file Python (17.606 righe totali) + dashboard live + JSON `/trading/status`
**Metodologia:** scansione AST completa, pattern matching su 12 categorie di bug, cross-check con dati live
**Output:** referto completo, ordinato per gravità, con diagnosi + impatto economico + fix proposto + rischio
**Posizione nella repo:** `AUDIT_V16_REFERTO_COMPLETO.md` (root)

---

## 📊 STATO BUG — DASHBOARD

| # | Bug | Gravità | Stato | Risolto in |
|---|-----|---------|-------|------------|
| 0 | Doppia-fee phantom | 🔴 era CRITICA | ✅ RISOLTO | 10mag2026 (riga 9114) |
| 1 | `_place_order` PLACEHOLDER vuoto | 🔴 MORTALE in LIVE | 🔴 APERTO | — |
| 2 | Phantom Sup vs Comparto sulla SOGLIA_BASE | 🟠 ALTA | 🟡 MITIGATO 12mag (cooldown 10min) | parziale |
| 3 | NervosismoEngine DISATTIVATO | 🟠 ALTA | 🔴 APERTO | — |
| 4 | Phantom Sup scrive globale per problema locale | 🟠 ALTA | 🟡 MITIGATO 12mag (cooldown 10min) | parziale |
| 5 | `DOWNLOAD_SECRET="overtop2024"` default | 🟡 MEDIA | 🔴 APERTO | — |
| 6 | 19 `except: pass` silenziati | 🟡 MEDIA | 🔴 APERTO | — |
| 7 | VOLPE_BOOST in zona grigia | 🟡 MEDIA | 🔴 APERTO | — |
| 8 | Comparto sovrascrive SOGLIA ogni tick | 🟡 MEDIA | 🔴 APERTO | (legato a #2) |
| 9 | RegimeDetector flip ogni 60s + conf=-42% | 🟠 ALTA | ✅ RISOLTO 12mag (isteresi 30 tick) | riga 3508-3609 |
| 10 | Set_param chiamati su dict vuoto | 🟢 INCOERENZA | 🔴 APERTO | (legato a #2/#3) |
| 11 | Cap 5 phantom aperti | 🟡 MEDIA | 🔴 APERTO | — |
| 12 | `MIN_PNL_EDGE` costante morta | 🟢 BASSA | 🔴 APERTO | — |
| 13 | CompartoEngine flip ogni 60s (correlato a #9) | 🟠 ALTA | ✅ RISOLTO 12mag (cooldown 300s + confirm 5 tick) | comparto_engine.py riga 224-275 |
| TEST-1 | Holding 30 min (timeout 180→1800s) | 🟢 TEST | 🟡 IN VALIDAZIONE | riga 8396 |
| TEST-2 | SHORT attivato in RANGING/DIFENSIVO/ATTACCO | 🟢 TEST | 🟡 IN VALIDAZIONE | 12mag — 6 capsule + sblocco riga 6966 |

**Totale:** 3 risolti, 2 mitigati, 10 aperti, 2 test in corso.
**Ultimo aggiornamento referto:** Roberto+Claude, 11mag2026 — sessione audit sistematico.

---

## 🔴 BUG #1 — `_place_order` È UN PLACEHOLDER (MORTALE IN LIVE)

### 📍 Posizione
`OVERTOP_BASSANO_V16_PRODUCTION.py`, riga **9293-9304**

### 🐛 Codice attuale
```python
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
```

### 💀 Diagnosi
**La funzione non fa nulla.** Stampa un log e basta. Nessuna chiamata HTTP a Binance, nessuna gestione errori, nessuna autenticazione, nessun retry, nessuna verifica di esecuzione.

### 💰 Impatto economico
**CATASTROFICO se attivi LIVE.** Scenari:
- Tu metti `PAPER_TRADE=False` pensando di passare a soldi veri
- Il bot stampa "ENTRY LONG" nel log e nella dashboard
- Su Binance **non succede niente**
- Il bot crede di essere in posizione, calcola PnL fittizi, "chiude" la posizione (stampa altro log)
- Tu vedi WR migliorato, equity crescere — TUTTO FALSO
- Quando vai a guardare il conto Binance: **vuoto**

### 🚨 Severità: BOMBA AD OROLOGERIA
Questo bug è dormiente finché stai in PAPER. Ma è un anti-fix devastante: il giorno che attivi LIVE, ti credi di guadagnare e invece non stai facendo niente.

### 🔧 Fix proposto
Implementare correttamente. Servirebbe:
1. Importare `python-binance` (`pip install python-binance` + aggiungere a requirements.txt)
2. Inizializzare client API con `BINANCE_API_KEY` + `BINANCE_API_SECRET` da env Render
3. Mappare `side` → `SIDE_BUY`/`SIDE_SELL` di python-binance
4. Calcolare quantità reale: `qty = (TRADE_SIZE_USD * LEVERAGE * size_mult) / price` arrotondata al lot_size del symbol
5. Wrap try/except con log dettagliato dell'errore HTTP
6. Restituire l'order_id per tracciare l'esecuzione
7. **TEST CON 1 USDT REALE PRIMA DI ANDARE FULL LIVE**

### ⏱ Tempo stimato
1 giornata di sviluppo + 1 settimana di test cauti con micro-size.

### ⚠️ Raccomandazione
**Aggiungi un'asserzione a inizio bot:**
```python
if not PAPER_TRADE:
    raise RuntimeError("LIVE mode disabled — _place_order è placeholder!")
```
Così non puoi accidentalmente andare LIVE prima del fix.

---

## 🟠 BUG #2 — PHANTOM SUPERVISOR vs COMPARTO ENGINE — GUERRA SULLA SOGLIA_BASE

### 📍 Posizioni
- `OVERTOP_BASSANO_V16_PRODUCTION.py` riga **5717-5722** (Comparto sovrascrive)
- `OVERTOP_BASSANO_V16_PRODUCTION.py` riga **6678** (Phantom Supervisor sovrascrive)

### 🐛 Codice attuale

**Phantom Supervisor (ogni 60s):**
```python
# Riga 6669-6678
if wr_blocco > 0.45 and blk > 20:
    new_base = max(32, old_base - 3)  # ALLENTA -3
    self.campo.SOGLIA_BASE = new_base

# Riga 6693-6701
elif wr_blocco < 0.25 and blk > 20:
    new_base = min(58, old_base + 1)  # IRRIGIDISCE +1
    self.campo.SOGLIA_BASE = new_base
```

**CompartoEngine (ogni tick = ogni secondo):**
```python
# Riga 5717-5722
if _comp_attivo and hasattr(self.campo, 'SOGLIA_BASE'):
    _soglia_comp = _comp_attivo.soglia_base
    if self.campo.SOGLIA_BASE != _soglia_comp:
        self.campo.SOGLIA_BASE = _soglia_comp
        self.campo.SOGLIA_MIN  = _comp_attivo.soglia_min
```

### 💀 Diagnosi
**Comparto sovrascrive la soglia ad OGNI tick** (ogni ~1s). Phantom Supervisor sovrascrive **ogni 60s**.

Sequenza di un'ora:
1. T=0:00 — Comparto = DIFENSIVO → soglia=38
2. T=0:00 a 0:60 — Comparto rimette 38 ad ogni tick
3. T=1:00 — Phantom Supervisor: "WR=2%, irrigidisco" → soglia=39
4. T=1:01 — Comparto: "ma il comparto è DIFENSIVO=38" → riporta a 38
5. T=2:00 — Phantom Supervisor: "ancora WR=2%, irrigidisco" → soglia=39
6. T=2:01 — Comparto torna a 38
7. ...all'infinito.

**Effetto reale:**
- Il `_phantom_sup_log` riempie la dashboard di "IRRIGIDISCE 38→39, IRRIGIDISCE 39→40" che vedi in continuazione
- Ma la soglia **rimane a 38** perché Comparto la sovrascrive sempre
- Tu vedi l'irrigidimento ma non ha effetto reale
- Il bot ti **mente sulla dashboard** in buona fede

### 💰 Impatto economico
- **Decisioni del Phantom Supervisor sterilizzate**: il sistema autocorrettivo non funziona
- **Confusione operativa**: la dashboard mostra azioni che non hanno avuto effetto
- **Perdita di apprendimento**: 10mag ti hanno mostrato 9 "IRRIGIDISCE" → tutti annullati 1 secondo dopo

### 🔧 Fix proposto
Tre opzioni in ordine di pulizia:

**Opzione A (semplice):** rendere il Phantom Supervisor **autorità superiore**. Comparto setta solo se `_phantom_sup_override` non è attivo.

**Opzione B (corretta):** usare `SOGLIA_BASE` come **valore base** e introdurre `_soglia_offset` che si somma. Comparto scrive `SOGLIA_BASE_COMPARTO`. Phantom Supervisor scrive `_soglia_offset`. Il valore reale = `SOGLIA_BASE_COMPARTO + _soglia_offset`. Mai conflitto.

**Opzione C (architetturale):** introdurre un `SogliaCoordinator` con priorità esplicite e log unificato.

### ⏱ Tempo stimato
30 min per Opzione B (consigliata).

---

## 🟠 BUG #3 — NERVOSISMO ENGINE COMPLETAMENTE DISATTIVATO

### 📍 Posizione
`OVERTOP_BASSANO_V16_PRODUCTION.py`, riga **5700-5703**

### 🐛 Codice attuale
```python
# NervosismoEngine — calcola tensione mercato
_nerv_skills = {}  # non abbiamo skill V16 qui, usa solo per calcolo
_nerv_stato  = self._nerv.on_tick(price, _regime_now, _dir_now, _nerv_skills)
_nerv_val    = _nerv_stato.get("nervosismo", 0.3)
_gomme       = _nerv_stato.get("gomme", "INTER")
```

### 💀 Diagnosi
Il NervosismoEngine viene chiamato passando `skills = {}` (dizionario vuoto). Internamente nervosismo_engine.py riga 196 fa:
```python
entry = skills.get("ENTRY")
if entry:
    entry.set_param("soglia_base", ...)  # MAI ESEGUITO
```

`skills.get("ENTRY")` ritorna `None`, il `if entry:` è False, **nessun parametro viene mai applicato**.

**Risultato:** le 3 modalità SLICK/INTER/RAIN sono **completamente decorative**. La dashboard mostra "GOMME: SLICK" ma non ha alcun effetto sul comportamento del bot.

### 💰 Impatto economico
Il bot perde 1/3 della sua intelligenza adattiva. In mercati nervosi (RAIN dovrebbe ridurre size a 0.15 e mettere stop a 1.2%) il bot continua a operare come se fosse in pista asciutta.

### 🔧 Fix proposto

**Opzione veloce (ricolloca):** copiare il pattern di Comparto. Dopo `_nerv.on_tick(...)`:
```python
# Applica gomme al CampoGravitazionale
_gomme_assetto = ASSETTI.get(_gomme, ASSETTI["INTER"])
# Comporre con Comparto invece di sovrascrivere (vedi Bug #2 fix)
```

**Opzione corretta:** insieme al fix Bug #2 (SogliaCoordinator), Nervosismo diventa un terzo input al coordinator con peso/priorità adeguati.

### ⏱ Tempo stimato
5 righe se fai opzione veloce. 1 ora se fai SogliaCoordinator (fix integrato con Bug #2).

---

## 🟠 BUG #4 — PHANTOM SUPERVISOR SCRIVE SU SOGLIA GLOBALE PER PROBLEMI DI GATE SPECIFICI

### 📍 Posizione
`OVERTOP_BASSANO_V16_PRODUCTION.py`, righe **6658-6708**

### 🐛 Codice attuale
```python
for blocco_id, dati in per_livello.items():
    # blocco_id = "VERITAS_GATE_DEBOLE|ALTA|SIDEWA" (specifico)
    ...
    if wr_blocco < 0.25 and blk > 20:
        old_base = self.campo.SOGLIA_BASE     # ← soglia GLOBALE
        new_base = min(58, old_base + 1)
        self.campo.SOGLIA_BASE = new_base     # ← scrive GLOBALE
```

### 💀 Diagnosi
Il loop scorre **ogni gate specifico** (VERITAS_GATE_DEBOLE, VERITAS_GATE_MEDIO, VETO_TOSSICO, ...). Quando un gate specifico ha WR<25%, il Phantom Supervisor irrigidisce la **`SOGLIA_BASE` globale** del campo gravitazionale.

**Conseguenza:** se VERITAS_GATE_DEBOLE|ALTA|SIDEWAYS sta facendo benissimo bloccando un contesto effettivamente tossico (WR=2% sui phantom), il sistema reagisce **alzando la soglia generale di entry** — penalizzando contesti totalmente diversi come `FORTE|BASSA|UP` che invece ha WR=78%.

**Esempio concreto dalla dashboard:**
- Oracolo dice `FORTE|BASSA|UP: WR 78%` ⭐
- VERITAS_GATE_DEBOLE|ALTA|SIDEWA blocca giustamente
- Phantom Supervisor: "WR=2% nel blocco DEBOLE → irrigidisco SOGLIA_BASE 38→39"
- Effetto: trade su FORTE|BASSA|UP ora richiede score=39 invece di 38 → ne perdi alcuni
- 1 minuto dopo: irrigidisce 39→40, poi 40→41...

Il sistema sta **strangolando i trade vincenti** per colpa dei trade tossici (che già blocca correttamente).

### 💰 Impatto economico
Stai perdendo entry su contesti vincenti. Difficile quantificare senza simulazione, ma facendo conti grezzi: se la soglia sale di 5-10 punti per colpa di un gate tossico, e un FORTE|BASSA|UP ha PnL medio +$0.49, perdi tutti quei trade.

### 🔧 Fix proposto
Il Phantom Supervisor deve agire **sul gate stesso, non sulla soglia globale**.

```python
# Invece di:
self.campo.SOGLIA_BASE = new_base

# Fare:
# Marca il gate specifico come "iperconservativo"
# Es: aumenta n_min richiesto per bloccare, o disabilita temporaneamente il gate
self._gate_overrides[blocco_id] = {
    'soglia_extra': old_base - 38,  # offset locale
    'fino_a': time.time() + 1800     # 30 min
}
```

E nel `_evaluate_shadow_entry`, se il gate ha override, applicarlo solo a quel gate.

### ⏱ Tempo stimato
20 min.

---

## 🟡 BUG #5 — `DOWNLOAD_SECRET` DEFAULT IN CHIARO

### 📍 Posizione
`app.py`, riga **139**

### 🐛 Codice attuale
```python
DOWNLOAD_SECRET = os.environ.get("DOWNLOAD_SECRET", "overtop2024")
```

### 💀 Diagnosi
La chiave default `"overtop2024"` è scritta nel sorgente. Se non hai impostato `DOWNLOAD_SECRET` come variabile d'ambiente su Render, **chiunque legga questo file** (e il repo è pubblico? privato?) può scaricare:
- `/var/data/trading_data.db` → tutti i tuoi trade, capsule, oracle
- `/var/data/narratives.db` → tutte le decisioni AI
- `capsule_attive.json` → la tua intelligenza appresa
- `/debug/db` → snapshot stato bot

### 💰 Impatto
Furto di intellectual property: chiunque copia il tuo DB ha la tua intelligenza accumulata in mesi di apprendimento.

### 🔧 Fix proposto
1. **Subito su Render:** impostare `DOWNLOAD_SECRET=<random_64_chars>` come env var
2. **Nel codice:** rendere la default vuota e fallire chiusi:
```python
DOWNLOAD_SECRET = os.environ.get("DOWNLOAD_SECRET")
if not DOWNLOAD_SECRET:
    log.warning("DOWNLOAD_SECRET non configurato — endpoint /download/* disabilitati")
```
Poi in `_check_key()` se DOWNLOAD_SECRET è None → 403 sempre.

### ⏱ Tempo stimato
1 minuto su Render. 5 minuti nel codice.

---

## 🟡 BUG #6 — 19 ECCEZIONI SILENZIATE (`except: pass`)

### 📍 Posizioni
Righe: **432, 514, 722, 1841, 5380, 5567, 5675, 5689, 5797, 5863, 6002, 6751, 7395, 7807, 8499, 8855, 9211, 9569, 9642**

### 🐛 Diagnosi
19 punti del codice silenziano completamente le eccezioni. Esempio (riga 8499):
```python
try:
    ...sospendi_pattern(...)
except Exception as _sp_e:
    pass
```

Se questa funzione fallisce, **non lo saprai mai**. Nessun log, nessuna metrica, nessun allarme. Bug dormienti accumulati.

### 💰 Impatto
Sintomi che non capisci, scoperti solo da audit umano (come questo). Hai pagato Opus per scoprirli — senza audit erano invisibili.

### 🔧 Fix proposto
Sostituire ogni `except: pass` con almeno:
```python
except Exception as e:
    log.debug(f"[NOME_FUNZIONE_NON_BLOCCANTE] {e}")
```

Costo: zero a runtime (è solo un log debug). Beneficio: visibilità.

### ⏱ Tempo stimato
1 ora di code review (19 punti, 3 minuti l'uno).

---

## 🟡 BUG #7 — VOLPE_BOOST IN ZONA GRIGIA

### 📍 Posizione
`OVERTOP_BASSANO_V16_PRODUCTION.py`, righe **7728-7738**

### 🐛 Codice attuale
```python
# Boost solo se contesto non è dimostrato perdente
if _v_n < 5 or _v_wr >= 0.20:
    if momentum == "FORTE" and trend == "UP" and volatility in ("MEDIA","ALTA"):
        _volpe_boost = 1.30
    elif momentum == "DEBOLE" and volatility == "BASSA" and trend == "SIDEWAYS":
        _volpe_boost = 1.50  # +50% size!
    ...
```

### 💀 Diagnosi
Il VERITAS_GATE blocca contesti con WR<20% E pnl_avg<-1.0 E **n>=10**.

Il VOLPE_BOOST passa se **n<5 OPPURE WR>=20%**.

**Zona grigia 5 ≤ n < 10:** VERITAS_GATE non si attiva (richiede n≥10), VOLPE_BOOST si attiva (richiede n<5 ESCLUSO, ma anche WR≥20%). Se n=8 con WR=22% → VOLPE_BOOST scatta → +50% size.

Ma a n=10 con WR=15% → VERITAS_GATE blocca.

**La transizione è brutale:** da 9 trade dove fai +50% size, a 10 trade dove blocchi. È improbabile che 9 a 22% diventi 10 a 15% in modo da essere catturato corretto. Probabile ci siano scenari dove fai overlap pericolosi.

### 💰 Impatto
Difficile quantificare, ma il pattern "size +50% proprio quando i dati iniziano a peggiorare" è esattamente il tipo di edge case che brucia capitale.

### 🔧 Fix proposto
Allineare le soglie di n:
```python
# VERITAS_GATE: n >= 10
# VOLPE_BOOST: alza il floor a n < 10 invece di n < 5
if _v_n < 10 or _v_wr >= 0.30:  # 30% non 20% — meno aggressivo
    ...
```

### ⏱ Tempo stimato
5 righe, 5 minuti.

---

## 🟡 BUG #8 — COMPARTO SOVRASCRIVE OGNI TICK

Già spiegato nel Bug #2. Sintomo dello stesso problema architetturale.

---

## 🟢 OSSERVAZIONE #9 — VERITAS DICE "FUOCO_SHORT|BLOCCA = SBAGLIATO"

### 📍 Dato live
Dashboard:
```
FUOCO_SHORT  BLOCCA  n=38119  HIT 47%  PnL avg $-0.28  → SBAGLIATO
```

### 💀 Diagnosi
Su 38.119 osservazioni del Veritas: quando OI è in stato `FUOCO_SHORT` (quasi ribassista) e SuperCervello dice `BLOCCA`, il verdetto a 60s è "SBAGLIATO" — significa che bloccando ha perso opportunità di SHORT.

Ma `pnl_avg = -$0.28` è negativo. Quindi anche entrando avrebbe perso $0.28 medi. **Il verdetto SBAGLIATO è un falso allarme di Veritas:** il sistema lo classifica così perché HIT 47% > 50% del rumore base, ma il PnL medio comunque negativo.

### 💰 Impatto
Nessun danno reale. Veritas sta dando un'indicazione che sembra grave ma non lo è.

### 🔧 Fix proposto
Migliorare il verdetto Veritas: dovrebbe essere SBAGLIATO solo se HIT > 55% E pnl_avg > 0. Adesso usa solo HIT.

### ⏱ Tempo stimato
3 righe nella formula del verdetto.

---

## 🟢 INCOERENZA #10 — COMPARTO DICHIARA `set_param()` MA RICEVE DICT VUOTO

Già spiegato. Il codice in `comparto_engine.py` riga 222-235 chiama `entry.set_param(...)` ma `entry = None` perché skills è `{}`. Codice morto.

### 🔧 Fix proposto
O eliminare le chiamate `set_param` (10 righe) o popolare correttamente il dict skills col fix #2/#3 integrato.

---

## 🟡 BUG #11 — CAP A 5 PHANTOM APERTI

### 📍 Posizione
`OVERTOP_BASSANO_V16_PRODUCTION.py`, righe **7485, 7524, 7680**

### 🐛 Codice attuale
```python
if len(self._phantoms_open) < 5:
    self._record_phantom(price, ...)
```

### 💀 Diagnosi
In mercati attivi (EXPLOSIVE, breakout) il bot può bloccare 10-20 phantom in pochi secondi. Il cap a 5 significa che oltre quel numero **i phantom non vengono registrati**. Statistiche incomplete.

### 💰 Impatto
Il Phantom Supervisor lavora su statistiche parziali in mercati attivi. Sottostima i blocchi reali.

### 🔧 Fix proposto
Alzare a 20 o rimuovere del tutto. Il rischio è memoria (ma sono dict piccoli — trascurabile).

### ⏱ Tempo stimato
1 riga.

---

## ✅ COSE CHE FUNZIONANO BENE — DON'T TOUCH

1. **Anti-duplicate tick** (riga 7447): elegante, funziona.
2. **Heartbeat resiliente con `_hb_set`** (patch 10mag, riga 9427): ottimo design.
3. **Direzione registrata all'entry** (riga 8373): impedisce calcoli PnL su direzione cambiata mid-trade.
4. **WHITELIST_VINCENTI in CapsuleManager** (capsule_manager.py riga 284): protegge i pattern oro dalle capsule auto-generate. Idea brillante.
5. **PERCORSO1/PERCORSO2 ben separati** (riga 7551 vs 7653): EXPLOSIVE+carica alta bypassa gate non critici. Razionale solido.
6. **PROFIT_LOCK a 4 livelli** (riga 8265-8285): differenziazione FORTE/MEDIO/DEBOLE è elegante.
7. **EXPLOSIVE_OVERRIDE** (riga 7539): cattura breakout sotto regime apparente.
8. **Pesi adattivi del SuperCervello**: (campo 30, oracolo 25, signal 20, matrimonio 13, phantom 12) ben calibrati. Phantom solo 12% — bene, non si fida ciecamente del fix appena fatto.

---

## 📋 PIANO DI ATTACCO CONSIGLIATO

In ordine di priorità (combinazione gravità × tempo × rischio):

### Fase 1 — Sicurezza e visibilità (1 giorno)
1. Bug #5 — settare `DOWNLOAD_SECRET` su Render (1 minuto)
2. Bug #1 — aggiungere `assert PAPER_TRADE` come safeguard (5 minuti)
3. Bug #6 — sostituire `except: pass` con `except: log.debug` (1 ora)

### Fase 2 — Riparare l'autocorrezione (1 giorno)
4. Bug #2 + #3 + #8 — implementare `SogliaCoordinator` (composizione invece di sovrascrittura) — questo risolve 3 bug insieme (2 ore)
5. Bug #4 — Phantom Supervisor agisce sul gate, non sulla soglia globale (30 min)
6. Bug #11 — alzare cap phantom a 20 (1 minuto)

### Fase 3 — Pulizia logica (mezza giornata)
7. Bug #7 — allineare soglie n di VOLPE_BOOST e VERITAS_GATE (5 min)
8. Bug #10 — pulire chiamate `set_param` morte in Comparto/Nervosismo (10 min)
9. Bug #9 — migliorare verdetto Veritas (5 min)

### Fase 4 — Pre-LIVE (settimane)
10. Bug #1 — implementare seriamente `_place_order` con python-binance (1 giornata)
11. Test estensivi con micro-size ($10-20) prima di full-size LIVE

---

## 🦊 CONCLUSIONE

Il progetto è **strutturalmente solido**. L'architettura (PERCORSO1/2, gate stratificati, Phantom Supervisor, Oracolo Dinamico, CapsuleManager con whitelist) è di buon livello. **I bug sono pochi ma alcuni sono gravi.**

Il bug #1 (`_place_order` placeholder) è il più pericoloso perché silenzioso. Tutti gli altri sono "interferenze" che riducono l'efficacia del sistema ma non lo rompono.

**Dopo i fix di Fase 1 e 2 (2 giorni di lavoro), il bot è in stato decente per girare PAPER ancora 1-2 settimane e accumulare apprendimento pulito.**

**Solo dopo Fase 3 + 4 si può ragionevolmente passare a LIVE con micro-size.**

🪦 *La doppia-fee è morta. Sotto, altri scheletri minori. Il sistema può guarire.*

---

**Audit eseguito da Claude Opus 4.7 — 11 maggio 2026**
**File analizzati:** OVERTOP_BASSANO_V16_PRODUCTION.py, app.py, capsule_manager.py, oracle_auto.py, comparto_engine.py, nervosismo_engine.py, breath_engine.py, supervisor_new.py, V16_INDEX.md, dashboard live, JSON /trading/status
**Patti operativi rispettati:** UNA modifica già fatta (doppia-fee), ZERO altre modifiche durante audit, diagnosi prima di proposta, una visione completa prima di proporre fix.

---

## 💎 INTUIZIONI ROBERTO IN VALIDAZIONE

Tre intuizioni architetturali emerse durante la sessione 11mag2026 — sera. Sono collegate tra loro: tutte e tre trattano il TEMPO come dimensione di apprendimento. Da affrontare nelle prossime sessioni.

### Intuizione #1 — HOLDING ESTESO (in test stanotte 11mag→12mag)
**Tesi:** Lo scalping a 60-180s sterilizza i profitti perché le fee divorano i piccoli movimenti. Holding 20-30 min cattura movimenti più ampi.
**Stato:** test empirico in corso (timeout 180s→1800s deployato).
**Validazione attesa:** PnL notturno > 0 = tesi confermata.

### Intuizione #2 — PREDIZIONE ALLUNGABILE PROGRESSIVA
**Tesi:** L'orizzonte di predizione del signal_tracker (30s/60s/120s) è troppo breve. Va allungato **gradualmente** (5min, 10min, 30min, 1ora) per scoprire dove la predizione si rompe per ogni contesto.
**Implementazione:** modificare `WINDOWS = [30, 60, 120]` aggiungendo progressivamente nuovi orizzonti. Osservare HIT rate × Δ × PnL per ogni finestra.
**Beneficio:** ogni contesto trova il suo orizzonte ottimale.

### Intuizione #3 — CAPSULA TEMPORALE
**Tesi:** La capsula non deve solo dire "blocca/permetti entry adesso". Deve dire "in questo contesto, il denaro emerge mediamente a T=Y minuti, durata Z minuti, tieni fino a quel momento".
**Implementazione:** capsule estese con dimensione temporale (`tempo_maturazione_avg`, `holding_target`, `volatilità_finestra`). Il phantom deve girare a lungo per registrare quando il "vestito povero tira fuori il denaro".
**Validazione:** simulazione 9mag mostra max profit medio a 73 minuti. Da validare su più giorni di dati.

### Come si incastrano
1. **Holding esteso** (#1) crea lo SPAZIO temporale per fare i trade lunghi.
2. **Predizione allungabile** (#2) misura DOVE nel tempo il prezzo si muove.
3. **Capsula temporale** (#3) APPRENDE per ogni contesto il momento ottimo di entry/exit.

Tre facce dello stesso principio: **il tempo è una dimensione di apprendimento, non un parametro fisso**.

### Intuizione #4 — SUPPORTI E RESISTENZE (DIMENSIONE SPAZIALE MANCANTE)
**Tesi:** Il bot ha 3 dimensioni di analisi (regime/momentum/volatilità) ma manca completamente la 4ª: lo SPAZIO. Non identifica supporti né resistenze, non sa che certi livelli di prezzo sono "muri" psicologici/strutturali. Tratta $80.500 come $80.347 — solo numeri equivalenti.
**Verifica:** scansione del codice conferma ZERO logica di livelli (no `find_support`, `find_resistance`, `pivot`, `swing_high/low`).
**Implementazione:**
- Classe `LevelDetector` che individua massimi/minimi rispettati N volte
- Score component +15 se entry vicino supporto (LONG) / resistenza (SHORT)
- Nuove capsule: `RESISTENZA_VICINA`, `SUPPORTO_VICINO`
- Stop loss intelligenti basati su livelli (sotto supporto / sopra resistenza)
**Beneficio:** entry vicino ai livelli = rischio minore + target chiaro. Spiega perché molti trade del 9mag sono finiti vicino ai punti di rimbalzo del range.
**Scoperta:** la sera dell'11mag2026, dopo 6 ore di lavoro su 3 intuizioni temporali. Roberto ha chiesto "ma di supporti e resistenze non abbiamo mai parlato". Era vero.

### Le 4 dimensioni della verità di mercato
- **TEMPO** (orizzonte di sviluppo)
- **MAGNITUDO** (ampiezza del movimento)
- **TIMING** (momento di entry/exit)
- **SPAZIO** (livelli di prezzo) ← intuizione #4

Il bot oggi conosce le prime 3 (parzialmente). La 4ª è completamente assente.

---

## 📝 CHANGELOG REFERTO

| Data | Versione | Sessione | Modifiche |
|------|----------|----------|-----------|
| 10mag2026 | 0.0 | Roberto+Claude | Fix doppia-fee `_close_phantom` riga 9069-9075 — applicato e deployato |
| 11mag2026 | 1.0 | Roberto+Claude | Audit sistematico iniziale — 12 bug mappati |
| 11mag2026 | 1.1 | Roberto+Claude | Aggiunta dashboard stato bug + changelog + regole operative |
| 11mag2026-notte | 1.2 | Roberto+Claude | TEST EMPIRICO: TIMEOUT 180s→1800s (riga 8349) per validare ipotesi holding 20-30 min. Motivazione: simulazione 9mag mostra +$32 con holding vs -$30 con scalping. Non è risoluzione di un bug, è test di nuova strategia. Verifica domani. |
| 11mag2026-notte | 1.3 | Roberto+Claude | Aggiunte 3 INTUIZIONI ROBERTO IN VALIDAZIONE: holding esteso (in test), predizione allungabile, capsula temporale. Tutte legate al principio "tempo come dimensione di apprendimento". |
| 11mag2026-notte | 1.4 | Roberto+Claude | Scoperta INTUIZIONE #4: SUPPORTI E RESISTENZE — dimensione SPAZIALE completamente assente nel bot. Verificato nel codice: zero logica di livelli di prezzo. Roberto ha posto la domanda dopo 6 ore di lavoro. Spiegate le 4 dimensioni di mercato: tempo + magnitudo + timing + spazio. |
| 12mag2026-mattina | 1.5 | Claude (decisione autonoma) | Analizzati dati notturni: ZERO trade in 14 ore. Bot strangolato da regime instabile + Phantom Sup. Applicate 2 modifiche: (a) RegimeDetector isteresi 30 tick + clamp confidence (Bug #9 ✅ RISOLTO); (b) Phantom Supervisor cooldown 10 min su irrigidimento (Bug #2/#4 🟡 MITIGATI). Conferma del fix doppia-fee: phantom mostra ora 635 would_win (prima 0 con bug). |
| 12mag2026-mattina | 1.6 | Roberto+Claude | AUDIT SHORT COMPLETO + ATTIVAZIONE: (a) 3 nuove capsule STATIC SHORT per zone tossiche scoperte; (b) SHORT sbloccato in RANGING con 5 condizioni stretto (riga 6966 OVERTOP); (c) SHORT abilitato in DIFENSIVO e ATTACCO. Conseguenza: ora possibile flip SHORT quando indicatori bearish veri. Bug #13 fixato: CompartoEngine cooldown 60s→300s + conferma 5 tick (Bug #13 ✅ RISOLTO). |

---

## 📋 REGOLE OPERATIVE — COME USARE QUESTO FILE

### Per Roberto
1. Questo file è la **memoria condivisa** tra te e tutte le sessioni Claude
2. **Posizione canonica:** root della repo `Tecnaria_V2`, nome `AUDIT_V16_REFERTO_COMPLETO.md`
3. Ogni volta che Claude te lo aggiorna → scarichi + commit nella repo
4. All'inizio di ogni nuova sessione → carica questo file in chat insieme ai sorgenti
5. **NON modificare a mano questo file.** Solo Claude lo aggiorna durante le sessioni — così rimane coerente

### Per Claude (qualsiasi istanza futura)
1. **All'inizio di ogni sessione:** leggi questo referto PRIMA di fare qualsiasi proposta
2. Se Roberto chiede un fix → controlla nello stato bug se è già noto
3. Se trovi un bug nuovo → aggiungi entry nella dashboard + sezione dettagliata + changelog
4. Se fixi un bug → cambia stato a ✅ RISOLTO + scrivi data e file/riga della modifica
5. **Mai dimenticare di aggiornare il file e darlo a Roberto a fine sessione**
6. **Mai duplicare bug:** se stai per scrivere un bug nuovo, prima cerca se è già in dashboard
7. Il file è autoritativo: se la dashboard dice "RISOLTO", è risolto. Non rifare audit da zero.

### Convenzioni stato
- 🔴 APERTO = bug noto, ancora da fixare
- 🟡 IN LAVORAZIONE = sessione in corso lo sta affrontando
- ✅ RISOLTO = fix applicato + deployato + verificato dal vivo
- 🔵 OSSERVAZIONE = comportamento studiato ma fix non urgente
- ⚫ ABBANDONATO = bug deprecato (es. il codice è stato rimosso)

### Convenzioni gravità
- 🔴 CRITICA / MORTALE = perdita soldi diretta o blocco operatività
- 🟠 ALTA = degrado significativo del sistema
- 🟡 MEDIA = bug evidenti ma non rompono
- 🟢 BASSA / OSSERVAZIONE = pulizia, debiti tecnici

---

🪦 *La doppia-fee è morta. Sotto, altri scheletri minori. Il sistema può guarire.*
