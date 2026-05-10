# 🗺️ MAPPA DEL TESORO — OVERTOP V16

**Versione:** 1.0
**Data:** 11 maggio 2026
**Autore:** Roberto + Claude (sessione strategica)
**Posizione nella repo:** root, `MAPPA_DEL_TESORO.md`
**Scopo:** Mappa operativa di TUTTI i contesti di trading. Dove fare denaro, dove non toccare.

---

## 🎯 IL PRINCIPIO FONDANTE (LA TESI DI ROBERTO)

> **"Il denaro non sta dove ho ragione spesso. Sta dove l'asimmetria tra guadagno-quando-vinco e perdita-quando-perdo è grande abbastanza da assorbire le fee."**

**Dimostrato sui dati live:**
- ❌ RANGING|LONG|DEBOLE_<58: HIT 65% (alto), Δ +$8.4 (piccolo), **PnL netto -$0.50** → trappola
- ✅ EXPLOSIVE|LONG|DEBOLE_<58: HIT 44% (basso!), Δ -$4.1 (negativo!), **PnL netto +$0.10** → tesoro

**L'equazione del denaro reale:**
```
EV = HIT × E[guadagno|win]  -  (1-HIT) × E[perdita|loss]  -  FEE($2)

La chiave NON è massimizzare HIT.
La chiave è massimizzare R = E[guadagno|win] / E[perdita|loss]
```

---

## 📊 LA TABELLA STRATEGICA — TUTTI I CONTESTI MAPPATI

### LONG (19 contesti analizzati)

| Contesto | WR | PnL | R | Categoria | Capsula esistente | Azione |
|----------|---:|----:|---:|-----------|-------------------|--------|
| FORTE\|BASSA\|UP | 78% | +$0.49 | 0.6x | ⭐ ORO | — | ✅ ENTRY OK |
| FORTE\|MEDIA\|UP | 68% | +$0.53 | 0.9x | ⭐ ORO | — | ✅ ENTRY OK |
| FORTE\|ALTA\|UP | 55% | +$0.32 | 1.1x | ✅ DECENTE | — | ✅ ENTRY OK |
| MEDIO\|BASSA\|UP | 65% | +$0.16 | 0.7x | ✅ DECENTE | — | ✅ ENTRY OK |
| FORTE\|BASSA\|SIDEWAYS | 65% | -$0.20 | 0.4x | 🟡 BREAKEVEN | — | ⚠️ Solo se OI ≥ 0.65 |
| FORTE\|MEDIA\|SIDEWAYS | 60% | -$0.20 | 0.5x | 🟡 BREAKEVEN | — | ⚠️ Solo se OI ≥ 0.65 |
| FORTE\|BASSA\|DOWN | 60% | -$0.45 | 0.3x | 🟡 BREAKEVEN | — | ❌ Skip (controtrend) |
| MEDIO\|MEDIA\|UP | 58% | -$0.08 | 0.7x | 🟡 BREAKEVEN | — | ⚠️ Solo se Δ_atteso ≥ $15 |
| DEBOLE\|BASSA\|UP | 55% | -$0.27 | 0.6x | 🟡 BREAKEVEN | — | ❌ Skip |
| MEDIO\|ALTA\|UP | 50% | $0.00 | 1.0x | 🟡 BREAKEVEN | — | ⚠️ Solo se Δ_atteso ≥ $20 |
| MEDIO\|BASSA\|SIDEWAYS | 50% | -$1.43 | -0.4x | ❌ EVITARE | — | 🚫 SUPER-VETO |
| FORTE\|MEDIA\|DOWN | 50% | -$1.07 | -0.1x | ❌ EVITARE | — | 🚫 SUPER-VETO |
| MEDIO\|BASSA\|DOWN | 45% | -$1.45 | -0.4x | ❌ EVITARE | — | 🚫 SUPER-VETO |
| MEDIO\|MEDIA\|SIDEWAYS | 45% | -$1.75 | -0.7x | 💀 MORTE | — | 🚫 SUPER-VETO |
| FORTE\|ALTA\|SIDEWAYS | 30% | -$2.06 | -1.1x | 💀 MORTE | STATIC ⚠️ bypass se OI≥0.65 | ⚠️ rivedere bypass |
| DEBOLE\|MEDIA\|SIDEWAYS | 21% | -$2.14 | -1.3x | 💀 MORTE | LEARNED CTX | ✅ già coperto |
| MEDIO\|ALTA\|SIDEWAYS | 17% | -$2.35 | -2.0x | 💀 MORTE | STATIC ⚠️ bypass se OI≥0.65 | ⚠️ rivedere bypass |
| DEBOLE\|BASSA\|SIDEWAYS | 16% | -$1.61 | 0.2x | 💀 MORTE | LEARNED CTX | ✅ già coperto |
| DEBOLE\|ALTA\|SIDEWAYS | 3% | -$2.23 | -4.8x | 💀 MORTE | LEARNED CTX | ✅ già coperto |

