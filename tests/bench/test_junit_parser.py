# tests/bench/test_junit_parser.py
from bench.measure import parse_junit

_XML = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
  <testcase classname="tests.test_a" name="test_ok"/>
  <testcase classname="tests.test_a" name="test_bad"><failure message="x">boom</failure></testcase>
  <testcase classname="tests.test_b" name="test_err"><error message="y">nope</error></testcase>
  <testcase classname="tests.test_b" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def test_counts_and_outcomes():
    r = parse_junit(_XML)
    assert (r["total"], r["passed"], r["failed"], r["errors"], r["skipped"]) == (4, 1, 1, 1, 1)


def test_node_ids_by_outcome():
    r = parse_junit(_XML)
    assert r["passed_node_ids"] == ("tests.test_a::test_ok",)
    assert r["failed_node_ids"] == ("tests.test_a::test_bad",)
    assert r["error_node_ids"] == ("tests.test_b::test_err",)


def test_empty_or_garbage_returns_zeroed():
    assert parse_junit("")["total"] == 0 and parse_junit("")["passed_node_ids"] == ()
    assert parse_junit("<not-xml")["total"] == 0
