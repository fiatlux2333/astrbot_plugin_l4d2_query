"""astrbot_plugin_l4d2_query — 主入口

从 koishi-plugin-l4d2-query 全量移植到 AstrBot 的 L4D2 服务器查询/管理插件。

指令:
  /l4d2                        查看帮助
  /l4d2 connect <ip[:port]>    查询任意服务器详情
  /l4d2 list [组名]             查询订阅服务器列表
  /l4d2 server <序号>           查询默认组某台服务器详情
  /l4d2 <组名> [序号]           查询某分组列表或详情
  /l4d2 search [选项]           Steam Web API 找服
  /l4d2 bind <SteamID>         绑定 SteamID
  /l4d2 stats [SteamID]        查询求生数据
  /l4d2 anne [玩家名]           查询 Anne 数据库
  /l4d2 rcon <Nf> <命令>        执行 RCON（管理员）
  /event add/del/chtime/...    事件预约系统
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import Any, AsyncGenerator, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig

from .anne import AnneDB
from .event import EventManager
from .query import QueryEngine, get_cfg_name
from .render import Renderer
from .steam import SteamAPI
from .utils import (
    PlatformUser,
    QueryResult,
    Reservation,
    ServerConfig,
    RESERVED_WORDS,
    THEMES,
    format_datetime,
    format_online_time,
    format_playtime,
    get_data_dir,
    get_servers_from_config,
    group_servers,
    is_valid_steamid,
    normalize_to_steamid64,
    parse_rcon_target,
    parse_server_addr,
    resolve_theme,
    steamid64_to_steamid2,
)

HELP_TEXT = """\
L4D2 求生之路查询 v1.0
━━━━━━━━━━━━━━━━━━━━
/l4d2 connect <ip[:port]>  查询任意服务器
/l4d2 list [组名]           订阅服务器列表
/l4d2 server <序号>         查询默认组第N台
/l4d2 <组名> [序号]         查询某分组
/l4d2 search [选项]         Steam找服
  -n 名称 -i IP -t 标签 -e空服 -a忽略限制 -m数量
