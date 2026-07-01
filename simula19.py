#!/usr/bin/env python3
"""
SIMULA19 — DINAMICA DEI PRIMI TICK: VIVI vs MORTI

Ipotesi: la femmina travestita imita il LIVELLO del maschio ma non il PASSO.
         La dinamica dei primi 3-5s distingue chi arriverà a 2.5 lordo da chi no.

GRUPPI:
  VIVI:  peak_nascita >= 0.5 (lordo >= 2.5) → 133 trade
  MORTI: peak_nascita <  0.5 (lordo <  2.5) → 385 trade

METRICHE (calcolate sui PRIMI tick, t < 5s dall'ingresso):
  1. VELOCITÀ:    media del delta-grasso per tick (sale veloce o lento?)
  2. CONTINUITÀ:  inversioni di direzione (dritto=0, a strappi=tante)
  3. ACCELERAZIONE: velocità finale - velocità iniziale (accelera o decelera?)
  4. PNL@t:       grasso a 1s, 2s, 3s, 5s (snapshot precoce)
  5. MAX_EARLY:   picco grasso nei primi 3s

Lancio: python3 simula19.py
"""
import sqlite3, json, math

DB = "/var/data/trading_data.db"

con  = sqlite3.connect(DB)
rows = con.execute(
    "SELECT peak_nascita, pnl_finale, curva_json FROM curva_nascita WHERE pnl_finale IS NOT NULL"
).fetchall()
con.close()

def analizza_dinamica(pts, t_max=5.0):
    """Analizza i tick fino a t_max secondi. Restituisce dict con metriche."""
    early = [(float(p[0]), float(p[1])) for p in pts
             if len(p) >= 2 and float(p[0]) <= t_max]
    if len(early) < 2:
        return None

    times  = [p[0] for p in early]
    grassi = [p[1] for p in early]

    # velocità tick per tick (delta grasso / delta tempo)
    velocita = []
    for i in range(1, len(early)):
        dt = times[i] - times[i-1]
        if dt > 0:
            velocita.append((grassi[i] - grassi[i-1]) / dt)

    if not velocita:
        return None

    avg_vel = sum(velocita) / len(velocita)

    # inversioni (cambio di segno in velocità consecutiva)
    inversioni = 0
    for i in range(1, len(velocita)):
        if velocita[i-1] * velocita[i] < 0:  # segni opposti
            inversioni += 1

    # accelerazione: confronta prima metà vs seconda metà velocità
    mid = len(velocita) // 2
    vel_early = sum(velocita[:max(1,mid)]) / max(1, mid)
    vel_late  = sum(velocita[mid:]) / max(1, len(velocita)-mid)
    accelerazione = vel_late - vel_early

    # max grasso nei primi t_max secondi
    max_early = max(grassi)

    # snapshots: grasso al punto più vicino a 1s, 2s, 3s, 5s
    def snap(t_target):
        if not early:
            return 0.0
        return min(early, key=lambda p: abs(p[0] - t_target))[1]

    return {
        "avg_vel":     avg_vel,
        "n_inv":       inversioni,
        "accel":       accelerazione,
        "max_early":   max_early,
        "pnl_1s":      snap(1.0),
        "pnl_2s":      snap(2.0),
        "pnl_3s":      snap(3.0),
        "pnl_5s":      snap(5.0),
        "n_tick_early":len(early),
    }

# costruisce dataset
records = []
for peak, pnlf, cj in rows:
    try:
        pts  = json.loads(cj)
        meta = analizza_dinamica(pts, t_max=5.0)
        if meta is None:
            continue
        meta["peak"]    = float(peak or 0)
        meta["pnl_fin"] = float(pnlf)
        records.append(meta)
    except Exception:
        pass

SOGLIA = 0.5  # grasso = lordo 2.5
vivi  = [r for r in records if r["peak"] >= SOGLIA]
morti = [r for r in records if r["peak"] <  SOGLIA]

N_v, N_m = len(vivi), len(morti)
print("=" * 72)
print("SIMULA19 — DINAMICA DEI PRIMI TICK: VIVI vs MORTI")
print("=" * 72)
print(f"VIVI  (peak >= 0.5 grasso = lordo 2.5): {N_v}")
print(f"MORTI (peak <  0.5 grasso = lordo 2.5): {N_m}")
print()

def media(lst, key):
    vals = [r[key] for r in lst if r[key] is not None]
    return sum(vals) / len(vals) if vals else 0.0

