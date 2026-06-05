# 🚀 个人 QQ Bot

基于 NoneBot2 + NapCatQQ 的 QQ 机器人，支持互动娱乐、实用工具及音游成绩查询。

角色设定：**冷淡猫娘**（傲娇、不耐烦但会帮忙）。

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.11 |
| 框架 | [NoneBot2](https://nonebot.dev/)（异步 / FastAPI 驱动） |
| 协议 | [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（OneBot V11） |
| 部署 | Docker Compose（Ubuntu Server） |

---

## 🌐 架构

```
NapCatQQ  ──WebSocket──▶  NoneBot2 (:8081)  ──▶  插件
(容器)      (反向WS)      (容器)
```

- **模式**: 反向 WebSocket — NapCat 主动连接 `ws://bot:8081/onebot/v11/ws`
- **消息格式**: Array
- **时区**: 全部统一为 `Asia/Shanghai`（北京时间）
- **容器网络**: NapCat 与 Bot 在同一 compose 网络内，通过服务名 `bot` 互相访问

---

## ⚡ 部署

### 前置条件

- Docker + Docker Compose Plugin
- 一个有权限的 QQ 号

### 一键部署

```bash
bash deploy.sh
```

按提示输入 QQ 号、管理员 QQ 号等，脚本会自动生成配置、构建镜像并启动两个容器。

### 手动配置 `.env`

```env
ENVIRONMENT=production
HOST=0.0.0.0
PORT=8081
ONEBOT_V11_ACCESS_TOKEN=你的Token

# 每日推送目标群（逗号分隔的群号，留空则不推送）
DAILY_PUSH_GROUPS=123456789

# 天气查询默认城市
WEATHER_CITY=北京

# 管理员 QQ 号（用于 /reload 等管理员指令）
ADMIN_QQ=你的QQ号
```

> ⚠️ NapCat 配置 `napcat/config/onebot11_<QQ号>.json` 中的 `token` 必须与 `.env` 中的 `ONEBOT_V11_ACCESS_TOKEN` 一致。`deploy.sh` 会自动保持同步。

### 扫码登录

部署后浏览器打开 `http://服务器IP:6099/webui/`，点击「登录」，手机 QQ 扫码即可。

### 常用命令

```bash
docker compose up -d --build   # 重新构建并启动（代码改动后）
docker compose up -d --force-recreate   # 仅重建容器不重建镜像（env 改动后）
docker compose logs -f bot     # 查看 bot 日志
docker compose logs -f napcat  # 查看 napcat 日志
docker compose restart         # 重启（不重建）
docker compose down            # 完全停止
```

### 时区说明

两个容器均设置 `TZ=Asia/Shanghai`，所有定时任务和 `/time` 指令均使用北京时间。部署后发 `/time` 可验证。

---

## 🧩 插件功能总览

### 互动

| 触发条件 | 功能 | 插件路径 |
|---|---|---|
| `/help` 或 `/帮助` | 查看帮助菜单 | `src/plugins/help_cmd/` |
| `/ping` | 连通性测试 | `src/plugins/ping_cmd/` |
| 群内只 @ 机器人（无文字） | 联网获取随机猫娘图片，失败则回退到 QQ 内置表情 | `src/plugins/random_face/` |
| 戳一戳 / 双击机器人 | 回复表情 + 文字 | `src/plugins/poke_reply/` |
| 发送未知 `/` 指令 | 兜底回复，提示发送 `/help` | `src/plugins/unknown_cmd/` |

### 工具

| 指令 | 功能 | 插件路径 |
|---|---|---|
| `/time` | 查看当前时间（可用于验证时区配置） | `src/plugins/time_cmd/` |
| `/roll <选项...>` | 从空格分隔的选项中随机选一个（至少 2 个） | `src/plugins/roll/` |
| `/每日运势` | 查看今日运势（同日同人不变化，带分数条） | `src/plugins/fortune/` |
| `/每日天气` | 查询指定城市今日天气 | `src/plugins/daily_weather/` |
| `/每日新闻` | 获取今日头条热榜（带链接） | `src/plugins/daily_news/` |
| `/email`（私聊） | 绑定邮箱，新邮件通知 | `src/plugins/email_notify/` |

### 定时推送

| 推送时间 | 内容 | 插件路径 |
|---|---|---|
| 每天 8:00 | 自动推送当日天气到 `DAILY_PUSH_GROUPS` 中的群 | `src/plugins/daily_weather/` |
| 每天 8:01 | 自动推送今日头条热榜到 `DAILY_PUSH_GROUPS` 中的群 | `src/plugins/daily_news/` |

> 💡 在 `.env` 中配置 `DAILY_PUSH_GROUPS=群号1,群号2` 来指定接收定时推送的群。

### 管理

| 指令 | 功能 | 插件路径 |
|---|---|---|
| `/reload` | 热重启 Bot（仅限管理员，不在帮助中显示） | `src/plugins/reload_cmd/` |

### 🎵 Chunithm 中二节奏（`/chu`）

基于 [lxns.net](https://maimai.lxns.net) API，支持成绩查询与图片生成。

| 子命令 | 功能 |
|---|---|
| `/chu b30bind <token>` | 绑定查分器 API Token |
| `/chu b30unbind` | 解除 Token 绑定 |
| `/chu b30` | 生成 Best 30 成绩图片（评分最高的 30 首曲） |
| `/chu b50` | 生成 Best 50 成绩图片（旧曲 30 + 新曲 20） |
| `/chu 推分` | 随机抽一首有成绩的歌曲，展示曲绘与分数 |
| `/chu 装福` | 随机抽一首单曲评分高于你当前 Rating 的上分曲 |

> 💡 使用成绩功能前需先用 `/chu b30bind <token>` 绑定 Token。
> Token 获取方式见 [lxns.net](https://maimai.lxns.net)。

插件路径: `src/plugins/chunithm_b30/`

---

## 📁 项目结构

```
bot/
├── bot.py                  # 入口
├── pyproject.toml          # NoneBot 插件配置
├── .env                    # 环境变量（Token 等，不提交）
├── .env.example            # .env 模板
├── requirements.txt        # 额外依赖
├── Dockerfile              # Bot 镜像
├── docker-compose.yml      # 服务编排（NapCat + Bot）
├── deploy.sh               # 一键部署脚本
│
├── src/
│   ├── common/__init__.py  # 公共工具（随机延迟防风控、共享调度器、推送辅助）
│   └── plugins/
│       ├── help_cmd/       # 帮助菜单
│       ├── ping_cmd/       # 连通性测试
│       ├── time_cmd/       # 当前时间查询
│       ├── random_face/    # 猫娘表情包（联网）
│       ├── poke_reply/     # 戳一戳回应
│       ├── unknown_cmd/    # 未知指令兜底
│       ├── roll/           # 随机选择
│       ├── fortune/        # 每日运势
│       ├── daily_weather/  # 每日天气（指令 + 定时推送）
│       ├── daily_news/     # 每日新闻（指令 + 定时推送）
│       ├── email_notify/   # 邮件通知
│       ├── reload_cmd/     # 热重启
│       └── chunithm_b30/   # Chunithm 成绩查询
│           ├── __init__.py # 指令处理
│           └── b30_core.py # 图片生成核心逻辑
│
├── data/                   # 持久化数据（Docker 挂载）
│   ├── fortune_data.json   # 运势缓存（按 用户ID_日期 存储）
│   ├── b30_tokens.json     # 用户 Token 存档
│   ├── b30_outputs/        # 成绩图片输出（>1 小时自动清理）
│   └── b30_assets/bg.png   # 成绩图背景
│
├── napcat/                 # 运行时生成（Docker 挂载，不提交）
│   ├── QQ/                 # QQ 登录态
│   ├── config/             # OneBot 配置
│   └── logs/               # 日志
│
└── test_local/             # 本地测试脚本
```

---

## 📦 依赖

`pyproject.toml` 中声明:
- `nonebot2 >= 2.2.0`
- `nonebot-adapter-onebot >= 2.2.0`

`requirements.txt` 中额外声明:
- `httpx` — 猫娘图片 / 天气 / 新闻 API 请求
- `requests` — Chunithm API 请求
- `Pillow` — 成绩图片生成
- `apscheduler` — 定时推送调度

---

## 💻 远程开发

生产代码在 Ubuntu 服务器上，可通过 VSCode Remote-SSH 直接编辑。

1. `Ctrl+Shift+P` → `Remote-SSH: Open SSH Configuration File`
2. 添加：
```
Host qq-bot
    HostName 你的服务器IP
    User ubuntu
```
3. `Ctrl+Shift+P` → `Remote-SSH: Connect to Host` → 选 `qq-bot`
4. 打开 `/opt/qq-bot`

修改后执行 `docker compose up -d --build` 即可生效。

---

## 🐛 常见问题

| 问题 | 原因 / 解决 |
|---|---|
| NapCat 频繁被踢下线 | QQ 反作弊检测。检查手机 QQ 是否频繁切换网络，固定 NapCat 镜像版本，确保仅一个终端在线 |
| `/time` 显示的时间不对 | 容器 TZ 未生效，检查 `docker-compose.yml` 是否设置 `TZ=Asia/Shanghai` |
| 定时推送时间不对 | 同上，重建容器 `docker compose up -d --force-recreate` |
| WebSocket 连接失败 | 检查 `.env` 和 NapCat 配置中的 Token 是否一致 |
| `/chu` 相关指令报错 | 检查 Token 是否已绑定 (`/chu b30bind`) 且未过期 |

---

## 🔮 后续计划

- [ ] Chunithm Recent 最近战绩查询
- [ ] 分数线计算器
- [ ] NapCat 掉线自动通知
