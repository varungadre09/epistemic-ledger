#!/usr/bin/env python3
import sys, os, glob, json, re
from collections import Counter
import pandas as pd
folder = sys.argv[1]
files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

def cand_of(s):
    s = str(s)
    m = FENCE.search(s)
    return (m.group(1) if m else s.strip()), bool(m)

fails = []
for i, v in enumerate(df["enhanced_completion"]):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        continue
    cand, had_fence = cand_of(v)
    try:
        json.loads(cand); continue
    except Exception as e:
        fails.append((i, str(e), cand, had_fence, str(v)))

print(f"total parse failures: {len(fails)}")
reasons = Counter(f[1].split(":")[0].split("(")[0].strip() for f in fails)
print("\nerror-message buckets:")
for k, c in reasons.most_common(12):
    print(f"  {c:5d}  {k}")

# truncation heuristics
closed_fence = sum(1 for f in fails if f[4].rstrip().endswith("```"))
ends_brace   = sum(1 for f in fails if f[2].rstrip().endswith("}"))
had_fence_n  = sum(1 for f in fails if f[3])
print(f"\nhad opening ```json fence: {had_fence_n}/{len(fails)}")
print(f"raw completion ends with closing ```: {closed_fence}/{len(fails)}  (missing => truncated)")
print(f"candidate ends with '}}': {ends_brace}/{len(fails)}")

# balance check
def balance(s):
    return s.count("{") - s.count("}"), s.count("[") - s.count("]")
bal = [balance(f[2]) for f in fails]
open_braces = sum(1 for b in bal if b[0] > 0)
print(f"candidates with unclosed braces (open>close): {open_braces}/{len(fails)}  (strong truncation signal)")

# length comparison
import numpy as np
flen = pd.Series([len(f[4]) for f in fails])
print(f"\nfailed completion raw length: min={flen.min()} med={int(flen.median())} max={flen.max()}")
print("\n== tail of 2 failed candidates ==")
for i,(idx,err,cand,_,_) in enumerate(fails[:2]):
    print(f"\n--- fail row {idx}  err={err} ---")
    print("...TAIL:", repr(cand[-300:]))
