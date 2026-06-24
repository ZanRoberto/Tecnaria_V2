#!/usr/bin/env python3
# SIMULATORE OVERTOP — trova il sistema entrata x uscita che lascia piu' soldi
# Gira SUL SERVER (legge le curve dal DB). Lancio: python3 simula.py
import sqlite3, json

DB = "/var/data/trading_data.db"
FEE = 2.0  # fee tot per trade (andata+ritorno)

# carica le 388 curve reali
con = sqlite3.connect(DB)
rows = con.execute("SELECT curva_json FROM curva_nascita").fetchall()
con.close()

curve = []
for (cj,) in rows:
    try:
        pts = json.loads(cj)
        # ogni punto: [t, grasso, mfe]. grasso e' gia' netto delle fee iniziali? 
        # No: grasso parte da -2 = le fee. Lo riporto a LORDO aggiungendo 2.
        seq = [(float(p[0]), float(p[1]) + FEE) for p in pts if len(p) >= 2]
        if len(seq) >= 3:
            curve.append(seq)
    except Exception:
        pass

print(f"Curve caricate: {len(curve)}")
print("="*70)

# ===== SISTEMI D'ENTRATA: dato l'andamento, a che punto (indice) entro? =====
# Ognuno ritorna l'indice di entrata, o None se non entra.

def entra_mosse_su(seq, n=3):
    # entra dopo n grassi crescenti di fila (sistema attuale)
    su = 0
    for i in range(1, len(seq)):
        if seq[i][1] > seq[i-1][1]:
            su += 1
            if su >= n: return i
        else:
            su = 0
    return None

def entra_grasso_basso(seq, soglia=1.0):
    # entra appena il grasso supera soglia bassa (cavalca la salita)
    for i in range(len(seq)):
        if seq[i][1] >= soglia: return i
    return None

def entra_grasso_alto(seq, soglia=3.0):
    # entra quando ha gia' fatto 3 (sistema "in cima")
    for i in range(len(seq)):
        if seq[i][1] >= soglia: return i
    return None

def entra_tiene(seq, soglia=1.0, tiene_s=12.0, pct=0.7):
    # entra se grasso>=soglia E dopo tiene_s e' ancora >= pct del picco visto
    t0 = seq[0][0]
    for i in range(len(seq)):
        if seq[i][1] >= soglia:
            picco = max(p[1] for p in seq[:i+1])
            # guarda avanti: a tiene_s secondi, tiene?
            for j in range(i, len(seq)):
                if seq[j][0] - seq[i][0] >= tiene_s:
                    if seq[j][1] >= picco * pct:
                        return i
                    else:
                        return None  # si e' svuotato
            return i  # non c'e' abbastanza storia, entra
    return None

ENTRATE = {
    "A_mosse_su_3":     lambda s: entra_mosse_su(s, 3),
    "B_grasso>=1":      lambda s: entra_grasso_basso(s, 1.0),
    "C_grasso>=3_cima": lambda s: entra_grasso_alto(s, 3.0),
    "D_tiene_12s":      lambda s: entra_tiene(s, 1.0, 12.0, 0.7),
    "E_grasso>=0.5":    lambda s: entra_grasso_basso(s, 0.5),
}

# ===== SISTEMI D'USCITA: dato l'indice d'entrata, quando esco? ritorna pnl netto =====

def esci_presa_secca(seq, ie, target=3.0):
    # strappa appena grasso da entrata >= target (lordo)
    ge = seq[ie][1]
    for i in range(ie, len(seq)):
        g = seq[i][1] - ge  # grasso dall'entrata
        if g >= target:
            return g - FEE
    return (seq[-1][1] - ge) - FEE  # finito senza target

def esci_trailing(seq, ie, cede=0.3):
    # lascia correre, esce quando cede 'cede' dal picco-dall-entrata
    ge = seq[ie][1]
    picco = 0.0
    for i in range(ie, len(seq)):
        g = seq[i][1] - ge
        if g > picco: picco = g
        if picco > 0 and (picco - g) >= cede:
            return g - FEE
    return (seq[-1][1] - ge) - FEE

def esci_trailing_largo(seq, ie, cede=1.0):
    return esci_trailing(seq, ie, 1.0)

def esci_hardstop_1(seq, ie, stop=1.0, target=3.0, cede=0.3):
    # stop a -1, altrimenti trailing
    ge = seq[ie][1]
    picco = 0.0
    for i in range(ie, len(seq)):
        g = seq[i][1] - ge
        if g <= -stop:
            return g - FEE
        if g > picco: picco = g
        if picco >= target and (picco - g) >= cede:
            return g - FEE
    return (seq[-1][1] - ge) - FEE

USCITE = {
    "1_presa_secca_3":   lambda s, ie: esci_presa_secca(s, ie, 3.0),
    "2_trailing_0.3":    lambda s, ie: esci_trailing(s, ie, 0.3),
    "3_trailing_1.0":    lambda s, ie: esci_trailing_largo(s, ie),
    "4_stop1+trail":     lambda s, ie: esci_hardstop_1(s, ie, 1.0, 3.0, 0.3),
}

# ===== INCROCIO: ogni entrata x ogni uscita, totale soldi su tutte le curve =====
print(f"{'ENTRATA x USCITA':<32} {'n_entr':>7} {'totale$':>10} {'medio$':>8} {'win%':>6}")
print("-"*70)

risultati = []
for en, efn in ENTRATE.items():
    for un, ufn in USCITE.items():
        tot = 0.0; n = 0; win = 0
        for seq in curve:
            ie = efn(seq)
            if ie is None: continue
            pnl = ufn(seq, ie)
            tot += pnl; n += 1
            if pnl > 0: win += 1
        if n > 0:
            risultati.append((tot, en, un, n, tot/n, 100*win/n))

# ordina per totale soldi decrescente
risultati.sort(reverse=True)
for tot, en, un, n, medio, wr in risultati:
    print(f"{en+' x '+un:<32} {n:>7} {tot:>+10.1f} {medio:>+8.2f} {wr:>5.0f}%")

print("="*70)
print("Il primo della lista lascia PIU' SOLDI sui 388 trade reali.")