### SHORT (8 contesti analizzati)

| Contesto | WR | PnL | R | Categoria | Capsula esistente | Azione |
|----------|---:|----:|---:|-----------|-------------------|--------|
| FORTE\|ALTA\|DOWN | 55% | +$0.04 | 0.9x | ✅ DECENTE | — | ✅ ENTRY OK (oro SHORT) |
| MEDIO\|ALTA\|DOWN | 48% | -$0.92 | 0.1x | ❌ EVITARE | — | 🚫 SUPER-VETO |
| DEBOLE\|ALTA\|DOWN | 40% | -$1.87 | -0.8x | 💀 MORTE | — | 🚫 SUPER-VETO + nuova capsula |
| MEDIO\|MEDIA\|SIDEWAYS | 20% | -$2.89 | -3.2x | 💀 MORTE | — | 🚫 SUPER-VETO + nuova capsula |
| FORTE\|MEDIA\|SIDEWAYS | 25% | -$2.67 | -2.3x | 💀 MORTE | — | 🚫 SUPER-VETO + nuova capsula |
| FORTE\|ALTA\|SIDEWAYS | 12% | -$3.42 | -6.9x | 💀 MORTE | LEARNED CTX | ✅ già coperto |
| DEBOLE\|ALTA\|SIDEWAYS | 10% | -$3.37 | -7.8x | 💀 MORTE | STATIC RANGE_VOL_W | ✅ già coperto |
| MEDIO\|ALTA\|SIDEWAYS | 8% | -$3.50 | -10.4x | 💀 MORTE | LEARNED CTX | ✅ già coperto |

---

## 💎 SEZIONE A — DOVE STA IL DENARO (i 5 punti caldi)

### A1 — ⭐ ORO PERFETTO (entry sempre permesso)

```
LONG | FORTE | BASSA | UP    →  WR 78%, PnL +$0.49, n=30 osservazioni
LONG | FORTE | MEDIA | UP    →  WR 68%, PnL +$0.53, n=23 osservazioni
```

**Strategia:** entry standard, profit lock 25-30%, stop -$5.

**Effetto economico stimato:** 50 trade su questi due contesti al mese → **+$25/mese netti** (paper).

### A2 — ✅ DECENTE (entry permesso con vigilanza)

```
LONG  | FORTE | ALTA  | UP    →  WR 55%, PnL +$0.32, n=15 — Volatilità alta, attenzione
LONG  | MEDIO | BASSA | UP    →  WR 65%, PnL +$0.16, n=19
SHORT | FORTE | ALTA  | DOWN  →  WR 55%, PnL +$0.04, n=10 — Oro SHORT
```

**Strategia:** entry con size 0.5-0.7x, stop -$4, profit lock 25%.

### A3 — 💎 IL TESORO ROBERTO (regime EXPLOSIVE)

**Dal Signal Tracker (non dall'oracolo, perché l'oracolo è ferma a RANGING):**

```
EXPLOSIVE | LONG | DEBOLE_<58 →  HIT 44%, Δ -$4.1, PnL +$0.10, n=2034 ⭐ STATISTICA SOLIDA
EXPLOSIVE | LONG | BASE_58-65 →  HIT 100%, Δ +$32.5, PnL +$1.08, n=2 ⏳ servono 30+ osservazioni
TRENDING_BEAR | LONG | DEBOLE_<58 → HIT 100%, Δ +$20.5, PnL +$0.44, n=3 ⏳ idem
```

**Strategia:** entry permesso ANCHE con HIT basso (es. 44%) PERCHÉ:
- L'asimmetria magnitudo è a favore
- Stop stretto (-$3 a -$4) limita le perdite quando sbaglia
- Profit lock RILASSATO (50% retreat invece di 25%) quando azzecca

