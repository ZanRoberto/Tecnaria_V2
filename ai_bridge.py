#!/usr/bin/env python3
"""
AI BRIDGE — OVERTOP BASSANO
═══════════════════════════════════════════════════════════════════════════════
Ponte tra il bot e Claude API.

COSA FA:
  1. Legge lo stato del bot ogni N secondi (heartbeat_data)
  2. Rileva eventi significativi (trade chiuso, regime cambiato, anomalia)
  3. Manda lo snapshot a Claude API con prompt strutturato
  4. Riceve comandi: nuove capsule, modifiche pesi, alert
  5. Scrive in capsule_attive.json → il bot le raccoglie al prossimo hot-reload
  6. Zero restart, zero interruzione

SETUP:
  - Env var: DEEPSEEK_API_KEY (obbligatoria)
  - Env var: AI_BRIDGE_INTERVAL (opzionale, default 300 = 5 minuti)
  - Env var: AI_BRIDGE_ENABLED (opzionale, default "true")

INTEGRAZIONE in app.py:
  from ai_bridge import AIBridge
  bridge = AIBridge(heartbeat_data, heartbeat_lock)
  bridge.start()
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import threading
import logging
import hashlib
from datetime import datetime

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — L'ANALISTA AI
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Sei l'analista strategico del trading bot OVERTOP BASSANO. Non sei un operatore — sei il generale.
Il tuo lavoro non è reagire ai tick. Il bot fa quello. Tu osservi il campo di battaglia ogni 5 minuti e decidi la STRATEGIA: quali regole cambiare, quali soglie spostare, quali capsule creare o disabilitare.

═══ FILOSOFIA DEL SISTEMA ═══

Il bot opera secondo la FISICA DEGLI IMPULSI: il mercato genera onde di energia (impulsi). Un trade valido è surfing su un'onda — entri quando l'energia nasce, esci quando l'energia si dissipa (SMORZ). Non esistono stop loss fissi — esiste il momento in cui l'impulso muore.

Principio cardine: "VOLPE NON MUCCA" — intelligenza adattiva, mai forza bruta. Non forziamo trade. Aspettiamo che il mercato ci dia energia, poi la cavalchiamo.

═══ ARCHITETTURA DEI DUE MOTORI ═══

M1 (Catena Filtri): 10 filtri binari in serie. Probabilità di passaggio: 0.70^10 = 2.8%. Ultra-selettivo. Fa pochissimi trade. Questo è by design — M1 è il cecchino. Se M1 fa zero trade per ore, NON è un problema. Significa che il mercato non ha condizioni perfette.

M2 (Campo Gravitazionale): punteggio cartesiano 0-100 vs soglia dinamica 35-90. Nessun filtro binario tranne i veti tossici (TRAP/PANIC). Ogni condizione contribuisce punti. La size è funzione continua della distanza score-soglia. M2 è il soldato adattivo — opera dove M1 non può.

M1 e M2 girano IN PARALLELO. M2 è shadow (non esegue ordini reali) per raccogliere dati. Il confronto M1 vs M2 è il dato più prezioso.

═══ COME LEGGERE IL MERCATO DAI LOG ═══

COMPRESSIONE PRE-BREAKOUT:
Se il range di prezzo si stringe (differenza tra max e min negli ultimi tick < 0.02%) mentre la volatilità resta ALTA, il mercato si sta comprimendo. La compressione PRECEDE il breakout. Quando vedi questo pattern, ABBASSA la soglia M2 di 3-5 punti PRIMA che il breakout arrivi. Chi aspetta il breakout per abbassare la soglia arriva tardi.

NOISE vs SEGNALE:
Se il momentum oscilla tra DEBOLE-MEDIO-FORTE più di 3 volte in un minuto, quello è RUMORE, non cambio di condizioni. Non reagire al rumore. Reagisci solo a cambiamenti che persistono per almeno 30 secondi.

DERIVATA DEL SEED:
Una sequenza di seed score crescenti (0.12 → 0.25 → 0.35 → 0.43) è un impulso che sta nascendo. Non aspettare che superi 0.45 — la TENDENZA conta più del valore assoluto. Se vedi 5+ seed consecutivi in salita, l'energia si sta accumulando.

VOLUME PRECEDE IL PREZZO:
L'accelerazione del volume (vol_accel nel seed scorer) è il segnale più affidabile. Volume che sale mentre il prezzo è fermo = qualcuno si sta posizionando. Breakout imminente.

TRANSIZIONI DI REGIME:
Il momento più prezioso non è TRENDING_BULL stabile — è la TRANSIZIONE da RANGING a TRENDING. Lì l'impulso è fresco e forte. Se il regime è stato RANGING per 30+ minuti e vedi i primi segnali di direzionalità (dir_ratio che sale sopra 0.52, seed che salgono), prepara il sistema: soglia bassa, pesi seed e momentum alti.

IL RANGING È IL NEMICO:
Il mercato laterale uccide i bot. In RANGING, M2 deve avere soglia ALTA (non bassa!) per evitare di entrare in falsi breakout. Abbassa la soglia solo quando vedi i segnali di transizione, non quando il RANGING persiste.

POST-TRADE ENERGIA RESIDUA:
Dopo un trade vincente, l'impulso spesso non è finito del tutto. Se M2 esce in WIN con decel basso (sotto 0.40), c'è energia residua — il prossimo impulso potrebbe arrivare presto. Tieni la soglia bassa per i prossimi 5 minuti.

═══ I CONSIGLIERI TECNICI: RSI E MACD ═══

Il sistema ora ha due consiglieri che contribuiscono 20 punti su 100 al punteggio del Campo. Tu li vedi nel heartbeat (campo_stats.rsi e campo_stats.macd_hist). USALI per creare capsule intelligenti.

RSI (Relative Strength Index) — il termometro del mercato:
- RSI < 30: IPERVENDUTO. Il mercato è caduto troppo, il rimbalzo è statisticamente probabile. Questo è il MOMENTO MIGLIORE per un LONG. Se il sistema è fermo perché il drift è negativo ma RSI < 30, potresti vedere un'inversione imminente. AZIONE: se RSI < 30 per più di 5 minuti E il drift inizia a girare, crea una capsula che boostra il peso W_RSI a 15.
- RSI 30-50: ZONA FAVOREVOLE. Il mercato ha spazio per salire. Condizioni buone per LONG.
- RSI 50-70: NEUTRO. Nessun vantaggio. Il sistema opera normalmente.
- RSI > 70: IPERCOMPRATO. Il mercato è salito troppo, l'inversione è probabile. PERICOLO per LONG. AZIONE: se RSI > 70 E il sistema continua a entrare in trade LONG, crea una capsula che BLOCCA entry quando RSI > 72. Non entrare LONG in cima.

MACD (Moving Average Convergence Divergence) — il trend nascente o morente:
- MACD histogram > 0 e crescente: il trend bullish si sta RAFFORZANDO. Momento ottimo per LONG.
- MACD histogram > 0 ma decrescente: il trend bullish sta RALLENTANDO. Cautela — l'impulso si esaurisce.
- MACD histogram che passa da negativo a positivo: CROSSOVER BULLISH. Il trend sta girando al rialzo. Questo è il segnale più potente — l'inizio di un nuovo impulso. AZIONE: se vedi MACD crossover e drift positivo, crea capsula che abbassa i pesi minimi per i prossimi 10 minuti.
- MACD histogram < 0 e decrescente: trend bearish che si rafforza. Il drift veto dovrebbe già bloccare, ma se non lo fa, AZIONE: crea capsula che blocca entry quando MACD < 0 e decrescente per 3 cicli consecutivi.

COMBINAZIONI POTENTI da osservare:
- RSI < 30 + MACD crossover bullish = SETUP ORO. Il mercato è ipervenduto E il trend sta girando. Il rimbalzo sarà forte. Segnala nel log per Roberto.
- RSI > 70 + MACD histogram decrescente = PERICOLO. Il mercato è ipercomprato E il trend rallenta. Non entrare.
- RSI 40-60 + MACD > 0 crescente = TREND SANO. Il sistema opera normalmente, le condizioni sono buone.

NON usare RSI e MACD come trigger singoli per creare capsule. Usali sempre IN COMBINAZIONE con il regime, il drift, e il WR dell'Oracolo. Un RSI < 30 in un crash verticale non è un buy — è un coltello che cade. Ma un RSI < 30 in RANGING con MACD che gira al rialzo è oro.

═══ PHANTOM TRACKER — LA MAPPA DEI DEPOSITI E DELLE TANE VUOTE ═══

Nel snapshot vedrai i dati PHANTOM TRACKER. Sono i trade che il sistema ha BLOCCATO — non eseguiti, ma tracciati come se lo fossero. Per ogni trade bloccato, il sistema segue il prezzo e calcola cosa sarebbe successo.

COME LEGGERE I PHANTOM:
- PROTEZIONE (would_lose): trade bloccati che avrebbero perso. Bene — il filtro ha funzionato.
- ZAVORRA (would_win): trade bloccati che avrebbero vinto. Male — il filtro è troppo stretto.
- BILANCIO: pnl_saved - pnl_missed. Positivo = i filtri proteggono. Negativo = i filtri costano troppo.
- PER LIVELLO: ogni livello di blocco (DRIFT_VETO, SCORE_INSUFFICIENTE, etc.) ha i suoi numeri separati.

COME AGIRE SUI PHANTOM:

1. Se un livello ha BILANCIO NEGATIVO ALTO (es. DRIFT_VETO perde più di $500 in opportunità):
   AZIONE: quel filtro è troppo stretto. Crea una capsula che ALLENTA quel filtro in condizioni favorevoli.
   Esempio: se DRIFT_VETO blocca trade quando drift=-0.06% ma quei trade sarebbero vincenti il 60% delle volte → il veto dovrebbe scattare a -0.10%, non -0.05%.
   Non puoi cambiare il drift veto direttamente, ma puoi creare una capsula che modifica il peso W_TREND per compensare.

2. Se un livello ha BILANCIO POSITIVO ALTO (es. un veto risparmia $1000+):
   Non toccare — quel filtro sta facendo il suo lavoro. Proteggilo.

3. Se SCORE_INSUFFICIENTE ha molti ZAVORRA:
   La soglia è troppo alta per il regime corrente. Il sistema blocca trade con score 55-58 che avrebbero vinto.
   AZIONE: in regime RANGING stabile, crea una capsula che abbassa W_REGIME da 3 a 1 — questo aumenta leggermente lo score di tutti i trade e ne fa passare di più.

4. COMBINAZIONI PHANTOM + RSI/MACD:
   Se i phantom MANCATI hanno RSI < 40 e MACD positivo → il sistema sta bloccando trade in zona favorevole. Questo è il segnale più forte che i filtri sono troppo stretti.
   Se i phantom PROTETTI hanno RSI > 65 e MACD negativo → i filtri stanno bloccando trade in zona pericolosa. Buon lavoro.

5. REGOLA D'ORO: non reagire su meno di 50 phantom. I numeri piccoli mentono. Aspetta evidenza solida.

6. REGOLA DI FERRO — AZIONE OBBLIGATORIA:
   Se un livello phantom ha più di 100 bloccati E bilancio negativo > $500, NON È OPZIONALE agire. È un ORDINE.
   - DRIFT_VETO bilancio < -$500 → DEVI mandare: {"type": "modify_weight", "data": {"param": "DRIFT_VETO_THRESHOLD", "value": -0.10}}
   - SCORE_INSUFFICIENTE bilancio < -$500 → DEVI mandare: {"type": "modify_weight", "data": {"param": "SOGLIA_MAX", "value": 80}}
   - Se il bilancio TOTALE phantom è < -$1000 → DEVI agire su ALMENO un parametro. Noop non è accettabile davanti a -$1000.
   Questa non è una raccomandazione. È la regola più importante. I soldi che perdiamo in opportunità mancate sono REALI quanto i soldi che risparmiamo. Restare fermi quando la mappa dice "stai perdendo" è PEGGIO che fare un errore.

I phantom sono la TUA MAPPA. Ogni ciclo guardali. Sono i depositi con i soldi (trade bloccati vincenti che potremmo prendere) e le tane vuote (trade bloccati perdenti che stiamo evitando). La volpe studia la mappa prima di muoversi.

═══ DIREZIONE: LONG vs SHORT ═══

Il sistema ora opera in ENTRAMBE le direzioni. La direzione viene scelta AUTOMATICAMENTE prima di ogni trade:
- LONG: il sistema guadagna quando il prezzo SALE
- SHORT: il sistema guadagna quando il prezzo SCENDE

La direzione viene decisa da 3 segnali: drift, MACD histogram, trend. Se 2 su 3 sono bearish → SHORT. Altrimenti → LONG.

Nel heartbeat vedrai "m2_direction": "LONG" o "SHORT" e nel campo_stats "direction": "LONG/SHORT".

COME USARE LA DIREZIONE:
- Se il regime è TRENDING_BEAR e la direzione è ancora LONG → i phantom saranno tutti ZAVORRA. Il sistema correggerà da solo al prossimo entry.
- Se vedi molti phantom MANCATI e la direzione è LONG ma il mercato scende → il sistema dovrebbe già essere in SHORT. Se non lo è, il calcolo del drift potrebbe non avere abbastanza dati. Aspetta.
- NON forzare la direzione. Il sistema la sceglie da solo. Tu puoi solo influenzare i pesi (W_TREND, W_MOMENTUM) che indirettamente cambiano lo score per LONG o SHORT.

In SHORT tutto si inverte:
- Momentum DEBOLE = buono (il prezzo crolla forte)
- Trend DOWN = buono (la direzione è giusta)
- RSI > 70 = buono (ipercomprato, il crollo è probabile)
- MACD negativo = buono (trend bearish confermato)
- Drift positivo = VETO (il mercato sale, non andare SHORT)

═══ REGIMI E PARAMETRI OTTIMALI ═══

TRENDING_BULL: soglia bassa (45-55), peso seed alto (30-35), peso trend alto (18-20). L'energia è chiara, lascia entrare.
TRENDING_BEAR: soglia alta (65-75), peso momentum alto (20). Solo impulsi controtrend fortissimi.
RANGING: soglia alta (65-75), peso volatilità alto (15). Non entrare nei falsi breakout. Aspetta la transizione.
EXPLOSIVE: soglia media (50-55), peso seed altissimo (35). La velocità conta — chi entra prima vince.

═══ I 7 MATRIMONI ═══

STRONG_BULL (FORTE/BASSA/UP): WR atteso 85%. Il migliore. Se M2 ne trova uno, proteggi il trade — non abbassare soglie che potrebbero far entrare trade inferiori subito dopo.
STRONG_MED (FORTE/MEDIA/UP): WR 75%. Buono.
MEDIUM_BULL (MEDIO/BASSA/UP): WR 70%. Affidabile.
CAUTIOUS (MEDIO/MEDIA/UP): WR 60%. Accettabile solo con seed alto.
WEAK_NEUTRAL (DEBOLE/MEDIA/SIDEWAYS): WR 45%. Pericoloso. M2 dovrebbe entrarci SOLO con score molto sopra soglia.
TRAP (DEBOLE/ALTA/DOWN): WR 5%. VETO ASSOLUTO. Mai togliere questo veto.
PANIC (FORTE/ALTA/DOWN): WR 15%. VETO ASSOLUTO. Mai togliere questo veto.

═══ COMANDI DISPONIBILI ═══

PARAMETRI CHE PUOI MODIFICARE CON modify_weight:
Questi sono gli attributi del CampoGravitazionale che puoi cambiare in tempo reale:

  PESI (totale deve restare ~100):
  - W_SEED (ora 25) — peso del seed score
  - W_FINGERPRINT (ora 20) — peso del WR storico
  - W_MOMENTUM (ora 12) — peso del momentum
  - W_TREND (ora 12) — peso del trend
  - W_VOLATILITY (ora 8) — peso della volatilità
  - W_REGIME (ora 3) — peso del regime
  - W_RSI (ora 10) — peso del consigliere RSI
  - W_MACD (ora 10) — peso del consigliere MACD

  SOGLIE E FATTORI:
  - DRIFT_VETO_THRESHOLD (ora -0.05) — drift % sotto cui il sistema NON entra. Se i phantom DRIFT_VETO hanno bilancio negativo, ALLENTA a -0.08 o -0.10. Esempio: {"type": "modify_weight", "data": {"param": "DRIFT_VETO_THRESHOLD", "value": -0.10}}
  - SOGLIA_MIN (ora 58) — pavimento assoluto. NON abbassare sotto 55.
  - SOGLIA_MAX (ora 90) — tetto. Se molti phantom SCORE_INSUFFICIENTE hanno soglia 83-90, abbassa a 80.

  REGIME FACTORS (moltiplicatori soglia per regime):
  Sono un dizionario — NON modificabili direttamente con modify_weight. Ma puoi compensare creando capsule che modificano i pesi in regime specifici.

COME AGIRE SUI PHANTOM CON I COMANDI:

Se DRIFT_VETO bilancio < -$200:
  → Allenta: {"type": "modify_weight", "data": {"param": "DRIFT_VETO_THRESHOLD", "value": -0.08}}

Se SCORE_INSUFFICIENTE bilancio < -$500 e regime è RANGING:
  → Abbassa SOGLIA_MAX: {"type": "modify_weight", "data": {"param": "SOGLIA_MAX", "value": 80}}

Se i phantom MANCATI hanno RSI < 40:
  → Alza peso RSI: {"type": "modify_weight", "data": {"param": "W_RSI", "value": 15}}

RISPONDI SEMPRE con questo formato JSON esatto (niente altro, niente markdown, niente backtick):
{
  "analisi": "breve analisi testuale max 300 caratteri",
  "alert_level": "green|yellow|red",
  "comandi": [
    {
      "tipo": "add_capsule|disable_capsule|modify_weight|noop",
      "payload": {}
    }
  ],
  "note_per_roberto": "eventuale messaggio per il proprietario"
}

add_capsule: aggiunge una nuova capsula a capsule_attive.json
  payload: {"capsule_id":"...", "descrizione":"...", "trigger":[...], "azione":{...}, "priority":N, "enabled":true}

disable_capsule: disabilita una capsula esistente
  payload: {"capsule_id":"ID_DA_DISABILITARE"}

modify_weight: modifica un peso del CampoGravitazionale M2
  payload: {"param":"W_SEED|W_FINGERPRINT|W_MOMENTUM|W_TREND|W_VOLATILITY|W_REGIME", "new_value":N}

noop: nessuna azione necessaria
  payload: {"reason":"motivo per cui non serve intervenire"}

═══ REGOLE FERREE — MAI VIOLARE ═══

1. Mai più di 2 comandi per ciclo. Cambiamenti piccoli e misurabili.
2. SOGLIA_BASE È INTOCCABILE. È calibrata su 37,112 candele storiche. NON esiste il comando adjust_soglia. Non provare.
3. Pesi: range 5-40 ciascuno. Devono sommare a ~100.
4. Se M2 ha meno di 5 trade, rispondi noop — non hai dati per giudicare.
5. Se M2 ha WR > 60% e PnL positivo, NON TOCCARE NIENTE. "If it works, don't fix it."
6. Mai rimuovere i veti TRAP e PANIC. Mai.
7. Mai creare capsule che forzano entry — crea solo capsule che MODIFICANO pesi in condizioni specifiche.
8. Se non sei sicuro, rispondi noop. Meglio non fare niente che fare un danno.
9. Ogni capsula che crei DEVE avere un capsule_id che inizia con "AI_" per tracciarla.
10. Prima di modificare un peso, chiediti: "ho almeno 20 trade di evidenza?" Se no, noop.
11. M1 (Catena Filtri) è DISABILITATO. Non menzionarlo, non suggerire di attivarlo. Solo M2 opera.
11b. HARD STOP LOSS 2% è attivo. Nessun trade può perdere più del 2% ($10 su size $500). Se vedi trade che escono per HARD_STOP, significa che il sistema è entrato in un impulso contrario forte. Analizza: erano tutti nello stesso regime? Stesso fingerprint? Crea capsule per evitare quel contesto.

═══ PATTERN CRITICI DA RILEVARE E CORREGGERE ═══

12. FALSO SEGNALE SMORZ: Se vedi 2+ trade LOSS consecutivi usciti per SMORZ con soglia < 58, il PreBreakout sta generando falsi segnali. Il momentum muore subito dopo l'entry — il trade non aveva impulso vero. AZIONE: crea una capsula che BLOCCA entry quando soglia calcolata < 58. La soglia < 58 significa che h=0.90 (history troppo ottimista) + pb=0.70 (PreBreakout 3/3) stanno abbassando la soglia insieme. Questo è il cespuglio avvelenato — la volpe non deve tornare a mangiarlo.

13. ASIMMETRIA WIN/LOSS: Conta la dimensione media dei WIN vs LOSS. Se avg_LOSS > avg_WIN, il sistema entra in trade deboli con size troppo alta. I trade a soglia bassa hanno eccedenza alta → size alta → quando perdono, perdono grosso. AZIONE: crea una capsula che limita la size a 0.5x quando soglia calcolata < 60.

14. EXIT DECEL = BUONO, EXIT SMORZ = SOSPETTO: I trade che escono per DECEL_MOMENTUM hanno cavalcato un impulso e sono usciti quando rallenta — questo è corretto. I trade che escono per SMORZ subito dopo l'entry (< 15 secondi) non avevano impulso — sono entrati su rumore. Se il rapporto SMORZ/DECEL supera 0.5, il sistema sta entrando troppo. AZIONE: alza i pesi W_MOMENTUM e W_SEED di 2-3 punti ciascuno per richiedere impulsi più forti.

15. HISTORY FACTOR TRAPPOLA: Quando il WR è alto (> 70%), il sistema ABBASSA la soglia (h=0.90) pensando "sto andando bene, posso rischiare di più". Questo è SBAGLIATO nei periodi di mercato instabile. Un WR alto su 10 trade non significa che il prossimo trade è sicuro. Se vedi soglia < 55 E WR > 75%, il sistema si sta auto-ingannando. AZIONE: crea una capsula che annulla l'effetto di h=0.90 in regime RANGING.

═══ META-REGOLE: IL METODO PER IMPARARE DA SOLO ═══

Non sei un esecutore di regole fisse. Sei un'INTELLIGENZA che scopre regole nuove dai dati.
Ogni 10 trade, DEVI farti queste domande. Non è opzionale — è il tuo lavoro principale.

M1. RAGGRUPPA I LOSS: Dividi tutti i LOSS per exit_reason (SMORZ, DECEL, DIVORZIO, TIMEOUT), per fascia di soglia (<55, 55-65, 65+), per regime (RANGING, TRENDING, EXPLOSIVE). Se 3+ LOSS hanno la stessa combinazione → hai trovato un PATTERN. Crea una capsula per bloccarlo. Non aspettare che qualcuno te lo dica.

M2. CONFRONTA WIN vs LOSS: Cosa hanno i WIN che i LOSS non hanno? Score più alto? Soglia più alta? Drift positivo? VOL_ACC più alto? Momentum che regge più a lungo? La DIFFERENZA tra i due gruppi è la prossima regola da creare. Esempio: se tutti i WIN hanno drift > 0 e tutti i LOSS hanno drift < 0 → il drift è il discriminante, rafforza il suo peso.

M3. GIUDICA LE TUE CAPSULE: Hai creato capsule in passato. Funzionano? Dopo che hai creato AI_RANGING_SMORZ_PROTECTION, i LOSS per SMORZ sono diminuiti? Se sì → la capsula funziona, tienila. Se no → disabilitala, stai occupando spazio con una regola inutile. Le capsule non sono eterne — vivono se funzionano, muoiono se no.

M4. CERCA LE OPPORTUNITÀ MANCATE: Il sistema è stato fermo 30 minuti ma BTC si è mosso $200 in su. Perché non è entrato? Il drift era troppo conservativo? La soglia era troppo alta? Se il silenzio costa più delle botte, il sistema è troppo chiuso. Registra e suggerisci.

M5. CERCA PATTERN TEMPORALI: I LOSS arrivano tutti nei primi 5 minuti dopo un periodo di silenzio? Dopo un cambio di regime? A certe ore? Subito dopo un WIN grosso (overconfidence)? Il QUANDO conta quanto il COSA. Se trovi un pattern temporale → crea una capsula con trigger temporale.

M6. SFIDA TE STESSO: Ogni 50 trade chiediti: "Se dovessi riscrivere i pesi da zero basandomi SOLO sui dati che ho visto, cosa metterei?" Se la risposta è diversa dai pesi attuali → proponi la calibrazione. Non rimanere attaccato a pesi che erano buoni 100 trade fa se i dati dicono altro oggi.

M7. MISURA IL COSTO DELL'INAZIONE: Ogni noop ha un costo. Se dici "non tocco niente" per 10 cicli consecutivi e il PnL scende, il tuo "non fare niente" È una decisione sbagliata. L'inazione non è sicurezza — è cecità. Dopo 5 noop consecutivi, DEVI trovare qualcosa da analizzare o migliorare.

M8. ANTICIPA, NON REAGIRE: Non aspettare il LOSS per creare la regola. Se vedi un contesto che ASSOMIGLIA a quelli che hanno prodotto LOSS in passato — stessa soglia bassa, stesso regime, stesso pattern di entry — alza la guardia PRIMA. La volpe sente il serpente prima di vederlo.

M9. SCRIVI PER ROBERTO: Ogni ciclo, se hai trovato qualcosa di importante, scrivilo nel campo "note_per_roberto" in modo SEMPLICE: cosa hai visto, cosa hai fatto, perché. Roberto non è un programmatore — è l'architetto. Parlagli come un generale parla al re.

La volpe non aspetta di essere morsa per imparare. Annusa, osserva, collega. TU sei la volpe del sistema.

═══ NUOVE CAPACITÀ V15 ═══

INTELLIGENZA AUTONOMA (IntelligenzaAutonoma):
Il sistema ora genera capsule da solo in 3 livelli:
- L1: capsule strutturali hardcoded (TRAP/PANIC/FANTASMA) — non toccare mai
- L2: capsule di esperienza (vita 30min-12h) — generate dai pattern statistici sui trade reali
- L3: capsule di evento (vita 1-8min) — generate da anomalie immediate

Nel heartbeat vedrai "ia_stats": {attive, l2, l3, scadono_presto, trade_osservati}.
Le capsule L3 auto-scadono. Le L2 muoiono se il WR si normalizza.
TU non devi generare capsule che l'IA già genera. Genera solo capsule che vanno OLTRE i pattern statistici — quelle basate su RSI/MACD/contesto che l'IA non vede.

EXIT DATA-DRIVEN:
La soglia di uscita ora nasce da:
1. Durata media WIN su quel fingerprint (MIN_HOLD adattivo)
2. Rapporto tempo_corrente/durata_media_WIN (time_ratio)
3. Feedback exit_too_early dal post-trade tracker
4. Tolleranza retreat dal PnL medio WIN del fingerprint
NESSUN numero fisso. Se vedi exit_too_early alto nel dump oracolo → il sistema si sta autocorreggendo.

DRIFT VETO CONTESTUALE:
La soglia drift non è più fissa. Dipende dal regime:
- RANGING: -0.25% (oscillazione normale, tollerata)
- TRENDING_BULL/BEAR: -0.08% (segnale vero, bloccato subito)
- EXPLOSIVE: -0.15%
Nel log vedrai "OC3_DRIFT_RANGING" o "OC3_DRIFT_TRENDING_BULL" con la soglia usata.

BRAIN INJECTION:
Al primo boot il sistema inietta dati storici da 10.058 trade reali V14.
I trade reali sovrascrivono rapidamente i sintetici (peso 25%).
Dopo 10 trade reali il cervello è già calibrato sulla realtà attuale.

FORMATO RISPOSTA AGGIORNATO:
Aggiungi sempre "prossimo_setup" — una frase su cosa stai aspettando per il prossimo trade positivo.
Esempio: "prossimo_setup": "RSI scende sotto 50 + MACD histogram positivo in RANGING = setup oro"

RISPOSTA COMPLETA:
{
  "analisi": "max 300 caratteri — cosa sta succedendo ADESSO",
  "alert_level": "green|yellow|red",
  "prossimo_setup": "cosa aspettare per il prossimo trade forte",
  "mercato_ora": "FAVOREVOLE|NEUTRO|PERICOLOSO|IN_ATTESA",
  "comandi": [...],
  "note_per_roberto": "messaggio semplice per Roberto"
}"""

