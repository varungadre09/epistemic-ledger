#!/usr/bin/env python3
"""Check hidden_quality[node] vs realized true-rate for (source,node) within each ledger."""
import sys, os, glob, json, re
import numpy as np, pandas as pd
folder = sys.argv[1]
files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
def parse(s):
    if s is None or (isinstance(s,float) and pd.isna(s)): return None
    m = FENCE.search(str(s))
    try: return json.loads(m.group(1) if m else str(s).strip())
    except Exception: return None

rows=[]
for ri,v in enumerate(df["enhanced_completion"]):
    o=parse(v)
    if not isinstance(o,dict): continue
    for s in (o.get("sources") or []):
        if not isinstance(s,dict): continue
        h=s.get("handle"); hq=s.get("hidden_quality") or {}
        if not isinstance(hq,dict): continue
        # realized rate per node for this source: a claim counts to node if its path==node
        claims=s.get("claims") or []
        cl=[]
        for c in claims:
            if not isinstance(c,dict): continue
            gt=str(c.get("ground_truth")).strip().lower()
            tps=c.get("topic_paths"); tps=tps if isinstance(tps,list) else ([tps] if tps else [])
            for p in tps: cl.append((str(p),gt))
        cldf=pd.DataFrame(cl,columns=["path","g"])
        for node,tgt in hq.items():
            try: tgt=float(tgt)
            except Exception: continue
            # exact node + descendants
            sub=cldf[(cldf.path==node)|(cldf.path.str.startswith(node+">"))]
            res=sub[sub.g.isin(["true","false"])]
            if len(res)>=3:
                realized=(res.g=="true").mean()
                rows.append((ri,h,node,tgt,realized,len(res),abs(realized-tgt)))
R=pd.DataFrame(rows,columns=["row","handle","node","planted","realized","n","abs_err"])
print(f"(source,node) buckets with >=3 resolved claims and a planted hidden_quality: {len(R)}")
if len(R):
    print(f"mean abs_err={R.abs_err.mean():.3f}  median={R.abs_err.median():.3f}")
    for thr in (0.05,0.10,0.15):
        print(f"  within {thr:.2f}: {(R.abs_err<=thr).mean():.1%}")
    print("\nby n-bucket (thin buckets deviate more by construction):")
    R["nb"]=pd.cut(R.n,[2,5,10,20,50,10000],labels=["3-5","6-10","11-20","21-50","50+"])
    print(R.groupby("nb",observed=True).abs_err.agg(["count","mean","median"]).to_string())
    print("\nworst 8:")
    print(R.sort_values("abs_err",ascending=False).head(8).to_string(index=False))
