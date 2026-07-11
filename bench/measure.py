# bench/measure.py
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_COLLECT_ERR = re.compile(r"((?:[A-Za-z_][\w.]*)?(?:Error|Exception|Warning)):")


def _node_id(tc: ET.Element) -> str:
    cls = tc.get("classname") or ""
    name = tc.get("name") or ""
    return f"{cls}::{name}" if cls else name


def parse_junit(xml_text: str) -> dict:
    passed, failed, errors, skipped = [], [], [], []
    try:
        root = ET.fromstring(xml_text) if xml_text.strip() else None
    except ET.ParseError:
        root = None
    if root is not None:
        for tc in root.iter("testcase"):
            nid = _node_id(tc)
            if tc.find("failure") is not None:
                failed.append(nid)
            elif tc.find("error") is not None:
                errors.append(nid)
            elif tc.find("skipped") is not None:
                skipped.append(nid)
            else:
                passed.append(nid)
    return {
        "total": len(passed) + len(failed) + len(errors) + len(skipped),
        "passed": len(passed), "failed": len(failed), "errors": len(errors), "skipped": len(skipped),
        "passed_node_ids": tuple(passed), "failed_node_ids": tuple(failed),
        "error_node_ids": tuple(errors),
    }


def parse_collected_node_ids(stdout: str) -> tuple:
    return tuple(ln.strip() for ln in (stdout or "").splitlines() if "::" in ln)


def parse_collect(rc: int, stdout: str) -> dict:
    errs = []
    for ln in (stdout or "").splitlines():
        if _COLLECT_ERR.search(ln) and "::" not in ln:
            errs.append(ln.strip()[:200])
    return {"collect_clean": rc in (0, 5), "collect_errors": tuple(errs)}
