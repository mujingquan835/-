"""AI 潜力项目雷达：抓取 GitHub 上 100~3000 星、近期新建/活跃的 AI 项目，
记录每日星数快照算增长，用 Claude 生成中文介绍，输出一个可搜索的静态网页。

用法:
    python radar.py fetch              # 抓取 GitHub 项目 + 记录星数快照
    python radar.py summarize [-n 40]  # 为新项目生成中文摘要(需要 ANTHROPIC_API_KEY)
    python radar.py build              # 生成 site/data.js，打开 site/index.html 即可查看
    python radar.py all                # 以上三步依次执行
    python radar.py serve              # 本地 http://localhost:8000 预览

环境变量:
    GITHUB_TOKEN       强烈建议设置（不设也能跑，但限速很严）
    ANTHROPIC_API_KEY  生成中文摘要用；不设则只展示英文描述
    CLAUDE_MODEL       默认 claude-opus-5-5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SITE = ROOT / "site"
REPOS_F = DATA / "repos.json"          # 最新的项目元数据
HISTORY_F = DATA / "history.json"      # {full_name: {"YYYY-MM-DD": stars}}
SUMMARY_F = DATA / "summaries.json"    # {full_name: {...中文摘要}}

# ---------------- 可调参数 ----------------
MIN_STARS = 100
MAX_STARS = 3000          # 潜力股上限；超过的算"爆款"
HOT_CREATED_WITHIN_DAYS = 90  # 爆款：只看 3 个月内新建、已超过 3000 星的项目（不限 AI）
HOT_QUERIES = [
    "",  # 所有领域的近期爆款，比如 Switch 模拟器这类非 AI 项目
    "llm OR agent OR ai OR gpt OR claude OR mcp in:name,description,topics",
]
CREATED_WITHIN_DAYS = 180 # 只看半年内新建的项目
PUSHED_WITHIN_DAYS = 30   # 且最近一个月有更新

# 每条是一个 GitHub 搜索条件（会自动拼上星数/时间限定）
QUERIES = [
    "topic:llm", "topic:ai-agent", "topic:agents", "topic:ai-agents",
    "topic:rag", "topic:mcp", "topic:mcp-server", "topic:generative-ai",
    "topic:large-language-models", "topic:llm-agent", "topic:ai-tools",
    "topic:claude", "topic:openai", "topic:chatgpt", "topic:ollama",
    "topic:text-to-speech", "topic:diffusion", "topic:computer-vision",
    "topic:artificial-intelligence", "topic:machine-learning",
    "topic:vibe-coding", "topic:coding-agent", "topic:multimodal",
    'llm in:name,description,readme', 'agent in:name,description',
    '"AI" in:name,description',
]

CATEGORIES = ["Agent/智能体", "编程助手", "RAG/知识库", "MCP/工具集成", "图像/视频生成",
              "语音/音频", "模型/训练/微调", "推理/部署", "AI应用/产品", "数据/评测", "其他"]


# ---------------- 通用工具 ----------------
def load(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def gh_get(url: str, accept="application/vnd.github+json") -> tuple[int, bytes, dict]:
    headers = {"Accept": accept, "User-Agent": "ai-repo-radar"}
    if tok := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# ---------------- 1. 抓取 ----------------
def search(q: str, pages: int) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 100, "page": page})
        for attempt in range(3):
            code, body, hdr = gh_get(url)
            if code == 200:
                break
            if code in (403, 429):
                reset = int(hdr.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(5, min(90, reset - int(time.time()) + 2))
                print(f"  限速，等待 {wait}s ...")
                time.sleep(wait)
            else:
                print(f"  搜索失败 {code}: {body[:200]!r}")
                return out
        else:
            return out
        items = json.loads(body).get("items", [])
        out.extend(items)
        if len(items) < 100:
            break
        time.sleep(2.5 if os.environ.get("GITHUB_TOKEN") else 7)
    return out


def cmd_fetch(_args):
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=CREATED_WITHIN_DAYS)).strftime("%Y-%m-%d")
    pushed = (now - timedelta(days=PUSHED_WITHIN_DAYS)).strftime("%Y-%m-%d")
    pages = 3 if os.environ.get("GITHUB_TOKEN") else 1
    if not os.environ.get("GITHUB_TOKEN"):
        print("提示：未设置 GITHUB_TOKEN，每个查询只取第一页，且需要较长等待。")

    repos = load(REPOS_F, {})
    history = load(HISTORY_F, {})
    seen_now = set()
    hot_created = (now - timedelta(days=HOT_CREATED_WITHIN_DAYS)).strftime("%Y-%m-%d")
    # 潜力股：AI 相关、100~3000 星；爆款：3000 星以上的近期新项目（不限 AI）
    jobs = [(q, f"{MIN_STARS}..{MAX_STARS}", created) for q in QUERIES]
    jobs += [(q, f">{MAX_STARS}", hot_created if not q else created) for q in HOT_QUERIES]
    for i, (q, stars, since) in enumerate(jobs, 1):
        full_q = f"{q} stars:{stars} created:>{since} pushed:>{pushed} fork:false archived:false".strip()
        items = search(full_q, pages)
        print(f"[{i}/{len(jobs)}] {q or '全部新项目'} ({stars}星): {len(items)} 个")
        for it in items:
            name = it["full_name"]
            seen_now.add(name)
            prev = repos.get(name, {})
            repos[name] = {
                "full_name": name,
                "url": it["html_url"],
                "description": it.get("description") or "",
                "stars": it["stargazers_count"],
                "forks": it["forks_count"],
                "language": it.get("language") or "",
                "topics": it.get("topics", []),
                "created_at": it["created_at"],
                "pushed_at": it["pushed_at"],
                "homepage": it.get("homepage") or "",
                "owner_avatar": it["owner"]["avatar_url"],
                "first_seen": prev.get("first_seen", today()),
                "last_seen": today(),
            }
            history.setdefault(name, {})[today()] = it["stargazers_count"]
        time.sleep(2.5 if os.environ.get("GITHUB_TOKEN") else 7)

    # 30 天没再出现在搜索结果里的项目清理掉（写过中文介绍的保留），防止数据无限变大
    stale = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    keep = load(SUMMARY_F, {})
    for name in [n for n, r in repos.items() if r["last_seen"] < stale and n not in keep]:
        repos.pop(name)
        history.pop(name, None)

    save(REPOS_F, repos)
    save(HISTORY_F, history)
    print(f"完成：本次命中 {len(seen_now)} 个，累计 {len(repos)} 个项目。")


# ---------------- 2a. 免费翻译描述（无需密钥）----------------
TRANS_F = DATA / "translations.json"   # {英文描述: 中文}


def google_translate(text: str) -> str | None:
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        return "".join(seg[0] for seg in data[0] if seg and seg[0])
    except Exception:
        return None


def cmd_translate(_args):
    from concurrent.futures import ThreadPoolExecutor

    import re
    has_zh = lambda s: bool(re.search(r"[一-鿿]", s))
    repos = load(REPOS_F, {})
    # 之前没翻成中文的(批量翻译时语言识别错)丢掉重翻
    trans = {k: v for k, v in load(TRANS_F, {}).items() if has_zh(v) or has_zh(k)}
    todo = sorted({r["description"].strip() for r in repos.values()
                   if r["description"].strip() and r["description"].strip() not in trans})
    print(f"待翻译描述：{len(todo)} 条")

    def work(batch: list[str]):
        # 多条用换行拼在一起翻，行数对不上就逐条翻
        clean = [t.replace("\n", " ") for t in batch]
        out = google_translate("\n".join(clean))
        lines = out.split("\n") if out else []
        res = {}
        if len(lines) == len(batch):
            res = {t: l.strip() for t, l in zip(batch, lines) if has_zh(l) or has_zh(t)}
        for t, c in zip(batch, clean):  # 批量没翻好的逐条补翻
            if t not in res and (z := google_translate(c)) and has_zh(z):
                res[t] = z.strip()
        return res

    batches = [todo[i:i + 20] for i in range(0, len(todo), 20)]
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for res in pool.map(work, batches):
            trans.update(res)
            done += len(res)
            if done and done % 200 < 20:
                print(f"  已翻译 {done}/{len(todo)}")
                save(TRANS_F, trans)
    save(TRANS_F, trans)
    print(f"翻译完成：{done}/{len(todo)} 条（失败的下次运行会重试）")


# ---------------- 2b. Claude 深度中文摘要（可选，需要密钥）----------------
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "one_liner": {"type": "string", "description": "一句话说清这是什么，20~40字"},
        "problem": {"type": "string", "description": "它解决了什么痛点/问题，2~3句"},
        "features": {"type": "array", "items": {"type": "string"}, "description": "3~5个核心功能或亮点"},
        "audience": {"type": "string", "description": "适合什么人用"},
        "video_angle": {"type": "string", "description": "做短视频的切入点/标题思路，1~2句"},
        "category": {"type": "string", "enum": CATEGORIES},
        "ease": {"type": "string", "enum": ["开箱即用", "需要一定配置", "偏开发者/研究"],
                 "description": "普通人上手难度"},
    },
    "required": ["one_liner", "problem", "features", "audience", "video_angle", "category", "ease"],
    "additionalProperties": False,
}

SYSTEM = (
    "你是一名科技视频博主的研究助理。根据 GitHub 项目的元数据和 README，"
    "用简体中文给出准确、具体、不夸大的介绍。只依据提供的材料，不确定的地方不要编造。"
)


def fetch_readme(full_name: str) -> str:
    for fname in ("README.md", "readme.md", "README_EN.md", "README.en.md", "README.rst", "README"):
        url = f"https://raw.githubusercontent.com/{full_name}/HEAD/{fname}"
        code, body, _ = gh_get(url, accept="text/plain")
        if code == 200 and body:
            return body.decode("utf-8", errors="ignore")
    return ""


def cmd_summarize(args):
    try:
        import anthropic
    except ImportError:
        sys.exit("请先安装依赖：pip install -r requirements.txt")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("未设置 ANTHROPIC_API_KEY，跳过摘要生成。")
        return
    client = anthropic.Anthropic()
    model = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

    repos = drop_spam(load(REPOS_F, {}))
    summaries = load(SUMMARY_F, {})
    history = load(HISTORY_F, {})
    rows = [enrich(r, history) for r in repos.values()]
    todo = [r for r in sorted(rows, key=lambda r: -r["score"])
            if r["full_name"] not in summaries][: args.n]
    print(f"待生成摘要：{len(todo)} 个（模型 {model}）")

    for i, r in enumerate(todo, 1):
        name = r["full_name"]
        readme = fetch_readme(name)[:30000]
        prompt = (
            f"项目：{name}\n描述：{r['description']}\n语言：{r['language']}\n"
            f"Topics：{', '.join(r['topics'])}\n星数：{r['stars']}\n主页：{r['homepage']}\n\n"
            f"<readme>\n{readme or '(无 README)'}\n</readme>"
        )
        try:
            resp = client.beta.messages.create(
                model=model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError:
            print("  Claude 限速，等待 30s 后跳过本条")
            time.sleep(30)
            continue
        except anthropic.APIStatusError as e:
            print(f"  {name} 调用失败：{e.status_code} {e.message}")
            continue
        except anthropic.APIConnectionError as e:
            print(f"  {name} 网络错误：{e}")
            continue
        if resp.stop_reason in ("refusal", "max_tokens"):
            print(f"  {name} 未完成（{resp.stop_reason}），跳过")
            continue
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"  {name} 返回非 JSON，跳过")
            continue
        data["summarized_at"] = today()
        summaries[name] = data
        save(SUMMARY_F, summaries)  # 每条都存，中断也不丢
        print(f"[{i}/{len(todo)}] {name}: {data['one_liner']}")


# ---------------- 3. 计算指标 & 生成网页数据 ----------------
def enrich(r: dict, history: dict) -> dict:
    now = datetime.now(timezone.utc)
    created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
    age = max(1.0, (now - created).total_seconds() / 86400)
    per_day = r["stars"] / age

    # 用快照算近 7 天增长（快照不足时用最早的快照，再不足则为 None）
    snaps = sorted(history.get(r["full_name"], {}).items())
    growth_7d = None
    if len(snaps) >= 2:
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        base_day, base_stars = next(((d, s) for d, s in snaps if d >= cutoff), snaps[0])
        if base_day == snaps[-1][0]:
            base_day, base_stars = snaps[-2]
        days = max(1, (datetime.fromisoformat(snaps[-1][0]) - datetime.fromisoformat(base_day)).days)
        growth_7d = round((snaps[-1][1] - base_stars) / days * 7)

    # 潜力分：增长速度为主，越"年轻"越加分
    speed = (growth_7d / 7) if growth_7d is not None else per_day
    youth = 1.5 if age < 30 else 1.2 if age < 90 else 1.0
    score = speed * youth

    return {**r, "tier": "hot" if r["stars"] > MAX_STARS else "rising",
            "age_days": round(age), "stars_per_day": round(per_day, 1),
            "growth_7d": growth_7d, "score": round(score, 2),
            "sparkline": [s for _, s in snaps[-30:]]}


# 顶部大分类：按顺序匹配，命中第一个即归类（英文词按单词边界匹配，中文按包含匹配）
BROAD = [
    ("🎮 游戏娱乐", r"\bgames?\b|gaming|\bunity\b|godot|minecraft|游戏|companion|伴侣|vtuber|live2d|二次元|anime|roleplay|角色扮演|恶搞|\bfun\b|\bmeme"),
    ("🎬 视频创作", r"\bvideos?\b|视频|短剧|漫剧|\bfilms?\b|电影|movie|animation|动画|manim|remotion|\bmotion\b|剪辑|youtube|直播|stream|storyboard|分镜|漫画|comic|manga"),
    ("🗣️ 语音音乐", r"\btts\b|text-to-speech|\bspeech\b|\bvoice\b|语音|\baudio\b|音频|\bmusic\b|音乐|whisper|字幕|同传|唱歌|\bsing\b|podcast"),
    ("🎨 设计绘图", r"\bdesign\b|设计|\bimages?\b|图像|图片|绘图|diffusion|comfyui|\bicons?\b|图标|\bui\b|figma|\b3d\b|blender|海报|\bsvg\b|logo|canva|服装|穿搭"),
    ("🔒 安全逆向", r"security|pentest|渗透|reverse.engineer|逆向|\bctf\b|vulnerab|漏洞|malware|exploit|red.team|安全"),
    ("🧠 模型与算力", r"inference|推理|llama\.cpp|gguf|quantiz|量化|\bgpus?\b|cuda|显卡|vllm|fine-?tun|微调|\btraining\b|训练|\bmoe\b|benchmark|评测|\bmodels?\b.*(weights|serving)|trainium|显存"),
    ("💼 办公效率", r"meeting|会议|\boffice\b|excel|\bppt|slides|幻灯片|\bemail|邮件|\bpdf\b|productivity|效率|kanban|看板|calendar|日程|notion|translat|翻译|简历|resume|\bdocs?\b|文档|files?\b|文件|launcher|\bsearch\b|搜索|\bchat\b|slack"),
    ("📚 知识学习", r"\brag\b|knowledge|知识|\bwiki\b|\bpapers?\b|论文|research|研究|\blearn|学习|awesome|education|教育|notes|笔记|obsidian|memory|记忆|law|法律|ontology"),
    ("💻 编程开发", r"\bcod(e|ing)\b|编程|代码|developer|开发者|\bide\b|cursor|claude.code|codex|\bcli\b|\bsdk\b|devops|github|\brust\b|python|typescript|swift|sandbox|沙箱|\bapp\b|应用"),
    ("🤖 AI 智能体", r"agents?\b|智能体|代理|\bmcp\b|automation|自动化|phone|手机|android|iphone|browser|浏览器|assistant|助手|workflow|skills?\b"),
]


def broad_category(r: dict, summary: dict | None) -> str:
    import re
    if summary and summary.get("broad"):  # 人工/AI 写介绍时指定的分类优先
        return summary["broad"]
    hay = " ".join([r["full_name"].replace("-", " "), r["description"], r.get("desc_zh", ""),
                    " ".join(t.replace("-", " ") for t in r["topics"])]).lower()
    # 统计每个分类命中了几个不同关键词，取最多的；平局按列表顺序
    best, best_n = "✨ 其他", 0
    for name, pat in BROAD:
        n = len({m.group(0) for m in re.finditer(pat, hay)})
        if n > best_n:
            best, best_n = name, n
    return best


def drop_spam(repos: dict) -> dict:
    """去掉刷星的垃圾仓库：同一段描述被 3 个以上仓库复用、或仓库名像 xxx--ai 的批量克隆。"""
    from collections import Counter
    import re
    cnt = Counter(r["description"].strip().lower() for r in repos.values() if r["description"].strip())
    # 手动拉黑（疑似恶意软件诱饵等），一行一个 owner/repo
    block = set(load(DATA / "blocklist.json", []))
    def bad(r):
        d = r["description"].strip().lower()
        return (r["full_name"] in block or (d and cnt[d] >= 3)
                or re.search(r"--(ai|manus)$|activator", r["full_name"].lower()))
    return {k: r for k, r in repos.items() if not bad(r)}


def cmd_build(_args):
    repos = drop_spam(load(REPOS_F, {}))
    history = load(HISTORY_F, {})
    summaries = load(SUMMARY_F, {})
    trans = load(TRANS_F, {})
    rows = []
    for r in repos.values():
        e = enrich(r, history)
        e["summary"] = summaries.get(r["full_name"])
        e["desc_zh"] = trans.get(r["description"].strip(), "")
        e["broad"] = broad_category(e, e["summary"])
        rows.append(e)
    rows.sort(key=lambda r: -r["score"])
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "max_stars": MAX_STARS, "categories": CATEGORIES,
               "broad": [b for b, _ in BROAD] + ["✨ 其他"], "repos": rows}
    SITE.mkdir(exist_ok=True)
    (SITE / "data.js").write_text(
        "window.RADAR = " + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8")
    print(f"已生成 site/data.js（{len(rows)} 个项目，{sum(1 for r in rows if r['summary'])} 个有中文摘要）")
    print(f"打开：{(SITE / 'index.html').resolve()}")


def cmd_serve(_args):
    """本地网页服务。网页上点"立即更新"会在后台执行 `radar.py all`，并实时显示进度。"""
    import functools
    import http.server
    import subprocess
    import threading

    job = {"running": False, "log": [], "started": None, "finished": None, "code": None}
    lock = threading.Lock()

    def run_job(n: int):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "radar.py"), "all", "-n", str(n)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            with lock:
                job["log"].append(line.rstrip())
                job["log"] = job["log"][-300:]
        proc.wait()
        with lock:
            job.update(running=False, code=proc.returncode,
                       finished=datetime.now().strftime("%H:%M:%S"))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")  # data.js 更新后刷新即可看到
            super().end_headers()

        def do_GET(self):
            if self.path.startswith("/api/status"):
                with lock:
                    return self._json({**job, "has_github_token": bool(os.environ.get("GITHUB_TOKEN")),
                                       "has_claude_key": bool(os.environ.get("ANTHROPIC_API_KEY"))})
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/api/run"):
                with lock:
                    if job["running"]:
                        return self._json({"ok": False, "msg": "已经在运行中"}, 409)
                    job.update(running=True, log=[], code=None, finished=None,
                               started=datetime.now().strftime("%H:%M:%S"))
                threading.Thread(target=run_job, args=(40,), daemon=True).start()
                return self._json({"ok": True})
            self.send_error(404)

        def log_message(self, *a):  # 不刷屏
            pass

    handler = functools.partial(Handler, directory=str(SITE))
    print("打开 http://localhost:8000 （Ctrl+C 退出）")
    http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler).serve_forever()


def main():
    p = argparse.ArgumentParser(description="AI 潜力项目雷达")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    s = sub.add_parser("summarize"); s.add_argument("-n", type=int, default=40, help="本次最多生成几条")
    sub.add_parser("translate")
    sub.add_parser("build")
    a = sub.add_parser("all"); a.add_argument("-n", type=int, default=40)
    sub.add_parser("serve")
    args = p.parse_args()
    if args.cmd == "all":
        cmd_fetch(args); cmd_translate(args); cmd_summarize(args); cmd_build(args)
    else:
        {"fetch": cmd_fetch, "translate": cmd_translate, "summarize": cmd_summarize,
         "build": cmd_build, "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
