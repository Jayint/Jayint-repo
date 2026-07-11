# bench/measure.py
from __future__ import annotations

import xml.etree.ElementTree as ET


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
