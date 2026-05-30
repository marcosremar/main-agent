"""Tiny status dashboard — no deps (stdlib http.server), Tailwind via CDN.

Cross-references task definitions (config-*.json) with the orchestrator's state journals
(state/state-*.json) and serves an auto-refreshing page grouped by plan/objective, showing
each task's description, tool+model, status, PR, and the full prompt on demand.

Run:  python3 dashboard.py [port]      (default 8787)  ->  http://127.0.0.1:8787
"""
import glob
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def collect():
    tasks = {}
    branches = {}
    for cf in sorted(glob.glob(os.path.join(HERE, "config*.json"))):
        try:
            cfg = json.load(open(cf))
        except Exception:
            continue
        ib = cfg.get("integration_branch", "?")
        plan = cfg.get("objective") or os.path.basename(cf)
        planner = cfg.get("planner", "hand")
        # creation date: explicit per-task `created`, else the config file's mtime (when the
        # batch was written) — both ISO yyyy-mm-dd for display.
        cfg_date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(cf)))
        for t in cfg.get("tasks", []):
            commit = t.get("commit", "")
            desc = t.get("title") or re.sub(r'^\w+(\([^)]*\))?:\s*', '', commit) or t["id"]
            tasks[t["id"]] = {
                "id": t["id"], "desc": desc, "lane": t.get("worker_model", "?"),
                "verify": t.get("verify_cmd", ""), "files": t.get("allowed_files", []),
                "commit": commit, "spec": t.get("spec", ""),
                "config": os.path.basename(cf), "plan": plan, "planner": planner,
                "created": (t.get("created") or cfg_date)[:10],
            }
            branches[t["id"]] = ib
    state = {}
    for sf in glob.glob(os.path.join(HERE, "state", "state-*.json")):
        try:
            for tid, r in json.load(open(sf)).items():
                state[tid] = r
        except Exception:
            pass
    # OUT_OF_SCOPE / PR_OUT_OF_SCOPE were a scope-check bug (git status dir-collapse), not a
    # real failure — treat such a record as if the task never ran (PENDING) so the artifact
    # disappears from the board instead of showing a phantom failure.
    BUG_STATUSES = {"OUT_OF_SCOPE", "PR_OUT_OF_SCOPE"}
    rows = []
    for tid, d in tasks.items():
        st = state.get(tid, {})
        status = st.get("status", "PENDING")
        if status in BUG_STATUSES:
            st, status = {}, "PENDING"
        rows.append({**d, "branch": branches.get(tid, "?"),
                     "status": status,
                     "pr": st.get("pr"), "iters": st.get("iters"),
                     "ran_model": st.get("model", ""), "error": st.get("error", "")})
    order = {"MERGED": 0, "RUNNING": 1, "PENDING": 2, "FAILED_MAX_ITERS": 3,
             "STUCK_NO_PROGRESS": 3, "OUT_OF_SCOPE": 3, "PR_OUT_OF_SCOPE": 3,
             "TIMEOUT_BUDGET": 4, "WORKTREE_FAILED": 4, "ERROR": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 5), r["id"]))
    return rows


