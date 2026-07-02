# Phase-1 Coverage Report

Corpus: 6 repos (6 feasible).
Deferred: unit8co/darts.

## Pooled per-tier recall (feasible repos, oracle-diff)

| Tier | Recall | Note |
|---|---|---|
| SYSTEM_LIB | 0.03 |  |
| RUNTIME | 0.22 |  |
| PACKAGE | 0.18 |  |
| SERVICE | n/a (no oracle signal) | not populated by construction-only pass (LLM classifier out of scope) |
| CONFIG | n/a (no oracle signal) | not populated by construction-only pass (LLM classifier out of scope) |

## Per-repo summary

| Repo | Feasible | Install OK | SYSTEM_LIB | RUNTIME | PACKAGE | Exec-missing |
|---|---|---|---|---|---|---|
| fastapi/typer | True | True | 0.00 | 0.00 | 0.00 | 0 |
| crytic/slither | True | True | 0.00 | 0.00 | 0.00 | 0 |
| mvt-project/mvt | True | True | 0.06 | 0.20 | 0.00 | 1 |
| python-semantic-release/python-semantic-release | True | True | n/a | n/a | 0.00 | 1 |
| mckinsey/vizro | True | False | 0.00 | n/a | 0.50 | 0 |
| crystaldba/postgres-mcp | True | True | 0.00 | 1.00 | n/a | 1 |

## Top missing-node clusters (ranked by count, tier, id)

1. **RUNTIME** `3.14` — 3 repo(s): crytic/slither, fastapi/typer, mvt-project/mvt
2. **SYSTEM_LIB** `libssl-dev` — 2 repo(s): fastapi/typer, mvt-project/mvt
3. **SYSTEM_LIB** `pkg-config` — 2 repo(s): fastapi/typer, mvt-project/mvt
4. **SYSTEM_LIB** `python3` — 2 repo(s): crytic/slither, mvt-project/mvt
5. **PACKAGE** `$psrWheelFile` — 1 repo(s): python-semantic-release/python-semantic-release
6. **PACKAGE** `.` — 1 repo(s): crytic/slither
7. **PACKAGE** `../vizro-ai` — 1 repo(s): mckinsey/vizro
8. **PACKAGE** `../vizro-core` — 1 repo(s): mckinsey/vizro
9. **PACKAGE** `./mvt` — 1 repo(s): mvt-project/mvt
10. **PACKAGE** `build` — 1 repo(s): fastapi/typer
11. **PACKAGE** `coverage` — 1 repo(s): crytic/slither
12. **PACKAGE** `dist` — 1 repo(s): python-semantic-release/python-semantic-release
13. **PACKAGE** `hatch` — 1 repo(s): mckinsey/vizro
14. **PACKAGE** `mvt` — 1 repo(s): mvt-project/mvt
15. **PACKAGE** `pip` — 1 repo(s): python-semantic-release/python-semantic-release
16. **PACKAGE** `postgres_mcp` — 1 repo(s): crystaldba/postgres-mcp
17. **PACKAGE** `pytest-github-actions-annotate-failures` — 1 repo(s): python-semantic-release/python-semantic-release
18. **PACKAGE** `semantic_release` — 1 repo(s): python-semantic-release/python-semantic-release
19. **PACKAGE** `setuptools` — 1 repo(s): python-semantic-release/python-semantic-release
20. **PACKAGE** `tests` — 1 repo(s): fastapi/typer
21. **PACKAGE** `wheel` — 1 repo(s): python-semantic-release/python-semantic-release
22. **RUNTIME** `3.10` — 1 repo(s): crytic/slither
23. **RUNTIME** `3.11` — 1 repo(s): mvt-project/mvt
24. **RUNTIME** `3.12` — 1 repo(s): mvt-project/mvt
25. **RUNTIME** `3.13` — 1 repo(s): mvt-project/mvt
26. **SYSTEM_LIB** `adb` — 1 repo(s): mvt-project/mvt
27. **SYSTEM_LIB** `autoconf` — 1 repo(s): mvt-project/mvt
28. **SYSTEM_LIB** `automake` — 1 repo(s): mvt-project/mvt
29. **SYSTEM_LIB** `build-essential` — 1 repo(s): fastapi/typer
30. **SYSTEM_LIB** `ca-certificates` — 1 repo(s): crytic/slither
31. **SYSTEM_LIB** `curl` — 1 repo(s): crytic/slither
32. **SYSTEM_LIB** `default-jre-headless` — 1 repo(s): mvt-project/mvt
33. **SYSTEM_LIB** `dnsutils` — 1 repo(s): crystaldba/postgres-mcp
34. **SYSTEM_LIB** `gcc` — 1 repo(s): crystaldba/postgres-mcp
35. **SYSTEM_LIB** `gh` — 1 repo(s): mckinsey/vizro
36. **SYSTEM_LIB** `git` — 1 repo(s): mvt-project/mvt
37. **SYSTEM_LIB** `iputils-ping` — 1 repo(s): crystaldba/postgres-mcp
38. **SYSTEM_LIB** `libc6-amd64-cross` — 1 repo(s): crytic/slither
39. **SYSTEM_LIB** `libcurl4` — 1 repo(s): mvt-project/mvt
40. **SYSTEM_LIB** `libcurl4-openssl-dev` — 1 repo(s): mvt-project/mvt
41. **SYSTEM_LIB** `libpq-dev` — 1 repo(s): crystaldba/postgres-mcp
42. **SYSTEM_LIB** `libssl3` — 1 repo(s): mvt-project/mvt
43. **SYSTEM_LIB** `libtool-bin` — 1 repo(s): mvt-project/mvt
44. **SYSTEM_LIB** `libusb-1.0-0` — 1 repo(s): mvt-project/mvt
45. **SYSTEM_LIB** `libusb-1.0-0-dev` — 1 repo(s): mvt-project/mvt
46. **SYSTEM_LIB** `net-tools` — 1 repo(s): crystaldba/postgres-mcp
47. **SYSTEM_LIB** `python3-venv` — 1 repo(s): crytic/slither
48. **SYSTEM_LIB** `sqlite3` — 1 repo(s): mvt-project/mvt
49. **SYSTEM_LIB** `udev` — 1 repo(s): mvt-project/mvt
50. **SYSTEM_LIB** `zlib1g-dev` — 1 repo(s): fastapi/typer

