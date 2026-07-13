#!/usr/bin/env python3
"""SWE-smith-py Task 3 (dataset stats) + Task 5 (per-image manifest consistency).
MEMORY-SAFE: streams parquet shard-by-shard, never holds all rows; keeps only
bounded per-image aggregates. Two light passes over the (column-projected) data.
Run with /opt/rat_venv/bin/python on the VM."""
from __future__ import annotations
import glob, json, re, sys
from collections import Counter, defaultdict
import pyarrow.parquet as pq

DATA = "/opt/swesmith_audit/data/data"
COLS = ["instance_id", "repo", "image_name", "PASS_TO_PASS", "FAIL_TO_PASS"]
OUT  = "/opt/swesmith_audit/out/dataset_findings.json"
files = sorted(glob.glob(f"{DATA}/*.parquet"))
assert files, f"no parquet under {DATA}"

def iter_rows():
    for f in files:
        d = pq.read_table(f, columns=COLS).to_pydict()
        for i in range(len(d["instance_id"])):
            yield (d["instance_id"][i], d["repo"][i], d["image_name"][i],
                   d["PASS_TO_PASS"][i], d["FAIL_TO_PASS"][i])

def pct(v):
    if not v: return {}
    s = sorted(v); q = lambda p: s[min(len(s)-1, int(p*len(s)))]
    return {"min": s[0], "p50": q(.5), "p90": q(.9), "p99": q(.99), "max": s[-1],
            "mean": round(sum(s)/len(s), 2)}

HAS_SEP = re.compile(r"::"); PY_LEFT = re.compile(r"^[^:]+\.py::")

# ---------- PASS A: stats + per-image union/biggest/hashes ----------
rows_n = 0
repos, images = set(), set()
per_img_tasks = Counter()
p2p_sizes, f2p_sizes = [], []
empty_p2p = empty_f2p = dup_p2p = dup_f2p = overlap = 0
shape = Counter(); nonpytest_ex = []; nonpy_img = defaultdict(int)
uni = {}           # image -> set (union of all task G)
big = {}           # image -> biggest task G (set)
ghash = defaultdict(set)   # image -> {hash(frozenset(G))}
phash = defaultdict(set)   # image -> {hash(frozenset(P2P))}

for iid, repo, img, p2p, f2p in iter_rows():
    rows_n += 1
    repos.add(repo); images.add(img); per_img_tasks[img] += 1
    sp, sf = set(p2p), set(f2p)
    p2p_sizes.append(len(p2p)); f2p_sizes.append(len(f2p))
    if not p2p: empty_p2p += 1
    if not f2p: empty_f2p += 1
    if len(p2p) != len(sp): dup_p2p += 1
    if len(f2p) != len(sf): dup_f2p += 1
    if sp & sf: overlap += 1
    for tid in p2p:
        if not HAS_SEP.search(tid):
            shape["no_double_colon(NON_PYTEST)"] += 1
            if len(nonpytest_ex) < 20: nonpytest_ex.append(tid)
        elif PY_LEFT.match(tid): shape["py_node(standard)"] += 1
        else: shape["has::_left_not_py(pygments)"] += 1; nonpy_img[img] += 1
    for tid in f2p:
        if not HAS_SEP.search(tid): shape["no_double_colon(NON_PYTEST)"] += 1
        elif PY_LEFT.match(tid): shape["py_node(standard)"] += 1
        else: shape["has::_left_not_py(pygments)"] += 1
    G = sp | sf
    u = uni.get(img)
    if u is None: uni[img] = set(G)
    else: u |= G
    b = big.get(img)
    if b is None or len(G) > len(b): big[img] = set(G)
    ghash[img].add(hash(frozenset(G)))
    phash[img].add(hash(frozenset(sp)))

# ---------- PASS B: subset-nesting vs each image's biggest ----------
nest_frac = {}     # image -> count tasks subset-of-biggest
nest_tot = Counter()
jac_vs_big = defaultdict(list)
for iid, repo, img, p2p, f2p in iter_rows():
    G = set(p2p) | set(f2p); B = big[img]
    nest_tot[img] += 1
    if G <= B: nest_frac[img] = nest_frac.get(img, 0) + 1
    u = len(G | B)
    jac_vs_big[img].append(len(G & B)/u if u else 1.0)

# ---------- assemble ----------
tpi = list(per_img_tasks.values())
consistency = []
for img in uni:
    Gs_exact = len(ghash[img]) == 1
    Ps_exact = len(phash[img]) == 1
    jl = sorted(jac_vs_big[img]); med = jl[len(jl)//2]
    frac_nest = nest_frac.get(img, 0)/nest_tot[img]
    consistency.append({
        "image": img.split(".x86_64.")[-1], "n_tasks": per_img_tasks[img],
        "G_exact_equal": Gs_exact, "P2P_exact_equal": Ps_exact,
        "G_union": len(uni[img]), "G_biggest": len(big[img]),
        "all_nest_in_biggest": frac_nest >= 0.9999,
        "frac_nest_in_biggest": round(frac_nest, 4),
        "median_task_vs_biggest_jaccard": round(med, 4),
    })
consistency.sort(key=lambda x: (x["frac_nest_in_biggest"], x["median_task_vs_biggest_jaccard"]))

report = {
    "dataset_revision": "77cab9055d42ab4a5c25c89a8f937096db13558e",
    "row_count": rows_n, "unique_repos": len(repos), "unique_images": len(images),
    "tasks_per_image": {**pct(tpi), "n_images": len(per_img_tasks)},
    "PASS_TO_PASS_sizes": pct(p2p_sizes), "FAIL_TO_PASS_sizes": pct(f2p_sizes),
    "rows_empty_PASS_TO_PASS": empty_p2p, "rows_empty_FAIL_TO_PASS": empty_f2p,
    "rows_dup_ids_P2P": dup_p2p, "rows_dup_ids_F2P": dup_f2p, "rows_P2P_F2P_overlap": overlap,
    "node_id_shapes": dict(shape), "truly_non_pytest_examples": nonpytest_ex,
    "images_with_nonpy_ids": len(nonpy_img),
    "images_with_nonpy_ids_names": sorted(k.split(".x86_64.")[-1] for k in nonpy_img)[:12],
    "consistency": {
        "n_images": len(uni),
        "G_exact_equal_rate": round(sum(1 for c in consistency if c["G_exact_equal"])/len(consistency), 4),
        "P2P_exact_equal_rate": round(sum(1 for c in consistency if c["P2P_exact_equal"])/len(consistency), 4),
        "n_images_all_nest_in_biggest": sum(1 for c in consistency if c["all_nest_in_biggest"]),
        "n_images_frac_nest_ge_0.99": sum(1 for c in consistency if c["frac_nest_in_biggest"] >= 0.99),
        "median_task_vs_biggest_jaccard": pct([c["median_task_vs_biggest_jaccard"] for c in consistency]),
    },
    "consistency_worst_15": consistency[:15],
    "stable_images_sizes": sorted(
        [{"image": c["image"], "G": c["G_biggest"], "n_tasks": c["n_tasks"]}
         for c in consistency if c["G_exact_equal"]], key=lambda z: z["G"]),
}
with open(OUT, "w") as fh:
    json.dump({"report": report, "per_image_consistency": consistency}, fh, indent=1)
print(json.dumps(report, indent=1))
print(f"\nwrote {OUT}", file=sys.stderr)
