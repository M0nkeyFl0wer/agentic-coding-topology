"""
act.topology.analyzer — Pass 2: Algebraic topology analysis on code graphs.

Runs structural analysis on the normalized code graphs and produces
deterministic, actionable findings. No LLM involved. No vibes.

Analysis methods:
  - Betweenness centrality (bridge/bottleneck detection)
  - Connected components (isolation, fragmentation)
  - Cycle detection (circular dependencies, loop structure)
  - Subgraph isomorphism (structural duplication detection)
  - Statement out-degree analysis (Karpathy's "one thing per line" rule)
  - Persistent homology via Ripser (topological holes = missed abstractions)

Each finding is typed, has a severity, and references specific variables
or functions in the original source with line numbers.
"""

from __future__ import annotations

import networkx as nx
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import itertools


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingType(Enum):
    # Karpathy-specific
    STATEMENT_MULTITASK = "statement_multitask"         # line does >1 thing
    ABSTRACTION_BLOAT = "abstraction_bloat"             # intermediate var unused structurally
    STRUCTURAL_DUPLICATION = "structural_duplication"   # copy-paste detected topologically

    # General structural
    BRIDGE_BOTTLENECK = "bridge_bottleneck"             # single point connecting subsystems
    CIRCULAR_DEPENDENCY = "circular_dependency"         # cycle in call/data flow
    ISOLATED_COMPONENT = "isolated_component"           # orphaned code
    HIGH_BETWEENNESS = "high_betweenness"               # dangerous centrality
    TOPOLOGICAL_HOLE = "topological_hole"               # H1 feature = missed abstraction
    FUNCTION_BLOAT = "function_bloat"                   # function does too many things
    FUNCTION_COUPLING = "function_coupling"             # function is called by too many others


@dataclass
class Finding:
    """A single topology finding with full provenance."""
    finding_type: FindingType
    severity: Severity
    message: str                        # human-readable, agent-actionable
    nodes: list[str]                    # variables/functions involved
    source_lines: list[int]             # original source lines
    metric_value: float                 # the measurement that triggered this
    metric_name: str                    # what was measured
    fix_suggestion: str                 # concrete suggested action


@dataclass
class TopologyReport:
    """Full topology analysis results for one module."""
    source_path: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = True

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    def by_type(self, t: FindingType) -> list[Finding]:
        return [f for f in self.findings if f.finding_type == t]

    def summary(self) -> str:
        errors = len(self.errors())
        warnings = len(self.warnings())
        status = "FAIL" if not self.passed else "PASS"
        return (f"[{status}] {self.source_path}: "
                f"{errors} errors, {warnings} warnings, "
                f"{len(self.findings)} total findings")


