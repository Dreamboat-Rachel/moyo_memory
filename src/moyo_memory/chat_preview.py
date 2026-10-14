from html import escape
from json import dumps
from urllib.parse import quote


def render_chat_preview(user_id: str) -> str:
    safe_user_id = escape(user_id)
    url_user_id = quote(user_id, safe="")
    javascript_user_id = dumps(user_id, ensure_ascii=True).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_user_id} · Moyo 对话</title>
  <style>
    :root {{
      font-family: Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
      color:#17201d; background:#f7f8f6; font-synthesis:none;
      --line:#dde2de; --muted:#66736c; --panel:#ffffff; --soft:#f0f3f0;
      --ink:#17201d; --brand:#18794e; --brand-soft:#e8f5ee; --danger:#b42318;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; height:100dvh; overflow:hidden; background:#f7f8f6; color:var(--ink); }}
    button, input, select, textarea {{ font:inherit; }}
    button {{ cursor:pointer; }}
    .app {{ height:100%; display:grid; grid-template-columns:280px minmax(0,1fr); }}
    .sidebar {{ min-width:0; border-right:1px solid var(--line); background:#eef1ee; display:grid; grid-template-rows:auto auto minmax(0,1fr) auto; }}
    .brand {{ height:64px; padding:0 16px; display:flex; align-items:center; gap:10px; border-bottom:1px solid var(--line); }}
    .brand-mark {{ width:30px; height:30px; display:grid; place-items:center; background:var(--ink); color:white; border-radius:7px; }}
    .brand-name {{ font-size:15px; font-weight:700; }}
    .brand-user {{ display:block; max-width:170px; color:var(--muted); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .new-chat {{ margin-left:auto; }}
    .user-switcher {{ padding:10px; border-bottom:1px solid var(--line); display:grid; grid-template-columns:minmax(0,1fr) 36px; gap:7px; }}
    .user-field {{ min-width:0; height:36px; display:flex; align-items:center; gap:8px; padding:0 10px; border:1px solid #cfd7d1; border-radius:7px; background:var(--panel); color:var(--muted); }}
    .user-field:focus-within {{ border-color:#699a80; box-shadow:0 0 0 3px #dcebe1; }}
    .user-field svg {{ width:16px; height:16px; flex:0 0 auto; }}
    .user-field select {{ width:100%; min-width:0; border:0; outline:0; background:transparent; color:var(--ink); font-size:12px; }}
    .icon-btn {{ width:36px; height:36px; border:1px solid var(--line); border-radius:7px; background:var(--panel); color:var(--ink); display:grid; place-items:center; flex:0 0 auto; }}
    .icon-btn:hover {{ border-color:#aab4ae; background:#fafbfa; }}
    .icon-btn.danger:hover {{ color:var(--danger); border-color:#e5aaa5; }}
    .icon-btn svg {{ width:18px; height:18px; }}
    .conversation-list {{ overflow:auto; padding:10px; }}
    .conversation {{ width:100%; min-height:54px; padding:9px 8px 9px 11px; display:grid; grid-template-columns:minmax(0,1fr) 30px; align-items:center; gap:6px; border:1px solid transparent; border-radius:7px; background:transparent; color:var(--ink); text-align:left; }}
    .conversation:hover {{ background:#e4e9e5; }}
    .conversation.active {{ background:var(--panel); border-color:#cfd7d1; box-shadow:0 1px 2px rgb(20 35 27 / .05); }}
    .conversation-title {{ overflow:hidden; white-space:nowrap; text-overflow:ellipsis; font-size:13px; font-weight:600; }}
    .conversation-time {{ margin-top:4px; color:var(--muted); font-size:11px; }}
    .conversation-delete {{ width:28px; height:28px; opacity:0; border:0; border-radius:6px; display:grid; place-items:center; background:transparent; color:var(--muted); }}
    .conversation:hover .conversation-delete, .conversation.active .conversation-delete {{ opacity:1; }}
    .conversation-delete:hover {{ color:var(--danger); background:#fff1f0; }}
    .conversation-delete svg {{ width:15px; height:15px; }}
    .sidebar-footer {{ padding:12px 16px; border-top:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; color:var(--muted); font-size:12px; }}
    .engine {{ display:flex; align-items:center; gap:7px; }}
    .engine-dot {{ width:7px; height:7px; border-radius:50%; background:#19a463; box-shadow:0 0 0 3px #d7f3e4; }}
    .engine-dot.syncing {{ background:#d49b22; box-shadow:0 0 0 3px #f8edcc; }}
    .engine-dot.error {{ background:#c73b32; box-shadow:0 0 0 3px #f8d8d5; }}
    .engine-dot.disabled {{ background:#89928d; box-shadow:0 0 0 3px #e3e7e4; }}
    .main {{ min-width:0; min-height:0; display:grid; grid-template-rows:64px minmax(0,1fr) auto; background:var(--panel); }}
    .topbar {{ padding:0 20px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:10px; }}
    .mobile-menu {{ display:none; }}
    .chat-title {{ min-width:0; flex:1; }}
    .chat-title strong {{ display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }}
    .chat-title span {{ color:var(--muted); font-size:11px; }}
    .tool-link {{ text-decoration:none; }}
    .messages {{ min-height:0; overflow:auto; padding:28px max(20px, calc((100% - 840px)/2)); scroll-behavior:smooth; }}
    .empty {{ height:100%; display:grid; place-items:center; text-align:center; color:var(--muted); }}
    .empty strong {{ display:block; margin-bottom:8px; color:var(--ink); font-size:20px; }}
    .empty button {{ margin-top:14px; }}
    .message {{ display:grid; grid-template-columns:30px minmax(0,1fr); gap:11px; margin:0 0 22px; }}
    .message.user {{ grid-template-columns:minmax(0,1fr); justify-items:end; }}
    .avatar {{ width:30px; height:30px; border-radius:7px; display:grid; place-items:center; background:#e8ece9; color:#3e4a44; }}
    .avatar svg {{ width:16px; height:16px; }}
    .message-content {{ max-width:100%; min-width:0; }}
    .message-text {{ font-size:14px; line-height:1.72; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .assistant .message-text {{ padding-top:3px; }}
    .user .message-text {{ max-width:min(680px, 88%); padding:10px 13px; border-radius:8px 8px 2px 8px; background:#26342d; color:#fff; }}
    .message-meta {{ margin-top:7px; color:var(--muted); font-size:10px; display:flex; gap:8px; align-items:center; }}
    .thinking {{ color:var(--muted); display:flex; align-items:center; gap:8px; padding-top:5px; }}
    .thinking-dots {{ display:flex; gap:4px; }}
    .thinking-dots i {{ width:5px; height:5px; border-radius:50%; background:#7d8b83; animation:pulse 1.1s infinite ease-in-out; }}
    .thinking-dots i:nth-child(2) {{ animation-delay:.15s; }} .thinking-dots i:nth-child(3) {{ animation-delay:.3s; }}
    @keyframes pulse {{ 0%,80%,100%{{opacity:.25;transform:translateY(0)}} 40%{{opacity:1;transform:translateY(-2px)}} }}
    .composer-wrap {{ padding:12px max(20px, calc((100% - 840px)/2)) 18px; background:var(--panel); }}
    .composer {{ min-height:54px; display:grid; grid-template-columns:minmax(0,1fr) 42px; align-items:end; gap:8px; padding:7px 7px 7px 14px; border:1px solid #cbd3cd; border-radius:8px; box-shadow:0 4px 18px rgb(24 45 33 / .07); }}
    .composer:focus-within {{ border-color:#699a80; box-shadow:0 0 0 3px #e7f3eb; }}
    textarea {{ width:100%; min-height:38px; max-height:160px; resize:none; padding:8px 0; border:0; outline:0; background:transparent; color:var(--ink); line-height:1.45; }}
    textarea::placeholder {{ color:#8a958f; }}
    .send {{ width:40px; height:40px; border:0; border-radius:7px; display:grid; place-items:center; background:var(--brand); color:#fff; }}
    .send:hover {{ background:#11683f; }} .send:disabled {{ background:#aeb8b2; cursor:not-allowed; }}
    .send svg {{ width:18px; height:18px; }}
    .drawer {{ position:fixed; z-index:20; top:0; right:0; bottom:0; width:min(380px, 92vw); transform:translateX(102%); transition:transform .2s ease; background:var(--panel); border-left:1px solid var(--line); box-shadow:-12px 0 40px rgb(22 35 28 / .12); display:grid; grid-template-rows:64px minmax(0,1fr); }}
    .drawer.open {{ transform:translateX(0); }}
    .drawer-head {{ padding:0 16px 0 20px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--line); }}
    .drawer-head strong {{ font-size:15px; }}
    .drawer-body {{ overflow:auto; padding:18px 20px; }}
    .memory-section {{ margin-bottom:24px; }}
    .memory-section h2 {{ margin:0 0 10px; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; }}
    .memory-toolbar {{ min-height:42px; display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid var(--line); margin-bottom:10px; }}
    .memory-toolbar span {{ font-size:12px; font-weight:600; }}
    .switch {{ position:relative; width:36px; height:20px; flex:0 0 auto; }}
    .switch input {{ position:absolute; opacity:0; pointer-events:none; }}
    .switch i {{ position:absolute; inset:0; border-radius:10px; background:#adb6b0; transition:.15s; }}
    .switch i::after {{ content:""; position:absolute; width:16px; height:16px; left:2px; top:2px; border-radius:50%; background:#fff; transition:.15s; box-shadow:0 1px 3px rgb(0 0 0 / .18); }}
    .switch input:checked+i {{ background:var(--brand); }}
    .switch input:checked+i::after {{ transform:translateX(16px); }}
    .memory-list {{ border-top:1px solid var(--line); }}
    .memory-item {{ min-width:0; padding:10px 0; border-bottom:1px solid var(--line); display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; }}
    .memory-value {{ min-width:0; font-size:12px; line-height:1.5; overflow-wrap:anywhere; }}
    .memory-value b {{ display:block; color:var(--muted); font-size:10px; font-weight:600; }}
    .memory-actions {{ display:flex; gap:4px; }}
    .memory-actions .icon-btn {{ width:30px; height:30px; border-color:transparent; background:transparent; }}
    .memory-actions .icon-btn:hover {{ border-color:var(--line); background:var(--soft); }}
    .memory-actions svg {{ width:15px; height:15px; }}
    .memory-state {{ display:inline-block; margin-top:4px; padding:2px 5px; border-radius:4px; color:#875900; background:#fff0c2; font-size:10px; }}
    .memory-stats {{ color:var(--muted); font-size:11px; line-height:1.6; }}
    .recall-box {{ padding:11px; border-left:3px solid #d49b22; background:#fff9e9; font-size:12px; line-height:1.65; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .muted {{ color:var(--muted); font-size:12px; }}
    .modal-backdrop {{ position:fixed; z-index:60; inset:0; display:none; place-items:center; padding:16px; background:rgb(16 24 19 / .38); }}
    .modal-backdrop.open {{ display:grid; }}
    .modal {{ width:min(440px,100%); border:1px solid var(--line); border-radius:8px; background:var(--panel); box-shadow:0 20px 60px rgb(0 0 0 / .2); }}
    .modal-head {{ height:56px; padding:0 12px 0 18px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--line); }}
    .modal-head strong {{ font-size:14px; }}
    .modal-body {{ padding:18px; display:grid; gap:14px; }}
    .field {{ display:grid; gap:6px; color:var(--muted); font-size:11px; }}
    .field input {{ width:100%; height:38px; padding:0 10px; border:1px solid #cfd7d1; border-radius:6px; outline:0; color:var(--ink); background:#fff; }}
    .field input:focus {{ border-color:#699a80; box-shadow:0 0 0 3px #dcebe1; }}
    .modal-actions {{ display:flex; justify-content:flex-end; gap:8px; }}
    .text-btn {{ min-height:36px; padding:0 13px; border:1px solid var(--line); border-radius:6px; background:#fff; color:var(--ink); }}
    .text-btn.primary {{ border-color:var(--brand); background:var(--brand); color:#fff; }}
    .toast {{ position:fixed; z-index:50; left:50%; bottom:92px; transform:translate(-50%, 14px); opacity:0; pointer-events:none; max-width:min(420px,90vw); padding:9px 12px; border-radius:7px; background:#252c28; color:#fff; font-size:12px; transition:.2s; }}
    .toast.show {{ opacity:1; transform:translate(-50%,0); }}
    .overlay {{ display:none; }}
    @media (max-width:760px) {{
      .app {{ grid-template-columns:minmax(0,1fr); }}
      .sidebar {{ position:fixed; z-index:30; inset:0 auto 0 0; width:min(300px,88vw); transform:translateX(-102%); transition:transform .2s; box-shadow:12px 0 40px rgb(0 0 0 / .14); }}
      .sidebar.open {{ transform:translateX(0); }}
      .mobile-menu {{ display:grid; }}
      .overlay {{ position:fixed; z-index:25; inset:0; background:rgb(13 20 16 / .3); }}
      .overlay.open {{ display:block; }}
      .messages {{ padding:22px 16px; }}
      .composer-wrap {{ padding:10px 12px 12px; }}
      .topbar {{ padding:0 12px; }}
      .topbar .tool-label {{ display:none; }}
      .user .message-text {{ max-width:94%; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside id="sidebar" class="sidebar">
      <div class="brand">
        <div class="brand-mark"><i data-lucide="brain-circuit"></i></div>
        <div><div class="brand-name">Moyo Memory</div><span class="brand-user">{safe_user_id}</span></div>
        <button id="new-chat" class="icon-btn new-chat" title="新建对话" aria-label="新建对话"><i data-lucide="square-pen"></i></button>
      </div>
      <form id="user-switcher" class="user-switcher">
        <label class="user-field"><i data-lucide="user-round"></i><select id="user-id-select" aria-label="选择用户"><option value="{safe_user_id}" selected>{safe_user_id}</option></select></label>
        <button class="icon-btn" type="submit" title="切换用户" aria-label="切换用户"><i data-lucide="arrow-right"></i></button>
      </form>
      <div id="conversation-list" class="conversation-list"></div>
      <div class="sidebar-footer"><span id="engine-status" class="engine"><i class="engine-dot syncing"></i><span>检查记忆状态</span></span><span id="conversation-count">0 个会话</span></div>
    </aside>
    <main class="main">
      <header class="topbar">
        <button id="mobile-menu" class="icon-btn mobile-menu" title="会话列表" aria-label="会话列表"><i data-lucide="panel-left"></i></button>
        <div class="chat-title"><strong id="current-title">对话</strong><span id="current-meta">选择或新建一个会话</span></div>
        <a class="icon-btn tool-link" href="/graph/{url_user_id}" title="知识图谱" aria-label="知识图谱"><i data-lucide="share-2"></i></a>
        <button id="memory-toggle" class="icon-btn" title="用户记忆" aria-label="用户记忆"><i data-lucide="database"></i></button>
      </header>
      <section id="messages" class="messages" aria-live="polite"></section>
      <div class="composer-wrap">
        <form id="composer" class="composer">
          <textarea id="message-input" rows="1" maxlength="20000" placeholder="输入消息…" aria-label="消息"></textarea>
          <button id="send" class="send" type="submit" title="发送" aria-label="发送"><i data-lucide="arrow-up"></i></button>
        </form>
      </div>
    </main>
  </div>
  <aside id="memory-drawer" class="drawer">
    <div class="drawer-head"><strong>用户记忆</strong><button id="memory-close" class="icon-btn" title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></div>
    <div id="memory-body" class="drawer-body"></div>
  </aside>
  <div id="memory-modal" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="memory-modal-title">
    <form id="memory-edit-form" class="modal">
      <div class="modal-head"><strong id="memory-modal-title">纠正记忆</strong><button id="memory-modal-close" class="icon-btn" type="button" title="关闭" aria-label="关闭"><i data-lucide="x"></i></button></div>
      <div class="modal-body">
        <label class="field">记忆内容<input id="memory-edit-value" maxlength="2000" required></label>
        <label class="field">开始时间（可选）<input id="memory-edit-from" type="datetime-local"></label>
        <label class="field">结束时间（可选）<input id="memory-edit-to" type="datetime-local"></label>
        <div class="modal-actions"><button id="memory-edit-cancel" class="text-btn" type="button">取消</button><button class="text-btn primary" type="submit">保存纠正</button></div>
      </div>
    </form>
  </div>
  <div id="overlay" class="overlay"></div>
  <div id="toast" class="toast"></div>
  <script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js"></script>
  <script>
    const userId = {javascript_user_id};
    const apiBase = '/v1/users/' + encodeURIComponent(userId);
    const state = {{ conversations:[], activeId:null, sending:false, lastRecall:'', memoryItems:[], editingFact:null }};
    const els = {{
      sidebar:document.getElementById('sidebar'), list:document.getElementById('conversation-list'),
      messages:document.getElementById('messages'), title:document.getElementById('current-title'),
      meta:document.getElementById('current-meta'), input:document.getElementById('message-input'),
      send:document.getElementById('send'), drawer:document.getElementById('memory-drawer'),
      memoryBody:document.getElementById('memory-body'), overlay:document.getElementById('overlay'),
      toast:document.getElementById('toast'), count:document.getElementById('conversation-count'),
      engineStatus:document.getElementById('engine-status'), memoryModal:document.getElementById('memory-modal'),
      editValue:document.getElementById('memory-edit-value'), editFrom:document.getElementById('memory-edit-from'), editTo:document.getElementById('memory-edit-to')
    }};
    const icon = () => window.lucide && lucide.createIcons();
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const formatText = value => esc(value).replace(/[*][*]([^*]+)[*][*]/g,'<strong>$1</strong>');
    const time = value => value ? new Intl.DateTimeFormat('zh-CN', {{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}}).format(new Date(value)) : '';
    async function loadUsers() {{
      const data=await api('/v1/users?limit=500',{{cache:'no-store'}}); const select=document.getElementById('user-id-select');
      const users=[...(data.items||[]),{{user_id:userId,memory_count:0}}].filter((item,index,all)=>all.findIndex(candidate=>candidate.user_id===item.user_id)===index);
      select.replaceChildren(...users.map(item=>{{const option=document.createElement('option');option.value=item.user_id;option.textContent=`${{item.user_id}} · ${{item.memory_count}} 条记忆`;return option;}})); select.value=userId;
    }}
    function switchUser(event) {{ event.preventDefault(); const next=document.getElementById('user-id-select').value; if(!next||next===userId)return; window.location.assign('/chat/'+encodeURIComponent(next)); }}
    function notify(message) {{ els.toast.textContent=message; els.toast.classList.add('show'); clearTimeout(notify.timer); notify.timer=setTimeout(()=>els.toast.classList.remove('show'),2800); }}
    async function refreshVectorStatus() {{
      const dot=els.engineStatus.querySelector('.engine-dot'); const label=els.engineStatus.querySelector('span');
      try {{
        const status=await api(apiBase+'/memory-index-status',{{cache:'no-store'}}); const outbox=status.outbox||{{}};
        const queued=(outbox.pending||0)+(outbox.processing||0);
        if(!status.semantic_recall_enabled) {{ dot.className='engine-dot disabled'; label.textContent='语义记忆关闭'; }}
        else if(queued>0) {{ dot.className='engine-dot syncing'; label.textContent='同步中 '+queued; }}
        else if((outbox.failed||0)>0) {{ dot.className='engine-dot error'; label.textContent='索引异常'; }}
        else {{ dot.className='engine-dot'; label.textContent='记忆已同步'; }}
      }} catch (_) {{ dot.className='engine-dot error'; label.textContent='状态不可用'; }}
    }}
    async function api(path, options={{}}) {{
      const response = await fetch(path, {{...options,headers:{{'Content-Type':'application/json','X-User-ID':userId,...(options.headers||{{}})}}}});
      if (!response.ok) {{ let detail='请求失败'; try {{ detail=(await response.json()).detail||detail; }} catch (_) {{}} throw new Error(detail); }}
      return response.status === 204 ? null : response.json();
    }}
    async function loadConversations(selectFirst=true) {{
      state.conversations = await api(apiBase + '/conversations');
      els.count.textContent = state.conversations.length + ' 个会话'; renderConversations();
      if (selectFirst && !state.activeId && state.conversations.length) await selectConversation(state.conversations[0].id);
      if (!state.conversations.length) renderEmpty();
    }}
    function renderConversations() {{
      els.list.innerHTML = state.conversations.map(item => `<button class="conversation ${{item.id===state.activeId?'active':''}}" data-id="${{esc(item.id)}}"><span><span class="conversation-title">${{esc(item.title||'新对话')}}</span><span class="conversation-time">${{esc(time(item.updated_at))}}</span></span><span class="conversation-delete" data-delete="${{esc(item.id)}}" title="删除对话"><i data-lucide="trash-2"></i></span></button>`).join('');
      icon();
    }}
    function renderEmpty() {{
      els.title.textContent='对话'; els.meta.textContent=userId;
      els.messages.innerHTML='<div class="empty"><div><strong>开始一段新对话</strong><div>长期记忆会在对话中自然更新</div><button class="icon-btn" style="margin-inline:auto" data-empty-new title="新建对话"><i data-lucide="plus"></i></button></div></div>'; icon();
    }}
    async function createConversation() {{
      const conversation = await api(apiBase + '/conversations', {{method:'POST',body:JSON.stringify({{title:'新对话'}})}});
      state.activeId=conversation.id; await loadConversations(false); await selectConversation(conversation.id); closeSidebar(); els.input.focus();
    }}
    async function selectConversation(id) {{
      state.activeId=id; renderConversations(); closeSidebar();
      const data=await api(apiBase+'/conversations/'+encodeURIComponent(id)+'?limit=200');
      els.title.textContent=data.conversation.title||'新对话';
      els.meta.textContent=(data.conversation.last_sequence/2)+' 轮 · '+time(data.conversation.updated_at);
      renderMessages(data.messages);
    }}
    function renderMessages(messages) {{
      if (!messages.length) {{ els.messages.innerHTML='<div class="empty"><div><strong>新对话</strong><div>写下第一条消息</div></div></div>'; return; }}
      els.messages.innerHTML=messages.map(message=>messageHtml(message)).join(''); icon(); scrollBottom();
    }}
    function messageHtml(message, meta='') {{
      if(message.role==='user') return `<article class="message user"><div class="message-content"><div class="message-text">${{formatText(message.content)}}</div><div class="message-meta">${{esc(time(message.created_at))}}</div></div></article>`;
      return `<article class="message assistant"><div class="avatar"><i data-lucide="sparkles"></i></div><div class="message-content"><div class="message-text">${{formatText(message.content)}}</div><div class="message-meta">${{esc(meta||time(message.created_at))}}</div></div></article>`;
    }}
    function renderThinking() {{ return '<article id="thinking" class="message assistant"><div class="avatar"><i data-lucide="sparkles"></i></div><div class="thinking"><span>正在思考</span><span class="thinking-dots"><i></i><i></i><i></i></span></div></article>'; }}
    async function sendMessage(event) {{
      event.preventDefault(); const content=els.input.value.trim(); if(!content||state.sending)return;
      if(!state.activeId) await createConversation();
      state.sending=true; els.send.disabled=true; els.input.value=''; resizeInput();
      if(els.messages.querySelector('.empty')) els.messages.innerHTML='';
      const optimistic={{role:'user',content,created_at:new Date().toISOString()}};
      els.messages.insertAdjacentHTML('beforeend',messageHtml(optimistic)+renderThinking()); icon(); scrollBottom();
      try {{
        const result=await api(apiBase+'/conversations/'+encodeURIComponent(state.activeId)+'/messages',{{method:'POST',body:JSON.stringify({{message_id:crypto.randomUUID(),content}})}});
        document.getElementById('thinking')?.remove();
        els.messages.insertAdjacentHTML('beforeend',messageHtml(result.assistant_message,result.retrieval_mode));
        state.lastRecall=result.memory_context||''; icon(); scrollBottom(); await Promise.all([loadConversations(false),loadMemories(),refreshVectorStatus()]);
        if(result.warnings?.length) notify('已完成，部分记忆服务发生降级');
      }} catch(error) {{
        document.getElementById('thinking')?.remove(); notify(error.message); els.input.value=content;
      }} finally {{ state.sending=false; els.send.disabled=false; els.input.focus(); }}
    }}
    async function deleteConversation(id) {{
      if(!confirm('删除这个会话及其全部消息？')) return;
      await api(apiBase+'/conversations/'+encodeURIComponent(id),{{method:'DELETE'}});
      if(state.activeId===id) state.activeId=null; await loadConversations(true);
    }}
    async function loadMemories() {{
      try {{
        const [items,policy,stats]=await Promise.all([api(apiBase+'/memory-items'),api(apiBase+'/memory-policy'),api(apiBase+'/memory-stats')]);
        state.memoryItems=items;
        const rows=items.map(item=>{{
          const pending=item.review_state==='pending';
          const actions=pending
            ? `<button class="icon-btn" data-review="confirmed" data-fact="${{esc(item.id)}}" title="确认保存" aria-label="确认保存"><i data-lucide="check"></i></button><button class="icon-btn danger" data-review="rejected" data-fact="${{esc(item.id)}}" title="拒绝保存" aria-label="拒绝保存"><i data-lucide="x"></i></button>`
            : `<button class="icon-btn" data-memory-edit="${{esc(item.id)}}" title="纠正" aria-label="纠正"><i data-lucide="pencil"></i></button><button class="icon-btn danger" data-memory-delete="${{esc(item.id)}}" title="删除" aria-label="删除"><i data-lucide="trash-2"></i></button>`;
          return `<div class="memory-item"><div class="memory-value"><b>${{esc(item.category)}} · ${{esc(item.key)}}</b>${{esc(item.value)}}${{pending?'<span class="memory-state">等待确认</span>':''}}</div><div class="memory-actions">${{actions}}</div></div>`;
        }}).join('');
        els.memoryBody.innerHTML=`<section class="memory-section"><h2>记忆设置</h2><div class="memory-toolbar"><span>允许保存长期记忆</span><label class="switch"><input id="memory-enabled" type="checkbox" ${{policy.enabled?'checked':''}} aria-label="允许保存长期记忆"><i></i></label></div><div class="memory-stats">有效 ${{stats.active}} · 待确认 ${{stats.pending_confirmation}} · 历史 ${{stats.superseded+stats.retracted}}</div></section><section class="memory-section"><h2>长期记忆</h2>${{rows?`<div class="memory-list">${{rows}}</div>`:'<div class="muted">暂无长期记忆</div>'}}</section><section class="memory-section"><h2>本轮召回</h2>${{state.lastRecall?`<div class="recall-box">${{esc(state.lastRecall)}}</div>`:'<div class="muted">发送消息后显示</div>'}}</section>`;
        document.getElementById('memory-enabled').addEventListener('change',event=>updateMemoryPolicy(policy,event.target.checked)); icon();
      }} catch(error) {{ els.memoryBody.innerHTML='<div class="muted">记忆加载失败</div>'; }}
    }}
    async function updateMemoryPolicy(policy,enabled) {{
      try {{ await api(apiBase+'/memory-policy',{{method:'PUT',body:JSON.stringify({{...policy,enabled}})}}); notify(enabled?'长期记忆已开启':'长期记忆已关闭'); }}
      catch(error) {{ notify(error.message); await loadMemories(); }}
    }}
    async function reviewMemory(id,stateValue) {{
      try {{ await api(apiBase+'/memories/'+encodeURIComponent(id)+'/review',{{method:'POST',body:JSON.stringify({{state:stateValue}})}}); notify(stateValue==='confirmed'?'记忆已确认':'记忆已拒绝'); await Promise.all([loadMemories(),refreshVectorStatus()]); }}
      catch(error) {{ notify(error.message); }}
    }}
    function openMemoryEditor(id) {{
      const fact=state.memoryItems.find(item=>item.id===id); if(!fact)return; state.editingFact=fact;
      els.editValue.value=fact.value; els.editFrom.value=fact.valid_from?new Date(fact.valid_from).toISOString().slice(0,16):''; els.editTo.value=fact.valid_to?new Date(fact.valid_to).toISOString().slice(0,16):'';
      els.memoryModal.classList.add('open'); els.editValue.focus();
    }}
    function closeMemoryEditor() {{ state.editingFact=null; els.memoryModal.classList.remove('open'); }}
    async function saveMemoryCorrection(event) {{
      event.preventDefault(); if(!state.editingFact)return;
      const body={{value:els.editValue.value.trim(),valid_from:els.editFrom.value?new Date(els.editFrom.value).toISOString():null,valid_to:els.editTo.value?new Date(els.editTo.value).toISOString():null}};
      try {{ await api(apiBase+'/memories/'+encodeURIComponent(state.editingFact.id),{{method:'PATCH',body:JSON.stringify(body)}}); closeMemoryEditor(); notify('记忆已纠正，旧版本已保留'); await Promise.all([loadMemories(),refreshVectorStatus()]); }}
      catch(error) {{ notify(error.message); }}
    }}
    async function deleteMemory(id) {{
      if(!confirm('删除这条长期记忆？历史记录仍用于审计，但不会再被召回。'))return;
      try {{ await api(apiBase+'/memories/'+encodeURIComponent(id),{{method:'DELETE'}}); notify('记忆已删除'); await Promise.all([loadMemories(),refreshVectorStatus()]); }}
      catch(error) {{ notify(error.message); }}
    }}
    function scrollBottom() {{ requestAnimationFrame(()=>els.messages.scrollTop=els.messages.scrollHeight); }}
    function resizeInput() {{ els.input.style.height='auto'; els.input.style.height=Math.min(els.input.scrollHeight,160)+'px'; }}
    function closeSidebar() {{ els.sidebar.classList.remove('open'); els.overlay.classList.remove('open'); }}
    document.getElementById('new-chat').addEventListener('click',createConversation);
    document.getElementById('user-switcher').addEventListener('submit',switchUser);
    document.getElementById('composer').addEventListener('submit',sendMessage);
    document.getElementById('mobile-menu').addEventListener('click',()=>{{els.sidebar.classList.add('open');els.overlay.classList.add('open');}});
    els.overlay.addEventListener('click',closeSidebar);
    els.list.addEventListener('click',event=>{{const remove=event.target.closest('[data-delete]');if(remove){{event.stopPropagation();deleteConversation(remove.dataset.delete);return;}}const row=event.target.closest('[data-id]');if(row)selectConversation(row.dataset.id);}});
    els.messages.addEventListener('click',event=>{{if(event.target.closest('[data-empty-new]'))createConversation();}});
    document.getElementById('memory-toggle').addEventListener('click',()=>{{els.drawer.classList.add('open');loadMemories();}});
    document.getElementById('memory-close').addEventListener('click',()=>els.drawer.classList.remove('open'));
    els.memoryBody.addEventListener('click',event=>{{const review=event.target.closest('[data-review]');if(review){{reviewMemory(review.dataset.fact,review.dataset.review);return;}}const edit=event.target.closest('[data-memory-edit]');if(edit){{openMemoryEditor(edit.dataset.memoryEdit);return;}}const remove=event.target.closest('[data-memory-delete]');if(remove)deleteMemory(remove.dataset.memoryDelete);}});
    document.getElementById('memory-edit-form').addEventListener('submit',saveMemoryCorrection);
    document.getElementById('memory-modal-close').addEventListener('click',closeMemoryEditor);
    document.getElementById('memory-edit-cancel').addEventListener('click',closeMemoryEditor);
    els.memoryModal.addEventListener('click',event=>{{if(event.target===els.memoryModal)closeMemoryEditor();}});
    els.input.addEventListener('input',resizeInput);
    els.input.addEventListener('keydown',event=>{{if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();document.getElementById('composer').requestSubmit();}}}});
    icon(); loadUsers().catch(error=>notify(error.message)); loadConversations().catch(error=>notify(error.message)); loadMemories(); refreshVectorStatus();
    setInterval(refreshVectorStatus,5000);
  </script>
</body>
</html>"""
