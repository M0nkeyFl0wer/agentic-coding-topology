# codetopo

Algebraic topology for code quality. Catches structural problems in agent-generated Python — copy-paste, god functions, circular dependencies — without an LLM judge. Deterministic, Goodhart-proof, and useful today.

## The problem

Coding agents (Claude Code, Cursor, Copilot) generate code that passes vibes checks but fails under structural scrutiny. They copy-paste functions with different variable names. They build 900-line god functions. They create circular dependencies. LLM-as-judge can't catch this reliably because the judge has the same blind spots as the generator.

**The insight**: structural code quality is a graph property, not a statistical prediction. You don't need a model to tell you two functions are identical — you can prove it with graph isomorphism. You don't need intuition to find circular dependencies — you can detect them with cycle enumeration. Math, not vibes.

## What it does

codetopo normalizes Python code into single-operation statements, builds data flow and call graphs (NetworkX), then runs topology analysis to produce deterministic findings.

```
$ codetopo check myfile.py

[FAIL] myfile.py: 2 errors, 1 warning

  ERRORS:
    [structural_duplication] Functions 'fetch_user' and 'fetch_result' have
    isomorphic data flow graphs — copy-paste detected topologically.
      Fix: Extract shared logic into a single parameterized function.

    [function_bloat] Function 'process_everything' has 139 normalized
    operations (threshold: 50). This function is doing too many things.
      Fix: Break into smaller functions with single responsibilities.
```

## What it catches (and what it can't)

| Finding | What it proves | Confidence |
|---|---|---|
| `structural_duplication` | Two functions have identical graph shape | High — mathematical proof |
| `circular_dependency` | Functions call each other in a cycle | High — exact detection |
| `function_bloat` | Function has >50 normalized operations | Medium — size ≠ complexity |
| `function_coupling` | Function called by >8 others | Medium — high blast radius |
| `statement_multitask` | Variable feeds 3+ downstream operations | Medium — often normal |
| `bridge_bottleneck` | Single call connecting two subsystems | Low — hub-spoke is fine |
| `isolated_component` | Disconnected code island | Low — standalone functions |

**What it can't see**: security vulnerabilities, performance problems, error handling quality, test coverage, readability, naming, whether an abstraction is the right one. These are real quality dimensions that require different tools (see Roadmap below).

## Install

```bash
pip install agentic-coding-topology

# With persistent homology (topological holes):
pip install "agentic-coding-topology[topology]"
```

## Usage

```bash
# Check a file
codetopo check myfile.py

# Check a directory
codetopo check src/ --recursive

# JSON output for CI/agent consumption
codetopo check src/ --output json

# Interactive topology visualization
codetopo viz myfile.py --open

# Show normalized intermediate form
codetopo check myfile.py --show-normalized
```

## Visualization

`codetopo viz` generates a self-contained HTML file with an interactive D3 force-directed graph. Nodes are variables, edges are data dependencies, colors indicate severity. Click nodes to see source code. Click findings to see what's wrong and why.

```bash
codetopo viz myfile.py --open
```

## Pre-commit hook

Only blocks on high-confidence findings (structural duplication + circular dependencies). Everything else is advisory.

```bash
# Copy .githooks/pre-commit to your project
cp .githooks/pre-commit your-project/.githooks/
cd your-project && git config core.hooksPath .githooks
```

## Claude Code integration

codetopo includes a PostToolUse hook that runs after every Python file edit and injects findings into the agent's context. Copy `.claude/hooks/codetopo-check.sh` and `.claude/settings.json` to your project's `.claude/` directory.

There's also a `/review` slash command that runs a structural code review combining codetopo measurements with manual principle checks.

## Configuration

`codetopo.toml` in your project root:

```toml
[codetopo]
max_statement_outdegree = 2     # max variable fan-out before flagging
max_betweenness = 0.3           # bottleneck centrality threshold
min_duplicate_size = 3          # min function size for duplication check
max_function_operations = 50    # god function threshold
max_function_callers = 8        # coupling hotspot threshold
fail_on_error = true            # exit 1 on errors (for CI)
min_isolated_size = 5           # ignore small disconnected components
min_bridge_hub_degree = 3       # ignore hub-spoke bridge patterns
```

## Calibration

Thresholds were calibrated against Andrej Karpathy's public repositories:

| Repo | Tier | Errors | Description |
|---|---|---|---|
| micrograd | Gold standard | 0 | Intentionally minimal, hand-written |
| nanoGPT | Gold standard | 1 | Clean ML code, one legitimate fan-out |
| minGPT | Moderate complexity | 1 | Karpathy called it "too complicated" |
| nanochat | Agent-assisted | 11 | Community PRs, most structural issues |

