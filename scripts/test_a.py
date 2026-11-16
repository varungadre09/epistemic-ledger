#!/usr/bin/env python3
"""
Test A - scoring-math validation for the track-record ledger. No model inference.
Reads the synthetic fixture (parquet) and checks that hierarchical Beta-Binomial
shrinkage recovers each source's planted accuracy (hidden_quality) with sensible
uncertainty on thin buckets.

    pip install pandas pyarrow scipy numpy
    python test_a.py /path/to/parquet_folder
"""
import sys, os, glob, json
import numpy as np
import pandas as pd
from scipy.stats import beta

# ---- adjust ONLY if the printed column names differ from these ----
COL = {
    "handle": "handle",
    "topic_paths": "topic_paths",
    "ground_truth": "ground_truth",
    "edge_case": "edge_case",            # optional
    "hidden_quality": "hidden_quality",  # optional; recovery check skipped if absent
}
PARENT_STRENGTH = 10.0   # pseudo-count weight of the prior at each level of back-off
CI = [0.05, 0.95]        # 90% credible interval
SEP = ">"
# -------------------------------------------------------------------


def load_folder(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.parquet")))
    if not files:
        sys.exit(f"No .parquet files found in {folder!r}")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"Loaded {len(df)} rows from {len(files)} file(s).")
    print("Columns:", list(df.columns))
    print(df.head(3).to_string(), "\n" + "-" * 80 + "\n")
    return df


def as_list(v):
    if v is None or (np.isscalar(v) and pd.isna(v)): return []
    if isinstance(v, (list, tuple, np.ndarray)): return list(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try: return list(json.loads(s))
            except Exception: pass
        return [s]
    return [v]


def as_dict(v):
    if isinstance(v, dict): return v
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return {}
    return {}


def prefixes(path):
    parts = path.split(SEP)
    return [SEP.join(parts[:i]) for i in range(1, len(parts) + 1)]


def shrink(hits, n, prior_mean):
    a = prior_mean * PARENT_STRENGTH + hits
    b = (1 - prior_mean) * PARENT_STRENGTH + (n - hits)
    lo, hi = beta.ppf(CI, a, b)
    return a / (a + b), lo, hi


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    df = load_folder(folder)

    for k in ("handle", "topic_paths", "ground_truth"):
        if COL[k] not in df.columns:
            sys.exit(f"Required column {COL[k]!r} not found - edit COL at top of script.")
    have_hq = COL["hidden_quality"] in df.columns
    have_edge = COL["edge_case"] in df.columns

    # explode to one row per (claim, topic_path); normalise ground_truth
    rows, hidden = [], {}
    for _, r in df.iterrows():
        h = r[COL["handle"]]
        if have_hq and h not in hidden:
            hidden[h] = {k: float(v) for k, v in as_dict(r[COL["hidden_quality"]]).items()}
        gt = str(r[COL["ground_truth"]]).strip().lower()
        edge = r[COL["edge_case"]] if have_edge else None
        for p in as_list(r[COL["topic_paths"]]):
            rows.append((h, p, gt, edge))
    ex = pd.DataFrame(rows, columns=["handle", "path", "gt", "edge"])

    resolved = ex[ex["gt"].isin(["true", "false"])]
    pop_rate = (resolved["gt"] == "true").mean()
    print(f"Population resolved true-rate (root prior): {pop_rate:.3f}\n")

    report = []
    for handle, g in ex.groupby("handle"):
        gr = g[g["gt"].isin(["true", "false"])]
        nodes = sorted({pre for p in g.path.unique() for pre in prefixes(p)},
                       key=lambda s: s.count(SEP))
        post = {}
        for node in nodes:
            parent = SEP.join(node.split(SEP)[:-1]) if node.count(SEP) else None
            prior = post.get(parent, pop_rate)
            mask = gr["path"].apply(lambda p: p == node or p.startswith(node + SEP)).astype(bool)
            sub = gr[mask]
            n, hits = len(sub), int((sub["gt"] == "true").sum())
            mean, lo, hi = shrink(hits, n, prior)
            post[node] = mean
            hq = hidden.get(handle, {}).get(node) if have_hq else None
            raw = hits / n if n else float("nan")
            raw_ne = (sub[sub.edge.isna()]["gt"] == "true").mean() if have_edge and n else float("nan")
            report.append(dict(handle=handle, path=node, n=n, raw=raw,
                               raw_no_edge=raw_ne, shrunk=mean, lo=lo, hi=hi,
                               hidden=hq, covers=(hq is not None and lo <= hq <= hi)))

    rep = pd.DataFrame(report)
    for opt, val in [("display.max_rows", None), ("display.width", 200),
                     ("display.float_format", lambda x: f"{x:.3f}")]:
        pd.set_option(opt, val)
    print(rep.to_string(index=False), "\n" + "-" * 80 + "\n")

    if have_hq:
        leaves = rep[rep.hidden.notna()]
        if len(leaves):
            print(f"Recovery: {int(leaves.covers.sum())}/{len(leaves)} planted accuracies "
                  f"fall inside the 90% credible interval ({leaves.covers.mean():.0%}).")
            miss = leaves[~leaves.covers]
            if len(miss):
                print("\nBuckets the interval MISSES (inspect - usually edge-case contamination\n"
                      "or the confidence/accuracy conflation, not a math bug):")
                print(miss[["handle", "path", "n", "raw", "raw_no_edge",
                            "shrunk", "lo", "hi", "hidden"]].to_string(index=False))
    else:
        print("No hidden_quality column - scores shown above, recovery check skipped.")


if __name__ == "__main__":
    main()
