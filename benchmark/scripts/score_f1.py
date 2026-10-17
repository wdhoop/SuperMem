# -*- coding: utf-8 -*-
"""
LoCoMo F1 评分：对比两份结果 jsonl（如 limit24 vs limit8），输出逐题 F1 对比与分类别明细。
用法：
  python score_f1.py --a results/locomo_results_125b_l24.jsonl --b results/locomo_results_125b.jsonl
"""
import json, re, string, argparse, os
from collections import defaultdict

def norm_tokens(s):
    s = str(s).lower()
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = re.sub(r"[" + re.escape(string.punctuation) + "]", " ", s)
    return set(s.split())

def f1(pred, gt):
    p, g = norm_tokens(pred), norm_tokens(gt)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    inter = len(p & g)
    if inter == 0:
        return 0.0
    prec = inter / len(p)
    rec = inter / len(g)
    return 2 * prec * rec / (prec + rec)

def load(path):
    d = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        d[(rec["sid"], rec["idx"])] = rec
    return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="结果 A jsonl")
    ap.add_argument("--b", required=True, help="结果 B jsonl（对照组）")
    args = ap.parse_args()
    ra = load(args.a)
    rb = load(args.b)
    common = [(k, ra[k], rb[k]) for k in ra if k in rb]
    n = len(common)
    print(f"== 对比 {os.path.basename(args.a)} vs {os.path.basename(args.b)}  共题 {n} ==")
    if n == 0:
        print("无共同题目，无法对比")
        return
    fa, fb = [], []
    win = loss = tie = 0
    ua = ub = 0
    by_cat = defaultdict(lambda: {"n": 0, "fa": 0.0, "fb": 0.0})
    for k, a, b in common:
        sa, sb = f1(a["pred"], a["gt"]), f1(b["pred"], b["gt"])
        fa.append(sa); fb.append(sb)
        if sa > sb: win += 1
        elif sa < sb: loss += 1
        else: tie += 1
        if "unknown" in a["pred"].lower(): ua += 1
        if "unknown" in b["pred"].lower(): ub += 1
        c = a["category"]
        by_cat[c]["n"] += 1
        by_cat[c]["fa"] += sa
        by_cat[c]["fb"] += sb
    print(f"A F1={sum(fa)/n:.4f}  unknown={ua/n*100:.1f}%")
    print(f"B F1={sum(fb)/n:.4f}  unknown={ub/n*100:.1f}%")
    print(f"逐题: A胜 {win} / B胜 {loss} / 平 {tie}")
    print("分类别:")
    for c, st in sorted(by_cat.items(), key=lambda x: -x[1]["n"]):
        d = (st["fa"] - st["fb"]) / st["n"] * 100
        print(f"  {c:>14} n={st['n']:>4}  A={st['fa']/st['n']:.4f}  B={st['fb']/st['n']:.4f}  Δ={d:+.1f}pp")

if __name__ == "__main__":
    main()
