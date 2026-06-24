#!/usr/bin/env python3
# SIMULA FILTRO COMBINATO — cammina le 388 curve reali, estrae MAE + grasso@10s + MFE
# per ognuna, e prova filtri d'ingresso combinati. Obiettivo: separare WIN da LOSS
# e battere il totale reale (-636). Niente codice nel bot finche' non batte -636.
# Lancio sul server: python3 simula2.py
import sqlite3, json

DB = "/var/data/trading_data.db"

con = sqlite3.connect(DB)
rows = con.execute("SELECT peak_nascita, t_peak_s, pnl_a_10s, pnl_finale, curva_json FROM curva_nascita").fetchall()
con.close()

trades = []
for peak, tpeak, g10, pnlf, cj in rows:
    try:
        pts = json.loads(cj)
        grassi = [float(p[1]) for p in pts if len(p) >= 2]  # colonna grasso
        if not grassi: continue
        mae = min(grassi)          # MAE = crollo minimo (il piu' negativo)
        mfe = max(grassi)          # MFE = picco massimo
        trades.append({
            "mfe": float(peak or mfe),
            "mae": mae,
            "tpeak": float(tpeak or 0),
            "g10": float(g10 or 0),
            "pnl": float(pnlf or 0),
            "win": (float(pnlf or 0) > 0),
        })
    except Exception:
        pass

N = len(trades)
tot_reale = sum(t["pnl"] for t in trades)
nwin = sum(1 for t in trades if t["win"])
print(f"Trade: {N} | WIN {nwin} | LOSS {N-nwin} | TOTALE REALE {tot_reale:+.1f}")
print("="*78)

# Un filtro = una funzione che dice ENTRA(True)/SCARTA(False) dato il candidato.
# Per ogni filtro calcolo: quanti entra, quanti win catturati, quanti loss evitati,
# e il TOTALE $ = somma dei pnl dei SOLI trade che il filtro fa entrare.

def valuta(nome, cond):
    entrati = [t for t in trades if cond(t)]
    if not entrati:
        print(f"{nome:<42} entra=0")
        return
    tot = sum(t["pnl"] for t in entrati)
    nw = sum(1 for t in entrati if t["win"])
    win_persi = nwin - nw  # win che il filtro ha scartato
    print(f"{nome:<42} entra={len(entrati):>3} win={nw:>3} totale={tot:>+7.1f} win_persi={win_persi:>3}")

print(f"{'FILTRO':<42} {'risultato'}")
print("-"*78)
# baseline: entra in tutto (sistema attuale ~)
valuta("0_ENTRA_TUTTO (baseline)", lambda t: True)
print("-"*78)
# singole firme
for soglia in [0.0, 1.0, 2.0]:
    valuta(f"MFE>={soglia}", lambda t,s=soglia: t["mfe"] >= s)
print("-"*78)
for soglia in [-1.0, -2.0, -3.0]:
    valuta(f"MAE>={soglia} (scende poco=dritto)", lambda t,s=soglia: t["mae"] >= s)
print("-"*78)
for soglia in [10, 15, 20]:
    valuta(f"t_peak>={soglia}s (picco tardivo)", lambda t,s=soglia: t["tpeak"] >= s)
print("-"*78)
for soglia in [-1.0, 0.0, 1.0]:
    valuta(f"g10>={soglia} (grasso a 10s)", lambda t,s=soglia: t["g10"] >= s)
print("="*78)
print("COMBINATI (la firma vera: MAE dritto + tempo + grasso):")
print("-"*78)
# combinato MAE + t_peak (sale dritto E picco tardivo)
valuta("MAE>=-2 AND tpeak>=10", lambda t: t["mae"]>=-2 and t["tpeak"]>=10)
valuta("MAE>=-2 AND tpeak>=15", lambda t: t["mae"]>=-2 and t["tpeak"]>=15)
valuta("MAE>=-1 AND tpeak>=10", lambda t: t["mae"]>=-1 and t["tpeak"]>=10)
valuta("MAE>=-3 AND tpeak>=15", lambda t: t["mae"]>=-3 and t["tpeak"]>=15)
# combinato MAE + MFE
valuta("MAE>=-2 AND MFE>=2", lambda t: t["mae"]>=-2 and t["mfe"]>=2)
valuta("MAE>=-1 AND MFE>=1", lambda t: t["mae"]>=-1 and t["mfe"]>=1)
# combinato pieno: MAE dritto + tardivo + grasso costruito
valuta("MAE>=-2 AND tpeak>=12 AND MFE>=2", lambda t: t["mae"]>=-2 and t["tpeak"]>=12 and t["mfe"]>=2)
valuta("MAE>=-2 AND tpeak>=15 AND MFE>=2", lambda t: t["mae"]>=-2 and t["tpeak"]>=15 and t["mfe"]>=2)
valuta("MAE>=-1 AND tpeak>=15 AND MFE>=2", lambda t: t["mae"]>=-1 and t["tpeak"]>=15 and t["mfe"]>=2)
print("="*78)
print("Cerca la riga con TOTALE piu' alto e win_persi BASSO.")
print("Quella e' la firma che prende i win e scarta i loss. Batte -636?")
