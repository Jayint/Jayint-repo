"""Static Service-tier discovery (design 2026-06-25-services-tier-design.md).

Pure (no Executor, no network): reads the repo on disk + the in-progress graph,
and appends confidence-annotated ``SERVICE`` nodes. Sources: CI ``services:`` /
compose ``services:`` (confirmed), ``*_URL`` config schemes + ``package->service``
table (inferred). Structural ``Package->Service`` edges are emitted only for
confirmed services (no false necessary conditions).
"""

from __future__ import annotations

import os
import re as _re
from dataclasses import replace
from urllib.parse import urlparse

try:  # PyYAML is available; degrade gracefully if ever absent.
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from python_deps.depgraph.ids import service_id, syslib_id, config_id
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType
from python_deps.depgraph.service_tables import services_for_package, service_defaults, KNOWN_SERVICE_KINDS, BROKER_CAPABLE_KINDS

# Binding obligation (Option B): the in-image dev credential write + profile export the
# LLM runs (the HOW), persisted so every login shell sources the rewritten app DB var.
BIND_PROFILE_PATH = "/etc/profile.d/zz_service_bind.sh"
ALTER_USER_CMD = "runuser -u postgres -- psql -c \"ALTER USER postgres PASSWORD 'postgres'\""

# URL scheme -> canonical service kind.
_SCHEME_TO_KIND: dict[str, str] = {
    "postgres": "postgres", "postgresql": "postgres", "postgresql+psycopg2": "postgres",
    "mysql": "mysql", "mysql+pymysql": "mysql",
    "redis": "redis", "rediss": "redis",
    "mongodb": "mongo", "mongodb+srv": "mongo",
    "amqp": "rabbitmq", "amqps": "rabbitmq",
    "elasticsearch": "elasticsearch",
}


def service_from_url(value: str) -> tuple[str, str | None, int | None] | None:
    """`(kind, host, port)` for a service URL/scheme, or None if unknown."""
    if not value or "://" not in value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    kind = _SCHEME_TO_KIND.get(parsed.scheme.lower())
    if kind is None:
        return None
    host = parsed.hostname
    port = parsed.port if _has_port(parsed) else None
    return kind, host, port


def _has_port(parsed) -> bool:
    try:
        return parsed.port is not None
    except ValueError:
        return False


def _kind_of(service_name: str, image: str | None) -> str | None:
    """Recognize a service kind from its compose/CI name or image."""
    name = (service_name or "").lower()
    for kind in KNOWN_SERVICE_KINDS:
        if kind in name:
            return kind
    img = (image or "").lower()
    for kind in KNOWN_SERVICE_KINDS:
        if kind in img:
            return kind
    # common image aliases not equal to the kind token
    if "postgres" in img or "postgis" in img:
        return "postgres"
    return None


def _port_of(entry: dict) -> int | None:
    ports = entry.get("ports")
    if isinstance(ports, list) and ports:
        first = str(ports[0])
        tail = first.split(":")[-1].split("/")[0]
        return int(tail) if tail.isdigit() else None
    return None


def _services_from_yaml_doc(doc, source: str, out: dict[str, dict]) -> None:
    """Merge a parsed YAML doc's `services:` blocks into `out` (first kind wins)."""
    if not isinstance(doc, dict):
        return
    blocks = []
    if isinstance(doc.get("services"), dict):
        blocks.append(doc["services"])           # compose top-level
    for job in (doc.get("jobs") or {}).values() if isinstance(doc.get("jobs"), dict) else []:
        if isinstance(job, dict) and isinstance(job.get("services"), dict):
            blocks.append(job["services"])       # GitHub Actions job.services
    for block in blocks:
        for svc_name, entry in block.items():
            entry = entry if isinstance(entry, dict) else {}
            image = entry.get("image")
            kind = _kind_of(svc_name, image)
            if kind and kind not in out:
                out[kind] = {"image": image or "", "port": _port_of(entry), "source": source}


def _env_pairs(entry: dict):
    """Yield (KEY, VALUE) from a compose/CI service `environment:` (list or map form)."""
    env = entry.get("environment")
    if isinstance(env, dict):
        for k, v in env.items():
            if v is not None:
                yield str(k), str(v)
    elif isinstance(env, list):
        for item in env:
            s = str(item)
            if "=" in s:
                k, v = s.split("=", 1)
                yield k.strip(), v.strip()