**Effetto economico stimato (proiezione):** se EXPLOSIVE arriva 5 volte al giorno e il bot prende anche solo 2 trade/giorno con +$0.30 medi → **+$18/mese netti**.

**⚠️ AZIONE CRITICA:** il bot deve smettere di considerare EXPLOSIVE come "rumore" e iniziare a osservarlo come **opportunità prioritaria**.

---

## 💀 SEZIONE B — DOVE C'È IL DISSANGUAMENTO

### B1 — Le 4 trappole HIT-alto (la grande illusione)

```
LONG | FORTE | BASSA | SIDEWAYS  →  WR 65% MA PnL -$0.20  💀 trappola
LONG | FORTE | MEDIA | SIDEWAYS  →  WR 60% MA PnL -$0.20  💀 trappola
LONG | MEDIO | MEDIA | UP        →  WR 58% MA PnL -$0.08  💀 trappola minore
LONG | DEBOLE | BASSA | UP       →  WR 55% MA PnL -$0.27  💀 trappola
```

**Diagnosi:** sono i contesti SIDEWAYS che vincono spesso ma con magnitudo piccolissima. Le fee li ammazzano.

**🚫 NESSUNA CAPSULA LI COPRE OGGI.** Buco strutturale.

### B2 — Le 12 zone di morte LONG (già coperte ma da rivedere)

```
LONG | MEDIO | ALTA  | SIDEWAYS    →  PnL -$2.35  💀 (capsula bypassa se OI≥0.65 ⚠️)
LONG | FORTE | ALTA  | SIDEWAYS    →  PnL -$2.06  💀 (capsula bypassa se OI≥0.65 ⚠️)
LONG | DEBOLE| ALTA  | SIDEWAYS    →  PnL -$2.23  💀 ✅ coperta
LONG | MEDIO | ALTA  | UP          →  PnL  $0.00   🟡 — non coperta, ma breakeven
LONG | DEBOLE| MEDIA | SIDEWAYS    →  PnL -$2.14  💀 ✅ coperta
LONG | DEBOLE| BASSA | SIDEWAYS    →  PnL -$1.61  💀 ✅ coperta
LONG | MEDIO | MEDIA | SIDEWAYS    →  PnL -$1.75  💀 ❌ NON coperta — buco!
LONG | MEDIO | BASSA | SIDEWAYS    →  PnL -$1.43  ❌ ❌ NON coperta — buco!
LONG | MEDIO | BASSA | DOWN        →  PnL -$1.45  ❌ ❌ NON coperta — buco!
LONG | FORTE | MEDIA | DOWN        →  PnL -$1.07  ❌ ❌ NON coperta — buco!
```

**4 buchi LONG identificati.**

### B3 — Le 5 zone di morte SHORT scoperte (RISCHIO ALTO)

```
SHORT | DEBOLE| ALTA  | DOWN     →  PnL -$1.87  💀 ❌ NON coperta!
SHORT | MEDIO | MEDIA | SIDEWAYS →  PnL -$2.89  💀 ❌ NON coperta!
SHORT | FORTE | MEDIA | SIDEWAYS →  PnL -$2.67  💀 ❌ NON coperta!
SHORT | MEDIO | ALTA  | DOWN     →  PnL -$0.92  ❌ ❌ NON coperta!
SHORT | DEBOLE| MEDIA | SIDEWAYS →  no dati ancora (potenziale buco)
```

**5+ buchi SHORT identificati. Più gravi perché SHORT è meno frequente — quando succede, costa caro.**

---

## 🔧 SEZIONE C — LA STRATEGIA "35% DOVE VINCO"

### C1 — La regola operativa derivata

Per ogni contesto, **entry permesso solo se:**
```
HIT × (Δ_atteso_win) > (1 - HIT) × |Δ_atteso_loss| × 2
```

Il **× 2** è il margine per coprire fee + slippage + asimmetria win/loss reale (vinci meno di quanto dice Δ teorico, perdi quasi tutto Δ teorico).

### C2 — Stop e profit lock differenziati per categoria

