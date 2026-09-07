#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI风月 (ai-xan.xyz) 聊天记录批量导出工具
=======================================
把「历史游玩记录」里所有应用的「会话列表」聊天记录全部导出。

原理（完全复用网站自身的接口）:
  1. 登录        POST /console/api/login            -> JWT (localStorage "console_token")
  2. 历史应用列表 GET  /console/api/used-installed-apps   (侧边栏「历史游玩记录」按钮的数据源)
  3. 会话列表     GET  /console/api/installed-apps/{appId}/conversations   (页面「会话列表」: 已置顶/对话列表 两个 tab 合并去重)
  4. 单会话导出   GET  /console/api/installed-apps/{appId}/messages/export?conversation_id=xxx
                 （等价于页面右上角菜单「导出记录」按钮；导出的 txt 格式与网站完全一致:
                   [USER]:/ [AI]: 前缀 + #----# 分隔问答 + #====# 分隔轮次 + UTF-8 BOM）

防封号说明（默认开启）:
  每次实际发出网络请求（登录 / 抓取应用列表 / 抓取会话列表 / 导出下载等）之前，
  都会先随机等待 10~30 秒 —— 所有线程共享同一个全局限速器排队放行，
  任意两次请求之间至少间隔约 10 秒，模拟真人操作节奏，尽量避免高频访问触发风控封号。
  可用 --sleep-min / --sleep-max 调整等待范围；两个都设为 0 可关闭等待（恢复旧版速度）。

图形界面 / 进度 / 历史记录（新增功能）:
  GUI:  py -3.11 export_ai_xan.py --gui    （需带 tkinter 的 Python，如官方安装版 3.11+）
  - 显示当前阶段与逐条进度（扫描应用 / 导出会话计数），等待限速时每秒倒计时提示，
    可随时「取消」；记住上次填写的账号、token 与输出目录。
  - 输出目录下的 history.json 自动记录两类历史:
      拉取历史 apps  : 每个应用最近一次成功抓取会话列表的时间与完整会话清单
      下载历史 convs : 每个会话已下载到的文件、消息数、下载时间
  - 重跑自动跳过「已下载」的会话（不联网、不计入限速等待）；--overwrite 强制重下。
    勾选「跳过已抓取的应用」(--skip-fetched) 后，上次成功抓取过会话列表的应用不再重复抓取，
    直接用历史清单补下载漏掉的会话 —— 适合每天/经常重跑做增量备份。

用法:
  python3 export_ai_xan.py --email 你的邮箱 --password 你的密码
  python3 export_ai_xan.py --token <console_token>            # 已有 token 时
  python3 export_ai_xan.py --email xxx --password xxx --out ./out --workers 6
  python3 export_ai_xan.py --email xxx --password xxx --only-app <appId> [--only-app <appId2>]
  python3 export_ai_xan.py --gui                               # 图形界面

选项:
  --email EMAIL        登录邮箱
  --password PASSWORD  登录密码
  --token TOKEN        直接用 token（跳过登录）
  --gui                打开图形界面（显示进度/可取消/记住设置）
  --out DIR            输出目录（默认 ./ai-xan-export-<日期>；想启用历史自动跳过请固定用同一个目录）
  --workers N          并发下载数（默认 4，别开太大，对服务器友好一点）
  --sleep SEC          请求成功后额外小额休眠（默认 0.2 秒，可选）
  --sleep-min SEC      每个网络请求前的随机等待下限，秒（默认 10）
  --sleep-max SEC      每个网络请求前的随机等待上限，秒（默认 30；两个都设 0 = 关闭等待）
  --only-app ID        只导出指定 app（可多次指定；appId 取页面 URL 最后一段）
  --include-json       每个会话额外存一份原始 JSON（含 query/answer 原文）
  --overwrite          重新下载已存在的文件（默认跳过已导出的，可断点续跑）
  --skip-fetched       跳过上次已成功抓取过会话列表的应用（增量模式；记录见 history.json）
  --domain URL         域名/镜像; 默认 auto=自动ping选延迟最低, 可指定或自定义
  --no-token-cache     不把 token 缓存到输出目录

环境变量: AIXAN_EMAIL / AIXAN_PASSWORD / AIXAN_TOKEN 也可替代上面参数。

输出结构:
  out/
    index.json                  # 总索引: 应用 / 会话 / 消息数 / 文件路径
    export.log                  # 过程日志
    errors.log                  # 失败的请求记录（改完参数重跑即可续）
    history.json                # 拉取/下载历史（自动维护，用于跳过已下载与已抓取）
    <应用名>/                   # 每个应用一个文件夹
      __ALL__<应用名>.txt       # 该应用所有会话合并版（按时间排序）
      <序号>_<会话名>_<时间戳>.txt
      (可选) *.json             # --include-json 时每个会话的原始数据
