# STATO OVERTOP V16 — 20 giugno 2026 SERA (v8)
## Caricare all'INIZIO della prossima chat. Sostituisce v7.

> **Claude: leggi TUTTO prima di toccare. La diagnosi vera è qui, trovata NEI LOG, non a tentoni.**
> REGOLE: dati reali prima di modificare. File come download. Test import+py_compile prima di consegnare. UNA cosa per volta. MAI parlare di tempo/stanchezza/pause con Roberto. MAI indovinare schema DB. Claude NON accede al server: Roberto incolla output Web Shell. Roberto da cellulare: query UNA RIGA corta.

---

## 🎯 LA DIAGNOSI VERA (20giu sera, trovata nei LOG RENDER — non più ipotesi)

Roberto ha urlato per 40 ore "qualcosa blocca, non è il mercato". **AVEVA RAGIONE.** I log lo provano:

**FATTO 1 — Il mercato NON è fermo.** Log: `TSUNAMI 30s:SCHIUMA(UP) 2min:SCHIUMA(UP) 10min:SCHIUMA(UP)` + `EVENT_PREBREAKOUT`. Il mercato si muove in SU. "Mercato piatto da 40 ore" era FALSO (era la dashboard che mostrava gf_drift/gf_stato morti — vedi sotto).

**FATTO 2 — Lo score NON è 0. È 23-28, sotto la soglia.** Log: `SCORE_SOTTO: 23.1 vs 34.0`, `28.6 vs 34.0`. Il bot calcola, vede il movimento, ma lo score si ferma a 28 e non supera la soglia.

**FATTO 3 (LA CAUSA) — Lo score è EROSO dai veti del vecchio mondo.** Log: `VERITAS dep: DEBOLE|BASSA|SIDEWAYS TOSSICO wr=18%`, `CAPSULE dep: block_score=200 reasons=[CTX_TOSSICO..., MAT_TOSSICO...]`, `[CM] BLOCCO LEARNED_CTX... WR=6%`. Questi organi (VERITAS, capsule, CM learned) **abbassano lo score** basandosi sullo storico di sconfitte in quel contesto. Lo score parte buono (seed era 20!) ma viene eroso a 28.

**FATTO 4 (insight di Roberto, decisivo) — Lo score non è più il giudice giusto.** Roberto: "prima passava a 48, ora a 34 (più basso) non passa → NON è la soglia. Chi governa lo score non è quello che deve far passare i candidati." ESATTO. Abbassare SCORE_FLOOR non risolve, perché il problema non è il livello della soglia: è che lo score è ancora governato da VERITAS/capsule/fattori (vecchio mondo) invece di essere un puro filtro anti-rumore.

**FATTO 5 — Il WebSocket cade di continuo.** Log: `[WS_ERROR] ping/pong timed out`, `reconn=34`. 34 riconnessioni. Causa: `ping_timeout=10` troppo stretto su Render → quando il thread è occupato il pong arriva tardi → Binance chiude. Dati a singhiozzo → score non si consolida. **FIX APPLICATO** nel file `9ef6b1e2`: ping_timeout allargato a 18s (ENV WS_PING_TIMEOUT). Da verificare che reconn smetta di crescere.

---

