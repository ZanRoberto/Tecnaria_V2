#!/usr/bin/env python3
"""
SIMULA27 — QUALE CONFIG È LA PIÙ AFFIDABILE? + STABILITÀ TEMPORALE

Due domande:
1. ROBUSTEZZA: tra le config positive che reggono i 3 test, quale ha il miglior
   equilibrio netto/N_trade? Score = netto_tot / sqrt(N)  (penalizza sia netto
   basso sia N piccolo). Più è alto, più il segnale è "denso e stabile".

2. STABILITÀ TEMPORALE: il segnale positivo regge negli ULTIMI 2 MESI
   separatamente? Se funziona solo nello storico antico ma non di recente,
   NON è un segnale attuale.

OUTPUT FINALE: la config raccomandata per il paper trading + soglia minima
di trade da raccogliere prima di valutare il go-live.

Lancio: python3 simula27.py
"""
import sqlite3, json, os, random, time, math, urllib.request, urllib.parse

DB         = "/var/data/trading_data.db"
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"

# ── carica DB ─────────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
ts_col = next((c for c in ["trade_ts","ts","timestamp","created_at","open_time"]
               if c in cols), None)
if ts_col is None:
    for c in cols:
        s = con.execute(f"SELECT {c} FROM curva_nascita LIMIT 1").fetchone()
        if s and s[0] and str(s[0]).isdigit() and len(str(int(s[0]))) >= 10:
            ts_col = c; break

rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale, curva_json "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL ORDER BY {ts_col} ASC"
).fetchall()
con.close()
print(f"Trade totali: {len(rows)}")

# ── cache / fetch ─────────────────────────────────────────────────────────────
def fetch_klines(ts_epoch, n=6):
    ts_ms = int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params = urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}", timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

def klines_to_metrics(klines):
    if not klines or len(klines)<4: return None
    closes=[float(k[4]) for k in klines]; opens=[float(k[1]) for k in klines]
    highs=[float(k[2]) for k in klines];  lows=[float(k[3]) for k in klines]
    vols=[float(k[5]) for k in klines]
    ranges=[h-l for h,l in zip(highs,lows)]; bodies=[abs(c-o) for c,o in zip(closes,opens)]
    mom_now=closes[-1]-closes[-2]; mom_prev=closes[-3]-closes[-4] if len(closes)>=5 else 0
    return {"trend_5m":closes[-1]-closes[0],"mom_last":mom_now,
            "vol_range":sum(ranges)/len(ranges),
            "vol_trend":vols[-1]/vols[0] if vols[0]>0 else 1.0,
            "body_ratio":sum(b/r if r>0 else 0 for b,r in zip(bodies,ranges))/len(bodies),
            "bull_count":sum(1 for c,o in zip(closes,opens) if c>o),
            "trend_accel":mom_now-mom_prev}

cache_map={}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE) as f:
        for item in json.load(f): cache_map[str(item["ts"])]=item["metrics"]
    print(f"Cache: {len(cache_map)} voci")

records=[]; to_save=[]
for ts,peak,pnlf,cj in rows:
    ts_str=str(ts)
    if ts_str in cache_map: m=cache_map[ts_str]
    else:
        kl=fetch_klines(ts); m=klines_to_metrics(kl) if kl else None
        cache_map[ts_str]=m; to_save.append({"ts":ts,"metrics":m}); time.sleep(0.05)
    if m is None: continue
    g1s=None
    try:
        pts=json.loads(cj); micro=[(float(p[0]),float(p[1])) for p in pts if len(p)>=2 and float(p[0])<=2.0]
        if len(micro)>=2: g1s=min(micro,key=lambda p:abs(p[0]-1.0))[1]
    except Exception: pass
    rec=dict(m); rec.update({"ts":ts,"peak":float(peak or 0),"pnl_fin":float(pnlf),"g1s":g1s})
    records.append(rec)

if to_save:
    existing=[]
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f: existing=json.load(f)
    with open(CACHE_FILE,"w") as f: json.dump(existing+to_save,f)

N=len(records); tot_real=sum(r["pnl_fin"] for r in records)
print(f"Record con campo: {N}  |  Netto reale: ${tot_real:+.1f}")

# date range
ts_min=records[0]["ts"]; ts_max=records[-1]["ts"]
ts_min_s=int(ts_min) if int(ts_min)<1e12 else int(ts_min)//1000
ts_max_s=int(ts_max) if int(ts_max)<1e12 else int(ts_max)//1000
import datetime
dt_min=datetime.datetime.utcfromtimestamp(ts_min_s)
dt_max=datetime.datetime.utcfromtimestamp(ts_max_s)
print(f"Range temporale: {dt_min.strftime('%Y-%m-%d')} → {dt_max.strftime('%Y-%m-%d')}")
print()

# ── definizione filtri (stessa griglia di simula26) ───────────────────────────
random.seed(42)
N_PERM=2000

filtri_tutti=[]
filtri_tutti.append(("accel>0 AND mom>0 AND t5m>100",
    lambda r: r["trend_accel"]>0 and r["mom_last"]>0 and r["trend_5m"]>100))