"""
import argparse, json, os, random, re, sys, time, urllib.request, urllib.parse, urllib.error, datetime, threading, queue

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    _TK_OK = True
except Exception:
    _TK_OK = False

# PyInstaller --windowed 双击运行时无控制台, sys.stdout/stderr 可能为 None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 镜像域名: 同一个网站(仅入口域名不同), 默认列表, 也可在 GUI/命令行自定义
MIRROR_DOMAINS = [
    "https://ai-xan.xyz",
    "https://acepro.store",
    "https://acquainte.xyz",
    "https://acquant.xyz",
    "https://affectional.xyz",
    "https://aiwhatis.xyz",
    "https://aquantancee.xyz",
    "https://aquante.xyz",
    "http://aisearches.xyz",
    "http://aigirlfriend.baby",
]

BASE = "https://ai-xan.xyz"
API = BASE + "/console/api"


def set_base(url):
    """切换当前使用的域名(镜像)。"""
    global BASE, API
    BASE = url.rstrip("/")
    API = BASE + "/console/api"


def ping_domain(url, timeout=5):
    """测单个域名延迟(ms); 不可达返回 None。"""
    url = url.rstrip("/")
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return round((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError:
        return round((time.monotonic() - t0) * 1000)
    except Exception:
        try:
            t0 = time.monotonic()
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout):
                pass
            return round((time.monotonic() - t0) * 1000)
        except urllib.error.HTTPError:
            return round((time.monotonic() - t0) * 1000)
        except Exception:
            return None


def ping_and_pick(domains, timeout=5, log_fn=None):
    """Ping 全部域名, 返回 [(url, ms)] 按延迟升序(不可达排最后)。"""
    results = []
    for url in domains:
        url = url.strip()
        if not url:
            continue
        if log_fn:
            log_fn(f"  测速 {url} ...")
        ms = ping_domain(url, timeout)
        results.append((url, ms))
        if log_fn:
            log_fn(f"    {url}: {ms}ms" if ms is not None else f"    {url}: 不可达")
    results.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 10**9))
    return results

USER_PREF = "[USER]:"
AI_PREF = "[AI]:"
ROUND_DIV = "#-------------------------------------------#"
CHAT_DIV = "#===========================================#"
BOM = b"\xef\xbb\xbf"

CHUNK = 500          # 会话列表每页拉取上限（前端也用 500）
LIST_LIMIT = 20      # 历史应用列表分页大小（前端默认 20）
RETRY = 3

_log_lock = threading.Lock()
_hist_lock = threading.Lock()          # 历史文件写锁
_counter = {"apps": 0, "convs": 0, "msgs": 0, "files": 0, "skipped": 0, "errors": 0}
_H = {"apps": {}, "convs": {}}         # 当前输出目录的拉取/下载历史(内存副本)
_OUT = ""                              # 当前输出目录(供各处访问)
_OUT_LOGPATH = ""
_OUT_ERRPATH = ""
_STOP_EVT = threading.Event()          # 置位 => 用户取消(限速等待中的请求尽快中止)
_UIQ = None                            # GUI 事件队列；None = 纯命令行模式
_hist_flush = [0]                      # 距上次落盘的下载数(每 25 个落盘一次)
_counter_lock = threading.Lock()
fmt_lock = threading.Lock()


class Cancelled(Exception):
    """用户点击取消。"""
    pass


def ui_event(**kw):
    """向 GUI 事件队列投递事件(纯 CLI 时为空操作)。"""
    q = _UIQ
    if q is not None:
        try:
            q.put(kw)
        except Exception:
            pass


class RateLimiter:
    """全局限速器（防封号）。

    所有线程共享同一个实例：每次调用 wait_turn() 会预约一个时间槽，保证任意两次
    网络请求之间至少随机间隔 [min_wait, max_wait] 秒；并发请求会排队放行，
    从源头避免短时间高频访问触发网站风控。等待期间每秒回调 tick_cb(剩余秒数)，
    并响应取消事件（被取消时返回 False）。
    """

    def __init__(self, min_wait=10.0, max_wait=30.0):
        self.lock = threading.Lock()
        self.min_wait = float(min_wait)
        self.max_wait = float(max_wait)
        self._next_allowed = 0.0  # time.monotonic() 时间轴上允许发起下一个请求的时刻
        self.tick_cb = None       # 供 GUI 每秒刷新倒计时: tick_cb(剩余秒数)

    def wait_turn(self):
        """阻塞直到轮到自己，并预约下一个请求的时间点。返回 False 表示用户已取消。"""
        with self.lock:
            now = time.monotonic()
            slot = max(now, self._next_allowed) + random.uniform(self.min_wait, self.max_wait)
            self._next_allowed = slot
            delay = slot - now
        while delay > 0:
            step = min(delay, 1.0)
            if _STOP_EVT.wait(step):     # Event.wait 可中断：取消后 1 秒内返回 True
                return False
            delay -= step
            if self.tick_cb is not None:
                try:
                    self.tick_cb(delay)
                except Exception:
                    pass
        return True


_throttle = RateLimiter()  # 模块级单例：默认每个网络操作前随机等 10~30 秒


def log(msg):
    with _log_lock:
        line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        ui_event(t="log", line=line)
        try:
            with open(_OUT_LOGPATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def errlog(msg):
    with _log_lock:
        line = f"[{datetime.datetime.now():%H:%M:%S}] ERROR {msg}"
        ui_event(t="logerr", line=line)
        try:
            with open(_OUT_ERRPATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


class Api:
    def __init__(self, token, sleep=0.2):
        self.token = token
        self.sleep = sleep

    def _req(self, method, path, params=None, body=None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json",
            "X-Language": "zh-Hans",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        last_err = None
        for attempt in range(1, RETRY + 1):
            if not _throttle.wait_turn():  # 防封号等待（可取消）
                raise Cancelled()
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read().decode("utf-8", "replace")
                if self.sleep:
                    time.sleep(self.sleep)
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                raw = ""
                try:
                    raw = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_err = f"HTTP {e.code} {raw}"
                if e.code in (401, 403):
                    break  # token 失效, 不用重试
                if _STOP_EVT.wait(2 * attempt):  # 退避等待期间也可取消
                    raise Cancelled()
            except Exception as e:
                last_err = str(e)
                if _STOP_EVT.wait(2 * attempt):
                    raise Cancelled()
        raise RuntimeError(f"{method} {path} 失败: {last_err}")

    def get(self, path, params=None):
        return self._req("GET", path, params=params)

    def post(self, path, body=None):
        return self._req("POST", path, body=body)


def login(email, password):
    """登录失败抛 RuntimeError（供 CLI/GUI 各自处理），不再直接 sys.exit。"""
    api = Api(None, sleep=0)
    try:
        d = api.post("/login", {"email": email, "password": password, "language": "zh-Hans"})
    except RuntimeError as e:
        raise RuntimeError(f"登录失败: {e}") from e
    tok = d.get("data")
    if not tok:
        raise RuntimeError(f"登录失败（返回内容异常）: {str(d)[:300]}")
    return tok


def sanitize(name, limit=80):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if len(name) > limit:
        name = name[:limit].rstrip()
    return name or "unnamed"
def fetch_all_conversations(api, app_id):
    """会话列表: pinned=false + pinned=true 两个 tab 都拉, 按 id 去重"""
    seen, order = {}, []
    for pinned in ("false", "true"):
        last_id = ""
        while True:
            params = {"limit": CHUNK, "pinned": pinned}
            if last_id:
                params["last_id"] = last_id
            d = api.get(f"/installed-apps/{app_id}/conversations", params)
            data = d.get("data") or []
            for c in data:
                cid = c.get("id")
                if cid and cid not in seen:
                    seen[cid] = c
                    order.append(cid)
            if not d.get("has_more") or not data:
                break
            last_id = data[-1].get("id") or last_id
    convs = [seen[cid] for cid in order]
    convs.sort(key=lambda c: c.get("created_at") or 0)
    return convs


def format_chat_txt(messages):
    """与网页「导出记录」生成的 txt 完全一致"""
    parts = []
    for m in messages:
        q = m.get("query") or ""
        a = m.get("answer") or ""
        parts.append(f"{USER_PREF}{q}\n{ROUND_DIV}\n{AI_PREF}{a}")
    if not parts:
        return ""
    return f"\n{CHAT_DIV}\n".join(parts) + f"\n{CHAT_DIV}\n"


def ts_to_name(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y%m%d-%H%M%S")
    except Exception:
        return "unknown"


# ---------- 拉取/下载历史 (history.json) ----------

def history_path(out_dir):
    return os.path.join(out_dir, "history.json")


def load_history(out_dir):
    try:
        with open(history_path(out_dir), "r", encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict):
            h.setdefault("apps", {})
            h.setdefault("convs", {})
            return h
    except Exception:
        pass
    return {"apps": {}, "convs": {}}


def save_history():
    """把内存历史 _H 原子落盘到输出目录 history.json。"""
    if not _OUT:
        return
    try:
        with _hist_lock:
            p = history_path(_OUT)
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(_H, f, ensure_ascii=False, indent=1)
            os.replace(p + ".tmp", p)
    except Exception as e:
        errlog(f"保存历史记录失败: {e}")


def _conv_done(conv, out_dir):
    """该会话是否已下载过(下载历史命中 且 记录的文件仍在)。"""
    cid = conv.get("id")
    if not cid:
        return False
    rec = _H["convs"].get(cid)
    return bool(rec and rec.get("txt") and os.path.exists(os.path.join(out_dir, rec["txt"])))


def conv_txt_path(app_name, conv, out_dir):
    """会话导出的 txt 完整路径(文件名规则与原版一致)。"""
    cname = sanitize(conv.get("name") or "新的对话", 60)
    cts = ts_to_name(conv.get("created_at") or 0)
    cid8 = (conv.get("id") or "unknown")[:8]
    safe = sanitize(app_name, 60)
    return os.path.join(out_dir, safe, f"{cname}_{cts}_{cid8}.txt")


def export_one(api, app_name, app_id, conv, out_dir, include_json, overwrite):
    """导出单个会话；已下载过则直接跳过(不联网)。

    返回 (txt路径, json路径, 消息数, 是否新下载)。
    """
    cid = conv.get("id")
    txt_path = conv_txt_path(app_name, conv, out_dir)
    if not overwrite and (_conv_done(conv, out_dir) or os.path.exists(txt_path)):
        with _counter_lock:
            _counter["skipped"] += 1
        return txt_path, None, None, False
    os.makedirs(os.path.dirname(txt_path), exist_ok=True)
    d = api.get(f"/installed-apps/{app_id}/messages/export",
                {"conversation_id": cid, "last_id": ""})
    messages = d.get("messages") or []
    with _counter_lock:
        _counter["convs"] += 1
        _counter["msgs"] += len(messages)
    text = format_chat_txt(messages)
    with fmt_lock:
        with open(txt_path, "wb") as f:
            f.write(BOM + text.encode("utf-8"))
        json_path = None
        if include_json:
            json_path = os.path.splitext(txt_path)[0] + ".json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"app_id": app_id, "conversation": conv, "messages": messages},
                          f, ensure_ascii=False, indent=1)
    # 记下载历史(断点重跑/崩溃续跑的依据)
    _H["convs"][cid] = {
        "app_id": app_id,
        "txt": os.path.relpath(txt_path, out_dir),
        "msgs": len(messages),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with _counter_lock:
        _counter["files"] += 1
    _hist_flush[0] += 1
    if _hist_flush[0] % 25 == 0:
        save_history()
    return txt_path, json_path, len(messages), True


def worker(api, job, out_dir, include_json, overwrite, results, lock):
    app_name, app_id, conv = job
    if _STOP_EVT.is_set():
        raise Cancelled()
    try:
        txt, jp, n, _fresh = export_one(api, app_name, app_id, conv, out_dir, include_json, overwrite)
        with lock:
            results.append({"conversation_id": conv.get("id"), "conversation_name": conv.get("name"),
                            "app_id": app_id, "app_name": app_name, "txt": txt,
                            "json": jp, "messages": n})
    except Cancelled:
        raise
    except Exception as e:
        with lock:
            _counter["errors"] += 1
            results.append({"conversation_id": conv.get("id"), "app_id": app_id, "app_name": app_name, "error": str(e)})
        errlog(f"导出失败 conv={conv.get('id')} app={app_id}: {e}")
    finally:
        with _counter_lock:
            done = _counter["convs"] + _counter["skipped"] + _counter["errors"]
        ui_event(t="convs", done=done)
def _finalize(me, apps, results, cancelled):
    """收尾: 合并各应用总 txt + 写 index.json + 存历史 + 汇总日志(正常/取消共用)。"""
    out_dir = _OUT
    ui_event(t="busy")
    if not apps:
        log("! 没有找到任何历史游玩记录/应用")
    results.sort(key=lambda r: (r.get("app_name") or "", r.get("conversation_id") or ""))

    # ---- 4. 每个应用合并一个总 txt (按会话时间) ----
    by_app = {}
    for r in results:
        if r.get("txt") and os.path.exists(r["txt"]):
            by_app.setdefault(r["app_name"], []).append(r)
    merged_count = 0
    for aname, rs in by_app.items():
        safe = sanitize(aname, 60)
        folder = os.path.join(out_dir, safe)
        merged_path = os.path.join(folder, f"__ALL__{safe}.txt")
        try:
            with open(merged_path, "wb") as f:
                for r in rs:
                    with open(r["txt"], "rb") as g:
                        content = g.read()
                    if content.startswith(BOM):
                        content = content[len(BOM):]
                    f.write(BOM)
                    f.write(content)
                    f.write(b"\n\n")
            merged_count += 1
        except Exception as e:
            errlog(f"合并文件失败 {merged_path}: {e}")

    # ---- 5. 总索引 ----
    index = {
        "account": me.get("email"),
        "exported_at": datetime.datetime.now().isoformat(),
        "app_count": len(apps),
        "conversation_count": _counter["convs"] + _counter["skipped"],
        "message_count": _counter["msgs"],
        "files_written": _counter["files"],
        "skipped_existing": _counter["skipped"],
        "errors": _counter["errors"],
        "cancelled": cancelled,
        "history": {"apps": len(_H["apps"]), "convs": len(_H["convs"])},
        "apps": {},
    }
    for aname, rs in by_app.items():
        index["apps"][aname] = rs
    index["app_list"] = [
        {"name": (it.get("app") or {}).get("name"), "app_id": (it.get("app") or {}).get("id"),
         "conversations": sum(1 for r in results if r.get("app_name") == (it.get("app") or {}).get("name"))}
        for it in apps
    ]
    try:
        with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
    except Exception as e:
        errlog(f"写 index.json 失败: {e}")
    save_history()

    log("=" * 50)
    log(f"完成! 输出目录: {os.path.abspath(out_dir)}")
    log(f"应用 {len(apps)} 个 | 会话 {_counter['convs'] + _counter['skipped']} 个"
        f"(新导出 {_counter['convs']}, 跳过已存在 {_counter['skipped']}) | "
        f"消息 {_counter['msgs']} 条 | 合并总txt {merged_count} 个 | 失败 {_counter['errors']}")
    if _counter["errors"]:
        log(f"有失败项, 详见 {os.path.join(out_dir, 'errors.log')}, 修复后重跑会自动续传。")
    if cancelled:
        log("本次已取消: 已完成/已下载的会话都记入 history.json, 下次重跑自动跳过。")
    return {
        "ok": True, "cancelled": cancelled,
        "apps": len(apps),
        "convs": _counter["convs"] + _counter["skipped"],
        "downloaded": _counter["convs"],
        "skipped": _counter["skipped"],
        "msgs": _counter["msgs"],
        "merged": merged_count,
        "errors": _counter["errors"],
        "out_dir": os.path.abspath(out_dir),
    }


def run_export(args, is_gui=False):
    """执行一次完整导出。args 为命名空间对象(CLI 解析结果或 GUI 构造的同名对象)。"""
    if args.sleep_min < 0 or args.sleep_max < args.sleep_min:
        raise RuntimeError("--sleep-min/--sleep-max 不合法: 需满足 0 <= min <= max")
    _throttle.min_wait = float(args.sleep_min)
    _throttle.max_wait = float(args.sleep_max)
    _STOP_EVT.clear()
    _hist_flush[0] = 0
    with _counter_lock:
        for k in _counter:
            _counter[k] = 0

    global _OUT, _H, _OUT_LOGPATH, _OUT_ERRPATH

    # ---- 0. 选择域名(镜像): auto=自动ping选延迟最低; 否则用指定/自定义域名 ----
    domain = getattr(args, "domain", "").strip()
    if not domain or domain == "auto":
        ui_event(t="phase", text="正在测试各镜像域名延迟…")
        log("自动 ping 各镜像域名, 选择延迟最低:")
        ranked = ping_and_pick(MIRROR_DOMAINS, log_fn=log)
        if ranked and ranked[0][1] is not None:
            set_base(ranked[0][0])
            log(f"已选择最快域名: {BASE} ({ranked[0][1]}ms)")
        else:
            raise RuntimeError("所有镜像域名均不可达, 请检查网络或用 --domain 手动指定")
    else:
        if not domain.startswith("http://") and not domain.startswith("https://"):
            domain = "https://" + domain
        set_base(domain)
        log(f"使用指定域名: {BASE}")

    token = args.token
    if not token:
        token = login(args.email, args.password)

    out_dir = args.out or f"ai-xan-export-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    os.makedirs(out_dir, exist_ok=True)
    _OUT = out_dir
    _OUT_LOGPATH = os.path.join(out_dir, "export.log")
    _OUT_ERRPATH = os.path.join(out_dir, "errors.log")
    _H = load_history(out_dir)

    # token 缓存（30 天有效）, 方便断点重跑不重复登录
    cache_path = os.path.join(out_dir, ".token.json")
    if not args.no_token_cache:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"token": token}, f)
            os.chmod(cache_path, 0o600)
        except Exception:
            pass

    api = Api(token, sleep=args.sleep)
    log(f"已登录, token 前缀: {token[:24]}...")
    if _throttle.min_wait > 0:
        log(f"防封号限速: 每次网络请求前随机等待 {_throttle.min_wait:g}~{_throttle.max_wait:g} 秒(全局排队)")
    else:
        log("防封号限速: 已关闭(--sleep-min/--sleep-max 均为 0)")
    ui_event(t="busy", text="登录中…")

    cancelled = False
    me = None
    apps, results = [], []
    try:
        ui_event(t="phase", text="获取账号信息…")
        try:
            me = api.get("/account/profile")
        except RuntimeError as e:
            raise RuntimeError(f"获取账号信息失败(登录可能已失效): {e}") from e
        uid = me.get("id")
        log(f"账号: {me.get('name')} <{me.get('email')}>  uid={uid}")

        # ---- 1. 历史游玩记录(应用列表) ----
        ui_event(t="phase", text="正在获取应用列表…")
        if args.only_app:
            for aid in args.only_app:
                if _STOP_EVT.is_set():
                    raise Cancelled()
                try:
                    d = api.get(f"/installed-apps/{aid}")
                    apps.append({"id": d.get("id"), "app": (d.get("app") or {})})
                except Exception as e:
                    log(f"! 无法读取指定 app {aid}: {e}")
        else:
            page = 1
            while True:
                if _STOP_EVT.is_set():
                    raise Cancelled()
                d = api.get("/used-installed-apps", {"user": uid, "page": page, "limit": LIST_LIMIT,
                                                     "keyword": "", "timestamp": 0})
                items = d.get("installed_apps") or []
                apps += items
                total = d.get("total") or 0
                log(f"历史游玩记录: 第{page}页 +{len(items)} (累计 {len(apps)}/{total})")
                if len(apps) >= total or not items:
                    break
                page += 1
        if not apps:
            log("! 没有找到任何历史游玩记录/应用")
            ui_event(t="phase", text="没有找到任何应用")
        else:
            log(f"共 {len(apps)} 个应用, 开始扫描会话列表…")
            ui_event(t="phase", text="扫描各应用会话列表…")

        # ---- 2. 每个应用的会话列表(拉取历史: 已抓取过的应用按需复用清单) ----
        jobs = []
        for idx, it in enumerate(apps):
            if _STOP_EVT.is_set():
                raise Cancelled()
            aid = (it.get("app") or {}).get("id") or it.get("id")
            aname = (it.get("app") or {}).get("name") or aid
            app_h = _H["apps"].get(aid)
            if args.skip_fetched and app_h is not None:
                convs = [dict(c) for c in (app_h.get("convs") or [])]
                log(f"  [历史] {aname[:40]}: 上次已抓取({len(convs)} 会话), 复用清单")
            else:
                try:
                    convs = fetch_all_conversations(api, aid)
                except Cancelled:
                    raise
                except Exception as e:
                    errlog(f"会话列表获取失败 app={aid}: {e}")
                    log(f"! 会话列表失败: {aname[:30]}… ({e})")
                    continue
                # 抓取成功即记拉取历史(含完整会话清单), 崩溃/中断后可用 --skip-fetched 续跑
                _H["apps"][aid] = {
                    "name": aname,
                    "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "convs": [{"id": c.get("id"), "name": c.get("name"),
                               "created_at": c.get("created_at")} for c in convs],
                }
                save_history()
            if convs:
                jobs += [(aname, aid, c) for c in convs]
                log(f"  {aname[:40]}: {len(convs)} 个会话")
            else:
                log(f"  {aname[:40]}: (无会话)")
            ui_event(t="apps", done=idx + 1, total=len(apps))

        # 同名应用加后缀避免文件夹串内容
        name_count = {}
        for aname, aid, _c in jobs:
            name_count[aname] = name_count.get(aname, 0) + 1
        jobs = [(aname + (f"_{aid[:8]}" if name_count[aname] > 1 else ""), aid, c)
                for aname, aid, c in jobs]

        # ---- 3. 逐个会话导出(下载历史: 已下载的自动跳过, 不联网) ----
        if not jobs:
            log("没有可导出的会话。")
        else:
            if _throttle.min_wait > 0:
                to_dl = len(jobs)
                if not args.overwrite:
                    to_dl = sum(1 for _an, _aid, c in jobs
                                if not _conv_done(c, out_dir)
                                and not os.path.exists(conv_txt_path(_an, c, out_dir)))
                avg_wait = (_throttle.min_wait + _throttle.max_wait) / 2
                log(f"预计需网络下载约 {to_dl}/{len(jobs)} 个会话(其余自动跳过), "
                    f"限速等待约 {to_dl * avg_wait / 60:.0f} 分钟")
            log(f"待导出会话总数: {len(jobs)}")
            ui_event(t="convs", done=0, total=len(jobs))
            ui_event(t="phase", text=f"导出会话 0/{len(jobs)}…")
            from concurrent.futures import ThreadPoolExecutor, CancelledError as _CF_Cancelled
            result_lock = threading.Lock()
            n_workers = max(1, min(int(args.workers), len(jobs)))
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = [ex.submit(worker, api, job, out_dir, args.include_json, args.overwrite,
                                  results, result_lock) for job in jobs]
                for f in futs:
                    try:
                        f.result()
                    except _CF_Cancelled:
                        pass
                    except Cancelled:
                        cancelled = True
                        log("收到取消请求, 等待进行中的请求结束…")
                        break
            if cancelled:
                log("下载已停止(部分完成), 正在收尾…")
    except Cancelled:
        cancelled = True
        if me is None:
            log("已取消(尚未开始导出)。")
            save_history()
            summary = {"ok": True, "cancelled": True, "apps": 0, "convs": 0, "downloaded": 0,
                       "skipped": 0, "msgs": 0, "merged": 0, "errors": 0,
                       "out_dir": os.path.abspath(out_dir)}
            if is_gui:
                summary["token"] = token
            ui_event(t="done", summary=summary)
            return summary
        log("已取消: 保留已完成的部分, 正在收尾…")

    summary = _finalize(me, apps, results, cancelled)
    if is_gui:
        summary["token"] = token
    return summary
# ---------- 图形界面 (GUI) ----------

GUI_STATE_PATH = os.path.join(os.path.expanduser("~"), ".aixan_export_gui.json")


def load_gui_state():
    try:
        with open(GUI_STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_gui_state(st):
    try:
        with open(GUI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


class ExportGui:
    """tkinter 图形界面: 配置表单 + 阶段/逐条进度 + 限速倒计时 + 可取消 + 日志窗。"""

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.running = False
        self._total = None  # 本批待导出会话总数(用于取消时显示已完成百分比)
        st = load_gui_state()
        default_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-xan-export")

        root.title("AI风月 聊天记录批量导出器")
        root.geometry("780x700")
        root.minsize(700, 580)

        pad = {"padx": 6, "pady": 3}
        frm = ttk.Frame(root, padding=8)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        # 行0: 邮箱 / 密码
        ttk.Label(frm, text="邮箱").grid(row=0, column=0, sticky="e", **pad)
        self.v_email = tk.StringVar(value=st.get("email", ""))
        self.inp_email = ttk.Entry(frm, textvariable=self.v_email)
        self.inp_email.grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(frm, text="密码").grid(row=0, column=2, sticky="e", **pad)
        self.v_password = tk.StringVar()
        self.inp_password = ttk.Entry(frm, textvariable=self.v_password, show="*", width=16)
        self.inp_password.grid(row=0, column=3, sticky="we", **pad)

        # 行1: token
        ttk.Label(frm, text="Token").grid(row=1, column=0, sticky="e", **pad)
        self.v_token = tk.StringVar(value=st.get("token", ""))
        self.inp_token = ttk.Entry(frm, textvariable=self.v_token)
        self.inp_token.grid(row=1, column=1, columnspan=3, sticky="we", **pad)
        ttk.Label(frm, text="有 token 优先(导出完自动回填); 留空则用邮箱+密码登录, 密码不保存。").grid(
            row=2, column=1, columnspan=3, sticky="w", **pad)

        # 行3/4: 输出目录
        ttk.Label(frm, text="输出目录").grid(row=3, column=0, sticky="e", **pad)
        self.v_out = tk.StringVar(value=st.get("out_dir", default_out))
        self.inp_out = ttk.Entry(frm, textvariable=self.v_out)
        self.inp_out.grid(row=3, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(frm, text="浏览…", command=self._pick_dir).grid(row=3, column=3, sticky="we", **pad)
        ttk.Label(frm, text="固定用同一目录才能自动跳过已下载(history.json 记录在目录内)").grid(
            row=4, column=1, columnspan=3, sticky="w", **pad)

        # 行5: 域名(镜像, 网站内容完全相同): auto=启动时自动ping选最快; 也可下拉或手输自定义域名
        ttk.Label(frm, text="域名").grid(row=5, column=0, sticky="e", **pad)
        self.v_domain = tk.StringVar(value=st.get("domain", "auto"))
        self.cb_domain = ttk.Combobox(frm, textvariable=self.v_domain, width=24)
        self.cb_domain["values"] = ["auto"] + MIRROR_DOMAINS
        self.cb_domain.grid(row=5, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(frm, text="测速", command=self._on_ping).grid(row=5, column=3, sticky="w", **pad)

        # 行6: 并发 / 限速
        ttk.Label(frm, text="并发下载").grid(row=6, column=0, sticky="e", **pad)
        self.v_workers = tk.StringVar(value=str(st.get("workers", 4)))
        self.sp_workers = ttk.Spinbox(frm, from_=1, to=12, width=4, textvariable=self.v_workers)
        self.sp_workers.grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(frm, text="请求前随机等待").grid(row=6, column=2, sticky="e", **pad)
        waitf = ttk.Frame(frm)
        waitf.grid(row=6, column=3, sticky="w", **pad)
        self.v_smin = tk.StringVar(value=str(st.get("sleep_min", 10)))
        self.sp_smin = ttk.Spinbox(waitf, from_=0, to=300, width=4, textvariable=self.v_smin)
        self.sp_smin.pack(side="left")
        ttk.Label(waitf, text=" ~ ").pack(side="left")
        self.v_smax = tk.StringVar(value=str(st.get("sleep_max", 30)))
        self.sp_smax = ttk.Spinbox(waitf, from_=0, to=300, width=4, textvariable=self.v_smax)
        self.sp_smax.pack(side="left")
        ttk.Label(waitf, text=" 秒(0 关闭)").pack(side="left")

        # 行7: 仅导出指定 app
        ttk.Label(frm, text="仅导出 App").grid(row=7, column=0, sticky="e", **pad)
        self.v_only = tk.StringVar(value=st.get("only_app", ""))
        self.inp_only = ttk.Entry(frm, textvariable=self.v_only)
        self.inp_only.grid(row=7, column=1, columnspan=2, sticky="we", **pad)
        ttk.Label(frm, text="appId 逗号分隔; 留空=全部").grid(row=7, column=3, sticky="w", **pad)

        # 行8: 选项
        chk = ttk.Frame(frm)
        chk.grid(row=8, column=1, columnspan=3, sticky="w", **pad)
        self.v_inc = tk.BooleanVar(value=bool(st.get("include_json", False)))
        self.v_ovw = tk.BooleanVar(value=bool(st.get("overwrite", False)))
        self.v_skip = tk.BooleanVar(value=bool(st.get("skip_fetched", True)))
        ttk.Checkbutton(chk, text="另存原始 JSON", variable=self.v_inc).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(chk, text="强制重下已存在的", variable=self.v_ovw).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(chk, text="跳过上次已抓取的应用(增量)", variable=self.v_skip).pack(side="left")

        # 行9: 按钮
        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=4, sticky="we", **pad)
        self.btn_start = ttk.Button(btns, text="开始导出", command=self._on_start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_cancel = ttk.Button(btns, text="取消", command=self._on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=(0, 8))
        self.btn_open = ttk.Button(btns, text="打开输出目录", command=self._open_dir)
        self.btn_open.pack(side="left")

        # 行10-11: 状态 / 进度条 / 限速倒计时
        self.var_status = tk.StringVar(value="就绪。填账号(或 token)后点「开始导出」。")
        ttk.Label(frm, textvariable=self.var_status, wraplength=720).grid(
            row=10, column=0, columnspan=4, sticky="we", **pad)
        self.bar = ttk.Progressbar(frm, mode="determinate")
        self.bar.grid(row=11, column=0, columnspan=4, sticky="we", padx=6, pady=2)
        self.var_wait = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.var_wait, foreground="#666666").grid(
            row=12, column=0, columnspan=4, sticky="w", padx=6)

        # 行13: 日志窗
        self.log_txt = tk.Text(frm, height=13, state="disabled", wrap="char")
        self.log_txt.grid(row=13, column=0, columnspan=4, sticky="nsew", padx=6, pady=4)
        sb = ttk.Scrollbar(frm, command=self.log_txt.yview)
        sb.grid(row=13, column=4, sticky="ns", pady=4)
        self.log_txt.config(yscrollcommand=sb.set)
        self.log_txt.tag_configure("err", foreground="#b00000")
        frm.rowconfigure(13, weight=1)

        self._log_append("就绪。设置好后点「开始导出」。")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(80, self._pump)

    # ---------- 小工具 ----------
    def _log_append(self, line, tag=None):
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", line + "\n", (tag,) if tag else ())
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _err(self, msg):
        messagebox.showerror("AI风月导出器", msg, parent=self.root)

    def _pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.v_out.get().strip() or None, title="选择导出目录")
        if d:
            self.v_out.set(d)

    def _set_running(self, on):
        self.running = on
        st = "disabled" if on else "normal"
        for w in (self.inp_email, self.inp_password, self.inp_token, self.inp_out, self.inp_only,
                  self.sp_workers, self.sp_smin, self.sp_smax, self.cb_domain):
            w.config(state=st)
        self.btn_start.config(state="disabled" if on else "normal")
        self.btn_cancel.config(state="normal" if on else "disabled")
        if not on:
            self.bar.stop()

    def _save_state(self):
        save_gui_state({
            "email": self.v_email.get().strip(),
            "token": self.v_token.get().strip(),
            "out_dir": self.v_out.get().strip(),
            "workers": self.v_workers.get(),
            "sleep_min": self.v_smin.get(),
            "sleep_max": self.v_smax.get(),
            "only_app": self.v_only.get(),
            "include_json": bool(self.v_inc.get()),
            "overwrite": bool(self.v_ovw.get()),
            "skip_fetched": bool(self.v_skip.get()),
            "domain": self.v_domain.get().strip(),
        })

    def _on_ping(self):
        """测速按钮: 后台 ping 全部镜像, 结果写日志, 最快域名自动填入下拉框。"""
        self._log_append("正在测试各镜像域名延迟…")
        threading.Thread(target=self._ping_worker, daemon=True).start()

    def _ping_worker(self):
        def cb(msg):
            self.q.put({"t": "log", "line": f"[{datetime.datetime.now():%H:%M:%S}] {msg}"})
        ranked = ping_and_pick(MIRROR_DOMAINS, log_fn=cb)
        if ranked and ranked[0][1] is not None:
            self.q.put({"t": "ping_done", "url": ranked[0][0], "ms": ranked[0][1]})
        else:
            self.q.put({"t": "fatal", "msg": "所有镜像域名均不可达, 请检查网络后重试"})

    # ---------- 事件 ----------
    def _on_start(self):
        email = self.v_email.get().strip()
        password = self.v_password.get()
        token = self.v_token.get().strip()
        out_dir = self.v_out.get().strip()
        if not token and not (email and password):
            self._err("请填写邮箱+密码, 或直接填 Token。")
            return
        if not out_dir:
            self._err("请填写输出目录。")
            return
        try:
            workers = int(self.v_workers.get())
            smin = float(self.v_smin.get())
            smax = float(self.v_smax.get())
        except ValueError:
            self._err("并发数 / 等待时间必须是数字。")
            return
        if workers < 1 or smin < 0 or smax < smin:
            self._err("参数不合法: 并发 >= 1, 且 0 <= 等待下限 <= 等待上限。")
            return
        args = argparse.Namespace(
            email=email or None, password=password or None, token=token or None,
            out=out_dir, workers=workers, sleep=0.2,
            sleep_min=smin, sleep_max=smax,
            only_app=[s.strip() for s in re.split(r"[,，;；]", self.v_only.get()) if s.strip()],
            include_json=bool(self.v_inc.get()), overwrite=bool(self.v_ovw.get()),
            skip_fetched=bool(self.v_skip.get()), no_token_cache=False,
            domain=self.v_domain.get().strip(),
        )
        self._save_state()
        global _UIQ
        _UIQ = self.q
        _throttle.tick_cb = lambda sec: self.q.put({"t": "wait", "sec": sec})
        self._total = None
        self.var_wait.set("")
        self.var_status.set("准备中…")
        self._log_append("----- 开始导出 -----")
        self._set_running(True)
        threading.Thread(target=self._run, args=(args,), daemon=True).start()

    def _run(self, args):
        try:
            run_export(args, is_gui=True)
        except RuntimeError as e:
            self.q.put({"t": "fatal", "msg": str(e)})
        except Cancelled:
            pass  # 引擎内已收尾并发出 done
        except Exception:
            import traceback
            self.q.put({"t": "fatal", "msg": "发生未预期的错误:\n" + traceback.format_exc()})

    def _on_cancel(self):
        self.var_status.set("正在取消…(等当前请求结束, 最长约 30s)")
        self.btn_cancel.config(state="disabled")
        _STOP_EVT.set()
        self._log_append("取消请求已发出…")

    def _open_dir(self):
        d = self.v_out.get().strip()
        if not d:
            d = os.path.dirname(os.path.abspath(__file__))
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            self._err(str(e))

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno("仍在导出", "正在导出中, 要取消并退出吗?", parent=self.root):
                return
            _STOP_EVT.set()
        self._save_state()
        self.root.destroy()

    def _on_done(self, s):
        self._set_running(False)
        global _UIQ
        _UIQ = None
        _throttle.tick_cb = None
        if s.get("token"):
            self.v_token.set(s["token"])
        self._save_state()
        dl = s.get("downloaded", 0)
        sk = s.get("skipped", 0)
        er = s.get("errors", 0)
        cancelled = bool(s.get("cancelled"))
        self.bar.config(mode="determinate")
        if cancelled and self._total:
            done_pct = 100 * (dl + sk + er) / max(1, self._total)
            self.bar.config(maximum=100, value=min(100, int(done_pct)))
        else:
            self.bar.config(maximum=100, value=100 if not cancelled else 0)
        self.var_wait.set("")
        head = "已取消(部分完成)" if cancelled else "导出完成"
        msg = (f"{head}\n\n应用 {s.get('apps', 0)} 个 | 会话共 {s.get('convs', 0)} 个\n"
               f"新下载 {dl} | 跳过已存在 {sk} | 失败 {er}\n消息 {s.get('msgs', 0)} 条\n\n"
               f"输出目录:\n{s.get('out_dir', '')}")
        self.var_status.set(head + "  →  " + s.get("out_dir", ""))
        if er:
            msg += "\n\n部分请求失败, 详情见输出目录 errors.log; 直接重跑会自动续传。"
        messagebox.showinfo(head, msg, parent=self.root)

    def _on_fatal(self, msg):
        self._set_running(False)
        global _UIQ
        _UIQ = None
        _throttle.tick_cb = None
        self.var_status.set("出错")
        self._log_append(msg, "err")
        messagebox.showerror("出错", msg, parent=self.root)

    # ---------- 事件泵: 工作线程只往队列塞, 只在主线程动控件 ----------
    def _pump(self):
        try:
            while True:
                ev = self.q.get_nowait()
                t = ev.get("t")
                if t == "log":
                    self._log_append(ev["line"])
                elif t == "logerr":
                    self._log_append(ev["line"], "err")
                elif t == "phase":
                    self.var_status.set(ev["text"])
                    self.var_wait.set("")
                elif t == "busy":
                    self.bar.stop()
                    self.bar.config(mode="indeterminate")
                    self.bar.start(12)
                    if ev.get("text"):
                        self.var_status.set(ev["text"])
                elif t == "apps":
                    total = max(1, int(ev["total"]))
                    self.bar.stop()
                    self.bar.config(mode="determinate", maximum=total, value=int(ev["done"]))
                    self.var_status.set(f"扫描会话列表 {ev['done']}/{ev['total']} …")
                    self.var_wait.set("")
                elif t == "convs":
                    total = int(ev["total"])
                    self._total = total
                    self.bar.stop()
                    self.bar.config(mode="determinate", maximum=max(1, total), value=int(ev["done"]))
                    self.var_status.set(f"导出会话 {ev['done']}/{total}(已下载的自动跳过)")
                    self.var_wait.set("")
                elif t == "wait":
                    self.var_wait.set(f"防封号限速: 下一请求还需等待约 {int(ev['sec']) + 1}s")
                elif t == "ping_done":
                    self.v_domain.set(ev["url"])
                    self._log_append(f"已选最快域名: {ev['url']} ({ev['ms']}ms)")
                    self.var_status.set(f"已选最快域名: {ev['url']} ({ev['ms']}ms), 可点「开始导出」")
                elif t == "done":
                    self._on_done(ev.get("summary") or {})
                elif t == "fatal":
                    self._on_fatal(ev.get("msg", "未知错误"))
        except queue.Empty:
            pass
        self.root.after(80, self._pump)


def run_gui():
    root = tk.Tk()
    ExportGui(root)
    root.mainloop()


# ---------- 命令行入口 ----------

def main():
    ap = argparse.ArgumentParser(description="AI风月(ai-xan.xyz) 聊天记录批量导出 (GUI/CLI)")
    ap.add_argument("--gui", action="store_true", help="打开图形界面(需带 tkinter 的 Python)")
    ap.add_argument("--email", default=os.environ.get("AIXAN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("AIXAN_PASSWORD"))
    ap.add_argument("--token", default=os.environ.get("AIXAN_TOKEN"))
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--sleep-min", type=float, default=10.0,
                    help="每个网络请求前随机等待的下限秒数（默认 10）")
    ap.add_argument("--sleep-max", type=float, default=30.0,
                    help="每个网络请求前随机等待的上限秒数（默认 30）")
    ap.add_argument("--only-app", action="append", default=[])
    ap.add_argument("--include-json", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--skip-fetched", action="store_true",
                    help="跳过上次已成功抓取过会话列表的应用(增量模式, 记录在输出目录 history.json)")
    ap.add_argument("--domain", default="auto",
                    help="域名/镜像; auto=自动ping选延迟最低(默认), 或指定如 https://ai-xan.xyz (可自定义)")
    ap.add_argument("--no-token-cache", action="store_true")
    args = ap.parse_args()

    if args.gui:
        if not _TK_OK:
            sys.exit("当前 Python 缺少 tkinter, 无法启动 GUI。\n"
                     "请用带 tkinter 的 Python(官方安装版)运行, 例如:  py -3.11 export_ai_xan.py --gui")
        run_gui()
        return

    if not args.token and not (args.email and args.password):
        # 双击 exe / 无控制台环境(sys.stdin=None): 直接开图形界面, 避免像"打不开"
        if sys.stdin is None:
            if not _TK_OK:
                sys.exit("当前 Python 缺少 tkinter, 无法启动 GUI。\n"
                         "请用带 tkinter 的 Python(官方安装版)运行, 例如:  py -3.11 export_ai_xan.py --gui")
            run_gui()
            return
        sys.exit("请提供账号密码: --email xxx --password xxx  (或用 --token; 想用图形界面请加 --gui)")

    try:
        run_export(args)
    except RuntimeError as e:
        sys.exit(str(e))
    except Cancelled:
        pass  # 引擎内已收尾


if __name__ == "__main__":
    main()