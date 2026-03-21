"""
act.cli — Command-line interface and pipeline orchestrator.

Runs the full two-pass pipeline:
  Pass 1: Normalize Python code to explicit intermediate form
  Pass 2: Build graphs + run topology analysis
  Output: Structured findings to stdout or file

Usage:
  codetopo check path/to/file.py
  codetopo check path/to/dir/ --recursive
  codetopo check file.py --output json
  codetopo check file.py --config codetopo.toml
  codetopo check file.py --fail-on error   # only fail CI on errors (default)
  codetopo check file.py --fail-on warning # fail on warnings too
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path

from agentic_coding_topology.normalizer.python import normalize_file
from agentic_coding_topology.graph.builder import build_graphs
from agentic_coding_topology.topology.analyzer import TopologyAnalyzer, TopologyReport, Severity


def load_config(config_path: Path | None) -> dict:
    """Load codetopo.toml if present, else return empty config."""
    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            return tomllib.load(f).get("codetopo", {})

    # Auto-detect codetopo.toml in current directory
    local = Path("codetopo.toml")
    if local.exists():
        with open(local, "rb") as f:
            return tomllib.load(f).get("codetopo", {})

    return {}


def run_pipeline(path: Path, analyzer: TopologyAnalyzer) -> TopologyReport:
    """Full two-pass pipeline for a single file."""
    # Pass 1: Normalize
    module = normalize_file(str(path))

    # Pass 1.5: Build graphs
    graphs = build_graphs(module)

    # Pass 2: Topology analysis
    return analyzer.analyze(graphs)


def format_report_text(report: TopologyReport) -> str:
    """Human-readable report output."""
    lines = [report.summary()]

    if not report.findings:
        lines.append("  No findings.")
        return "\n".join(lines)

    # Group by severity
    for severity in [Severity.ERROR, Severity.WARNING, Severity.INFO]:
        findings = [f for f in report.findings if f.severity == severity]
        if not findings:
            continue
        lines.append(f"\n  {severity.value.upper()}S ({len(findings)}):")
        for f in findings:
            lines.append(f"    [{f.finding_type.value}] {f.message}")
            if f.source_lines:
                lines.append(f"      Lines: {f.source_lines}")
            lines.append(f"      Fix: {f.fix_suggestion}")

    # Metrics summary
    lines.append(f"\n  Metrics: {json.dumps(report.metrics, indent=4)}")
    return "\n".join(lines)


def format_report_json(report: TopologyReport) -> str:
    """JSON report for CI/agent consumption."""
    return json.dumps({
        "source_path": report.source_path,
        "passed": report.passed,
        "summary": report.summary(),
        "findings": [
            {
                "type": f.finding_type.value,
                "severity": f.severity.value,
                "message": f.message,
                "nodes": f.nodes,
                "source_lines": f.source_lines,
                "metric_value": f.metric_value,
                "metric_name": f.metric_name,
                "fix_suggestion": f.fix_suggestion,
            }
            for f in report.findings
        ],
        "metrics": report.metrics,
    }, indent=2)


def main():
    parser = argparse.ArgumentParser(
        prog="codetopo",
        description=(
            "Algebraic topology analysis for code quality. "
            "Two-pass pipeline: normalize → topology → findings."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="Analyze code files")
    check.add_argument("paths", nargs="+", type=Path,
                       help="Files or directories to analyze")
    check.add_argument("--recursive", "-r", action="store_true",
                       help="Recurse into directories")
    check.add_argument("--output", "-o", choices=["text", "json"], default="text",
                       help="Output format (default: text)")
    check.add_argument("--config", "-c", type=Path, default=None,
                       help="Path to codetopo.toml config")
    check.add_argument("--fail-on", choices=["error", "warning"],
                       default="error",
                       help="Severity level that causes non-zero exit")
    check.add_argument("--show-normalized", action="store_true",
                       help="Also print the normalized intermediate form")

    viz = sub.add_parser("viz", help="Visualize code topology as interactive HTML")
    viz.add_argument("path", type=Path, help="Python file to visualize")
    viz.add_argument("--output", "-o", type=Path, default=None,
                     help="Output HTML path (default: <file>.topology.html)")
    viz.add_argument("--config", "-c", type=Path, default=None,
                     help="Path to codetopo.toml config")
    viz.add_argument("--open", action="store_true",
                     help="Open in browser after generating")
    viz.add_argument("--json", action="store_true",
                     help="Output raw graph data as JSON instead of HTML")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "viz":
        from agentic_coding_topology.viz import extract_graph_data, generate_viz
        config = load_config(getattr(args, "config", None))

        if args.json:
            data = extract_graph_data(str(args.path), config)
            print(json.dumps(data, indent=2))
        else:
            out = generate_viz(str(args.path),
                               str(args.output) if args.output else None,
                               config)
            print(f"Visualization written to {out}", file=sys.stderr)
            if getattr(args, "open", False):
                import webbrowser
                webbrowser.open(f"file://{Path(out).resolve()}")
        sys.exit(0)

    # Load config
    config = load_config(getattr(args, "config", None))
    if getattr(args, "fail_on", "error") == "warning":
        config["fail_on_warning"] = True

    analyzer = TopologyAnalyzer(config=config)

    # Collect files
    files = []
    for path in args.paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            pattern = "**/*.py" if args.recursive else "*.py"
            files.extend(path.glob(pattern))
        else:
            print(f"Warning: {path} does not exist", file=sys.stderr)

    if not files:
        print("No Python files found.", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    reports = []
    any_failed = False

    for file_path in sorted(files):
        try:
            report = run_pipeline(file_path, analyzer)
            reports.append(report)

            if getattr(args, "show_normalized", False):
                from agentic_coding_topology.normalizer.python import normalize_file
                module = normalize_file(str(file_path))
                print(f"\n=== Normalized: {file_path} ===")
                for stmt in module.statements:
                    marker = " [intermediate]" if stmt.is_intermediate else ""
                    print(f"  {stmt.var_name} = {stmt.operation}"
                          f"  # L{stmt.source_line}{marker}")

            if args.output == "json":
                print(format_report_json(report))
            else:
                print(format_report_text(report))

            if not report.passed:
                any_failed = True

        except SyntaxError as e:
            print(f"Syntax error in {file_path}: {e}", file=sys.stderr)
            any_failed = True
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}", file=sys.stderr)
            any_failed = True

    # Exit code for CI
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