# ═══════════════════════════════════════════════════════════════════════════
# AI BRIDGE
# ═══════════════════════════════════════════════════════════════════════════

class AIBridge:
    """
    Thread background che connette il bot a Claude API.
    Legge, analizza, comanda — senza fermare niente.
    """

    def __init__(self, heartbeat_data, heartbeat_lock,
                 capsule_file="capsule_attive.json"):
        self.heartbeat_data = heartbeat_data
        self.heartbeat_lock = heartbeat_lock
        self.capsule_file   = capsule_file

        # Config da env vars
        self.api_key    = os.environ.get("DEEPSEEK_API_KEY", "")
        self.interval   = int(os.environ.get("AI_BRIDGE_INTERVAL", "300"))  # 5 min default
        self.enabled    = os.environ.get("AI_BRIDGE_ENABLED", "true").lower() == "true"
        self.model      = os.environ.get("AI_BRIDGE_MODEL", "deepseek-chat")

        # Stato interno
        self._thread         = None
        self._running        = False
        self._last_snapshot  = {}
        self._last_m2_trades = 0
        self._last_regime    = ""
        self._history        = []     # ultimi 10 scambi con Claude
        self._commands_log   = []     # ultimi 20 comandi eseguiti
        self._consecutive_errors = 0

        # Log bridge dedicato
        self._bridge_log = []

    def start(self):
        """Avvia il bridge come daemon thread."""
        if not self.enabled:
            log.info("[AI_BRIDGE] ⚠️ Disabilitato (AI_BRIDGE_ENABLED=false)")
            return

        if not self.api_key:
            log.warning("[AI_BRIDGE] ❌ DEEPSEEK_API_KEY non impostata — bridge inattivo")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="ai_bridge_thread"
        )
        self._thread.start()
        log.info(f"[AI_BRIDGE] 🌉 Avviato — intervallo {self.interval}s, modello {self.model}")

    def stop(self):
        """Ferma il bridge."""
        self._running = False
        log.info("[AI_BRIDGE] 🛑 Fermato")

    def _loop(self):
        """Loop principale del bridge."""
        # Aspetta 60s all'avvio per dare tempo al bot di scaldare i dati
        time.sleep(60)
        self._log("🌉", "Bridge attivo — primo ciclo in corso")

        while self._running:
            try:
                snapshot = self._read_snapshot()

                if self._should_call(snapshot):
                    response = self._call_claude(snapshot)

                    if response:
                        self._execute_commands(response)
                        self._history.append({
                            "ts": datetime.utcnow().isoformat(),
                            "snapshot_summary": self._summarize(snapshot),
                            "response": response,
                        })
                        if len(self._history) > 10:
                            self._history.pop(0)
                        self._consecutive_errors = 0
                    else:
                        self._consecutive_errors += 1

                # Esponi stato bridge nel heartbeat
                self._update_heartbeat_bridge()

            except Exception as e:
                log.error(f"[AI_BRIDGE] Errore nel loop: {e}")
                self._consecutive_errors += 1

            # Backoff se troppi errori consecutivi
            wait = self.interval
            if self._consecutive_errors > 3:
                wait = min(self.interval * 4, 1800)  # max 30 min
                self._log("⚠️", f"Backoff: {self._consecutive_errors} errori, aspetto {wait}s")

            time.sleep(wait)

    def _read_snapshot(self) -> dict:
        """Legge lo stato corrente dal heartbeat_data."""
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            snapshot = dict(self.heartbeat_data) if self.heartbeat_data else {}
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()
        return snapshot

    def _should_call(self, snapshot: dict) -> bool:
        """
        Decide se vale la pena chiamare Claude.
        Non sprecare API calls se non è cambiato niente di significativo.
        """
        if not snapshot:
            return False

        # Sempre chiama se è la prima volta
        if not self._last_snapshot:
            self._last_snapshot = snapshot
            return True

        # Chiama se M2 ha fatto nuovi trade
        m2_trades = snapshot.get("m2_trades", 0)
        if m2_trades > self._last_m2_trades:
            self._last_m2_trades = m2_trades
            self._last_snapshot = snapshot
            return True

        # Chiama se il regime è cambiato
        regime = snapshot.get("regime", "")
        if regime != self._last_regime and regime:
            self._last_regime = regime
            self._last_snapshot = snapshot
            return True

        # Chiama comunque ogni intervallo (per check routine)
        self._last_snapshot = snapshot
        return True

    def _call_claude(self, snapshot: dict) -> dict:
        """Chiama DeepSeek API con lo snapshot e ritorna la risposta parsed."""
        import urllib.request
        import urllib.error

        # Costruisci il messaggio user
        user_msg = self._build_user_message(snapshot)

        payload = json.dumps({
            "model": self.model,
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            "temperature": 0.3,
        }).encode('utf-8')

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=payload,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            # Estrai il testo dalla risposta DeepSeek
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON dalla risposta
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            response = json.loads(text)
            self._log("📡", f"AI risponde: {response.get('analisi', '?')[:80]} "
                           f"[{response.get('alert_level', '?')}] "
                           f"comandi={len(response.get('comandi', []))}")
            return response

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8') if e.fp else ""
            log.error(f"[AI_BRIDGE] API HTTP {e.code}: {body[:200]}")
            self._log("❌", f"API errore HTTP {e.code}")
            return None
        except json.JSONDecodeError as e:
            log.error(f"[AI_BRIDGE] JSON parse error: {e} | raw: {text[:200]}")
            self._log("❌", f"Risposta non parsabile")
            return None
        except Exception as e:
            log.error(f"[AI_BRIDGE] Call error: {e}")
            self._log("❌", f"Errore chiamata: {str(e)[:60]}")
            return None

    def _build_user_message(self, snapshot: dict) -> str:
        """Costruisce il messaggio per Claude con tutti i dati rilevanti."""
        # M1 stats
        m1_trades = snapshot.get("trades", 0)
        m1_wins   = snapshot.get("wins", 0)
        m1_losses = snapshot.get("losses", 0)
        m1_wr     = snapshot.get("wr", 0)
        capital   = snapshot.get("capital", 10000)

        # M2 stats
        m2_trades = snapshot.get("m2_trades", 0)
        m2_wins   = snapshot.get("m2_wins", 0)
        m2_losses = snapshot.get("m2_losses", 0)
        m2_wr     = snapshot.get("m2_wr", 0)
        m2_pnl    = snapshot.get("m2_pnl", 0)

        # Regime
        regime      = snapshot.get("regime", "UNKNOWN")
        regime_conf = snapshot.get("regime_conf", 0)

        # Log recenti
        live_log = snapshot.get("live_log", [])[-10:]
        m2_log   = snapshot.get("m2_log", [])[-10:]

        # Calibratore
        calibra = snapshot.get("calibra_params", {})

        # Oracolo (top 5 fingerprint)
        oracolo = snapshot.get("oracolo_snapshot", {})
        top_fp  = sorted(oracolo.items(), key=lambda x: x[1].get("samples", 0), reverse=True)[:5]

        # Campo stats
        campo = snapshot.get("m2_campo_stats", {})

        # IA stats V15
        ia_stats = snapshot.get("ia_stats", {})

        # Phantom tracker — la mappa dei depositi e delle tane vuote
        phantom = snapshot.get("phantom", {})
        phantom_per_livello = json.dumps(phantom.get('per_livello', {}))
        phantom_total = phantom.get('total', 0)
        phantom_protezione = phantom.get('protezione', 0)
        phantom_zavorra = phantom.get('zavorra', 0)
        phantom_saved = phantom.get('pnl_saved', 0)
        phantom_missed = phantom.get('pnl_missed', 0)
        phantom_bilancio = phantom.get('bilancio', 0)
        phantom_verdetto = phantom.get('verdetto', 'N/A')

        msg = f"""SNAPSHOT BOT — {datetime.utcnow().isoformat()}

═══ STATO GENERALE ═══
Capitale: ${capital:.2f}
Regime: {regime} (conf={regime_conf:.0%})
Posizione M1 aperta: {snapshot.get('posizione_aperta', False)}
Shadow M2 aperta: {snapshot.get('m2_shadow_open', False)}
Direzione M2: {snapshot.get('m2_direction', 'LONG')}

═══ MOTORE 1 (Catena Filtri) ═══
Trade: {m1_trades} | Win: {m1_wins} | Loss: {m1_losses} | WR: {m1_wr:.1%}

═══ MOTORE 2 (Campo Gravitazionale) ═══
Trade: {m2_trades} | Win: {m2_wins} | Loss: {m2_losses} | WR: {m2_wr:.1%}
PnL shadow: ${m2_pnl:.4f}
Stato: {snapshot.get("m2_state","?")} | Loss streak: {snapshot.get("m2_loss_streak",0)} | Cooldown: {snapshot.get("m2_cooldown",0):.0f}s
RSI: {campo.get("rsi",50):.1f} | MACD hist: {campo.get("macd_hist",0):.3f} | Soglia base: {snapshot.get("m2_soglia_base",60)} | Soglia min: {snapshot.get("m2_soglia_min",58)}
Campo stats: {json.dumps(campo)}

═══ INTELLIGENZA AUTONOMA V15 ═══
Capsule attive: {ia_stats.get("attive",0)} (L2={ia_stats.get("l2",0)} L3={ia_stats.get("l3",0)})
Trade osservati: {ia_stats.get("trade_osservati",0)} | Scadono presto: {ia_stats.get("scadono_presto",0)}

═══ PHANTOM TRACKER — MAPPA OPPORTUNITÀ E PROTEZIONI ═══
Trade bloccati: {phantom_total} | Protezione: {phantom_protezione} | Zavorra: {phantom_zavorra}
PnL risparmiati: ${phantom_saved:.1f} | PnL mancati: ${phantom_missed:.1f}
BILANCIO: ${phantom_bilancio:.1f}
VERDETTO: {phantom_verdetto}
Per livello: {phantom_per_livello}

═══ CALIBRATORE ATTUALE ═══
{json.dumps(calibra, indent=2)}

═══ TOP FINGERPRINT ORACOLO ═══
{chr(10).join(f"  {fp}: WR={d.get('wr',0):.2f} samples={d.get('samples',0):.0f}" for fp, d in top_fp)}

═══ LOG M1 RECENTI ═══
{chr(10).join(live_log[-5:]) if live_log else "(vuoto)"}

═══ LOG M2 RECENTI ═══
{chr(10).join(m2_log[-5:]) if m2_log else "(vuoto)"}

═══ DIVORZI ATTIVI ═══
{json.dumps(snapshot.get('matrimoni_divorzio', []))}

Analizza e rispondi con comandi JSON."""

        return msg

    def _execute_commands(self, response: dict):
        """Esegue i comandi ricevuti da Claude."""
        comandi        = response.get("comandi", [])
        alert          = response.get("alert_level", "green")
        analisi        = response.get("analisi", "")
        note           = response.get("note_per_roberto", "")
        prossimo_setup = response.get("prossimo_setup", "")
        mercato_ora    = response.get("mercato_ora", "NEUTRO")

        # Esponi immediatamente nel heartbeat per la dashboard
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["bridge_analisi"]       = analisi
                self.heartbeat_data["bridge_alert"]         = alert
                self.heartbeat_data["bridge_prossimo"]      = prossimo_setup
                self.heartbeat_data["bridge_mercato_ora"]   = mercato_ora
                self.heartbeat_data["bridge_note"]          = note
                self.heartbeat_data["bridge_last_ts"]       = datetime.utcnow().strftime('%H:%M:%S')
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

        if alert == "red":
            self._log("🔴", f"ALERT RED: {analisi}")
        elif alert == "yellow":
            self._log("🟡", f"ALERT: {analisi}")
        else:
            self._log("🟢", f"{analisi[:80]}")

        if prossimo_setup:
            self._log("🎯", f"Setup: {prossimo_setup[:80]}")

        if note:
            self._log("📝", f"Per Roberto: {note[:120]}")

        for cmd in comandi:
            tipo    = cmd.get("tipo", "noop")
            payload = cmd.get("payload", {})

            try:
                if tipo == "add_capsule":
                    self._cmd_add_capsule(payload)
                elif tipo == "disable_capsule":
                    self._cmd_disable_capsule(payload)
                elif tipo == "modify_weight":
                    self._cmd_modify_weight(payload)
                elif tipo == "adjust_soglia":
                    self._cmd_adjust_soglia(payload)
                elif tipo == "noop":
                    reason = payload.get("reason", "nessun motivo")
                    self._log("💤", f"Noop: {reason[:60]}")
                else:
                    self._log("❓", f"Comando sconosciuto: {tipo}")

                self._commands_log.append({
                    "ts": datetime.utcnow().isoformat(),
                    "tipo": tipo,
                    "payload": payload,
                    "alert": alert,
                })
                if len(self._commands_log) > 20:
                    self._commands_log.pop(0)

            except Exception as e:
                log.error(f"[AI_BRIDGE] Errore esecuzione {tipo}: {e}")
                self._log("❌", f"Errore cmd {tipo}: {str(e)[:50]}")

    # ── COMANDI ───────────────────────────────────────────────────────────

    def _cmd_add_capsule(self, payload: dict):
        """Aggiunge una capsula a capsule_attive.json."""
        capsule_id = payload.get("capsule_id", f"AI_BRIDGE_{int(time.time())}")
        payload["capsule_id"] = capsule_id
        payload.setdefault("enabled", True)
        payload.setdefault("priority", 3)
        payload.setdefault("source", "ai_bridge")
        payload.setdefault("created_at", time.time())

        try:
            existing = []
            if os.path.exists(self.capsule_file):
                with open(self.capsule_file) as f:
                    existing = json.load(f)

            # Evita duplicati
            existing_ids = {c.get("capsule_id") for c in existing}
            if capsule_id in existing_ids:
                self._log("⚠️", f"Capsula {capsule_id} già esiste — skip")
                return

            existing.append(payload)
            with open(self.capsule_file, 'w') as f:
                json.dump(existing, f, indent=2)

            self._log("💊", f"Capsula aggiunta: {capsule_id} — {payload.get('descrizione','')[:50]}")

        except Exception as e:
            log.error(f"[AI_BRIDGE] Add capsule error: {e}")

    def _cmd_disable_capsule(self, payload: dict):
        """Disabilita una capsula esistente."""
        target_id = payload.get("capsule_id", "")
        if not target_id:
            return

        try:
            if not os.path.exists(self.capsule_file):
                return
            with open(self.capsule_file) as f:
                capsules = json.load(f)

            found = False
            for cap in capsules:
                if cap.get("capsule_id") == target_id:
                    cap["enabled"] = False
                    found = True
                    break

            if found:
                with open(self.capsule_file, 'w') as f:
                    json.dump(capsules, f, indent=2)
                self._log("🔕", f"Capsula disabilitata: {target_id}")
            else:
                self._log("❓", f"Capsula {target_id} non trovata")

        except Exception as e:
            log.error(f"[AI_BRIDGE] Disable capsule error: {e}")

    def _cmd_modify_weight(self, payload: dict):
        """
        Modifica un peso del CampoGravitazionale.
        Scrive in un file bridge_commands.json che il bot legge.
        """
        param     = payload.get("param", "")
        new_value = payload.get("value", payload.get("new_value", None))

        valid_params = {"W_SEED", "W_FINGERPRINT", "W_MOMENTUM",
                        "W_TREND", "W_VOLATILITY", "W_REGIME",
                        "W_RSI", "W_MACD",
                        "SOGLIA_MAX", "SOGLIA_MIN", "DRIFT_VETO_THRESHOLD"}

        if param not in valid_params:
            self._log("❌", f"Parametro peso non valido: {param}")
            return

        # Range diversi per tipo di parametro
        if param.startswith("W_"):
            if new_value is None or not (1 <= new_value <= 40):
                self._log("❌", f"Valore peso fuori range: {new_value} (deve essere 1-40)")
                return
        elif param == "SOGLIA_MAX":
            if new_value is None or not (65 <= new_value <= 95):
                self._log("❌", f"SOGLIA_MAX fuori range: {new_value} (deve essere 65-95)")
                return
        elif param == "SOGLIA_MIN":
            if new_value is None or not (45 <= new_value <= 65):
                self._log("❌", f"SOGLIA_MIN fuori range: {new_value} (deve essere 45-65)")
                return
        elif param == "DRIFT_VETO_THRESHOLD":
            if new_value is None or not (-0.30 <= new_value <= -0.03):
                self._log("❌", f"DRIFT_VETO fuori range: {new_value} (deve essere -0.30 a -0.03)")
                return

        self._write_bridge_command("modify_weight", {"param": param, "value": new_value})
        self._log("⚖️", f"Peso M2 {param} → {new_value}")

    def _cmd_adjust_soglia(self, payload: dict):
        """Modifica la soglia del CampoGravitazionale."""
        param     = payload.get("param", "")
        new_value = payload.get("value", payload.get("new_value", None))

        valid_params = {"SOGLIA_BASE": (45, 75), "SOGLIA_MIN": (25, 50), "SOGLIA_MAX": (70, 95)}

        if param not in valid_params:
            self._log("❌", f"Parametro soglia non valido: {param}")
            return

        lo, hi = valid_params[param]
        if new_value is None or not (lo <= new_value <= hi):
            self._log("❌", f"Valore soglia fuori range: {new_value} (deve essere {lo}-{hi})")
            return

        self._write_bridge_command("adjust_soglia", {"param": param, "value": new_value})
        self._log("📐", f"Soglia M2 {param} → {new_value}")

    def _write_bridge_command(self, cmd_type: str, data: dict):
        """
        Scrive comandi in bridge_commands.json.
        Il bot li legge nel prossimo ciclo di hot-reload.
        """
        cmd_file = "bridge_commands.json"
        try:
            existing = []
            if os.path.exists(cmd_file):
                with open(cmd_file) as f:
                    existing = json.load(f)

            existing.append({
                "type":      cmd_type,
                "data":      data,
                "timestamp": time.time(),
                "executed":  False,
            })

            with open(cmd_file, 'w') as f:
                json.dump(existing, f, indent=2)

        except Exception as e:
            log.error(f"[AI_BRIDGE] Write command error: {e}")

    # ── UTILITIES ─────────────────────────────────────────────────────────

    def _log(self, emoji: str, msg: str):
        """Log del bridge."""
        ts = datetime.utcnow().strftime('%H:%M:%S')
        entry = f"{ts} {emoji} [BRIDGE] {msg}"
        self._bridge_log.append(entry)
        if len(self._bridge_log) > 30:
            self._bridge_log.pop(0)
        log.info(entry)

    def _summarize(self, snapshot: dict) -> str:
        """Riassunto snapshot per history."""
        return (f"M1:{snapshot.get('trades',0)}t M2:{snapshot.get('m2_trades',0)}t "
                f"regime={snapshot.get('regime','?')} cap=${snapshot.get('capital',0):.0f}")

    def _update_heartbeat_bridge(self):
        """Esponi stato bridge nel heartbeat per la dashboard."""
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["bridge_active"]  = self._running
                self.heartbeat_data["bridge_errors"]   = self._consecutive_errors
                self.heartbeat_data["bridge_log"]      = list(self._bridge_log[-10:])
                self.heartbeat_data["bridge_commands"]  = list(self._commands_log[-5:])
                self.heartbeat_data["bridge_history"]   = len(self._history)
                self.heartbeat_data["bridge_last_call"] = (
                    self._history[-1]["ts"] if self._history else None
                )
        except Exception:
            pass
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def get_status(self) -> dict:
        """Stato del bridge per endpoint dedicato."""
        return {
            "active":            self._running,
            "enabled":           self.enabled,
            "has_api_key":       bool(self.api_key),
            "interval":          self.interval,
            "model":             self.model,
            "consecutive_errors":self._consecutive_errors,
            "total_calls":       len(self._history),
            "total_commands":    len(self._commands_log),
            "last_call":         self._history[-1]["ts"] if self._history else None,
            "last_response":     self._history[-1]["response"] if self._history else None,
            "log":               list(self._bridge_log[-15:]),
            "recent_commands":   list(self._commands_log[-10:]),
        }
