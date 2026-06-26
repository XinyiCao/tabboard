#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carrie's Tab Board — 动态 Chrome tab 看板
读取当前 Chrome 实时 tab -> 关键词自动归类 -> 网页里分组展示，自动刷新。
支持：点标题跳到真实 tab、关单个、一键去重、整组关闭。
运行： python3 tab_board.py   然后浏览器打开 http://localhost:8765
"""
import json, re, subprocess, threading, webbrowser, time, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from pathlib import Path

# ========== 演示模式（python3 tab_board.py --demo）==========
# 用预置假数据展示全部功能，不读取真实 Chrome、不读写真实数据文件。适合录 demo / 别人试用。
DEMO = "--demo" in sys.argv

DEMO_CATS = [
    ("meetings",   "🗓️", "Meetings & Notes"),
    ("docs",       "📄", "Docs & Specs"),
    ("dev",        "💻", "Dev & Code"),
    ("dashboards", "📊", "Dashboards"),
    ("reading",    "📚", "Reading List"),
    ("personal",   "🎧", "Personal"),
    ("other",      "📦", "Uncategorized"),
]
DEMO_RULES = [
    ("meetings",   ["1:1", "meeting", "standup", "sync", "notes", "okr", "retro"]),
    ("docs",       ["spec", "prd", "roadmap", "proposal", "doc", "plan", "brief"]),
    ("dev",        ["github", "pull request", "stack overflow", "localhost", "api", "deploy", "ci/cd"]),
    ("dashboards", ["dashboard", "analytics", "metrics", "grafana", "report"]),
    ("reading",    ["blog", "article", "guide", "tutorial", "medium", "newsletter"]),
    ("personal",   ["youtube", "spotify", "gmail", "calendar", "amazon", "lo-fi"]),
]
DEMO_HOST_RULES = [
    ("dev", ["github.com", "stackoverflow.com"]),
    ("personal", ["youtube.com", "mail.google.com", "spotify.com", "amazon.com"]),
]
DEMO_TABS = [
    {"wid":"1","tid":"d01","title":"Alex / Jordan — 1:1 Notes","url":"https://notes.example.com/1on1-alex"},
    {"wid":"1","tid":"d02","title":"Q3 Planning — Meeting Notes","url":"https://notes.example.com/q3-planning"},
    {"wid":"1","tid":"d03","title":"Engineering Weekly Sync","url":"https://notes.example.com/eng-sync"},
    {"wid":"1","tid":"d04","title":"Sprint Retro — Action Items","url":"https://notes.example.com/retro"},
    {"wid":"1","tid":"d05","title":"PRD: Checkout Redesign","url":"https://docs.example.com/prd-checkout"},
    {"wid":"1","tid":"d06","title":"API Spec v2 — Authentication","url":"https://docs.example.com/api-spec"},
    {"wid":"1","tid":"d07","title":"2025 Product Roadmap","url":"https://docs.example.com/roadmap"},
    {"wid":"1","tid":"d08","title":"Design Proposal — Onboarding Flow","url":"https://docs.example.com/proposal-onboarding"},
    {"wid":"1","tid":"d09","title":"acme/web · Pull Request #482","url":"https://github.com/acme/web/pull/482"},
    {"wid":"1","tid":"d10","title":"async/await best practices — Stack Overflow","url":"https://stackoverflow.com/q/123456"},
    {"wid":"1","tid":"d11","title":"localhost:3000 — Local Dev","url":"http://localhost:3000/"},
    {"wid":"1","tid":"d12","title":"CI/CD Deploy Pipeline","url":"https://ci.example.com/pipeline"},
    {"wid":"1","tid":"d13","title":"Product Analytics Dashboard","url":"https://analytics.example.com/overview"},
    {"wid":"1","tid":"d14","title":"Grafana — Service Metrics","url":"https://grafana.example.com/d/abc"},
    {"wid":"1","tid":"d15","title":"The Pragmatic Engineer — Blog","url":"https://blog.example.com/pragmatic"},
    {"wid":"1","tid":"d16","title":"Rust Ownership — Tutorial","url":"https://guide.example.com/rust-ownership"},
    {"wid":"1","tid":"d17","title":"Lo-fi beats to code to — YouTube","url":"https://www.youtube.com/watch?v=demo"},
    {"wid":"1","tid":"d18","title":"Gmail — Inbox","url":"https://mail.google.com/"},
    {"wid":"1","tid":"d19","title":"Amazon — Order History","url":"https://www.amazon.com/orders"},
]
def demo_library_seed():
    now = int(time.time())
    return {
        "https://docs.example.com/roadmap": {"url":"https://docs.example.com/roadmap","title":"2025 Product Roadmap","ts":now-60*86400,"last_opened":now-45*86400},  # 尘封（>30天）
        "https://blog.example.com/pragmatic": {"url":"https://blog.example.com/pragmatic","title":"The Pragmatic Engineer — Blog","ts":now-5*86400,"last_opened":now-2*86400},
    }

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
if DEMO: CATS = DEMO_CATS

# ---------- 自定义分类（用户新建，参与分组，持久化）----------
CATEGORIES_FILE = Path.home() / ".tabboard_categories.json"

def load_categories():
    if DEMO: return []
    try:
        return json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_categories(d):
    if DEMO: return
    try:
        backup_file(CATEGORIES_FILE)
        CATEGORIES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存自定义分类失败:", e)

CUSTOM_CATS = load_categories()   # [{"key","emoji","name"}, ...]

def all_cats():
    """内置分类 + 自定义分类（自定义插在「其他」之前）。"""
    rest = [c for c in CATS if c[0] != "other"]
    other = [c for c in CATS if c[0] == "other"]
    custom = [(c["key"], c.get("emoji", "🏷️"), c["name"]) for c in CUSTOM_CATS]
    return rest + custom + other

def rebuild_meta():
    global CAT_META, VALID_KEYS
    CAT_META = {k: (e, n) for k, e, n in all_cats()}
    VALID_KEYS = set(CAT_META)

rebuild_meta()

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
if DEMO:
    RULES = DEMO_RULES
    HOST_RULES = DEMO_HOST_RULES

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
    if DEMO: return {}
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_overrides(d):
    if DEMO: return
    try:
        backup_file(OVERRIDES_FILE)
        OVERRIDES_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存分类覆盖失败:", e)

OVERRIDES = load_overrides()   # {url: cat_key}

# ---------- 暂存区（park：关掉真实 tab 但记住，可一键找回）----------
PARKED_FILE = Path.home() / ".tabboard_parked.json"

def load_parked():
    if DEMO: return {}
    try:
        return json.loads(PARKED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_parked(d):
    if DEMO: return
    try:
        backup_file(PARKED_FILE)
        PARKED_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存暂存区失败:", e)

PARKED = load_parked()   # {url: {url, title, ts}}

# ---------- 收藏夹（library：永久留存，打开不消失，可视化分类）----------
LIBRARY_FILE = Path.home() / ".tabboard_library.json"

def load_library():
    if DEMO: return demo_library_seed()
    try:
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_library(d):
    if DEMO: return
    try:
        backup_file(LIBRARY_FILE)
        LIBRARY_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("⚠️ 保存收藏夹失败:", e)

LIBRARY = load_library()   # {url: {url, title, ts}}

def categorize(title, url):
    if url in OVERRIDES and OVERRIDES[url] in VALID_KEYS:   # 手动拖过的优先（且该分类仍存在）
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
    if DEMO: return list(DEMO_TABS)
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

def group_by_cat(items, include_empty=()):
    custom_keys = {c["key"] for c in CUSTOM_CATS}
    out = []
    for key, emoji, name in all_cats():
        g = [it for it in items if categorize(it["title"], it["url"]) == key]
        if g or key in include_empty:               # 空的自定义分类也显示（可作拖放目标）
            out.append({"key": key, "emoji": emoji, "name": name, "tabs": g,
                        "custom": key in custom_keys})
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
    custom_keys = {c["key"] for c in CUSTOM_CATS}
    return {"cats": group_by_cat(tabs, include_empty=custom_keys), "total": len(tabs),
            "parked": parked_list(),
            "library": group_by_cat(libvals)}

def activate(wid, tid):
    if DEMO: return                       # 演示模式：点标题不跳转真实 tab
    osa(f'''tell application "Google Chrome"
      set theWin to window id {wid}
      set i to 0
      repeat with t in tabs of theWin
        set i to i + 1
        if (id of t as string) is "{tid}" then
          set active tab index of theWin to i
          set index of theWin to 1
          exit repeat
        end if
      end repeat
      activate
    end tell''')

def close_tab(wid, tid):
    if DEMO:
        global DEMO_TABS
        DEMO_TABS = [t for t in DEMO_TABS if t["tid"] != tid]
        return
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
    if DEMO:
        for t in DEMO_TABS:
            if t["tid"] == tid: return t["title"], t["url"]
        return None, None
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
    if DEMO:                               # 演示模式：把它当作“重新打开”，加回实时列表
        global DEMO_TABS
        if not any(t["url"] == url for t in DEMO_TABS):
            title = (LIBRARY.get(url) or {}).get("title", url)
            DEMO_TABS = DEMO_TABS + [{"wid":"1","tid":"o"+str(int(time.time()*1000)%1000000),"title":title,"url":url}]
        return
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

def add_cat(name, emoji="🏷️"):
    name = (name or "").strip() or "新分类"
    key = "c%d" % int(time.time() * 1000)
    while key in VALID_KEYS:
        key += "x"
    CUSTOM_CATS.append({"key": key, "emoji": emoji or "🏷️", "name": name})
    save_categories(CUSTOM_CATS)
    rebuild_meta()
    return key

def del_cat(key):
    global CUSTOM_CATS
    if not any(c["key"] == key for c in CUSTOM_CATS):
        return
    CUSTOM_CATS = [c for c in CUSTOM_CATS if c["key"] != key]
    save_categories(CUSTOM_CATS)
    gone = [u for u, k in OVERRIDES.items() if k == key]   # 该分类下的 tab 回到自动分类
    for u in gone:
        OVERRIDES.pop(u, None)
    if gone:
        save_overrides(OVERRIDES)
    rebuild_meta()

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
            self._send(PAGE.replace("__PORT__", str(PORT)).replace("__DEMOBADGE__", '<span class="demobadge">🎬 DEMO</span>' if DEMO else ""), "text/html")

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
        elif p == "/api/addcat":  return self._send(json.dumps({"key": add_cat(data.get("name"), data.get("emoji", "🏷️"))}))
        elif p == "/api/delcat":  del_cat(data.get("cat"))                 # 删除自定义分类
        self._send(json.dumps({"ok": True}))

PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>📑 Carrie's Tab Board · live</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1d212b;--line:#2a2f3a;--txt:#e6e8ee;--muted:#8b93a7;--accent:#7aa2ff;--warn:#ffb454;--headerbg:rgba(15,17,21,.92)}
:root[data-theme="light"]{--bg:#f4f5f7;--panel:#ffffff;--panel2:#eceff3;--line:#dce1e8;--txt:#1b2130;--muted:#6a7585;--accent:#2f6bff;--warn:#c1761a;--headerbg:rgba(244,245,247,.92)}
:root[data-theme="everforest-light"]{--bg:#fdf6e3;--panel:#f4f0d9;--panel2:#efebd4;--line:#e0dcc7;--txt:#5c6a72;--muted:#939f91;--accent:#3a94c5;--warn:#f57d26;--headerbg:rgba(253,246,227,.92)}
:root[data-theme="iceberg-light"]{--bg:#e8e9ec;--panel:#f3f4f6;--panel2:#dcdfe7;--line:#cad0de;--txt:#33374c;--muted:#8389a3;--accent:#2d539e;--warn:#c57339;--headerbg:rgba(232,233,236,.92)}
:root[data-theme="solarized-light"]{--bg:#fdf6e3;--panel:#eee8d5;--panel2:#e7e0c9;--line:#ddd6be;--txt:#586e75;--muted:#93a1a1;--accent:#268bd2;--warn:#cb4b16;--headerbg:rgba(253,246,227,.92)}
:root[data-theme="tokyonight-day"]{--bg:#e1e2e7;--panel:#eaeaee;--panel2:#d6d8e0;--line:#c4c8da;--txt:#343b58;--muted:#848cb5;--accent:#2e7de9;--warn:#b15c00;--headerbg:rgba(225,226,231,.92)}
:root[data-theme="zenwritten-light"]{--bg:#eeeeee;--panel:#f6f6f6;--panel2:#e2e2e2;--line:#d2d2d2;--txt:#353535;--muted:#8a8784;--accent:#286486;--warn:#803d1c;--headerbg:rgba(238,238,238,.92)}
:root[data-theme="ayu-dark"]{--bg:#0b0e14;--panel:#0d1017;--panel2:#131721;--line:#1f2430;--txt:#bfbdb6;--muted:#565b66;--accent:#59c2ff;--warn:#ffb454;--headerbg:rgba(11,14,20,.92)}
:root[data-theme="github-dark"]{--bg:#0d1117;--panel:#161b22;--panel2:#1c2128;--line:#30363d;--txt:#c9d1d9;--muted:#8b949e;--accent:#58a6ff;--warn:#d29922;--headerbg:rgba(13,17,23,.92)}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"PingFang SC","Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt);font-size:14px;line-height:1.45}
header{position:sticky;top:0;z-index:20;background:var(--headerbg);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:600}
.stat{color:var(--muted);font-size:12.5px}
.demobadge{font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--warn);border:1px solid var(--warn);border-radius:999px;padding:1px 8px;align-self:center}
.dot{width:7px;height:7px;border-radius:50%;background:#3ddc84;display:inline-block;margin-right:5px;animation:p 2s infinite}
@keyframes p{50%{opacity:.3}}
.controls{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.searchbar{background:var(--panel2);border:1px solid var(--line);border-radius:8px;color:var(--txt);padding:7px 11px;width:200px;outline:none;cursor:text}
.searchbar:hover,.searchbar:focus{border-color:var(--accent)}
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
.cat-head .delcat{font-size:12px;color:var(--muted);cursor:pointer;border:1px solid var(--line);border-radius:6px;padding:3px 7px}
.cat-head .delcat:hover{color:var(--warn);border-color:var(--warn)}
li.hint{justify-content:center;color:var(--muted);font-size:12px;cursor:default;padding:14px 8px;border:1px dashed var(--line);border-radius:8px;margin:2px}
li.hint:hover{background:none}
section.cat.custom .cat-head{background:linear-gradient(180deg,rgba(122,162,255,.14),transparent)}
.cat-body{display:grid;grid-template-rows:1fr;transition:grid-template-rows .28s cubic-bezier(.4,0,.2,1),opacity .2s ease}
.cat.collapsed .cat-body{grid-template-rows:0fr;opacity:.4}
ul{list-style:none;margin:0;padding:6px;overflow:hidden;min-height:0}
li{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:8px;cursor:grab}
li:hover{background:var(--panel2)}
li:active{cursor:grabbing}
li.drag{opacity:.4}
section.cat.dropping{outline:2px dashed var(--accent);outline-offset:-3px;background:rgba(122,162,255,.12)}
.grip{color:var(--muted);cursor:grab;font-size:14px;flex:none;line-height:1;letter-spacing:-3px;padding-right:2px}
.grip:active{cursor:grabbing}
.cat-head:hover .grip{color:var(--txt)}
section.cat.drag{opacity:.45}
.cat.collapsed .cat-head{border-radius:13px}
/* 重排插入线：显示模块将落在目标的上方或下方 */
.cat.insert-before::before,.cat.insert-after::after{content:"";position:absolute;left:0;right:0;height:3px;border-radius:3px;background:var(--accent);box-shadow:0 0 7px var(--accent);z-index:5}
.cat.insert-before::before{top:-10px}
.cat.insert-after::after{bottom:-10px}
li img{width:16px;height:16px;border-radius:3px;flex:none;background:var(--panel2)}
li .title,li .ptitle,li .ltitle{color:var(--txt);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
li .title:hover,li .ptitle:hover,li .ltitle:hover{color:var(--accent)}
li .age{flex:none;color:var(--muted);font-size:11px}
li .x,li .park,li .del,li .star,li .pstar,li .libdel{flex:none;color:var(--muted);cursor:pointer;border-radius:5px;padding:0 6px;font-size:13px;opacity:0;visibility:hidden;filter:grayscale(1)}
li:hover .x,li:hover .park,li:hover .del,li:hover .star,li:hover .pstar,li:hover .libdel{opacity:1;visibility:visible}
li .x:hover,li .del:hover,li .libdel:hover{color:var(--warn);background:rgba(255,180,84,.16);filter:none}
li .park:hover{color:#1aa85a;background:rgba(61,220,132,.16)}
li .star:hover,li .pstar:hover{background:rgba(255,200,61,.2);filter:none}
li .star.saved{opacity:1;visibility:visible;filter:none;color:#ffc83d}
li .age.stale{color:var(--warn)}
#notice{padding:0 22px}
.review{background:rgba(255,180,84,.12);border:1px solid rgba(255,180,84,.5);border-radius:10px;padding:10px 14px;margin:12px 0 0;display:flex;align-items:center;gap:10px;color:var(--warn);font-size:13px}
.review b{color:var(--warn);font-weight:700}
.review .btn{padding:5px 10px;font-size:12.5px}
.rename{font:inherit;font-weight:600;background:var(--bg);border:1px solid var(--accent);border-radius:5px;color:var(--txt);padding:1px 6px;width:170px;outline:none}
section.cat[data-key="__parked__"]{border-color:rgba(255,180,84,.5)}
section.cat[data-key="__parked__"] .cat-head{background:linear-gradient(180deg,rgba(255,180,84,.14),transparent)}
.empty{color:var(--muted);text-align:center;padding:80px 0}
.themewrap{position:relative}
.menu{position:absolute;right:0;top:calc(100% + 8px);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px;box-shadow:0 14px 44px rgba(0,0,0,.4);min-width:200px;z-index:40}
.menu-hidden{display:none}
.menu-item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--txt)}
.menu-item:hover{background:var(--panel2)}
.menu-item.active{background:var(--panel2)}
.menu-item.active::after{content:"✓";margin-left:auto;color:var(--accent);font-weight:700}
.sw{width:18px;height:18px;border-radius:6px;flex:none;border:1px solid var(--line)}
#spot{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;align-items:center;padding-top:13vh;background:rgba(0,0,0,.32);backdrop-filter:blur(2px);animation:spotin .12s ease}
#spot.spot-hidden{display:none}
@keyframes spotin{from{opacity:0}to{opacity:1}}
#spotInput{width:min(580px,88vw);font-size:19px;padding:15px 20px;border-radius:14px;border:1px solid var(--accent);background:var(--panel);color:var(--txt);outline:none;box-shadow:0 18px 55px rgba(0,0,0,.45)}
.spot-hint{margin-top:11px;color:#e9ecf3;font-size:12.5px;opacity:.9;text-shadow:0 1px 4px rgba(0,0,0,.6)}
/* 🍀 彩蛋：平时隐藏，鼠标移到右下角才浮现 */
#lucky{position:fixed;right:18px;bottom:16px;z-index:60;width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:22px;cursor:pointer;background:var(--panel);border:1px solid var(--line);box-shadow:0 6px 20px rgba(0,0,0,.35);opacity:0;transform:translateY(12px) scale(.7);pointer-events:none;transition:opacity .25s ease,transform .25s ease}
#lucky.show{opacity:.93;transform:none;pointer-events:auto}
#lucky:hover{opacity:1;transform:scale(1.13)}
#lucky.spin{animation:luckyspin .7s cubic-bezier(.3,1.4,.5,1)}
@keyframes luckyspin{to{transform:rotate(360deg)}}
#luckyBox{position:fixed;right:18px;bottom:66px;z-index:60;display:flex;flex-direction:column;align-items:flex-end;gap:8px;pointer-events:none}
#luckyTag{font-size:12px;font-weight:600;letter-spacing:.02em;color:var(--accent);text-shadow:0 1px 8px rgba(0,0,0,.3);opacity:0;transform:translateY(6px);transition:opacity .22s ease,transform .22s ease}
#luckyTag.show{opacity:1;transform:none}
#luckyToast{max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:var(--panel);border:1px solid var(--line);color:var(--txt);padding:6px 12px;border-radius:999px;font-size:12px;box-shadow:0 6px 22px rgba(0,0,0,.16);opacity:0;transform:translateY(6px);transition:opacity .22s ease,transform .22s ease}
#luckyToast.show{opacity:1;transform:none}
.confetti{position:fixed;z-index:59;width:7px;height:7px;border-radius:50%;pointer-events:none}
footer{color:var(--muted);font-size:11.5px;padding:0 22px 40px;text-align:center}
.hints{line-height:1.6}
.tagline{margin-top:13px;font-size:11px;font-weight:400;color:var(--muted);opacity:.7;letter-spacing:.1em}
.copyright{margin-top:5px;font-size:11px;opacity:.55;letter-spacing:.03em}
.copyright a{color:inherit;text-decoration:none;border-bottom:1px solid transparent;transition:color .15s,border-color .15s}
.copyright a:hover{color:var(--accent);border-bottom-color:var(--accent)}
</style></head><body>
<script>document.documentElement.dataset.theme=localStorage.getItem('tb_theme')||'dark';</script>
<header>
  <h1>📑 Tab Board</h1>__DEMOBADGE__
  <span class="stat"><span class="dot"></span><span id="stat">读取中…</span></span>
  <div class="controls">
    <input class="searchbar" id="searchBar" placeholder="🔍 搜索标题…" readonly>
    <button class="btn" id="addcat">＋ 新建分类</button>
    <button class="btn" id="libBtn">📚 收藏夹</button>
    <button class="btn" id="dedup">🧹 一键去重</button>
    <button class="btn" id="refresh">↻ 刷新</button>
    <div class="themewrap">
      <button class="btn" id="themeBtn" title="主题配色">🎨 主题</button>
      <div id="themeMenu" class="menu menu-hidden">
        <div class="menu-item" data-theme="dark"><span class="sw" style="background:linear-gradient(135deg,#171a21 0 50%,#7aa2ff 50%)"></span>默认暗</div>
        <div class="menu-item" data-theme="light"><span class="sw" style="background:linear-gradient(135deg,#ffffff 0 50%,#2f6bff 50%)"></span>默认亮</div>
        <div class="menu-item" data-theme="github-dark"><span class="sw" style="background:linear-gradient(135deg,#161b22 0 50%,#58a6ff 50%)"></span>GitHub Dark</div>
        <div class="menu-item" data-theme="ayu-dark"><span class="sw" style="background:linear-gradient(135deg,#0d1017 0 50%,#59c2ff 50%)"></span>Ayu Dark</div>
        <div class="menu-item" data-theme="everforest-light"><span class="sw" style="background:linear-gradient(135deg,#f4f0d9 0 50%,#3a94c5 50%)"></span>Everforest Light</div>
        <div class="menu-item" data-theme="iceberg-light"><span class="sw" style="background:linear-gradient(135deg,#f3f4f6 0 50%,#2d539e 50%)"></span>Iceberg Light</div>
        <div class="menu-item" data-theme="solarized-light"><span class="sw" style="background:linear-gradient(135deg,#eee8d5 0 50%,#268bd2 50%)"></span>Solarized Light</div>
        <div class="menu-item" data-theme="tokyonight-day"><span class="sw" style="background:linear-gradient(135deg,#eaeaee 0 50%,#2e7de9 50%)"></span>Tokyo Night Day</div>
        <div class="menu-item" data-theme="zenwritten-light"><span class="sw" style="background:linear-gradient(135deg,#f6f6f6 0 50%,#286486 50%)"></span>Zenwritten Light</div>
      </div>
    </div>
  </div>
</header>
<div id="spot" class="spot-hidden">
  <input id="spotInput" placeholder="🔍 搜索标题…" autocomplete="off">
  <div class="spot-hint">实时过滤 · Enter 打开第一个结果 · Esc 关闭</div>
</div>
<div id="notice"></div>
<div class="grid" id="grid"></div>
<div id="luckyBox">
  <div id="luckyTag">I'm Feeling Lucky!</div>
  <div id="luckyToast"></div>
</div>
<div id="lucky" title="I'm Feeling Lucky">🍀</div>
<footer>
  <div class="hints">⭐ 收藏 · 📥 关闭但暂存 · ✕ 关闭 · 单击标题栏折叠 · 双击标题栏重命名 · 「📚 收藏夹」切换视图</div>
  <div class="tagline">All-In-One Tab Board</div>
  <div class="copyright">Tab Board v0.3.1 · © 2026 carrie.cao · <a href="https://github.com/XinyiCao/tabboard" target="_blank" rel="noopener">GitHub</a> · MIT License</div>
</footer>
<script>
const grid=document.getElementById('grid'), stat=document.getElementById('stat');
const spot=document.getElementById('spot'), spotInput=document.getElementById('spotInput');
window.addEventListener('error',e=>{if(stat)stat.textContent="⚠️ JS错误: "+(e.message||e.error);});
const libBtn=document.getElementById('libBtn'), noticeEl=document.getElementById('notice');
libBtn.onclick=()=>{mode=(mode==='lib'?'live':'lib');filter='';spotInput.value='';staleOnly=false;reviewDismissed=false;if(lastData)render(lastData);};
noticeEl.addEventListener('click',e=>{
  const now=String(Math.floor(Date.now()/1000));
  if(e.target.classList.contains('rv-show')){staleOnly=true;reviewDismissed=true;localStorage.setItem('tb_lastreview',now);render(lastData);}
  else if(e.target.classList.contains('rv-ok')){reviewDismissed=true;localStorage.setItem('tb_lastreview',now);render(lastData);}
  else if(e.target.classList.contains('rv-all')){staleOnly=false;render(lastData);}
});
let lastSig="", filter="", dragging=false, dragMode=null, dragKey=null, staleOnly=false, reviewDismissed=false, editing=false, headerClickTimer=null;
const collapsed=new Set(JSON.parse(localStorage.getItem('tb_collapsed')||'[]'));
const names=JSON.parse(localStorage.getItem('tb_names')||'{}');   // 自定义分类名 {key: name}

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
  const dispName=names[c.key]||c.name;
  const tail=plain?'':`<span class="closeg" data-cat="${c.key}">关闭整组 ✕</span>`
      +(c.custom?`<span class="delcat" data-cat="${c.key}" title="删除此自定义分类（里面的 tab 回到自动分类）">🗑️</span>`:'');
  const itemFn=c.parked?parkedItem:(c.lib?libItem:liveItem);
  const items=c.tabs.length ? c.tabs.map(itemFn).join("")
      : (c.custom?`<li class="hint">把 tab 拖到这里归入「${esc(dispName)}」</li>`:'');
  return `
    <section class="cat ${collapsed.has(c.key)?'collapsed':''}${c.custom?' custom':''}" data-key="${c.key}">
      <div class="cat-head">${grip}<span class="caret">▾</span><span class="emoji">${c.emoji}</span><span class="name" title="双击重命名">${esc(dispName)}</span>
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
  if(dragging||editing) return;   // 拖动/重命名中不重绘，免得打断
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
  else if(t.classList.contains('delcat')){
    if(confirm("删除这个自定义分类？里面的 tab 会回到自动分类（tab 本身不受影响）。")){await api("/api/delcat",{cat:t.dataset.cat});lastSig="";poll();}
  }
  else if(t.closest('.cat-head') && !t.classList.contains('grip') && !t.classList.contains('rename')){   // 点标题栏：折叠/展开（手柄/重命名框除外）
    const sec=t.closest('section.cat'), key=sec.dataset.key;
    const toggle=()=>{const nc=sec.classList.toggle('collapsed');nc?collapsed.add(key):collapsed.delete(key);localStorage.setItem('tb_collapsed',JSON.stringify([...collapsed]));};
    if(t.classList.contains('name')){clearTimeout(headerClickTimer);headerClickTimer=setTimeout(toggle,220);} // 点分类名：延迟，给双击重命名留空档
    else toggle();                                                                                            // 点三角/其它：即时折叠
  }
});
// ---- 双击分类名 = 重命名 ----
grid.addEventListener('dblclick',e=>{
  const nameEl=e.target.closest('.name'); if(!nameEl) return;
  e.preventDefault(); clearTimeout(headerClickTimer);
  startRename(nameEl);
});
function startRename(nameEl){
  const key=nameEl.closest('section.cat').dataset.key;
  editing=true;
  const inp=document.createElement('input');
  inp.className='rename'; inp.value=nameEl.textContent;
  nameEl.replaceWith(inp); inp.focus(); inp.select();
  const done=save=>{
    if(inp._done) return; inp._done=true;
    if(save){const v=inp.value.trim(); if(v) names[key]=v; else delete names[key]; localStorage.setItem('tb_names',JSON.stringify(names));} // 空=恢复默认名
    editing=false; render(lastData);
  };
  inp.addEventListener('keydown',e=>{e.stopPropagation(); if(e.key==='Enter')done(true); else if(e.key==='Escape')done(false);});
  inp.addEventListener('blur',()=>done(true));
  inp.addEventListener('click',e=>e.stopPropagation());
  inp.addEventListener('mousedown',e=>e.stopPropagation());
}
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

const themeBtn=document.getElementById('themeBtn'), themeMenu=document.getElementById('themeMenu');
function markTheme(){const cur=document.documentElement.dataset.theme||'dark';themeMenu.querySelectorAll('.menu-item').forEach(it=>it.classList.toggle('active',it.dataset.theme===cur));}
themeBtn.onclick=e=>{e.stopPropagation();themeMenu.classList.toggle('menu-hidden');markTheme();};
themeMenu.addEventListener('click',e=>{const it=e.target.closest('.menu-item');if(!it)return;const t=it.dataset.theme;document.documentElement.dataset.theme=t;localStorage.setItem('tb_theme',t);markTheme();themeMenu.classList.add('menu-hidden');});
document.addEventListener('click',()=>themeMenu.classList.add('menu-hidden'));   // 点别处关闭菜单
document.getElementById('addcat').onclick=async()=>{
  const name=prompt("新建分类名称（例如 Arena）：");
  if(!name||!name.trim()) return;
  await api("/api/addcat",{name:name.trim()});
  mode='live'; lastSig=""; poll();      // 切回实时视图，方便把 tab 拖进去
};
document.getElementById('dedup').onclick=async()=>{const r=await api("/api/dedup");alert(`已关闭 ${r.removed} 个重复 tab`);lastSig="";poll();};
document.getElementById('refresh').onclick=()=>{lastSig="";poll();};
// ---- 居中聚焦搜索（Spotlight 风格）----
function openSpot(){spot.classList.remove('spot-hidden');spotInput.value=filter;spotInput.focus();spotInput.select();}
function closeSpot(){spot.classList.add('spot-hidden');filter='';applyFilter();}
const searchBar=document.getElementById('searchBar');
searchBar.addEventListener('click',openSpot);
searchBar.addEventListener('focus',openSpot);
spotInput.addEventListener('input',e=>{filter=e.target.value;applyFilter();});
spotInput.addEventListener('keydown',e=>{
  if(e.key==='Escape'){closeSpot();}
  else if(e.key==='Enter'){
    const el=[...document.querySelectorAll('.title,.ltitle,.ptitle')].find(x=>x.closest('li').style.display!=='none');
    if(el) el.click();           // 打开第一个匹配结果（复用点击逻辑）
    closeSpot();
  }
});
spot.addEventListener('click',e=>{if(e.target===spot) closeSpot();});   // 点空白处关闭
document.addEventListener('keydown',e=>{                                 // 「/」快捷键唤起
  if(e.key==='/' && spot.classList.contains('spot-hidden') && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)){
    e.preventDefault(); openSpot();
  }
});
// ---- 🍀 I'm Feeling Lucky 彩蛋 ----
const lucky=document.getElementById('lucky'), luckyToast=document.getElementById('luckyToast'), luckyTag=document.getElementById('luckyTag');
let luckyShown=false, luckyCurrent=null;
function luckyReveal(){
  lucky.classList.add('show');
  lucky.animate([{transform:'rotate(0)'},{transform:'rotate(360deg)'}],{duration:700,easing:'cubic-bezier(.3,1.4,.5,1)'}); // 浮现即旋转
  confetti();
  luckyCurrent=luckyPick();                                             // 移过去就抽定一个
  if(luckyCurrent){
    luckyTag.classList.add('show');                                    // 无框漂浮的一句话
    luckyToast.textContent=luckyCurrent.t.title;                       // 框里只放标题
    luckyToast.classList.add('show');
    clearTimeout(toastT); toastT=setTimeout(()=>{luckyTag.classList.remove('show');luckyToast.classList.remove('show');},4000);
  }
}
window.addEventListener('mousemove',e=>{
  const x=e.clientX, y=e.clientY;
  if(!luckyShown){ if(x>innerWidth-120 && y>innerHeight-120){luckyShown=true; luckyReveal();} }      // 进右下角 → 浮现+旋转+彩屑+提示
  else if(x<innerWidth-160 || y<innerHeight-160){ luckyShown=false; luckyCurrent=null; lucky.classList.remove('show'); luckyToast.classList.remove('show'); luckyTag.classList.remove('show'); } // 移开 → 全部消失
});
let toastT;
function toast(msg){luckyToast.textContent=msg;luckyToast.classList.add('show');clearTimeout(toastT);toastT=setTimeout(()=>luckyToast.classList.remove('show'),3800);}
function confetti(){
  const colors=['var(--accent)','#3ddc84','var(--warn)'];
  for(let i=0;i<18;i++){
    const s=document.createElement('span');
    s.className='confetti';
    s.style.background=colors[i%colors.length];
    s.style.right='39px'; s.style.bottom='37px';            // 四叶草中心
    const sz=4+Math.random()*4; s.style.width=s.style.height=sz+'px';
    const ang=Math.random()*Math.PI*2, dist=30+Math.random()*64;  // 随机方向、随机距离 → 向四周扩散
    const dx=Math.cos(ang)*dist, dy=Math.sin(ang)*dist;
    document.body.appendChild(s);
    const a=s.animate(
      [{transform:'translate(0,0) scale(1)',opacity:1},
       {transform:`translate(${dx}px,${-dy}px) scale(.25)`,opacity:0}],
      {duration:720+Math.random()*260,easing:'cubic-bezier(.15,.7,.3,1)',fill:'forwards'}
    );
    a.onfinish=()=>s.remove();      // 动画一结束立即移除，避免回弹到中心残留
  }
}
function luckyPick(){
  if(!lastData) return null;
  const lib=(lastData.library||[]).flatMap(c=>c.tabs);
  const stale=lib.filter(t=>t.stale);
  if(stale.length) return {kind:'lib', t:stale[Math.floor(Math.random()*stale.length)], why:'尘封已久的收藏'};   // 优先翻出忘掉的
  if(lib.length)   return {kind:'lib', t:lib[Math.floor(Math.random()*lib.length)],     why:'收藏夹'};
  const live=(lastData.cats||[]).flatMap(c=>c.tabs);
  if(live.length)  return {kind:'live', t:live[Math.floor(Math.random()*live.length)],  why:'当前 tab'};
  return null;
}
lucky.onclick=()=>{
  const p=luckyCurrent;                          // 用 hover 时已抽定的那一个
  if(!p){toast('🍀 还没东西可抽 —— 先收藏点什么？');return;}
  setTimeout(async()=>{                           // 点击才跳转
    if(p.kind==='lib') await api('/api/libopen',{url:p.t.url});
    else await api('/api/activate',{wid:p.t.wid,tid:p.t.tid});
    lastSig=''; poll();
  }, 250);
};
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
    if DEMO:
        print("🎬 演示模式：使用预置假数据，不读取真实 Chrome，也不读写真实数据文件。")
    print(f"✅ Tab Board 运行中 → {url}  (Ctrl+C 停止)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
