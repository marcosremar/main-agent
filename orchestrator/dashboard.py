"""Tiny status dashboard — no deps (stdlib http.server).

Cross-references the task definitions (config-*.json) with the orchestrator's state
journals (state/state-*.json) and serves an auto-refreshing HTML page: which tasks are
MERGED / FAILED / ERROR / PENDING, by lane, with PR#, iters, files, and the spec on click.

Run:  python3 dashboard.py [port]      (default 8787)  ->  http://127.0.0.1:8787
"""
import glob
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def collect():
    tasks = {}          # id -> definition
    branches = {}       # id -> integration_branch
    for cf in sorted(glob.glob(os.path.join(HERE, "config*.json"))):
        try:
            cfg = json.load(open(cf))
        except Exception:
            continue
        ib = cfg.get("integration_branch", "?")
        for t in cfg.get("tasks", []):
            tasks[t["id"]] = {
                "id": t["id"],
                "lane": t.get("worker_model", "?"),
                "verify": t.get("verify_cmd", ""),
                "files": t.get("allowed_files", []),
                "commit": t.get("commit", ""),
                "spec": t.get("spec", ""),
                "config": os.path.basename(cf),
            }
            branches[t["id"]] = ib
    state = {}
    for sf in glob.glob(os.path.join(HERE, "state", "state-*.json")):
        try:
            for tid, r in json.load(open(sf)).items():
                state[tid] = r
        except Exception:
            pass
    rows = []
    for tid, d in tasks.items():
        st = state.get(tid, {})
        rows.append({**d, "branch": branches.get(tid, "?"),
                     "status": st.get("status", "PENDING"),
                     "pr": st.get("pr"), "iters": st.get("iters"),
                     "ran_model": st.get("model", ""),   # actual final model (may differ via escalation)
                     "error": st.get("error", "")})
    order = {"MERGED": 0, "RUNNING": 1, "PENDING": 2, "FAILED_MAX_ITERS": 3,
             "OUT_OF_SCOPE": 3, "PR_OUT_OF_SCOPE": 3, "ERROR": 4, "TIMEOUT_BUDGET": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 5), r["id"]))
    return rows


PAGE = """<!doctype html><meta charset=utf-8><title>babylon-cinema · task status</title>
<style>
body{font:14px -apple-system,system-ui,sans-serif;margin:0;background:#0d1117;color:#e6edf3}
header{padding:14px 20px;background:#161b22;border-bottom:1px solid #30363d;position:sticky;top:0}
h1{font-size:16px;margin:0 0 6px} .sum{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
.pill{padding:2px 9px;border-radius:12px;font-weight:600}
.MERGED{background:#1a7f37;color:#fff}.PENDING{background:#30363d;color:#adbac7}
.FAILED_MAX_ITERS,.OUT_OF_SCOPE,.PR_OUT_OF_SCOPE,.TIMEOUT_BUDGET,.STUCK_NO_PROGRESS{background:#9e6a03;color:#fff}
.ERROR{background:#cf222e;color:#fff}.RUNNING{background:#1f6feb;color:#fff}
table{border-collapse:collapse;width:100%}
td,th{padding:7px 12px;border-bottom:1px solid #21262d;text-align:left;vertical-align:top}
th{color:#7d8590;font-size:12px;text-transform:uppercase}
tr:hover{background:#161b22}.tool{font-weight:600;color:#e6edf3}.lane{font-family:monospace;font-size:12px;color:#8b949e}
.files{font-family:monospace;font-size:11px;color:#6e7681}
.spec{display:none;white-space:pre-wrap;font-size:14px;line-height:1.55;color:#c9d1d9;max-width:1000px;background:#0b0f14;border:1px solid #30363d;border-radius:6px;padding:12px;margin-top:8px}
.id{cursor:pointer;font-weight:600}a{color:#58a6ff}
</style>
<header><h1>babylon-cinema · task status <span id=t style=color:#7d8590;font-weight:400></span></h1>
<div class=sum id=sum></div></header>
<table><thead><tr><th>task<th>tool · model<th>status<th>PR<th>iters<th>files / detail</tr></thead><tbody id=b></tbody></table>
<script>
const REPO="https://github.com/marcosremar/babylon-cinema";
const opened=new Set();
function tg(s){const e=document.getElementById(s);const on=e.style.display!='block';e.style.display=on?'block':'none';if(on)opened.add(s);else opened.delete(s);}
function toolModel(lane, ran){
 // ran = actual final model used (preferred); lane = configured starting worker
 const v=(ran||lane||'').toString();
 if(v.startsWith('claude')) return ['Claude Code','Opus 4.8'];
 if(v.startsWith('dumont')) return ['Dumont', v.includes(':')? v.split(':')[1] : 'MiniMax M2.7'];
 if(v.startsWith('minimax')) return ['OpenCode', v.replace('minimax/','')];
 return [v||'?',''];
}
async function load(){
 const r=await fetch('/api/data');const rows=await r.json();
 const c={};rows.forEach(x=>c[x.status]=(c[x.status]||0)+1);
 document.getElementById('sum').innerHTML=Object.entries(c).map(([k,v])=>`<span class="pill ${k}">${k} ${v}</span>`).join('')+`<span class=pill style=background:#30363d>TOTAL ${rows.length}</span>`;
 document.getElementById('t').textContent=' · '+new Date().toLocaleTimeString();
 const esc=s=>(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
 document.getElementById('b').innerHTML=rows.map((x,i)=>{const[tool,model]=toolModel(x.lane,x.ran_model);return `<tr>
  <td><b>${x.id}</b><div style=color:#7d8590;font-size:12px;max-width:340px>${esc(x.commit)}</div></td>
  <td><span class=tool>${tool}</span><div class=lane>${esc(model)}${x.ran_model&&x.ran_model!=x.lane?' <span style=color:#d29922>(escalou)</span>':''}</div></td>
  <td><span class="pill ${x.status}">${x.status}</span></td>
  <td>${x.pr?`<a href="${REPO}/pull/${x.pr}" target=_blank>#${x.pr}</a>`:''}</td>
  <td>${x.iters??''}</td>
  <td><button onclick="tg('s${i}')" style="background:#21262d;color:#58a6ff;border:1px solid #30363d;border-radius:5px;padding:3px 9px;cursor:pointer">📄 ver prompt</button>
   <span class=files style=margin-left:8px>${(x.files||[]).join(' · ')}</span>${x.error?`<div style=color:#f85149;font-size:11px>${esc(x.error).slice(0,300)}</div>`:''}
   <div class=spec id=s${i}><b style=color:#58a6ff>FERRAMENTA:</b> ${tool} · ${esc(model)}<br><b style=color:#58a6ff>VERIFY:</b> ${esc(x.verify)}<br><b style=color:#58a6ff>PROMPT ENVIADO:</b><br>${esc(x.spec)}</div></td></tr>`}).join('');
 opened.forEach(s=>{const e=document.getElementById(s);if(e)e.style.display='block';});
}
load();setInterval(load,5000);
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/api/data"):
            body = json.dumps(collect()).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"dashboard: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