/l4d2 bind <SteamID>        绑定SteamID
/l4d2 stats [SteamID]       求生数据
/l4d2 anne [玩家名]          Anne查询
/l4d2 rcon <Nf> <命令>       RCON(管理员)
━━━━━━━━━━━━━━━━━━━━
快捷指令(无需l4d2前缀):
/服务器          订阅服列表
/求生数据        玩家统计
/connect <ip>    查询服务器
/rcon <Nf> <命令> RCON控制
/steam绑定 <ID>  绑定SteamID
/找服 [选项]      Steam找服
/anne查询 [名字]  Anne查询"""

EVENT_HELP_TEXT = """\
事件预约系统
━━━━━━━━━━━━
/event add <名称> <时间> [人数]   创建预约
/event del <序号>                  删除预约
/event chtime <序号> <时间>        修改时间
/event chname <序号> <名称>        修改名称
/event desc <序号> <描述>          修改描述
/event list                       列出本群预约
/event view <序号>                 查看详情
/event join <序号>                 报名(满进替补)
/event leave <序号>                退出(替补递补)
━━━━━━━━━━━━
时间格式: YYYY/MM/DD HH:MM 或 MM/DD HH:MM"""


class L4D2QueryPlugin(Star):
    """L4D2 服务器查询/管理插件。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.data_dir = get_data_dir()
        # 子模块
        self._query: Optional[QueryEngine] = None
        self._steam: Optional[SteamAPI] = None
        self._anne: Optional[AnneDB] = None
        self._renderer: Optional[Renderer] = None
        self._events: Optional[EventManager] = None
        # 绑定文件锁
        self._bind_lock = asyncio.Lock()
        self._bind_path = os.path.join(self.data_dir, "steam_bindings.json")

    # ==================================================================
    # 生命周期 / Lifecycle
    # ==================================================================
    async def initialize(self) -> None:
        """初始化各子模块，各自 try/except 降级。"""
        # 查询引擎
        try:
            self._query = QueryEngine(
                timeout=1.0,
                concurrency=int(self._cfg("query_limit", 4)),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"QueryEngine 初始化失败: {e}")

        # Steam API
        try:
            self._steam = SteamAPI(
                api_key=self._cfg("steam_web_api", ""),
                proxy_url=self._cfg("proxy_url", ""),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"SteamAPI 初始化失败: {e}")

        # Anne 数据库
        if self._cfg("use_anne", False):
            try:
                self._anne = AnneDB(
                    host=self._cfg("db_host", "127.0.0.1"),
                    port=int(self._cfg("db_port", 3306)),
                    user=self._cfg("db_user", ""),
                    password=self._cfg("db_password", ""),
                    db=self._cfg("db_name", ""),
                )
                await self._anne.connect()
                logger.info("Anne 数据库连接成功")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Anne 数据库连接失败，anne 功能将不可用: {e}")
                self._anne = None

        # 图片渲染
        try:
            self._renderer = Renderer()
            await self._renderer.start()
            logger.info("playwright 渲染器启动成功")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"playwright 启动失败，将降级为纯文本: {e}")
            self._renderer = None

        # 事件系统
        if self._cfg("use_event", False):
            try:
                self._events = EventManager(
                    context=self.context,
                    data_dir=self.data_dir,
                    notice_lead_min=int(self._cfg("event_notice_lead", 30)),
                )
                await self._events.start()
                logger.info("事件预约系统已启动")
            except Exception as e:  # noqa: BLE001
                logger.error(f"事件系统启动失败: {e}")
                self._events = None

    async def terminate(self) -> None:
        """逆序关闭子模块。"""
        if self._events:
            try:
                await self._events.stop()
            except Exception:
                pass
        if self._renderer:
            try:
                await self._renderer.stop()
            except Exception:
                pass
        if self._anne:
            try:
                await self._anne.close()
            except Exception:
                pass

    # ==================================================================
    # l4d2 主指令（单一入口 + 手动分发）
    # ==================================================================
    @filter.command("l4d2")
    async def l4d2(self, event: AstrMessageEvent):
        """L4D2 服务器查询与管理"""
        text = event.message_str.strip()
        # 去掉指令名 l4d2（可能在开头，大小写不敏感，可能带 /）
        text = self._strip_cmd(text, "l4d2")
        parts = text.split(maxsplit=2) if text else []
        sub = parts[0].lower() if parts else "help"
        rest = text[len(parts[0]):].strip() if parts else ""

        dispatch = {
            "connect": self._cmd_connect,
            "list": self._cmd_list,
            "server": self._cmd_server,
            "search": self._cmd_search,
            "bind": self._cmd_bind,
            "stats": self._cmd_stats,
            "anne": self._cmd_anne,
            "rcon": self._cmd_rcon,
            "help": self._cmd_help,
        }

        handler = dispatch.get(sub)
        if handler is not None:
            async for r in handler(event, rest):
                yield r
        elif sub in self._group_names():
            # 组名查询：l4d2 <组名> [序号]
            idx_str = rest.strip() if rest else ""
            async for r in self._cmd_group_query(event, sub, idx_str):
                yield r
        else:
            yield event.plain_result(f"未知子指令或组名: {sub}\n\n{HELP_TEXT}")

    # ==================================================================
    # 自定义指令别名 / Custom Command Aliases
    # 可直接使用中文指令触发对应功能，无需 l4d2 前缀
    # ==================================================================
    @filter.command("服务器", alias={"订阅服"})
    async def alias_server(self, event: AstrMessageEvent):
        """查询订阅服务器列表"""
        async for r in self._cmd_list(event, ""):
            yield r

    @filter.command("求生数据", alias={"玩家数据"})
    async def alias_stats(self, event: AstrMessageEvent):
        """查询求生数据"""
        async for r in self._cmd_stats(event, ""):
            yield r

    @filter.command("connect", alias={"连接", "查询服务器"})
    async def alias_connect(self, event: AstrMessageEvent, ip: str = ""):
        """查询任意服务器: connect <ip[:port]>"""
        if not ip:
            yield event.plain_result("用法: /connect <ip[:port]>")
            return
        async for r in self._cmd_connect(event, ip):
            yield r

    @filter.command("rcon", alias={"远程控制"})
    async def alias_rcon(self, event: AstrMessageEvent, server: str = "", cmd: str = ""):
        """RCON 远程控制: rcon <Nf> <命令>"""
        if not server or not cmd:
            yield event.plain_result("用法: /rcon <Nf> <命令>\n例如: /rcon 2f status")
            return
        rest = f"{server} {cmd}"
        async for r in self._cmd_rcon(event, rest):
            yield r

    @filter.command("steam绑定", alias={"Steam绑定", "绑定steam"})
    async def alias_bind(self, event: AstrMessageEvent, steamid: str = ""):
        """绑定 SteamID"""
        async for r in self._cmd_bind(event, steamid):
            yield r

    @filter.command("找服", alias={"搜索服务器"})
    async def alias_search(self, event: AstrMessageEvent):
        """Steam 找服"""
        # 从 message_str 提取参数
        rest = self._strip_cmd(event.message_str, "找服")
        async for r in self._cmd_search(event, rest):
            yield r

    @filter.command("anne查询", alias={"Anne查询", "anne"})
    async def alias_anne(self, event: AstrMessageEvent):
        """查询 Anne 数据库"""
        rest = self._strip_cmd(event.message_str, "anne查询")
        async for r in self._cmd_anne(event, rest):
            yield r

    # ==================================================================
    # event 指令组
    # ==================================================================
    @filter.command_group("event")
    def event(self):
        """事件预约系统"""
        # 仅声明，子指令在下方注册

    @event.command("add")
    async def event_add(self, event: AstrMessageEvent, name: str, time_str: str, max_player: int = 10000):
        """创建预约: event add <名称> <时间> [人数上限]"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.add(
            group_key=event.unified_msg_origin,
            name=name,
            time_str=time_str,
            desc="",
            max_player=max_player,
            creator=user,
        )
        if result.get("ok"):
            yield event.plain_result(
                f"✅ 事件 #{result['index']}「{name}」已创建\n"
                f"时间: {result['time']}\n人数上限: {max_player}"
            )
        else:
            yield event.plain_result(f"❌ {result.get('error', '创建失败')}")

    @event.command("del")
    async def event_del(self, event: AstrMessageEvent, index: int):
        """删除预约: event del <序号>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.delete(
            event.unified_msg_origin, index, user, event.is_admin()
        )
        yield event.plain_result("✅ 已删除" if result.get("ok") else f"❌ {result.get('error')}")

    @event.command("chtime")
    async def event_chtime(self, event: AstrMessageEvent, index: int, time_str: str):
        """修改时间: event chtime <序号> <时间>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.change_time(
            event.unified_msg_origin, index, time_str, user, event.is_admin()
        )
        if result.get("ok"):
            yield event.plain_result(f"✅ 时间已改为 {result['time']}")
        else:
            yield event.plain_result(f"❌ {result.get('error')}")

    @event.command("chname")
    async def event_chname(self, event: AstrMessageEvent, index: int, name: str):
        """修改名称: event chname <序号> <名称>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.change_name(
            event.unified_msg_origin, index, name, user, event.is_admin()
        )
        yield event.plain_result("✅ 名称已修改" if result.get("ok") else f"❌ {result.get('error')}")

    @event.command("desc")
    async def event_desc(self, event: AstrMessageEvent, index: int):
        """修改描述: event desc <序号> <描述>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        # desc 需要贪心文本，手动从 message_str 解析
        desc = self._extract_rest(event.message_str, "event", "desc", str(index))
        user = self._get_user(event)
        result = await self._events.set_desc(
            event.unified_msg_origin, index, desc, user, event.is_admin()
        )
        yield event.plain_result("✅ 描述已修改" if result.get("ok") else f"❌ {result.get('error')}")

    @event.command("list")
    async def event_list(self, event: AstrMessageEvent):
        """列出本群预约: event list"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        res_list = self._events.list_group(event.unified_msg_origin)
        if not res_list:
            yield event.plain_result("本群暂无预约")
            return
        lines = ["本群预约列表", "━━━━━━━━━━━━"]
        for r in res_list:
            dt = r.datetime
            time_str = dt.strftime("%m-%d %H:%M") if dt else "未知"
            cur = len(r.participants)
            extra = len(r.extra_participants)
            lines.append(f"#{r.index} {r.name}")
            lines.append(f"  时间: {time_str} | {cur}/{r.max_player}" + (f"(替补{extra})" if extra else ""))
            if r.desc:
                lines.append(f"  描述: {r.desc}")
        yield event.plain_result("\n".join(lines))

    @event.command("view")
    async def event_view(self, event: AstrMessageEvent, index: int):
        """查看详情: event view <序号>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        r = self._events.view(event.unified_msg_origin, index)
        if r is None:
            yield event.plain_result("未找到该事件")
            return
        dt = r.datetime
        lines = [
            f"事件 #{r.index}「{r.name}」",
            f"时间: {dt.strftime('%Y-%m-%d %H:%M') if dt else '未知'}",
            f"发起人: {r.initiator.nickname}",
            f"人数: {len(r.participants)}/{r.max_player}" + (f"(替补{len(r.extra_participants)})" if r.extra_participants else ""),
        ]
        if r.desc:
            lines.append(f"描述: {r.desc}")
        if r.participants:
            lines.append("━━━ 参与者 ━━━")
            for i, p in enumerate(r.participants, 1):
                lines.append(f"{i}. {p.nickname}")
        if r.extra_participants:
            lines.append("━━━ 替补 ━━━")
            for i, p in enumerate(r.extra_participants, 1):
                lines.append(f"{i}. {p.nickname}")
        yield event.plain_result("\n".join(lines))

    @event.command("join")
    async def event_join(self, event: AstrMessageEvent, index: int):
        """报名: event join <序号>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.join(event.unified_msg_origin, index, user)
        if result.get("ok"):
            if result.get("role") == "substitute":
                yield event.plain_result(f"事件已满，你已加入替补（第{result.get('position')}位）")
            else:
                yield event.plain_result(f"✅ 报名成功（第{result.get('position')}位）")
        else:
            yield event.plain_result(f"❌ {result.get('error')}")

    @event.command("leave")
    async def event_leave(self, event: AstrMessageEvent, index: int):
        """退出: event leave <序号>"""
        if not self._events:
            yield event.plain_result("事件预约系统未启用")
            return
        user = self._get_user(event)
        result = await self._events.leave(event.unified_msg_origin, index, user)
        if result.get("ok"):
            promoted = result.get("promoted")
            if promoted:
                yield event.plain_result(f"✅ 已退出，替补 {promoted.get('nickname', '')} 已自动递补")
            else:
                yield event.plain_result("✅ 已退出")
        else:
            yield event.plain_result(f"❌ {result.get('error')}")

    # ==================================================================
    # l4d2 子指令实现
    # ==================================================================
    async def _cmd_help(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        yield event.plain_result(HELP_TEXT)

    async def _cmd_connect(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """查询任意服务器。"""
        if not rest:
            yield event.plain_result("用法: /l4d2 connect <ip[:port]>")
            return
        if not self._query:
            yield event.plain_result("查询引擎未初始化")
            return
        host, port = parse_server_addr(rest)
        server = ServerConfig(name=rest, group="", host=host, port=port)
        yield event.plain_result(f"正在查询 {host}:{port} ...")
        result = await self._query.query_full(server)
        async for r in self._output_detail(event, result):
            yield r

    async def _cmd_list(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """查询订阅服务器列表。"""
        servers = get_servers_from_config(self.config)
        if not servers:
            yield event.plain_result("未配置订阅服务器")
            return
        groups = group_servers(servers)
        group_name = rest.strip()
        if group_name and group_name not in groups:
            yield event.plain_result(f"未找到分组: {group_name}\n可用分组: {', '.join(groups.keys())}")
            return
        # 空组名 -> 第一个分组（默认组）；否则取指定分组
        target = groups.get(group_name) if group_name else list(groups.values())[0]
        sv_list = [sv for _, sv in target]
        if not sv_list:
            yield event.plain_result("该分组无服务器")
            return
        yield event.plain_result(f"正在查询 {len(sv_list)} 台服务器...")
        results = await asyncio.wait_for(
            self._query.query_batch_light(sv_list), timeout=15
        ) if self._query else [QueryResult(server=sv) for sv in sv_list]
        async for r in self._output_list(event, results, group_name=group_name or "服务器"):
            yield r

    async def _cmd_server(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """查询默认组第 N 台服务器。"""
        if not rest.isdigit():
            yield event.plain_result("用法: /l4d2 server <序号>")
            return
        servers = get_servers_from_config(self.config)
        if not servers:
            yield event.plain_result("未配置订阅服务器")
            return
        groups = group_servers(servers)
        default_group = list(groups.values())[0]
        idx = int(rest) - 1  # 1-based -> 0-based
        if idx < 0 or idx >= len(default_group):
            yield event.plain_result(f"序号超出范围，共 {len(default_group)} 台服务器")
            return
        server = default_group[idx][1]
        yield event.plain_result(f"正在查询 {server.name}...")
        result = await self._query.query_full(server) if self._query else QueryResult(server=server)
        async for r in self._output_detail(event, result):
            yield r

    async def _cmd_group_query(self, event: AstrMessageEvent, group_name: str, idx_str: str) -> AsyncGenerator:
        """组名查询：有序号查详情，无序号查列表。"""
        servers = get_servers_from_config(self.config)
        groups = group_servers(servers)
        target = groups.get(group_name)
        if not target:
            yield event.plain_result(f"未找到分组: {group_name}")
            return
        if idx_str and idx_str.isdigit():
            # 查详情
            idx = int(idx_str) - 1
            if idx < 0 or idx >= len(target):
                yield event.plain_result(f"序号超出范围，共 {len(target)} 台")
                return
            server = target[idx][1]
            yield event.plain_result(f"正在查询 {server.name}...")
            result = await self._query.query_full(server) if self._query else QueryResult(server=server)
            async for r in self._output_detail(event, result):
                yield r
        else:
            # 查列表
            sv_list = [sv for _, sv in target]
            yield event.plain_result(f"正在查询 {group_name} 分组 {len(sv_list)} 台服务器...")
            results = await asyncio.wait_for(
                self._query.query_batch_light(sv_list), timeout=15
            ) if self._query else [QueryResult(server=sv) for sv in sv_list]
            async for r in self._output_list(event, results, group_name=group_name):
                yield r

    async def _cmd_search(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """Steam 找服。"""
        if not self._steam or not self._steam.available:
            yield event.plain_result("未配置 Steam Web API Key，找服功能不可用")
            return
        opts = self._parse_search_opts(rest)
        if isinstance(opts, str):
            yield event.plain_result(opts)
            return
        yield event.plain_result("正在搜索服务器...")
        servers = await self._steam.search_servers(**opts)
        if servers and servers[0].get("error"):
            yield event.plain_result(f"❌ {servers[0]['error']}")
            return
        if not servers:
            yield event.plain_result("未找到匹配的服务器")
            return
        lines = [f"找到 {len(servers)} 台服务器", "━━━━━━━━━━━━"]
        for i, s in enumerate(servers, 1):
            lines.append(f"{i}. {s.get('name', '?')}")
            lines.append(f"   地图: {s.get('map', '?')} | 人数: {s.get('players', 0)}/{s.get('max_players', 0)}")
            addr = s.get("addr", "")
            if addr:
                lines.append(f"   steam://connect/{addr}")
        yield event.plain_result("\n".join(lines))

    async def _cmd_bind(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """绑定 SteamID。"""
        sid = rest.strip()
        if not sid:
            # 查看当前绑定
            current = await self._get_bind(event)
            yield event.plain_result(
                f"当前绑定: {current}" if current else "未绑定。用法: /l4d2 bind <SteamID>"
            )
            return
        if not is_valid_steamid(sid):
            yield event.plain_result("SteamID 格式不正确，支持 STEAM_0:1:xxx 或 7656... 形式")
            return
        sid64 = normalize_to_steamid64(sid)
        await self._set_bind(event, sid)
        yield event.plain_result(f"✅ 已绑定 SteamID: {sid}\n(SteamID64: {sid64})")

    async def _cmd_stats(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """求生数据查询。"""
        if not self._steam or not self._steam.available:
            yield event.plain_result("未配置 Steam Web API Key，求生数据功能不可用")
            return
        sid = rest.strip()
        if not sid:
            sid = await self._get_bind(event)
            if not sid:
                yield event.plain_result("请先绑定 SteamID 或直接输入: /l4d2 stats <SteamID>")
                return
        if not is_valid_steamid(sid):
            yield event.plain_result("SteamID 格式不正确")
            return
        yield event.plain_result("正在查询求生数据...")
        data = await self._steam.get_player_full(sid)
        if data.get("error"):
            yield event.plain_result(f"❌ {data['error']}")
            return
        lines = [
            f"玩家: {data.get('nickname', '?')}",
            f"SteamID64: {data.get('steamid64', '?')}",
            f"游玩时长: {data.get('playtime_hours', 0)} 小时",
            f"对抗胜/负: {data.get('versus_won', 0)}/{data.get('versus_lost', 0)} (胜率 {data.get('win_rate', 0)}%)",
            f"总击杀: {data.get('total_kills', 0)}",
            f"  手枪: {data.get('pistol_kill', 0)} | 马格南: {data.get('magnum_kill', 0)}",
            f"  SMG: {data.get('smg_kill', 0)} | 霰弹枪: {data.get('shotgun_kill', 0)}",
            f"  狩猎步枪: {data.get('hunting_kill', 0)} | 爆头: {data.get('headshots', 0)}",
            f"经验评分: {data.get('exp', 0)}",
        ]
        yield event.plain_result("\n".join(lines))

    async def _cmd_anne(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """Anne 数据库查询。"""
        if not self._anne or not self._anne.available:
            yield event.plain_result("Anne 数据库未启用或未连接")
            return
        name = rest.strip()
        if not name:
            # 尝试用绑定的 SteamID
            sid = await self._get_bind(event)
            if not sid:
                yield event.plain_result("用法: /l4d2 anne <玩家名>（或先 bind SteamID）")
                return
            name = sid
        yield event.plain_result("正在查询 Anne 数据库...")
        data = await self._anne.get_full(name)
        if data.get("error"):
            yield event.plain_result(f"❌ {data['error']}")
            return
        tag = data.get("tag", "")
        player_str = f"[{tag}]{data.get('name', '')}" if tag else data.get("name", "")
        lines = [
            f"玩家: {player_str}",
            f"分数: {data.get('points', 0)}    排名: {data.get('rank', 0)}/{data.get('total', 0)}",
            f"游玩时间: {format_playtime(data.get('playtime_minutes', 0) * 60, only_hour=True)}",
            f"最后上线: {format_datetime(data.get('lastontime', 0))}",
        ]
        yield event.plain_result("\n".join(lines))

    async def _cmd_rcon(self, event: AstrMessageEvent, rest: str) -> AsyncGenerator:
        """RCON 远程控制。"""
        if not self._cfg("rcon_enabled", False):
            yield event.plain_result("RCON 功能未启用")
            return
        if not event.is_admin():
            yield event.plain_result("❌ RCON 需要管理员权限")
            return
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法: /l4d2 rcon <Nf> <命令>\n例如: /l4d2 rcon 2f status")
            return
        target_str, cmd = parts
        idx = parse_rcon_target(target_str)
        if idx is None:
            yield event.plain_result("服务器编号格式不正确，应为 数字f（如 2f）")
            return
        servers = get_servers_from_config(self.config)
        if idx < 0 or idx >= len(servers):
            yield event.plain_result(f"没有这个服务器（共 {len(servers)} 台）")
            return
        server = servers[idx]
        if server.rcon_port < 0:
            yield event.plain_result(f"{server.name} 未开启 RCON")
            return
        yield event.plain_result(f"正在执行 RCON: {cmd}")
        try:
            output = await asyncio.to_thread(
                self._rcon_execute, server.host, server.rcon_port, server.rcon_password, cmd
            )
            yield event.plain_result(f"✅ 执行成功\n{output}")
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"❌ RCON 连接失败: {e}")

    # ==================================================================
    # 输出辅助 / Output Helpers
    # ==================================================================
    async def _output_list(self, event: AstrMessageEvent, results: list[QueryResult], group_name: str = "") -> AsyncGenerator:
        """根据 list_style 输出列表（图片或文本）。"""
        style = self._cfg("list_style", "normal")
        max_players = int(self._cfg("max_show_player", 4))
        output_ip = bool(self._cfg("output_ip", True))
        _, theme = resolve_theme(self.config)

        # 渲染器不可用则强制纯文本
        if style != "text" and (not self._renderer or not self._renderer.available):
            style = "text"

        if style == "text":
            text = self._renderer.render_text(results, output_ip, group_name) if self._renderer else self._render_text_fallback(results, output_ip, group_name)
            yield event.plain_result(text)
            return

        try:
            png = await self._renderer.render_list(results, theme, style, max_players, output_ip, group_name)
            if isinstance(png, bytes):
                yield event.chain_result([Image.fromBytes(png)])
            else:
                yield event.plain_result(str(png))
        except Exception as e:  # noqa: BLE001
            logger.error(f"列表渲染失败: {e}")
            text = self._render_text_fallback(results, output_ip, group_name)
            yield event.plain_result(text)

    async def _output_detail(self, event: AstrMessageEvent, result: QueryResult) -> AsyncGenerator:
        """根据配置输出单服详情（图片或文本）。"""
        style = self._cfg("list_style", "normal")
        max_players = int(self._cfg("max_show_player", 4))
        output_ip = bool(self._cfg("output_ip", True))
        _, theme = resolve_theme(self.config)

        if style != "text" and (not self._renderer or not self._renderer.available):
            style = "text"

        if style == "text":
            text = (self._renderer.render_detail_text(result, output_ip)
                    if self._renderer else self._render_detail_text_fallback(result, output_ip))
            yield event.plain_result(text)
            return

        try:
            png = await self._renderer.render_detail(result, theme, max_players, output_ip)
            if isinstance(png, bytes):
                yield event.chain_result([Image.fromBytes(png)])
            else:
                yield event.plain_result(str(png))
        except Exception as e:  # noqa: BLE001
            logger.error(f"详情渲染失败: {e}")
            text = self._render_detail_text_fallback(result, output_ip)
            yield event.plain_result(text)

    def _render_text_fallback(self, results: list[QueryResult], output_ip: bool, group_name: str = "") -> str:
        """纯文本列表（渲染器不可用时的回退）。"""
        lines: list[str] = []
        for idx, r in enumerate(results, 1):
            if r.online:
                lines.append(f"{idx}. {r.name}")
                lines.append(f"   地图: {r.map_name}")
                lines.append(f"   人数: {r.player_count[0]}/{r.player_count[1]}")
                cfg = get_cfg_name(r.rules)
                if cfg:
                    lines.append(f"   模式: {cfg}")
            else:
                name = r.server.name if r.server else "未知"
                lines.append(f"{idx}. {name} — 无响应")
            lines.append("")
        lines.append("© AstrBot")
        return "\n".join(lines).strip() or "无服务器"

    def _render_detail_text_fallback(self, result: QueryResult, output_ip: bool) -> str:
        """纯文本详情（回退）。"""
        if not result.online:
            name = result.server.name if result.server else "未知"
            return f"{name}\n服务器无响应"
        lines = [f"名称: {result.name}", f"地图: {result.map_name}"]
        cfg = get_cfg_name(result.rules)
        if cfg:
            lines.append(f"模式: {cfg}")
        on, mx, bots = result.player_count
        lines.append(f"玩家: {on}/{mx}")
        if result.players:
            lines.append("\n玩家列表:")
            for p in result.players:
                lines.append(f"  [{p.get('score', 0)}] {p.get('name', '')} ({format_online_time(p.get('duration', 0))})")
        return "\n".join(lines)

    # ==================================================================
    # SteamID 绑定 / SteamID Binding
    # ==================================================================
    def _bind_key(self, event: AstrMessageEvent) -> str:
        return f"{event.get_platform_name()}:{event.get_sender_id()}"

    async def _get_bind(self, event: AstrMessageEvent) -> Optional[str]:
        async with self._bind_lock:
            data = self._load_binds()
            return data.get(self._bind_key(event))

    async def _set_bind(self, event: AstrMessageEvent, steamid: str) -> None:
        async with self._bind_lock:
            data = self._load_binds()
            data[self._bind_key(event)] = steamid
            self._save_binds(data)

    def _load_binds(self) -> dict[str, str]:
        try:
            if os.path.exists(self._bind_path):
                with open(self._bind_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_binds(self, data: dict[str, str]) -> None:
        try:
            with open(self._bind_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ==================================================================
    # RCON 执行 / RCON Execution
    # ==================================================================
    @staticmethod
    def _rcon_execute(host: str, port: int, password: str, cmd: str) -> str:
        """同步执行 RCON（由 asyncio.to_thread 调用）。"""
        from rcon import Client as RconClient
        with RconClient(host, int(port), password) as client:
            return client.run(cmd)

    # ==================================================================
    # 辅助方法 / Helpers
    # ==================================================================
    def _cfg(self, key: str, default: Any = None) -> Any:
        if self.config is None:
            return default
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _group_names(self) -> list[str]:
        servers = get_servers_from_config(self.config)
        groups = group_servers(servers)
        return [g for g in groups.keys() if g not in RESERVED_WORDS]

    def _get_user(self, event: AstrMessageEvent) -> PlatformUser:
        return PlatformUser(
            uid=str(event.get_sender_id()),
            nickname=str(event.get_sender_name() or event.get_sender_id()),
        )

    @staticmethod
    def _strip_cmd(text: str, cmd: str) -> str:
        """从消息文本中去除指令名前缀。"""
        t = text.strip()
        # 去除可能的 / 前缀
        if t.startswith("/"):
            t = t[1:]
        # 不区分大小写去除指令名
        low = t.lower()
        if low.startswith(cmd.lower()):
            t = t[len(cmd):].strip()
        return t

    @staticmethod
    def _extract_rest(message_str: str, group: str, sub: str, index_str: str) -> str:
        """从完整消息中提取 desc 子指令后的贪心文本。"""
        # message_str 形如 "event desc 3 这里是描述"
        t = message_str.strip()
        if t.startswith("/"):
            t = t[1:]
        # 去除 group + sub + index
        tokens = t.split(maxsplit=3)
        # tokens = ["event", "desc", "3", "描述..."]
        if len(tokens) >= 4:
            return tokens[3]
        return ""

    @staticmethod
    def _parse_search_opts(rest: str) -> dict[str, Any] | str:
        """解析 find 选项: -n -i -t -e -a -r -m。"""
        opts: dict[str, Any] = {
            "name": "", "ip": "", "tag": "",
            "empty_only": False, "ignore_player_limit": False,
            "region": "", "max_results": 5,
        }
        try:
            tokens = shlex.split(rest)
        except ValueError:
            tokens = rest.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-n", "--name"):
                i += 1
                if i < len(tokens):
                    opts["name"] = tokens[i]
            elif tok in ("-i", "--ip"):
                i += 1
                if i < len(tokens):
                    opts["ip"] = tokens[i]
            elif tok in ("-t", "--tag"):
                i += 1
                if i < len(tokens):
                    opts["tag"] = tokens[i]
            elif tok in ("-e", "--empty"):
                opts["empty_only"] = True
            elif tok in ("-a", "--all"):
                opts["ignore_player_limit"] = True
            elif tok in ("-r", "--region"):
                i += 1
                if i < len(tokens):
                    opts["region"] = tokens[i]
            elif tok in ("-m", "--max"):
                i += 1
                if i < len(tokens):
                    try:
                        opts["max_results"] = max(1, int(tokens[i]))
                    except ValueError:
                        pass
            i += 1
        return opts
