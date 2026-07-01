#!/usr/bin/env python3
"""
SIMULA30 — COSA RENDE UN'ACCELERAZIONE FERTILE?

I 4 trade del 4 giugno con campo passato → +28.85$.
Gli altri giorni col campo passato → pareggio/negativo.

Cerco la QUARTA DIMENSIONE che distingue l'accelerazione fertile
da quella sterile. Testo metriche aggiuntive dalle kline:

  vol_abs      Voluome assoluto nelle 5 kline (BTC)
  vol_rel      Volume relativo alla media recente (30 kline)
  vol_spike    Ultima kline ha volume > 150% della media? (spike)
  bull_streak  Quante kline consecutive bullish FINALI (0-5)
  body_last    Forza dell'ultima kline: |close-open|/range
  accel_sharp  Accelerazione "ripida": accel / vol_range (normalizzata)
  mom_consist  Tutti e 4 i gap tra close consecutivi positivi?
  range_expand Ultima kline ha range > media precedente? (espansione)
  vol_accel    Volume cresce: vol ultima > vol penultima?

Per ogni metrica: media sui trade del giorno-forte vs altri.
Poi: 3 test (walk-forward + OOS + permutation) sul filtro più promettente
combinato col campo base.

Lancio: python3 simula30.py
"""
import sqlite3, json, os, datetime, time, random, math
import urllib.request, urllib.parse

DB         = "/var/data/trading_data.db"
CACHE_RAW  = "/tmp/campo_raw_cache.json"   # kline grezze per estrarre più metriche
CACHE_FILE = "/tmp/campo_cache.json"
SYM        = "BTCUSDC"
N_PERM     = 2000

# ── fetch con cache kline GREZZE ──────────────────────────────────────────────
def fetch_klines_raw(ts_epoch, n=6):
    ts_ms=int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    params=urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_ms,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=8) as r:
            return json.loads(r.read())
    except Exception: return None

def fetch_vol_baseline(ts_epoch, n=30):
    """Fetcha le 30 kline PRIMA di quella corrente per la baseline volume."""
    ts_ms=int(ts_epoch)*1000 if int(ts_epoch)<1e12 else int(ts_epoch)
    # endTime 6 min prima (per escludere le 5 kline di segnale)
    ts_pre=ts_ms - 6*60*1000
    params=urllib.parse.urlencode({"symbol":SYM,"interval":"1m","endTime":ts_pre,"limit":n})
    try:
        with urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?{params}",timeout=8) as r:
            kl=json.loads(r.read())
        if kl: return sum(float(k[5]) for k in kl)/len(kl)
    except Exception: pass
    return None

def estrai_metriche_ricche(klines, vol_baseline=None):
    """Estrae tutte le metriche, incluse quelle della quarta dimensione."""
    if not klines or len(klines)<5: return None
    opens =[float(k[1]) for k in klines]
    highs =[float(k[2]) for k in klines]
    lows  =[float(k[3]) for k in klines]
    closes=[float(k[4]) for k in klines]
    vols  =[float(k[5]) for k in klines]
    ranges=[h-l for h,l in zip(highs,lows)]
    bodies=[abs(c-o) for c,o in zip(closes,opens)]

    mom_now  = closes[-1]-closes[-2]
    mom_prev = closes[-3]-closes[-4]
    trend_5m = closes[-1]-closes[0]
    accel    = mom_now-mom_prev

    # ── metriche base ──────────────────────────────────────────────────────
    vol_abs = sum(vols)
    vol_avg = vol_abs / len(vols)

    # volume relativo (se abbiamo baseline)
    vol_rel = vol_avg / vol_baseline if vol_baseline and vol_baseline > 0 else None

    # spike volume ultima kline
    mean_prev = sum(vols[:-1])/(len(vols)-1) if len(vols)>1 else vols[-1]
    vol_spike = vols[-1]/mean_prev if mean_prev > 0 else 1.0

    # bull streak: quante kline consecutive FINALI chiudono in verde
    bull_streak = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > opens[i]: bull_streak += 1
        else: break

    # forza ultima kline
    body_last = bodies[-1]/ranges[-1] if ranges[-1] > 0 else 0.0

    # consistenza momentum: tutti i gap close-close positivi?
    gaps = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    mom_consist = all(g > 0 for g in gaps)
    n_gaps_pos  = sum(1 for g in gaps if g > 0)

    # range expansion: ultima kline ha range > media precedenti
    mean_range_prev = sum(ranges[:-1])/(len(ranges)-1) if len(ranges)>1 else ranges[-1]
    range_expand = ranges[-1] > mean_range_prev
    range_ratio  = ranges[-1]/mean_range_prev if mean_range_prev > 0 else 1.0

    # volume accelera: ultima > penultima
    vol_accel = vols[-1] > vols[-2] if len(vols)>=2 else False

    # accel normalizzata per il range (quanto è "ripida" rispetto al movimento)
    vol_range_medio = sum(ranges)/len(ranges)
    accel_sharp = accel/vol_range_medio if vol_range_medio > 0 else 0.0

    # curvatura dell'accelerazione (terza derivata del prezzo)
    if len(closes)>=5:
        accels=[closes[i]-2*closes[i-1]+closes[i-2] for i in range(2,len(closes))]
        accel_curve = accels[-1]-accels[-2] if len(accels)>=2 else 0.0
    else:
        accel_curve = 0.0

    return {
        # base
        "trend_5m":   trend_5m,
        "mom_last":   mom_now,
        "trend_accel":accel,
        "bull_count": sum(1 for c,o in zip(closes,opens) if c>o),
        # quarta dimensione
        "vol_abs":    vol_abs,
        "vol_rel":    vol_rel,
        "vol_spike":  vol_spike,
        "bull_streak":bull_streak,
        "body_last":  body_last,
        "mom_consist":1 if mom_consist else 0,
        "n_gaps_pos": n_gaps_pos,
        "range_expand":1 if range_expand else 0,
        "range_ratio": range_ratio,
        "vol_accel":  1 if vol_accel else 0,
        "accel_sharp":accel_sharp,
        "accel_curve":accel_curve,
    }