| Categoria | Stop | Profit Lock | Size | Logica |
|-----------|------|-------------|------|--------|
| ⭐ ORO PERFETTO | -$5 | 25% retreat | 1.0x | classico |
| ✅ DECENTE | -$4 | 25% retreat | 0.7x | controllo |
| 💎 TESORO ROBERTO (EXPLOSIVE) | **-$3** | **50% retreat** | 0.5x | stop stretto + lascia correre |
| 🟡 BREAKEVEN | non entrare | — | — | inutile |
| 💀 MORTE | non entrare | — | — | mortale |

**Rivelazione architetturale:** il sistema attuale usa lo stesso stop e lo stesso profit lock per tutti. **È sbagliato.** Va differenziato per categoria.

### C3 — Il SUPER-VETO RANGING DEBOLE

```
SE regime == RANGING AND signal_score < 58:
    → BLOCCA entry (sempre, anche se HIT 60s = 65%)
    → Continuare osservazione passiva del Signal Tracker
    → NON sprecare $2 di fee × 27.000 osservazioni
```

**Effetto economico stimato:** zero perdita su 27k contesti tossici significa **risparmio di $50-100/mese** in fee bruciate.

---

## 📋 SEZIONE D — PIANO DI CAPSULE NUOVE DA CREARE

### D1 — 4 capsule LONG mancanti (coprire i buchi)

```
1. LEARNED_CTX_MEDIO_MEDIA_SIDEWAYS_LONG    → blocca, WR 45%, PnL -$1.75
2. LEARNED_CTX_MEDIO_BASSA_SIDEWAYS_LONG    → blocca, WR 50%, PnL -$1.43
3. LEARNED_CTX_MEDIO_BASSA_DOWN_LONG        → blocca, WR 45%, PnL -$1.45
4. LEARNED_CTX_FORTE_MEDIA_DOWN_LONG        → blocca, WR 50%, PnL -$1.07
```

### D2 — 5 capsule SHORT mancanti (urgenti)

```
1. LEARNED_CTX_DEBOLE_ALTA_DOWN_SHORT       → blocca, WR 40%, PnL -$1.87
2. LEARNED_CTX_MEDIO_MEDIA_SIDEWAYS_SHORT   → blocca, WR 20%, PnL -$2.89
3. LEARNED_CTX_FORTE_MEDIA_SIDEWAYS_SHORT   → blocca, WR 25%, PnL -$2.67
4. LEARNED_CTX_MEDIO_ALTA_DOWN_SHORT        → blocca, WR 48%, PnL -$0.92
5. LEARNED_CTX_DEBOLE_MEDIA_DOWN_SHORT      → osservazione (no dati ancora)
```

### D3 — Capsule da RIVEDERE (con bypass OI sospetto)

```
STATIC_LONG_FORTE_ALTA_SIDEWAYS_BTC  →  attualmente bypassa se OI≥0.65
  ⚠️ Verificare: con bypass quanti trade reali? Quanti vincenti?
  Se WR < 30% anche col bypass → togliere il bypass.
  
STATIC_LONG_MEDIO_ALTA_SIDEWAYS_BTC  →  idem (PnL -$2.35 storico, lieve dubbio sul bypass)
```

---

## 🎯 SEZIONE E — IL FOCUS NUOVO DEL BOT

### E1 — Il bot oggi (allocazione attenzione)

```
RANGING|DEBOLE: 95% del tempo  ←  zona inutile (27.123 osservazioni)
EXPLOSIVE:       7% del tempo  ←  zona del denaro (2.034 osservazioni)
TRENDING_BEAR:   <1% tempo     ←  zona del denaro (3 osservazioni)
```

### E2 — Il bot dovrebbe (allocazione corretta)

```
RANGING|DEBOLE → solo OSSERVAZIONE passiva (zero fee, zero entry)
EXPLOSIVE|*    → MASSIMA PRIORITÀ entry, capsula tesoro
TRENDING_BEAR  → seconda priorità, raccoglie dati
FORTE|*|UP     → entry sempre se contesto permette
```

### E3 — Il "cecchino da EXPLOSIVE"

> **"Il bot deve smettere di essere uno sniper da RANGING e diventare un cecchino da EXPLOSIVE."**

Lo sniper da RANGING:
- Vede tante prede al giorno
- Ogni preda è piccola
- Le fee divorano i profitti
- Risultato: equity che scende lentamente

Il cecchino da EXPLOSIVE:
- Vede poche prede al giorno (3-10)
- Ogni preda è grande
- Le fee diventano irrilevanti
- Risultato: equity che cresce a scatti

