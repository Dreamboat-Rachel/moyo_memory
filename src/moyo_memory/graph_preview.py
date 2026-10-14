from html import escape
from json import dumps
from urllib.parse import quote


def render_graph_preview(user_id: str) -> str:
    safe_user_id = escape(user_id)
    url_user_id = quote(user_id, safe="")
    javascript_user_id = dumps(user_id, ensure_ascii=True).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_user_id} · 知识图谱</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; --line:#dde2de; --panel:#fff; --ink:#17201d; --muted:#66736c; }}
    body {{ margin: 0; height:100vh; overflow:hidden; display:grid; grid-template-rows:auto minmax(0,1fr); background: Canvas; color: CanvasText; }}
    header {{ padding: 14px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0; font-size:20px; font-weight:600; }}
    .meta {{ color: GrayText; font-size:13px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
    .sync-dot {{ width:7px; height:7px; border-radius:50%; background:#16a34a; display:inline-block; }}
    .sync-dot.loading {{ background:#f59e0b; }} .sync-dot.error {{ background:#dc2626; }}
    .legend {{ margin-left:auto; display:flex; gap:12px; flex-wrap:wrap; font-size:12px; }}
    .legend span::before {{ content:""; display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:5px; background:var(--c); }}
    #graph {{ width:100%; height:100%; min-height:360px; display:block; }}
    .edge {{ stroke: color-mix(in srgb, CanvasText 25%, transparent); stroke-width:1.4; }}
    .relation {{ stroke:#8b5cf6; stroke-width:2.2; }}
    .edge-label {{ font-size:11px; fill:GrayText; text-anchor:middle; paint-order:stroke; stroke:Canvas; stroke-width:4px; }}
    .node circle {{ stroke:Canvas; stroke-width:3; filter:drop-shadow(0 2px 4px rgb(0 0 0 / .18)); }}
    .node text {{ font-size:12px; text-anchor:middle; fill:CanvasText; paint-order:stroke; stroke:Canvas; stroke-width:4px; pointer-events:none; }}
    .empty {{ padding:60px 24px; text-align:center; color:GrayText; }}
    .tooltip {{ position:fixed; display:none; max-width:320px; padding:10px 12px; border-radius:8px; background:Canvas; color:CanvasText; border:1px solid color-mix(in srgb, CanvasText 20%, transparent); box-shadow:0 8px 30px rgb(0 0 0 / .2); font-size:12px; pointer-events:none; white-space:pre-wrap; }}
    .tools {{ display:flex; align-items:center; gap:8px; }}
    .user-switcher {{ display:grid; grid-template-columns:minmax(120px,180px) 36px; gap:7px; }}
    .user-field {{ height:36px; display:flex; align-items:center; gap:7px; padding:0 9px; border:1px solid var(--line); border-radius:7px; background:var(--panel); color:var(--muted); }}
    .user-field:focus-within {{ border-color:#699a80; box-shadow:0 0 0 3px #e7f3eb; }}
    .user-field svg {{ width:16px; height:16px; flex:0 0 auto; }}
    .user-field select {{ width:100%; min-width:0; border:0; outline:0; background:transparent; color:var(--ink); font:inherit; font-size:12px; }}
    .icon-btn {{ width:36px; height:36px; border:1px solid var(--line); border-radius:7px; background:var(--panel); color:var(--ink); display:grid; place-items:center; text-decoration:none; cursor:pointer; }}
    .icon-btn:hover {{ border-color:#aab4ae; background:#f7f9f7; }}
    .icon-btn svg {{ width:18px; height:18px; }}
    @media (max-width:760px) {{ header {{ align-items:flex-start; }} .legend {{ order:3; width:100%; margin-left:0; }} .tools {{ margin-left:auto; }} .user-switcher {{ grid-template-columns:minmax(90px,150px) 36px; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>用户知识图谱</h1><div class="meta">用户：{safe_user_id} · <span id="sync-dot" class="sync-dot loading"></span><span id="sync-status">正在同步</span></div></div>
    <div class="legend"><span style="--c:#2563eb">用户</span><span style="--c:#16a34a">资料</span><span style="--c:#f59e0b">偏好</span><span style="--c:#dc2626">事件</span><span style="--c:#8b5cf6">显式关系</span></div>
    <div class="tools">
      <a class="icon-btn" href="/chat/{url_user_id}" title="返回对话" aria-label="返回对话"><i data-lucide="message-circle"></i></a>
      <form id="user-switcher" class="user-switcher">
        <label class="user-field"><i data-lucide="user-round"></i><select id="user-id-select" aria-label="选择用户"><option value="{safe_user_id}" selected>{safe_user_id}</option></select></label>
        <button class="icon-btn" type="submit" title="切换用户" aria-label="切换用户"><i data-lucide="arrow-right"></i></button>
      </form>
    </div>
  </header>
  <svg id="graph" role="img" aria-label="用户知识图谱"></svg>
  <div id="tooltip" class="tooltip"></div>
  <script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
  <script>
    const userId = {javascript_user_id};
    const svg = d3.select('#graph');
    const tooltip = document.getElementById('tooltip');
    const syncDot = document.getElementById('sync-dot');
    const syncStatus = document.getElementById('sync-status');
    const colors = {{user:'#2563eb', profile:'#16a34a', preference:'#f59e0b', event:'#dc2626'}};
    let fingerprint = '', simulation = null, resizeObserver = null, loading = false;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    async function loadUsers() {{
      const response=await fetch('/v1/users?limit=500',{{cache:'no-store'}}); if(!response.ok)throw new Error('用户列表加载失败');
      const data=await response.json(), select=document.getElementById('user-id-select');
      const users=[...(data.items||[]),{{user_id:userId,memory_count:0}}].filter((item,index,all)=>all.findIndex(candidate=>candidate.user_id===item.user_id)===index);
      select.replaceChildren(...users.map(item=>{{const option=document.createElement('option');option.value=item.user_id;option.textContent=`${{item.user_id}} · ${{item.memory_count}} 条记忆`;return option;}})); select.value=userId;
    }}
    function switchUser(event) {{ event.preventDefault(); const next=document.getElementById('user-id-select').value; if(!next||next===userId)return; window.location.assign('/graph/'+encodeURIComponent(next)); }}
    async function refreshGraph() {{
      if (loading || document.hidden) return;
      loading = true;
      try {{
        const response = await fetch('/v1/users/' + encodeURIComponent(userId) + '/knowledge-graph', {{cache:'no-store'}});
        if (!response.ok) throw new Error('加载失败');
        const data = await response.json();
        const nextFingerprint = JSON.stringify(data);
        syncDot.className = 'sync-dot';
        syncStatus.textContent = `${{Math.max(data.nodes.length - 1, 0)}} 条记忆 · 已同步`;
        if (nextFingerprint !== fingerprint) {{ fingerprint = nextFingerprint; draw(data); }}
      }} catch (error) {{
        syncDot.className = 'sync-dot error'; syncStatus.textContent = '同步失败';
        if (!fingerprint) {{ svg.selectAll('*').remove(); svg.append('text').attr('x','50%').attr('y','45%').attr('text-anchor','middle').text(error.message); }}
      }} finally {{ loading = false; }}
    }}
    function draw(data) {{
      if (simulation) simulation.stop();
      if (resizeObserver) resizeObserver.disconnect();
      svg.selectAll('*').remove();
      if (data.nodes.length <= 1) {{
        svg.append('text').attr('x','50%').attr('y','45%').attr('text-anchor','middle').attr('fill','GrayText').text('暂无长期记忆');
        return;
      }}
      const width = svg.node().clientWidth, height = svg.node().clientHeight;
      const links = data.edges.map(d => ({{...d}}));
      const nodes = data.nodes.map(d => ({{...d}}));
      const zoomLayer = svg.append('g');
      svg.call(d3.zoom().scaleExtent([.35, 3]).on('zoom', e => zoomLayer.attr('transform', e.transform)));
      const link = zoomLayer.append('g').selectAll('line').data(links).join('line').attr('class', d => 'edge ' + (d.edge_type === 'relation' ? 'relation' : ''));
      const relationLabels = {{works_at:'任职于', located_in:'位于', joined:'加入', happened_at:'发生于', caused_by:'源于', related_to:'相关'}};
      const linkLabel = zoomLayer.append('g').selectAll('text').data(links.filter(d => d.edge_type === 'relation')).join('text').attr('class','edge-label').text(d => relationLabels[d.label] || d.label);
      const node = zoomLayer.append('g').selectAll('g').data(nodes).join('g').attr('class','node')
        .on('pointerenter', (e,d) => {{ tooltip.style.display='block'; tooltip.textContent = d.key ? `${{d.label}}\n类型：${{d.node_type}}\n字段：${{d.key}}\n置信度：${{Math.round((d.confidence||0)*100)}}%` : d.label; }})
        .on('pointermove', e => {{ tooltip.style.left=(e.clientX+14)+'px'; tooltip.style.top=(e.clientY+14)+'px'; }})
        .on('pointerleave', () => tooltip.style.display='none');
      node.append('circle').attr('r', d => d.node_type === 'user' ? 28 : 19).attr('fill', d => colors[d.node_type] || '#64748b');
      node.append('text').attr('dy', d => d.node_type === 'user' ? 45 : 34).text(d => d.label.length > 16 ? d.label.slice(0,15)+'…' : d.label);
      node.call(d3.drag().on('start',(e,d)=>{{if(!e.active)simulation.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;}}).on('drag',(e,d)=>{{d.fx=e.x;d.fy=e.y;}}).on('end',(e,d)=>{{if(!e.active)simulation.alphaTarget(0);d.fx=null;d.fy=null;}}));
      link.on('pointerenter',(e,d)=>{{tooltip.style.display='block';tooltip.textContent=`${{d.label}}\n${{d.evidence||''}}`;}}).on('pointermove',e=>{{tooltip.style.left=(e.clientX+14)+'px';tooltip.style.top=(e.clientY+14)+'px';}}).on('pointerleave',()=>tooltip.style.display='none');
      simulation = d3.forceSimulation(nodes).force('link',d3.forceLink(links).id(d=>d.id).distance(d=>d.edge_type==='relation'?145:180).strength(.65)).force('charge',d3.forceManyBody().strength(-700)).force('center',d3.forceCenter(width/2,height/2)).force('collision',d3.forceCollide().radius(58)).on('tick',()=>{{
        link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
        linkLabel.attr('x',d=>(d.source.x+d.target.x)/2).attr('y',d=>(d.source.y+d.target.y)/2-5);
        node.attr('transform',d=>`translate(${{d.x}},${{d.y}})`);
      }});
      resizeObserver = new ResizeObserver(()=>{{const w=svg.node().clientWidth,h=svg.node().clientHeight;simulation.force('center',d3.forceCenter(w/2,h/2)).alpha(.2).restart();}}); resizeObserver.observe(svg.node());
    }}
    document.getElementById('user-switcher').addEventListener('submit',switchUser);
    loadUsers().catch(()=>{{document.getElementById('user-id-select').title='用户列表加载失败';}}); window.lucide && lucide.createIcons(); refreshGraph();
    setInterval(refreshGraph, 3000);
    document.addEventListener('visibilitychange', () => {{ if (!document.hidden) refreshGraph(); }});
  </script>
</body>
</html>"""