The gradient is real: hand-crafted code scores near zero, agent-assisted code scores higher. The tool measures the gap.

## Architecture

```
Python source
    ↓
Pass 1: PythonNormalizer → single-operation statements
    ↓
Pass 1.5: GraphBuilder → DataFlowGraph + CallGraph (NetworkX)
    ↓
Pass 2: TopologyAnalyzer → findings with source lines and fix suggestions
    ↓
Output: text, JSON, or interactive HTML visualization
```

The normalizer is language-specific (Python via stdlib `ast`). Everything downstream is language-agnostic.

## Roadmap: from structural topology to full quality verification

codetopo measures one dimension of code quality — structural shape. But every quality dimension has a topology: a set of nodes, edges, and provable properties. The long-term vision is a multi-layer quality graph where each layer adds verifiable (not statistical) quality measurement.

Here are ten concrete steps, ordered by value and feasibility:

### Step 1: Use codetopo daily (now)
Run it on your projects. Let the pre-commit hook catch real problems. See where the thresholds are wrong. The tool improves through use, not through more engineering.

### Step 2: Add existing security tools to the pre-commit pipeline
`ruff` for style, `bandit` for security. These are mature, fast, and already solve layers 2 and 5 of the quality stack. Don't rebuild what exists — compose it.
```bash
ruff check src/ && bandit -r src/ && codetopo check src/ --recursive
```

### Step 3: Build a taint flow layer (security as graph reachability)
User input → [sanitizer?] → dangerous sink. If there's a path without a sanitizer node, that's a provable vulnerability. The AST visitor already exists in codetopo's normalizer — extend it to track input sources and sink functions. This is where algebraic verification matters most: "no path exists from user input to SQL execute" is a proof, not a guess.

### Step 4: Add control flow analysis (performance as graph structure)
Nested loops where the inner iteration depends on the outer = O(n²). Database calls inside loop bodies = N+1 queries. These are detectable subgraph patterns in the control flow graph. Python's `ast` module gives us loop/branch structure for free.

### Step 5: Map error handling completeness (reliability as path coverage)
Every external call (HTTP, file I/O, database) should have an edge to an error handler. Missing error paths are measurable as incomplete subgraphs. "This API call has no exception handler within 3 hops" is verifiable.

### Step 6: Build the test coverage graph (verification as bipartite reachability)
Test functions on one side, source functions on the other. Coverage = fraction of source nodes reachable from test nodes. pytest + coverage.py already produce this data — codetopo just needs to ingest it and add it to the quality graph.

### Step 7: Cross-file structural analysis
codetopo currently checks one file at a time. The same function duplicated across two modules is invisible. Build a module-level graph that connects function subgraphs across files via import edges. This is where the knowledge graph architecture becomes necessary — single-file analysis can't see cross-module patterns.

### Step 8: Feed the multi-layer graph into GraphMETR training
The topo-test corpus already has layer 1 features (structural topology). Adding layers 2-6 makes the feature space richer. Train on the Karpathy calibration corpus + your own repos. The research question: does multi-layer topology predict real bugs better than any single layer?

### Step 9: Temporal quality tracking
Run codetopo on every commit in a repo's history. Plot the quality trajectory. Does quality degrade as agents contribute more? Does it improve after reviews? The trajectory is a time-series on the quality graph — and it answers the question "is this codebase getting better or worse?"

### Step 10: The unified quality knowledge graph
All layers connected. A finding in one layer correlated with findings in others. "This function has structural duplication AND a taint flow vulnerability AND no test coverage" is a compound risk score that no single tool can produce. This is the research frontier — not a tool to build next week, but the direction everything points toward.

### What to resist

- Building layers 3-7 before layer 1 has proven its value in daily use
- Rebuilding what Semgrep, Bandit, and coverage.py already do well
- Treating algebraic certainty as a ceiling — the most important quality judgments (right abstraction, good naming, minimal surface area) are still human calls
- Spending more time on quality infrastructure than on the projects it's supposed to protect

The beautiful code people admire has two properties codetopo can measure (no duplication, single responsibility) and three it can't (good naming, minimal surface area, obvious intent). Algebraic verification gives you a floor. Judgment gives you the ceiling. Build the floor, then stand on it.

## Research context

This implements one answer to Q21-Q31 in [Belova, Kansal et al. "An Alternative Trajectory for Generative AI" (Princeton DSS, 2026)](https://arxiv.org/abs/2603.14147): how do you use structural verification as a reward signal that resists Goodharting?

The calibration corpus and before/after pairs from `fix_pass` runs feed the [topo-test](https://github.com/M0nkeyFl0wer/topo-test) experiment for GraphMETR training.

## License

MIT