for t5m in [150,200,300]:
    t=t5m
    filtri_tutti.append((f"accel>0 AND mom>0 AND t5m>{t5m}",
        lambda r,t=t: r["trend_accel"]>0 and r["mom_last"]>0 and r["trend_5m"]>t))
for ac in [10,20,30]:
    a=ac
    filtri_tutti.append((f"accel>{ac} AND mom>0 AND t5m>100",
        lambda r,a=a: r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>100))
for ac in [10,20]:
    for t5m in [150,200]:
        a,t=ac,t5m
        filtri_tutti.append((f"accel>{ac} AND mom>0 AND t5m>{t5m}",
            lambda r,a=a,t=t: r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>t))
for ac in [0,10]:
    for bc in [3,4]:
        a,b=ac,bc
        filtri_tutti.append((f"accel>{ac} AND mom>0 AND t5m>100 AND bull>={bc}",
            lambda r,a=a,b=b: r["trend_accel"]>a and r["mom_last"]>0 and r["trend_5m"]>100 and r["bull_count"]>=b))

def netto_filt(subset,cond): return sum(r["pnl_fin"] for r in subset if cond(r))
def permutation_p(recs,cond,n_perm=N_PERM):
    pnls=[r["pnl_fin"] for r in recs]; flags=[cond(r) for r in recs]
    obs=sum(p for p,f in zip(pnls,flags) if f); count=0
    for _ in range(n_perm):
        random.shuffle(flags)
        if sum(p for p,f in zip(pnls,flags) if f)>=obs: count+=1
    return obs,count/n_perm

# ── PARTE 1: SCORING ROBUSTEZZA ───────────────────────────────────────────────
print("="*82)
print("PARTE 1 — SCORING ROBUSTEZZA: netto vs numero trade (score = netto/√N)")
print("  Solo le config con netto>0 e p<0.05 compaiono.")
print("="*82)
print()
print(f"  {'CONFIG':<42}  {'N':>4}  {'netto':>7}  {'avg/tr':>7}  {'OOS':>7}  {'p':>6}  {'SCORE':>7}  RANK")
print(f"  {'-'*90}")

mid=N//2; train=records[:mid]; test=records[mid:]

risultati=[]
for label,cond in filtri_tutti:
    n_filt=sum(1 for r in records if cond(r))
    if n_filt<10: continue
    obs,p_val=permutation_p(records,cond)
    if obs<=0 or p_val>0.05: continue
    # OOS
    oos=netto_filt(test,cond)
    # walk-forward
    bs=N//3
    blks=[records[:bs],records[bs:2*bs],records[2*bs:]]
    wf=[netto_filt(b,cond) for b in blks]
    wf_ok=all(x>=-20 for x in wf)
    if not wf_ok: continue
    # score
    score=obs/math.sqrt(n_filt)
    avg_tr=obs/n_filt
    risultati.append({"label":label,"n":n_filt,"netto":obs,"avg_tr":avg_tr,
                      "oos":oos,"p_val":p_val,"score":score,"wf":wf,"wf_ok":wf_ok})

risultati.sort(key=lambda x:x["score"],reverse=True)
for i,r in enumerate(risultati):
    rank=f"#{i+1}"
    bon="✓BON" if r["p_val"]<=0.05/len(filtri_tutti) else ""
    print(f"  {r['label']:<42}  {r['n']:>4}  {r['netto']:>+7.1f}  {r['avg_tr']:>+7.2f}  "
          f"{r['oos']:>+7.1f}  {r['p_val']:>6.3f}  {r['score']:>7.2f}  {rank} {bon}")

print()
if risultati:
    best=risultati[0]
    print(f"  RACCOMANDATA (score più alto): '{best['label']}'")
    print(f"    N={best['n']}  netto=${best['netto']:+.1f}  avg=${best['avg_tr']:+.2f}/trade")
    print(f"    OOS: ${best['oos']:+.1f}  p={best['p_val']:.3f}")
    print(f"    Walk-forward blocchi: ({best['wf'][0]:+.1f}, {best['wf'][1]:+.1f}, {best['wf'][2]:+.1f})")
    BEST_COND=next(c for l,c in filtri_tutti if l==best["label"])
    BEST_LABEL=best["label"]
else:
    print("  Nessuna config supera tutti i criteri.")
    BEST_COND=filtri_tutti[0][1]; BEST_LABEL=filtri_tutti[0][0]
print()

# ── PARTE 2: STABILITÀ TEMPORALE ─────────────────────────────────────────────
print("="*82)
print("PARTE 2 — STABILITÀ TEMPORALE: il segnale regge negli ULTIMI 2 MESI?")
print("="*82)
print()

# calcola cutoff: ultimi 60 giorni dai dati
SECONDS_60D = 60*24*3600
ts_max_s_val = int(ts_max) if int(ts_max)<1e12 else int(ts_max)//1000
cutoff_60d   = ts_max_s_val - SECONDS_60D
cutoff_30d   = ts_max_s_val - 30*24*3600

