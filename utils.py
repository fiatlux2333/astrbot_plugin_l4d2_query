"""utils.py — 数据类、工具函数与主题定义。

从 koishi-plugin-l4d2-query 移植到 AstrBot 的通用工具模块。
包含：服务器/查询结果/玩家/预约数据类、SteamID 转换、地址解析、
主题判定（含夜间模式）、RCON 目标解析、数据目录与时间格式化。
"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ======================================================================
# 主题定义 / Theme Definitions
# ======================================================================
# 每套主题 4 个颜色：背景 / 字色 / 内层 / 边框
THEMES: dict[str, dict[str, str]] = {
    "normal": {"bg": "#FFFFFF", "font": "#000000", "inner": "#F5F6F7", "border": "#E5E7EB"},
    "dark":   {"bg": "#1F1F1F", "font": "#DDDDDD", "inner": "#0B0B0B", "border": "#3E3E3E"},
    "neon":   {"bg": "#34405A", "font": "#FFFFFF", "inner": "#222C44", "border": "#36507E"},
    "wind":   {"bg": "#FFFFFF", "font": "#000000", "inner": "#F5F6F7", "border": "#E5E7EB"},
    "oled":   {"bg": "#000000", "font": "#D6D6D6", "inner": "#000000", "border": "#1F1F1F"},
}


# ======================================================================
# 数据类 / Data Classes
# ======================================================================
@dataclass
class ServerConfig:
    """订阅服务器配置。"""
    name: str
    group: str
    host: str
    port: int = 27015
    rcon_port: int = -1
    rcon_password: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ServerConfig":
        return cls(
            name=str(d.get("name", "服务器")),
            group=str(d.get("group", "")).strip(),
            host=str(d.get("host", "127.0.0.1")),
            port=int(d.get("port", 27015)),
            rcon_port=int(d.get("rcon_port", -1)),
            rcon_password=str(d.get("rcon_password", "")),
        )


@dataclass
class QueryResult:
    """A2S 查询结果。"""
    server: Optional[ServerConfig] = None
    info: Optional[dict[str, Any]] = None
    players: list[dict[str, Any]] = field(default_factory=list)
    rules: Optional[dict[str, Any]] = None
    info_error: bool = True
    player_error: bool = True
    rule_error: bool = True

    @property
    def online(self) -> bool:
        """服务器是否在线（info 查询成功）。"""
        return not self.info_error and self.info is not None

    @property
    def name(self) -> str:
        return self.info.get("server_name", "") if self.info else (self.server.name if self.server else "")

    @property
    def map_name(self) -> str:
        return self.info.get("map_name", "") if self.info else ""

    @property
    def player_count(self) -> tuple[int, int, int]:
        """(online, max, bots)。"""
        if self.info:
            return (
                self.info.get("player_count", 0),
                self.info.get("max_players", 0),
                self.info.get("bot_count", 0),
            )
        return (0, 0, 0)


@dataclass
class PlatformUser:
    """平台用户标识。"""
    uid: str
    nickname: str

    def to_dict(self) -> dict[str, str]:
        return {"uid": self.uid, "nickname": self.nickname}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlatformUser":
        return cls(uid=str(d.get("uid", "")), nickname=str(d.get("nickname", "")))


@dataclass
class Reservation:
    """事件预约。"""
    index: int
    is_expired: bool = False
    is_noticed: int = 0  # 0=未提醒 1=已提醒
    name: str = ""
    desc: str = ""
    group_key: str = ""  # 平台:消息类型:会话ID，用于隔离不同群
    event_date: float = 0.0  # unix timestamp
    max_player: int = 10000
    initiator: PlatformUser = field(default_factory=lambda: PlatformUser("", ""))
    participants: list[PlatformUser] = field(default_factory=list)
    extra_participants: list[PlatformUser] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "is_expired": self.is_expired,
            "is_noticed": self.is_noticed,
            "name": self.name,
            "desc": self.desc,
            "group_key": self.group_key,
            "event_date": self.event_date,
            "max_player": self.max_player,
            "initiator": self.initiator.to_dict(),
            "participants": [p.to_dict() for p in self.participants],
            "extra_participants": [p.to_dict() for p in self.extra_participants],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Reservation":
        return cls(
            index=int(d.get("index", 0)),
            is_expired=bool(d.get("is_expired", False)),
            is_noticed=int(d.get("is_noticed", 0)),
            name=str(d.get("name", "")),
            desc=str(d.get("desc", "")),
            group_key=str(d.get("group_key", "")),
            event_date=float(d.get("event_date", 0.0)),
            max_player=int(d.get("max_player", 10000)),
            initiator=PlatformUser.from_dict(d.get("initiator") or {}),
            participants=[PlatformUser.from_dict(p) for p in (d.get("participants") or [])],
            extra_participants=[PlatformUser.from_dict(p) for p in (d.get("extra_participants") or [])],
        )

    @property
    def datetime(self) -> Optional[datetime]:
        if self.event_date > 0:
            return datetime.fromtimestamp(self.event_date)
        return None


# ======================================================================
# SteamID 转换 / SteamID Conversion
# ======================================================================
_STEAM2_RE = re.compile(r"^STEAM_[01]:[01]:\d+$")
_STEAM64_RE = re.compile(r"^7656119\d{10}$")
_STEAM64_BASE = 76561197960265728


def is_valid_steamid(sid: str) -> bool:
    """判断是否为合法 SteamID（Steam2 或 SteamID64）。"""
    return bool(_STEAM2_RE.match(sid) or _STEAM64_RE.match(sid))


def steamid2_to_steamid64(steam2: str) -> Optional[str]:
    """STEAM_0:1:xxx -> 76561197960265728 + auth*2 + server。"""
    if not _STEAM2_RE.match(steam2):
        return None
    parts = steam2.split(":")
    server = int(parts[1])
    auth = int(parts[2])
    sid64 = _STEAM64_BASE + auth * 2 + server
    return str(sid64)


def steamid64_to_steamid2(sid64: str) -> Optional[str]:
    """SteamID64 -> STEAM_X:Y:Z。"""
    if not _STEAM64_RE.match(sid64):
        return None
    num = int(sid64)
    auth = (num - _STEAM64_BASE) // 2
    server = (num - _STEAM64_BASE) % 2
    return f"STEAM_0:{server}:{auth}"


def normalize_to_steamid64(sid: str) -> Optional[str]:
    """任意合法 SteamID -> SteamID64。"""
    if _STEAM64_RE.match(sid):
        return sid
    return steamid2_to_steamid64(sid)


# ======================================================================
# 服务器地址解析 / Server Address Parsing
# ======================================================================
_IP_RE = re.compile(
    r"^((2(5[0-5]|[0-4]\d))|[0-1]?\d{1,2})(\.((2(5[0-5]|[0-4]\d))|[0-1]?\d{1,2})){3}$"
)


def is_ip(host: str) -> bool:
    return bool(_IP_RE.match(host))


def parse_server_addr(addr: str, default_port: int = 27015) -> tuple[str, int]:
    """解析 ip[:port] 或 domain:port -> (host, port)。

    支持域名，返回原样（a2s 内部会做 DNS 解析）。
    """
    addr = addr.strip()
    if ":" in addr:
        host, _, port_str = addr.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            port = default_port
    else:
        host = addr
        port = default_port
    return host, port


async def resolve_host(host: str) -> str:
    """域名 -> IP（若是 IP 直接返回）。失败返回原 host。"""
    if is_ip(host):
        return host
    try:
        # 同步 gethostbyname 放到线程池避免阻塞
        import asyncio
        return await asyncio.to_thread(socket.gethostbyname, host)
    except Exception:
        return host


# ======================================================================
# 主题判定 / Theme Resolution
# ======================================================================
def resolve_theme(config: Any, now_hour: Optional[int] = None) -> tuple[str, dict[str, str]]:
    """根据配置与当前时间返回 (主题名, 主题颜色)。

    夜间模式：night_start <= now 或 now <= night_end 时切换。
    跨午夜情况（如 21~7）正确处理。
    """
    if now_hour is None:
        now_hour = datetime.now().hour

    night_mode = bool(_get(config, "night_mode", False))
    if night_mode:
        start = int(_get(config, "night_start", 21))
        end = int(_get(config, "night_end", 7))
        # 跨午夜：start > end 时，now >= start 或 now <= end 算夜间
        # 不跨午夜：start <= end 时，start <= now <= end 算夜间
        is_night = (now_hour >= start or now_hour <= end) if start > end else (start <= now_hour <= end)
        if is_night:
            if bool(_get(config, "night_oled", False)):
                theme_name = "oled"
            else:
                theme_name = "dark"
            return theme_name, THEMES[theme_name]

    theme_name = str(_get(config, "theme_type", "normal")).lower()
    if theme_name not in THEMES:
        theme_name = "normal"
    return theme_name, THEMES[theme_name]


# ======================================================================
# RCON 目标解析 / RCON Target Parsing
# ======================================================================
_RCON_TARGET_RE = re.compile(r"^([1-9]\d*)f$")


def parse_rcon_target(target: str) -> Optional[int]:
    """解析 '2f' -> 1（0-based 索引）。不合法返回 None。"""
    target = target.strip()
    m = _RCON_TARGET_RE.match(target)
    if not m:
        return None
    return int(m.group(1)) - 1  # 转为 0-based


# ======================================================================
# 数据目录 / Data Directory
# ======================================================================
def get_data_dir() -> str:
    """返回插件持久化数据目录并确保存在。

    使用 AstrBot 的 plugin_data 目录；若无法获取则回退到插件自身目录下的 data。
    """
    base: Optional[str] = None
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
        base = get_astrbot_plugin_data_path()
    except Exception:
        pass
    if not base:
        # 回退：插件目录下 data/
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    data_dir = os.path.join(base, "l4d2_query")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


# ======================================================================
# 时间格式化 / Time Formatting
# ======================================================================
def format_online_time(raw_seconds: float) -> str:
    """在线时长格式化：秒 -> 'Xh Ym' 或 'Ym Zs'。"""
    total = int(raw_seconds)
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_playtime(seconds: float, only_hour: bool = False) -> str:
    """游玩时长格式化。only_hour=True 时只显示小时。"""
    total = int(seconds)
    if total < 0:
        total = 0
    hours = total // 3600
    minutes = (total % 3600) // 60
    if only_hour or hours >= 1:
        return f"{hours}小时{minutes}分钟" if minutes and not only_hour else f"{hours}小时"
    return f"{minutes}分钟"


def format_datetime(ts: float) -> str:
    """unix timestamp -> 'YYYY-MM-DD HH:MM'。"""
    if ts <= 0:
        return "未知"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ======================================================================
# 时间解析（事件预约用）/ Time Parsing for Events
# ======================================================================
def parse_event_time(time_str: str) -> Optional[datetime]:
    """解析事件时间字符串。

    支持：YYYY/MM/DD HH:MM、YYYY-MM-DD HH:MM、MM/DD HH:MM
    返回 datetime 或 None（格式不合法）。
    """
    time_str = time_str.strip()
    now = datetime.now()

    # YYYY/MM/DD HH:MM 或 YYYY-MM-DD HH:MM
    if re.match(r"^\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\s+\d{1,2}:\d{1,2}$", time_str):
        sep = "/" if "/" in time_str.split(" ")[0] else "-"
        try:
            date_part, time_part = time_str.split()
            y, mo, d = date_part.split(sep)
            h, mi = time_part.split(":")
            dt = datetime(int(y), int(mo), int(d), int(h), int(mi))
            return dt
        except (ValueError, IndexError):
            return None

    # MM/DD HH:MM 或 MM-DD HH:MM（用当前年份）
    if re.match(r"^\d{1,2}[/\-]\d{1,2}\s+\d{1,2}:\d{1,2}$", time_str):
        sep = "/" if "/" in time_str.split(" ")[0] else "-"
        try:
            date_part, time_part = time_str.split()
            mo, d = date_part.split(sep)
            h, mi = time_part.split(":")
            dt = datetime(now.year, int(mo), int(d), int(h), int(mi))
            return dt
        except (ValueError, IndexError):
            return None

    return None


# ======================================================================
# 内部辅助 / Internal Helpers
# ======================================================================
def _get(config: Any, key: str, default: Any = None) -> Any:
    """从 dict 或 AstrBotConfig 取值。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    # AstrBotConfig 支持 .get
    try:
        return config.get(key, default)
    except Exception:
        return default


def get_servers_from_config(config: Any) -> list[ServerConfig]:
    """从配置解析服务器列表。"""
    raw = _get(config, "servers", []) or []
    servers: list[ServerConfig] = []
    for item in raw:
        try:
            servers.append(ServerConfig.from_dict(item if isinstance(item, dict) else dict(item)))
        except Exception:
            continue
    return servers


def group_servers(servers: list[ServerConfig]) -> dict[str, list[tuple[int, ServerConfig]]]:
    """按 group 分组，返回 {组名: [(全局序号, 服务器), ...]}。

    未填 group 的归入默认组 "服务器"。
    """
    groups: dict[str, list[tuple[int, ServerConfig]]] = {}
    for idx, sv in enumerate(servers):
        gname = sv.group if sv.group else "服务器"
        groups.setdefault(gname, []).append((idx, sv))
    return groups


# 保留字，组名不能与之冲突
RESERVED_WORDS = frozenset({
    "connect", "list", "server", "search", "bind", "stats",
    "anne", "rcon", "help", "event",
})
