#!/usr/bin/env python3
# SIMULA STRAPPO (corretto, blindato) — cammina la curva VERA tick-per-tick.
# Trova la soglia di strappo che SALVA i trans (che franano) SENZA castrare i
# maschi (che salirebbero ancora). Solo sui trade che ENTRANO (picco>=1).
# Regola d'oro blindato: NON tagliare i maschi. Cerco totale max.
# Lancio: python3 simula4.py
import sqlite3, json

DB = "/var/data/trading_data.db"
FEE = 2.0

con = sqlite3.connect(DB)
rows = con.execute("SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE peak_nascita >= 1.0").fetchall()
con.close()

trades = []
for peak, pnlf, cj in rows:
    try:
        pts = json.loads(cj)
        # curva di grasso LORDO (il salvato e' netto -2, riporto a lordo)
        curva = [float(p[1]) + FEE for p in pts if len(p) >= 2]
        if len(curva) >= 2:
            trades.append({"curva": curva, "pnl_reale": float(pnlf or 0)})
    except Exception:
        pass

N = len(trades)
tot_reale = sum(t["pnl_reale"] for t in trades)
print(f"Trade che ENTRANO (picco>=1): {N}")
print(f"TOTALE REALE (uscita attuale): {tot_reale:+.1f}")
print("="*64)

# STRAPPO: cammina la curva. Appena il grasso LORDO tocca 'soglia', esci (strappo).
# Se non tocca mai la soglia, il trade va come e' andato REALMENTE (pnl_reale):
#   -> cosi' NON invento l'uscita dei maschi che non strappo: uso il loro esito vero.
# Questo evita l'errore di simula1/simula3 (inventare uscite ovunque).
def simula_strappo(soglia):
    tot = 0.0; strappati = 0; nw = 0
    for t in trades:
        # il grasso ha mai raggiunto la soglia di strappo?
        if max(t["curva"]) >= soglia:
            # strappo: incasso soglia netto fee
            pnl = soglia - FEE
            strappati += 1
        else:
            # non ha raggiunto la soglia: lascio l'esito REALE (non invento)
            pnl = t["pnl_reale"]
        tot += pnl
        if pnl > 0: nw += 1
    return tot, strappati, nw

print(f"{'STRAPPO a':<12} {'totale$':>9} {'strappati':>10} {'win':>6} {'vs reale':>9}")
print("-"*64)
for soglia in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
    tot, strap, nw = simula_strappo(soglia)
    delta = tot - tot_reale
    print(f"+{soglia:<11} {tot:>+9.1f} {strap:>10} {nw:>4}/{N} {delta:>+9.1f}")
print("="*64)
print(f"REALE (nessuno strappo):  {tot_reale:>+9.1f}")
print()
print("La soglia col TOTALE piu' alto e' lo strappo giusto.")
print("Nota: strappare TUTTI a soglia X castra i maschi che salivano oltre.")
print("Se il reale batte tutti, l'uscita attuale e' gia' ottima e NON si tocca.")
