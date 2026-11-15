# AI 潜力项目雷达

自动从 GitHub 抓取 **100~3000 星、半年内新建、最近一个月还在更新** 的 AI 项目，
每天记录星数快照计算增长速度，用 Claude 生成中文介绍（是什么 / 解决什么问题 / 适合谁 / 视频切入点），
输出一个可搜索、可筛选的网页，方便挑选做视频的项目。

## 最简单的用法（不需要任何密钥）

双击 **`启动雷达.bat`** → 浏览器自动打开 → 点右上角 **「立即更新数据」**。
项目描述会自动翻译成中文。下面的密钥都是可选的。

## 快速开始（命令行）

```bash
pip install -r requirements.txt
```

PowerShell 中设置密钥（GitHub Token 在 GitHub → Settings → Developer settings → Personal access tokens 创建，不需要任何权限）：

```powershell
$env:GITHUB_TOKEN="ghp_xxx"
$env:ANTHROPIC_API_KEY="sk-ant-xxx"
```

```bash
python radar.py all
```

然后双击打开 `site/index.html`。

## 命令

| 命令 | 作用 |
|---|---|
| `python radar.py fetch` | 抓取项目并记录今天的星数快照 |
| `python radar.py summarize -n 40` | 给潜力分最高、还没摘要的 40 个项目生成中文介绍 |
| `python radar.py build` | 生成网页数据 `site/data.js` |
| `python radar.py all -n 40` | 三步一起跑 |

## 潜力分怎么算

- 有 ≥2 天快照：用 **近 7 天增星速度**；否则用 **总星数 / 项目天数**
- 30 天内创建 ×1.5，90 天内 ×1.2
- 超过 3000 星（已经火了）×0.3

所以 **每天跑一次** 数据才会越来越准，建议用 Windows 任务计划程序每天执行 `run_daily.bat`。

## 调整范围

改 `radar.py` 顶部的 `MIN_STARS / MAX_STARS / CREATED_WITHIN_DAYS / QUERIES` 即可。
