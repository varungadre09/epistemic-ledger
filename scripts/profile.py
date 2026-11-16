#!/usr/bin/env python3
"""Profile the track-record ledger parquet fixture. Read-only."""
import sys, os, glob, json
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

folder = sys.argv[1]
files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
print(f"== {len(files)} parquet files ==")

# Arrow schema from first file (dtypes without full load)
sch = pq.ParquetFile(files[0]).schema_arrow
print("\n== Arrow schema (file 0) ==")
for f in sch:
    print(f"  {f.name}: {f.type}")

# per-file row counts
counts = [pq.ParquetFile(f).metadata.num_rows for f in files]
print(f"\n== rows: total={sum(counts)}  per-file min/med/max="
      f"{min(counts)}/{int(np.median(counts))}/{max(counts)} ==")

df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
print(f"\n== loaded {len(df)} rows, {df.shape[1]} cols ==")
print("columns:", list(df.columns))

print("\n== null counts ==")
print(df.isna().sum().to_string())

print("\n== dtypes ==")
print(df.dtypes.to_string())

print("\n== sample rows (3) ==")
pd.set_option("display.max_colwidth", 120)
print(df.head(3).to_string())

# per-column value characterisation
def peek(col):
    if col not in df.columns:
        print(f"\n[{col}] MISSING"); return
    s = df[col]
    print(f"\n[{col}] dtype={s.dtype} nulls={s.isna().sum()} nunique(approx)=", end="")
    try:
        print(s.nunique())
    except TypeError:
        print("unhashable (list/dict)")
    nn = s.dropna()
    if len(nn):
        v = nn.iloc[0]
        print(f"   example type={type(v).__name__}  value={str(v)[:200]}")

for c in ["handle","topic_paths","ground_truth","edge_case","hidden_quality",
          "claim","text","timestamp","created_at","resolution","confidence"]:
    peek(c)

# ground_truth distribution if present
for c in df.columns:
    if "ground" in c.lower() or c.lower() in ("gt","label","resolution","verdict"):
        print(f"\n== value_counts: {c} ==")
        print(df[c].astype(str).str.strip().str.lower().value_counts(dropna=False).head(20).to_string())

# handle cardinality
for c in df.columns:
    if "handle" in c.lower() or "source" in c.lower() or "author" in c.lower():
        print(f"\n== {c}: {df[c].nunique()} unique; top 10 by rows ==")
        print(df[c].value_counts().head(10).to_string())

# topic_paths structure
if "topic_paths" in df.columns:
    def as_list(v):
        if v is None: return []
        if isinstance(v,(list,tuple,np.ndarray)): return list(v)
        if isinstance(v,str):
            s=v.strip()
            if s.startswith("["):
                try: return list(json.loads(s))
                except Exception: return [s]
            return [s]
        return [v]
    lens = df["topic_paths"].apply(lambda v: len(as_list(v)))
    print(f"\n== topic_paths per-row count: min={lens.min()} med={int(lens.median())} max={lens.max()} ==")
    allp = [p for v in df["topic_paths"] for p in as_list(v)]
    depths = [str(p).count(">") + 1 for p in allp]
    print(f"   total path instances={len(allp)}  unique paths={len(set(map(str,allp)))}")
    print(f"   depth (levels): min={min(depths)} med={int(np.median(depths))} max={max(depths)}")
    from collections import Counter
    roots = Counter(str(p).split(">")[0].strip() for p in allp)
    print(f"   roots ({len(roots)}):")
    for r,c in roots.most_common(15):
        print(f"      {r}: {c}")

# hidden_quality structure
if "hidden_quality" in df.columns:
    def as_dict(v):
        if isinstance(v,dict): return v
        if isinstance(v,str):
            try: return json.loads(v)
            except Exception: return {}
        return {}
    sample = df["hidden_quality"].dropna()
    print(f"\n== hidden_quality: {sample.notna().sum()} non-null ==")
    if len(sample):
        d = as_dict(sample.iloc[0])
        print(f"   example keys={list(d.keys())[:8]}")
        print(f"   example={json.dumps(d, default=str)[:300]}")
