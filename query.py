"""query.py — A2S (Valve Source Query) 服务器查询引擎。

封装 python-a2s，提供轻量列表查询与完整详情查询，
带并发限制（asyncio.Semaphore）与超时控制，每个查询独立容错。
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any, Optional

import a2s

from .utils import QueryResult, ServerConfig, is_ip


class QueryEngine:
    """A2S 查询引擎。

    Args:
        timeout: 单次查询超时秒数（原插件 1000ms）。
        concurrency: 并发查询上限（原插件 queryLimit 默认 4）。
    """

    def __init__(self, timeout: float = 1.0, concurrency: int = 4):
        self._timeout = timeout
        self._sem = asyncio.Semaphore(max(1, concurrency))

    async def query_light(self, server: ServerConfig) -> QueryResult:
        """轻量查询：仅 a2s.a2s_info，用于列表展示。"""
        result = QueryResult(server=server)
        try:
            addr = await self._resolve_addr(server)
            async with self._sem:
                info = await asyncio.wait_for(
                    a2s.ainfo(addr, timeout=self._timeout),
                    timeout=self._timeout + 1.0,
                )
            result.info = _info_to_dict(info)
            result.info_error = False
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
        return result

    async def query_full(self, server: ServerConfig) -> QueryResult:
        """完整查询：info + players + rules，各自独立容错。用于详情展示。"""
        result = QueryResult(server=server)
        try:
            addr = await self._resolve_addr(server)
        except Exception:
            return result

        # 三个查询各自独立 try/except，任一失败不阻塞其他
        async with self._sem:
            # info
            try:
                info = await asyncio.wait_for(
                    a2s.ainfo(addr, timeout=self._timeout),
                    timeout=self._timeout + 1.0,
                )
                result.info = _info_to_dict(info)
                result.info_error = False
            except Exception:
                pass

            # players
            try:
                players = await asyncio.wait_for(
                    a2s.aplayers(addr, timeout=self._timeout),
                    timeout=self._timeout + 1.0,
                )
                result.players = [_player_to_dict(p) for p in players]
                result.player_error = False
            except Exception:
                pass

            # rules
            try:
                rules = await asyncio.wait_for(
                    a2s.arules(addr, timeout=self._timeout),
                    timeout=self._timeout + 1.0,
                )
                result.rules = dict(rules) if rules else {}
                result.rule_error = False
            except Exception:
                pass

        return result

    async def query_batch_light(self, servers: list[ServerConfig]) -> list[QueryResult]:
        """批量轻量查询：asyncio.gather + Semaphore 并发。

        单台异常不影响其他；异常的服务器返回离线 QueryResult，避免整体取消丢结果。
        """
        if not servers:
            return []
        tasks = [self.query_light(sv) for sv in servers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[QueryResult] = []
        for sv, r in zip(servers, results, strict=True):
            if isinstance(r, QueryResult):
                out.append(r)
            else:
                # 异常（如 CancelledError）退化为离线，保留 server 占位
                out.append(QueryResult(server=sv))
        return out

    async def query_batch_full(self, servers: list[ServerConfig]) -> list[QueryResult]:
        """批量完整查询（慎用，较慢）。"""
        if not servers:
            return []
        tasks = [self.query_full(sv) for sv in servers]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _resolve_addr(self, server: ServerConfig) -> tuple[str, int]:
        """解析服务器地址，域名转 IP。返回 (ip, port)。"""
        host = server.host
        if not is_ip(host):
            try:
                host = await asyncio.to_thread(socket.gethostbyname, host)
            except Exception:
                host = server.host  # 保留原值，a2s 可能仍可解析
        return (host, int(server.port))


# ======================================================================
# a2s 对象转 dict（统一字段，便于渲染）
# ======================================================================
def _info_to_dict(info: Any) -> dict[str, Any]:
    """a2s.SourceInfo -> dict，统一字段名。"""
    return {
        "server_name": getattr(info, "server_name", ""),
        "map_name": getattr(info, "map_name", ""),
        "folder": getattr(info, "folder", ""),
        "game": getattr(info, "game", ""),
        "app_id": getattr(info, "app_id", 0),
        "player_count": getattr(info, "player_count", 0),
        "max_players": getattr(info, "max_players", 0),
        "bot_count": getattr(info, "bot_count", 0),
        "server_type": str(getattr(info, "server_type", "")),
        "platform": str(getattr(info, "platform", "")),
        "visibility": getattr(info, "visibility", 0),
        "vac_enabled": getattr(info, "vac_enabled", False),
        "version": getattr(info, "version", ""),
        "port": getattr(info, "port", 0),
        "steam_id": str(getattr(info, "steam_id", "")),
        "keywords": getattr(info, "keywords", ""),
        "game_id": getattr(info, "game_id", 0),
        # 原始对象保留（部分字段可能未列举）
        "_raw": info,
    }


def _player_to_dict(p: Any) -> dict[str, Any]:
    """a2s.Player -> dict。"""
    return {
        "index": getattr(p, "index", 0),
        "name": getattr(p, "name", ""),
        "score": getattr(p, "score", 0),
        "duration": getattr(p, "duration", 0.0),  # 在线秒数
    }


def get_os_icon(platform: str) -> str:
    """根据平台返回操作系统标识（用于渲染）。"""
    p = (platform or "").upper()
    if "L" in p:
        return "linux"
    if "W" in p:
        return "windows"
    return "unknown"


def get_cfg_name(rules: Optional[dict[str, Any]]) -> str:
    """从 rules 取 confogl 模式名 (l4d_ready_cfg_name)。"""
    if not rules:
        return ""
    return str(rules.get("l4d_ready_cfg_name", "") or "")
