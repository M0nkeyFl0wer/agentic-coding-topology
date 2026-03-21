# agentic-coding-topology

Algebraic topology for code quality enforcement. Catches structural problems in agent-generated code that linters miss — without an LLM judge.

Motivated by Andrej Karpathy's observation that coding agents bloat abstractions, copy-paste code blocks, and ignore style instructions — and that LLM-as-judge is vulnerable to Goodhart's Law.

**The insight**: structural code quality is deterministic, not probabilistic. You don't need a judge to tell you whether code is bloated — you can measure it with math.

## How it works

**Pass 1 — Normalize**: Transform code into an explicit intermediate form where every statement does exactly one thing. Makes the true structural shape visible.

```python
# Agent wrote this (Karpathy's complaint: calls 2 functions, indexes result)
result = process(load(fetch(url)[0]))

# After normalization (for analysis only, not production output)
_t0 = fetch(url)        # [intermediate]
_t1 = _t0[0]            # [intermediate]
_t2 = load(_t1)         # [intermediate]
result = process(_t2)
```

**Pass 2 — Topology**: Run algebraic topology on the normalized data-flow and call graphs using NetworkX. Produce deterministic findings.

Findings are **Goodhart-proof**: you can't fake good betweenness centrality. The math checks structure, not aesthetics.

## What it detects

| Finding | What it means | Severity |
|---|---|---|
| `structural_duplication` | Two functions with isomorphic data flow graphs — copy-paste detected topologically | ERROR |
| `circular_dependency` | Cycle in the call graph | ERROR |
| `abstraction_bloat` | Intermediate variable with low structural utility (high degree, low betweenness) | WARNING |
| `bridge_bottleneck` | Single call connecting two subsystems — fragility point | WARNING |
| `high_betweenness` | Variable on too many data paths — high blast radius | WARNING |
| `isolated_component` | Disconnected code island | WARNING |
| `topological_hole` | H1 persistent homology feature — missing abstraction (requires ripser) | INFO |

## Real output on agent-generated code

```
[FAIL] agent_generated.py: 1 errors, 6 warnings, 7 total findings

  ERRORS (1):
    [structural_duplication] Functions 'fetch_first_user' and 'fetch_first_result'
    have isomorphic data flow graphs — structurally identical despite different
    variable names. This is copy-paste detected topologically.
      Fix: Extract the shared logic into a single function. They perform the same
      structural computation regardless of what they're named.

  WARNINGS:
    [abstraction_bloat] Intermediate variable '_t3' has low structural utility
    (betweenness/degree = 0.003). Operation: _t2['results']
      Fix: Consider inlining or naming for a meaningful concept.
```

## Install

```bash
pip install agentic-coding-topology
# With persistent homology (H1 topological holes):
pip install "agentic-coding-topology[topology]"
```

## Usage

```bash
# Check a file
codetopo check myfile.py

# Check a directory recursively
codetopo check src/ --recursive

# JSON output for CI/agent consumption
codetopo check src/ --output json

# Show the normalized intermediate form
codetopo check myfile.py --show-normalized

# Fail on warnings too (strict mode)
codetopo check src/ --fail-on warning
```

## CI integration

```yaml
- name: Topology check
  run: codetopo check src/ --recursive --output json
```

Returns exit code 1 if any ERROR findings are found (configurable via `codetopo.toml`).

## Configuration

`codetopo.toml` in your project root:

```toml
[act]
max_statement_outdegree = 1   # Karpathy's core rule: one thing per line
max_betweenness = 0.3         # bottleneck threshold
max_duplicate_pairs = 0       # zero copy-paste tolerance
min_duplicate_size = 4        # ignore tiny functions (too many false positives)
fail_on_error = true
```

## Architecture

```
agent output
    ↓
Pass 1: PythonNormalizer  (agentic_coding_topology/normalizer/python.py)
    → NormalizedModule: statements with explicit single-operation nodes
    ↓
Pass 1.5: GraphBuilder  (agentic_coding_topology/graph/builder.py)
    → DataFlowGraph (NetworkX DiGraph, variable-level)
    → CallGraph     (NetworkX DiGraph, function-level)
    ↓
Pass 2: TopologyAnalyzer  (agentic_coding_topology/topology/analyzer.py)
    → betweenness centrality, cycle detection, isomorphism, homology
    → TopologyReport: typed findings with source lines and fix suggestions
    ↓
Output: text (human) or JSON (CI/agent feedback loop)
```

## Language support

- **Python**: Full support via stdlib `ast`
- **TypeScript/JavaScript**: Planned (Tree-sitter)
- **Rust, Go**: Planned (Tree-sitter)

The graph and topology layers are language-agnostic. Only the normalizer is language-specific.

## Connection to the Princeton DSS research

This implements one concrete answer to Q21-Q31 in
[Belova, Kansal et al. "An Alternative Trajectory for Generative AI" (Princeton, 2026)](https://arxiv.org/abs/2603.14147):
how do you use structural verification as a reward signal that resists Goodharting?

The normalized data flow graph is the symbolic abstraction layer.
Topology analysis on that graph is the verifier.
The findings are the reward signal — deterministic, not probabilistic, not gameable.

More context: [benwest.blog](https://benwest.blog)

## License

MIT