def _bindings_from_yaml_doc(doc, out: dict[str, dict]) -> None:
    """Merge service-URL env bindings from a parsed compose/CI doc into `out` (first wins)."""
    if not isinstance(doc, dict):
        return
    blocks = []
    if isinstance(doc.get("services"), dict):
        blocks.append(doc["services"])
    for job in (doc.get("jobs") or {}).values() if isinstance(doc.get("jobs"), dict) else []:
        if isinstance(job, dict) and isinstance(job.get("services"), dict):
            blocks.append(job["services"])
    for block in blocks:
        for _svc_name, entry in block.items():
            entry = entry if isinstance(entry, dict) else {}
            for key, value in _env_pairs(entry):
                hit = service_from_url(value)
                if not hit:
                    continue
                kind, host, port = hit
                if kind in out:
                    continue
                out[kind] = {"var": key, "url": value, "host": host, "port": port,
                             "db": service_db_from_url(value) or "postgres"}


def _load_yaml(path: str):
    if yaml is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def scan_compose_services(repo_path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fname in os.listdir(repo_path) if os.path.isdir(repo_path) else []:
        low = fname.lower()
        if (low.startswith("docker-compose") or low.startswith("compose.")) and \
                low.endswith((".yml", ".yaml")):
            doc = _load_yaml(os.path.join(repo_path, fname))
            _services_from_yaml_doc(doc, f"docker-compose: {fname}", out)
    return out


def scan_ci_services(repo_path: str) -> tuple[dict[str, dict], bool]:
    out: dict[str, dict] = {}
    present = False
    wf_dir = os.path.join(repo_path, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        return out, present
    for fname in os.listdir(wf_dir):
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        doc = _load_yaml(os.path.join(wf_dir, fname))
        if isinstance(doc, dict) and isinstance(doc.get("jobs"), dict):
            for job in doc["jobs"].values():
                if isinstance(job, dict) and isinstance(job.get("services"), dict) and job["services"]:
                    present = True
        _services_from_yaml_doc(doc, f".github/workflows/{fname}", out)
    return out, present


def scan_env_bindings(repo_path: str) -> dict[str, dict]:
    """Discover `KEY=<service-url>` bindings from compose + CI `environment:` blocks."""
    out: dict[str, dict] = {}
    for fname in os.listdir(repo_path) if os.path.isdir(repo_path) else []:
        low = fname.lower()
        if (low.startswith("docker-compose") or low.startswith("compose.")) and \
                low.endswith((".yml", ".yaml")):
            _bindings_from_yaml_doc(_load_yaml(os.path.join(repo_path, fname)), out)
    wf_dir = os.path.join(repo_path, ".github", "workflows")
    if os.path.isdir(wf_dir):
        for fname in os.listdir(wf_dir):
            if fname.lower().endswith((".yml", ".yaml")):
                _bindings_from_yaml_doc(_load_yaml(os.path.join(wf_dir, fname)), out)
    return out


# Ordered (signature regex -> kind). First match wins.
_ERROR_SIGNATURES: tuple[tuple[object, str], ...] = (
    (_re.compile(r"could not connect to server|psycopg2\.OperationalError|connection to server at", _re.I), "postgres"),
    (_re.compile(r"redis(\.exceptions)?\.ConnectionError|Error \d+ connecting to .*redis", _re.I), "redis"),
    (_re.compile(r"pymongo\.errors\.(ServerSelectionTimeoutError|ConnectionFailure)", _re.I), "mongo"),
    (_re.compile(r"amqp|pika\.exceptions|AMQPConnectionError|rabbitmq", _re.I), "rabbitmq"),
    (_re.compile(r"OperationalError.*MySQL|Can't connect to MySQL server", _re.I), "mysql"),
)


def classify_service_error(text: str) -> str | None:
    """Map a connection-failure log to a service kind (failure-driven primitive)."""
    if not text:
        return None
    for pattern, kind in _ERROR_SIGNATURES:
        if pattern.search(text):
            return kind
    return None


def _service_node(kind: str, *, confidence: str, discovered_by: DiscoveredBy,
                  evidence: str, extra: dict) -> Node:
    image, port = service_defaults(kind) if kind in KNOWN_SERVICE_KINDS else (f"{kind}:latest", None)
    data = {"service_confidence": confidence, "image": extra.get("image") or image,
            "host": extra.get("host") or kind, "port": extra.get("port") or port}
    data.update({k: v for k, v in extra.items()
                 if k in ("bound_config", "inducing_package", "bound_config_url", "db")})
    p = data["port"]
    return Node(
        id=service_id(kind), type=NodeType.SERVICE, name=kind, layer=Layer.SERVICES,
        discovered_by=discovered_by, state=State.UNKNOWN,
        check_command=f"pg_isready -h {data['host']} -p {p}" if kind == "postgres"
                      else f"nc -z {data['host']} {p}" if p else None,
        fix_candidates=(f"service:{data['image']}",), chosen_fix=f"service:{data['image']}",
        evidence=evidence, provenance="service scan", data=data,
    )


def scan_services(repo_path: str, graph: DepGraph, *, bind_env: bool = False) -> DepGraph:
    """Append confidence-annotated SERVICE nodes (design 2026-06-25 §5). NEW graph.

    ``bind_env`` (arm ``v1gsps``, default off) gates ONLY the NEW compose/CI
    ``environment:`` DB-URL absorption onto confirmed service nodes; off it,
    this stage is byte-identical to the pre-binding behaviour (the always-on
    inferred CONFIG-URL binding path below is unaffected).
    """
    compose = scan_compose_services(repo_path)
    ci, ci_present = scan_ci_services(repo_path)
    confirmed: dict[str, dict] = {**compose, **ci}           # CI wins on conflict
    env_bindings = scan_env_bindings(repo_path) if bind_env else {}

    packages = [n for n in graph.nodes if n.type is NodeType.PACKAGE]
    project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    test = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    anchor = project or test

    # Inferred: package->service, and *_URL config schemes.
    inferred: dict[str, dict] = {}
    for pkg in packages:
        for kind in services_for_package(pkg.name):
            inferred.setdefault(kind, {"inducing_package": pkg.name})
    for cfg in [n for n in graph.nodes if n.type is NodeType.CONFIG]:
        for fix in cfg.fix_candidates:
            url = fix.split("=", 1)[1] if "=" in fix else ""
            hit = service_from_url(url)
            if hit:
                kind, host, port = hit
                inferred.setdefault(kind, {})
                inferred[kind].update({"bound_config": cfg.name, "host": host, "port": port,
                                       "bound_config_url": url})

    new = graph

    # Confirmed nodes (+ Package->Service edges where a package maps to the kind).
    for kind, meta in confirmed.items():
        # Absorb a config-URL binding discovered for this kind (e.g. CELERY_BROKER_URL
        # -> redis) so the confirmed node surfaces `addresses: <VAR>` in the advisory.
        # Compose/CI image+port stay authoritative; only bound_config is added.
        extra = dict(meta)
        binding = inferred.get(kind)
        if binding and "bound_config" in binding:
            extra["bound_config"] = binding["bound_config"]
        if binding and binding.get("bound_config_url"):
            extra["bound_config_url"] = binding["bound_config_url"]
        env_binding = env_bindings.get(kind)
        if env_binding:                          # compose `environment:` is authoritative
            extra["bound_config"] = env_binding["var"]
            extra["bound_config_url"] = env_binding["url"]
            extra["db"] = env_binding["db"]
        node = _service_node(kind, confidence="confirmed",
                             discovered_by=DiscoveredBy.STATIC_SCAN,
                             evidence=meta.get("source", "ci/compose"), extra=extra)
        new = new.with_node(node)
        owners = [p for p in packages if kind in services_for_package(p.name)]
        for owner in owners:
            new = new.with_edge(Edge(src=owner.id, dst=node.id,
                                     relation=EdgeType.REQUIRES, origin="service"))
        if not owners and anchor is not None:
            new = new.with_edge(Edge(src=anchor.id, dst=node.id,
                                     relation=EdgeType.REQUIRES, origin="service"))

    # A generic `broker` (celery/kombu) is satisfied by any concrete broker-capable
    # service already present (confirmed or inferred) — don't emit a redundant shadow.
    broker_capable_present = (set(confirmed) | set(inferred)) & BROKER_CAPABLE_KINDS

    # Inferred nodes (no requires edge; suppressed when CI is authoritative & absent it).
    for kind, meta in inferred.items():
        if kind in confirmed:
            continue
        if kind == "broker" and broker_capable_present:
            continue   # abstract broker already covered by a concrete redis/rabbitmq
        if ci_present and kind not in ci:
            continue   # CI services: is authoritative — a guess it omits is dropped
        ev = (f"inferred from package {meta['inducing_package']}" if "inducing_package" in meta
              else f"inferred from {meta.get('bound_config', 'config')} URL")
        new = new.with_node(_service_node(kind, confidence="inferred",
                            discovered_by=DiscoveredBy.RESOLVER, evidence=ev, extra=meta))

    return new


def service_db_from_url(value: str) -> str | None:
    """The database name from a service URL path (``postgres://h/appdb`` -> ``appdb``)."""
    if not value or "://" not in value:
        return None
    try:
        path = urlparse(value).path
    except ValueError:
        return None
    name = (path or "").lstrip("/").strip()
    return name or None


def service_bind_url(scheme: str, port: int, db: str) -> str:
    """Uniform in-image Postgres URL (Option B): our creds + loopback host, app's scheme+db.

    The app's original scheme (incl. dialect suffix like ``postgresql+psycopg2``) is
    preserved so SQLAlchemy's driver selection is unchanged; only host/credentials are
    rewritten to the in-image instance configured by the binding obligation.
    """
    return f"{scheme}://postgres:postgres@127.0.0.1:{port}/{db}"


def postgres_start_recipe(port: int, db: str | None) -> dict:
    """Root-safe, runtime-version-resolved in-image Postgres start recipe.

    The cluster version is resolved at runtime from ``/etc/postgresql`` so no
    static ``<ver>`` (or base-image lookup) is needed; the daemon runs as the
    ``postgres`` user (the container is uid 0 and Postgres refuses to run as root).
    """
    start = (
        'PG_VER="$(ls /etc/postgresql 2>/dev/null | head -1)"; '
        'runuser -u postgres -- pg_ctlcluster "$PG_VER" main start'
    )
    wait = (
        f"for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p {port} && break; "
        "sleep 1; done"
    )
    createdb = f"runuser -u postgres -- createdb {db}" if (db and db != "postgres") else None
    return {
        "system_package": "postgresql",
        "start": start,
        "wait": wait,
        "createdb": createdb,                    # FATAL when present (no `|| true`)
        "certify": f"pg_isready -h 127.0.0.1 -p {port}",
        "port": port,
        "db": db,
    }


def attach_in_image_provisioning(graph: DepGraph, *, enabled: bool) -> DepGraph:
    """Promote each CONFIRMED postgres SERVICE to an in-image obligation.

    Adds a ``SystemLib(postgresql)`` prereq (emit bakes ``apt install postgresql``),
    a ``Service->SystemLib`` requires edge, rewrites the service ``check_command``
    to a loopback probe, and attaches ``data["start_recipe"]``. ``enabled=False``
    returns the graph unchanged (off-state byte-identity). NEW graph.
    """
    if not enabled:
        return graph
    new = graph
    for svc in [n for n in graph.nodes if n.type is NodeType.SERVICE]:
        if svc.name != "postgres" or svc.data.get("service_confidence") != "confirmed":
            continue
        port = svc.data.get("port") or 5432
        db = svc.data.get("db") or service_db_from_url(svc.data.get("bound_config_url", ""))
        recipe = postgres_start_recipe(port, db)
        sysl_id = syslib_id("postgresql")
        if new.get(sysl_id) is None:
            new = new.with_node(Node(
                id=sysl_id, type=NodeType.SYSTEM_LIB, name="postgresql",
                layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
                state=State.UNKNOWN, check_command="command -v pg_ctlcluster",
                fix_candidates=("apt:postgresql",), chosen_fix="apt:postgresql",
                evidence="in-image server for confirmed service postgres",
                provenance="service provision",
            ))
        # Rewrite the service node: loopback probe + recipe in data.
        new_data = dict(svc.data)
        new_data["start_recipe"] = recipe
        new = new.with_node(replace(
            svc, check_command=recipe["certify"], data=new_data,
        ))
        new = new.with_edge(Edge(src=svc.id, dst=sysl_id,
                                 relation=EdgeType.REQUIRES, origin="service"))

        # Bind the app's DB env var to the in-image instance (Option B). The
        # rewritten value keeps the app's scheme (driver fidelity); the psql
        # check_command uses the base ``postgresql`` scheme (psql rejects dialect
        # suffixes). The host owns truth — this node flips SATISFIED only when the
        # host runs the psql probe (anti-hollow); the recipe is the LLM-run HOW.
        var = svc.data.get("bound_config")
        if var:
            scheme = "postgresql"
            orig = svc.data.get("bound_config_url") or ""
            if "://" in orig:
                scheme = orig.split("://", 1)[0]
            bind_db = svc.data.get("db") or service_db_from_url(orig) or "postgres"
            app_url = service_bind_url(scheme, port, bind_db)
            probe_url = service_bind_url("postgresql", port, bind_db)
            bind_profile = f"echo 'export {var}=\"{app_url}\"' > {BIND_PROFILE_PATH}"
            bnode = Node(
                id=config_id(var), type=NodeType.CONFIG, name=var, layer=Layer.CONFIG,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
                check_command=f'test -n "${var}" && psql "{probe_url}" -c "select 1"',
                fix_candidates=(f"env:{var}={app_url}",), chosen_fix=f"env:{var}={app_url}",
                evidence=f"bind {var} to in-image postgres", provenance="service binding",
                data={"binding": True, "bind_recipe": {
                    "var": var, "url": app_url,
                    "alter_user": ALTER_USER_CMD, "bind_profile": bind_profile}},
            )
            new = new.with_node(bnode)
            new = new.with_edge(Edge(src=bnode.id, dst=svc.id,
                                     relation=EdgeType.REQUIRES, origin="service"))
    return new
