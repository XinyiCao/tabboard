#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carrie's Tab Board — 动态 Chrome tab 看板
读取当前 Chrome 实时 tab -> 关键词自动归类 -> 网页里分组展示，自动刷新。
支持：点标题跳到真实 tab、关单个、一键去重、整组关闭。
运行： python3 tab_board.py   然后浏览器打开 http://localhost:8765
"""
import json, re, subprocess, threading, webbrowser, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from pathlib import Path

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

# ---------- 自动备份（每次写入前，把旧版本存一份带时间戳的快照）----------
BACKUP_DIR = Path.home() / ".tabboard_backups"

def backup_file(path, keep=20):
    """写入前调用：把 path 当前内容快照到 ~/.tabboard_backups/，每个文件保留最近 keep 份。"""
    if not path.exists():
        return
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (BACKUP_DIR / f"{path.name}.{stamp}.bak").write_bytes(path.read_bytes())
        snaps = sorted(BACKUP_DIR.glob(f"{path.name}.*.bak"))
        for old in snaps[:-keep]:        # 超出保留数的最老快照删掉
            old.unlink()
    except Exception as e:
        print("⚠️ 备份失败:", e)

# ---------- 手动归类覆盖（拖动产生，按 URL 持久化）----------
OVERRIDES_FILE = Path.home() / ".tabboard_overrides.json"

def load_overrides():
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_overrides(d):
    try:
        backup_file(OVERRIDES_FILE)
        OVERRIDES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存分类覆盖失败:", e)

OVERRIDES = load_overrides()   # {url: cat_key}

# ---------- 暂存区（park：关掉真实 tab 但记住，可一键找回）----------
PARKED_FILE = Path.home() / ".tabboard_parked.json"

def load_parked():
    try:
        return json.loads(PARKED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_parked(d):
    try:
        backup_file(PARKED_FILE)
        PARKED_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存暂存区失败:", e)

PARKED = load_parked()   # {url: {url, title, ts}}

# ---------- 收藏夹（library：永久留存，打开不消失，可视化分类）----------
LIBRARY_FILE = Path.home() / ".tabboard_library.json"

def load_library():
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_library(d):
    try:
        backup_file(LIBRARY_FILE)
        LIBRARY_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存收藏夹失败:", e)

LIBRARY = load_library()   # {url: {url, title, ts}}

def categorize(title, url):
    if url in OVERRIDES:                 # 你手动拖过的，优先听你的
        return OVERRIDES[url]
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

def group_by_cat(items):
    out = []
    for key, emoji, name in CATS:
        g = [it for it in items if categorize(it["title"], it["url"]) == key]
        if g:
            out.append({"key": key, "emoji": emoji, "name": name, "tabs": g})
    return out

def grouped():
    tabs = read_tabs()
    for t in tabs:
        t["saved"] = t["url"] in LIBRARY     # 标记是否已在收藏夹
    now = int(time.time())
    libvals = []
    for it in sorted(LIBRARY.values(), key=lambda x: -x.get("last_opened", x.get("ts", 0))):
        lo = it.get("last_opened", it.get("ts", now))
        it2 = dict(it)
        it2["idle_days"] = (now - lo) // 86400
        it2["stale"] = (now - lo) > 30 * 86400      # 超 30 天没点 = 久未访问
        libvals.append(it2)
    return {"cats": group_by_cat(tabs), "total": len(tabs),
            "parked": parked_list(),
            "library": group_by_cat(libvals)}

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

def read_one(wid, tid):
    out = osa(f'''tell application "Google Chrome"
      set US to (ASCII character 31)
      set L to (every tab of window id {wid} whose id is {tid})
      if (count of L) is 0 then return ""
      set t to item 1 of L
      return (title of t) & US & (URL of t)
    end tell''')
    if US in out:
        title, url = out.strip().split(US, 1)
        return title.strip(), url.strip()
    return None, None

def park(wid, tid):
    title, url = read_one(wid, tid)
    if not url:
        return
    PARKED[url] = {"url": url, "title": title or url, "ts": int(time.time())}
    save_parked(PARKED)
    close_tab(wid, tid)

def open_url(url):
    safe = url.replace("\\", "\\\\").replace('"', '\\"')
    osa(f'''tell application "Google Chrome"
      if (count of windows) = 0 then make new window
      make new tab at end of tabs of front window with properties {{URL:"{safe}"}}
      activate
    end tell''')

def unpark(url, open_it):
    PARKED.pop(url, None)
    save_parked(PARKED)
    if open_it:
        open_url(url)

def parked_list():
    return sorted(PARKED.values(), key=lambda x: -x.get("ts", 0))

def save_lib(wid, tid):
    title, url = read_one(wid, tid)
    if not url:
        return
    now = int(time.time())
    LIBRARY[url] = {"url": url, "title": title or url, "ts": now, "last_opened": now}
    save_library(LIBRARY)        # 只收藏，不关闭（tab 由你自己点 ✕ 关）

def lib_from_parked(url):
    item = PARKED.pop(url, None)
    if item:
        now = int(time.time())
        item["ts"] = now; item["last_opened"] = now
        LIBRARY[url] = item
        save_parked(PARKED); save_library(LIBRARY)

def lib_remove(url):
    LIBRARY.pop(url, None)
    save_library(LIBRARY)

def lib_open(url):
    if url in LIBRARY:
        LIBRARY[url]["last_opened"] = int(time.time())   # 打开即刷新“最后访问”
        save_library(LIBRARY)
    open_url(url)

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
        elif p == "/api/recategorize":
            url, cat = data.get("url"), data.get("cat")
            if url and cat in CAT_META:
                OVERRIDES[url] = cat
                save_overrides(OVERRIDES)
        elif p == "/api/park":    park(data["wid"], data["tid"])
        elif p == "/api/unpark":  unpark(data.get("url"), data.get("open", False))
        elif p == "/api/save":    save_lib(data["wid"], data["tid"])      # 实时 tab -> 收藏夹（不关闭）
        elif p == "/api/libfromparked": lib_from_parked(data.get("url"))  # 暂存 -> 收藏夹
        elif p == "/api/libopen": lib_open(data.get("url"))               # 打开收藏（不移除，刷新最后访问）
        elif p == "/api/libremove": lib_remove(data.get("url"))           # 移除收藏
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
.grid{display:flex;gap:18px;align-items:flex-start;padding:16px 22px 60px}
.col{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:16px}
section.cat{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:14px}
.cat-head{display:flex;align-items:center;gap:9px;padding:11px 13px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel));cursor:pointer;user-select:none;border-radius:13px 13px 0 0}
.cat-head:hover{background:var(--panel2)}
.caret{color:var(--muted);font-size:10px;flex:none;transition:transform .28s cubic-bezier(.4,0,.2,1)}
.cat.collapsed .caret{transform:rotate(-90deg)}
.cat.collapsed .cat-head{border-bottom:none}
.cat-head .emoji{font-size:17px}.cat-head .name{font-weight:600}.cat-head .count{color:var(--muted);font-size:12px}
.cat-head .closeg{margin-left:auto;font-size:11.5px;color:var(--muted);cursor:pointer;border:1px solid var(--line);border-radius:6px;padding:3px 8px}
.cat-head .closeg:hover{color:var(--warn);border-color:var(--warn)}
.cat-body{display:grid;grid-template-rows:1fr;transition:grid-template-rows .28s cubic-bezier(.4,0,.2,1),opacity .2s ease}
.cat.collapsed .cat-body{grid-template-rows:0fr;opacity:.4}
ul{list-style:none;margin:0;padding:6px;overflow:hidden;min-height:0}
li{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:8px;cursor:grab}
li:hover{background:var(--panel2)}
li:active{cursor:grabbing}
li.drag{opacity:.4}
section.cat.dropping{outline:2px dashed var(--accent);outline-offset:-3px;background:#1a2030}
.grip{color:var(--muted);cursor:grab;font-size:14px;flex:none;line-height:1;letter-spacing:-3px;padding-right:2px}
.grip:active{cursor:grabbing}
.cat-head:hover .grip{color:var(--txt)}
section.cat.drag{opacity:.45}
.cat.collapsed .cat-head{border-radius:13px}
/* 重排插入线：显示模块将落在目标的上方或下方 */
.cat.insert-before::before,.cat.insert-after::after{content:"";position:absolute;left:0;right:0;height:3px;border-radius:3px;background:var(--accent);box-shadow:0 0 7px var(--accent);z-index:5}
.cat.insert-before::before{top:-10px}
.cat.insert-after::after{bottom:-10px}
li img{width:16px;height:16px;border-radius:3px;flex:none;background:#222}
li .title,li .ptitle,li .ltitle{color:var(--txt);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
li .title:hover,li .ptitle:hover,li .ltitle:hover{color:var(--accent)}
li .age{flex:none;color:var(--muted);font-size:11px}
li .x,li .park,li .del,li .star,li .pstar,li .libdel{flex:none;color:var(--muted);cursor:pointer;border-radius:5px;padding:0 6px;font-size:13px;opacity:0;visibility:hidden;filter:grayscale(1)}
li:hover .x,li:hover .park,li:hover .del,li:hover .star,li:hover .pstar,li:hover .libdel{opacity:1;visibility:visible}
li .x:hover,li .del:hover,li .libdel:hover{color:var(--warn);background:#3a2a18;filter:none}
li .park:hover{color:#3ddc84;background:#16301f}
li .star:hover,li .pstar:hover{background:#332a10;filter:none}
li .star.saved{opacity:1;visibility:visible;filter:none;color:#ffc83d}
li .age.stale{color:var(--warn)}
#notice{padding:0 22px}
.review{background:#2a2113;border:1px solid #5a4420;border-radius:10px;padding:10px 14px;margin:12px 0 0;display:flex;align-items:center;gap:10px;color:#ffd98a;font-size:13px}
.review b{color:#ffc83d}
.review .btn{padding:5px 10px;font-size:12.5px}
section.cat[data-key="__parked__"]{border-color:#5a4420}
section.cat[data-key="__parked__"] .cat-head{background:linear-gradient(180deg,#2a2113,var(--panel))}
.empty{color:var(--muted);text-align:center;padding:80px 0}
footer{color:var(--muted);font-size:11.5px;padding:0 22px 40px;text-align:center}
</style></head><body>
<header>
  <h1>📑 Tab Board</h1>
  <span class="stat"><span class="dot"></span><span id="stat">读取中…</span></span>
  <div class="controls">
    <input id="search" placeholder="🔍 搜索标题…">
    <button class="btn" id="libBtn">📚 收藏夹</button>
    <button class="btn" id="dedup">🧹 一键去重</button>
    <button class="btn" id="refresh">↻ 刷新</button>
  </div>
</header>
<div id="notice"></div>
<div class="grid" id="grid"></div>
<footer>⭐ 收藏（不关闭，金色=已收藏，再点取消）· 📥 暂存（关闭但记住）· ✕ 关闭 ｜ 顶部「📚 收藏夹」切换视图，点标题打开<b>不消失</b></footer>
<script>
const grid=document.getElementById('grid'), stat=document.getElementById('stat'), search=document.getElementById('search');
window.addEventListener('error',e=>{if(stat)stat.textContent="⚠️ JS错误: "+(e.message||e.error);});
const libBtn=document.getElementById('libBtn'), noticeEl=document.getElementById('notice');
libBtn.onclick=()=>{mode=(mode==='lib'?'live':'lib');filter='';search.value='';staleOnly=false;reviewDismissed=false;if(lastData)render(lastData);};
noticeEl.addEventListener('click',e=>{
  const now=String(Math.floor(Date.now()/1000));
  if(e.target.classList.contains('rv-show')){staleOnly=true;reviewDismissed=true;localStorage.setItem('tb_lastreview',now);render(lastData);}
  else if(e.target.classList.contains('rv-ok')){reviewDismissed=true;localStorage.setItem('tb_lastreview',now);render(lastData);}
  else if(e.target.classList.contains('rv-all')){staleOnly=false;render(lastData);}
});
let lastSig="", filter="", dragging=false, dragMode=null, dragKey=null, staleOnly=false, reviewDismissed=false;
const collapsed=new Set(JSON.parse(localStorage.getItem('tb_collapsed')||'[]'));

// 分类顺序（拖动手柄重排，存 localStorage）
function orderedCats(cats){
  const ord=JSON.parse(localStorage.getItem('tb_order')||'[]');
  if(!ord.length) return cats;
  return [...cats].sort((a,b)=>{
    let ia=ord.indexOf(a.key), ib=ord.indexOf(b.key);
    if(ia<0) ia=999+cats.indexOf(a);   // 新出现的分类排在已排序的后面
    if(ib<0) ib=999+cats.indexOf(b);
    return ia-ib;
  });
}
function reorder(dragK, dropK, after){
  let keys=orderedCats(lastData.cats).map(c=>c.key).filter(k=>k!==dragK);
  let i=keys.indexOf(dropK);
  if(after) i+=1;                               // 落在目标下方 => 插到其后
  keys.splice(i, 0, dragK);
  localStorage.setItem('tb_order', JSON.stringify(keys));
  render(lastData);
}
function clearInsert(){document.querySelectorAll('.insert-before,.insert-after').forEach(x=>x.classList.remove('insert-before','insert-after'));}
function dropAfter(sec,e){const r=sec.getBoundingClientRect();return e.clientY > r.top + r.height/2;}
const fav=u=>{try{return"https://www.google.com/s2/favicons?domain="+new URL(u).hostname+"&sz=32"}catch(e){return""}};
const esc=s=>s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})}).then(r=>r.json());}

function ago(ts){
  const s=Math.max(0,Math.floor(Date.now()/1000)-(ts||0));
  if(s<3600) return Math.max(1,Math.floor(s/60))+'分钟前';
  if(s<86400) return Math.floor(s/3600)+'小时前';
  return Math.floor(s/86400)+'天前';
}
function liveItem(t){return `
        <li draggable="true" data-title="${esc(t.title.toLowerCase())}" data-url="${esc(t.url)}">
          <img src="${fav(t.url)}" draggable="false" onerror="this.style.visibility='hidden'">
          <span class="title" data-wid="${t.wid}" data-tid="${t.tid}" title="${esc(t.title)}">${esc(t.title)}</span>
          <span class="star${t.saved?' saved':''}" data-wid="${t.wid}" data-tid="${t.tid}" data-url="${esc(t.url)}" title="${t.saved?'已在收藏夹（点击取消收藏）':'收藏（不关闭，存入收藏夹）'}">⭐</span>
          <span class="park" data-wid="${t.wid}" data-tid="${t.tid}" title="暂存（关闭但记住，之后可一键找回）">📥</span>
          <span class="x" data-wid="${t.wid}" data-tid="${t.tid}" title="关闭">✕</span>
        </li>`;}
function parkedItem(t){return `
        <li data-title="${esc(t.title.toLowerCase())}" data-url="${esc(t.url)}">
          <img src="${fav(t.url)}" draggable="false" onerror="this.style.visibility='hidden'">
          <span class="ptitle" data-url="${esc(t.url)}" title="点击重新打开：${esc(t.title)}">${esc(t.title)}</span>
          <span class="age">${ago(t.ts)}</span>
          <span class="pstar" data-url="${esc(t.url)}" title="存入收藏夹（永久）">⭐</span>
          <span class="del" data-url="${esc(t.url)}" title="从暂存区删除">🗑</span>
        </li>`;}
function libItem(t){return `
        <li data-title="${esc(t.title.toLowerCase())}" data-url="${esc(t.url)}">
          <img src="${fav(t.url)}" draggable="false" onerror="this.style.visibility='hidden'">
          <span class="ltitle" data-url="${esc(t.url)}" title="点击打开（保留在收藏夹）：${esc(t.title)}">${esc(t.title)}</span>
          <span class="age${t.stale?' stale':''}">${t.stale?('💤 '+t.idle_days+'天没点'):ago(t.last_opened||t.ts)}</span>
          <span class="libdel" data-url="${esc(t.url)}" title="移除收藏">🗑</span>
        </li>`;}
function sectionHTML(c){
  const plain=c.parked||c.lib;     // 暂存区/收藏夹：不需要手柄和“关闭整组”
  const grip=plain?'':'<span class="grip" draggable="true" title="拖动调整分组顺序">⠿</span>';
  const tail=plain?'':`<span class="closeg" data-cat="${c.key}">关闭整组 ✕</span>`;
  const itemFn=c.parked?parkedItem:(c.lib?libItem:liveItem);
  const items=c.tabs.map(itemFn).join("");
  return `
    <section class="cat ${collapsed.has(c.key)?'collapsed':''}" data-key="${c.key}">
      <div class="cat-head">${grip}<span class="caret">▾</span><span class="emoji">${c.emoji}</span><span class="name">${esc(c.name)}</span>
        <span class="count">${c.tabs.length}</span>${tail}</div>
      <div class="cat-body"><ul>${items}</ul></div>
    </section>`;
}
let lastData=null, mode='live';      // 'live' = 实时 tab + 暂存区；'lib' = 收藏夹
function render(d){
  lastData=d;
  const np=(d.parked||[]).length;
  const nl=(d.library||[]).reduce((s,c)=>s+c.tabs.length,0);
  const staleCount=(d.library||[]).reduce((s,c)=>s+c.tabs.filter(t=>t.stale).length,0);
  libBtn.textContent = mode==='lib' ? '← 返回实时 tab' : `📚 收藏夹 (${nl})`;
  noticeEl.innerHTML='';
  let groups=[];
  if(mode==='lib'){
    stat.textContent=`📚 收藏夹 · ${nl} 项`+(staleCount?` · 💤 ${staleCount} 久未访问`:'');
    const lastReview=parseFloat(localStorage.getItem('tb_lastreview')||'0');
    const due=(Date.now()/1000 - lastReview) > 7*86400;          // 距上次清理 ≥ 7 天
    if(staleOnly){
      noticeEl.innerHTML=`<div class="review">🧹 只看久未访问（≥30天，共 ${staleCount} 项）<button class="btn rv-all">← 显示全部收藏</button></div>`;
    }else if(staleCount>0 && due && !reviewDismissed){
      noticeEl.innerHTML=`<div class="review">🧹 收藏夹该清理了：<b>${staleCount}</b> 项超过 30 天没点 <button class="btn rv-show">只看这些</button> <button class="btn rv-ok">知道了</button></div>`;
    }
    if(!nl){grid.innerHTML='<div class="empty">收藏夹还是空的 —— 在实时 tab 上点 ⭐ 收藏</div>';return;}
    groups=(d.library||[]).map(c=>({...c,lib:true}));
    if(staleOnly){groups=groups.map(c=>({...c,tabs:c.tabs.filter(t=>t.stale)})).filter(c=>c.tabs.length);}
    if(!groups.length){grid.innerHTML='<div class="empty">没有久未访问的收藏 🎉</div>';return;}
  }else{
    stat.textContent=`${d.total} tabs · ${d.cats.length} 类`+(np?` · 📦 ${np} 暂存`:'');
    if(!d.cats.length && !np){grid.innerHTML='<div class="empty">没有读到 tab —— Chrome 开着吗？</div>';return;}
    if(np) groups.push({key:'__parked__',emoji:'📦',name:'暂存区',parked:true,tabs:d.parked});  // 固定置顶
    groups.push(...orderedCats(d.cats));
  }
  // 固定独立列：按序轮流分到各列，列与列互不影响（折叠不会引起跨列跳动）
  let ncols=Math.floor((grid.clientWidth||window.innerWidth||1200)/430);
  if(!Number.isFinite(ncols)||ncols<1) ncols=1;   // 防 clientWidth 异常导致 NaN
  const cols=Array.from({length:ncols},()=>[]);
  groups.forEach((c,i)=>cols[i%ncols].push(sectionHTML(c)));
  grid.innerHTML=cols.map(c=>`<div class="col">${c.join("")}</div>`).join("");
  applyFilter();
}
let resizeT;
window.addEventListener('resize',()=>{clearTimeout(resizeT);resizeT=setTimeout(()=>{if(lastData)render(lastData);},120);});
function applyFilter(){
  const q=filter.trim().toLowerCase();
  document.querySelectorAll('li').forEach(li=>li.style.display=(!q||li.dataset.title.includes(q))?"":"none");
  document.querySelectorAll('section.cat').forEach(s=>{
    const any=[...s.querySelectorAll('li')].some(li=>li.style.display!=="none");
    s.style.display=any?"":"none";
  });
}
async function poll(){
  if(dragging) return;   // 拖动中不重绘，免得打断拖拽
  try{
    const d=await fetch("/api/tabs").then(r=>r.json());
    const sig=JSON.stringify(d);
    if(sig!==lastSig){lastSig=sig;render(d);}   // 无变化不重绘，避免闪烁
  }catch(e){stat.textContent="⚠️ "+(e&&e.message?e.message:"连接断开 —— 服务还在跑吗？");}
}
grid.addEventListener('click',async e=>{
  const t=e.target;
  if(t.classList.contains('title')){await api("/api/activate",{wid:t.dataset.wid,tid:t.dataset.tid});}
  else if(t.classList.contains('star')){
    if(t.classList.contains('saved')) await api("/api/libremove",{url:t.dataset.url});  // 已收藏 -> 取消
    else await api("/api/save",{wid:t.dataset.wid,tid:t.dataset.tid});                  // 未收藏 -> 收藏（不关闭）
    lastSig="";poll();
  }
  else if(t.classList.contains('park')){t.closest('li').style.opacity=.3;await api("/api/park",{wid:t.dataset.wid,tid:t.dataset.tid});lastSig="";poll();}
  else if(t.classList.contains('ptitle')){await api("/api/unpark",{url:t.dataset.url,open:true});lastSig="";poll();}
  else if(t.classList.contains('pstar')){t.closest('li').style.opacity=.3;await api("/api/libfromparked",{url:t.dataset.url});lastSig="";poll();}
  else if(t.classList.contains('ltitle')){await api("/api/libopen",{url:t.dataset.url});}
  else if(t.classList.contains('libdel')){t.closest('li').style.opacity=.3;await api("/api/libremove",{url:t.dataset.url});lastSig="";poll();}
  else if(t.classList.contains('del')){t.closest('li').style.opacity=.3;await api("/api/unpark",{url:t.dataset.url,open:false});lastSig="";poll();}
  else if(t.classList.contains('x')){t.closest('li').style.opacity=.3;await api("/api/close",{wid:t.dataset.wid,tid:t.dataset.tid});lastSig="";poll();}
  else if(t.classList.contains('closeg')){
    if(confirm("关闭这一整组 tab？")){await api("/api/closegroup",{cat:t.dataset.cat});lastSig="";poll();}
  }
  else if(t.closest('.cat-head') && !t.classList.contains('grip')){   // 点标题栏：折叠/展开（手柄除外）
    const sec=t.closest('section.cat'), key=sec.dataset.key;
    const nowCollapsed=sec.classList.toggle('collapsed');
    nowCollapsed?collapsed.add(key):collapsed.delete(key);
    localStorage.setItem('tb_collapsed', JSON.stringify([...collapsed]));
  }
});
// ---- 拖拽：抓手柄=挪整个分类模块；抓条目=改归类 ----
grid.addEventListener('dragstart',e=>{
  if(e.target.classList.contains('grip')){          // 手柄 → 重排分类
    const sec=e.target.closest('section.cat');
    dragMode='section'; dragKey=sec.dataset.key; dragging=true;
    sec.classList.add('drag');
    e.dataTransfer.effectAllowed='move';
    e.dataTransfer.setData('text/plain','section:'+dragKey);
    try{e.dataTransfer.setDragImage(sec,24,18);}catch(_){}
    return;
  }
  const li=e.target.closest('li'); if(!li) return;  // 条目 → 改归类
  dragMode='tab'; dragging=true; li.classList.add('drag');
  e.dataTransfer.effectAllowed='move';
  e.dataTransfer.setData('text/plain', li.dataset.url);
});
grid.addEventListener('dragend',()=>{
  dragging=false; dragMode=null; dragKey=null;
  document.querySelectorAll('.drag').forEach(x=>x.classList.remove('drag'));
  document.querySelectorAll('.dropping').forEach(x=>x.classList.remove('dropping'));
  clearInsert();
});
grid.addEventListener('dragover',e=>{
  const sec=e.target.closest('section.cat'); if(!sec) return;
  e.preventDefault(); e.dataTransfer.dropEffect='move';
  if(dragMode==='section'){                           // 重排：画一根插入线（上/下）
    clearInsert();
    if(sec.dataset.key===dragKey) return;             // 自己身上不画
    sec.classList.add(dropAfter(sec,e)?'insert-after':'insert-before');
  }else{
    sec.classList.add('dropping');                    // 改归类：高亮目标分类
  }
});
grid.addEventListener('dragleave',e=>{
  const sec=e.target.closest('section.cat');
  if(sec && !sec.contains(e.relatedTarget)){
    sec.classList.remove('dropping','insert-before','insert-after');
  }
});
grid.addEventListener('drop',async e=>{
  const sec=e.target.closest('section.cat'); if(!sec) return;
  e.preventDefault();
  if(dragMode==='section'){                          // 重排分类顺序
    const dropKey=sec.dataset.key, after=dropAfter(sec,e);
    clearInsert();
    if(dragKey && dropKey && dragKey!==dropKey) reorder(dragKey, dropKey, after);
    dragging=false; dragMode=null; return;
  }
  sec.classList.remove('dropping');
  const url=e.dataTransfer.getData('text/plain'), cat=sec.dataset.key;  // 改归类
  dragging=false;
  await api("/api/recategorize",{url,cat});
  lastSig=""; poll();
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
