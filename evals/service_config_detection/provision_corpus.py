"""Labeled corpus of service-provisioning cases (ground truth for stages 3/5/6/7).

Each `ServiceCase` pairs a compose-service YAML block with the ground-truth
expectation: does a correctly-generated local-daemon setup provision it? 14 cases are
ported verbatim from the PoC's `DIVERSE` batch (broad SQL/NoSQL/graph/search/vector/
coordination/secrets/mail/aws-mock coverage, all provisionable, no known failure). The
remaining 4 are the PoC certify set's adversary/arch cases (redis + qdrant + the two
known-broken setups: memcached needs root privileges the certify container lacks,
milvus's "standalone binary" is an LLM hallucination — no such release artifact
exists).

Labels are GROUND TRUTH, transcribed verbatim from the task brief — not re-derived.

Pure data module: no Docker, no model, nothing imported from the pipeline except a
read-only `service_tables.KNOWN_SERVICE_KINDS` membership check in the test suite.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceCase:
    """One labeled provisioning case.

    Attributes:
        name: short id, e.g. "mariadb".
        kind: service kind (known kinds match `service_tables.KNOWN_SERVICE_KINDS`;
            exotic kinds are the image basename, e.g. "weaviate").
        compose_entry: the compose service YAML block, verbatim (from the PoC's
            DIVERSE dict, or the 4 PoC certify blocks).
        expect: "provisionable" | "non_provisionable".
        expected_probe_family: "tcp"|"http"|"pg"|"mysql"|"redis"|"cql"|"etcdctl".
        known_failure: "arch" | "root" | "hallucination" | "none".
    """

    name: str
    kind: str
    compose_entry: str
    expect: str
    expected_probe_family: str
    known_failure: str


PROVISION_CASES: tuple[ServiceCase, ...] = (
    # ---- 14 cases ported verbatim from the PoC's DIVERSE dict -----------------------
    ServiceCase(
        name="mariadb",
        kind="mysql",
        compose_entry="""
image: mariadb:11
environment: {MARIADB_DATABASE: app, MARIADB_USER: app, MARIADB_PASSWORD: secret, MARIADB_ROOT_PASSWORD: root}
ports: ["3306:3306"]
healthcheck: {test: ["CMD", "healthcheck.sh", "--connect"]}
""",
        expect="provisionable",
        expected_probe_family="mysql",
        known_failure="none",
    ),
    ServiceCase(
        name="couchdb",
        kind="couchdb",
        compose_entry="""
image: couchdb:3
environment: {COUCHDB_USER: admin, COUCHDB_PASSWORD: secret}
ports: ["5984:5984"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="cockroachdb",
        kind="cockroachdb",
        compose_entry="""
image: cockroachdb/cockroach:v23.2.0
command: start-single-node --insecure
ports: ["26257:26257"]
healthcheck: {test: ["CMD", "curl", "-f", "http://localhost:8080/health"]}
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="neo4j",
        kind="neo4j",
        compose_entry="""
image: neo4j:5
environment: {NEO4J_AUTH: neo4j/password}
ports: ["7687:7687"]
""",
        expect="provisionable",
        expected_probe_family="tcp",
        known_failure="none",
    ),
    ServiceCase(
        name="cassandra",
        kind="cassandra",
        compose_entry="""
image: cassandra:5
ports: ["9042:9042"]
healthcheck: {test: ["CMD", "cqlsh", "-e", "describe keyspaces"]}
""",
        expect="provisionable",
        expected_probe_family="cql",
        known_failure="none",
    ),
    ServiceCase(
        name="meilisearch",
        kind="meilisearch",
        compose_entry="""
image: getmeili/meilisearch:v1.7
environment: {MEILI_MASTER_KEY: masterKey}
ports: ["7700:7700"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="opensearch",
        kind="opensearch",
        compose_entry="""
image: opensearchproject/opensearch:2.13.0
environment: {discovery.type: single-node, DISABLE_SECURITY_PLUGIN: "true", OPENSEARCH_JAVA_OPTS: "-Xms512m -Xmx512m"}
ports: ["9200:9200"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="weaviate",
        kind="weaviate",
        compose_entry="""
image: semitechnologies/weaviate:1.24.0
environment: {AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true", PERSISTENCE_DATA_PATH: /var/lib/weaviate}
ports: ["8080:8080"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="chroma",
        kind="chroma",
        compose_entry="""
image: chromadb/chroma:0.5.0
ports: ["8000:8000"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="etcd",
        kind="etcd",
        compose_entry="""
image: quay.io/coreos/etcd:v3.5.12
command: etcd --advertise-client-urls http://0.0.0.0:2379 --listen-client-urls http://0.0.0.0:2379
ports: ["2379:2379"]
""",
        expect="provisionable",
        expected_probe_family="etcdctl",
        known_failure="none",
    ),
    ServiceCase(
        name="vault",
        kind="vault",
        compose_entry="""
image: hashicorp/vault:1.15
environment: {VAULT_DEV_ROOT_TOKEN_ID: root}
ports: ["8200:8200"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="mailpit",
        kind="mailpit",
        compose_entry="""
image: axllent/mailpit:v1.15
ports: ["1025:1025", "8025:8025"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    ServiceCase(
        name="keydb",
        kind="redis",
        compose_entry="""
image: eqalpha/keydb:latest
ports: ["6379:6379"]
""",
        expect="provisionable",
        expected_probe_family="redis",
        known_failure="none",
    ),
    ServiceCase(
        name="localstack",
        kind="localstack",
        compose_entry="""
image: localstack/localstack:3.4
environment: {SERVICES: s3,sqs}
ports: ["4566:4566"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="none",
    ),
    # ---- 4 cases from the PoC certify set (adversaries + the arch case) ------------
    ServiceCase(
        name="redis",
        kind="redis",
        compose_entry="""# redis
image: redis:7
ports: ["6379:6379"]
""",
        expect="provisionable",
        expected_probe_family="redis",
        known_failure="none",
    ),
    ServiceCase(
        name="qdrant",
        kind="qdrant",
        compose_entry="""# qdrant
image: qdrant/qdrant:v1.9.0
ports: ["6333:6333"]
""",
        expect="provisionable",
        expected_probe_family="http",
        known_failure="arch",
    ),
    ServiceCase(
        name="memcached",
        kind="memcached",
        compose_entry="""# memcached
image: memcached:1.6
ports: ["11211:11211"]
""",
        expect="non_provisionable",
        expected_probe_family="tcp",
        known_failure="root",
    ),
    ServiceCase(
        name="milvus",
        kind="milvus",
        compose_entry="""# milvus
image: milvusdb/milvus:v2.4.0
ports: ["19530:19530"]
""",
        expect="non_provisionable",
        expected_probe_family="http",
        known_failure="hallucination",
    ),
)


# --------------------------------------------------------------------------------------
# Synthetic clean-shape fixtures for future stages (no new machinery — plain dicts).
# --------------------------------------------------------------------------------------

# Stage 4 will assert `normalize_probe` rewrites a curl-based healthcheck to `nc -z`.
CURL_PROBE_CASE: dict = {
    "raw_probe": "curl -f http://localhost:8080/health",
    "port": 8080,
}

# Stage 6 will assert a non-postgres DSN's host is rewritten to localhost/127.0.0.1.
NON_POSTGRES_DSN_REPOINT_CASE: dict = {
    "dsn": "redis://redis:6379/0",
    "var": "CACHE_URL",
    "kind": "redis",
    "expected_localhost": "redis://127.0.0.1:6379/0",
}
