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

    return {
        "source_path": source_path,
        "summary": report.summary(),
        "passed": report.passed,
        "metrics": report.metrics,
        "findings": [{
            "type": f.finding_type.value,
            "severity": f.severity.value,
            "message": f.message,
            "nodes": f.nodes,
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
#graph {{ flex: 1; }}
#sidebar {{ width: 320px; background: #161b22; border-left: 1px solid #30363d; overflow-y: auto; padding: 12px; font-size: 12px; }}
#sidebar h2 {{ font-size: 13px; margin-bottom: 8px; color: #58a6ff; }}
.finding {{ margin-bottom: 10px; padding: 8px; border-radius: 4px; border-left: 3px solid; }}
.finding.error {{ border-color: #da3633; background: #1c1012; }}
.finding.warning {{ border-color: #d29922; background: #1c1a10; }}
.finding .type {{ font-weight: 600; font-size: 11px; text-transform: uppercase; }}
.finding .msg {{ margin-top: 4px; color: #8b949e; line-height: 1.4; }}
.finding .fix {{ margin-top: 4px; color: #7ee787; font-size: 11px; }}
.metric {{ display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #21262d; }}
.metric .val {{ color: #58a6ff; }}
#tooltip {{ position: absolute; background: #1c2128; border: 1px solid #30363d; padding: 8px 12px; border-radius: 6px; font-size: 11px; pointer-events: none; display: none; max-width: 300px; z-index: 10; }}
svg text {{ font-family: 'SF Mono', 'Fira Code', monospace; }}
</style>
</head>
<body>
<div id="header">
  <h1>codetopo &mdash; <span id="filepath"></span></h1>
  <span class="status" id="status"></span>
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

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA = {data_json};

// Header
document.getElementById('filepath').textContent = DATA.source_path;
const statusEl = document.getElementById('status');
statusEl.textContent = DATA.passed ? 'PASS' : 'FAIL';
statusEl.className = 'status ' + (DATA.passed ? 'pass' : 'fail');

// Sidebar: findings
const findingsEl = document.getElementById('findings');
DATA.findings.forEach(f => {{
  const div = document.createElement('div');
  div.className = 'finding ' + f.severity;
  div.innerHTML = `<div class="type">${{f.severity}} — ${{f.type}}</div>
    <div class="msg">${{f.message}}</div>
    <div class="fix">${{f.fix}}</div>`;
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

// Sidebar: functions
const funcsEl = document.getElementById('functions');
Object.entries(DATA.functions).forEach(([name, info]) => {{
  const div = document.createElement('div');
  div.className = 'metric';
  div.innerHTML = `<span>${{name}}</span><span class="val">${{info.node_count}}n / ${{info.edge_count}}e</span>`;
  funcsEl.appendChild(div);
}});

// Graph visualization
const container = document.getElementById('graph');
const width = container.clientWidth;
const height = container.clientHeight;

const svg = d3.select('#graph').append('svg')
  .attr('width', width).attr('height', height);

const g = svg.append('g');

// Zoom
svg.call(d3.zoom().scaleExtent([0.1, 8]).on('zoom', (e) => g.attr('transform', e.transform)));

// Color scales
const severityColor = {{ error: '#da3633', warning: '#d29922', clean: '#238636' }};
const funcColors = d3.scaleOrdinal(d3.schemeTableau10);

// Assign function groups
const nodeToFunc = {{}};
Object.entries(DATA.functions).forEach(([fname, info], i) => {{
  info.nodes.forEach(n => {{ nodeToFunc[n] = fname; }});
}});

// Build D3 data
const nodes = DATA.data_flow.nodes.map(n => ({{
  ...n, group: nodeToFunc[n.id] || '__module__',
  color: severityColor[n.severity],
  radius: n.findings.length > 0 ? 8 : (n.is_intermediate ? 3 : 5),
}}));

const nodeMap = new Map(nodes.map(n => [n.id, n]));
const links = DATA.data_flow.edges
  .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
  .map(e => ({{ source: e.source, target: e.target, operation: e.operation }}));

// Call graph edges (separate layer)
const cgNodes = DATA.call_graph.nodes.map(n => ({{ id: 'cg_' + n.id, label: n.id, isCG: true }}));
const cgLinks = DATA.call_graph.edges.map(e => ({{ source: 'cg_' + e.source, target: 'cg_' + e.target, isCG: true }}));

// Simulation
const simulation = d3.forceSimulation(nodes)
  .force('link', d3.forceLink(links).id(d => d.id).distance(40).strength(0.5))
  .force('charge', d3.forceManyBody().strength(-60))
  .force('center', d3.forceCenter(width / 2, height / 2))
  .force('collision', d3.forceCollide().radius(d => d.radius + 2))
  .force('group', d3.forceX(width / 2).strength(0.02))
  .force('groupY', d3.forceY(height / 2).strength(0.02));

// Arrow markers
svg.append('defs').append('marker')
  .attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
  .attr('refX', 15).attr('refY', 0)
  .attr('markerWidth', 6).attr('markerHeight', 6)
  .attr('orient', 'auto')
  .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', '#484f58');

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

// Labels
const label = g.append('g').selectAll('text')
  .data(nodes).join('text')
  .text(d => d.id)
  .attr('font-size', d => d.findings.length > 0 ? 10 : 8)
  .attr('fill', d => d.findings.length > 0 ? d.color : '#8b949e')
  .attr('dx', 10).attr('dy', 3);

// Tooltip
const tooltip = document.getElementById('tooltip');
node.on('mouseover', (e, d) => {{
  let html = `<b>${{d.id}}</b><br>`;
  if (d.operation) html += `<span style="color:#8b949e">${{d.operation}}</span><br>`;
  html += `Line: ${{d.source_line}} | In: ${{d.in_degree}} Out: ${{d.out_degree}}<br>`;
  if (d.findings.length) {{
    d.findings.forEach(f => {{
      const c = f.severity === 'error' ? '#da3633' : '#d29922';
      html += `<span style="color:${{c}}">${{f.type}}: ${{f.message.slice(0,80)}}...</span><br>`;
    }});
  }}
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';
  tooltip.style.left = (e.pageX + 12) + 'px';
  tooltip.style.top = (e.pageY - 10) + 'px';
}})
.on('mouseout', () => {{ tooltip.style.display = 'none'; }});

// Tick
simulation.on('tick', () => {{
  link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
  node.attr('cx', d => d.x).attr('cy', d => d.y);
  label.attr('x', d => d.x).attr('y', d => d.y);
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
