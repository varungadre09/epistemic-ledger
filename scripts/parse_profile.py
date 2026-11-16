#!/usr/bin/env python3
"""Parse enhanced_completion JSON ledgers and profile the real content + quality."""
import sys, os, glob, json, re
from collections import Counter
import numpy as np
import pandas as pd

folder = sys.argv[1]
files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
print(f"rows={len(df)}  cols={list(df.columns)}")

FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

def extract_json(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None, "null"
    s = str(s)
    m = FENCE.search(s)
    cand = m.group(1) if m else s.strip()
    try:
        return json.loads(cand), "ok"
    except Exception as e:
        # try to grab first {...} balanced-ish
        try:
            start = cand.index("{");
            return json.loads(cand[start:]), "ok_trim"
        except Exception:
            return None, f"parse_fail:{type(e).__name__}"

parsed, status = [], Counter()
for v in df["enhanced_completion"]:
    obj, st = extract_json(v)
    status[st.split(":")[0]] += 1
    parsed.append(obj)

print("\n== completion JSON parse status ==")
for k, c in status.most_common():
    print(f"  {k}: {c}  ({c/len(df):.1%})")

# flatten claims
claims = []
src_rows = []
top_keys = Counter()
claim_keys = Counter()
n_no_sources = 0
for ri, obj in enumerate(parsed):
    if not isinstance(obj, dict):
        continue
    top_keys.update(obj.keys())
    srcs = obj.get("sources")
    if not isinstance(srcs, list):
        n_no_sources += 1
        continue
    for s in srcs:
        if not isinstance(s, dict):
            continue
        h = s.get("handle")
        hq = s.get("hidden_quality", {}) or {}
        cl = s.get("claims", []) or []
        src_rows.append(dict(row=ri, handle=h, n_claims=len(cl),
                             n_hq=len(hq) if isinstance(hq, dict) else 0,
                             persona=s.get("persona")))
        for c in (cl if isinstance(cl, list) else []):
            if not isinstance(c, dict):
                continue
            claim_keys.update(c.keys())
            tp = c.get("topic_paths")
            tp = tp if isinstance(tp, list) else ([tp] if tp else [])
            claims.append(dict(
                row=ri, handle=h,
                topic_paths=tp,
                primary_path=(tp[0] if tp else None),
                ground_truth=str(c.get("ground_truth")).strip().lower(),
                stated_confidence=c.get("stated_confidence"),
                is_falsifiable=c.get("is_falsifiable"),
                edge_case=c.get("edge_case"),
                resolves_on=c.get("resolves_on"),
                posted_at=c.get("posted_at"),
            ))

C = pd.DataFrame(claims)
S = pd.DataFrame(src_rows)
print(f"\n== top-level keys across ledgers ==\n  {dict(top_keys)}")
print(f"\n== claim keys ==\n  {dict(claim_keys)}")
print(f"\nledgers w/o 'sources' list: {n_no_sources}")
print(f"\n== totals: {len(S)} source-instances, {len(C)} claims ==")

print(f"\n== claims per ledger-row: min={S.groupby('row').n_claims.sum().min()} "
      f"med={int(S.groupby('row').n_claims.sum().median())} "
      f"max={S.groupby('row').n_claims.sum().max()} ==")

print("\n== handles (unique={}): top 20 by claims ==".format(C.handle.nunique()))
print(C.handle.value_counts().head(20).to_string())

print("\n== ground_truth distribution ==")
print(C.ground_truth.value_counts(dropna=False).to_string())

print("\n== edge_case distribution ==")
print(C.edge_case.astype(str).value_counts(dropna=False).head(20).to_string())

print("\n== is_falsifiable ==")
print(C.is_falsifiable.astype(str).value_counts(dropna=False).to_string())

# topic hierarchy
allp = [p for lst in C.topic_paths for p in (lst or [])]
depths = [str(p).count(">") + 1 for p in allp]
roots = Counter(str(p).split(">")[0].strip() for p in allp)
print(f"\n== topic paths: {len(allp)} instances, {len(set(map(str,allp)))} unique, "
      f"depth min/med/max={min(depths)}/{int(np.median(depths))}/{max(depths)} ==")
print("  roots:", dict(roots.most_common()))
print("  example unique paths:")
for p in sorted(set(map(str, allp)))[:25]:
    print("   ", p)

# stated_confidence
sc = pd.to_numeric(C.stated_confidence, errors="coerce")
print(f"\n== stated_confidence: nonnull={sc.notna().sum()} "
      f"min={sc.min():.2f} med={sc.median():.2f} max={sc.max():.2f} ==")

# hidden_quality presence
print(f"\n== sources with hidden_quality: {(S.n_hq>0).sum()}/{len(S)} ==")

# ---- DATA QUALITY: does completion honor the prompt's target_accuracy? ----
print("\n" + "="*70)
print("QUALITY CHECK: prompt target_accuracy vs realized ground_truth rate")
print("="*70)
# parse "target_accuracy for @X on <topic-ish> is 0.NN" from prompts
pat = re.compile(r"target_accuracy for (@\w+) on ([\w\-> ]+?) is ([01]?\.\d+)", re.I)
checks = []
for ri, (prompt, obj) in enumerate(zip(df["enhanced_prompt"], parsed)):
    if not isinstance(obj, dict):
        continue
    for m in pat.finditer(str(prompt)):
        handle, topic, tgt = m.group(1), m.group(2).strip(), float(m.group(3))
        sub = C[(C.row == ri) & (C.handle == handle)]
        # match topic substring against primary_path leaf or full path
        if topic:
            key = topic.replace(" ", "")
            sub = sub[sub.topic_paths.apply(
                lambda lst: any(key in str(p).replace(" ","") or
                                str(p).split(">")[-1]==topic.split(">")[-1] for p in (lst or [])))]
        res = sub[sub.ground_truth.isin(["true","false"])]
        if len(res):
            realized = (res.ground_truth == "true").mean()
            checks.append((ri, handle, topic, tgt, realized, len(res), abs(realized-tgt)))
if checks:
    ck = pd.DataFrame(checks, columns=["row","handle","topic","target","realized","n","abs_err"])
    print(f"parsed {len(ck)} explicit target_accuracy constraints from prompts")
    print(f"mean abs error (realized vs target): {ck.abs_err.mean():.3f}  median: {ck.abs_err.median():.3f}")
    print(f"within 0.05: {(ck.abs_err<=0.05).mean():.1%}   within 0.10: {(ck.abs_err<=0.10).mean():.1%}")
    print("\nworst 10 deviations:")
    print(ck.sort_values("abs_err", ascending=False).head(10).to_string(index=False))
else:
    print("no explicit target_accuracy constraints parsed from prompts")

# duplicate ledgers?
print(f"\n== duplicate completions: {df['enhanced_completion'].duplicated().sum()} / {len(df)} ==")
print(f"== duplicate prompts: {df['enhanced_prompt'].duplicated().sum()} / {len(df)} ==")