## Install failures (Reference-A-only fallback)

- **mckinsey/vizro**: `python3 -m pip install --break-system-packages --no-deps github==1.2.6`

## Concerns

- 'mvt' (the repo's OWN package) failed to import: the renderer never emits an install step for the PROJECT node itself (only PACKAGE/SYSTEM_LIB/TOOL nodes are 'reciped' by build_script._is_reciped), so setup.sh never installs the repo under test. A flat-layout repo (the package dir sits at repo root) can still import by accident via Python's cwd-prepended sys.path; a src-layout or name-mismatched repo cannot. This generalizes across the corpus and is a render/construction gap, not a one-off.
- 'postgres_mcp' (the repo's OWN package) failed to import: the renderer never emits an install step for the PROJECT node itself (only PACKAGE/SYSTEM_LIB/TOOL nodes are 'reciped' by build_script._is_reciped), so setup.sh never installs the repo under test. A flat-layout repo (the package dir sits at repo root) can still import by accident via Python's cwd-prepended sys.path; a src-layout or name-mismatched repo cannot. This generalizes across the corpus and is a render/construction gap, not a one-off.
- 'semantic_release' (the repo's OWN package) failed to import: the renderer never emits an install step for the PROJECT node itself (only PACKAGE/SYSTEM_LIB/TOOL nodes are 'reciped' by build_script._is_reciped), so setup.sh never installs the repo under test. A flat-layout repo (the package dir sits at repo root) can still import by accident via Python's cwd-prepended sys.path; a src-layout or name-mismatched repo cannot. This generalizes across the corpus and is a render/construction gap, not a one-off.
- CONFIG/SERVICE/DataAsset tiers are never populated by this construction-only pass (the LLM env_classifier that proposes them is out of scope here, per the no-LLM-in-the-scorer / no-agent rule) -- their recall is not meaningful signal in this report.
- Reference-A recall is a PROXY: the held-out Dockerfile/CI/compose recipe may itself omit real needs (uv/poetry-managed installs record no explicit pip package list) or use tools this oracle parser doesn't read -- a 'missing' entry can reflect the oracle's blind spot, not the graph's.
- base image: kept selector base 3.10 (satisfies '>= 3.10')
- base image: kept selector base 3.10 (satisfies '>=3.10')
- base image: kept selector base 3.11 (satisfies '>=3.10')
- base image: kept selector base 3.12 (satisfies '>=3.12')
- base image: no requires-python; kept selector base 3.11
- base image: unparseable requires-python '~= 3.8'; kept selector base 3.11
- setup.sh failed on fresh replay -- Reference-A only for this repo (a render/build-dep issue is Phase-2 scope, not a Phase-1 coverage gap).
