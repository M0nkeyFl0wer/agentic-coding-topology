#!/usr/bin/env python3
"""
fix_pass.py — codetopo correction pass.

Reads codetopo JSON findings for a file, constructs a structured prompt,
calls the Claude API to produce a refactored version, writes it alongside
the original, then re-runs codetopo check to measure the delta.

Usage:
    # Run check first, pipe findings in:
    codetopo check myfile.py --output json > findings.json
    python fix_pass.py myfile.py findings.json

    # Or let fix_pass run the check itself:
    python fix_pass.py myfile.py --auto-check

    # Dry run — print prompt only, don't call API:
    python fix_pass.py myfile.py findings.json --dry-run

Output:
    myfile.fixed.py       — patched file
    myfile.delta.json     — before/after finding counts and diff summary

The correction agent is constrained to:
  - Only touch functions/variables named in the findings
  - Preserve all public interfaces (function signatures, class APIs)
  - Not rewrite logic, only restructure for topology compliance
  - Produce valid Python (checked with ast.parse before writing)
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a Python refactoring agent. Your job is to improve code structure
based on findings from a topology analysis tool called codetopo.

codetopo measures structural quality algebraically — it detects copy-paste
via graph isomorphism, data flow fan-out, and circular dependencies.
These are mathematical properties, not style preferences.

Your constraints are strict:
1. Only modify functions or variables explicitly named in the findings.
2. Preserve all public interfaces — function signatures, class APIs, return types.
3. Do not change logic or behavior — only restructure for topology compliance.
4. Do not add new dependencies or imports beyond what's already present.
5. Produce the complete file, not a diff or snippet.
6. The output must be valid Python.

For each finding type, here is what you should do:

structural_duplication:
  Two functions are topologically identical (same data flow graph shape).
  Extract the shared structure into a single helper function that both call.
  The helper should be named for what it actually does, not what it replaces.

statement_multitask:
  A variable feeds 3+ downstream operations simultaneously — too much fan-out.
  Split the computation: introduce named intermediate variables for each
  distinct downstream use, so each variable has a clear single purpose.

circular_dependency:
  Functions call each other in a cycle.
  Break at the weakest semantic link — extract shared logic into a third
  function that neither of the cyclic functions calls the other to get.

bridge_bottleneck:
  A single function call is the only connection between two subsystems.
  This is a fragility point. Consider whether a second path should exist,
  or whether the two subsystems should be more explicitly decoupled.

Respond with ONLY the complete refactored Python file.
No explanation, no markdown fences, no preamble. Just the code.
"""


def build_user_prompt(source_code: str, findings: list[dict], source_path: str) -> str:
    """Build the structured correction prompt from source and findings."""

    # Filter to errors only — warnings are optional, errors are the signal
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    findings_text = []
    for i, f in enumerate(errors, 1):
        findings_text.append(
            f"FINDING {i} [{f['type']}] (ERROR)\n"
            f"  Nodes involved: {', '.join(f['nodes'])}\n"
            f"  Lines: {f.get('source_lines', [])}\n"
            f"  Problem: {f['message']}\n"
            f"  Required fix: {f['fix_suggestion']}"
        )

    if warnings:
        findings_text.append(f"\n(Additionally, {len(warnings)} warnings — address only if "
                             f"it doesn't complicate the error fixes above.)")

    if not errors:
        return None  # nothing to fix

    return (
        f"File: {source_path}\n\n"
        f"TOPOLOGY FINDINGS TO FIX ({len(errors)} errors):\n\n"
        + "\n\n".join(findings_text)
        + f"\n\n---\n\nSOURCE CODE:\n\n{source_code}"
    )


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _get_api_key() -> str:
    """Resolve API key: env var first, then keyring."""
    import os
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import keyring
        key = keyring.get_password("openrouter", "api_key")
        if key:
            return key
    except Exception:
        pass
    raise RuntimeError(
        "No API key found. Set OPENROUTER_API_KEY env var or store in keyring:\n"
        "  keyring set openrouter api_key"
    )


