"""
codetopo visualization — self-contained HTML/D3 graph visualization.

Generates a single HTML file with:
  - Force-directed data flow graph (nodes = variables, edges = data deps)
  - Call graph overlay
  - Color coding: red = error nodes, orange = warning nodes, green = clean
  - Finding annotations on hover
  - Per-function subgraph clustering

Also exports raw graph data as JSON for external tools.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from agentic_coding_topology.normalizer.python import normalize_file
from agentic_coding_topology.graph.builder import build_graphs, CodeGraphs
from agentic_coding_topology.topology.analyzer import TopologyAnalyzer, TopologyReport


def extract_graph_data(source_path: str, config: dict | None = None) -> dict:
    """Run full pipeline and extract visualization data."""
    module = normalize_file(source_path)
    graphs = build_graphs(module)
    analyzer = TopologyAnalyzer(config=config)
    report = analyzer.analyze(graphs)

    # Build node→finding map for coloring
    error_nodes = set()
    warning_nodes = set()
    finding_map = {}  # node → list of finding messages

    for f in report.findings:
        for node in f.nodes:
            if node not in finding_map:
                finding_map[node] = []
            finding_map[node].append({
                "type": f.finding_type.value,
                "severity": f.severity.value,
                "message": f.message,
            })
            if f.severity.value == "error":
                error_nodes.add(node)
            elif f.severity.value == "warning":
                warning_nodes.add(node)

    # Data flow graph
    dfg = graphs.data_flow
    dfg_nodes = []
    for node in dfg.nodes:
        attrs = dfg.nodes[node]
        severity = "error" if node in error_nodes else "warning" if node in warning_nodes else "clean"
        dfg_nodes.append({
            "id": node,
            "is_intermediate": attrs.get("is_intermediate", False),
            "source_line": attrs.get("source_line", 0),
            "operation": attrs.get("operation", ""),
            "severity": severity,
            "findings": finding_map.get(node, []),
            "in_degree": dfg.in_degree(node),
            "out_degree": dfg.out_degree(node),
        })

    dfg_edges = []
    for u, v, data in dfg.edges(data=True):
        dfg_edges.append({
            "source": u,
            "target": v,
            "operation": data.get("operation", ""),
            "weight": data.get("weight", 1),
        })

    # Call graph
    cg = graphs.call_graph
    cg_nodes = [{"id": n, "statement_count": cg.nodes[n].get("statement_count", 0)}
                for n in cg.nodes]
    cg_edges = [{"source": u, "target": v, "weight": d.get("weight", 1)}
                for u, v, d in cg.edges(data=True)]

    # Function subgraphs (for clustering)
    functions = {}
    for fname, subgraph in graphs.function_subgraphs.items():
        functions[fname] = {
            "nodes": list(subgraph.nodes),
            "node_count": subgraph.number_of_nodes(),
            "edge_count": subgraph.number_of_edges(),
        }

    # Read source code with line numbers
    try:
        source_lines = Path(source_path).read_text().splitlines()
    except Exception:
        source_lines = []

    # Build normalized form per function
    normalized = {}
    for fname, stmts in module.functions.items():
        normalized[fname] = [
            {"var": s.var_name, "op": s.operation, "line": s.source_line,
             "intermediate": s.is_intermediate}
            for s in stmts
        ]

    return {
        "source_path": source_path,
        "summary": report.summary(),
        "passed": report.passed,
        "metrics": report.metrics,
        "source_lines": source_lines,
        "normalized": normalized,
        "findings": [{
            "type": f.finding_type.value,
            "severity": f.severity.value,
            "message": f.message,
            "nodes": f.nodes,
            "source_lines": f.source_lines,
            "fix": f.fix_suggestion,
        } for f in report.findings],
        "data_flow": {"nodes": dfg_nodes, "edges": dfg_edges},
        "call_graph": {"nodes": cg_nodes, "edges": cg_edges},
        "functions": functions,
    }


def generate_html(graph_data: dict) -> str:
    """Generate self-contained HTML visualization."""
    data_json = json.dumps(graph_data)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>codetopo — {graph_data['source_path']}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'SF Mono', 'Fira Code', monospace; background: #0d1117; color: #c9d1d9; }}
#header {{ padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }}
#header h1 {{ font-size: 14px; font-weight: 500; }}
#header .status {{ font-size: 13px; padding: 3px 10px; border-radius: 12px; }}
.status.pass {{ background: #238636; color: #fff; }}
.status.fail {{ background: #da3633; color: #fff; }}
#controls {{ padding: 8px 20px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; gap: 12px; font-size: 12px; }}
#controls label {{ cursor: pointer; }}
#controls input[type=checkbox] {{ margin-right: 4px; }}
#main {{ display: flex; height: calc(100vh - 90px); }}
#graph {{ flex: 1; position: relative; }}
#sidebar {{ width: 340px; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; padding: 12px; font-size: 12px; }}
#sidebar h2 {{ font-size: 13px; margin-bottom: 8px; color: #58a6ff; }}
.finding {{ margin-bottom: 10px; padding: 8px; border-radius: 4px; border-left: 3px solid; cursor: pointer; transition: background 0.15s; }}
.finding:hover {{ filter: brightness(1.2); }}
.finding.error {{ border-color: #da3633; background: #1c1012; }}
.finding.warning {{ border-color: #d29922; background: #1c1a10; }}
.finding.selected {{ outline: 2px solid #58a6ff; outline-offset: -2px; }}
.finding .type {{ font-weight: 600; font-size: 11px; text-transform: uppercase; }}
.finding .msg {{ margin-top: 4px; color: #8b949e; line-height: 1.4; }}
.finding .fix {{ margin-top: 4px; color: #7ee787; font-size: 11px; }}
.finding .view-code {{ margin-top: 6px; display: inline-block; font-size: 10px; color: #58a6ff; cursor: pointer; border: 1px solid #30363d; padding: 2px 8px; border-radius: 3px; }}
.finding .view-code:hover {{ background: #21262d; }}
.metric {{ display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #21262d; }}
.metric .val {{ color: #58a6ff; }}
.func-item {{ display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #21262d; cursor: pointer; }}
.func-item:hover {{ color: #58a6ff; }}
/* About modal */
#about-btn {{ cursor: pointer; color: #8b949e; font-size: 12px; border: 1px solid #30363d; padding: 3px 10px; border-radius: 12px; }}
#about-btn:hover {{ color: #c9d1d9; background: #21262d; }}
#about-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 200; justify-content: center; align-items: center; }}
#about-overlay.open {{ display: flex; }}
#about-modal {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; max-width: 560px; width: 90vw; max-height: 80vh; overflow-y: auto; padding: 24px; font-size: 13px; line-height: 1.6; }}
#about-modal h2 {{ color: #58a6ff; font-size: 16px; margin-bottom: 12px; }}
#about-modal h3 {{ color: #d2a8ff; font-size: 13px; margin-top: 16px; margin-bottom: 4px; }}
#about-modal p {{ color: #8b949e; margin-bottom: 8px; }}
#about-modal a {{ color: #58a6ff; text-decoration: none; }}
#about-modal a:hover {{ text-decoration: underline; }}
#about-modal .close-about {{ float: right; cursor: pointer; color: #8b949e; font-size: 20px; line-height: 1; }}
#about-modal .close-about:hover {{ color: #c9d1d9; }}
#about-modal code {{ background: #0d1117; padding: 2px 6px; border-radius: 3px; font-size: 12px; }}
#about-modal .legend {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 8px 0; }}
#about-modal .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; }}
#about-modal .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
#tooltip {{ position: absolute; background: #1c2128; border: 1px solid #30363d; padding: 8px 12px; border-radius: 6px; font-size: 11px; pointer-events: none; display: none; max-width: 300px; z-index: 10; }}
#tooltip .btn {{ pointer-events: auto; margin-top: 6px; display: inline-block; font-size: 10px; color: #58a6ff; cursor: pointer; border: 1px solid #30363d; padding: 2px 8px; border-radius: 3px; background: #161b22; }}
#tooltip .btn:hover {{ background: #21262d; }}

/* Code panel overlay */
#code-panel {{ display: none; position: fixed; top: 0; right: 0; width: 55vw; height: 100vh; background: #0d1117; border-left: 2px solid #30363d; z-index: 100; flex-direction: column; }}
#code-panel.open {{ display: flex; }}
#code-panel-header {{ padding: 10px 16px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; font-size: 13px; flex-shrink: 0; }}
#code-panel-header .close {{ cursor: pointer; color: #8b949e; font-size: 18px; padding: 0 6px; }}
#code-panel-header .close:hover {{ color: #c9d1d9; }}
#code-panel-tabs {{ display: flex; gap: 0; background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0; }}
.code-tab {{ padding: 6px 16px; font-size: 11px; cursor: pointer; border-bottom: 2px solid transparent; color: #8b949e; }}
.code-tab.active {{ color: #58a6ff; border-color: #58a6ff; }}
.code-tab:hover {{ color: #c9d1d9; }}
#code-panel-body {{ flex: 1; overflow-y: auto; padding: 0; }}
#code-panel-body pre {{ margin: 0; padding: 12px 0; font-size: 12px; line-height: 1.6; }}
.code-line {{ display: flex; padding: 0 16px; }}
.code-line:hover {{ background: #161b22; }}
.code-line.highlighted {{ background: #2d1b00; }}
.code-line.error-line {{ background: #300a0a; }}
.code-line .lineno {{ color: #484f58; min-width: 45px; text-align: right; padding-right: 16px; user-select: none; flex-shrink: 0; }}
.code-line .code {{ white-space: pre; color: #c9d1d9; }}
/* Finding banner inside code panel */
.code-finding-banner {{ margin: 0 16px 0 16px; padding: 8px 12px; border-radius: 4px; font-size: 11px; line-height: 1.5; }}
.code-finding-banner.error {{ background: #1c1012; border: 1px solid #da3633; }}
.code-finding-banner.warning {{ background: #1c1a10; border: 1px solid #d29922; }}
.code-finding-banner .fix-text {{ color: #7ee787; margin-top: 4px; }}
/* Normalized view */
.norm-line {{ display: flex; padding: 2px 16px; font-size: 11px; }}
.norm-line.intermediate {{ opacity: 0.5; }}
.norm-line .var {{ color: #d2a8ff; min-width: 120px; }}
.norm-line .op {{ color: #8b949e; }}
.norm-line .ln {{ color: #484f58; min-width: 40px; text-align: right; padding-right: 12px; }}
svg text {{ font-family: 'SF Mono', 'Fira Code', monospace; }}
</style>
</head>
<body>
<div id="header">
  <h1>codetopo &mdash; <span id="filepath"></span></h1>
  <div style="display:flex;gap:10px;align-items:center">
    <span id="about-btn" onclick="document.getElementById('about-overlay').classList.add('open')">About</span>
    <span class="status" id="status"></span>
  </div>
</div>

<!-- About modal -->
<div id="about-overlay" onclick="if(event.target===this)this.classList.remove('open')">
  <div id="about-modal">
    <span class="close-about" onclick="document.getElementById('about-overlay').classList.remove('open')">&times;</span>
    <h2>codetopo</h2>
    <p>Algebraic topology for code quality. Catches structural problems in Python code &mdash; copy-paste, abstraction bloat, circular dependencies &mdash; without an LLM judge. Deterministic and Goodhart-proof by design.</p>

    <h3>What you're looking at</h3>
    <p>This is an interactive visualization of the <b>data flow graph</b> extracted from a Python source file. Each node is a variable or intermediate value. Each edge means "this value was computed from that one." codetopo normalizes the code into single-operation statements, builds the graph, then runs topology analysis to find structural problems.</p>

    <div class="legend">
      <div class="legend-item"><span class="dot" style="background:#da3633"></span> Error node</div>
      <div class="legend-item"><span class="dot" style="background:#d29922"></span> Warning node</div>
      <div class="legend-item"><span class="dot" style="background:#238636"></span> Clean node</div>
      <div class="legend-item"><span class="dot" style="background:#484f58;width:6px;height:6px"></span> Intermediate (normalizer-introduced)</div>
    </div>
    <p>Node border colors indicate which function the variable belongs to. Drag nodes to rearrange. Scroll to zoom.</p>

    <h3>How to use</h3>
    <p><b>Click a node</b> to open the source code panel, scrolled to that line.<br>
    <b>Click "View Code &amp; Topology"</b> on a finding to see the source with error lines highlighted and the normalized data flow.<br>
    <b>Click a function name</b> in the sidebar to view its source and normalized form.<br>
    <b>Switch to "Normalized" tab</b> in the code panel to see how codetopo decomposes the code before analysis.<br>
    <b>Press Esc</b> to close the code panel.</p>

    <h3>Finding types</h3>
    <p><code>structural_duplication</code> &mdash; Two functions have isomorphic data flow graphs (same shape, different names). Copy-paste detected topologically.<br>
    <code>statement_multitask</code> &mdash; A variable feeds too many downstream operations simultaneously.<br>
    <code>abstraction_bloat</code> &mdash; An intermediate variable exists but has no structural utility.<br>
    <code>bridge_bottleneck</code> &mdash; A single call edge connecting two subsystems. Fragility point.<br>
    <code>circular_dependency</code> &mdash; Functions calling each other in a cycle.<br>
    <code>isolated_component</code> &mdash; Code disconnected from the main data flow.</p>

    <h3>Generate your own</h3>
    <p><code>pip install agentic-coding-topology</code><br>
    <code>codetopo viz yourfile.py --open</code></p>

    <p style="margin-top:16px"><a href="https://github.com/M0nkeyFl0wer/agentic-coding-topology" target="_blank">github.com/M0nkeyFl0wer/agentic-coding-topology</a></p>
  </div>
</div>
<div id="controls">
  <label><input type="checkbox" id="showDFG" checked> Data Flow</label>
  <label><input type="checkbox" id="showCG"> Call Graph</label>
  <label><input type="checkbox" id="showIntermediate" checked> Intermediates</label>
  <label><input type="checkbox" id="showLabels" checked> Labels</label>
</div>
<div id="main">
  <div id="graph"></div>
  <div id="sidebar">
    <h2>Findings</h2>
    <div id="findings"></div>
    <h2 style="margin-top:16px">Metrics</h2>
    <div id="metrics"></div>
    <h2 style="margin-top:16px">Functions</h2>
    <div id="functions"></div>
  </div>
</div>
<div id="tooltip"></div>

<!-- Code panel overlay -->
<div id="code-panel">
  <div id="code-panel-header">
    <span id="code-panel-title">Source Code</span>
    <span class="close" onclick="closeCodePanel()">&times;</span>
  </div>
  <div id="code-panel-tabs">
    <div class="code-tab active" data-tab="source" onclick="switchTab('source')">Source</div>
    <div class="code-tab" data-tab="normalized" onclick="switchTab('normalized')">Normalized</div>
  </div>
  <div id="code-panel-body"></div>
</div>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA = {data_json};
const escHtml = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
let currentTab = 'source';
let currentContext = null; // {{funcName, lines, finding}}

// Header
document.getElementById('filepath').textContent = DATA.source_path;
const statusEl = document.getElementById('status');
statusEl.textContent = DATA.passed ? 'PASS' : 'FAIL';
statusEl.className = 'status ' + (DATA.passed ? 'pass' : 'fail');

// --- Code Panel ---
function openCodePanel(context) {{
  currentContext = context;
  document.getElementById('code-panel').classList.add('open');
  document.getElementById('code-panel-title').textContent =
    context.funcName ? `${{context.funcName}}` : 'Source Code';
  renderTab(currentTab);
}}

function closeCodePanel() {{
  document.getElementById('code-panel').classList.remove('open');
  currentContext = null;
}}

function switchTab(tab) {{
  currentTab = tab;
  document.querySelectorAll('.code-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  renderTab(tab);
}}

function renderTab(tab) {{
  const body = document.getElementById('code-panel-body');
  if (!currentContext) return;

  if (tab === 'source') {{
    const highlightLines = new Set(currentContext.lines || []);
    const errorLines = new Set(currentContext.errorLines || []);
    let html = '';

    // Show finding banner if present
    if (currentContext.finding) {{
      const f = currentContext.finding;
      html += `<div class="code-finding-banner ${{f.severity}}">
        <b>${{f.severity.toUpperCase()}}: ${{f.type}}</b><br>
        ${{escHtml(f.message)}}<br>
        <div class="fix-text">Fix: ${{escHtml(f.fix)}}</div>
      </div>`;
    }}

    html += '<pre>';
    DATA.source_lines.forEach((line, i) => {{
      const num = i + 1;
      const cls = errorLines.has(num) ? 'code-line error-line' :
                  highlightLines.has(num) ? 'code-line highlighted' : 'code-line';
      html += `<div class="${{cls}}"><span class="lineno">${{num}}</span><span class="code">${{escHtml(line)}}</span></div>`;
    }});
    html += '</pre>';
    body.innerHTML = html;

    // Scroll to first highlighted line
    const first = Math.min(...[...highlightLines, ...errorLines].filter(n => n > 0));
    if (first < Infinity) {{
      const el = body.querySelectorAll('.code-line')[first - 1];
      if (el) el.scrollIntoView({{ block: 'center' }});
    }}
  }} else if (tab === 'normalized') {{
    let html = '';

    if (currentContext.finding) {{
      const f = currentContext.finding;
      html += `<div class="code-finding-banner ${{f.severity}}">
        <b>${{f.severity.toUpperCase()}}: ${{f.type}}</b><br>
        ${{escHtml(f.message)}}
      </div>`;
    }}

    const funcs = currentContext.funcName ? [currentContext.funcName] :
      (currentContext.finding?.nodes || []).filter(n => DATA.normalized[n]);

    // If finding involves multiple functions, show them side by side
    if (funcs.length === 0) {{
      html += '<div style="padding:16px;color:#8b949e">No normalized form available (module-level code)</div>';
    }}
    funcs.forEach(fn => {{
      const stmts = DATA.normalized[fn] || [];
      html += `<div style="padding:8px 16px;color:#58a6ff;font-size:12px;border-bottom:1px solid #21262d"><b>def ${{fn}}()</b> &mdash; ${{stmts.length}} operations</div>`;
      stmts.forEach(s => {{
        const cls = s.intermediate ? 'norm-line intermediate' : 'norm-line';
        html += `<div class="${{cls}}"><span class="ln">L${{s.line}}</span><span class="var">${{escHtml(s.var)}}</span><span class="op">= ${{escHtml(s.op)}}</span></div>`;
      }});
    }});
    body.innerHTML = html;
  }}
}}

// Esc to close
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeCodePanel(); }});

// --- Sidebar: findings (clickable) ---
const findingsEl = document.getElementById('findings');
DATA.findings.forEach((f, idx) => {{
  const div = document.createElement('div');
  div.className = 'finding ' + f.severity;
  div.innerHTML = `<div class="type">${{f.severity}} — ${{f.type}}</div>
    <div class="msg">${{f.message}}</div>
    <div class="fix">${{f.fix}}</div>
    <span class="view-code">View Code &amp; Topology</span>`;
  div.querySelector('.view-code').addEventListener('click', (e) => {{
    e.stopPropagation();
    // Highlight finding nodes in graph
    highlightNodes(f.nodes);
    openCodePanel({{
      funcName: null,
      lines: f.source_lines || [],
      errorLines: f.source_lines || [],
      finding: f,
    }});
  }});
  div.addEventListener('click', () => highlightNodes(f.nodes));
  findingsEl.appendChild(div);
}});
if (!DATA.findings.length) findingsEl.innerHTML = '<div style="color:#7ee787">No findings</div>';

// Sidebar: metrics
const metricsEl = document.getElementById('metrics');
Object.entries(DATA.metrics).forEach(([k, v]) => {{
  const div = document.createElement('div');
  div.className = 'metric';
  div.innerHTML = `<span>${{k}}</span><span class="val">${{typeof v === 'number' ? v.toFixed(4) : v}}</span>`;
  metricsEl.appendChild(div);
}});

// Sidebar: functions (clickable)
const funcsEl = document.getElementById('functions');
Object.entries(DATA.functions).forEach(([name, info]) => {{
  const div = document.createElement('div');
  div.className = 'func-item';
  div.innerHTML = `<span>${{name}}</span><span class="val">${{info.node_count}}n / ${{info.edge_count}}e</span>`;
  div.addEventListener('click', () => {{
    highlightNodes(info.nodes);
    const stmts = DATA.normalized[name] || [];
    const lines = stmts.map(s => s.line).filter(l => l > 0);
    openCodePanel({{ funcName: name, lines: lines, errorLines: [], finding: null }});
  }});
  funcsEl.appendChild(div);
}});

// --- Highlight nodes in graph ---
function highlightNodes(nodeIds) {{
  const ids = new Set(nodeIds);
  node.attr('opacity', d => ids.size === 0 || ids.has(d.id) ? 1 : 0.1)
      .attr('r', d => ids.has(d.id) ? d.radius * 1.8 : d.radius);
  label.attr('opacity', d => ids.size === 0 || ids.has(d.id) ? 1 : 0.1);
  link.attr('opacity', d => {{
    const sid = typeof d.source === 'string' ? d.source : d.source.id;
    const tid = typeof d.target === 'string' ? d.target : d.target.id;
    return ids.size === 0 || ids.has(sid) || ids.has(tid) ? 1 : 0.05;
  }});
  // Reset after 5 seconds
  clearTimeout(window._hlTimeout);
  window._hlTimeout = setTimeout(() => {{
    node.attr('opacity', d => d.is_intermediate ? 0.6 : 1).attr('r', d => d.radius);
    label.attr('opacity', 1);
    link.attr('opacity', 1);
  }}, 5000);
}}

// --- Graph visualization ---
const container = document.getElementById('graph');
const width = container.clientWidth;
const height = container.clientHeight;

const svg = d3.select('#graph').append('svg')
  .attr('width', width).attr('height', height);

const g = svg.append('g');

svg.call(d3.zoom().scaleExtent([0.1, 8]).on('zoom', (e) => g.attr('transform', e.transform)));

const severityColor = {{ error: '#da3633', warning: '#d29922', clean: '#238636' }};
const funcColors = d3.scaleOrdinal(d3.schemeTableau10);

const nodeToFunc = {{}};
Object.entries(DATA.functions).forEach(([fname, info]) => {{
  info.nodes.forEach(n => {{ nodeToFunc[n] = fname; }});
}});

const nodes = DATA.data_flow.nodes.map(n => ({{
  ...n, group: nodeToFunc[n.id] || '__module__',
  color: severityColor[n.severity],
  radius: n.findings.length > 0 ? 8 : (n.is_intermediate ? 3 : 5),
}}));

const nodeMap = new Map(nodes.map(n => [n.id, n]));
const links = DATA.data_flow.edges
  .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
  .map(e => ({{ source: e.source, target: e.target, operation: e.operation }}));

// --- Compute group centers for function clustering ---
const groupNames = [...new Set(nodes.map(n => n.group))];
const groupCenters = {{}};
const cols = Math.ceil(Math.sqrt(groupNames.length));
groupNames.forEach((gn, i) => {{
  const col = i % cols;
  const row = Math.floor(i / cols);
  const spacingX = width / (cols + 1);
  const spacingY = height / (Math.ceil(groupNames.length / cols) + 1);
  groupCenters[gn] = {{ x: spacingX * (col + 1), y: spacingY * (row + 1) }};
}});

// --- Force simulation with function group clustering ---
const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(links).id(d => d.id).distance(30).strength(0.7))
  .force('charge', d3.forceManyBody().strength(-40))
  .force('collision', d3.forceCollide().radius(d => d.radius + 3))
  // Pull nodes toward their function's group center
  .force('groupX', d3.forceX(d => groupCenters[d.group]?.x || width/2).strength(0.15))
  .force('groupY', d3.forceY(d => groupCenters[d.group]?.y || height/2).strength(0.15));

// Arrow markers
svg.append('defs').append('marker')
  .attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
  .attr('refX', 15).attr('refY', 0)
  .attr('markerWidth', 6).attr('markerHeight', 6)
  .attr('orient', 'auto')
  .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', '#484f58');

// --- Convex hull backgrounds for function groups ---
const hullLayer = g.append('g').attr('class', 'hulls');

function updateHulls() {{
  const groups = d3.group(nodes, d => d.group);
  const hullData = [];
  groups.forEach((groupNodes, groupName) => {{
    if (groupNodes.length < 2) return; // need 2+ for a hull
    const points = groupNodes.map(d => [d.x, d.y]);
    // Pad the hull so it wraps around nodes
    const padded = [];
    points.forEach(([px, py]) => {{
      for (let a = 0; a < Math.PI * 2; a += Math.PI / 3) {{
        padded.push([px + Math.cos(a) * 20, py + Math.sin(a) * 20]);
      }}
    }});
    const hull = d3.polygonHull(padded);
    if (hull) hullData.push({{ group: groupName, path: hull }});
  }});

  const hulls = hullLayer.selectAll('path').data(hullData, d => d.group);
  hulls.enter().append('path')
    .attr('fill', d => funcColors(d.group))
    .attr('opacity', 0.06)
    .attr('stroke', d => funcColors(d.group))
    .attr('stroke-width', 1)
    .attr('stroke-opacity', 0.2)
    .merge(hulls)
    .attr('d', d => 'M' + d.path.join('L') + 'Z');
  hulls.exit().remove();
}}

// --- Group labels ---
const groupLabelLayer = g.append('g').attr('class', 'group-labels');

function updateGroupLabels() {{
  const groups = d3.group(nodes, d => d.group);
  const labelData = [];
  groups.forEach((groupNodes, groupName) => {{
    if (groupName === '__module__' && groupNodes.length < 3) return;
    const cx = d3.mean(groupNodes, d => d.x);
    const cy = d3.min(groupNodes, d => d.y) - 14;
    const displayName = groupName === '__module__' ? 'module' : groupName;
    labelData.push({{ group: groupName, x: cx, y: cy, name: displayName }});
  }});

  const labels = groupLabelLayer.selectAll('text').data(labelData, d => d.group);
  labels.enter().append('text')
    .attr('fill', d => funcColors(d.group))
    .attr('font-size', 10)
    .attr('text-anchor', 'middle')
    .attr('opacity', 0.5)
    .merge(labels)
    .attr('x', d => d.x).attr('y', d => d.y)
    .text(d => d.name);
  labels.exit().remove();
}}

// Draw edges
const link = g.append('g').selectAll('line')
  .data(links).join('line')
  .attr('stroke', '#484f58').attr('stroke-width', 1)
  .attr('marker-end', 'url(#arrow)');

// Draw nodes
const node = g.append('g').selectAll('circle')
  .data(nodes).join('circle')
  .attr('r', d => d.radius)
  .attr('fill', d => d.color)
  .attr('stroke', d => funcColors(d.group))
  .attr('stroke-width', 1.5)
  .attr('opacity', d => d.is_intermediate ? 0.6 : 1)
  .call(d3.drag()
    .on('start', (e, d) => {{ if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
    .on('drag', (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
    .on('end', (e, d) => {{ if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}));

// Node labels
const label = g.append('g').selectAll('text')
  .data(nodes).join('text')
  .text(d => d.id)
  .attr('font-size', d => d.findings.length > 0 ? 10 : 8)
  .attr('fill', d => d.findings.length > 0 ? d.color : '#8b949e')
  .attr('dx', 10).attr('dy', 3);

// Tooltip + click to open code panel
const tooltip = document.getElementById('tooltip');
node.on('mouseover', (e, d) => {{
  let html = `<b>${{d.id}}</b>`;
  if (d.group !== '__module__') html += ` <span style="color:${{funcColors(d.group)}}">${{d.group}}</span>`;
  html += '<br>';
  if (d.operation) html += `<span style="color:#8b949e">${{escHtml(d.operation)}}</span><br>`;
  html += `Line: ${{d.source_line}} | In: ${{d.in_degree}} Out: ${{d.out_degree}}<br>`;
  if (d.findings.length) {{
    d.findings.forEach(f => {{
      const c = f.severity === 'error' ? '#da3633' : '#d29922';
      html += `<span style="color:${{c}}">${{f.type}}</span><br>`;
    }});
  }}
  html += `<span style="color:#484f58">Click to view code</span>`;
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 10) + 'px';
}})
.on('mouseout', () => {{ tooltip.style.display = 'none'; }})
.on('click', (e, d) => {{
  tooltip.style.display = 'none';
  const funcName = nodeToFunc[d.id] || null;
  const finding = d.findings.length > 0 ? DATA.findings.find(f => f.nodes.includes(d.id)) : null;
  openCodePanel({{
    funcName: funcName,
    lines: d.source_line > 0 ? [d.source_line] : [],
    errorLines: finding ? (finding.source_lines || []) : [],
    finding: finding,
  }});
}});

// Tick: update positions + hulls
simulation.on('tick', () => {{
  link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  node.attr('cx', d => d.x).attr('cy', d => d.y);
  label.attr('x', d => d.x).attr('y', d => d.y);
  updateHulls();
  updateGroupLabels();
}});

// Controls
document.getElementById('showIntermediate').addEventListener('change', (e) => {{
  const show = e.target.checked;
  node.attr('display', d => (!show && d.is_intermediate) ? 'none' : null);
  label.attr('display', d => (!show && d.is_intermediate) ? 'none' : null);
  link.attr('display', d => {{
    const s = nodeMap.get(typeof d.source === 'string' ? d.source : d.source.id);
    const t = nodeMap.get(typeof d.target === 'string' ? d.target : d.target.id);
    return (!show && (s?.is_intermediate || t?.is_intermediate)) ? 'none' : null;
  }});
}});

document.getElementById('showLabels').addEventListener('change', (e) => {{
  label.attr('display', e.target.checked ? null : 'none');
}});

// Click on graph background to close code panel
svg.on('click', (e) => {{
  if (e.target === svg.node()) closeCodePanel();
}});
</script>
</body>
</html>"""


def generate_viz(source_path: str, output_path: str | None = None,
                 config: dict | None = None) -> str:
    """Generate visualization and return the output path."""
    data = extract_graph_data(source_path, config)

    if output_path is None:
        output_path = str(Path(source_path).with_suffix('.topology.html'))

    html = generate_html(data)
    Path(output_path).write_text(html)
    return output_path