class TopologyAnalyzer:
    """
    Runs all topology checks on the code graphs.

    Thresholds are configurable via act.toml.
    Defaults are tuned for agent-generated Python code.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        # Karpathy rule: max out-degree per statement node in data flow graph
        self.max_statement_outdegree = cfg.get("max_statement_outdegree", 2)
        # Max betweenness centrality before a node is flagged as bottleneck
        self.max_betweenness = cfg.get("max_betweenness", 0.3)
        # Min ratio of betweenness/degree for an intermediate var to be "useful"
        self.min_utility_ratio = cfg.get("min_utility_ratio", 0.05)
        # Max number of structurally isomorphic subgraph pairs allowed
        self.max_duplicate_pairs = cfg.get("max_duplicate_pairs", 0)
        # Min subgraph size for duplication detection
        self.min_duplicate_size = cfg.get("min_duplicate_size", 4)
        # Fail build on errors
        self.fail_on_error = cfg.get("fail_on_error", True)
        # Min isolated component size to flag — small islands (< 5 nodes) are
        # almost always standalone functions, not real orphans
        self.min_isolated_size = cfg.get("min_isolated_size", 5)
        # Bridge bottleneck: ignore bridges where either endpoint has degree >= this.
        self.min_bridge_hub_degree = cfg.get("min_bridge_hub_degree", 3)
        # Function bloat: max normalized operations per function before flagging
        # A/B review showed 900-line god functions are the worst structural problem
        # codetopo missed. This catches them.
        self.max_function_operations = cfg.get("max_function_operations", 50)
        # Function coupling: max in-degree in call graph before flagging
        # High in-degree = many callers = high blast radius on change
        self.max_function_callers = cfg.get("max_function_callers", 8)

    def analyze(self, graphs) -> TopologyReport:
        """
        Run all topology checks and return a report.

        graphs: CodeGraphs from agentic_coding_topology.graph.builder
        """
        from agentic_coding_topology.graph.builder import CodeGraphs
        report = TopologyReport(source_path=graphs.source_path)

        # --- Data flow graph checks ---
        dfg = graphs.data_flow
        if dfg.number_of_nodes() > 0:
            self._check_statement_outdegree(dfg, report)
            self._check_abstraction_bloat(dfg, report)
            self._check_betweenness(dfg, report)
            self._check_connected_components(dfg, report)

        # --- Call graph checks ---
        cg = graphs.call_graph
        if cg.number_of_nodes() > 0:
            self._check_circular_dependencies(cg, report)
            self._check_bridge_bottlenecks(cg, report)

        # --- Function-level checks (P4: bloat, P7: coupling) ---
        if len(graphs.function_subgraphs) >= 1:
            self._check_function_bloat(graphs.function_subgraphs, report)
        if cg.number_of_nodes() > 0:
            self._check_function_coupling(cg, report)

        # --- Cross-function structural duplication ---
        if len(graphs.function_subgraphs) >= 2:
            self._check_structural_duplication(graphs.function_subgraphs, report)

        # --- Persistent homology (if ripser available) ---
        try:
            self._check_persistent_homology(dfg, report)
        except ImportError:
            pass  # ripser optional — skip silently, note in metrics
        except Exception:
            pass  # graph too small or degenerate — skip

        # Determine pass/fail
        report.passed = not (self.fail_on_error and len(report.errors()) > 0)

        # Summary metrics
        report.metrics["node_count"] = dfg.number_of_nodes()
        report.metrics["edge_count"] = dfg.number_of_edges()
        report.metrics["intermediate_ratio"] = self._intermediate_ratio(dfg)
        report.metrics["function_count"] = len(graphs.function_subgraphs)

        return report

    # -------------------------------------------------------------------------
    # Check implementations
    # -------------------------------------------------------------------------

    def _check_statement_outdegree(self, dfg: nx.DiGraph, report: TopologyReport):
        """
        Karpathy's core rule: every statement should do exactly one thing.

        In the normalized data flow graph, a statement node with out-degree > 1
        means this variable feeds multiple downstream operations directly —
        it's being used in multiple contexts simultaneously.

        More importantly: in the NORMALIZED form, if the normalizer correctly
        decomposed everything, high out-degree on an intermediate variable means
        the original code had multiple operations happening "in parallel" that
        we couldn't fully decompose. This is the structural fingerprint of
        "complex constructs where one line calls 2 functions."
        """
        violations = []
        for node in dfg.nodes:
            out_deg = dfg.out_degree(node)
            if out_deg > self.max_statement_outdegree:
                attrs = dfg.nodes[node]
                violations.append((node, out_deg, attrs))

        for node, out_deg, attrs in violations:
            report.findings.append(Finding(
                finding_type=FindingType.STATEMENT_MULTITASK,
                severity=Severity.ERROR,
                message=(
                    f"Variable '{node}' feeds {out_deg} downstream operations "
                    f"simultaneously (max: {self.max_statement_outdegree}). "
                    f"Operation: {attrs.get('operation', 'unknown')}"
                ),
                nodes=[node] + list(dfg.successors(node)),
                source_lines=[attrs.get("source_line", 0)],
                metric_value=out_deg,
                metric_name="statement_out_degree",
                fix_suggestion=(
                    f"Split the use of '{node}' into separate statements, "
                    f"one per downstream operation. Each intermediate result "
                    f"should be named for what it represents."
                ),
            ))

    def _check_abstraction_bloat(self, dfg: nx.DiGraph, report: TopologyReport):
        """
        Detect intermediate variables that are structurally useless.

        In the normalized graph, an intermediate variable (introduced by the
        normalizer) that has betweenness centrality near zero relative to its
        degree is a variable that exists but that no important paths route through.
        It's an abstraction that doesn't earn its place.

        High degree + low betweenness = named but not structurally meaningful.
        This is the topology fingerprint of "bloated abstractions."
        """
        if dfg.number_of_nodes() < 3:
            return

        betweenness = nx.betweenness_centrality(dfg, normalized=True)

        for node in dfg.nodes:
            attrs = dfg.nodes[node]
            if not attrs.get("is_intermediate", False):
                continue  # only check normalizer-introduced intermediates

            degree = dfg.degree(node)
            if degree == 0:
                continue

            # Exempt purely linear chain nodes: in=1, out=1
            # These are normalizer decompositions of A.B.C() chains.
            # They cannot have meaningful betweenness by construction —
            # there's only one path through them — so flagging them is noise.
            if dfg.in_degree(node) == 1 and dfg.out_degree(node) == 1:
                continue

            bc = betweenness.get(node, 0.0)
            utility = bc / max(degree, 1)

            if utility < self.min_utility_ratio and degree > 1:
                report.findings.append(Finding(
                    finding_type=FindingType.ABSTRACTION_BLOAT,
                    severity=Severity.WARNING,
                    message=(
                        f"Intermediate variable '{node}' has low structural utility "
                        f"(betweenness/degree = {utility:.3f}, threshold: "
                        f"{self.min_utility_ratio}). "
                        f"Operation: {attrs.get('operation', 'unknown')}"
                    ),
                    nodes=[node],
                    source_lines=[attrs.get("source_line", 0)],
                    metric_value=utility,
                    metric_name="abstraction_utility_ratio",
                    fix_suggestion=(
                        f"Consider inlining '{node}' or verifying it's named "
                        f"for a meaningful concept. If it exists only as a "
                        f"stepping stone with no routing significance, it's bloat."
                    ),
                ))

    def _check_betweenness(self, dfg: nx.DiGraph, report: TopologyReport):
        """
        Find high-betweenness nodes — structural bottlenecks.

        A variable or value that lies on many shortest paths between other
        nodes is a single point of architectural coupling. Change it and
        you affect everything downstream. This is the blast-radius metric.
        """
        if dfg.number_of_nodes() < 4:
            return

        betweenness = nx.betweenness_centrality(dfg, normalized=True)
        report.metrics["max_betweenness"] = max(betweenness.values(), default=0.0)
        report.metrics["mean_betweenness"] = (
            sum(betweenness.values()) / len(betweenness) if betweenness else 0.0
        )

        for node, bc in betweenness.items():
            if bc > self.max_betweenness:
                attrs = dfg.nodes[node]
                report.findings.append(Finding(
                    finding_type=FindingType.HIGH_BETWEENNESS,
                    severity=Severity.WARNING,
                    message=(
                        f"Variable '{node}' has betweenness centrality {bc:.3f} "
                        f"(threshold: {self.max_betweenness}). "
                        f"Many data paths route through this value — high blast radius."
                    ),
                    nodes=[node],
                    source_lines=[attrs.get("source_line", 0)],
                    metric_value=bc,
                    metric_name="betweenness_centrality",
                    fix_suggestion=(
                        f"'{node}' is a structural bottleneck. Consider whether "
                        f"its dependents can be decoupled, or whether it should "
                        f"be made explicit as a named architectural boundary."
                    ),
                ))

    def _check_connected_components(self, dfg: nx.DiGraph, report: TopologyReport):
        """
        Find isolated subgraphs — code that computes something nobody uses,
        or that uses inputs from nowhere.
        """
        undirected = dfg.to_undirected()
        components = list(nx.connected_components(undirected))
        report.metrics["component_count"] = len(components)

        if len(components) <= 1:
            return

        # Sort by size — the giant component is expected, others are suspicious
        components.sort(key=len, reverse=True)
        isolated = components[1:]  # everything that's not the giant component

        for component in isolated:
            if len(component) < self.min_isolated_size:
                continue  # small islands are almost always standalone functions
            nodes = list(component)
            lines = [dfg.nodes[n].get("source_line", 0) for n in nodes
                     if dfg.nodes[n].get("source_line")]
            report.findings.append(Finding(
                finding_type=FindingType.ISOLATED_COMPONENT,
                severity=Severity.WARNING,
                message=(
                    f"Isolated data flow component with {len(nodes)} nodes: "
                    f"{nodes[:3]}{'...' if len(nodes) > 3 else ''}. "
                    f"This computation is disconnected from the main flow."
                ),
                nodes=nodes,
                source_lines=sorted(set(lines)),
                metric_value=len(nodes),
                metric_name="isolated_component_size",
                fix_suggestion=(
                    "This isolated computation is not connected to the main "
                    "data flow. Either it's dead code, or a dependency is "
                    "missing that should connect it."
                ),
            ))

    def _check_circular_dependencies(self, cg: nx.DiGraph, report: TopologyReport):
        """Find cycles in the call graph — functions calling each other circularly."""
        cycles = list(nx.simple_cycles(cg))
        report.metrics["cycle_count"] = len(cycles)

        for cycle in cycles:
            report.findings.append(Finding(
                finding_type=FindingType.CIRCULAR_DEPENDENCY,
                severity=Severity.ERROR,
                message=(
                    f"Circular call dependency: {' → '.join(cycle)} → {cycle[0]}"
                ),
                nodes=cycle,
                source_lines=[],
                metric_value=len(cycle),
                metric_name="cycle_length",
                fix_suggestion=(
                    f"Break the cycle at its weakest semantic link. "
                    f"Often the fix is extracting shared logic into a "
                    f"third function that neither calls the other."
                ),
            ))

    def _check_bridge_bottlenecks(self, cg: nx.DiGraph, report: TopologyReport):
        """
        Find bridge edges in the call graph — single function calls that,
        if removed, would disconnect subsystems.
        """
        if cg.number_of_edges() < 2:
            return

        undirected = cg.to_undirected()
        bridges = list(nx.bridges(undirected))
        report.metrics["bridge_count"] = len(bridges)

        for u, v in bridges:
            # Skip hub-and-spoke patterns: if either endpoint has high degree,
            # this is normal module decomposition, not structural fragility.
            # Real bridges connect two independent subsystems of similar size.
            if (undirected.degree(u) >= self.min_bridge_hub_degree or
                    undirected.degree(v) >= self.min_bridge_hub_degree):
                continue
            report.findings.append(Finding(
                finding_type=FindingType.BRIDGE_BOTTLENECK,
                severity=Severity.WARNING,
                message=(
                    f"Call '{u}→{v}' is a bridge — removing it disconnects "
                    f"the call graph. Structural fragility point."
                ),
                nodes=[u, v],
                source_lines=[],
                metric_value=1.0,
                metric_name="is_bridge",
                fix_suggestion=(
                    f"The '{u}→{v}' relationship is the only connection "
                    f"between two subsystems. Consider whether a second "
                    f"connection exists or should exist."
                ),
            ))

    def _check_function_bloat(
        self,
        subgraphs: dict[str, nx.DiGraph],
        report: TopologyReport,
    ):
        """
        Detect functions with too many operations (P4: single responsibility).

        A function with 50+ normalized operations is doing too many things.
        This catches the 'god function' pattern that graph topology alone
        cannot see — it's about scope, not data flow shape.
        """
        for func_name, subgraph in subgraphs.items():
            op_count = subgraph.number_of_nodes()
            if op_count >= self.max_function_operations:
                report.findings.append(Finding(
                    finding_type=FindingType.FUNCTION_BLOAT,
                    severity=Severity.WARNING,
                    message=(
                        f"Function '{func_name}' has {op_count} normalized "
                        f"operations (threshold: {self.max_function_operations}). "
                        f"This function is doing too many things."
                    ),
                    nodes=[func_name],
                    source_lines=[],
                    metric_value=op_count,
                    metric_name="function_operation_count",
                    fix_suggestion=(
                        f"Break '{func_name}' into smaller functions, each with "
                        f"a single responsibility. Extract coherent sub-sequences "
                        f"of operations into named helpers."
                    ),
                ))

    def _check_function_coupling(self, cg: nx.DiGraph, report: TopologyReport):
        """
        Detect functions with too many callers (P7: minimize blast radius).

        A function called by 8+ other functions is a high-coupling point.
        Changing it affects many callers — high blast radius.
        """
        for func_name in cg.nodes:
            in_deg = cg.in_degree(func_name)
            if in_deg >= self.max_function_callers:
                callers = [u for u, _ in cg.in_edges(func_name)]
                report.findings.append(Finding(
                    finding_type=FindingType.FUNCTION_COUPLING,
                    severity=Severity.WARNING,
                    message=(
                        f"Function '{func_name}' is called by {in_deg} other "
                        f"functions (threshold: {self.max_function_callers}). "
                        f"High coupling — changes here affect many callers."
                    ),
                    nodes=[func_name] + callers[:5],
                    source_lines=[],
                    metric_value=in_deg,
                    metric_name="function_caller_count",
                    fix_suggestion=(
                        f"'{func_name}' is a coupling hotspot. Consider whether "
                        f"callers could use a more specific interface, or whether "
                        f"this function should be split into variants."
                    ),
                ))

    def _check_structural_duplication(
        self,
        subgraphs: dict[str, nx.DiGraph],
        report: TopologyReport,
    ):
        """
        Detect structurally isomorphic function subgraphs — copy-paste
        detected topologically rather than textually.

        Two functions with isomorphic data flow graphs (same shape,
        regardless of variable names) are structurally identical.
        They should be one function.

        Uses approximate isomorphism via degree sequence comparison
        as a fast first filter, then full isomorphism check on candidates.
        """
        duplicate_pairs = []
        func_names = list(subgraphs.keys())

        for i, j in itertools.combinations(range(len(func_names)), 2):
            f1, f2 = func_names[i], func_names[j]

            # Skip dunder method pairs — operator overloads are intentionally
            # isomorphic (e.g. __add__ ≅ __mul__) by Python's data model
            if (f1.startswith('__') and f1.endswith('__') and
                    f2.startswith('__') and f2.endswith('__')):
                continue

            g1, g2 = subgraphs[f1], subgraphs[f2]

            # Skip tiny functions — too many false positives
            if (g1.number_of_nodes() < self.min_duplicate_size or
                    g2.number_of_nodes() < self.min_duplicate_size):
                continue

            # Skip edgeless graphs entirely. Two disconnected node sets of
            # the same size are trivially isomorphic — that's counting, not
            # topology. Small edgeless functions (API handlers) are real
            # copy-paste but the signal is too noisy to be useful.
            # The /review skill's manual pass catches these instead.
            if g1.number_of_edges() == 0 or g2.number_of_edges() == 0:
                continue

            # Fast filter: degree sequences must match
            if (sorted(d for _, d in g1.degree()) !=
                    sorted(d for _, d in g2.degree())):
                continue

            # Full isomorphism check
            if nx.is_isomorphic(g1, g2):
                duplicate_pairs.append((f1, f2))

        report.metrics["structural_duplicate_pairs"] = len(duplicate_pairs)

        for f1, f2 in duplicate_pairs:
            report.findings.append(Finding(
                finding_type=FindingType.STRUCTURAL_DUPLICATION,
                severity=Severity.ERROR,
                message=(
                    f"Functions '{f1}' and '{f2}' have isomorphic data flow "
                    f"graphs — structurally identical despite different variable "
                    f"names. This is copy-paste detected topologically."
                ),
                nodes=[f1, f2],
                source_lines=[],
                metric_value=1.0,
                metric_name="graph_isomorphic",
                fix_suggestion=(
                    f"Extract the shared logic from '{f1}' and '{f2}' into "
                    f"a single function. They perform the same structural "
                    f"computation regardless of what they're named."
                ),
            ))

    def _check_persistent_homology(self, dfg: nx.DiGraph, report: TopologyReport):
        """
        Find topological holes using persistent homology (Ripser).

        H1 features (1-cycles) in the data flow graph represent regions where
        multiple computation paths converge toward a synthesis that hasn't
        been written yet — the shape of a missing abstraction.

        Long-lived H1 bars = deep structural holes = high-priority refactoring.
        Short-lived H1 bars = noise from normal data flow branching.
        """
        import ripser
        import numpy as np
        from scipy.spatial.distance import cdist

        if dfg.number_of_nodes() < 4:
            return

        # Build distance matrix from shortest path lengths
        # Nodes that can't reach each other get max distance
        nodes = list(dfg.nodes)
        n = len(nodes)
        idx = {node: i for i, node in enumerate(nodes)}

        dist_matrix = np.full((n, n), float(n + 1))
        np.fill_diagonal(dist_matrix, 0)

        undirected = dfg.to_undirected()
        for source in nodes:
            lengths = nx.single_source_shortest_path_length(undirected, source)
            for target, length in lengths.items():
                dist_matrix[idx[source]][idx[target]] = length

        # Cap at max finite value for ripser
        finite_max = dist_matrix[dist_matrix < float(n + 1)].max() if \
            np.any(dist_matrix < float(n + 1)) else 1.0
        dist_matrix[dist_matrix == float(n + 1)] = finite_max + 1

        result = ripser.ripser(dist_matrix, metric="precomputed", maxdim=1)
        h1_bars = result["dgms"][1]  # H1 persistence diagram

        if len(h1_bars) == 0:
            return

        # Filter to persistent features (birth-death gap > threshold)
        persistence_threshold = 2.0  # tunable
        persistent_holes = [
            (birth, death) for birth, death in h1_bars
            if death - birth > persistence_threshold and death < np.inf
        ]

        report.metrics["h1_feature_count"] = len(h1_bars)
        report.metrics["persistent_h1_count"] = len(persistent_holes)

        for birth, death in persistent_holes:
            report.findings.append(Finding(
                finding_type=FindingType.TOPOLOGICAL_HOLE,
                severity=Severity.INFO,
                message=(
                    f"Persistent H1 topological hole detected "
                    f"(birth={birth:.1f}, death={death:.1f}, "
                    f"persistence={death-birth:.1f}). "
                    f"Multiple computation paths converge without a shared abstraction."
                ),
                nodes=[],
                source_lines=[],
                metric_value=death - birth,
                metric_name="h1_persistence",
                fix_suggestion=(
                    "A topological hole indicates that several related computations "
                    "orbit an abstraction that hasn't been written yet. "
                    "Look for repeated patterns in the code that could be "
                    "extracted into a single named function or class."
                ),
            ))

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _intermediate_ratio(self, dfg: nx.DiGraph) -> float:
        """Fraction of nodes that are normalizer-introduced intermediates."""
        if dfg.number_of_nodes() == 0:
            return 0.0
        intermediates = sum(
            1 for n in dfg.nodes if dfg.nodes[n].get("is_intermediate", False)
        )
        return intermediates / dfg.number_of_nodes()
