"""
act — algebraic topology for code quality enforcement.

Two-pass pipeline:
  Pass 1: Normalize agent-generated code to explicit intermediate form
  Pass 2: Build call/data-flow graph, run topology analysis, emit findings

The normalized form makes structural problems visible that style analysis misses.
Topology findings are deterministic and Goodhart-proof: you can't fake
good betweenness centrality.
"""

__version__ = "0.1.0"