# ── carica DB ─────────────────────────────────────────────────────────────────
con  = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(curva_nascita)").fetchall()]
ts_col = next((c for c in ["trade_ts","ts","timestamp","created_at","open_time"]
               if c in cols), None)
rows = con.execute(
    f"SELECT {ts_col}, peak_nascita, pnl_finale, curva_json "
    f"FROM curva_nascita WHERE pnl_finale IS NOT NULL ORDER BY {ts_col} ASC"
).fetchall()
con.close()
print(f"Trade totali: {len(rows)}")

# ── cache raw ─────────────────────────────────────────────────────────────────
raw_cache={}
if os.path.exists(CACHE_RAW):
    with open(CACHE_RAW) as f:
        for item in json.load(f):
            raw_cache[str(item["ts"])]=item
    print(f"Cache raw: {len(raw_cache)} voci")

def ts_dt(ts):
    s=int(ts) if int(ts)<1e12 else int(ts)//1000
    return datetime.datetime.utcfromtimestamp(s)

records=[]; to_save=[]
print("Caricamento metriche (può richiedere fetch aggiuntivi)...")
for i,(ts,peak,pnlf,cj) in enumerate(rows):
    ts_str=str(ts)
    if ts_str in raw_cache:
        cached=raw_cache[ts_str]
        m=cached.get("metrics"); vb=cached.get("vol_baseline")
    else:
        if i%50==0: print(f"  fetch {i}/{len(rows)}...")
        kl=fetch_klines_raw(ts,n=6)
        vb=fetch_vol_baseline(ts,n=30)
        m=estrai_metriche_ricche(kl,vb) if kl else None
        raw_cache[ts_str]={"ts":ts,"metrics":m,"vol_baseline":vb}
        to_save.append({"ts":ts,"metrics":m,"vol_baseline":vb})
        time.sleep(0.12)
    if m is None: continue
    rec=dict(m); rec.update({"ts":ts,"peak":float(peak or 0),"pnl_fin":float(pnlf)})
    records.append(rec)

if to_save:
    existing=[]
    if os.path.exists(CACHE_RAW):
        with open(CACHE_RAW) as f: existing=json.load(f)
    with open(CACHE_RAW,"w") as f: json.dump(existing+to_save,f)
    print(f"Cache raw salvata: {len(raw_cache)} voci")

def filtro_base(r):
    return r["trend_accel"]>20 and r["mom_last"]>0 and r["trend_5m"]>200

filtrati=[r for r in records if filtro_base(r)]
N_f=len(filtrati)
print(f"\nRecord totali: {len(records)}  |  Filtrati (campo base): {N_f}")
print()

# ── split FERTILE vs STERILE ───────────────────────────────────────────────────
# identifico il giorno con netto massimo (4 giugno o equivalente)
from collections import defaultdict
giorni_netto=defaultdict(float)
giorni_n    =defaultdict(int)
for r in filtrati:
    d=ts_dt(r["ts"]).strftime("%Y-%m-%d")
    giorni_netto[d]+=r["pnl_fin"]
    giorni_n[d]+=1

giorno_top=max(giorni_netto,key=giorni_netto.get)
print(f"Giorno TOP: {giorno_top}  netto=${giorni_netto[giorno_top]:+.2f}  N={giorni_n[giorno_top]}")
print()

