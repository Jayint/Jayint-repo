# Service-detection oracle — provenance

Ground truth for `service_oracle.json`. Every entry is transcribed **from
`.superpowers/sdd/ratbench-service-catalog.md` alone** — never by running
`build_service_nodes`. An oracle fitted to the detector would measure nothing.

The scorer compares the detector's `ServiceNode.name` set against `must_detect`, so the
oracle lists **service names** (the compose `services.<name>` / CI
`jobs.<job>.services.<name>` key) — not kinds. `rq/rq` is `["valkey"]`, not `["redis"]`.

## Schema

```json
"mlflow/mlflow": {"must_detect": ["minio", "storage", "mssql", "mysql", "postgres", "postgresql"], "complete": true}
```

- **`complete: true`** — the catalog states every backing service name for this repo
  exhaustively → scores recall **and** precision.
- **`complete: false`** — at least one catalog row is unrecoverable (a glob, or a
  descriptor group), so `must_detect` is a known-true **subset** → scores **recall only**;
  an extra the detector finds is not held against precision.

Recall pools over **all** repos; precision over `complete` repos **only**.

## Mechanical expansion table (applied to every row of every repo)

`complete: false` shields **precision**; it must never be used to under-report **recall**.
Every name the catalog states plainly is in `must_detect`, even in a partial repo. The
`complete` flag is re-derived from this table — a repo is `complete: false` **iff any** of
its rows is a glob or a non-name descriptor group.

| Catalog notation | must_detect gets | Example |
|---|---|---|
| `a(+b)`, `b` a full name | `a`, `b` | `postgres(+postgresql)` → postgres, postgresql |
| `a(-suffix)` | `a`, `a-suffix` | `ezdata-es(-dev)` → ezdata-es, ezdata-es-dev |
| `a/b` or `a/b/c` | `a`, `b`, `c` | `db/postgres` → db, postgres |
| `a(+x,y,z)`, items **not** names | `a` only (triggers `complete:false`) | `redis(+7,cluster,nodes)` → redis |
| `*-x` glob | nothing (triggers `complete:false`) | `*-redis(-dev)` → omit |

INFRA/ADMIN rows (reverse-proxy, admin UI, observability sidecar, CLI/init helper) are
excluded everywhere — the catalog's §1 legend says they are not backing services. Where a
parenthetical sibling is itself INFRA (`opensearch(+dashboards)`, `temporal(+admin,ui)`,
`minio(+setup)`), the base is kept and the sibling dropped.

## `complete: true` backing-service repos (14)

Every row is a plain name or an *enumerating* notation (`a(+b full name)`, `a/b full
names`) — nothing is hidden, so the list is exhaustive and scores precision.

