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
from urllib.parse import urlparse

try:  # PyYAML is available; degrade gracefully if ever absent.
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from python_deps.depgraph.ids import service_id
from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType
from python_deps.depgraph.service_tables import services_for_package, service_defaults, KNOWN_SERVICE_KINDS, BROKER_CAPABLE_KINDS

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
    data.update({k: v for k, v in extra.items() if k in ("bound_config", "inducing_package")})
    p = data["port"]
    return Node(
        id=service_id(kind), type=NodeType.SERVICE, name=kind, layer=Layer.SERVICES,
        discovered_by=discovered_by, state=State.UNKNOWN,
        check_command=f"pg_isready -h {data['host']} -p {p}" if kind == "postgres"
                      else f"nc -z {data['host']} {p}" if p else None,
        fix_candidates=(f"service:{data['image']}",), chosen_fix=f"service:{data['image']}",
        evidence=evidence, provenance="service scan", data=data,
    )


def scan_services(repo_path: str, graph: DepGraph) -> DepGraph:
    """Append confidence-annotated SERVICE nodes (design 2026-06-25 §5). NEW graph."""
    compose = scan_compose_services(repo_path)
    ci, ci_present = scan_ci_services(repo_path)
    confirmed: dict[str, dict] = {**compose, **ci}           # CI wins on conflict

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
                inferred[kind].update({"bound_config": cfg.name, "host": host, "port": port})

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