fertili =[r for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")==giorno_top]
sterili =[r for r in filtrati if ts_dt(r["ts"]).strftime("%Y-%m-%d")!=giorno_top]
N_fer,N_ste=len(fertili),len(sterili)

# ── CONFRONTO METRICHE FERTILE vs STERILE ────────────────────────────────────
print("="*76)
print(f"CONFRONTO: {giorno_top} (FERTILE, +{giorni_netto[giorno_top]:.1f}$) vs ALTRI GIORNI")
print("="*76)
print()
print(f"  {'METRICA':<28}  {'FERTILE':>10}  {'STERILE':>10}  {'DIFF':>8}  {'DIFF%':>7}")
print(f"  {'-'*68}")

def avg(lst,key):
    v=[r[key] for r in lst if r.get(key) is not None]
    return sum(v)/len(v) if v else 0.0

metriche_4d=[
    ("trend_accel ($)",     "trend_accel"),
    ("mom_last ($)",        "mom_last"),
    ("trend_5m ($)",        "trend_5m"),
    ("vol_abs (BTC)",       "vol_abs"),
    ("vol_rel (x media)",   "vol_rel"),
    ("vol_spike (x prev)",  "vol_spike"),
    ("bull_streak (0-5)",   "bull_streak"),
    ("body_last (0-1)",     "body_last"),
    ("mom_consist (0/1)",   "mom_consist"),
    ("n_gaps_pos (0-5)",    "n_gaps_pos"),
    ("range_expand (0/1)",  "range_expand"),
    ("range_ratio (x prev)","range_ratio"),
    ("vol_accel (0/1)",     "vol_accel"),
    ("accel_sharp",         "accel_sharp"),
    ("accel_curve",         "accel_curve"),
]

separatori=[]
for nome,key in metriche_4d:
    vf=avg(fertili,key); vs=avg(sterili,key)
    diff=vf-vs
    mag=abs(vf)+abs(vs)
    diff_pct=100*abs(diff)/mag if mag>0.001 else 0
    flag="  ◄◄◄ FORTE" if diff_pct>50 else ("  ◄◄ MEDIO" if diff_pct>25 else ("  ◄" if diff_pct>10 else ""))
    print(f"  {nome:<28}  {vf:>+10.4f}  {vs:>+10.4f}  {diff:>+8.4f}  {diff_pct:>6.0f}%{flag}")
    if diff_pct>15 and mag>0.001:
        separatori.append((diff_pct,nome,key,vf,vs,diff))

separatori.sort(reverse=True)
print()
print(f"  Metriche più separanti (>15% diff relativa):")
for dpct,nome,key,vf,vs,diff in separatori[:5]:
    print(f"    {nome}: fertile={vf:+.4f}  sterile={vs:+.4f}  diff={diff:+.4f} ({dpct:.0f}%)")
print()

# ── 3 TEST SU OGNI METRICA AGGIUNTIVA ────────────────────────────────────────
print("="*76)
print("3 TEST SU FILTRI CAMPO ARRICCHITI (campo_base + 4a dimensione)")
print(f"  p-value corretto (Bonferroni): 0.05/{len(separatori)} = {0.05/max(len(separatori),1):.4f}")
print("="*76)
print()

random.seed(42)

def netto_filt(subset,cond):
    return sum(r["pnl_fin"] for r in subset if cond(r))

def permutation_p(recs,cond,n_perm=N_PERM):
    pnls=[r["pnl_fin"] for r in recs]; flags=[cond(r) for r in recs]
    obs=sum(p for p,f in zip(pnls,flags) if f); count=0
    for _ in range(n_perm):
        random.shuffle(flags)
        if sum(p for p,f in zip(pnls,flags) if f)>=obs: count+=1
    return obs,count/n_perm

mid=len(records)//2; train=records[:mid]; test=records[mid:]
bs=len(records)//3
blks=[records[:bs],records[bs:2*bs],records[2*bs:]]

# costruisce filtri candidati per ogni metrica separante
candidati_4d=[]
for _,nome,key,vf,vs,diff in separatori[:6]:
    if diff>0:
        # fertile > sterile: soglia a metà tra i due valori
        soglia=(vf+vs)/2
        label=f"base + {key}>{soglia:.3f}"
        s=soglia
        candidati_4d.append((label,
            lambda r,k=key,s=s: filtro_base(r) and r.get(k,0) is not None and r.get(k,0)>s))
        # soglia alternativa: valore fertile - 20%
        soglia2=vf*0.8
        label2=f"base + {key}>{soglia2:.3f}"
        candidati_4d.append((label2,
            lambda r,k=key,s2=soglia2: filtro_base(r) and r.get(k,0) is not None and r.get(k,0)>s2))
    else:
        soglia=(vf+vs)/2
        label=f"base + {key}<{soglia:.3f}"
        s=soglia
        candidati_4d.append((label,
            lambda r,k=key,s=s: filtro_base(r) and r.get(k,0) is not None and r.get(k,0)<s))

# aggiunge combinazioni specifiche basate sui top separatori
top_keys=[x[2] for x in separatori[:3]]
if len(top_keys)>=2:
    k1,k2=top_keys[0],top_keys[1]
    vf1=avg(fertili,k1); vf2=avg(fertili,k2)
    vs1=avg(sterili,k1); vs2=avg(sterili,k2)
    s1=(vf1+vs1)/2; s2=(vf2+vs2)/2
    d1=separatori[0][5]; d2=separatori[1][5]
    op1=">" if d1>0 else "<"; op2=">" if d2>0 else "<"
    label=f"base + {k1}{op1}{s1:.2f} + {k2}{op2}{s2:.2f}"
    candidati_4d.append((label,
        lambda r,k1=k1,k2=k2,s1=s1,s2=s2,d1=d1,d2=d2:
            filtro_base(r) and
            r.get(k1) is not None and r.get(k2) is not None and
            (r[k1]>s1 if d1>0 else r[k1]<s1) and
            (r[k2]>s2 if d2>0 else r[k2]<s2)))

BONF=0.05/max(len(candidati_4d),1)
print(f"  {'FILTRO ARRICCHITO':<44}  {'N':>4}  {'netto':>7}  {'WF(1,2,3)':>16}  {'OOS':>7}  {'p':>6}  ESITO")
print(f"  {'-'*100}")

migliori=[]
for label,cond in candidati_4d:
    n_f=sum(1 for r in records if cond(r))
    if n_f<10:
        print(f"  {label:<44}  {n_f:>4}  {'(pochi)':>40}")
        continue
    obs,p_val=permutation_p(records,cond)
    oos=netto_filt(test,cond)
    wf_n=[netto_filt(b,cond) for b in blks]
    wf_ok=all(x>=-20 for x in wf_n)
    wf_str=f"({wf_n[0]:+.0f},{wf_n[1]:+.0f},{wf_n[2]:+.0f})"
    all_ok=obs>0 and wf_ok and oos>=-30 and p_val<=0.05
    esito="✓✓ PASSA!" if all_ok else ("◐ 2/3" if sum([obs>0,wf_ok,oos>=-30,p_val<=0.05])>=2 else "✗")
    bon="✓BON" if p_val<=BONF else ""
    print(f"  {label:<44}  {n_f:>4}  {obs:>+7.1f}  {wf_str:>16}  {oos:>+7.1f}  {p_val:>6.3f}  {esito} {bon}")
    if all_ok: migliori.append({"label":label,"n":n_f,"netto":obs,"oos":oos,"p":p_val})

print()

# ── VERDETTO ──────────────────────────────────────────────────────────────────
print("="*76)
print("VERDETTO — ESISTE LA QUARTA DIMENSIONE?")
print("="*76)
print()
print(f"  Giorno fertile ({giorno_top}):")
for _,nome,key,vf,vs,diff in separatori[:4]:
    print(f"    {nome}: {vf:+.4f}  (altri giorni: {vs:+.4f}  diff={diff:+.4f})")
print()

if migliori:
    migliori.sort(key=lambda x:x["netto"],reverse=True)
    print(f"  ✓ Trovata 4a dimensione con netto positivo e 3 test superati:")
    for m in migliori:
        print(f"    '{m['label']}'")
        print(f"    N={m['n']}  netto=${m['netto']:+.1f}  OOS=${m['oos']:+.1f}  p={m['p']:.3f}")
    print()
    print(f"  PROSSIMO PASSO: aggiungere questa condizione al gate CAMPO_ESTERNO nel bot.")
    print(f"  ENV da aggiungere in Render per testare la 4a dimensione in paper.")
else:
    print(f"  ~ Nessuna 4a dimensione supera tutti e 3 i test.")
    print(f"  I separatori trovati (se esistono) non reggono statisticamente.")
    print()
    print(f"  INTERPRETAZIONE:")
    print(f"  Il 4 giugno era probabilmente un giorno di regime specifico di mercato")
    print(f"  (forte trend BTC) che non si cattura con una singola metrica aggiuntiva.")
    print(f"  Le dimensioni separanti nelle kline pre-entrata potrebbero essere:")
    print(f"    1. Già nei 3 filtri base (il giorno era solo molto al di sopra)")
    print(f"    2. In dati che non ho: OI, funding rate, regime macro del giorno")
    print()
    print(f"  CONCLUSIONE: il campo a 3 dimensioni cattura i giorni forti quando arrivano.")
    print(f"  Il pareggio negli altri giorni è il costo di stare fuori quando non fertile.")
    print(f"  Il paper trading chiarirà se i giorni forti sono abbastanza frequenti.")
print()
