"""
tests/unit/test_pipeline.py — Core pipeline tests.

Tests the full two-pass pipeline on known-bad agent code
and verifies the right findings are produced.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agentic_coding_topology.normalizer.python import normalize_python
from agentic_coding_topology.graph.builder import build_graphs
from agentic_coding_topology.topology.analyzer import TopologyAnalyzer, FindingType, Severity


# ============================================================
# Pass 1: Normalization tests
# ============================================================

def test_chained_call_decomposed():
    """Karpathy's complaint: fetch(url)[0] should become two statements."""
    source = "result = process(fetch(url)[0])"
    module = normalize_python(source)

    # Should have more than one statement after normalization
    assert len(module.statements) > 1, (
        "Chained call should decompose into multiple statements"
    )

    # The intermediate fetch result should appear as its own node
    ops = [s.operation for s in module.statements]
    assert any("fetch" in op for op in ops)
    assert any("[0]" in op or "0]" in op for op in ops)


def test_simple_assignment_unchanged():
    """Simple assignments should pass through without decomposition."""
    source = "x = 42"
    module = normalize_python(source)
    assert len(module.statements) == 1
    assert module.statements[0].var_name == "x"


def test_nested_calls_fully_decomposed():
    """Deep nesting should produce one statement per operation."""
    source = "result = a(b(c(x)))"
    module = normalize_python(source)
    # c(x), b(_t0), a(_t1) = 3 statements minimum
    assert len(module.statements) >= 3


def test_intermediate_variables_marked():
    """Variables introduced by normalization should be flagged."""
    source = "result = process(fetch(url)[0])"
    module = normalize_python(source)

    intermediates = [s for s in module.statements if s.is_intermediate]
    non_intermediates = [s for s in module.statements if not s.is_intermediate]

    assert len(intermediates) >= 1, "Should have intermediate variables"
    assert any(s.var_name == "result" for s in non_intermediates), (
        "Original target 'result' should not be marked intermediate"
    )


# ============================================================
# Pass 2: Topology analysis tests
# ============================================================

def test_structural_duplication_detected():
    """Two structurally identical functions should be flagged."""
    source = """
def fetch_first_user(client, query):
    return format_user(client.search(query)["results"][0])

def fetch_first_result(client, query):
    return format_result(client.search(query)["results"][0])
"""
    module = normalize_python(source)
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer()
    report = analyzer.analyze(graphs)

    dup_findings = report.by_type(FindingType.STRUCTURAL_DUPLICATION)
    assert len(dup_findings) >= 1, (
        f"Should detect structural duplication. Got findings: "
        f"{[f.finding_type.value for f in report.findings]}"
    )


def test_clean_code_passes():
    """Karpathy-style clean code should produce no errors."""
    source = """
def fetch_first_user_clean(client, query):
    search_results = client.search(query)
    results_list = search_results["results"]
    first_result = results_list[0]
    formatted = format_user(first_result)
    return formatted
"""
    module = normalize_python(source)
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer()
    report = analyzer.analyze(graphs)

    errors = report.errors()
    assert len(errors) == 0, (
        f"Clean code should have no errors. Got: "
        f"{[(e.finding_type.value, e.message) for e in errors]}"
    )


def test_circular_dependency_detected():
    """Circular function calls should be flagged as errors."""
    source = """
def a(x):
    return b(x)

def b(x):
    return a(x)
"""
    module = normalize_python(source)
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer()
    report = analyzer.analyze(graphs)

    cycle_findings = report.by_type(FindingType.CIRCULAR_DEPENDENCY)
    assert len(cycle_findings) >= 1, "Circular dependency should be detected"
    assert all(f.severity == Severity.ERROR for f in cycle_findings)


def test_report_has_fix_suggestions():
    """Every finding should include an actionable fix suggestion."""
    source = """
def bad(client, query):
    return fmt(client.search(query)["results"][0])

def also_bad(client, query):
    return fmt(client.search(query)["results"][0])
"""
    module = normalize_python(source)
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer()
    report = analyzer.analyze(graphs)

    for finding in report.findings:
        assert finding.fix_suggestion, (
            f"Finding {finding.finding_type.value} has no fix suggestion"
        )
        assert len(finding.fix_suggestion) > 20, (
            f"Fix suggestion too short: '{finding.fix_suggestion}'"
        )


def test_json_output_structure():
    """JSON output should have all required fields for agent consumption."""
    from agentic_coding_topology.cli import format_report_json
    from agentic_coding_topology.topology.analyzer import TopologyReport, Finding, Severity, FindingType
    import json

    report = TopologyReport(source_path="test.py", passed=True)
    output = json.loads(format_report_json(report))

    assert "source_path" in output
    assert "passed" in output
    assert "findings" in output
    assert "metrics" in output


# ============================================================
# Integration: full fixture file
# ============================================================

def test_fixture_file_finds_problems():
    """The agent_generated.py fixture should produce multiple findings."""
    fixture = Path(__file__).parent.parent / "fixtures" / "agent_generated.py"
    assert fixture.exists(), f"Fixture not found: {fixture}"

    module = normalize_python(fixture.read_text(), path=str(fixture))
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer()
    report = analyzer.analyze(graphs)

    assert len(report.findings) > 0, (
        "Agent-generated code fixture should produce topology findings"
    )
    print(f"\nFixture findings ({len(report.findings)}):")
    for f in report.findings:
        print(f"  [{f.severity.value}] {f.finding_type.value}: {f.message[:80]}")
