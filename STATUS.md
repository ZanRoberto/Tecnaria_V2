# OVERTOP BASSANO V16 — STATUS SESSION 07/05/2026

## STATO SISTEMA
- **Bot:** Paper mode, tecnaria-v2.onrender.com
- **Repo:** ZanRoberto/Tecnaria_V2 (branch: main)
- **Ultimo trade reale:** #772, PnL -$2.00, 05/05/2026 11:56
- **Totale trade:** 259 | WIN: 6 | PnL: -$473.66
- **WR:** 2.3%

---

## COSA FUNZIONA ADESSO

### Precursore Esplosivo ✅
- File: `capsule_manager.py` riga 338
- `_prec2 = (_oi_carica >= 0.80 or _oi_short >= 0.80)`
- `_regime in ("RANGING", "EXPLOSIVE")`
- Confermato nei log: `[PRECURSORE] ⚡ Bypass LEARNED_MAT_TOSSICO_RANGE_VOL_W — OI=0.87 p2=True`

### Fix _check_triggers ✅
- File: `capsule_manager.py` riga 435
- Campi mancanti con operatori numerici (`<`, `>`) → `return False`
- Risolve: STATIC_LONG_MEDIO_ALTA_SIDEWAYS bloccava anche senza oi_carica nel contesto

### Capsule CTX con OI ✅
- Tutte le LEARNED_CTX_* hanno trigger `oi_carica < 0.70`
- Non bloccano quando OI è alto

### Oracle Auto — SKIP update ✅
- File: `oracle_auto.py` riga 784
- Non riabilita SC_EVITA quando MIGLIORA attiva protegge il contesto
- Fix frozenset ordine-insensitive in `_has_migliora_attiva`

### Capsule permanenti BLOCCA_CONTESTO ✅
- Non vengono mai più salvate permanentemente
- File: `OVERTOP_BASSANO_V16_PRODUCTION.py` riga 5687

### Autocorrettore regime dual-window ✅
- Finestra 50+200 tick, soglia 0.15%
- Riconosce TRENDING_BULL anche con movimenti graduali

---

## NUOVO — MECCANISMO SOSPENSIONE (da deployare)

### Filosofia
- Prima perdita → SOSPENSIONE (non EVITA immediata)
- Phantom osserva 3 trade shadow
- Dopo 3 shadow → decide automaticamente:
  - WR < 34% e max_win < $2 → EVITA con `oi_carica < 0.70`
  - Altrimenti → MIGLIORA con retreat 25%

### File modificati (pronti in outputs)
1. `capsule_manager.py` — metodi: `sospendi_pattern()`, `registra_shadow()`, `_decide_sospensione()`
2. `oracle_auto.py` — classificazione EVITA → SOSPENSIONE
3. `OVERTOP_BASSANO_V16_PRODUCTION.py` — Phantom collegato a `registra_shadow()`, perdita reale collega a `sospendi_pattern()`

### DA FARE prima del deploy
- Verificare che `_shadow_entry_momentum` esista nel bot al momento dell'exit
- Test syntax su tutti e tre i file ✅

---

## CAPSULE DB — STATO ATTUALE

### Attive e corrette
| ID | Tipo | Trigger |
|---|---|---|
| LEARNED_MAT_TOSSICO_RANGE_VOL_W | EVITA | matrimonio=RANGE_VOL_W |
| LEARNED_MAT_TOSSICO_RANGE_VOL_M | EVITA | matrimonio=RANGE_VOL_M |
| LEARNED_MAT_TOSSICO_WEAK_NEUTRAL | EVITA | matrimonio=WEAK_NEUTRAL |
| LEARNED_CTX_DEBOLE_ALTA_SIDEWAYS | EVITA | contesto + oi<0.70 |
| LEARNED_CTX_MEDIO_ALTA_SIDEWAYS | EVITA | contesto + oi<0.70 |
| LEARNED_CTX_DEBOLE_BASSA_SIDEWAYS | EVITA | contesto + oi<0.70 |
| LEARNED_CTX_DEBOLE_MEDIA_SIDEWAYS | EVITA | contesto + oi<0.70 |
| MIGLIORA_PROFIT_LOCK_FORTE_ALTA_SIDEWAYS | MIGLIORA | retreat 30% |
| MIGLIORA_PROFIT_LOCK_RANGE_DEAD | MIGLIORA | retreat 20% |

### Disabilitate
- LEARNED_MAT_TOSSICO_RANGE_DEAD — sostituita da MIGLIORA
- SC_EVITA_BTC_LONG_FORTE_ALTA_SIDEWAYS — Oracle Auto non la rigenera più

---

## BUG APERTI

1. `[BRAIN] ❌ 'int' object is not subscriptable` — CapsuleIntelligente, non critico ma da investigare
2. `[CM] dashboard: unsupported operand type(s) for -: 'float' and 'str'` — cosmetic, non blocca trading

---

## REGOLE OPERATIVE

- **Mai modificare file su Render senza aggiornare GitHub** — ogni deploy sovrascrive
- **Una modifica alla volta** verificata prima della successiva
- **Prima di ogni modifica** → `python3 -c "import ast; ast.parse(open('file.py').read())"`
- **Fee reale:** $2.00 per trade | TRADE_SIZE=$1000 | LEVERAGE=5x
- **PnL formula:** `delta × (exposure/entry_price) - fee`
- **Backup locale** prima di ogni deploy importante

---

## FILOSOFIA SISTEMA (non dimenticare)

- EVITA = decisione umana da dati reali — mai automatica
- MIGLIORA = può essere automatica — migliora exit
- SOSPENSIONE = prima perdita → osserva → poi decide
- Le capsule sono come pistole — con licenza proteggono, senza licenza uccidono
- Il precursore bypassa i matrimoni tossici quando OI >= 0.80 in RANGING/EXPLOSIVE
- RANGE_VOL_W max_win $1.07 < fee $2.00 → EVITA corretta
- RANGE_DEAD max_win $6.22 → MIGLIORA retreat 20%

---

## TRADE VINCENTI STORICI (riferimento)

| ID | PnL | Matrimonio | Contesto | Score | Exit |
|---|---|---|---|---|---|
| 516 | +$6.22 | RANGE_DEAD | DEBOLE\|BASSA\|SIDEWAYS | 48.29 | PROFIT_LOCK_E15 |
| 557 | +$1.37 | RANGE_VOL_M | MEDIO\|ALTA\|SIDEWAYS | 75.0 | PROFIT_LOCK_E15 |
| 561 | +$1.07 | RANGE_VOL_W | DEBOLE\|ALTA\|SIDEWAYS | 75.0 | EXIT_E40 |
| 622 | +$0.12 | RANGE_VOL_M | MEDIO\|ALTA\|SIDEWAYS | 75.0 | EXIT_E40 |
| 655 | +$1.47 | RANGE_VOL_M | MEDIO\|ALTA\|SIDEWAYS | 75.0 | PROFIT_LOCK_E40 |
| 764 | +$2.05 | RANGE_VOL_F | FORTE\|ALTA\|SIDEWAYS | 59.64 | PROFIT_LOCK_E55 |