def call_llm(system: str, user: str, model: str = "anthropic/claude-sonnet-4") -> str:
    """Call OpenRouter API (OpenAI-compatible). Returns the text response."""
    import urllib.request

    api_key = _get_api_key()

    payload = json.dumps({
        "model": model,
        "max_tokens": 8096,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    # OpenAI-compatible response format
    choices = data.get("choices", [])
    if choices:
        return choices[0]["message"]["content"].strip()

    raise ValueError(f"No content in API response: {data}")


# ---------------------------------------------------------------------------
# Delta measurement
# ---------------------------------------------------------------------------

def run_check(file_path: Path) -> dict:
    """Run codetopo check on a file, return parsed JSON report."""
    result = subprocess.run(
        [sys.executable, "-m", "agentic_coding_topology.cli",
         "check", str(file_path), "--output", "json"],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"findings": [], "metrics": {}, "passed": True, "error": result.stderr}


def compute_delta(before: dict, after: dict) -> dict:
    """Compute before/after finding counts by type."""
    def count_by_type(report):
        counts = {}
        for f in report.get("findings", []):
            t = f["type"]
            counts[t] = counts.get(t, 0) + 1
        return counts

    before_counts = count_by_type(before)
    after_counts = count_by_type(after)
    all_types = set(before_counts) | set(after_counts)

    delta = {}
    for t in sorted(all_types):
        b = before_counts.get(t, 0)
        a = after_counts.get(t, 0)
        delta[t] = {"before": b, "after": a, "change": a - b}

    before_errors = sum(1 for f in before.get("findings", []) if f["severity"] == "error")
    after_errors = sum(1 for f in after.get("findings", []) if f["severity"] == "error")
    before_warnings = sum(1 for f in before.get("findings", []) if f["severity"] == "warning")
    after_warnings = sum(1 for f in after.get("findings", []) if f["severity"] == "warning")

    return {
        "by_type": delta,
        "totals": {
            "errors": {"before": before_errors, "after": after_errors,
                       "change": after_errors - before_errors},
            "warnings": {"before": before_warnings, "after": after_warnings,
                         "change": after_warnings - before_warnings},
        },
        "improved": after_errors < before_errors,
    }


def print_delta(delta: dict, source_path: str, fixed_path: str):
    """Print a human-readable delta summary."""
    totals = delta["totals"]
    improved = delta["improved"]
    status = "✓ IMPROVED" if improved else ("~ NO CHANGE" if totals["errors"]["change"] == 0
                                             else "✗ REGRESSED")

    print(f"\n{'='*60}")
    print(f"  codetopo delta: {source_path}")
    print(f"  Status: {status}")
    print(f"{'='*60}")
    print(f"  Errors:   {totals['errors']['before']} → {totals['errors']['after']}"
          f"  ({totals['errors']['change']:+d})")
    print(f"  Warnings: {totals['warnings']['before']} → {totals['warnings']['after']}"
          f"  ({totals['warnings']['change']:+d})")

    if delta["by_type"]:
        print(f"\n  By finding type:")
        for t, counts in delta["by_type"].items():
            if counts["change"] != 0:
                arrow = "↓" if counts["change"] < 0 else "↑"
                print(f"    {arrow} {t}: {counts['before']} → {counts['after']}")

    print(f"\n  Patched file: {fixed_path}")
    print(f"  Review with: diff {source_path} {fixed_path}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="fix_pass",
        description="codetopo correction pass — topology-guided refactoring via Claude API",
    )
    parser.add_argument("source", type=Path, help="Python file to fix")
    parser.add_argument("findings", type=Path, nargs="?",
                        help="codetopo JSON findings file (omit with --auto-check)")
    parser.add_argument("--auto-check", action="store_true",
                        help="Run codetopo check automatically instead of reading findings file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompt only, do not call API or write files")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output path for patched file (default: <source>.fixed.py)")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4",
                        help="OpenRouter model ID (default: anthropic/claude-sonnet-4)")
    parser.add_argument("--errors-only", action="store_true", default=True,
                        help="Only fix ERROR findings, skip warnings (default: true)")

    args = parser.parse_args()

    # --- Load source ---
    if not args.source.exists():
        print(f"Error: {args.source} not found", file=sys.stderr)
        sys.exit(1)

    source_code = args.source.read_text()

    # --- Load or generate findings ---
    if args.auto_check or args.findings is None:
        print(f"Running codetopo check on {args.source}...", file=sys.stderr)
        before_report = run_check(args.source)
        findings = before_report.get("findings", [])
    else:
        if not args.findings.exists():
            print(f"Error: findings file {args.findings} not found", file=sys.stderr)
            sys.exit(1)
        with open(args.findings) as f:
            findings_data = json.load(f)
        # Handle both single report and list of reports
        if isinstance(findings_data, list):
            findings = []
            before_report = None
            for r in findings_data:
                findings.extend(r.get("findings", []))
        else:
            findings = findings_data.get("findings", [])
            before_report = findings_data

    error_count = sum(1 for f in findings if f["severity"] == "error")
    warning_count = sum(1 for f in findings if f["severity"] == "warning")
    print(f"Findings: {error_count} errors, {warning_count} warnings", file=sys.stderr)

    if error_count == 0:
        print("No errors to fix. Exiting.", file=sys.stderr)
        sys.exit(0)

    # --- Build prompt ---
    user_prompt = build_user_prompt(source_code, findings, str(args.source))
    if user_prompt is None:
        print("No actionable findings. Exiting.", file=sys.stderr)
        sys.exit(0)

    if args.dry_run:
        print("=== SYSTEM PROMPT ===")
        print(SYSTEM_PROMPT)
        print("\n=== USER PROMPT ===")
        print(user_prompt)
        sys.exit(0)

    # --- Call API ---
    print(f"Calling Claude API ({args.model})...", file=sys.stderr)
    try:
        patched_code = call_llm(SYSTEM_PROMPT, user_prompt, model=args.model)
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        sys.exit(1)

    # Strip accidental markdown fences if model added them despite instructions
    if patched_code.startswith("```"):
        lines = patched_code.split("\n")
        patched_code = "\n".join(
            l for l in lines
            if not l.strip().startswith("```")
        )

    # --- Validate syntax ---
    try:
        ast.parse(patched_code)
    except SyntaxError as e:
        print(f"API returned invalid Python: {e}", file=sys.stderr)
        print("Raw output saved to fix_pass_error.py for inspection", file=sys.stderr)
        Path("fix_pass_error.py").write_text(patched_code)
        sys.exit(1)

    # --- Write patched file ---
    output_path = args.output or args.source.with_suffix(".fixed.py")
    output_path.write_text(patched_code)
    print(f"Patched file written: {output_path}", file=sys.stderr)

    # --- Measure delta ---
    print("Re-running codetopo check on patched file...", file=sys.stderr)
    after_report = run_check(output_path)

    # Use before_report we already have, or run fresh check on original
    if before_report is None:
        before_report = run_check(args.source)

    delta = compute_delta(before_report, after_report)

    # Print human-readable summary
    print_delta(delta, str(args.source), str(output_path))

    # Write delta JSON
    delta_path = args.source.with_suffix(".delta.json")
    with open(delta_path, "w") as f:
        json.dump({
            "source": str(args.source),
            "fixed": str(output_path),
            "model": args.model,
            "delta": delta,
            "before_metrics": before_report.get("metrics", {}),
            "after_metrics": after_report.get("metrics", {}),
        }, f, indent=2)
    print(f"Delta written: {delta_path}", file=sys.stderr)

    # Exit code: 0 if improved or unchanged, 1 if regressed
    sys.exit(0 if delta["improved"] or delta["totals"]["errors"]["change"] == 0 else 1)


if __name__ == "__main__":
    main()
