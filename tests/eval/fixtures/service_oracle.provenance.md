# Service-detection oracle — provenance

Ground truth for `service_oracle.json`. Every entry is transcribed **from
`.superpowers/sdd/ratbench-service-catalog.md` alone** — never by running
`build_service_nodes`. An oracle fitted to the detector would measure nothing.

The scorer (`scripts/eval_service_detection_fidelity.py`) compares the detector's
`ServiceNode.name` set against `must_detect`, so the oracle lists **service names**
(the compose `services.<name>` key / CI `jobs.<job>.services.<name>` key), exactly as
the catalog's *Service* column records them — not kinds. That is why `rq/rq` is
`["valkey"]` (the CI service is *named* `valkey`), not `["redis"]` (its kind).

## Schema: precision and recall are split (why the earlier flat oracle was wrong)

```json
"PostHog/posthog": {"must_detect": ["clickhouse", "elasticsearch", "etcd", ...], "complete": false}
```

- **`complete: true`** — the catalog states *every* backing service name for this repo
  exhaustively. The repo scores **recall AND precision**.
- **`complete: false`** — the catalog *collapses* one or more service names, so
  `must_detect` is a known-true **subset**. The repo scores **recall only**; any extra
  the detector finds is not held against precision, because we cannot know it is wrong.

Recall pools over **all** repos (every known-true positive must be found); precision
pools over `complete` repos **only**. This is what lets all 22 backing repos — including
the 9 hard, name-collapsing ones — contribute recall signal without the earlier
all-or-nothing choice (a partial list would have manufactured false positives; omitting
the repo would have biased the score upward by dropping the hardest cases).

INFRA/ADMIN rows (reverse-proxy, admin UI, observability sidecar, CLI/init helper) are
excluded everywhere — the catalog's own legend (§1) says they are not backing services.

## `complete: true` classification rule

A repo is `complete: true` iff every backing service key is *spelled out* in the catalog
— including cells that ENUMERATE keys via `a(+b)` or `a/b` where both `a` and `b` are
literal names. It is `complete: false` iff any cell HIDES a key: a glob (`*-postgres`),
a descriptor-variant (`(+primary,replica)`, `(+7,cluster,nodes)`, `(+test)`), or a
*role-ambiguous* slash (`db/postgres` — one service or two? which name does the file
use?). For `complete: false` repos, `must_detect` lists only the names I am confident are
exact keys (bare tokens / enumerated base tokens); the hidden portion is left out and
explained below.

## `complete: true` backing-service repos (13) — catalog citations (§1 table)

| Repo | must_detect | Catalog rows |
|---|---|---|
| Checkmk/checkmk | oracle-12c, oracle-19c, oracle-free, oracle-perf, oracle-xe | 5 distinct `oracle-*` rows, `oracle \| EXOTIC` |
| Cloud-CV/EvalAI | db, memcached, sqs | `sqs \| softwaremill/elasticmq`, `memcached \| memcached:1.6.15`, `db \| postgres:17.10` |
| Donkie/Spoolman | db | 3 rows all `Service = db` (cockroach/mariadb/postgres image variants) → one name |
| aiidateam/aiida-core | database, postgres, messaging, rabbitmq, slurm | `database`, `postgres`(ci), `messaging`, `rabbitmq`(ci), `slurm` — 5 distinct rows |
| baserow/baserow | db, redis, s3mock | `db \| pgvector…`, `redis \| redis:6…`, `s3mock \| adobe/s3mock` |
| coderamp-labs/gitingest | minio | `minio \| minio/minio` (its `minio-setup \| minio/mc` is INFRA) |
| feast-dev/feast | etcd, milvus, minio, postgres, redis | `etcd`, `milvus`, `minio`, `postgres \| pgvector:pg16`, `redis` |
| jhao104/proxy_pool | proxy_redis | `proxy_redis \| redis \| KNOWN` |
| mozilla/addons-server | autograph, elasticsearch, memcached, mysqld, rabbitmq, redis | 6 distinct rows (its `nginx` is INFRA) |
| pretix/pretix | postgres | `pretix/pretix \| ci \| postgres \| postgres:15` |
| rq/rq | valkey | `rq/rq \| ci \| valkey \| valkey/valkey:{matrix} \| ALIAS->redis` |
| sooperset/mcp-atlassian | confluence, jira, confluence-db, jira-db | `confluence`, `jira`, `confluence-db/jira-db \| postgres:15-alpine` (slash enumerates two distinct db services) |
| tgoai/tgo | postgres, db, redis, wukongim | `postgres(+db)` (enumerates both), `redis`, `wukongim` (flower/adminer/redis-commander/nginx are INFRA) |

