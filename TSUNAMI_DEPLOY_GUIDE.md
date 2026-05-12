# 🌊 TSUNAMI DETECTOR — DEPLOY GUIDE
**Versione:** V16 + Tsunami | **Data:** 12 maggio 2026

---

## 📦 FILE DA DEPLOYARE

Due file nuovi/aggiornati:

| File | Stato | Riepilogo |
|------|-------|-----------|
| `OVERTOP_BASSANO_V16_PRODUCTION.py` | **MODIFICATO** | Aggiunto: import TsunamiEngine, istanza, feed tick, VETO entry, persistenza |
| `tsunami_detector.py` | **NUOVO** | 501 righe, 3 moduli + facade. Da creare su repo. |

`capsule_manager.py` è già deployato col FIX #19 (stamattina).

---

## 🚀 PROCEDURA DEPLOY — Step by step

### STEP 1 — Aggiungi il file nuovo `tsunami_detector.py` su GitHub

1. Vai su `github.com/ZanRoberto/Tecnaria_V2`
2. Click "Add file" → "Create new file"
3. Nome file: `tsunami_detector.py`
4. Copia il contenuto da `tsunami_detector.py` (link sopra)
5. Commit message: `add: TsunamiDetector multi-scala 30s/2min/10min (invenzione Roberto)`
6. Click "Commit new file"

### STEP 2 — Aggiorna `OVERTOP_BASSANO_V16_PRODUCTION.py`

1. GitHub → `OVERTOP_BASSANO_V16_PRODUCTION.py`
2. Click matita ✏️
3. Ctrl+A → Delete
4. Incolla nuovo contenuto
5. Commit message: `fix #20 + tsunami integration: VETO multi-scala + persistenza candele`
6. Push

### STEP 3 — Aspetta deploy verde

Render Dashboard → `tecnaria-v2` → aspetta che il pallino diventi 🟢 verde (2-3 minuti).

---

## 🔍 VERIFICHE POST-DEPLOY

### ✅ Verifica 1 — Import OK

Subito dopo deploy, dallo Shell:

```bash
grep "TsunamiEngine disponibile" /opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py | head -2
grep -c "FIX #20" /opt/render/project/src/OVERTOP_BASSANO_V16_PRODUCTION.py
```

**Aspettativa:** 
- prima riga restituisce il commento del log
- seconda riga: `2`

### ✅ Verifica 2 — Init OK (guarda i log Render)

Sui log del bot, nei primi secondi di vita dovresti vedere:

```
[TSUNAMI] 🌊 TsunamiEngine disponibile
[TSUNAMI] 🌊 TsunamiEngine inizializzato (30s + 2min + 10min)
```

Se vedi `⚠️ tsunami_detector.py non trovato` → file non caricato, ripeti STEP 1.

### ✅ Verifica 3 — Tick alimentano TsunamiEngine

Dopo 5-10 minuti, lancia dallo Shell:

```bash
python3 -c "
import sqlite3, json
conn = sqlite3.connect('/var/data/trading_data.db')
cur = conn.cursor()
cur.execute(\"SELECT value FROM bot_state WHERE key='runtime_state'\")
row = cur.fetchone()
if row:
    d = json.loads(row[0])
    ts = d.get('tsunami_state')
    if ts:
        print(f'Candele 30s:   {len(ts.get(\"30s\", []))}')
        print(f'Candele 2min:  {len(ts.get(\"2min\", []))}')
        print(f'Candele 10min: {len(ts.get(\"10min\", []))}')
    else:
        print('tsunami_state non presente nel save')
"
```

**Aspettative (dopo 5-10 min):**
- 30s: 8-15 candele
- 2min: 2-4 candele  
- 10min: 0-1 candele

(le candele 10min si formano lentamente)

### ✅ Verifica 4 — VETO in azione

Sui log del bot, cerca queste righe (potrebbero apparire a flusso):

