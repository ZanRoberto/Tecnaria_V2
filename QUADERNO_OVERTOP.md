# QUADERNO OVERTOP — registro di laboratorio

> Non una teoria. Il **quaderno delle prove**: cosa abbiamo provato, com'è finito,
> e perché NON va riproposto. Ogni istanza nuova legge questo PRIMA di proporre,
> così non rifà le prove già cadute. Einstein teneva il quaderno; l'AI senza quaderno
> riparte cieca ogni volta.

**Build corrente:** `e6411416` — motore con osservazione del *vettore del piattello*.
**Storico di riferimento:** 675 trade M2_EXIT, netto fortemente negativo.

---

## REGOLA ZERO (in cima, perché è quella che ci frega)

**Io (l'AI) sono statistica, non predizione.** Conto frequenze passate. Non spaccio
una media per una legge del futuro. Il predittore è la **memoria dei tiri reali**
(Sinapsi), non la mia curva. Quando dico "questo separa", è un'ipotesi da misurare,
mai un fatto.

---

## PROVE FALLITE — non riproporle (con la ragione)

| # | Ipotesi | Esito | Perché è caduta |
|---|---|---|---|
| 1 | **Corsia maschio** (entra subito se grasso netto > soglia) | RIMOSSA | Azzerava lo stato del ritardo e poi cadeva nei cancelli a valle che la rileggevano "piatta"; scriveva entrato=1 falso senza aprire. Era GATE PEAK senza finestra. |
| 2 | **Il seme separa maschi/femmine** | FALSO | 484 ibridi: perdenti con seme alto quanto/più dei vincenti. Su 675 trade non separa. Conferma firma ambigua. |
| 3 | **momentum × volatilità è la firma** | FALSO | Ogni cella sotto il 36% WR, nessuna in verde. Non c'è una cella buona da tenere. |
| 4 | **`regime` è la firma** (es. BEAR) | CIRCOLARE | `_regime_current` misurato alla CHIUSURA. Un LONG che perde → prezzo sceso → regime letto BEAR. La perdita causa l'etichetta. Mai usare il regime come firma d'ingresso. |
| 5 | **Le perdite "corrono" di più adesso** | FALSO | Recenti −2.41 vs vecchi −2.81. Le recenti sono PIÙ PICCOLE. Avevo guardato 2 code drammatiche (−5) e le avevo lette come trend. Tasso-base. |
| 6 | **DEBOLE\|ALTA è una firma da bloccare** | DEBOLE | 6% WR (peggiore), ma è storico vecchio e i 6 vincenti sono briciole. Candidato, NON confermato sul vivo. Va misurato in avanti prima di bloccare. |
| 7 | **Griglia cartesiana di soglie/pesi** | PERICOLOSA | Forza bruta su pochi dati = overfitting. Più combinazioni provi, più è probabile che una vinca per CASO sullo storico e muoia sul vivo. Mai come prima mossa. |

---

## VERITÀ MISURATA (il muro)

Su 675 trade: **nessuna variabile misurata all'INGRESSO separa i vincenti dai perdenti.**
Seme, momentum, volatilità, regime — tutte sotto la soglia di utilità. È la legge della
**firma ambigua** di Roberto, confermata su larga scala: all'istante della nascita, maschio
e femmina (vincente e perdente) sono indistinguibili.

**Conseguenza dura:** se neanche la prossima ipotesi separa, NON c'è edge all'ingresso —
e la risposta giusta diventa *tradare molto meno / fermarsi*, non tarare un altro gate.

---

## IPOTESI VIVA — il tiro al piattello (occhio della volpe, non della mucca)

La mucca guarda il **punto** (dov'è il treno/picco). La volpe guarda il **vettore**
(da dove arriva, quanto va, dove sarà). Tutta la notte ho guardato punti fermi (picco,
seme, pnl finale) — per questo le femmine passano: un punto non dice se sta arrivando o
se ne sta andando.

**L'algoritmo (da provare, non da credere):**
- Non entro sul picco (dov'è ORA). Stimo la traiettoria nella finestra e proietto dove
  sarà tra 1-2s. Entro SE il punto proiettato batte la fee — "il proiettile più veloce
  del piattello".
- **Due finestre, non una:** una corta (dov'è) e una lunga (da dove viene). Entro solo se
  **concordano** sulla direzione futura. Disaccordo tra finestre = non sparo.
  - Femmina che spizza → corta dice "sale", lunga vede il rotolamento → **discordano → no.**
  - Maschio vivo → concordano → sì. Piatto → niente sale → no.
- **Le componenti del vettore:** da dove parte (posizione nel range), il **vento**
  (`vol_pressure`, esiste già), il **peso** (ampiezza/corpo del movimento), la **direzione**
  a due finestre.
- **Il predittore è Sinapsi, non la statistica:** non "qual è la media", ma "l'ultima volta
  che è partito da QUESTA posizione, con QUESTO vento e QUESTO peso — l'ho centrato o mancato?".
  Memoria di casi reali.

**Il dubbio onesto (dove vive o muore):** il piattello ha fisica deterministica; il prezzo
a 1-2s no, può invertire a mezz'aria. La domanda che decide tutto: **nella finestra, la
direzione dei primi secondi PERSISTE abbastanza spesso da pagare i piattelli che invertono?**
Non si sa a memoria. Si misura.

---

## PROTOCOLLO — ordine obbligato (non saltarlo)

1. **Raccogliere la variabile giusta PRIMA di simulare.** Senza il dato, simulare è
   indovinare. → FATTO: build `e6411416` registra il vettore in `piattello_osserva`.
2. **Misurare la persistenza/concordanza** WIN vs LOSS sul vettore raccolto. Una sola
   ipotesi alla volta, test secco.
3. **Controprova anti-autoinganno (obbligatoria):** ogni regola che sembra vincere sui
   perdenti va RI-testata sui vincenti. Se taglia anche quelli → è rumore → scartata.
4. **NIENTE griglia cartesiana come prima mossa.** Una variabile con senso fisico, misurata
   pulita. La forza bruta solo come ultima spiaggia, mai come metodo.

---

## REGOLE DI METODO (le lezioni della notte — valgono per ogni sessione)

- Prima di proporre, **documentare l'esistente** (vedi `MAPPA_ESISTENTE.md`). Non rituffarsi nel buio.
- `regime` è alla chiusura → **circolare**, mai firma d'ingresso.
- Pannello: **PNL netto, 10S lordo**. Grasso sotto +2 lordo = fee travestita.
- **Due semi diversi** (SeedScorer vs seed_history). Non confonderli.
- **Tasso-base:** due numeri drammatici non sono una firma. Guarda il denominatore.
- **Un Passo = un file = un test = un deploy.** Mai pacchettare.

---

## STATO RACCOLTA (build `e6411416`)

Tabella `piattello_osserva` — UNA riga per aggancio che arriva a GATE PEAK, solo osserva,
non tocca la decisione. Campi: `slope_corto`, `slope_lungo` (le due finestre, lunghezze in
ENV `VETTORE_WIN_CORTO`/`VETTORE_WIN_LUNGO`, default 5/15), `pos_start` (da dove parte),
`vento` (vol_pressure), `peso` (ampiezza), `picco_pre`, `passa`, `agg_rowid` (per legare
all'esito via `ritardo_agganci`).

Dopo ~30-40 agganci, la prima domanda da fare ai dati:

```
sqlite3 /var/data/trading_data.db "SELECT passa, COUNT(*) n, ROUND(AVG(slope_corto),5) sc, ROUND(AVG(slope_lungo),5) sl, ROUND(AVG(vento),3) vento, ROUND(AVG(peso),2) peso FROM piattello_osserva GROUP BY passa;"
```

Poi, quando gli agganci passati avranno un esito (join su `agg_rowid` → trade), la domanda
vera: **slope_corto e slope_lungo CONCORDANO sui vincenti e DISCORDANO sui perdenti?**
Se sì, il piattello è reale. Se no, si torna alla "verità muro" e si trada meno.
