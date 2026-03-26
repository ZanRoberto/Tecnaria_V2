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
    Bridge PREDITTIVO — vive ogni tick, anticipa, comanda.
    Non narra. Non aspetta. Non racconta il passato.
    
    4 LAYER:
    L1 — GEOMETRIA:  posizione nel range, compressione
    L2 — SEQUENZA:   drift persistence, volume pressure, sign flips
    L3 — MEMORIA:    Signal Tracker — il dopo diventa il prima
    L4 — DECISIONE:  carica → conferma → ENTRA o BLOCCA
    """

    def __init__(self, heartbeat_data, heartbeat_lock,
                 capsule_file="capsule_attive.json"):
        self.heartbeat_data = heartbeat_data
        self.heartbeat_lock = heartbeat_lock
        self.capsule_file   = capsule_file

        self.api_key  = os.environ.get("DEEPSEEK_API_KEY", "")
        self.interval = 5  # ogni 5 secondi — vive il tick
        self.enabled  = os.environ.get("AI_BRIDGE_ENABLED", "true").lower() == "true"

        # Stato interno
        self._thread    = None
        self._running   = False
        self._history   = []
        self._commands_log = []
        self._consecutive_errors = 0
        self._bridge_log = []

        # L1+L2 — buffer tick per calcolo feature sequenziali
        self._buf_price  = []   # ultimi 50 prezzi
        self._buf_drift  = []   # ultimi 50 drift
        self._buf_volume = []   # ultimi 50 volumi
        self._last_price = 0.0

        # L3 — memoria pattern dal Signal Tracker
        self._signal_memory = {}  # contesto → {hit_rate, avg_delta, n}

        # L4 — carica e stato
        self._carica     = 0.0
        self._stato      = "ATTESA"   # ATTESA / CARICA / FUOCO
        self._tick_pronto = 0
        self._soglia_fuoco = 0.65
        self._loss_streak  = 0

        # Ultimo comando eseguito
        self._last_comando = None
        self._last_regime  = ""
        self._last_m2_trades = 0

    def start(self):
        if not self.enabled:
            log.info("[AI_BRIDGE] ⚠️ Disabilitato")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ai_bridge_thread")
        self._thread.start()
        log.info("[AI_BRIDGE] 🧠 Bridge PREDITTIVO attivo — vive ogni tick")

    def stop(self):
        self._running = False
        log.info("[AI_BRIDGE] 🛑 Fermato")

    def _loop(self):
        time.sleep(30)  # warmup iniziale ridotto
        self._log("🧠", "Bridge predittivo online")

        while self._running:
            try:
                snapshot = self._read_snapshot()
                if snapshot:
                    self._processa_tick(snapshot)
                self._update_heartbeat_bridge()
            except Exception as e:
                log.error(f"[AI_BRIDGE] Errore: {e}")
                self._consecutive_errors += 1
            time.sleep(self.interval)

    def _read_snapshot(self) -> dict:
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            return dict(self.heartbeat_data) if self.heartbeat_data else {}
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def _processa_tick(self, snap: dict):
        """
        Cuore del Bridge predittivo.
        Ogni 5 secondi legge lo stato e decide.
        """
        price    = snap.get("last_price", 0.0)
        regime   = snap.get("regime", "RANGING")
        drift    = snap.get("m2_campo_stats", {}).get("drift", 0.0)
        rsi      = snap.get("m2_campo_stats", {}).get("rsi", 50.0)
        macd     = snap.get("m2_campo_stats", {}).get("macd_hist", 0.0)
        score    = snap.get("m2_last_score", 0.0)
        shadow   = snap.get("m2_shadow_open", False)
        m2_trades= snap.get("m2_trades", 0)
        loss_str = snap.get("m2_loss_streak", 0)

        if price == 0 or price == self._last_price:
            return
        self._last_price = price

        # Aggiorna buffer
        self._buf_price.append(price)
        self._buf_drift.append(drift)
        self._buf_volume.append(1.0)  # volume normalizzato
        if len(self._buf_price) > 50:
            self._buf_price.pop(0)
            self._buf_drift.pop(0)
            self._buf_volume.pop(0)

        if len(self._buf_price) < 20:
            return

        # Aggiorna memoria dal Signal Tracker
        self._aggiorna_memoria(snap)

        # L1 + L2: calcola feature
        f = self._calcola_features()
        if not f:
            return

        # L3: consulta memoria
        pred = self._consulta_memoria(regime, score)

        # L4: aggiorna carica e decide
        comando = self._aggiorna_carica_e_decidi(f, regime, pred, shadow, loss_str)

        # Esegui comando se cambiato
        if comando and comando != self._last_comando:
            self._esegui_comando(comando, f, pred, regime, score, rsi, macd)
            self._last_comando = comando

        # Aggiorna loss streak
        if m2_trades > self._last_m2_trades:
            self._last_m2_trades = m2_trades

    def _calcola_features(self) -> dict:
        """L1+L2 — geometria e sequenza."""
        pp = self._buf_price[-20:]
        dd = self._buf_drift[-20:]
        vv = self._buf_volume[-20:]

        r5  = max(pp[-5:])  - min(pp[-5:])
        r10 = max(pp[-10:]) - min(pp[-10:])
        r20 = max(pp)       - min(pp)

        if r20 == 0:
            return None

        pos     = (pp[-1] - min(pp)) / r20
        cr      = r5 / (r10 + 0.01)
        midzone = 0.40 <= pos <= 0.60
        bordo   = pos >= 0.80

        dp = sum(1 for d in dd[-10:] if d > 0) / 10
        sf = sum(1 for i in range(1, len(dd)) if dd[i]*dd[i-1] < 0)

        vm5  = sum(vv[-5:])  / 5
        vm15 = sum(vv[-15:]) / 15
        vp   = vm5 / (vm15 + 0.01)
        ds   = sum(dd[-5:])/5 - sum(dd[-15:])/15

        cd = sum(1 for i in range(len(pp)-1, max(0, len(pp)-15), -1)
                 if (max(pp[max(0,i-5):i+1]) - min(pp[max(0,i-5):i+1])) < r20*0.65)

        return {
            'pos': pos, 'cr': cr, 'midzone': midzone, 'bordo': bordo,
            'dp': dp, 'sf': sf, 'vp': vp, 'ds': ds, 'cd': cd
        }

    def _aggiorna_memoria(self, snap: dict):
        """L3 — aggiorna memoria dal Signal Tracker."""
        tracker = snap.get("signal_tracker", {})
        top     = tracker.get("top", [])
        for entry in top:
            ctx     = entry.get("context", "")
            hit     = entry.get("hit_60s", 0.5)
            delta   = entry.get("avg_delta_60s", 0)
            pnl     = entry.get("pnl_sim_avg", 0)
            n       = entry.get("n", 0)
            self._signal_memory[ctx] = {
                'hit_rate':  hit,
                'avg_delta': delta,
                'pnl_sim':   pnl,
                'n':         n,
            }

    def _consulta_memoria(self, regime: str, score: float) -> dict:
        """L3 — consulta il Signal Tracker per questo contesto."""
        if score >= 75:   band = "FORTE_75+"
        elif score >= 65: band = "BUONO_65-75"
        elif score >= 58: band = "BASE_58-65"
        else:             band = "DEBOLE_<58"

        key = f"LONG {regime} {band}"
        mem = self._signal_memory.get(key, {})

        if not mem or mem.get('n', 0) < 5:
            return {'verdict': 'NO_DATA', 'hit_rate': 0.5, 'avg_delta': 0}

        hit  = mem['hit_rate']
        pnl  = mem['pnl_sim']
        delta= mem['avg_delta']

        if hit >= 0.65 and pnl > 0:
            verdict = "ENTRA"
        elif hit <= 0.40 or pnl < -1:
            verdict = "BLOCCA"
        else:
            verdict = "NEUTRO"

        return {'verdict': verdict, 'hit_rate': hit, 'avg_delta': delta, 'pnl_sim': pnl}

    def _aggiorna_carica_e_decidi(self, f: dict, regime: str,
                                   pred: dict, shadow: bool,
                                   loss_streak: int) -> str:
        """L4 — accumula carica e decide il comando."""

        # BLOCCO TOTALE: posizione già aperta
        if shadow:
            self._carica = self._carica * 0.8
            self._stato  = "TIENI"
            return None

        # BLOCCO: loss streak alta
        if loss_streak >= 3:
            self._stato = "ATTESA"
            return "BLOCCA"

        # BLOCCO: midzone
        if f['midzone']:
            self._carica     = self._carica * 0.5
            self._tick_pronto = 0
            self._stato      = "ATTESA"
            return "BLOCCA"

        # BLOCCO: memoria dice BLOCCA con alta affidabilità
        if pred['verdict'] == "BLOCCA" and pred.get('hit_rate', 0.5) < 0.40:
            self._carica = self._carica * 0.6
            self._stato  = "ATTESA"
            return "BLOCCA"

        # Calcola carica
        nc = 0.0
        if f['cr']  < 0.80: nc += 0.20
        if f['bordo']:       nc += 0.25
        if f['dp'] >= 0.60: nc += 0.20
        if f['sf'] <= 4:    nc += 0.15
        if f['vp'] >= 1.1:  nc += 0.15
        if f['ds'] >  0:    nc += 0.05

        # Boost da memoria
        if pred['verdict'] == "ENTRA":
            nc = min(1.0, nc * 1.20)

        self._carica = self._carica * 0.7 + nc * 0.3

        # Stato macchina
        if self._carica >= self._soglia_fuoco:
            self._tick_pronto += 1
        else:
            self._tick_pronto = 0

        if self._tick_pronto >= 2:
            # Conferma: drift positivo e prezzo in espansione
            drift_ok  = len(self._buf_drift)>=2 and self._buf_drift[-1] > 0.0001
            price_ok  = len(self._buf_price)>=2 and self._buf_price[-1] > self._buf_price[-2]
            if drift_ok and price_ok:
                self._stato = "FUOCO"
                return "ENTRA"
            else:
                self._stato = "CARICA"
                return None
        elif self._carica >= 0.40:
            self._stato = "CARICA"
            return None
        else:
            self._stato = "ATTESA"
            return None

    def _esegui_comando(self, comando: str, f: dict, pred: dict,
                         regime: str, score: float, rsi: float, macd: float):
        """Scrive il comando nel heartbeat per il bot."""
        ts = datetime.utcnow().isoformat()

        if comando == "ENTRA":
            motivo = (f"CARICA {self._carica:.2f} | "
                     f"pos={f['pos']:.2f} cr={f['cr']:.2f} dp={f['dp']:.2f} | "
                     f"mem={pred['verdict']} hit={pred['hit_rate']:.0%}")
            self._write_bridge_command("entry_signal", {
                "carica":  round(self._carica, 3),
                "regime":  regime,
                "score":   score,
                "motivo":  motivo,
            })
            self._log("🚀", f"FUOCO — {motivo}")

        elif comando == "BLOCCA":
            motivo = ("MIDZONE" if f['midzone'] else
                     f"MEM_BLOCCA hit={pred.get('hit_rate',0):.0%}" if pred['verdict']=="BLOCCA" else
                     "LOSS_STREAK")
            self._log("🚫", f"BLOCCA — {motivo}")

        # Aggiorna heartbeat con stato Bridge
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["bridge_stato"]   = self._stato
                self.heartbeat_data["bridge_carica"]  = round(self._carica, 3)
                self.heartbeat_data["bridge_comando"] = comando
                self.heartbeat_data["bridge_analisi"] = (
                    f"Carica={self._carica:.2f} Stato={self._stato} "
                    f"Regime={regime} Score={score:.1f} "
                    f"Mem={pred['verdict']} Hit={pred['hit_rate']:.0%}"
                )
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

        cmd_entry = {
            "tipo":    comando,
            "alert":   "green" if comando == "ENTRA" else "yellow",
            "payload": {"carica": round(self._carica, 3), "motivo": motivo if 'motivo' in dir() else ""},
            "ts":      ts,
        }
        self._commands_log.append(cmd_entry)
        if len(self._commands_log) > 20:
            self._commands_log.pop(0)
        self._bridge_log.append(f"{ts[11:19]} {'🚀' if comando=='ENTRA' else '🚫'} [{comando}] {regime} carica={self._carica:.2f}")
        if len(self._bridge_log) > 20:
            self._bridge_log.pop(0)

    def _write_bridge_command(self, cmd_type: str, data: dict):
        """Scrive comando nel DB per il bot."""
        try:
            import sqlite3
            db_path = os.environ.get("DB_PATH", "/home/app/data/trading_data.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT OR REPLACE INTO bot_state VALUES ('bridge_cmd', ?)",
                (json.dumps({"type": cmd_type, "data": data, "ts": datetime.utcnow().isoformat()}),)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"[AI_BRIDGE] Errore scrittura cmd: {e}")

    def _log(self, emoji: str, msg: str):
        entry = f"{datetime.utcnow().strftime('%H:%M:%S')} {emoji} [BRIDGE] {msg}"
        log.info(entry)
        self._bridge_log.append(entry)
        if len(self._bridge_log) > 20:
            self._bridge_log.pop(0)

    def _update_heartbeat_bridge(self):
        if self.heartbeat_lock:
            self.heartbeat_lock.acquire()
        try:
            if self.heartbeat_data is not None:
                self.heartbeat_data["bridge_active"]   = True
                self.heartbeat_data["bridge_alert"]    = (
                    "green" if self._stato == "FUOCO" else
                    "yellow" if self._stato == "CARICA" else "grey"
                )
                self.heartbeat_data["bridge_stato"]    = self._stato
                self.heartbeat_data["bridge_carica"]   = round(self._carica, 3)
                self.heartbeat_data["bridge_commands"] = self._commands_log[-5:]
                self.heartbeat_data["bridge_log"]      = self._bridge_log[-10:]
                self.heartbeat_data["bridge_errors"]   = self._consecutive_errors
                self.heartbeat_data["bridge_last_ts"]  = datetime.utcnow().strftime("%H:%M:%S")
        finally:
            if self.heartbeat_lock:
                self.heartbeat_lock.release()

    def get_status(self) -> dict:
        return {
            "active":   self._running,
            "stato":    self._stato,
            "carica":   round(self._carica, 3),
            "comando":  self._last_comando,
            "memoria":  len(self._signal_memory),
            "log":      self._bridge_log[-5:],
        }