```
🌊 TSUNAMI_VETO: NO TSUNAMI multi-scala — 30s:SCHIUMA(NONE) 2min:SCHIUMA(NONE)...
🌊 TSUNAMI_DISCORDE: campo=LONG vs tsunami=SHORT (2/3) → blocca
🌊 TSUNAMI_OK: LONG confidenza=3/3 size_mult=1.00
```

Significato:
- `TSUNAMI_VETO`: nessuna delle 3 scale rileva tsunami → bot non entra (normale per mercati lenti)
- `TSUNAMI_DISCORDE`: campo vorrebbe entrare LONG ma tsunami vede SHORT → blocca (PROTEZIONE)
- `TSUNAMI_OK`: tsunami concorda con direction del campo → entry consentito

### ✅ Verifica 5 — Persistenza candele (test restart)

1. Aspetta 30 minuti dopo deploy (così le candele 30s sono almeno 30 e le 2min sono 10+)
2. Render → Manual Deploy
3. Dopo restart, cerca nei log:

```
[RUNTIME_LOAD] 🌊 Tsunami candele ripristinate: 30s=N 2min=N 10min=N
```

**Se vedi questa riga → ✅ FIX #20 funziona, candele sopravvivono ai restart.**

---

## 🧪 COSA ASPETTARSI NEI PRIMI 2 GIORNI

### Giorno 1 (oggi)
- 🌊 **Tante righe TSUNAMI_VETO**: normale, mercato BTC è lento
- 🟡 **Pochissimi trade**: il bot sarà MOLTO conservativo
- **OK così** — il TsunamiDetector vuole esplicitamente filtrare la schiuma

### Giorno 2-3
- Quando mercato fa un movimento vero (>$200 in 30-60 min):
  - 🌊 **TSUNAMI_OK LONG/SHORT confidenza 2-3/3**
  - 🎯 Entry SOLO in quei momenti
  - Aspettativa: WR > 50% (vs 11% di prima)

### Giorno 7
- Avrai 20-50 trade. **Decidiamo soglie**:
  - Se WR > 50% → tutto bene, lasciamo girare
  - Se WR ancora bassa → analisi: forse soglie troppo blande
  - Se 0 trade in 1 settimana → soglie troppo strette, allenta `SOGLIA_FORZA` da 0.50 a 0.45

---

## 🔧 COME REGOLARE LE SOGLIE (se serve)

In `tsunami_detector.py` riga ~210:

```python
class TsunamiDetector:
    MIN_CANDELE        = 6      # candele minime
    SOGLIA_COERENZA    = 0.55   # % candele direzione concorde
    SOGLIA_FORZA       = 0.50   # score minimo per tsunami
```

- **Bot non trada mai**: abbassa SOGLIA_FORZA a 0.40
- **Bot trada male**: alza SOGLIA_FORZA a 0.60
- **Bot ignora setup veri**: abbassa SOGLIA_COERENZA a 0.50

Modifica su GitHub, push, restart.

---

## 📊 SOMMARIO RAPIDO COSA CAMBIA

### Prima
- SeedScorer su 50 tick (~5 sec) → **misurava il rumore**
- Score ≥ 34 → entry permesso (in zona sterile)
- Result: 11% WR, -$18 al giorno

### Dopo
- TsunamiDetector multi-scala (30s + 2min + 10min) → **misura la struttura**
- Solo se TUTTE le scale dicono "tsunami" → entry
- Result atteso: meno trade, WR > 50%

---

## 🦊 NOTA FINALE

Il TsunamiDetector è **AFFIANCO** al SeedScorer, non lo sostituisce.

Pipeline completa di entry:
1. State Engine: stato bot OK
2. Seed: dati sufficienti
3. **🌊 TSUNAMI: verifica multi-scala** ← NUOVO
4. Veritas Gate: contesto non tossico
5. Capsule: nessuna LEARNED blocca
6. Score finale ≥ soglia

Solo se TUTTI e 6 dicono OK → entry.

---

**Autore invenzione fisica:** Roberto Zanardo  
**Implementazione:** 12 maggio 2026  
**Repo:** ZanRoberto/Tecnaria_V2  
**Live:** tecnaria-v2.onrender.com