def pct_pos(lst, key):
    vals = [r[key] for r in lst if r[key] is not None]
    if not vals: return 0.0
    return 100 * sum(1 for v in vals if v > 0) / len(vals)

print(f"  {'METRICA':<30}  {'VIVI':>10}  {'MORTI':>10}  {'DIFF':>10}  {'SEGNALE'}")
print(f"  {'-'*76}")

metriche = [
    ("Velocità media ($/s)",  "avg_vel",   False),
    ("Inversioni in 5s (#)",  "n_inv",     False),
    ("Accelerazione",         "accel",     False),
    ("Max grasso in 5s ($)",  "max_early", False),
    ("Grasso a 1s ($)",       "pnl_1s",    False),
    ("Grasso a 2s ($)",       "pnl_2s",    False),
    ("Grasso a 3s ($)",       "pnl_3s",    False),
    ("Grasso a 5s ($)",       "pnl_5s",    False),
]

risultati = {}
for nome, key, _ in metriche:
    mv = media(vivi,  key)
    mm = media(morti, key)
    diff = mv - mm
    # segnale: differenza >= 20% del range medio
    mag = abs(mv) + abs(mm)
    forte = abs(diff) > 0.1 * mag and mag > 0.01
    segnale = "  ◄ DIFF" if forte else ""
    print(f"  {nome:<30}  {mv:>+10.3f}  {mm:>+10.3f}  {diff:>+10.3f}{segnale}")
    risultati[key] = {"vivi": mv, "morti": mm, "diff": diff}

print()

# distribuzione inversioni
print("─" * 72)
print("DISTRIBUZIONE INVERSIONI IN 5s (0=dritto, tante=a strappi):")
print(f"  {'inversioni':>12}  {'VIVI%':>8}  {'MORTI%':>8}")
for n_inv in range(6):
    pv = 100 * sum(1 for r in vivi  if r["n_inv"] == n_inv) / N_v if N_v else 0
    pm = 100 * sum(1 for r in morti if r["n_inv"] == n_inv) / N_m if N_m else 0
    flag = "  ◄" if abs(pv-pm) >= 10 else ""
    print(f"  {n_inv:>12}  {pv:>7.0f}%  {pm:>7.0f}%{flag}")
print()

# distribuzione grasso a 1s e 3s
print("─" * 72)
print("DISTRIBUZIONE GRASSO A 1s (snapshot precocissimo):")
fasce_1s = [(-99,-0.5,"<-0.5$"),(-0.5,0.0,"-0.5→0$"),(0.0,0.3,"0→0.3$"),
            (0.3,0.7,"0.3→0.7$"),(0.7,99,">0.7$")]
print(f"  {'fascia':>12}  {'VIVI%':>8}  {'MORTI%':>8}")
for lo,hi,lab in fasce_1s:
    pv = 100 * sum(1 for r in vivi  if lo <= r["pnl_1s"] < hi) / N_v if N_v else 0
    pm = 100 * sum(1 for r in morti if lo <= r["pnl_1s"] < hi) / N_m if N_m else 0
    flag = "  ◄" if abs(pv-pm) >= 15 else ""
    print(f"  {lab:>12}  {pv:>7.0f}%  {pm:>7.0f}%{flag}")
print()

print("─" * 72)
print("DISTRIBUZIONE GRASSO A 3s:")
fasce_3s = [(-99,-0.5,"<-0.5$"),(-0.5,0.0,"-0.5→0$"),(0.0,0.5,"0→0.5$"),
            (0.5,1.0,"0.5→1$"),(1.0,99,">1$")]
print(f"  {'fascia':>12}  {'VIVI%':>8}  {'MORTI%':>8}")
for lo,hi,lab in fasce_3s:
    pv = 100 * sum(1 for r in vivi  if lo <= r["pnl_3s"] < hi) / N_v if N_v else 0
    pm = 100 * sum(1 for r in morti if lo <= r["pnl_3s"] < hi) / N_m if N_m else 0
    flag = "  ◄" if abs(pv-pm) >= 15 else ""
    print(f"  {lab:>12}  {pv:>7.0f}%  {pm:>7.0f}%{flag}")
print()

# ── VALUTAZIONE FILTRI SUI DYNAMICS ────────────────────────────────────────
print("=" * 72)
print("FILTRI DINAMICI — saldo netto se NON APRO quando condizione vera")
print("  (ricorda: il bot è GIÀ ENTRATO, quindi 'non aprire' = 'esci a 3s')")
print("=" * 72)
print()

