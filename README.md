# AI风月 聊天记录批量导出工具 
把 **AI风月**账号下「历史游玩记录 + 会话列表」里的**全部聊天记录**一键导出。

## 两种运行方式

### 方式一：双击 exe（推荐）

下载 `aixan-exporter.exe`，双击打开图形界面，填邮箱+密码（或直接填 token），选输出目录，点「开始导出」。

- 进度条实时显示扫描/导出进度，限速等待时每秒倒计时
- 可随时「取消」，已完成的部分会保留
- 设置自动记住，下次打开无需重填
- 已下载的会话自动跳过，可勾选「跳过上次已抓取的应用」做增量备份

[GUI 界面预览]
<img width="1361" height="1267" alt="屏幕截图 2026-09-08 000237" src="https://github.com/user-attachments/assets/ba5d605a-e3b2-45ac-9c8f-59cec14ac043" />

### 方式二：命令行（Python 脚本）

```bash
# 图形界面（需带 tkinter 的 Python，如官方安装版 3.11+）
py -3.11 export_ai_xan.py --gui

# 账号密码
python3 export_ai_xan.py --email 你的邮箱 --password 你的密码

# 已有 token（浏览器 localStorage 的 console_token）
python3 export_ai_xan.py --token {你的token}

# 只导出某个应用（appId 取页面网址最后一段 uuid）
python3 export_ai_xan.py --email x@x.com --password xxx --only-app {uuid}

# 常用组合
python3 export_ai_xan.py --email x@x.com --password xxx \
    --out ./out --workers 6 --include-json --skip-fetched
```

## 命令行参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `--gui` | 打开图形界面 | — |
| `--email EMAIL` | 登录邮箱 | 或环境变量 `AIXAN_EMAIL` |
| `--password PASSWORD` | 登录密码 | 或环境变量 `AIXAN_PASSWORD` |
| `--token TOKEN` | 直接用 token（跳过登录） | 或环境变量 `AIXAN_TOKEN` |
| `--out DIR` | 输出目录 | `./ai-xan-export-<日期>` |
| `--workers N` | 并发下载数 | 4 |
| `--sleep-min SEC` | 每次请求前随机等待下限 | 10 |
| `--sleep-max SEC` | 每次请求前随机等待上限 | 30（两个都设 0 = 关闭） |
| `--only-app ID` | 只导出指定 app（可多次指定） | — |
| `--include-json` | 每个会话额外存原始 JSON | 关 |
| `--overwrite` | 强制重新下载已存在的文件 | 关 |
| `--skip-fetched` | 跳过上次已成功抓取过会话列表的应用（增量模式） | 关 |
| `--domain URL` | 域名/镜像; `auto`=自动 ping 选延迟最低(默认), 可指定或自定义 | `auto` |
| `--no-token-cache` | 不把 token 缓存到输出目录 | 关 |

## 原理

完全复用网站自己的接口（不抓页面、不模拟点击）：

| 你要导出的内容 | 网站界面位置 | 实际接口 |
|---|---|---|
| 玩过的应用列表 | 侧边栏「历史游玩记录」按钮 | `GET /console/api/used-installed-apps` |
| 某应用的会话列表 | 聊天页「会话列表」（已置顶 / 对话列表） | `GET /console/api/installed-apps/{appId}/conversations`（两 tab 合并去重） |
| 单会话内容 | 聊天页右上角菜单「导出记录」按钮 | `GET /console/api/installed-apps/{appId}/messages/export?conversation_id=…` |

导出文件格式与网页「导出记录」按钮生成的文件**逐字节一致**：
`[USER]:提问` / `#----#` 分隔 / `[AI]:回答` / `#====#` 分隔轮次，UTF-8 带 BOM，
可以直接再通过网页「导入历史」功能导回去。

## 输出结构

```
out/
├── index.json            # 总索引（账号、应用、会话、文件路径）
├── export.log            # 运行日志
├── errors.log            # 失败记录
├── history.json          # 拉取/下载历史（自动维护，用于自动跳过已下载和增量续跑）
└── <应用名>/
    ├── __ALL__<应用名>.txt          # 该应用全部会话合并版
    ├── 会话名_时间戳_uuid8.txt       # 每个会话一个文件
    └── *.json                        # 加 --include-json 时的原始数据
```

## 防封号说明

每次网络请求前随机等待 10~30 秒（所有线程共享同一个全局限速器排队放行），任意两次请求之间至少间隔约 10 秒。可用 `--sleep-min` / `--sleep-max` 调整；两个都设 0 可关闭。

> ⏱️ 批量导出很慢（500 个会话 × 平均 20s ≈ 2.8 小时），想快可调小等待范围或用 `--only-app` 分批导出。

## 历史记录与自动跳过

输出目录下的 `history.json` 自动记录两类历史：

- **拉取历史** `apps`：每个应用最近一次成功抓取会话列表的时间与完整会话清单
- **下载历史** `convs`：每个会话已下载到的文件、消息数、下载时间

重跑时已下载的会话不再联网（也不计入限速等待）。勾选「跳过上次已抓取的应用」（`--skip-fetched`）后，上次成功抓取过会话列表的应用也不重新抓取，直接用历史清单补下载漏掉的会话 —— 适合经常重跑做增量备份。

> **注意**：自动跳过意味着正在继续聊的旧会话不会更新。要重下某个会话，勾选「强制重下已存在的」（`--overwrite`）。想发现新开的对话，取消勾选「跳过已抓取」跑一次。

## 镜像域名

程序内置多个镜像域名（同一网站，内容完全相同，仅入口域名不同）。开始导出时自动 ping 全部镜像并选择延迟最低的：

- **GUI**：「域名」下拉框选 `auto`（自动选最快），或直接选某个镜像；也可**手动输入任意域名**（自定义添加）
- **CLI**：`--domain auto`（默认），或 `--domain https://ai-xan.xyz` 指定
- GUI 点「测速」按钮可随时查看各镜像实时延迟，自动把最快的填入下拉框

## 隐私与安全 🔒

- **完全开源**：全部代码就是仓库中的 `export_ai_xan.py`，无任何隐藏逻辑，可自行审查。
- **不会保存、不会传输任何个人文件、账号、密码**：程序只在运行时用你输入的邮箱密码登录一次；密码仅存在于内存、直接发给官网登录接口，不写入任何文件。
- **只与你选择的域名通信**：所有网络请求只发给 ai-xan.xyz 及其同站镜像，不连接任何第三方服务器，无遥测、无统计、无更新检查。
- GUI 只记住邮箱与 token（官网签发的登录凭证，约 30 天有效），存于 `~/.aixan_export_gui.json` 与输出目录 `.token.json`，删除即失效，随时可手动删除。

## 注意

- 仅限导出**你自己的账号**数据；请妥善保管导出的内容与密码。
- 并发数别开太大（默认 4），对服务器礼貌一点。
- exe 版本无需安装 Python，双击即用；脚本版仅需 Python 3 标准库，无需第三方包。