def ts_seconds(r):
    t=r["ts"]; return int(t) if int(t)<1e12 else int(t)//1000

old_60d    = [r for r in records if ts_seconds(r) <  cutoff_60d]
recent_60d = [r for r in records if ts_seconds(r) >= cutoff_60d]
recent_30d = [r for r in records if ts_seconds(r) >= cutoff_30d]

print(f"  Storico 'vecchio' (prima degli ultimi 60gg): {len(old_60d)} trade")
print(f"  Ultimi 60 giorni:                            {len(recent_60d)} trade")
print(f"  Ultimi 30 giorni:                            {len(recent_30d)} trade")
print()

# per le top 3 config, analizza per periodo
top3 = risultati[:min(3,len(risultati))]
if not top3:
    top3 = [{"label": BEST_LABEL}]

print(f"  {'CONFIG':<42}  {'VECCHIO':>10}  {'ULT60gg':>10}  {'ULT30gg':>10}  TREND")
print(f"  {'-'*82}")

for r in top3:
    lbl=r["label"]
    cond=next(c for l,c in filtri_tutti if l==lbl)
    n_old=netto_filt(old_60d,cond)   if old_60d    else 0
    n_60 =netto_filt(recent_60d,cond) if recent_60d else 0
    n_30 =netto_filt(recent_30d,cond) if recent_30d else 0
    cnt_old=sum(1 for x in old_60d    if cond(x))
    cnt_60 =sum(1 for x in recent_60d if cond(x))
    cnt_30 =sum(1 for x in recent_30d if cond(x))
    # normalizza per numero di trade (avg)
    avg_old=n_old/cnt_old if cnt_old else 0
    avg_60 =n_60 /cnt_60  if cnt_60  else 0
    avg_30 =n_30 /cnt_30  if cnt_30  else 0
    trend="✓ STABILE" if avg_60>=0 and avg_30>=-0.5 else ("~ DEGRADA" if avg_60>=-0.5 else "✗ INVERTITO")
    print(f"  {lbl:<42}  {n_old:>+10.1f}  {n_60:>+10.1f}  {n_30:>+10.1f}  {trend}")
    print(f"  {'(avg/trade)':42}  {avg_old:>+10.2f}  {avg_60:>+10.2f}  {avg_30:>+10.2f}")
    print(f"  {'(N filtrati)':42}  {cnt_old:>10}  {cnt_60:>10}  {cnt_30:>10}")
    print()

# ── PIANO PAPER TRADING ───────────────────────────────────────────────────────
print("="*82)
print("PIANO PAPER TRADING — prima dei soldi veri")
print("="*82)
print()

if risultati:
    best=risultati[0]
    # frequenza: quanti trade al mese passano il filtro?
    n_filtrati_tot=best["n"]
    durata_giorni=max((ts_max_s_val-int(ts_min_s if int(ts_min_s)<1e12 else int(ts_min_s)//1000))/86400,1)
    tasso_al_giorno=n_filtrati_tot/durata_giorni
    tasso_al_mese=tasso_al_giorno*30

    # per avere un intervallo di confidenza decente serve N>=50
    mesi_necessari=math.ceil(50/tasso_al_mese) if tasso_al_mese>0 else 99

    print(f"  Config raccomandata: '{best['label']}'")
    print(f"  Trade filtrati in storico: {best['n']} su {N} ({100*best['n']/N:.0f}%)")
    print(f"  Tasso stimato: ~{tasso_al_mese:.1f} trade/mese col filtro attivo")
    print()
    print(f"  OBIETTIVO PAPER TRADING:")
    print(f"    Raccogli almeno 50 trade filtrati → ~{mesi_necessari} mesi")
    print(f"    Criteri GO/NO-GO dopo il paper:")
    print(f"      ✓ GO  se netto paper > 0 E avg/trade > +0.50$ E N >= 50")
    print(f"      ✗ STOP se netto paper < -${best['n']*0.3:.0f} (perdita > 30% del guadagno storico)")
    print()
    print(f"  IMPLEMENTAZIONE (cosa cambia nel bot):")
    print(f"    PRIMA di ogni entrata, controlla le ultime 5 kline al minuto.")
    print(f"    Entra SOLO SE:")
    print(f"      trend_accel = (mom_ultimo_min - mom_penultimo_min) > X")
    print(f"      mom_last    = (close[-1] - close[-2])              > 0")
    print(f"      trend_5m    = (close[-1] - close[-5])              > Y$")
    print(f"    Dove X e Y sono le soglie della config raccomandata.")
    print()
    print(f"  RISCHIO RESIDUO:")
    print(f"    Con N={best['n']} trade storici, l'intervallo di confidenza al 95% del")
    print(f"    netto medio è circa ±{1.96*abs(best['avg_tr'])*math.sqrt(best['n'])/(best['n']):.2f}$/trade.")
    print(f"    Il paper trading di 50 trade ridurrà questa incertezza.")
else:
    print("  Nessuna config supera tutti i criteri: continua la raccolta dati.")
    print("  Torna a simula26 quando hai +200 trade in curva_nascita.")
print()