def valuta_filtro_dinamico(nome, cond, records):
    """Simula: se condizione → esci a 3s (pnl_sim=pnl_3s), altrimenti tieni."""
    filtrati     = [r for r in records if cond(r)]
    non_filtrati = [r for r in records if not cond(r)]
    tot_reale = sum(r["pnl_fin"] for r in records)

    pnl_sim_tot = (
        sum(r["pnl_3s"]  for r in filtrati) +
        sum(r["pnl_fin"] for r in non_filtrati)
    )
    delta = pnl_sim_tot - tot_reale
    n_filtrati_vivi  = sum(1 for r in filtrati if r["peak"] >= SOGLIA)
    n_filtrati_morti = sum(1 for r in filtrati if r["peak"] <  SOGLIA)
    return {
        "nome": nome,
        "delta": delta,
        "tot_sim": pnl_sim_tot,
        "n_filt": len(filtrati),
        "pct_morti_filt": 100*n_filtrati_morti/N_m if N_m else 0,
        "pct_vivi_filt":  100*n_filtrati_vivi /N_v if N_v else 0,
    }

tot_reale_all = sum(r["pnl_fin"] for r in records)

filtri = [
    ("pnl_1s < -0.3",      lambda r: r["pnl_1s"] < -0.3),
    ("pnl_1s < -0.1",      lambda r: r["pnl_1s"] < -0.1),
    ("pnl_1s < 0.0",       lambda r: r["pnl_1s"] < 0.0),
    ("pnl_3s < -0.3",      lambda r: r["pnl_3s"] < -0.3),
    ("pnl_3s < 0.0",       lambda r: r["pnl_3s"] < 0.0),
    ("inversioni >= 2",    lambda r: r["n_inv"] >= 2),
    ("inversioni >= 3",    lambda r: r["n_inv"] >= 3),
    ("avg_vel < 0",        lambda r: r["avg_vel"] < 0),
    ("avg_vel < -0.1",     lambda r: r["avg_vel"] < -0.1),
    ("accel < -0.1",       lambda r: r["accel"] < -0.1),
    ("pnl_1s<0 AND inv>=2",lambda r: r["pnl_1s"] < 0 and r["n_inv"] >= 2),
    ("pnl_3s<0 AND inv>=2",lambda r: r["pnl_3s"] < 0 and r["n_inv"] >= 2),
]

print(f"  {'FILTRO':<28}  {'delta$':>7}  {'tot_sim':>8}  {'morti_filt%':>11}  {'vivi_filt%':>10}")
print(f"  {'-'*74}")

migliore = None
for nome, cond in filtri:
    f = valuta_filtro_dinamico(nome, cond, records)
    mk = ""
    if migliore is None or f["delta"] > migliore["delta"]:
        migliore = f
        mk = " ◄ BEST"
    print(f"  {f['nome']:<28}  {f['delta']:>+7.1f}  {f['tot_sim']:>+8.1f}  "
          f"{f['pct_morti_filt']:>10.0f}%  {f['pct_vivi_filt']:>9.0f}%{mk}")

print()

# ── verdetto ────────────────────────────────────────────────────────────────
print("=" * 72)
print("VERDETTO")
print("=" * 72)
print()
print(f"  Netto REALE (tutti i trade): ${tot_reale_all:+.1f}")
print()

# quale metrica separa di più?
diffs_sorted = sorted(metriche, key=lambda m: abs(risultati[m[1]]["diff"]), reverse=True)
print("  METRICHE PIÙ SEPARANTI (vivi vs morti):")
for nome, key, _ in diffs_sorted[:4]:
    r = risultati[key]
    print(f"    {nome:<28}: vivi={r['vivi']:+.3f}  morti={r['morti']:+.3f}  diff={r['diff']:+.3f}")
print()

if migliore and migliore["delta"] > 0:
    print(f"  ✓ Esiste segnale dinamico utile: '{migliore['nome']}'")
    print(f"    Applica a 3s: netto migliora di +${migliore['delta']:.1f}")
    print(f"    Elimina {migliore['pct_morti_filt']:.0f}% dei morti, "
          f"sacrifica {migliore['pct_vivi_filt']:.0f}% dei vivi")
else:
    print(f"  ✗ Nessun filtro dinamico migliora il netto in modo significativo.")
    print(f"    La dinamica dei primi 5s NON distingue vivi da morti.")
print()