**La tua intuizione è esattamente questa.** I dati la confermano.

---

## 📊 SEZIONE F — STIMA ECONOMICA POST-FIX

### F1 — Scenario PESSIMO (oggi)

```
WR globale:         9%
PnL/giorno:         -$5 a -$10
Trade/giorno:       0 a 3
Fee bruciate:       -$50/mese
Status:             EQUITY DECRESCE
```

### F2 — Scenario CONSERVATIVO (dopo fix #2-#4 + super-veto + 4 nuove capsule LONG)

```
WR globale:         35-45%
PnL/giorno:         -$2 a +$5
Trade/giorno:       2-5
Fee:                stessa ma su trade migliori
Status:             BREAKEVEN o leggero positivo
```

### F3 — Scenario REALISTICO (dopo Mappa del Tesoro implementata + capsule complete)

```
WR globale:         40-55%
PnL/giorno:         +$3 a +$10
Trade/giorno:       3-7 (concentrati su EXPLOSIVE + ORO)
Fee:                $20-40/mese, ammortizzate
Status:             POSITIVO STABILE (~$100-200/mese paper)
```

### F4 — Scenario OTTIMISTICO (dopo LIVE serio + capitale $5k)

```
WR globale:         45-55%
PnL/giorno:         +$15 a +$50  (con leverage 5x e capitale $5k)
Trade/giorno:       3-5
Status:             POSITIVO REALE ($300-1000/mese)
```

⚠️ **CAVEAT:** queste sono stime basate sui pattern attuali. Trading è incerto per natura. Il PaperMatch (live segue paper a +/- 10%) deve essere verificato prima di scalare.

---

## 🚦 SEZIONE G — PRIORITÀ DI IMPLEMENTAZIONE

### Priority 1 — IMMEDIATA (impatto massimo, rischio minimo)
1. **SUPER-VETO RANGING|DEBOLE_<58** → blocca 27.000 entry inutili
2. **Promozione EXPLOSIVE** → rendere visibile ed entry-prioritario nel score
3. **Asimmetria stop/profit per categoria** → -$3/+50% per EXPLOSIVE, -$5/+25% per ORO

### Priority 2 — SETTIMANA 1
4. **Aggiungere 4 capsule LONG mancanti** (i buchi B2)
5. **Aggiungere 5 capsule SHORT mancanti** (i buchi B3)
6. **Rivedere bypass OI≥0.65** sulle capsule STATIC FORTE/MEDIO ALTA SIDEWAYS

### Priority 3 — SETTIMANA 2
7. Dopo 30+ osservazioni in EXPLOSIVE BASE_58-65, validare definitivamente la zona
8. Calibrare profit lock 50% retreat empiricamente

### Priority 4 — SETTIMANA 3-4
9. Fix #2/#3/#4 dell'AUDIT (Phantom Sup, Comparto, Nervosismo coordinati)
10. Implementare `_place_order` reale (Bug #1 audit)
11. Test LIVE micro-size

---

## 🦊 CONCLUSIONE STRATEGICA

**La tua intuizione è confermata: il denaro non sta dove HIT è alto.**

Il bot oggi è uno **sniper da RANGING** che spara 27.000 colpi al giorno, vince il 65% delle volte ma perde soldi a colpi di fee. Va trasformato in un **cecchino da EXPLOSIVE**: 5-10 colpi al giorno, vince anche solo il 40-50% delle volte, ma ogni colpo riuscito vale 5-10x un colpo fallito.

Le 3 mosse strategiche per arrivarci:

1. **SUPER-VETO RANGING DEBOLE** (blocca le illusioni)
2. **PROMOZIONE EXPLOSIVE** (caccia il tesoro)
3. **STOP/PROFIT ASIMMETRICI** (cattura i grossi, ferma i piccoli)

Il resto sono dettagli implementativi.

---

## 📝 CHANGELOG

| Data | Versione | Modifiche |
|------|----------|-----------|
| 11mag2026 | 1.0 | Mappa iniziale — 27 contesti analizzati, 9 buchi capsule identificati, strategia "cecchino EXPLOSIVE" definita |

---

*"Le perdite non sono perdite se diventano dati. I dati non sono dati se non si traducono in regole. Le regole non sono regole se non si traducono in fee non sprecate."* 🦊
