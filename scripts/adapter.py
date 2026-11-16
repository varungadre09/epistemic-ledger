#!/usr/bin/env python3
"""
Adapter: parse the track_record_ledger_eval prompt/completion parquet into the
flat per-claim schema that test_a.py consumes.

Input  : folder of parquet files with columns [enhanced_prompt, enhanced_completion]
Output : folder with one parquet file, one row per (claim, source, ledger), columns:
           handle, handle_raw, ledger_id, topic_paths (list<str>),
           ground_truth, edge_case, edge_case_norm, hidden_quality (JSON str),
           claim_id, text, stated_confidence, is_falsifiable,
           resolves_on, posted_at, resolution_basis, persona, as_of, salvaged

Usage:
    python -I adapter.py <input_folder> <output_folder> [--scope ledger|global]

Notes
 - Each ledger row is an independent synthetic world with its own planted
   hidden_quality. Default --scope ledger sets handle = "{ledger_id}::{raw}" so
   test_a.py scores each ledger-source separately and the hidden_quality dedup is
   correct. --scope global keeps the raw handle (pools across ledgers; conflates
   planted qualities -- use only if you deliberately want that).
 - Truncated completions (~31%) are salvaged with a bracket-aware repair that
   keeps every complete claim up to the cut point.
"""
import sys, os, glob, json, re, argparse
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

FENCE_OPEN = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
FENCE_CLOSE = re.compile(r"\s*```\s*$")


def strip_fence(s: str) -> str:
    s = s.strip()
    s = FENCE_OPEN.sub("", s)
    s = FENCE_CLOSE.sub("", s)
    return s.strip()


def repair_truncated(s: str):
    """Return the longest valid-JSON prefix by closing open brackets at the last
    point a nested container completed. Returns parsed obj or None."""
    stack = []          # expected closing chars, in open order
    in_str = esc = False
    last_good = None    # (cut_index_exclusive, stack_snapshot_after_close)
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
            if stack:  # closed a nested container; parent can still be closed -> safe cut
                last_good = (i + 1, list(stack))
    if last_good is None:
        return None
    cut, st = last_good
    frag = s[:cut].rstrip().rstrip(",")
    frag += "".join(reversed(st))  # close innermost first
    try:
        return json.loads(frag)
    except Exception:
        return None


