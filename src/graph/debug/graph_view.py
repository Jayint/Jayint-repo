"""Constructed dep-graph -> browsable HTML collection.

Turns one or more ``DepGraph``s into a single self-contained HTML page: a
"collection" the frontend switches between (e.g. the graph after each construction
stage, so you watch it assemble — or one graph per repo).  Nodes are laid out in
horizontal bands by execution Layer (Runtime at the top down to Tests), coloured
with the SAME encoding as ``docs/sample-dependency-graph-visualization.html`` — fill
= ``discovered_by`` (pipeline stage), border = host-certified ``state``, glyph =
node type — so the encoding is already familiar.  Edges are drawn (on a canvas
overlay) only for the selected node, so a 98-node closure stays legible instead of
becoming a hairball.

Self-contained: the graph data is embedded inline and everything renders with no
network fetch, no CDN, no external font — it opens straight from disk and satisfies
an Artifact CSP.  GraphML (the interchange format compatible with the existing
vis-network viewer / Gephi / yEd) is emitted separately by ``scripts/graph_view.py``
via :func:`graph.compile.export.to_graphml`.
"""

from __future__ import annotations

import json

from graph.compile.export import _build_from_source_value, _fix_value, _label
from graph.model import DepGraph, Edge, Node


def _fe_node(n: Node) -> dict:
    return {
        "id": n.id,
        "label": _label(n),
        "type": n.type.value,
        "layer": n.layer.value,
        "state": n.state.value,
        "discovered_by": n.discovered_by.value,
        "cycle": n.discovered_cycle,
        "version": n.version or "",
        "unresolved": (bool(n.data.get("unresolved")) if "unresolved" in n.data else None),
        "check": n.check_command or "",
        "fix": _fix_value(n),
        "evidence": n.evidence or "",
        "bfs": _build_from_source_value(n),
        "provenance": n.provenance or "",
    }


def _fe_edge(e: Edge) -> dict:
    return {
        "source": e.src,
        "target": e.dst,
        "relation": e.relation.value,
        "marker": e.marker or "",
    }


def collection_payload(items: list, title: str = "dep-graph collection") -> dict:
    """Build the frontend payload from ``[(name, DepGraph), ...]`` (order preserved).

    Each graph carries its frontend nodes/edges plus ``by_type`` / ``by_state``
    counts for the summary strip."""
    graphs = []
    for name, g in items:
        by_type: dict[str, int] = {}
        by_state: dict[str, int] = {}
        for n in g.nodes:
            by_type[n.type.value] = by_type.get(n.type.value, 0) + 1
            by_state[n.state.value] = by_state.get(n.state.value, 0) + 1
        graphs.append(
            {
                "name": name,
                "n_nodes": len(g.nodes),
                "n_edges": len(g.edges),
                "by_type": by_type,
                "by_state": by_state,
                "nodes": [_fe_node(n) for n in g.nodes],
                "edges": [_fe_edge(e) for e in g.edges],
            }
        )
    return {"title": title, "graphs": graphs}


