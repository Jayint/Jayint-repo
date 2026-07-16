#!/usr/bin/env python3
"""audit_swesmith_images.py — narrowly-scoped auditor for whether SWE-smith
images reproduce their published test manifests, for use as a FIXED LOWER-BOUND
gold set in an environment-reproduction benchmark.

Default is DRY-RUN: reads the pinned dataset, groups by image, pins image digests
via the registry (NO docker pulls), reports manifest size + per-image consistency.
With --execute it pulls a SELECTED image by digest and runs, in the pristine
reference container, (1) structured collection and (2) the official test command
through a harness-owned pytest plugin, then compares against G = P2P u F2P.

It NEVER claims absolute repository test coverage: |G| is a reproducible lower
bound (SWE-smith's log parser drops whitespace-parametrized IDs — see report),
not the full suite.

Deps: pyarrow (dry-run); docker CLI + the plugin (execute). stdlib otherwise.
"""
import argparse, glob, json, os, re, subprocess, sys, urllib.request as U
from collections import defaultdict
import pyarrow.parquet as pq

PINNED_REV = "77cab9055d42ab4a5c25c89a8f937096db13558e"
DEFAULT_TEST_CMD = "pytest --disable-warnings --color=no --tb=no --verbose"  # PythonProfile default
COLS = ["instance_id", "repo", "image_name", "PASS_TO_PASS", "FAIL_TO_PASS"]

# ---------- dataset ----------
def load_grouped(data_dir):
    by_img = defaultdict(list)  # image -> list of (instance_id, repo, frozenset(G))
    for f in sorted(glob.glob(os.path.join(data_dir, "*.parquet"))):
        d = pq.read_table(f, columns=COLS).to_pydict()
        for i in range(len(d["instance_id"])):
            g = frozenset(d["PASS_TO_PASS"][i]) | frozenset(d["FAIL_TO_PASS"][i])
            by_img[d["image_name"][i]].append((d["instance_id"][i], d["repo"][i],
                                               d["PASS_TO_PASS"][i], d["FAIL_TO_PASS"][i], g))
    return by_img

def image_meta(image, tasks):
    Gs = [t[4] for t in tasks]
    biggest_i = max(range(len(Gs)), key=lambda k: len(Gs[k]))
    exact = all(g == Gs[0] for g in Gs)
    # representative task: if consistent, task 0; else the biggest (superset candidate)
    rep = tasks[0] if exact else tasks[biggest_i]
    m = re.search(r"\.x86_64\.(.+)$", image)
    slug = m.group(1) if m else image
    commit = slug.rsplit(".", 1)[-1] if "." in slug else ""
    return {
        "image_name": image, "profile_name": slug, "commit": commit,
        "n_tasks": len(tasks), "manifest_consistent": exact,
        "expected_manifest_size": len(rep[4]),
        "rep_instance_id": rep[0], "repo": rep[1],
        "rep_P2P": list(rep[2]), "rep_F2P": list(rep[3]),
    }

# ---------- registry digest (no pull) ----------
def resolve_digest(image, timeout=25):
    repo = image.split("@")[0]
    try:
        tok = json.load(U.urlopen(
            "https://auth.docker.io/token?service=registry.docker.io&scope=repository:%s:pull" % repo,
            timeout=timeout))["token"]
        req = U.Request("https://registry-1.docker.io/v2/%s/manifests/latest" % repo, headers={
            "Authorization": "Bearer " + tok,
            "Accept": "application/vnd.oci.image.index.v1+json,"
                      "application/vnd.docker.distribution.manifest.list.v2+json,"
                      "application/vnd.docker.distribution.manifest.v2+json"})
        return U.urlopen(req, timeout=timeout).headers.get("Docker-Content-Digest")
    except Exception as e:
        return "ERR:%s" % e

