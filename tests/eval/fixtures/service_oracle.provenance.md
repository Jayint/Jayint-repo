# Service-detection oracle — provenance

Ground truth for `service_oracle.json`. Every entry is transcribed **from
`.superpowers/sdd/ratbench-service-catalog.md` alone** — never by running
`build_service_nodes`. An oracle fitted to the detector would measure nothing.

The scorer (`scripts/eval_service_detection_fidelity.py`) compares the detector's
`ServiceNode.name` set against these lists, so the oracle lists **service names**
(the compose `services.<name>` key / CI `jobs.<job>.services.<name>` key), exactly as
the catalog's *Service* column records them — not kinds. That is why `rq/rq` is
`["valkey"]` (the CI service is *named* `valkey`), not `["redis"]` (its kind).

INFRA/ADMIN entries (reverse-proxy, admin UI, observability sidecar, CLI/init helper)
are **excluded** — the catalog flags them as "a real compose entry, but not a backing
service a test suite talks to" (§1 legend). If the detector emits one, that is a real
false-positive finding for Step 3 to log, not an oracle entry.

## Convention for the catalog's collapsed *Service* cells

- Plain cell (`db`, `minio`, `valkey`) → one service name, transcribed verbatim.
- `a(+b)` / `a(+b,c)` where `b`,`c` are **literal sibling service names**
  (`postgres(+db)`, `postgres(+postgresql)`) → include `a` and each named sibling
  (minus any INFRA sibling).
- `a/b` where the row's single image shows one role seen under two source-specific
  names → include both `a` and `b`.
- **Omit the whole repo** (recorded below) when any backing service's exact name is
  *not* recoverable: a glob (`*-postgres`), or a `(+variant)` whose variant is a
  descriptor (`+primary,replica`, `+test`, `+7,cluster,nodes`, `+autoconfirm,disabled`)
  rather than a spelled-out service key. A partial list would inject uncontrolled
  false positives (undetected variant nodes) that masquerade as detection bugs — 13
  honest repos beat 22 half-guessed ones.

## Included backing-service repos (13) — with catalog citations

| Repo | Names | Catalog rows (§1 table) |
|---|---|---|
| Checkmk/checkmk | oracle-12c, oracle-19c, oracle-free, oracle-perf, oracle-xe | 5 rows `Checkmk/checkmk \| compose \| oracle-1{2,9}c / oracle-{free,perf,xe} \| … \| oracle \| EXOTIC` |
| Cloud-CV/EvalAI | db, memcached, sqs | `sqs \| softwaremill/elasticmq`, `memcached \| memcached:1.6.15`, `db \| postgres:17.10` |
| Donkie/Spoolman | db | 3 rows all `Service = db` (cockroachdb / mariadb / postgres image variants) → one name `db` |
| aiidateam/aiida-core | database, postgres, messaging, rabbitmq, slurm | `database \| postgres:15`, `postgres \| postgres:10,12` (ci), `messaging \| rabbitmq`, `rabbitmq \| …` (ci), `slurm \| xenonmiddleware/slurm:17` |
| baserow/baserow | db, redis, s3mock | `db \| pgvector/pgvector…`, `redis \| redis:6…`, `s3mock \| adobe/s3mock:3.12.0` |
| coderamp-labs/gitingest | minio | `minio \| minio/minio:latest` (its `minio-setup \| minio/mc` is INFRA, excluded) |
| feast-dev/feast | etcd, milvus, minio, postgres, redis | `etcd`, `milvus`, `minio`, `postgres \| pgvector/pgvector:pg16`, `redis` rows |
| jhao104/proxy_pool | proxy_redis | `proxy_redis \| redis \| redis \| KNOWN` |
| mozilla/addons-server | autograph, elasticsearch, memcached, mysqld, rabbitmq, redis | `autograph`, `elasticsearch`, `memcached`, `mysqld \| mysql:8.4.10`, `rabbitmq`, `redis` rows (its `nginx` is INFRA) |
| pretix/pretix | postgres | `pretix/pretix \| ci \| postgres \| postgres:15` |
| rq/rq | valkey | `rq/rq \| ci \| valkey \| valkey/valkey:{matrix} \| redis \| ALIAS->redis` |
| sooperset/mcp-atlassian | confluence, jira, confluence-db, jira-db | `confluence \| atlassian/confluence`, `jira \| atlassian/jira-software`, `confluence-db/jira-db \| postgres:15-alpine` (two named db services) |
| tgoai/tgo | postgres, db, redis, wukongim | `postgres(+db) \| …`, `redis \| redis:7-alpine`, `wukongim \| wukongim:v2.2.5` (flower/adminer/redis-commander/nginx are INFRA) |

### Notes on interpretation calls above
- **Cloud-CV/EvalAI** deliberately follows the catalog (`memcached`), **not** the
  brief's illustrative JSON example, which shows `["db", "redis", "sqs"]`. The catalog
  has no `redis` for EvalAI and does have `memcached`; the brief states the catalog is
  ground truth and the JSON snippet is only "this exact shape".
- **aiida-core** keeps both `database` (compose) and `postgres` (CI), and both
  `messaging` (compose) and `rabbitmq` (CI): they are four distinct service *names*
  across two sources, so the detector emits four nodes.
- **tgo** `postgres(+db)` → both `postgres` and `db` are spelled-out names.
- **mcp-atlassian** `confluence-db/jira-db` → two fully-spelled db service names.

## Omitted backing-service repos (9) — ambiguous, per the convention above

The catalog counts **22** repos with ≥1 backing service (§2). These 9 are left out
because at least one backing service's exact name is not recoverable from the catalog
text. Recording the ambiguity here is required by the brief rather than guessing the
detector's answer.

| Repo | Why omitted (catalog cell) |
|---|---|
| PostHog/posthog | Heavily collapsed: `redis(+7,cluster,nodes)`, `kafka(+redpanda,console,init)`, `temporal(+admin,ui)`, `objectstorage/seaweedfs`, `db/postgres`, `objectstorage-azure` — most backing service keys are hidden behind descriptor variants. |
| bruin-data/ingestr | `*-postgres (4x)` is a glob — the four postgres service names are not given (only `bench-mongo-source`, `bench-mssql-dest`, `bench-mysql-source` are spelled out). |
| django-oauth/django-oauth-toolkit | `mysql(+primary,replica)`, `postgres(+primary,replica)` — the primary/replica service keys are descriptors, not literal names. |
| frappe/press | `redis-cache/queue/socketio` — three redis service names implied but the exact keys (separator/suffix) are not certain; `frankfurter`, `mariadb`, `postgres` are clear but a partial list would create FPs. |
| mlflow/mlflow | `minio/storage` is ambiguous — the row carries two images (`minio/minio`, `rustfs`), so it is unclear whether `minio` and `storage` are one service or two; `postgres(+postgresql)` is fine but the repo as a whole is not confidently transcribable. |
| polarsource/polar | `db/postgres` + `minio(+setup)` require source-vs-name inference; conservative omission (the slash-name reading is plausible but not certain enough to risk). |
| supabase/supabase-py | `gotrue(+autoconfirm,disabled)` — variant service keys hidden; would create FPs. |
| wecode-ai/Wegent | `mysql(+test)`, `redis(+test)` — the `-test` variant service keys are not spelled out. |
| xuwei95/ezdata | `ezdata-es(-dev)`, `ezdata-minio(-dev)`, `*-db/mysql-dev/ruoyi-mysql`, `*-redis(-dev)` — pervasive globs and `-dev` variants; exact keys unrecoverable. |

## Empty-list repos (10) — false-positive candidates

These declare compose/CI files but **zero backing services**, so an empty list scores
any detector false positive. All are named in the catalog (§0 false-positive dirs and
§1 "zero backing services" note); none are invented.

| Repo | Catalog basis |
|---|---|
| ArchipelagoMW/Archipelago | §1: "nginx reverse-proxy only" |
| BeehiveInnovations/pal-mcp-server | §1: "self-build only" |
| fastapi/typer | §1: "self-build only" |
| karlicoss/promnesia | §1: "self-build only" |
| nginx-proxy/nginx-proxy | §1: "nginx/dockergen — it *is* the reverse proxy under test … no data store" |
| OpenCTI-Platform/connectors | §0: 277 connector composes, each only its own `opencti/connector-*` image talking to an external URL → "nets to zero" |
| Qiskit/qiskit | §0: `*decompose*.yaml` release notes — not compose files |
| Azure/azure-cli | §0: static YAML test fixtures for the `compose` conversion command |
| containers/podman-compose | §0: `tests/integration/*` parser fixtures (trivial alpine/busybox/nginx) |
| testcontainers/testcontainers-python | §0: `compose_fixtures/*` for the library's own `DockerCompose` wrapper |

The remaining ~23 of the 50 repos "have no compose file and no CI `services:` block at
all" (§1). They cannot produce a false positive and the catalog does not name them
individually, so they are excluded (adding them would require inventing repo keys).

## Summary

- 13 backing-service repos included · 9 ambiguous repos omitted (of 22 in the catalog)
- 10 empty-list repos (false-positive candidates)
- **23 total oracle keys**