# --------------------------------------------------------------------------- #
# HTML.  ``__DATA__`` / ``__TITLE__`` are substituted (never an f-string / .format
# — the CSS+JS below is full of literal braces).
# --------------------------------------------------------------------------- #
_BODY = r"""
<style>
  :root{
    --bg:#0e1526; --panel:#182338; --panel2:#0f1830; --line:#2a3854;
    --ink:#dce4f0; --muted:#8394ad; --accent:#67d3f5;
    --sat:#34d399; --miss:#f87171; --unk:#64748b;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font-family:var(--sans);}
  #app{display:grid;grid-template-columns:1fr 340px;grid-template-rows:auto auto minmax(0,1fr);height:100vh;min-height:0;}
  header{grid-column:1/-1;grid-row:1;padding:11px 18px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}
  header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.01em;}
  header .sub{font-size:12px;color:var(--muted);}
  header code{font-family:var(--mono);background:var(--panel2);color:var(--accent);padding:1px 6px;border-radius:4px;font-size:12px;}
  #picker{grid-column:1/-1;grid-row:2;display:flex;align-items:center;gap:8px;padding:9px 16px;border-bottom:1px solid var(--line);background:var(--panel2);overflow-x:auto;}
  .stagebtn{font-family:var(--mono);font-size:11px;white-space:nowrap;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--muted);cursor:pointer;transition:background .12s,color .12s,border-color .12s;}
  .stagebtn:hover{color:var(--ink);border-color:var(--accent);}
  .stagebtn.active{background:var(--accent);color:#06121c;border-color:var(--accent);font-weight:650;}
  .stagebtn .ct{opacity:.75;margin-left:5px;}
  #picker .spacer{flex:1;}
  #search{font-family:var(--mono);font-size:12px;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);min-width:150px;}
  #search::placeholder{color:var(--muted);}
  label.toggle{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:5px;white-space:nowrap;cursor:pointer;}
  #canvaswrap{grid-column:1;grid-row:3;position:relative;min-height:0;min-width:0;overflow:auto;background:radial-gradient(circle at 26% 12%,#13223f,#0e1526 70%);}
  #bands{position:relative;padding:14px 16px 40px;min-width:min-content;}
  #edges{position:absolute;left:0;top:0;pointer-events:none;}
  .band{position:relative;margin-bottom:10px;}
  .band-label{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 5px 2px;}
  .band-label .n{color:var(--accent);}
  .band-nodes{display:flex;flex-wrap:wrap;gap:7px;}
  .node{position:relative;display:flex;align-items:center;gap:6px;max-width:280px;padding:4px 9px 4px 7px;border:2px solid var(--unk);border-left-width:5px;border-radius:7px;background:var(--panel);cursor:pointer;transition:transform .1s,box-shadow .1s,opacity .1s;}
  .node:hover{transform:translateY(-1px);box-shadow:0 3px 12px rgba(0,0,0,.4);}
  .node .glyph{font-size:12px;line-height:1;flex:0 0 auto;}
  .node .nlabel{font-family:var(--mono);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .node.sel{box-shadow:0 0 0 2px var(--accent),0 4px 16px rgba(0,0,0,.5);}
  #bands.dimming .node:not(.sel):not(.nbr){opacity:.22;}
  .node.hide{display:none;}
  .node .ur{width:6px;height:6px;border-radius:50%;background:var(--miss);flex:0 0 auto;}
  aside{grid-column:2;grid-row:3;min-height:0;border-left:1px solid var(--line);background:var(--panel);overflow-y:auto;padding:14px;}
  aside h2{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 8px;}
  .sep{height:1px;background:var(--line);margin:13px 0;}
  .statrow{display:flex;flex-wrap:wrap;gap:6px;font-family:var(--mono);font-size:11px;}
  .chip{padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--ink);}
  .pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:650;font-family:var(--mono);}
  .s-satisfied{background:#064e3b;color:#6ee7b7;} .s-missing{background:#7f1d1d;color:#fca5a5;} .s-unknown{background:#243247;color:#cbd5e1;}
  .legrow{display:flex;align-items:center;gap:8px;font-size:11px;margin:4px 0;color:var(--ink);}
  .sw{width:13px;height:13px;border-radius:3px;flex:0 0 auto;}
  .glyphsw{width:15px;text-align:center;flex:0 0 auto;}
  #detail{font-size:12px;line-height:1.5;}
  #detail .k{color:var(--muted);}
  #detail code{font-family:var(--mono);background:var(--panel2);color:var(--accent);padding:1px 5px;border-radius:4px;word-break:break-all;display:inline-block;margin-top:2px;}
  #detail .title{font-family:var(--mono);font-weight:650;margin-bottom:6px;word-break:break-all;}
  .navlist{display:flex;flex-direction:column;gap:3px;margin-top:4px;}
  .navlist button{text-align:left;font-family:var(--mono);font-size:11px;color:var(--accent);background:transparent;border:0;padding:2px 0;cursor:pointer;}
  .navlist button:hover{text-decoration:underline;}
  .muted{color:var(--muted);}
  @media (prefers-reduced-motion:reduce){*{transition:none!important;}}
</style>

<div id="app">
  <header>
    <h1 id="title">dep-graph collection</h1>
    <span class="sub">fill = <code>discovered_by</code> · border = <code>state</code> · glyph = type · bands = execution layer. Click a node for its fields + edges.</span>
  </header>
  <div id="picker"></div>
  <div id="canvaswrap"><div id="bands"><canvas id="edges"></canvas></div></div>
  <aside>
    <h2>Graph</h2><div id="stats"></div>
    <div class="sep"></div>
    <h2>Selection</h2><div id="detail"><span class="muted">Click a node…</span></div>
    <div class="sep"></div>
    <h2>Legend</h2><div id="legend"></div>
  </aside>
</div>

<script>
const DATA = __DATA__;

const DISC = {goal:"#10b981",static_scan:"#a78bfa",resolver:"#3b82f6",probe:"#f97316",runtime:"#ec4899",audit:"#f59e0b",classifier:"#22d3ee"};
const STATE = {satisfied:"#34d399",missing:"#f87171",unknown:"#64748b"};
const GLYPH = {Test:"★",Project:"▣",Import:"●",Package:"▮",SystemLib:"▲",Tool:"◆",Runtime:"⬡",Platform:"◐",Service:"⚙",Config:"⌸",Module:"◈"};
const LAYER_ORDER = ["runtime","interpreter","system","toolchain","pip","naming","config","tests","services"];
const REL_COLOR = {requires:"#7f9bc4",conflicts_with:"#f59e0b",alternative_to:"#2dd4bf"};

const $ = (s)=>document.querySelector(s);
const esc = (s)=>(s==null?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;");
let cur=0, sel=null, showAll=false;

function layerRank(l){const i=LAYER_ORDER.indexOf(l);return i<0?LAYER_ORDER.length:i;}

function buildPicker(){
  const p=$("#picker");p.innerHTML="";
  DATA.graphs.forEach((g,i)=>{
    const b=document.createElement("button");
    b.className="stagebtn"+(i===cur?" active":"");
    b.innerHTML=esc(g.name)+' <span class="ct">'+g.n_nodes+'</span>';
    b.onclick=()=>{cur=i;sel=null;render();};
    p.appendChild(b);
  });
  const sp=document.createElement("div");sp.className="spacer";p.appendChild(sp);
  const t=document.createElement("label");t.className="toggle";
  t.innerHTML='<input type="checkbox" id="alledges"> show all edges';p.appendChild(t);
  const s=document.createElement("input");s.id="search";s.placeholder="filter nodes…";p.appendChild(s);
  s.oninput=()=>applyFilter(s.value.trim().toLowerCase());
  t.querySelector("input").onchange=(e)=>{showAll=e.target.checked;drawEdges();};
}

function graph(){return DATA.graphs[cur];}
function nodeById(id){return graph().nodes.find(n=>n.id===id);}

function render(){
  $("#title").textContent=DATA.title;
  document.querySelectorAll(".stagebtn").forEach((b,i)=>b.classList.toggle("active",i===cur));
  renderBands();renderStats();renderLegend();
  $("#detail").innerHTML='<span class="muted">Click a node…</span>';
  $("#bands").classList.remove("dimming");
  requestAnimationFrame(drawEdges);
}

function renderBands(){
  const g=graph();
  const byLayer={};
  g.nodes.forEach(n=>{(byLayer[n.layer]=byLayer[n.layer]||[]).push(n);});
  const layers=Object.keys(byLayer).sort((a,b)=>layerRank(a)-layerRank(b));
  const bands=$("#bands");
  [...bands.querySelectorAll(".band")].forEach(e=>e.remove());
  layers.forEach(layer=>{
    const band=document.createElement("div");band.className="band";
    const lab=document.createElement("div");lab.className="band-label";
    lab.innerHTML=esc(layer)+' <span class="n">'+byLayer[layer].length+'</span>';
    const row=document.createElement("div");row.className="band-nodes";
    byLayer[layer].forEach(n=>row.appendChild(nodeEl(n)));
    band.appendChild(lab);band.appendChild(row);bands.appendChild(band);
  });
}

function nodeEl(n){
  const el=document.createElement("div");
  el.className="node";el.dataset.id=n.id;
  el.style.borderColor=STATE[n.state]||"#64748b";
  el.style.borderLeftColor=DISC[n.discovered_by]||"#64748b";
  const ur=(n.unresolved===true)?'<span class="ur" title="unresolved"></span>':"";
  el.innerHTML='<span class="glyph" style="color:'+(DISC[n.discovered_by]||"#8394ad")+'">'+(GLYPH[n.type]||"●")+'</span>'+ur+'<span class="nlabel">'+esc(n.label)+'</span>';
  el.title=n.type+" · "+n.layer+" · "+n.state+"  ("+n.discovered_by+")";
  el.onclick=(e)=>{e.stopPropagation();select(n.id);};
  return el;
}

function neighbors(id){
  const outs=graph().edges.filter(e=>e.source===id);
  const ins=graph().edges.filter(e=>e.target===id);
  return {outs,ins};
}

function select(id){
  sel=id;
  const {outs,ins}=neighbors(id);
  const nbr=new Set([...outs.map(e=>e.target),...ins.map(e=>e.source)]);
  document.querySelectorAll(".node").forEach(el=>{
    el.classList.toggle("sel",el.dataset.id===id);
    el.classList.toggle("nbr",nbr.has(el.dataset.id));
  });
  $("#bands").classList.add("dimming");
  renderDetail(nodeById(id),outs,ins);
  drawEdges();
}

function renderDetail(n,outs,ins){
  if(!n){$("#detail").innerHTML='<span class="muted">Click a node…</span>';return;}
  let h='<div class="title">'+esc(n.label)+'</div>';
  h+='<div><span class="k">id:</span> <code>'+esc(n.id)+'</code></div>';
  h+='<div><span class="k">type:</span> '+esc(n.type)+' &nbsp; <span class="k">layer:</span> '+esc(n.layer)+'</div>';
  h+='<div><span class="k">discovered_by:</span> '+esc(n.discovered_by)+' &nbsp;<span class="k">cycle:</span> '+esc(n.cycle)+'</div>';
  h+='<div><span class="k">state:</span> <span class="pill s-'+esc(n.state)+'">'+esc(n.state)+'</span></div>';
  if(n.version)h+='<div><span class="k">version:</span> '+esc(n.version)+'</div>';
  if(n.unresolved===true)h+='<div><span class="pill s-missing">unresolved</span></div>';
  if(n.bfs)h+='<div><span class="k">build_from_source:</span> '+esc(n.bfs)+'</div>';
  if(n.provenance)h+='<div style="margin-top:5px"><span class="k">provenance:</span> '+esc(n.provenance)+'</div>';
  if(n.fix)h+='<div style="margin-top:5px"><span class="k">fix:</span> <code>'+esc(n.fix)+'</code></div>';
  if(n.check)h+='<div style="margin-top:5px"><span class="k">check:</span><br><code>'+esc(n.check)+'</code></div>';
  if(n.evidence)h+='<div style="margin-top:5px"><span class="k">evidence:</span><br><code>'+esc(n.evidence)+'</code></div>';
  h+=navBlock("requires ("+outs.length+")",outs.map(e=>e.target));
  h+=navBlock("required by ("+ins.length+")",ins.map(e=>e.source));
  $("#detail").innerHTML=h;
  document.querySelectorAll("#detail .navlist button").forEach(b=>b.onclick=()=>select(b.dataset.id));
}

function navBlock(title,ids){
  if(!ids.length)return"";
  let h='<div style="margin-top:8px"><span class="k">'+title+':</span><div class="navlist">';
  ids.slice(0,40).forEach(id=>{const t=nodeById(id);h+='<button data-id="'+esc(id)+'">'+esc(t?t.label:id)+'</button>';});
  if(ids.length>40)h+='<span class="muted">+'+(ids.length-40)+' more</span>';
  return h+'</div></div>';
}

function applyFilter(q){
  document.querySelectorAll(".node").forEach(el=>{
    const n=nodeById(el.dataset.id);
    const hit=!q||n.id.toLowerCase().includes(q)||n.label.toLowerCase().includes(q);
    el.classList.toggle("hide",!hit);
  });
  drawEdges();
}

function centerOf(el,bands){
  const r=el.getBoundingClientRect(),br=bands.getBoundingClientRect();
  return {x:r.left-br.left+bands.scrollLeft+r.width/2,y:r.top-br.top+bands.scrollTop+r.height/2};
}

function drawEdges(){
  const bands=$("#bands"),cv=$("#edges");
  cv.width=bands.scrollWidth;cv.height=bands.scrollHeight;
  const ctx=cv.getContext("2d");ctx.clearRect(0,0,cv.width,cv.height);
  const els={};document.querySelectorAll(".node").forEach(el=>{els[el.dataset.id]=el;});
  let edges=graph().edges;
  if(!showAll){if(!sel)return;edges=edges.filter(e=>e.source===sel||e.target===sel);}
  ctx.lineWidth=showAll?0.6:1.3;
  edges.forEach(e=>{
    const a=els[e.source],b=els[e.target];if(!a||!b)return;
    if(a.classList.contains("hide")||b.classList.contains("hide"))return;
    const p=centerOf(a,bands),q=centerOf(b,bands);
    ctx.strokeStyle=REL_COLOR[e.relation]||"#7f9bc4";ctx.globalAlpha=showAll?0.28:0.85;
    ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.bezierCurveTo(p.x,(p.y+q.y)/2,q.x,(p.y+q.y)/2,q.x,q.y);ctx.stroke();
    ctx.globalAlpha=1;const ang=Math.atan2(q.y-p.y,q.x-p.x);
    ctx.beginPath();ctx.moveTo(q.x,q.y);ctx.lineTo(q.x-8*Math.cos(ang-0.4),q.y-8*Math.sin(ang-0.4));ctx.lineTo(q.x-8*Math.cos(ang+0.4),q.y-8*Math.sin(ang+0.4));ctx.closePath();ctx.fillStyle=REL_COLOR[e.relation]||"#7f9bc4";ctx.fill();
  });
}

function renderStats(){
  const g=graph();
  let h='<div class="statrow"><span class="chip">'+g.n_nodes+' nodes</span><span class="chip">'+g.n_edges+' edges</span></div>';
  h+='<div class="statrow" style="margin-top:6px">';
  ["satisfied","missing","unknown"].forEach(s=>{if(g.by_state[s])h+='<span class="pill s-'+s+'">'+g.by_state[s]+' '+s+'</span>';});
  h+='</div><div class="statrow" style="margin-top:6px">';
  Object.entries(g.by_type).sort((a,b)=>b[1]-a[1]).forEach(([t,c])=>h+='<span class="chip">'+(GLYPH[t]||"")+' '+c+' '+esc(t)+'</span>');
  $("#stats").innerHTML=h+'</div>';
}

function renderLegend(){
  let h='<div class="muted" style="font-size:10px;margin-bottom:3px">fill = discovered_by</div>';
  Object.entries(DISC).forEach(([k,c])=>h+='<div class="legrow"><span class="sw" style="background:'+c+'"></span>'+k+'</div>');
  h+='<div class="muted" style="font-size:10px;margin:7px 0 3px">border = state</div>';
  Object.entries(STATE).forEach(([k,c])=>h+='<div class="legrow"><span class="sw" style="background:transparent;border:2px solid '+c+'"></span>'+k+'</div>');
  h+='<div class="muted" style="font-size:10px;margin:7px 0 3px">glyph = type</div>';
  Object.entries(GLYPH).forEach(([k,c])=>h+='<div class="legrow"><span class="glyphsw">'+c+'</span>'+k+'</div>');
  $("#legend").innerHTML=h;
}

$("#canvaswrap").addEventListener("click",()=>{sel=null;document.querySelectorAll(".node").forEach(el=>el.classList.remove("sel","nbr"));$("#bands").classList.remove("dimming");$("#detail").innerHTML='<span class="muted">Click a node…</span>';drawEdges();});
$("#canvaswrap").addEventListener("scroll",()=>requestAnimationFrame(drawEdges));
window.addEventListener("resize",()=>requestAnimationFrame(drawEdges));
buildPicker();render();
</script>
"""


def render_collection_html(payload: dict, standalone: bool = True) -> str:
    """Render ``payload`` (from :func:`collection_payload`) to an HTML string.

    ``standalone=True`` wraps a full ``<!doctype html>`` document (open from disk);
    ``standalone=False`` returns body-only content for publishing as an Artifact
    (which supplies its own document skeleton).  The graph data is embedded inline
    — no external resource of any kind."""
    # ensure_ascii keeps the embed byte-safe; the </ -> <\/ guard prevents a
    # </script> inside any evidence/check string from breaking out of the block.
    # The data lands on a line of the exact form ``const DATA = ...;`` so it is
    # deterministically locatable.
    data_json = json.dumps(payload, ensure_ascii=True).replace("</", "<\\/")
    body = _BODY.replace("__DATA__", data_json)
    if not standalone:
        return body
    title = payload.get("title", "dep-graph collection")
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )
