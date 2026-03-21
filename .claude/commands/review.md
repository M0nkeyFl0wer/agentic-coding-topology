# /review — Structural Code Review

Review changed Python files against deterministic structural principles.
This is not a style check. It measures algebraic properties of your code's
data flow and call graph, then maps violations to specific design principles.

## What to do

1. Identify which Python files were recently edited (use `git diff --name-only` or the file the user specifies).

2. Run `codetopo check <file> --output json` on each file. Use the project's `.venv/bin/codetopo` if available.

3. For each finding, map it to the violated principle from the rubric below and explain **why** the structure is wrong — not just what codetopo found, but what design rule it breaks and what the concrete fix looks like.

4. If codetopo finds no errors, still review the code against the rubric manually for issues the tool can't catch (naming, API boundaries, side effects).

5. Generate a visualization with `codetopo viz <file> -o /tmp/<name>_review.html` so the user can see the topology.

## Structural Rubric

These are not opinions. Each principle maps to a measurable graph property.

### P1: One operation per statement (Karpathy)
**Metric:** Data flow node out-degree ≤ 2
**Violation:** `statement_multitask` finding
**Principle:** Every line should do exactly one thing. If a variable feeds three downstream operations, the code is doing too much in one place. This isn't about line length — it's about cognitive and structural coupling.
**Gold standard:** Karpathy's nanoGPT `train.py` — 164 nodes, 1 error (a checkpoint dict, which is an acceptable exception).

### P2: No structural duplication (DRY, topologically verified)
**Metric:** Zero isomorphic function subgraphs with edges
**Violation:** `structural_duplication` finding
**Principle:** Two functions with the same data flow shape — same number of operations, same dependency pattern — are copy-paste regardless of variable names. This is DRY enforced by graph isomorphism, not string matching. Extract the shared pattern into a parameterized helper.
**Gold standard:** Karpathy's micrograd — 0 structural duplication errors after dunder exemption.

### P3: No circular dependencies (Unix: simple, composable units)
**Metric:** Zero cycles in call graph
**Violation:** `circular_dependency` finding
**Principle:** Functions that call each other create implicit state machines. Break the cycle at the weakest semantic link — extract shared logic into a third function.

### P4: Do one thing (Unix philosophy)
**Metric:** Function subgraph complexity — high node count + low edge density = a bag of unrelated operations
**Principle:** A function should have one reason to change. If its normalized form has 20 operations and 2 edges, it's a grab-bag of unrelated statements sharing a scope, not a coherent computation.

### P5: Compose, don't nest (Unix pipes)
**Metric:** Normalizer decomposition depth — how many intermediate variables the normalizer introduces
**Principle:** `result = f(g(h(x)))` is three operations pretending to be one. The normalizer decomposes it and the graph shows the real structure. Prefer flat pipelines over nested calls.

### P6: Make dependencies explicit (Hitchhiker's Guide)
**Metric:** Isolated component count (after min_size filter)
**Violation:** `isolated_component` finding
**Principle:** Code that computes a result nobody uses, or uses inputs from nowhere, is either dead code or has a hidden dependency. The graph makes invisible coupling visible.

### P7: Minimize blast radius (Google style guide)
**Metric:** Betweenness centrality < 0.3
**Violation:** `high_betweenness` finding
**Principle:** A variable that lies on 30%+ of all shortest paths is a single point of architectural coupling. Change it and you affect everything downstream. Make it an explicit boundary, or decouple its dependents.

## How to present findings

For each file, report:

```
## <filename>

**Topology:** N nodes, E edges, F functions
**Findings:** X errors, Y warnings

### [P2] structural_duplication: fetch_data ≅ load_data
These two functions have isomorphic data flow graphs — same shape, different names.
The shared pattern is: build_path → open → parse → extract → return.
**Fix:** Extract into `_load_and_parse(path, key)` parameterized by what differs.
**Principle violated:** P2 (no structural duplication)

### [P1] statement_multitask: config feeds 4 operations
The `config` variable is used by 4 downstream operations simultaneously.
**Fix:** Destructure early: `host, port, path = config["host"], config["port"], config["path"]`
**Principle violated:** P1 (one operation per statement)
```

## What this is NOT

- This is not a linter. It doesn't check formatting, naming conventions, or type annotations.
- This is not an LLM judgment. Every finding is a measured graph property with a threshold.
- This is not about making code "look clean." It's about structural properties that correlate with maintainability in calibrated benchmarks (Karpathy nanoGPT/micrograd/minGPT/nanochat gradient).

## Confidence levels

**High confidence (blocks commits):**
- `structural_duplication` — graph isomorphism is mathematical proof of copy-paste
- `circular_dependency` — cycle detection is exact

**Medium confidence (advisory):**
- `statement_multitask` — real signal but threshold is debatable
- `bridge_bottleneck` — depends on whether hub-spoke is intentional

**Low confidence (informational):**
- `abstraction_bloat` — often normalizer artifact
- `isolated_component` — often standalone functions at module scope
