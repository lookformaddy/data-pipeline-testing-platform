"""JUnit-style and human-readable reporting for CI gate integration.

``to_junit`` emits a JUnit XML document (one ``testsuite`` per run, one
``testcase`` per test, ``failure`` elements for failures) that CI systems
consume directly. ``exit_code`` maps a run + gate decision onto the
process exit code a CI step should use: 0 pass, 1 gate failed, 2 infra
error (no run / no results).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .gates import EXIT_GATE_FAILED, EXIT_INFRA_ERROR, EXIT_PASS
from .models import DiffResult, TestRun


def to_junit(run: TestRun) -> str:
    """Render a TestRun as a JUnit XML document."""
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", {
        "name": f"{run.suite_name}@{run.suite_version}",
        "tests": str(len(run.results)),
        "failures": str(run.failed_count),
        "errors": "0",
        "time": f"{run.duration_ms / 1000.0:.3f}",
    })
    for result in run.results:
        case = ET.SubElement(suite, "testcase", {
            "classname": f"{run.suite_name}.{result.test_id}",
            "name": result.test_name,
            "time": f"{result.duration_ms / 1000.0:.3f}",
        })
        if not result.passed:
            failure = ET.SubElement(case, "failure", {
                "message": result.message,
                "type": result.kind.value,
            })
            failure.text = result.message
        if result.details:
            system_out = ET.SubElement(case, "system-out")
            system_out.text = json.dumps(result.details, sort_keys=True, default=str)
    ET.indent(suites)
    return ET.tostring(suites, encoding="unicode", xml_declaration=True)


def to_markdown(run: TestRun, diffs: list[DiffResult] | None = None) -> str:
    """Render a TestRun as a Markdown summary for PR comments / logs."""
    lines = [
        f"# Test run: {run.suite_name} v{run.suite_version}",
        "",
        f"- Run: `{run.run_id}` · stage `{run.stage.value}` · "
        f"fingerprint `{run.fingerprint[:12]}`",
        f"- Result: **{run.passed_count}/{len(run.results)} passed "
        f"({run.pass_rate:.0%})**",
        f"- Gate: {'PASS' if run.gate_passed else 'BLOCKED'}"
        + (f" — {'; '.join(run.gate_reasons)}" if run.gate_reasons else ""),
        "",
        "| Test | Kind | Severity | Result | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in run.results:
        mark = "✅" if r.passed else "❌"
        lines.append(f"| {r.test_id} | {r.kind.value} | {r.severity.value} | "
                     f"{mark} | {escape(r.message)} |")
    if diffs:
        lines += ["", "## Diffs"]
        for d in diffs:
            lines.append(
                f"- **{d.table}**: {d.total_diffs} diff(s) "
                f"(+{len(d.added)} added, −{len(d.removed)} removed, "
                f"~{len(d.changed)} changed) — "
                f"{'equal' if d.is_equal else 'MISMATCH'}"
            )
    return "\n".join(lines) + "\n"


def to_json(run: TestRun) -> str:
    return json.dumps(run.to_dict(), indent=2, sort_keys=True)


def exit_code(run: TestRun | None) -> int:
    """CI exit code for a run: 0 pass, 1 gate failed, 2 infra error."""
    if run is None or not run.results:
        return EXIT_INFRA_ERROR
    if run.gate_passed is False or run.gate_blocked:
        return EXIT_GATE_FAILED
    return EXIT_PASS