| Repo | must_detect | Notable expansions (catalog row → names) |
|---|---|---|
| Checkmk/checkmk | oracle-12c, oracle-19c, oracle-free, oracle-perf, oracle-xe | 5 plain rows |
| Cloud-CV/EvalAI | db, memcached, sqs | plain (follows catalog: `memcached`, not the old example's `redis`) |
| Donkie/Spoolman | db | 3 image variants, all `Service = db` |
| aiidateam/aiida-core | database, postgres, messaging, rabbitmq, slurm | 5 plain rows |
| baserow/baserow | db, redis, s3mock | plain (celery-flower/mailhog/otel/caddy INFRA) |
| coderamp-labs/gitingest | minio | plain (`minio-setup` INFRA) |
| feast-dev/feast | etcd, milvus, minio, postgres, redis | plain (jaeger/kind/otel/prometheus INFRA) |
| jhao104/proxy_pool | proxy_redis | plain |
| mozilla/addons-server | autograph, elasticsearch, memcached, mysqld, rabbitmq, redis | plain (nginx INFRA) |
| pretix/pretix | postgres | plain (CI-only) |
| rq/rq | valkey | plain (CI-only) |
| sooperset/mcp-atlassian | confluence, jira, confluence-db, jira-db | `confluence-db/jira-db` → confluence-db, jira-db |
| tgoai/tgo | postgres, db, redis, wukongim | `postgres(+db)` → postgres, db (flower/adminer/redis-commander/nginx INFRA) |
| mlflow/mlflow | minio, storage, mssql, mysql, postgres, postgresql | `minio/storage` → minio, storage; `postgres(+postgresql)` → postgres, postgresql (`minio-create-bucket` INFRA) |

`mlflow` is now `complete: true`: after mechanical expansion its two compound rows
enumerate full names (no glob, no descriptor group). It was previously omitted, and then
under-reported (`postgres` only); both are corrected here.

## `complete: false` backing-service repos (8) — expansion + unrecoverable rows

Each `must_detect` includes every plainly-stated name; the unrecoverable rows (globs /
descriptor groups) that set the flag are listed.

| Repo | must_detect | Expansion notes & unrecoverable rows (→ why `complete:false`) |
|---|---|---|
| PostHog/posthog | clickhouse, db, elasticsearch, etcd, kafka, localstack, objectstorage, objectstorage-azure, opensearch, postgres, redis, seaweedfs, temporal, zookeeper | `objectstorage/seaweedfs` → objectstorage, seaweedfs; `db/postgres` → db, postgres; `opensearch(+dashboards)` → opensearch (dashboards INFRA). **Descriptor groups** (base only, set the flag): `kafka(+redpanda,console,init)` → kafka, `redis(+7,cluster,nodes)` → redis, `temporal(+admin,ui)` → temporal. |
| bruin-data/ingestr | bench-mongo-source, bench-mssql-dest, bench-mysql-source | **Glob**: `*-postgres (4x)` → omitted. |
| django-oauth/django-oauth-toolkit | mysql, postgres | **Descriptor groups**: `mysql(+primary,replica)` → mysql; `postgres(+primary,replica)` → postgres. |
| frappe/press | frankfurter, mariadb, postgres, redis-cache, redis-queue, redis-socketio | `redis-cache/queue/socketio` is a shared-prefix slash → redis-cache, redis-queue, redis-socketio (the compression required expanding the shared `redis-` prefix, so the repo is marked partial rather than treating `queue`/`socketio` as standalone keys). |
| polarsource/polar | db, localstack, minio, postgres, redis, tinybird | `db/postgres` → db, postgres. **Descriptor group**: `minio(+setup)` → minio (setup is an INFRA init container). |
| supabase/supabase-py | db, gotrue, rest | **Descriptor group**: `gotrue(+autoconfirm,disabled)` → gotrue (mail INFRA). |
| wecode-ai/Wegent | elasticsearch, mysql, redis | **Descriptor groups**: `mysql(+test)` → mysql; `redis(+test)` → redis (jaeger/kibana/otel INFRA). |
| xuwei95/ezdata | ezdata-es, ezdata-es-dev, ezdata-minio, ezdata-minio-dev, mysql-dev, ruoyi-mysql, ruoyi-pg | `ezdata-es(-dev)` → ezdata-es, ezdata-es-dev; `ezdata-minio(-dev)` → ezdata-minio, ezdata-minio-dev; mixed `*-db/mysql-dev/ruoyi-mysql` → mysql-dev, ruoyi-mysql (omit `*-db`); ruoyi-pg plain. **Globs**: `*-db`, `*-redis(-dev)` → omitted (`*-minio-init` INFRA). |

## Empty-list repos (10) — `complete: true`, `must_detect: []` — false-positive candidates

Compose/CI files but zero backing services, so any detector output is a scored false
positive. All named in the catalog (§0 false-positive dirs, §1 "zero backing" note); none
invented.

| Repo | Catalog basis |
|---|---|
| ArchipelagoMW/Archipelago | §1: nginx reverse-proxy only |
| BeehiveInnovations/pal-mcp-server | §1: self-build only |
| fastapi/typer | §1: self-build only |
| karlicoss/promnesia | §1: self-build only |
| nginx-proxy/nginx-proxy | §1: nginx/dockergen, no data store |
| OpenCTI-Platform/connectors | §0: connector images only → "nets to zero" |
| Qiskit/qiskit | §0: `*decompose*.yaml` release notes |
| Azure/azure-cli | §0: `compose` conversion test fixtures |
| containers/podman-compose | §0: parser test fixtures |
| testcontainers/testcontainers-python | §0: `DockerCompose` wrapper fixtures |

The ~23 remaining repos have no compose/CI file at all (§1) — no false positive possible,
and the catalog does not name them individually, so they are excluded (no invented keys).

## Summary

- **32 total oracle keys** = 22 backing-service repos (all of the catalog's 22) + 10 empty.
- **24 `complete: true`** (14 backing + 10 empty) → recall AND precision.
- **8 `complete: false`** → recall only; each `must_detect` is a documented known-true subset.
- **90 total `must_detect` names** (46 in complete backing repos + 44 in partial repos).
