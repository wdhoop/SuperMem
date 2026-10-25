# -*- coding: utf-8 -*-
"""fullctx（天花板）vs l24 v2（检索供给）：F1 全量 + 分类 + 逐题对照 + unknown 率"""
import json, re, string
from collections import defaultdict

EVAL = r"E:\AIGC_Hub\_refactor\locomo_eval"

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
        rec = json.loads(line)
        d[(rec["sid"], rec["idx"])] = rec
    return d

def summary(name, data):
    by_cat = defaultdict(lambda: {"n": 0, "f": 0.0, "unknown": 0})
    f_all = []
    unknown_all = 0
    for k, rec in data.items():
        s = f1(rec["pred"], rec["gt"])
        f_all.append(s)
        if "unknown" in rec["pred"].lower():
            unknown_all += 1
        c = rec["category"]
        by_cat[c]["n"] += 1
        by_cat[c]["f"] += s
        if "unknown" in rec["pred"].lower():
            by_cat[c]["unknown"] += 1
    n = len(f_all)
    print(f"\n===== {name} 全量 {n} =====")
    print(f"F1 = {sum(f_all)/n:.4f}   unknown = {unknown_all/n*100:.1f}%")
    print("分类别:")
    for c, st in sorted(by_cat.items(), key=lambda x: -x[1]["n"]):
        print(f"  {c:>14} n={st['n']:>4}  F1={st['f']/st['n']:.4f}  unknown={st['unknown']/st['n']*100:.1f}%")

def main():
    v2 = load(EVAL + r"\locomo_results_125b_l24_v2.jsonl")
    fc = load(EVAL + r"\locomo_results_125b_fullctx.jsonl")
    print(f"v2={len(v2)} fullctx={len(fc)}")

    summary("fullctx(天花板)", fc)
    summary("l24 v2(检索供给)", v2)

    common = [(k, v2[k], fc[k]) for k in v2 if k in fc]
    cn = len(common)
    fv, ff = [], []
    win = loss = tie = 0
    for k, a, b in common:
        sv, sf = f1(a["pred"], a["gt"]), f1(b["pred"], b["gt"])
        fv.append(sv); ff.append(sf)
        if sf > sv: win += 1
        elif sf < sv: loss += 1
        else: tie += 1
    print(f"\n===== 同题对照 fullctx vs l24-v2 {cn} =====")
    print(f"fullctx F1={sum(ff)/cn:.4f}   l24-v2 F1={sum(fv)/cn:.4f}   Δ={100*(sum(ff)-sum(fv))/cn:+.2f}pp")
    print(f"逐题: fullctx胜 {win} / l24胜 {loss} / 平 {tie}")

    bc = defaultdict(lambda: {"n": 0, "fv": 0.0, "ff": 0.0})
    for k, a, b in common:
        sv, sf = f1(a["pred"], a["gt"]), f1(b["pred"], b["gt"])
        c = a["category"]
        bc[c]["n"] += 1
        bc[c]["fv"] += sv
        bc[c]["ff"] += sf
    print("分类对照:")
    for c, st in sorted(bc.items(), key=lambda x: -x[1]["n"]):
        d = (st["ff"] - st["fv"]) / st["n"] * 100
        print(f"  {c:>14} n={st['n']:>4}  fullctx={st['ff']/st['n']:.4f}  l24={st['fv']/st['n']:.4f}  Δ={d:+.2f}pp")

    # unknown 率对照
    u_v2 = sum(1 for k, r in v2.items() if "unknown" in r["pred"].lower()) / len(v2) * 100
    u_fc = sum(1 for k, r in fc.items() if "unknown" in r["pred"].lower()) / len(fc) * 100
    print(f"\nunknown 率: l24-v2={u_v2:.1f}%  fullctx={u_fc:.1f}%")

if __name__ == "__main__":
    main()
