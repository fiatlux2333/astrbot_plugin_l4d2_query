"""steam.py — Steam Web API 集成。

提供服务器搜索（IGameServersService/GetServerList）与玩家统计查询
（GetUserStatsForGame + GetPlayerSummaries），含伪经验评分计算。
使用 aiohttp 异步请求，支持 HTTP 代理。
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp

from .utils import normalize_to_steamid64

L4D2_APPID = 550


class SteamAPI:
    """Steam Web API 客户端。

    Args:
        api_key: Steam Web API Key。为空时 search/stats 不可用。
        proxy_url: HTTP 代理地址，留空直连。
    """

    BASE = "https://api.steampowered.com"

    def __init__(self, api_key: str = "", proxy_url: str = ""):
        self._key = api_key or ""
        self._proxy = proxy_url or None
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def available(self) -> bool:
        return bool(self._key)

    async def _get_session(self) -> aiohttp.ClientSession:
        """复用同一个 ClientSession，避免每次请求都重建连接池。"""
        if self._session is None or self._session.closed:
            if self._proxy:
                connector = aiohttp.TCPConnector(proxy=self._proxy)
                self._session = aiohttp.ClientSession(connector=connector)
            else:
                self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """关闭底层 ClientSession。应在插件 terminate 时调用。"""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    # ==================================================================
    # 服务器搜索 / Server Search
    # ==================================================================
    async def search_servers(
        self,
        name: str = "",
        ip: str = "",
        tag: str = "",
        empty_only: bool = False,
        ignore_player_limit: bool = False,
        region: str = "",
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """使用 IGameServersService/GetServerList/v1 搜索 L4D2 服务器。

        返回精简 dict 列表：name, addr, players, max_players, map, tags。
        """
        if not self.available:
            return [{"error": "未配置 Steam Web API Key"}]

        # filter 用反斜杠分隔 key\value。用户输入去掉反斜杠，避免注入额外 filter 段。
        def _clean(v: str) -> str:
            return v.replace("\\", "")

        filters = [f"appid\\{L4D2_APPID}"]
        if name:
            filters.append(f"name_match\\{_clean(name)}")
        if ip:
            filters.append(f"gameaddr\\{_clean(ip)}")
        if tag:
            filters.append(f"gametype\\{_clean(tag)}")
        if not ignore_player_limit:
            if empty_only:
                filters.append("noplayers\\1")
            else:
                filters.append("empty\\1")
        if region:
            filters.append(f"region\\{_clean(region)}")

        filter_str = "\\".join(filters)
        url = f"{self.BASE}/IGameServersService/GetServerList/v1/"
        params = {"key": self._key, "filter": filter_str, "limit": str(max_results)}

        try:
            session = await self._get_session()
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return [{"error": f"Steam API 返回 {resp.status}"}]
                data = await resp.json()
        except asyncio.TimeoutError:
            return [{"error": "Steam API 请求超时"}]
        except Exception as e:  # noqa: BLE001
            return [{"error": f"请求失败: {e}"}]

        servers = data.get("response", {}).get("servers", []) or []
        return [
            {
                "name": s.get("name", ""),
                "addr": s.get("addr", ""),
                "players": s.get("players", 0),
                "max_players": s.get("max_players", 0),
                "map": s.get("map", ""),
                "tags": s.get("gametype", ""),
                "steam_id": s.get("steamid", ""),
            }
            for s in servers
        ]

    # ==================================================================
    # 玩家统计 / Player Stats
    # ==================================================================
    async def get_player_stats(self, steamid64: str) -> dict[str, Any]:
        """GetUserStatsForGame/v0002 — L4D2 玩家统计。"""
        if not self.available:
            return {"error": "未配置 Steam Web API Key"}
        url = f"{self.BASE}/ISteamUserStats/GetUserStatsForGame/v0002/"
        params = {"appid": str(L4D2_APPID), "key": self._key, "steamid": steamid64}
        try:
            session = await self._get_session()
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return {"error": f"API 返回 {resp.status}"}
                data = await resp.json()
        except Exception as e:  # noqa: BLE001
            return {"error": f"请求失败: {e}"}

        stats_list = data.get("playerstats", {}).get("stats", []) or []
        # 转为 name -> value 的 dict
        stats = {s.get("name", ""): s.get("value", 0) for s in stats_list}
        return stats

    async def get_player_summaries(self, steamid64: str) -> dict[str, Any]:
        """GetPlayerSummaries/v2 — 玩家昵称等信息。"""
        if not self.available:
            return {"error": "未配置 Steam Web API Key"}
        url = f"{self.BASE}/ISteamUser/GetPlayerSummaries/v2/"
        params = {"key": self._key, "steamids": steamid64}
        try:
            session = await self._get_session()
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return {"error": f"API 返回 {resp.status}"}
                data = await resp.json()
        except Exception as e:  # noqa: BLE001
            return {"error": f"请求失败: {e}"}

        players = data.get("response", {}).get("players", []) or []
        return players[0] if players else {}

    async def get_player_full(self, steamid_input: str) -> dict[str, Any]:
        """合并统计 + 昵称 + 伪经验评分。

        Args:
            steamid_input: STEAM_0:1:xxx 或 7656... 形式。

        Returns:
            dict 含 nickname, playtime, versus_won, versus_lost, kills, headshots,
            exp 等字段；出错含 error。
        """
        sid64 = normalize_to_steamid64(steamid_input)
        if not sid64:
            return {"error": "SteamID 格式不正确"}

        stats = await self.get_player_stats(sid64)
        if "error" in stats:
            return stats

        summary = await self.get_player_summaries(sid64)
        nickname = summary.get("personaname", "未知玩家") if isinstance(summary, dict) else "未知玩家"

        # 提取关键统计（按原插件字段名）
        playtime = stats.get("Stat.TotalPlayTime.Total", 0)
        versus_won = stats.get("Stat.GamesWon.Versus", 0)
        versus_lost = stats.get("Stat.GamesLost.Versus", 0)

        pistol_kill = stats.get("Stat.pistol.Kills.Total", 0)
        magnum_kill = stats.get("Stat.pistol_magnum.Kills.Total", 0)
        smg_kill = stats.get("Stat.smg_silenced.Kills.Total", 0)
        uzi_kill = stats.get("Stat.smg.Kills.Total", 0)
        pump_kill = stats.get("Stat.pumpshotgun.Kills.Total", 0)
        chrome_kill = stats.get("Stat.shotgun_chrome.Kills.Total", 0)
        hunting_kill = stats.get("Stat.hunting_rifle.Kills.Total", 0)
        pump_head = stats.get("Stat.pumpshotgun.Head.Total", 0)
        chrome_head = stats.get("Stat.shotgun_chrome.Head.Total", 0)

        total_kills = (pistol_kill + magnum_kill + smg_kill + uzi_kill
                       + pump_kill + chrome_kill + hunting_kill + pump_head + chrome_head)

        # 伪经验公式（按原插件）
        total_games = versus_won + versus_lost
        win_rate = (versus_won / total_games) if total_games > 0 else 0
        playtime_hours = playtime / 3600 if playtime else 0
        exp = win_rate * (0.55 * playtime_hours + 0.005 * total_kills)

        return {
            "nickname": nickname,
            "steamid64": sid64,
            "playtime_seconds": playtime,
            "playtime_hours": round(playtime_hours, 1),
            "versus_won": versus_won,
            "versus_lost": versus_lost,
            "win_rate": round(win_rate * 100, 1),
            "total_kills": total_kills,
            "pistol_kill": pistol_kill,
            "magnum_kill": magnum_kill,
            "smg_kill": smg_kill + uzi_kill,
            "shotgun_kill": pump_kill + chrome_kill,
            "hunting_kill": hunting_kill,
            "headshots": pump_head + chrome_head,
            "exp": round(exp, 1),
        }