PAGE = r"""<!doctype html><html lang=pt><head><meta charset=utf-8>
<title>babylon-cinema · tasks</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={darkMode:'class'}</script>
</head>
<body class="bg-slate-950 text-slate-200 font-sans">
<header class="sticky top-0 z-10 bg-slate-900/95 backdrop-blur border-b border-slate-800 px-6 py-3">
  <div class="flex items-center gap-3">
    <h1 class="text-base font-semibold">babylon-cinema</h1>
    <span class="text-slate-500 text-sm">task status</span>
    <span id="t" class="text-slate-600 text-xs ml-auto"></span>
  </div>
  <div id="sum" class="flex gap-2 flex-wrap mt-2 text-xs"></div>
  <div id="filters" class="flex gap-2 mt-2 text-xs"></div>
  <div id="sorts" class="flex gap-2 mt-2 text-xs items-center"></div>
</header>
<main id="main" class="p-6 space-y-6 max-w-6xl mx-auto"></main>
<script>
const REPO="https://github.com/marcosremar/babylon-cinema";
const opened=new Set();
let mode='active';   // 'active' (not merged) | 'done' | 'all' — default focuses on remaining work
let sort='status';   // 'status' | 'date-asc' | 'date-desc' | 'az' | 'za'
function setMode(m){mode=m;load();}
function setSort(s){sort=s;load();}
const STORD={MERGED:0,RUNNING:1,PENDING:2,FAILED_MAX_ITERS:3,STUCK_NO_PROGRESS:3,TIMEOUT_BUDGET:4,WORKTREE_FAILED:4,ERROR:4};
function sortRows(a,b){
 if(sort=='date-asc')  return (a.created||'').localeCompare(b.created||'')||a.id.localeCompare(b.id);
 if(sort=='date-desc') return (b.created||'').localeCompare(a.created||'')||a.id.localeCompare(b.id);
 if(sort=='az') return (a.desc||'').localeCompare(b.desc||'');
 if(sort=='za') return (b.desc||'').localeCompare(a.desc||'');
 return (STORD[a.status]??5)-(STORD[b.status]??5)||a.id.localeCompare(b.id); // status
}
function inMode(s){return mode=='all'?true:(mode=='done'?s=='MERGED':s!='MERGED');}
function tg(s){const e=document.getElementById(s);const on=e.classList.contains('hidden');e.classList.toggle('hidden');if(on)opened.add(s);else opened.delete(s);}
const esc=s=>(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
function toolModel(lane,ran){const v=(ran||lane||'').toString();
 if(v.startsWith('claude'))return['Claude Code','Opus 4.8','bg-orange-500/20 text-orange-300'];
 if(v.startsWith('dumont'))return['Dumont',v.includes(':')?v.split(':')[1]:'MiniMax M2.7','bg-cyan-500/20 text-cyan-300'];
 if(v.startsWith('codex'))return['Codex',v.includes(':')?v.split(':')[1]:'gpt-5.3-codex-spark','bg-violet-500/20 text-violet-300'];
 if(v.startsWith('minimax'))return['OpenCode',v.replace('minimax/',''),'bg-cyan-500/20 text-cyan-300'];
 return[v||'?','','bg-slate-700 text-slate-300'];}
const STC={MERGED:'bg-green-600 text-white',PENDING:'bg-slate-700 text-slate-300',RUNNING:'bg-blue-600 text-white',
 ERROR:'bg-red-600 text-white',FAILED_MAX_ITERS:'bg-amber-700 text-white',STUCK_NO_PROGRESS:'bg-amber-700 text-white',
 OUT_OF_SCOPE:'bg-amber-700 text-white',PR_OUT_OF_SCOPE:'bg-amber-700 text-white',TIMEOUT_BUDGET:'bg-amber-700 text-white',WORKTREE_FAILED:'bg-red-700 text-white'};
function pill(s,cls){return `<span class="px-2 py-0.5 rounded-full text-xs font-semibold ${cls}">${s}</span>`;}
async function load(){
 const all=await (await fetch('/api/data')).json();
 const c={};all.forEach(x=>c[x.status]=(c[x.status]||0)+1);
 document.getElementById('sum').innerHTML=Object.entries(c).map(([k,v])=>pill(k+' '+v,(STC[k]||'bg-slate-700')))
   .join('')+pill('TOTAL '+all.length,'bg-slate-800 text-slate-400');
 const fb=(m,label)=>`<button onclick="setMode('${m}')" class="px-3 py-1 rounded-full ${mode==m?'bg-blue-600 text-white':'bg-slate-800 text-slate-400'}">${label}</button>`;
 const nActive=all.filter(x=>x.status!='MERGED').length, nDone=all.filter(x=>x.status=='MERGED').length;
 document.getElementById('filters').innerHTML=fb('active','⏳ Em andamento ('+nActive+')')+fb('done','✅ Concluídas ('+nDone+')')+fb('all','Todas ('+all.length+')');
 const sb=(s,label)=>`<button onclick="setSort('${s}')" class="px-3 py-1 rounded-full ${sort==s?'bg-indigo-600 text-white':'bg-slate-800 text-slate-400'}">${label}</button>`;
 document.getElementById('sorts').innerHTML='<span class="text-slate-500 mr-1">ordenar:</span>'+
   sb('status','status')+sb('date-asc','📅 data ↑')+sb('date-desc','📅 data ↓')+sb('az','A–Z')+sb('za','Z–A');
 const rows=all.filter(x=>inMode(x.status));
 document.getElementById('t').textContent=new Date().toLocaleTimeString();
 // full per-plan stats (from ALL tasks) so the progress bar is correct even when filtered
 const pstat={};all.forEach(x=>{const p=x.plan||'?';pstat[p]=pstat[p]||{done:0,total:0};pstat[p].total++;if(x.status=='MERGED')pstat[p].done++;});
 const groups={};rows.forEach(x=>{(groups[x.plan||'?']=groups[x.plan||'?']||[]).push(x);});
 let html='';let gi=0;
 for(const plan of Object.keys(groups)){
  const g=groups[plan].slice().sort(sortRows);const done=pstat[plan].done;const total=pstat[plan].total;const pct=Math.round(done/total*100);
  const badge=g[0].planner=='opus'?pill('🧠 planner','bg-violet-600 text-white'):pill('✍️ manual','bg-slate-700 text-slate-300');
  html+=`<section class="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
   <div class="px-4 py-3 border-b border-slate-800">
     <div class="flex items-center gap-2 flex-wrap">${badge}<span class="font-semibold text-slate-100">${esc(plan)}</span>
       <span class="text-slate-500 text-sm ml-auto">${done}/${total}</span></div>
     <div class="h-1.5 bg-slate-800 rounded-full mt-2"><div class="h-1.5 bg-green-600 rounded-full" style="width:${pct}%"></div></div>
   </div>
   <div class="divide-y divide-slate-800">`;
  g.forEach(x=>{gi++;const i='s'+gi;const[tool,model,tc]=toolModel(x.lane,x.ran_model);
   html+=`<div class="px-4 py-3 hover:bg-slate-800/40">
     <div class="flex items-start gap-3">
       <div class="flex-1 min-w-0">
         <div class="font-medium text-slate-100">${esc(x.desc)}</div>
         <div class="text-xs text-slate-500 font-mono mt-0.5">${x.id}<span class="ml-2 text-slate-600">📅 ${esc(x.created||'')}</span></div>
       </div>
       <div class="text-right shrink-0">
         ${pill(tool,tc)}<div class="text-xs text-slate-500 mt-0.5">${esc(model)}${x.ran_model&&x.ran_model!=x.lane?' <span class="text-amber-400">↑esc</span>':''}</div>
       </div>
       <div class="shrink-0">${pill(x.status,(STC[x.status]||'bg-slate-700'))}</div>
       <div class="shrink-0 text-sm w-12 text-right">${x.pr?`<a class="text-blue-400 hover:underline" target=_blank href="${REPO}/pull/${x.pr}">#${x.pr}</a>`:''}</div>
       <div class="shrink-0 text-xs text-slate-500 w-8 text-right">${x.iters??''}</div>
     </div>
     ${x.error?`<div class="text-xs text-red-400 mt-1">${esc(x.error).slice(0,300)}</div>`:''}
     <button onclick="tg('${i}')" class="mt-2 text-xs text-blue-400 hover:text-blue-300">📄 ver prompt</button>
     <div id="${i}" class="hidden mt-2 p-3 rounded-lg bg-slate-950 border border-slate-800 text-sm whitespace-pre-wrap leading-relaxed text-slate-300">
       <span class="text-blue-400 font-semibold">FERRAMENTA:</span> ${tool} · ${esc(model)}
       <span class="text-blue-400 font-semibold">  VERIFY:</span> ${esc(x.verify)}
       <div class="text-blue-400 font-semibold mt-2">PROMPT ENVIADO:</div>${esc(x.spec)}</div>
   </div>`;});
  html+=`</div></section>`;
 }
 document.getElementById('main').innerHTML=html;
 opened.forEach(s=>{const e=document.getElementById(s);if(e)e.classList.remove('hidden');});
}
load();setInterval(load,5000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/api/data"):
            body = json.dumps(collect()).encode()
            ctype = "application/json"
        else:
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"dashboard: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
