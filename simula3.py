#!/usr/bin/env python3
# SIMULA USCITA (serio) — eredita da simula2: cammina la curva VERA tick-per-tick.
# Differenza da sim_trailing (23giu, abbozzo): NON stima da p10/p20, cammina TUTTO.
# Differenza da simula1 (sbagliato): NON inventa, applica le regole alla curva reale.
# Simula SOLO sui trade che ENTRANO (MFE>=1, la porta unica). L'uscita lavora su quelli.
# Lancio: python3 simula3.py
import sqlite3, json

DB = "/var/data/trading_data.db"
FEE = 2.0
MFE_MIN = 1.0  # la porta unica: simulo l'uscita solo su chi entra

con = sqlite3.connect(DB)
rows = con.execute("SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita").fetchall()
con.close()

# tengo solo i trade che ENTRANO (picco >= MFE_MIN) e la loro curva di grasso LORDO
entrati = []
for peak, pnlf, cj in rows:
    try:
        if (peak or 0) < MFE_MIN:
            continue  # non entra, l'uscita non lo tocca
        pts = json.loads(cj)
        # curva = [(t, grasso_lordo)] — il grasso nel DB e' netto delle fee iniziali (-2),
        # lo riporto a lordo per ragionare sul movimento puro, poi tolgo FEE all'uscita.
        curva = [(float(p[0]), float(p[1]) + FEE) for p in pts if len(p) >= 2]
        if len(curva) >= 2:
            entrati.append({"curva": curva, "pnl_reale": float(pnlf or 0)})
    except Exception:
        pass

N = len(entrati)
tot_reale = sum(t["pnl_reale"] for t in entrati)
print(f"Trade che ENTRANO (MFE>={MFE_MIN}): {N}")
print(f"Totale REALE di questi (com'e' andata l'uscita vera): {tot_reale:+.1f}")
print("="*72)

# ===== REGOLE D'USCITA da testare (camminano la curva dall'ingresso) =====
# Ognuna ritorna il pnl NETTO (lordo all'uscita - FEE).

def usc_trailing(curva, attiva, margine):
    # lascia correre; quando il grasso scende 'margine' sotto il picco visto -> esce.
    # se non attiva mai il picco sopra 'attiva', esce alla fine.
    picco = curva[0][1]
    for t, g in curva:
        if g > picco:
            picco = g
        if picco >= attiva and (picco - g) >= margine:
            return g - FEE
    return curva[-1][1] - FEE

def usc_strappo(curva, target):
    # strappa appena tocca +target lordo (PRESA SECCA). Se non arriva, esce alla fine.
    for t, g in curva:
        if g >= target:
            return g - FEE
    return curva[-1][1] - FEE

def usc_stop_trailing(curva, stop, attiva, margine):
    # stop secco a -stop; altrimenti trailing.
    picco = curva[0][1]
    for t, g in curva:
        if g <= -stop:
            return g - FEE
        if g > picco:
            picco = g
        if picco >= attiva and (picco - g) >= margine:
            return g - FEE
    return curva[-1][1] - FEE

def valuta(nome, fn):
    tot = 0.0; nw = 0
    for t in entrati:
        pnl = fn(t["curva"])
        tot += pnl
        if pnl > 0: nw += 1
    wr = 100*nw/N if N else 0
    print(f"{nome:<34} totale={tot:>+8.1f}  win={nw:>3}/{N}  WR={wr:>3.0f}%")
    return tot

print(f"{'REGOLA USCITA':<34} {'risultato (su chi entra)'}")
print("-"*72)
print(f"{'REALE (uscita attuale)':<34} totale={tot_reale:>+8.1f}")
print("-"*72)
# TRAILING a varie tarature
for attiva in [1.0, 1.5, 2.0]:
    for margine in [0.3, 0.5, 1.0]:
        valuta(f"trailing attiva{attiva} margine{margine}", lambda c,a=attiva,m=margine: usc_trailing(c,a,m))
print("-"*72)
# STRAPPO SECCO
for target in [1.5, 2.0, 3.0]:
    valuta(f"strappo_secco +{target}", lambda c,t=target: usc_strappo(c,t))
print("-"*72)
# STOP + TRAILING
for stop in [1.0, 2.0]:
    valuta(f"stop-{stop}+trailing(att1.5,mar0.5)", lambda c,s=stop: usc_stop_trailing(c,s,1.5,0.5))
print("="*72)
print("Cerca il totale PIU' ALTO. Quella e' l'uscita che lascia piu' soldi")
print("sui trade che entrano. Confronta col REALE qui sopra.")
