#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carrie's Tab Board — 动态 Chrome tab 看板
读取当前 Chrome 实时 tab -> 关键词自动归类 -> 网页里分组展示，自动刷新。
支持：点标题跳到真实 tab、关单个、一键去重、整组关闭。
运行： python3 tab_board.py   然后浏览器打开 http://localhost:8765
"""
import json, re, subprocess, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = 8765
US, RS = "\x1f", "\x1e"   # field / record separators

# ---------- 分类定义（顺序即优先级，先匹配先归类）----------
CATS = [
    ("oneone",  "🤝", "1:1 Meeting Notes"),
    ("dash",    "📊", "Dashboards & 数据"),
    ("onb",     "🧭", "Onboarding & 知识库"),
    ("edu",     "🎓", "教育项目（我的方向）"),
    ("ailearn", "🤖", "AI 学习课程"),
    ("prd",     "📐", "PRD & 治理产品参考"),
    ("access",  "🔑", "Access & Playbook"),
    ("meeting", "📋", "Meeting Notes & OKR"),
    ("support", "🛠️", "Support / How-to / 工具"),
    ("office",  "🏢", "Office / 生活"),
    ("pmail",   "✉️", "Personal · Email & YouTube"),
    ("pbday",   "🎉", "Personal · 生日派对 & Shopping"),
    ("other",   "📦", "其他 / 未分类"),
]
CAT_META = {k: (e, n) for k, e, n in CATS}

RULES = [   # (cat_key, [关键词 — 命中 title+url(小写) 任意一个即归类])
    ("oneone",  ["carrie /", " 1:1", "1:1 -"]),
    ("dash",    ["看板", "dashboard", "满意度"]),
    ("onb",     ["onboarding", "知识库", "全景分析", "journal", "landing", "文档清单"]),
    ("edu",     ["教育", "e-learning", "学习吸收", "one-pager", "申诉"]),
    ("ailearn", ["扣子", "大模型", "ai 助理", "ai bot", "学习分享", "github.com",
                 "agent_guru", "frontend-slides", "ai 的正确"]),
    ("prd",     ["prd", "penalty", "lattice", "语料库", "violation insight",
                 "demonetisation", "punishdictionary"]),
    ("access",  ["permission", "authority", "playbook", "iesapps", "/tools/auth"]),
    ("meeting", ["meeting", "minutes", "okr", "workshop", "planning", "周会"]),
    ("support", ["操作指南", "使用说明", "开播", "aime", "/drive/"]),
    ("office",  ["fitness", "coleman", "flex.plusone"]),
    ("pbday",   ["生日", "派对", "fairy", "ezcontacts", "lenses", "美瞳", "gemini.google"]),
]
HOST_RULES = [  # 按域名兜底
    ("pmail", ["mail.google.com", "youtube.com", "youtube.studio", "studio.youtube.com"]),
    ("pbday", ["gemini.google.com", "ezcontacts.com"]),
]

def categorize(title, url):
    blob = (title + " " + url).lower()
    for key, kws in RULES:
        if any(k in blob for k in kws):
            return key
    host = (urlparse(url).hostname or "").lower()
    for key, hosts in HOST_RULES:
        if any(h in host for h in hosts):
            return key
    return "other"

# ---------- AppleScript 桥接 ----------
def osa(script):
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True).stdout

READ = f'''
tell application "Google Chrome"
  set US to (ASCII character 31)
  set RS to (ASCII character 30)
  set out to ""
  repeat with w in windows
    set wid to id of w
    repeat with t in tabs of w
      set out to out & wid & US & (id of t) & US & (title of t) & US & (URL of t) & RS
    end repeat
  end repeat
  return out
end tell
'''

def read_tabs():
    raw = osa(READ)
    tabs = []
    for rec in raw.split(RS):
        if US not in rec:
            continue
        parts = rec.split(US)
        if len(parts) < 4:
            continue
        wid, tid, title, url = parts[0], parts[1], parts[2], parts[3]
        host = (urlparse(url).hostname or "")
        if host in ("localhost", "127.0.0.1") and f":{PORT}" in url:
            continue   # 隐藏看板自己
        tabs.append({"wid": wid.strip(), "tid": tid.strip(),
                     "title": title.strip() or url, "url": url.strip()})
    return tabs

def grouped():
    tabs = read_tabs()
    for t in tabs:
        t["cat"] = categorize(t["title"], t["url"])
    out = []
    for key, emoji, name in CATS:
        items = [t for t in tabs if t["cat"] == key]
        if items:
            out.append({"key": key, "emoji": emoji, "name": name, "tabs": items})
    return {"cats": out, "total": len(tabs)}

def activate(wid, tid):
    osa(f'''tell application "Google Chrome"
      set theWin to window id {wid}
      set i to 0
      repeat with t in tabs of theWin
        set i to i + 1
        if id of t is {tid} then
          set active tab index of theWin to i
          set index of theWin to 1
        end if
      end repeat
      activate
    end tell''')

def close_tab(wid, tid):
    osa(f'tell application "Google Chrome" to close (every tab of window id {wid} whose id is {tid})')

def close_group(cat):
    for t in read_tabs():
        if categorize(t["title"], t["url"]) == cat:
            close_tab(t["wid"], t["tid"])

def dedup():
    seen, removed = set(), 0
    for t in read_tabs():
        if t["url"] in seen:
            close_tab(t["wid"], t["tid"]); removed += 1
        else:
            seen.add(t["url"])
    return removed

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, ctype="application/json"):
        b = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/tabs"):
            self._send(json.dumps(grouped()))
        else:
            self._send(PAGE.replace("__PORT__", str(PORT)), "text/html")

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(ln) or "{}")
        p = self.path
        if p == "/api/activate":  activate(data["wid"], data["tid"])
        elif p == "/api/close":   close_tab(data["wid"], data["tid"])
        elif p == "/api/closegroup": close_group(data["cat"])
        elif p == "/api/dedup":   return self._send(json.dumps({"removed": dedup()}))
        self._send(json.dumps({"ok": True}))

PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>📑 Carrie's Tab Board · live</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1d212b;--line:#2a2f3a;--txt:#e6e8ee;--muted:#8b93a7;--accent:#7aa2ff;--warn:#ffb454}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"PingFang SC","Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt);font-size:14px;line-height:1.45}
header{position:sticky;top:0;z-index:20;background:rgba(15,17,21,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:600}
.stat{color:var(--muted);font-size:12.5px}
.dot{width:7px;height:7px;border-radius:50%;background:#3ddc84;display:inline-block;margin-right:5px;animation:p 2s infinite}
@keyframes p{50%{opacity:.3}}
.controls{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#search{background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--txt);padding:7px 11px;width:220px;outline:none}
#search:focus{border-color:var(--accent)}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--txt);padding:7px 11px;cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent)}
.grid{column-width:420px;column-gap:18px;padding:16px 22px 60px}
section.cat{break-inside:avoid;background:var(--panel);border:1px solid var(--line);border-radius:14px;margin:0 0 16px;overflow:hidden;display:inline-block;width:100%}
.cat-head{display:flex;align-items:center;gap:9px;padding:11px 13px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel))}
.cat-head .emoji{font-size:17px}.cat-head .name{font-weight:600}.cat-head .count{color:var(--muted);font-size:12px}
.cat-head .closeg{margin-left:auto;font-size:11.5px;color:var(--muted);cursor:pointer;border:1px solid var(--line);border-radius:6px;padding:3px 8px}
.cat-head .closeg:hover{color:var(--warn);border-color:var(--warn)}
ul{list-style:none;margin:0;padding:6px}
li{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:8px}
li:hover{background:var(--panel2)}
li img{width:16px;height:16px;border-radius:3px;flex:none;background:#222}
li .title{color:var(--txt);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
li .title:hover{color:var(--accent)}
li .x{flex:none;color:var(--muted);cursor:pointer;border-radius:5px;padding:0 6px;font-size:13px;opacity:0;visibility:hidden}
li:hover .x{opacity:1;visibility:visible}
li .x:hover{color:var(--warn);background:#3a2a18}
.empty{color:var(--muted);text-align:center;padding:80px 0}
footer{color:var(--muted);font-size:11.5px;padding:0 22px 40px;text-align:center}
</style></head><body>
<header>
  <h1>📑 Tab Board</h1>
  <span class="stat"><span class="dot"></span><span id="stat">读取中…</span></span>
  <div class="controls">
    <input id="search" placeholder="🔍 搜索标题…">
    <button class="btn" id="dedup">🧹 一键去重</button>
    <button class="btn" id="refresh">↻ 刷新</button>
  </div>
</header>
<div class="grid" id="grid"></div>
<footer>实时读取当前 Chrome · 每 2 秒自动更新 · 点标题跳到真实 tab · 悬停出现 ✕ 关闭</footer>
<script>
const grid=document.getElementById('grid'), stat=document.getElementById('stat'), search=document.getElementById('search');
let lastSig="", filter="";
const fav=u=>{try{return"https://www.google.com/s2/favicons?domain="+new URL(u).hostname+"&sz=32"}catch(e){return""}};
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})}).then(r=>r.json());}

function render(d){
  stat.textContent=`${d.total} tabs · ${d.cats.length} 类`;
  if(!d.cats.length){grid.innerHTML='<div class="empty">没有读到 tab —— Chrome 开着吗？</div>';return;}
  grid.innerHTML=d.cats.map(c=>`
    <section class="cat" data-key="${c.key}">
      <div class="cat-head"><span class="emoji">${c.emoji}</span><span class="name">${esc(c.name)}</span>
        <span class="count">${c.tabs.length}</span>
        <span class="closeg" data-cat="${c.key}">关闭整组 ✕</span></div>
      <ul>${c.tabs.map(t=>`
        <li data-title="${esc(t.title.toLowerCase())}">
          <img src="${fav(t.url)}" onerror="this.style.visibility='hidden'">
          <span class="title" data-wid="${t.wid}" data-tid="${t.tid}" title="${esc(t.title)}">${esc(t.title)}</span>
          <span class="x" data-wid="${t.wid}" data-tid="${t.tid}">✕</span>
        </li>`).join("")}</ul>
    </section>`).join("");
  applyFilter();
}
function applyFilter(){
  const q=filter.trim().toLowerCase();
  document.querySelectorAll('li').forEach(li=>li.style.display=(!q||li.dataset.title.includes(q))?"":"none");
  document.querySelectorAll('section.cat').forEach(s=>{
    const any=[...s.querySelectorAll('li')].some(li=>li.style.display!=="none");
    s.style.display=any?"":"none";
  });
}
async function poll(){
  try{
    const d=await fetch("/api/tabs").then(r=>r.json());
    const sig=JSON.stringify(d);
    if(sig!==lastSig){lastSig=sig;render(d);}   // 无变化不重绘，避免闪烁
  }catch(e){stat.textContent="连接断开 —— 服务还在跑吗？";}
}
grid.addEventListener('click',async e=>{
  const t=e.target;
  if(t.classList.contains('title')){await api("/api/activate",{wid:t.dataset.wid,tid:t.dataset.tid});}
  else if(t.classList.contains('x')){t.closest('li').style.opacity=.3;await api("/api/close",{wid:t.dataset.wid,tid:t.dataset.tid});lastSig="";poll();}
  else if(t.classList.contains('closeg')){
    if(confirm("关闭这一整组 tab？")){await api("/api/closegroup",{cat:t.dataset.cat});lastSig="";poll();}
  }
});
document.getElementById('dedup').onclick=async()=>{const r=await api("/api/dedup");alert(`已关闭 ${r.removed} 个重复 tab`);lastSig="";poll();};
document.getElementById('refresh').onclick=()=>{lastSig="";poll();};
search.addEventListener('input',e=>{filter=e.target.value;applyFilter();});
poll();setInterval(poll,2000);
</script></body></html>"""

def main():
    url = f"http://localhost:{PORT}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError as e:
        if e.errno in (48, 98):   # 端口被占用：很可能已经在跑了
            print(f"ℹ️  Tab Board 似乎已在运行 → {url}，直接打开。")
            webbrowser.open(url)
            return
        raise
    print(f"✅ Tab Board 运行中 → {url}  (Ctrl+C 停止)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
