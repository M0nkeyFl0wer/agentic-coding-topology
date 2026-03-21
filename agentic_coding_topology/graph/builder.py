"""
act.graph.builder — Pass 1.5: Build analyzable graphs from normalized code.

Takes the NormalizedModule from Pass 1 and constructs two complementary graphs:

  DataFlowGraph: nodes are variables/values, edges are operations.
    - Directed: A → B means "B was computed from A"
    - Edge weight: operation complexity (call=2, subscript=1, binop=1)
    - Node attributes: var_name, is_intermediate, source_line

  CallGraph: nodes are functions/methods, edges are call relationships.
    - Directed: A → B means "A calls B"
    - Edge weight: call frequency within the analyzed scope

These are the graphs that topology analysis runs on.
The DataFlowGraph is the primary one for Karpathy-style quality checks.
"""

import networkx as nx
from dataclasses import dataclass
from agentic_coding_topology.normalizer.python import NormalizedModule, NormalizedStatement


@dataclass
class CodeGraphs:
    """The two graphs produced from a normalized module."""
    data_flow: nx.DiGraph       # variable-level data flow
    call_graph: nx.DiGraph      # function-level call relationships
    source_path: str
    function_subgraphs: dict[str, nx.DiGraph]  # per-function DFGs


def build_data_flow_graph(statements: list[NormalizedStatement]) -> nx.DiGraph:
    """
    Build a directed data flow graph from normalized statements.

    Each variable is a node. Each statement creates an edge from each
    dependency to the output variable.

    Node attributes:
      - is_intermediate: True if introduced by normalizer (not in original source)
      - source_line: original line number
      - operation: what was computed to produce this value
      - out_degree_count: tracked separately (nx computes this, but we cache)

    Edge attributes:
      - operation: the computation on this edge
      - weight: complexity proxy (calls=2, indexing=1, arithmetic=1)
    """
    g = nx.DiGraph()

    for stmt in statements:
        # Add the output node
        if stmt.var_name not in g:
            g.add_node(stmt.var_name,
                       is_intermediate=stmt.is_intermediate,
                       source_line=stmt.source_line,
                       operation=stmt.operation)
        else:
            # Node already exists (from being a dependency) — update attributes
            g.nodes[stmt.var_name].update(
                is_intermediate=stmt.is_intermediate,
                source_line=stmt.source_line,
                operation=stmt.operation,
            )

        # Determine edge weight from operation type
        weight = _operation_weight(stmt.operation)

        # Add edges from each dependency to this variable
        for dep in stmt.depends_on:
            if dep not in g:
                g.add_node(dep, is_intermediate=True, source_line=stmt.source_line,
                           operation="<dependency>")
            g.add_edge(dep, stmt.var_name,
                       operation=stmt.operation,
                       weight=weight)

        # If no dependencies, add a self-edge marker for source nodes
        # (constants, literals, function parameters)
        if not stmt.depends_on and stmt.var_name not in g.nodes:
            g.add_node(stmt.var_name,
                       is_intermediate=False,
                       source_line=stmt.source_line,
                       operation="<source>")

    return g


def build_call_graph(module: NormalizedModule) -> nx.DiGraph:
    """
    Build a function-level call graph.

    Edges represent "function A calls function B" relationships.
    Extracted from the operation strings in normalized statements.

    For now uses string matching on operation fields — a more robust
    version would cross-reference against the full module's function
    definitions. Good enough for inter-function structural analysis.
    """
    g = nx.DiGraph()

    # Add all known functions as nodes
    for func_name in module.functions:
        g.add_node(func_name, statement_count=len(module.functions[func_name]))

    # Find call relationships
    for caller, stmts in module.functions.items():
        for stmt in stmts:
            # Extract called function name from operation string
            # e.g. "load(_t0)" → "load", "self.process(_t1)" → "process"
            callee = _extract_callee(stmt.operation)
            if callee and callee in module.functions and callee != caller:
                if g.has_edge(caller, callee):
                    g[caller][callee]["weight"] += 1
                else:
                    g.add_edge(caller, callee, weight=1)

    return g


def build_graphs(module: NormalizedModule) -> CodeGraphs:
    """Build all graphs from a normalized module."""
    data_flow = build_data_flow_graph(module.statements)
    call_graph = build_call_graph(module)

    # Build per-function data flow subgraphs
    function_subgraphs = {}
    for func_name, stmts in module.functions.items():
        function_subgraphs[func_name] = build_data_flow_graph(stmts)

    return CodeGraphs(
        data_flow=data_flow,
        call_graph=call_graph,
        source_path=module.source_path,
        function_subgraphs=function_subgraphs,
    )


def _operation_weight(operation: str) -> int:
    """
    Proxy for operation complexity as an edge weight.

    Calls are heavier than subscripts which are heavier than arithmetic.
    This affects betweenness centrality calculations — operations that
    are both frequently traversed AND computationally heavy are priority
    refactoring targets.
    """
    if "(" in operation:
        return 2   # function call
    elif "[" in operation:
        return 1   # subscript/index
    else:
        return 1   # arithmetic, attribute access, etc.


def _extract_callee(operation: str) -> str | None:
    """
    Extract the function name from an operation string.
    Returns None if operation is not a function call.
    """
    if "(" not in operation:
        return None
    # "funcname(..." or "obj.method(..." → extract the last component
    call_part = operation.split("(")[0]
    if "." in call_part:
        return call_part.split(".")[-1]
    return call_part.strip()