def extract(s):
    """Return (obj, status) where status in {ok, salvaged, fail, null}."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None, "null"
    body = strip_fence(str(s))
    try:
        return json.loads(body), "ok"
    except Exception:
        pass
    obj = repair_truncated(body)
    if isinstance(obj, (dict, list)):
        return obj, "salvaged"
    return None, "fail"


def norm_edge(v):
    if v is None:
        return None
    t = str(v).strip().lower()
    if t in ("", "none", "null", "nan"):
        return None
    if t.startswith("flip_flop") or t.startswith("flip-flop"):
        return "flip_flop"
    if "barely" in t:
        return "barely_resolved"
    if "multi" in t:
        return "multi_topic"
    if "retract" in t:
        return "retracted"
    if t.startswith("open") or t == "open claim":
        return "open"
    return t


def as_path_list(tp):
    if tp is None:
        return []
    if isinstance(tp, (list, tuple, np.ndarray)):
        return [str(x) for x in tp if x is not None]
    return [str(tp)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_folder")
    ap.add_argument("output_folder")
    ap.add_argument("--scope", choices=["ledger", "global"], default="ledger")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.input_folder, "*.parquet")))
    if not files:
        sys.exit(f"No parquet files in {args.input_folder!r}")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"loaded {len(df)} ledger rows from {len(files)} files")

    status = {"ok": 0, "salvaged": 0, "fail": 0, "null": 0}
    rows = []
    n_ledgers_used = n_no_sources = 0
    for ri, comp in enumerate(df["enhanced_completion"]):
        obj, st = extract(comp)
        status[st] += 1
        if not isinstance(obj, dict):
            continue
        srcs = obj.get("sources")
        if not isinstance(srcs, list):
            n_no_sources += 1
            continue
        as_of = obj.get("as_of")
        n_ledgers_used += 1
        for s in srcs:
            if not isinstance(s, dict):
                continue
            raw = s.get("handle")
            if raw is None:
                continue
            hq_raw = s.get("hidden_quality")
            hq = {}
            if isinstance(hq_raw, dict):
                for k, v in hq_raw.items():
                    if v is None:
                        continue
                    try:
                        hq[str(k)] = float(v)   # drop null/non-numeric planted accuracies
                    except (TypeError, ValueError):
                        pass
            hq_json = json.dumps(hq)
            persona = s.get("persona")
            handle = f"{ri}::{raw}" if args.scope == "ledger" else str(raw)
            for c in (s.get("claims") or []):
                if not isinstance(c, dict):
                    continue
                gt = c.get("ground_truth")
                gt = "" if gt is None else str(gt).strip().lower()
                sc = c.get("stated_confidence")
                try:
                    sc = float(sc) if sc is not None else None
                except (TypeError, ValueError):
                    sc = None
                edge = c.get("edge_case")
                edge = None if (edge is None or (isinstance(edge, float) and pd.isna(edge))) else str(edge)
                rows.append(dict(
                    handle=handle,
                    handle_raw=str(raw),
                    ledger_id=ri,
                    topic_paths=as_path_list(c.get("topic_paths")),
                    ground_truth=gt,
                    edge_case=edge,
                    edge_case_norm=norm_edge(edge),
                    hidden_quality=hq_json,
                    claim_id=c.get("claim_id"),
                    text=c.get("text"),
                    stated_confidence=sc,
                    is_falsifiable=bool(c.get("is_falsifiable")) if c.get("is_falsifiable") is not None else None,
                    resolves_on=c.get("resolves_on"),
                    posted_at=c.get("posted_at"),
                    resolution_basis=c.get("resolution_basis"),
                    persona=persona,
                    as_of=as_of,
                    salvaged=(st == "salvaged"),
                ))

    out = pd.DataFrame(rows)
    print(f"\nparse status: {status}")
    print(f"ledgers with sources used: {n_ledgers_used}  (no-sources skipped: {n_no_sources})")
    print(f"flat claims emitted: {len(out)}  "
          f"(from salvaged rows: {int(out.salvaged.sum())})")
    print(f"unique scoped handles: {out.handle.nunique()}  "
          f"unique raw handles: {out.handle_raw.nunique()}")
    print("ground_truth:", out.ground_truth.value_counts().to_dict())

    os.makedirs(args.output_folder, exist_ok=True)
    # explicit arrow schema so nulls/lists are stable
    schema = pa.schema([
        ("handle", pa.string()), ("handle_raw", pa.string()),
        ("ledger_id", pa.int64()), ("topic_paths", pa.list_(pa.string())),
        ("ground_truth", pa.string()), ("edge_case", pa.string()),
        ("edge_case_norm", pa.string()), ("hidden_quality", pa.string()),
        ("claim_id", pa.string()), ("text", pa.string()),
        ("stated_confidence", pa.float64()), ("is_falsifiable", pa.bool_()),
        ("resolves_on", pa.string()), ("posted_at", pa.string()),
        ("resolution_basis", pa.string()), ("persona", pa.string()),
        ("as_of", pa.string()), ("salvaged", pa.bool_()),
    ])
    for col in ("claim_id", "text", "resolves_on", "posted_at",
                "resolution_basis", "persona", "as_of"):
        out[col] = out[col].astype(object).where(out[col].notna(), None)
    tbl = pa.Table.from_pandas(out, schema=schema, preserve_index=False)
    dest = os.path.join(args.output_folder, "part-000000.parquet")
    pq.write_table(tbl, dest)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
