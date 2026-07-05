"""anne.py — Anne 服 MySQL 数据库查询。

使用 aiomysql 连接池查询 players 表（分数/时长/排名）与 rpg 表（玩家标签）。
原插件使用 @curRank 变量算排名，本模块改用 COUNT(*) 更简洁可靠。
"""
from __future__ import annotations

from typing import Any, Optional

import aiomysql


class AnneDB:
    """Anne 数据库客户端。

    Args:
        host: 数据库地址。
        port: 端口。
        user: 用户名。
        password: 密码。
        db: 数据库名。
    """

    def __init__(self, host: str, port: int, user: str, password: str, db: str):
        self._cfg = dict(
            host=host, port=int(port), user=user,
            password=password, db=db, autocommit=True,
            charset="utf8mb4",
        )
        self._pool: Optional[aiomysql.Pool] = None

    async def connect(self) -> None:
        """创建连接池。失败抛异常（由调用方 catch 降级）。"""
        self._pool = await aiomysql.create_pool(
            minsize=1, maxsize=5, **self._cfg
        )

    async def close(self) -> None:
        """关闭连接池。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    @property
    def available(self) -> bool:
        return self._pool is not None

    # ==================================================================
    # 玩家查询 / Player Queries
    # ==================================================================
    async def query_player_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """按昵称查 players 表。"""
        sql = (
            "SELECT lastontime, playtime, points, steamid, name "
            "FROM players WHERE name = %s LIMIT 1"
        )
        rows = await self._fetchall(sql, (name,))
        return dict(zip(("lastontime", "playtime", "points", "steamid", "name"), rows[0])) if rows else None

    async def query_player_by_steamid(self, steamid: str) -> Optional[dict[str, Any]]:
        """按 SteamID 查 players 表。"""
        sql = (
            "SELECT lastontime, playtime, points, steamid, name "
            "FROM players WHERE steamid = %s LIMIT 1"
        )
        rows = await self._fetchall(sql, (steamid,))
        return dict(zip(("lastontime", "playtime", "points", "steamid", "name"), rows[0])) if rows else None

    async def query_rpg_tag(self, steamid: str) -> str:
        """查 rpg 表 CHATTAG（玩家标签）。无记录返回空串。"""
        sql = "SELECT CHATTAG FROM rpg WHERE steamid = %s LIMIT 1"
        rows = await self._fetchall(sql, (steamid,))
        if rows and rows[0][0] is not None:
            return str(rows[0][0])
        return ""

    async def query_rank(self, points: int) -> tuple[int, int]:
        """计算排名与总玩家数 -> (rank, total)。

        rank = 分数高于该玩家的数量 + 1。
        """
        rank_sql = "SELECT COUNT(*) + 1 FROM players WHERE points > %s"
        total_sql = "SELECT COUNT(*) FROM players"
        rank_rows = await self._fetchall(rank_sql, (points,))
        total_rows = await self._fetchall(total_sql)
        rank = int(rank_rows[0][0]) if rank_rows else 0
        total = int(total_rows[0][0]) if total_rows else 0
        return (rank, total)

    # ==================================================================
    # 综合查询 / Full Query
    # ==================================================================
    async def get_full(self, name_or_steamid: str) -> dict[str, Any]:
        """合并 player + rpg + rank 查询。

        输入可以是玩家名或 SteamID。返回 dict 含 name, points, rank, total,
        playtime_minutes, lastontime, tag；未找到含 error。
        """
        if not self.available:
            return {"error": "Anne 数据库未连接"}

        # 先尝试按 SteamID 查，再按名称查
        player = await self.query_player_by_steamid(name_or_steamid)
        if player is None:
            player = await self.query_player_by_name(name_or_steamid)

        if player is None:
            return {"error": f"未找到玩家: {name_or_steamid}"}

        # 排名
        rank, total = await self.query_rank(int(player.get("points", 0)))
        # 标签
        tag = ""
        try:
            tag = await self.query_rpg_tag(str(player.get("steamid", "")))
        except Exception:
            pass

        return {
            "name": player.get("name", ""),
            "points": int(player.get("points", 0)),
            "rank": rank,
            "total": total,
            "playtime_minutes": int(player.get("playtime", 0)),
            "lastontime": int(player.get("lastontime", 0)),
            "tag": tag,
        }

    # ==================================================================
    # 底层执行 / Low-level Execution
    # ==================================================================
    async def _fetchall(self, sql: str, args: tuple = ()) -> list[tuple]:
        """执行查询并返回所有行。"""
        if not self._pool:
            raise RuntimeError("连接池未初始化")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return await cur.fetchall()