Notes:
- **Cloud-CV/EvalAI** deliberately follows the catalog (`memcached`), not the earlier
  illustrative example's `redis` — the catalog has no redis for EvalAI.
- **mcp-atlassian** / **tgo** are `complete: true`: their `/` and `(+)` cells *enumerate*
  the extra names (`confluence-db`+`jira-db`; `postgres`+`db`) rather than hiding a key.

## `complete: false` backing-service repos (9) — subset + which names the catalog collapsed

Each lists only names I can confirm are exact service keys. The collapsed portion (and
why it is unrecoverable from the catalog) is stated so the subset is auditable.

| Repo | must_detect (subset) | Collapsed / omitted names & why |
|---|---|---|
| PostHog/posthog | clickhouse, elasticsearch, etcd, localstack, objectstorage-azure, opensearch, redis, temporal, zookeeper | `kafka(+redpanda,console,init)` (image is redpanda — literal key uncertain), `objectstorage/seaweedfs` (role-ambiguous slash), `db/postgres` (role-ambiguous slash). `redis`/`temporal`/`opensearch` kept as certain base tokens; their `(+7,cluster,nodes)`/`(+admin,ui)`/`(+dashboards)` variant/INFRA siblings are not listed. |
| bruin-data/ingestr | bench-mongo-source, bench-mssql-dest, bench-mysql-source | `*-postgres (4x)` is a glob — the four postgres service names are not given. |
| django-oauth/django-oauth-toolkit | mysql, postgres | `mysql(+primary,replica)`, `postgres(+primary,replica)` — primary/replica are descriptors; the replica service keys (e.g. `mysql-replica`) are not spelled out. |
| frappe/press | frankfurter, mariadb, postgres | `redis-cache/queue/socketio` — three redis service keys implied but the exact suffixed names are not certain. |
| mlflow/mlflow | mssql, mysql, postgres | `minio/storage` (role-ambiguous slash, row carries two images minio+rustfs — one service or two?); `postgres(+postgresql)` sibling `postgresql` not listed. |
| polarsource/polar | localstack, minio, redis, tinybird | `db/postgres` (role-ambiguous slash) omitted; `minio(+setup)` base `minio` kept, `setup` is INFRA (`minio/mc`). |
| supabase/supabase-py | db, gotrue, rest | `gotrue(+autoconfirm,disabled)` — autoconfirm/disabled variant service keys not spelled out (`mail` is INFRA). |
| wecode-ai/Wegent | elasticsearch, mysql, redis | `mysql(+test)`, `redis(+test)` — the `-test` CI-variant service keys are not spelled out. |
| xuwei95/ezdata | ezdata-es, ezdata-minio, ruoyi-pg | `*-db/mysql-dev/ruoyi-mysql` (glob+slash), `*-redis(-dev)` (glob) — those service keys are unrecoverable; `-dev` variants of es/minio also omitted. |

## Empty-list repos (10) — `complete: true`, `must_detect: []` — false-positive candidates

These declare compose/CI files but **zero backing services**, so an empty complete list
scores any detector false positive. All are named in the catalog (§0 false-positive dirs
and §1 "zero backing services" note); none are invented.

| Repo | Catalog basis |
|---|---|
| ArchipelagoMW/Archipelago | §1: "nginx reverse-proxy only" |
| BeehiveInnovations/pal-mcp-server | §1: "self-build only" |
| fastapi/typer | §1: "self-build only" |
| karlicoss/promnesia | §1: "self-build only" |
| nginx-proxy/nginx-proxy | §1: "nginx/dockergen — it *is* the reverse proxy under test … no data store" |
| OpenCTI-Platform/connectors | §0: 277 connector composes, each only its own `opencti/connector-*` image → "nets to zero" |
| Qiskit/qiskit | §0: `*decompose*.yaml` release notes — not compose files |
| Azure/azure-cli | §0: static YAML test fixtures for the `compose` conversion command |
| containers/podman-compose | §0: `tests/integration/*` parser fixtures (trivial alpine/busybox/nginx) |
| testcontainers/testcontainers-python | §0: `compose_fixtures/*` for the library's own `DockerCompose` wrapper |

The remaining ~23 of the 50 repos "have no compose file and no CI `services:` block at
all" (§1). They cannot produce a false positive and the catalog does not name them
individually, so they are excluded (adding them would require inventing repo keys).

## Summary

- **32 total oracle keys** = 22 backing-service repos (all of the catalog's 22) + 10 empty.
- **23 `complete: true`** (13 backing + 10 empty) → score recall AND precision.
- **9 `complete: false`** (the name-collapsing repos) → score recall only; each `must_detect`
  is a documented known-true subset.