## ❌ GLI INTERRUTTORI DI OGGI COPRONO SOLO PARTE DEI VETI
Il problema per cui lo score resta eroso: gli interruttori messi oggi NON spengono i veti per intero.
- `SC_CAPSULE_VOTO_OFF=true` → spegne SOLO il "voto" capsule (riga 6326). Il codice stesso dice (r.6321-6323): "NON tocca Veritas, NON tocca FP_TOSSICO, NON tocca il block altrove. Solo il VOTO."
- `ATTRITO_OFF`, `VERITAS_OFF` → coprono alcuni return, ma VERITAS continua a comparire nei log come `dep` (deposto) e ad abbassare lo score.
- I veti sono "DEPOSTI" (loggano, l'SC decide) ma "deposto" ≠ "spento": continuano a pesare sullo score.

**CONCLUSIONE:** il vecchio modello (VERITAS/capsule che giudicano sullo storico) e il nuovo (cancello sul movimento) convivono nello stesso file, e il vecchio STROZZA il nuovo PRIMA che arrivi al cancello. È la "dicotomia" che Roberto aveva individuato la mattina del 20giu.

---

## ✅ LA STRADA GIUSTA (prossima sessione, intervento strutturale lucido)
NON tarare lo score. NON un altro interruttore a tentoni. La strada è:
1. **Scollegare lo score dal giudizio.** Lo score deve diventare un filtro anti-rumore FISSO e BASSO (15-20): "c'è movimento o no". Punto.
2. **Spegnere VERITAS + capsule learned + CM_LEARNED PER INTERO** (non "deposti"), mappando TUTTI i punti dove toccano lo score in un colpo solo, non uno per volta.
3. Il candidato arriva al cancello appena c'è movimento minimo, senza erosione a monte.
4. Il CANCELLO (FILTRO M/F, sul movimento) fa l'unico giudizio: maschio (sale, nuovi massimi) entra, femmina (piatta/si sgonfia) tagliata.
5. Poi RACCOGLIERE trade nuovi puliti e leggerli (phantom_forensic per i blocchi del cancello, trades per i maschi entrati).

**Il GRANDE PASSO (quando il modello regge):** estrarre campo+cancello in un file PULITO di poche centinaia di righe. Le 17.200 righe con VERITAS/capsule/SC/fattori sono ingovernabili — la prova è la giornata del 20giu: 10 file consegnati per cacciare freni sparsi in funzioni diverse.

---

## 🔖 BUILD / FILE (20giu sera)
- **Ultimo file:** `9ef6b1e2` = catena fix del 20giu (SCORE_FLOOR env r.1026, ATTRITO_OFF veti motore, BOOT_GUARD warmup RSI, SC_BLOCCA_EXPLOSIVE observer, SEME_GATE_OFF, SOGLIA_PIATTA in score_now+evaluate, gf_drift VIVO, gf_stato VIVO, WS ping_timeout 18s).
- Cronologia MD5: f5152eb7 (verde base) → 2d346957 → dc116177 → 2fcb616f → 0118b249 (gf_drift) → b4fbd5d1 (gf_stato) → **9ef6b1e2 (WS ping, ULTIMO)**.
- ⚠️ Verificare sempre `md5sum` sul container + `wc -l` (~17.200). DeepSeek una volta ha dato un file monco da 656 righe: NON caricare file sotto ~17.000 righe.
- Launcher app.py `6c12b89e` stabile. Procfile `web: python app.py`. DB /var/data/trading_data.db.
- **GATE PEAK (gate_peak_osserva) è SPENTO** (`GATE_PEAK_OFF=true`) — è il giudice VECCHIO, lasciarlo spento. Il giudice di Roberto è il FILTRO M/F / cancello (scrive in phantom_forensic con block_reason=MINA_CANCELLO_SALITA, NON in gate_peak_osserva).

---

## 🩹 FIX DISPLAY FATTI OGGI (erano "tachimetri rotti")
- **gf_drift** (r.16566): mostrava 0.000 morto (scritto solo dentro GF_DIREZIONE). Ora calcolato da _prices_long a ogni heartbeat → mostra drift vero.
- **gf_stato** (LIBERO/FUORI): mostrava "—" permanente (scritto solo dentro _evaluate_shadow_entry, cioè solo durante un aggancio). Ora calcolato dal drift vivo a ogni heartbeat.
- Questi NON erano blocchi di trading, solo display. Ma facevano credere a Roberto "mercato fermo/rotto" quando il bot girava.

---

## 🎯 ENV ATTUALI (20giu sera)
```
ATTRITO_OFF=true · CANCELLI_OBSERVER=true · VERITAS_OFF=true · MERCATO_MORTO_OFF=true
SC_FP_TOSSICO_OFF=true · SC_CAPSULE_VOTO_OFF=true · SEME_GATE_OFF=true · LOSS_STREAK_OFF=true
SOGLIA_PIATTA=true · SCORE_FLOOR=34 · CANCELLO_PICCO_MIN_USD=0.0 · CROMO_GATE_ON=false
CANCELLO_APERTURA_OFF=false · CANCELLO_SALITA_OFF=false · CANCELLO_MOSSE=3 · CANCELLO_RESPIRO_TICK=40
GF_DIREZIONE_OFF=false (LONG-only, NON spegnere)
WS_WATCHDOG_SEC=60 · CONTORNO_OFF=true · WS_PING_TIMEOUT=18 (nuovo, opzionale)
GATE_PEAK_OFF=true (gate vecchio, lasciare spento)
```
NB: questi interruttori NON bastano — VERITAS/capsule erodono ancora lo score (vedi diagnosi). Serve l'intervento strutturale.

## 📜 PROIBIZIONI
MAI parlare di tempo/stanchezza. MAI distinguere M/F all'ingresso (firma ambigua). MAI SHORT (LONG-only). MAI veti con finestre/conteggi. Fee VERA $2.00/trade. MAI pacchettare. MAI riscrivere da zero. MAI indovinare schema DB. MAI dire a Roberto che ha scoperto qualcosa che nessuno ha pensato (onestà > carezza). MAI fare prove a caso: leggere i LOG RENDER per il perché.

## 📜 CADAVERI (NON riattivare)
streak_4, Narratore, RSI/MACD, ZONA_MORTA, TRANELLO_FEE_ZERO, gate "3 morsi", CROMO.

## METODO CHE HA FUNZIONATO IL 20giu (tenerlo)
La verità è venuta dai LOG RENDER (stdout, NON il file /var/data/trading.log che è vuoto, NON lo spioncino /trading/status che non espone momentum). Quando Roberto dice "qualcosa blocca", LEGGERE I LOG, non indovinare da status. I log dicono esattamente: TSUNAMI, VERITAS dep, CAPSULE dep, SCORE_SOTTO X vs Y, WS_ERROR. Quella è la telecamera vera.
