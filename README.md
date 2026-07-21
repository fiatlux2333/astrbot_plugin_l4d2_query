# L4D2 求生之路查询 (astrbot_plugin_l4d2_query)

✨ 基于 AstrBot 的一个插件 ✨

为你的群聊提供 Left 4 Dead 2 服务器查询、订阅管理、Steam 找服、Anne 数据、RCON 控制与事件预约功能。

> **求生之路，与你同行。**

## 📖 介绍

这是一个从 [koishi-plugin-l4d2-query](https://github.com/CatKoishi/koishi-plugin-l4d2-query) 移植到 AstrBot 的 L4D2 服务器查询/管理插件。插件支持服务器详情查询、订阅分组列表、Steam Web API 找服、玩家数据统计、Anne 药役数据库查询、RCON 远程控制，以及群内事件预约系统（含定时提醒）。


## 💿 安装

### 通过 AstrBot 插件市场安装（推荐）

1. 在 AstrBot WebUI 中打开插件市场
2. 搜索 `astrbot_plugin_l4d2_query` 或 `L4D2 求生之路查询`
3. 点击安装

### 手动安装

1. 克隆仓库到 AstrBot 插件目录：

```
cd AstrBot/data/plugins
git clone https://github.com/yourname/astrbot_plugin_l4d2_query
```

2. 安装依赖：

```
cd astrbot_plugin_l4d2_query
pip install -r requirements.txt
playwright install chromium
```

3. 安装中文字体（**Linux 服务器必需**，否则图片中文会显示为方框）：

```
# Debian / Ubuntu
apt install -y fonts-noto-cjk

# RHEL / CentOS
yum install -y google-noto-cjk-fonts

# Alpine
apk add font-noto-cjk
```

> macOS / Windows 系统自带中文字体，无需此步。

4. 在 AstrBot WebUI 的插件管理中启用插件

> ⚠️ **图片渲染功能依赖 Playwright 的 Chromium 浏览器**，必须执行 `playwright install chromium`，否则会自动降级为纯文本输出。

## ⚙️ 配置

在 AstrBot WebUI 的插件配置页面进行配置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `theme_type` | str | `normal` | 图片主题样式，可选 `normal` / `dark` / `neon` / `wind` / `oled` |
| `night_mode` | bool | `false` | 启用夜间模式，按时间段自动切换主题 |
| `night_start` | int | `21` | 夜间模式开始小时（0-23） |
| `night_end` | int | `7` | 夜间模式结束小时（0-23） |
| `night_oled` | bool | `false` | 夜间模式使用 OLED 主题 |
| `list_style` | str | `normal` | 列表输出样式，可选 `normal`（图片卡片）/ `lite`（图片表格）/ `text`（纯文本） |
| `max_show_player` | int | `4` | 图片最大显示人数 |
| `output_ip` | bool | `true` | 详情中输出 connect 地址 |
| `query_limit` | int | `4` | 并发查询限制，查询订阅服列表时的最大并发数 |
| `servers` | list | `[]` | 订阅服务器列表，每一项含 `name`/`group`/`host`/`port`/`rcon_port`/`rcon_password` |
| `steam_web_api` | str | `""` | Steam Web API Key，用于找服和求生数据查询。在 https://steamcommunity.com/dev/apikey 获取 |
| `proxy_url` | str | `""` | HTTP 代理地址，例如 `http://127.0.0.1:7890`；留空表示直连 |
| `use_anne` | bool | `false` | 启用 Anne 数据库查询 |
| `db_host` | str | `127.0.0.1` | Anne 数据库地址 |
| `db_port` | int | `3306` | Anne 数据库端口 |
| `db_user` | str | `""` | Anne 数据库用户名 |
| `db_password` | str | `""` | Anne 数据库密码 |
| `db_name` | str | `""` | Anne 数据库名 |
| `rcon_enabled` | bool | `false` | 启用 RCON，可远程执行服务器命令 |
| `use_event` | bool | `false` | 启用事件预约系统 |
| `event_notice_lead` | int | `30` | 事件提醒提前分钟数 |

### 服务器配置示例

在 `servers` 中填写订阅服务器列表：

```json
[
  {
    "name": "主服",
    "group": "训练",
    "host": "127.0.0.1",
    "port": 27015,
    "rcon_port": -1,
    "rcon_password": ""
  },
  {
    "name": "药役服",
    "group": "药役",
    "host": "example.com",
    "port": 27016,
    "rcon_port": 27016,
    "rcon_password": "your_rcon_pw"
  }
]
```

`group` 字段用于分组，相同 group 归为一组，可通过 `/l4d2 <组名>` 查询。未填 group 的服务器归入默认组。

## 🎁 使用

### 服务器查询

```
/l4d2                          查看帮助
/l4d2 connect <ip[:port]>      查询任意服务器详情（默认端口 27015）
/l4d2 list [组名]               查询订阅服务器列表
/l4d2 server <序号>             查询默认组第 N 台服务器详情
/l4d2 <组名> [序号]             查询某分组列表或详情
```
<img width="360" height="452" alt="0a16632ab7c6db97aec77e247f87b6f5" src="https://github.com/user-attachments/assets/1ea45864-617e-4cda-b2ae-110c19be3663" />


### Steam 找服

```
/l4d2 search [选项]
  -n <名称>    服务器名（支持 * 通配）
  -i <ip>      服务器 IP
  -t <标签>    服务器 tag
  -e           仅找空服
  -a           忽略人数限制
  -m <数量>    最多返回数量（默认 5）
```

示例：`/l4d2 search -n *药役* -e -m 10`

### Steam 玩家数据

```
/l4d2 bind <SteamID>           绑定 SteamID（STEAM_0:1:xxx 或 7656... 形式）
/l4d2 stats [SteamID]          查询 L4D2 玩家统计与伪经验评分
```

绑定后可直接 `/l4d2 stats` 免输入查询。

### Anne 数据库查询

```
/l4d2 anne [玩家名]             查询 Anne 药役玩家数据（分数/排名/时长/标签）
```

需先在配置中启用 `use_anne` 并填写数据库连接信息。

### RCON 远程控制

```
/l4d2 rcon <Nf> <命令>          执行 RCON 命令（需管理员权限）
```

`<Nf>` 格式如 `2f` 表示订阅列表第 2 台服务器。示例：`/l4d2 rcon 2f status`

### 快捷指令（无需 l4d2 前缀）

除了 `/l4d2` 系列指令，插件还提供了以下快捷指令，可直接使用：

```
/服务器                查询订阅服务器列表
/求生数据 [SteamID]    查询玩家统计
/connect <ip[:port]>   查询任意服务器
/rcon <Nf> <命令>       RCON 远程控制（管理员）
/steam绑定 <SteamID>   绑定 SteamID
/找服 [选项]            Steam 找服
/anne查询 [玩家名]      Anne 数据库查询
```

### 事件预约系统

```
/event add <名称> <时间> [人数上限]   创建预约
/event del <序号>                      删除预约
/event chtime <序号> <时间>            修改时间
/event chname <序号> <名称>            修改名称
/event desc <序号> <描述>              修改描述
/event list                           列出本群预约
/event view <序号>                     查看详情
/event join <序号>                     报名（满员进替补）
/event leave <序号>                    退出（替补自动递补）
```

时间格式支持：`YYYY/MM/DD HH:MM`、`YYYY-MM-DD HH:MM`、`MM/DD HH:MM`。事件开始前 30 分钟（可配置 `event_notice_lead`）自动群内提醒。

## 📋 依赖

- `python-a2s>=1.3.0` - Valve Source 服务器查询协议库
- `aiohttp>=3.8.0` - 异步 HTTP 请求库（Steam Web API）
- `aiomysql>=0.2.0` - 异步 MySQL 驱动（Anne 数据库）
- `jinja2>=3.1.0` - HTML 模板渲染引擎
- `playwright>=1.40.0` - 浏览器自动化，用于 HTML 转图片
- `rcon>=2.0` - Source RCON 协议实现

安装 Playwright 浏览器：

```
playwright install chromium
```

## 🖋 字体说明

本插件渲染服务器列表图片时使用 **Noto Sans SC**（通过 Google Fonts 加载）作为中文字体，并保留系统字体回退链（Microsoft YaHei、PingFang SC、SimSun 等）以保证跨平台一致性。

> 字体安装已在「安装」章节中提前说明，请务必在安装阶段完成。Linux 服务器若未安装中文字体，图片中的中文会显示为方框（□）。

## ⚠️ 注意事项

1. **Steam Web API Key**：找服（`/l4d2 search`）和求生数据（`/l4d2 stats`）功能需要配置 Steam Web API Key，在 https://steamcommunity.com/dev/apikey 获取
2. **Anne 数据库**：Anne 查询（`/l4d2 anne`）需要自建或可访问的 Anne 数据库，配置 `use_anne` 并填写数据库连接信息
3. **RCON 权限**：RCON 命令需要管理员权限，且需在配置中启用 `rcon_enabled`。部分服务器 RCON 监听在 loopback，可能需要端口转发
4. **网络环境**：Steam Web API 访问可能需要代理，可在 `proxy_url` 中配置 HTTP 代理地址
5. **主动消息限制**：事件预约的定时提醒通过主动消息发送，部分平台（如 qq_official）可能不支持主动推送，提醒会静默失败

## 🛠️ 技术实现

- 使用 **python-a2s** 通过 Valve Source Query 协议查询服务器信息（info/players/rules）
- 使用 **Playwright + Jinja2** 进行 HTML 到图片的转换，支持 5 套主题与夜间模式
- 使用 **aiohttp** 异步调用 Steam Web API 进行服务器搜索与玩家统计
- 使用 **aiomysql** 连接池查询 Anne 数据库，计算玩家排名
- 使用 **rcon** 库通过 asyncio.to_thread 异步执行 RCON 命令，避免阻塞事件循环
- 事件预约系统使用 JSON 持久化 + asyncio 定时循环，支持报名/替补/自动递补

## 📝 功能特性

- 🎮 **服务器查询** - A2S 协议查询任意 L4D2 服务器详情与玩家列表
- 📋 **订阅管理** - 分组管理订阅服务器，支持图片卡片/表格/纯文本输出
- 🎨 **主题样式** - 5 套主题 + 自动夜间模式，3 种列表样式
- 🔍 **Steam 找服** - 通过名称/IP/标签/空服等条件搜索服务器
- 📊 **玩家统计** - 查询 L4D2 游戏数据与伪经验评分
- 🗄️ **Anne 查询** - 查询 Anne 药役数据库玩家分数与排名
- 🔧 **RCON 控制** - 远程执行服务器命令（管理员权限）
- 📅 **事件预约** - 群内游戏预约，支持报名/替补/定时提醒

## 📄 许可证

本项目继承原项目 [koishi-plugin-l4d2-query](https://github.com/CatKoishi/koishi-plugin-l4d2-query) 的 **GPL-3.0** 许可证，详见仓库根目录的 [LICENSE](./LICENSE) 文件。

## ❤ 致谢

- [koishi-plugin-l4d2-query](https://github.com/CatKoishi/koishi-plugin-l4d2-query) - 原始项目，由 [NyaKoishi](https://github.com/CatKoishi) 开发
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) - 优秀的机器人框架
- [python-a2s](https://github.com/Yepoleb/python-a2s) - Python A2S 协议实现
- [Playwright](https://playwright.dev/) - 浏览器自动化方案

## 📮 反馈与建议

如有问题或建议，欢迎提交 Issue 或 Pull Request！