# ---------- execute (pilot) ----------
def sh(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def dexec(name, shell_cmd, workdir, env=None, timeout=None):
    cmd = ["docker", "exec", "-w", workdir]
    for k, v in (env or {}).items():
        cmd += ["-e", "%s=%s" % (k, v)]
    cmd += [name, "bash", "-lc", shell_cmd]
    return sh(cmd, timeout=timeout)

def execute_pilot(meta, digest, test_cmd, workdir, plugin, out_dir, run_timeout):
    image = "%s@%s" % (meta["image_name"], digest)
    G = set(meta["rep_P2P"]) | set(meta["rep_F2P"])
    name = re.sub(r"[^a-z0-9]+", "_", meta["profile_name"].lower())[:50]
    cname = "audit_" + name
    res = {"execution_complete": False, "timeout": False, "notes": []}
    sh(["docker", "rm", "-f", cname])
    run = sh(["docker", "run", "-d", "--name", cname, "--network", "none",
              "--cpus", "2", "--memory", "4g", "--pids-limit", "512",
              "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
              image, "sleep", "infinity"])
    if run.returncode != 0:
        res["notes"].append("docker run failed: " + run.stderr[:300]); return res
    try:
        res["git_head"] = dexec(cname, "git rev-parse HEAD", workdir).stdout.strip()
        sh(["docker", "exec", cname, "mkdir", "-p", "/audit"])
        sh(["docker", "cp", plugin, "%s:/audit/swesmith_audit_plugin.py" % cname])
        base_env = {"PYTHONPATH": "/audit", "PYTHONDONTWRITEBYTECODE": "1"}
        # collection
        col = ("pytest --collect-only -q --continue-on-collection-errors "
               "--disable-warnings -p no:cacheprovider -p swesmith_audit_plugin")
        try:
            dexec(cname, col, workdir, {**base_env, "AUDIT_OUT": "/audit/COLLECT.json"}, run_timeout)
        except subprocess.TimeoutExpired:
            res["timeout"] = True; res["notes"].append("collection timed out"); return res
        sh(["docker", "cp", "%s:/audit/COLLECT.json" % cname, os.path.join(out_dir, name + "_collect.json")])
        # official run
        full = "%s -p no:cacheprovider -p swesmith_audit_plugin" % test_cmd
        try:
            dexec(cname, full, workdir, {**base_env, "AUDIT_OUT": "/audit/RUN.json"}, run_timeout)
        except subprocess.TimeoutExpired:
            res["timeout"] = True; res["notes"].append("official run timed out"); return res
        sh(["docker", "cp", "%s:/audit/RUN.json" % cname, os.path.join(out_dir, name + "_run.json")])
    finally:
        sh(["docker", "rm", "-f", cname])
    col_p = os.path.join(out_dir, name + "_collect.json")
    run_p = os.path.join(out_dir, name + "_run.json")
    collect = json.load(open(col_p)) if os.path.exists(col_p) else {}
    runj = json.load(open(run_p)) if os.path.exists(run_p) else {}
    collected = set(collect.get("collected", []))
    passed = set(runj.get("passed_ids", []))
    tally = runj.get("outcome_tally", {})
    res.update({
        "execution_complete": bool(runj),
        "collected_count": collect.get("collected_count"),
        "collection_errors": collect.get("n_collect_errors"),
        # setup-time skips only visible in the FULL run (collect-only can't see them)
        "collection_skips": tally.get("skipped", 0) + tally.get("xfailed", 0),
        "deselected": collect.get("n_deselected"),
        "outcome_tally": tally,
        "manifest_collection_recall": round(len(G & collected)/len(G), 4) if G else None,
        "passed_expected": len(G & passed),
        "manifest_pass_recall": round(len(G & passed)/len(G), 4) if G else None,
        "missing_expected": len(G - collected),
        "unexpected_collected": len(collected - G),
        "unexpected_collected_sample": sorted(collected - G)[:15],
    })
    if res["unexpected_collected"]:
        res["notes"].append("%d collected tests absent from manifest (lower-bound; e.g. whitespace-param IDs)"
                            % res["unexpected_collected"])
    return res

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="/opt/swesmith_audit/data/data")
    ap.add_argument("--dataset-revision", default=PINNED_REV)
    ap.add_argument("--repo", help="substring filter on image slug (e.g. iniconfig)")
    ap.add_argument("--image", help="exact image_name")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out-dir", default="/opt/swesmith_audit/out")
    ap.add_argument("--raw-dir", default="/opt/swesmith_audit/raw")
    ap.add_argument("--no-digest", action="store_true", help="skip registry digest resolution")
    ap.add_argument("--execute", action="store_true", help="PULL selected image(s) by digest and run (default: dry-run, no pulls)")
    ap.add_argument("--test-cmd", default=DEFAULT_TEST_CMD)
    ap.add_argument("--workdir", default="/testbed")
    ap.add_argument("--plugin", default="/opt/swesmith_audit/scripts/swesmith_audit_plugin.py")
    ap.add_argument("--run-timeout", type=int, default=900)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True); os.makedirs(a.raw_dir, exist_ok=True)

    by_img = load_grouped(a.data_dir)
    images = sorted(by_img)
    if a.image: images = [i for i in images if i == a.image]
    elif a.repo: images = [i for i in images if a.repo.lower() in i.lower()]
    if a.limit: images = images[:a.limit]
    if not images:
        print("no images matched", file=sys.stderr); sys.exit(2)

    rows = []
    for img in images:
        meta = image_meta(img, by_img[img])
        row = {k: meta[k] for k in ("repo", "image_name", "profile_name", "commit",
                                    "n_tasks", "manifest_consistent", "expected_manifest_size")}
        row["dataset_revision"] = a.dataset_revision
        row["test_command"] = a.test_cmd
        row["image_digest"] = None if a.no_digest else resolve_digest(img)
        row["mode"] = "dry-run"
        row["notes"] = []
        if not meta["manifest_consistent"]:
            row["notes"].append("manifest VARIES across tasks (bug-scoped); |G| is per-representative-task, not a repo-wide set")
        if a.execute:
            if row["image_digest"] and not str(row["image_digest"]).startswith("ERR"):
                pull = sh(["docker", "pull", "%s@%s" % (img, row["image_digest"])], timeout=1200)
                if pull.returncode != 0:
                    row["notes"].append("pull failed: " + pull.stderr[:200])
                else:
                    row["mode"] = "execute"
                    ex = execute_pilot(meta, row["image_digest"], a.test_cmd, a.workdir,
                                       a.plugin, a.raw_dir, a.run_timeout)
                    row["notes"] += ex.pop("notes", [])
                    row.update(ex)
            else:
                row["notes"].append("no digest resolved; skipped execute")
        rows.append(row)

    out_json = os.path.join(a.out_dir, "audit_report.json")
    json.dump({"dataset_revision": a.dataset_revision, "n_images": len(rows), "rows": rows},
              open(out_json, "w"), indent=1)
    # concise markdown/terminal summary
    print("# SWE-smith image audit  (rev %s)  mode=%s" % (a.dataset_revision, "execute" if a.execute else "dry-run"))
    print("| image | tasks | consist | |G| | digest | coll_rec | pass_rec | unexp |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        d = (r["image_digest"] or "")[:19]
        print("| %s | %d | %s | %d | %s | %s | %s | %s |" % (
            r["profile_name"], r["n_tasks"], "Y" if r["manifest_consistent"] else "N",
            r["expected_manifest_size"], d,
            r.get("manifest_collection_recall", "-"), r.get("manifest_pass_recall", "-"),
            r.get("unexpected_collected", "-")))
    print("\nwrote", out_json)
    print("NOTE: |G| is a reproducible LOWER BOUND, not the full repo suite.")

if __name__ == "__main__":
    main()
