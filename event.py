"""event.py — 事件预约系统。

提供预约 CRUD、JSON 持久化、10 分钟定时扫描（过期标记 + 30 分钟提醒）、
报名/替补/退出递补逻辑。

预约按 group_key（unified_msg_origin）隔离不同群/平台。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any, Optional

from .utils import PlatformUser, Reservation, parse_event_time

LOOP_INTERVAL = 600  # 10 分钟


class EventManager:
    """事件预约管理器。

    Args:
        context: AstrBot Context，用于主动发送提醒消息。
        data_dir: 插件数据目录。
        notice_lead_min: 提前提醒分钟数（默认 30）。
    """

    def __init__(self, context: Any, data_dir: str, notice_lead_min: int = 30):
        self._ctx = context
        self._path = os.path.join(data_dir, "reservations.json")
        self._lead = max(1, int(notice_lead_min))
        self._res: dict[int, Reservation] = {}
        self._task: Optional[asyncio.Task] = None
        self._next_index = 1

    # ==================================================================
    # 生命周期 / Lifecycle
    # ==================================================================
    async def start(self) -> None:
        """加载持久化数据并启动定时循环。"""
        self._load()
        if self._res:
            self._next_index = max(self._res.keys()) + 1
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """取消定时循环并保存。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._save()

    # ==================================================================
    # CRUD
    # ==================================================================
    async def add(self, group_key: str, name: str, time_str: str,
                  desc: str, max_player: int, creator: PlatformUser) -> dict[str, Any]:
        """创建预约。返回 {ok, index, error?}。"""
        dt = parse_event_time(time_str)
        if dt is None:
            return {"ok": False, "error": "时间格式不正确，支持 YYYY/MM/DD HH:MM 或 MM/DD HH:MM"}
        if dt.timestamp() <= time.time():
            return {"ok": False, "error": "事件时间已过期"}

        idx = self._next_index
        self._next_index += 1
        res = Reservation(
            index=idx,
            name=name,
            desc=desc,
            group_key=group_key,
            event_date=dt.timestamp(),
            max_player=max(1, max_player),
            initiator=creator,
        )
        self._res[idx] = res
        self._save()
        return {"ok": True, "index": idx, "time": dt.strftime("%Y-%m-%d %H:%M")}

    async def delete(self, group_key: str, index: int, user: PlatformUser, is_admin: bool) -> dict[str, Any]:
        """删除预约（创建者或管理员）。返回 {ok, error?}。"""
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}
        if not is_admin and res.initiator.uid != user.uid:
            return {"ok": False, "error": "只有事件发起者或管理员可删除"}
        del self._res[index]
        self._save()
        return {"ok": True}

    async def change_time(self, group_key: str, index: int, time_str: str,
                          user: PlatformUser, is_admin: bool) -> dict[str, Any]:
        dt = parse_event_time(time_str)
        if dt is None:
            return {"ok": False, "error": "时间格式不正确"}
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}
        if not is_admin and res.initiator.uid != user.uid:
            return {"ok": False, "error": "无权限"}
        res.event_date = dt.timestamp()
        res.is_noticed = 0  # 重置提醒
        self._save()
        return {"ok": True, "time": dt.strftime("%Y-%m-%d %H:%M")}

    async def change_name(self, group_key: str, index: int, name: str,
                          user: PlatformUser, is_admin: bool) -> dict[str, Any]:
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}
        if not is_admin and res.initiator.uid != user.uid:
            return {"ok": False, "error": "无权限"}
        res.name = name
        self._save()
        return {"ok": True}

    async def set_desc(self, group_key: str, index: int, desc: str,
                       user: PlatformUser, is_admin: bool) -> dict[str, Any]:
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}
        if not is_admin and res.initiator.uid != user.uid:
            return {"ok": False, "error": "无权限"}
        res.desc = desc
        self._save()
        return {"ok": True}

    def list_group(self, group_key: str) -> list[Reservation]:
        """列出某群未过期的预约。"""
        return [r for r in self._res.values()
                if r.group_key == group_key and not r.is_expired]

    def view(self, group_key: str, index: int) -> Optional[Reservation]:
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return None
        return res

    # ==================================================================
    # 报名 / Join & Leave
    # ==================================================================
    async def join(self, group_key: str, index: int, user: PlatformUser) -> dict[str, Any]:
        """报名。满员进替补。返回 {ok, role, error?}。"""
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}
        if res.is_expired:
            return {"ok": False, "error": "事件已过期"}

        # 检查是否已报名
        if any(p.uid == user.uid for p in res.participants):
            return {"ok": False, "error": "你已报名该事件"}
        if any(p.uid == user.uid for p in res.extra_participants):
            return {"ok": False, "error": "你已在替补名单中"}

        if len(res.participants) >= res.max_player:
            res.extra_participants.append(user)
            self._save()
            return {"ok": True, "role": "substitute", "position": len(res.extra_participants)}
        else:
            res.participants.append(user)
            self._save()
            return {"ok": True, "role": "member", "position": len(res.participants)}

    async def leave(self, group_key: str, index: int, user: PlatformUser) -> dict[str, Any]:
        """退出。正式成员退出后若有替补则自动递补。返回 {ok, promoted?, error?}。"""
        res = self._res.get(index)
        if res is None or res.group_key != group_key:
            return {"ok": False, "error": "未找到该事件"}

        # 从正式名单移除
        for i, p in enumerate(res.participants):
            if p.uid == user.uid:
                res.participants.pop(i)
                # 递补
                promoted = None
                if res.extra_participants:
                    promoted = res.extra_participants.pop(0)
                    res.participants.append(promoted)
                self._save()
                return {"ok": True, "promoted": promoted.to_dict() if promoted else None}

        # 从替补移除
        for i, p in enumerate(res.extra_participants):
            if p.uid == user.uid:
                res.extra_participants.pop(i)
                self._save()
                return {"ok": True, "promoted": None, "role": "substitute"}

        return {"ok": False, "error": "你未报名该事件"}

    # ==================================================================
    # 持久化 / Persistence
    # ==================================================================
    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for idx_str, d in data.items():
                res = Reservation.from_dict(d)
                self._res[res.index] = res
        except Exception:
            self._res = {}

    def _save(self) -> None:
        try:
            data = {str(idx): res.to_dict() for idx, res in self._res.items()}
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ==================================================================
    # 定时循环 / Scheduled Loop
    # ==================================================================
    async def _loop(self) -> None:
        """每 10 分钟扫描：过期标记+通知 / 30 分钟提醒。"""
        while True:
            try:
                await asyncio.sleep(LOOP_INTERVAL)
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception:
                # 单轮出错不影响后续
                continue

    async def _scan(self) -> None:
        """扫描所有预约，处理过期与提醒。"""
        now = time.time()
        changed = False
        for res in list(self._res.values()):
            if res.is_expired:
                continue
            try:
                time_until = res.event_date - now
                if time_until <= 0:
                    # 已过期
                    res.is_expired = True
                    changed = True
                    await self._notify(res.group_key,
                        f"⏰ 事件 #{res.index} 「{res.name}」已开始/结束。")
                elif time_until <= self._lead * 60 and res.is_noticed == 0:
                    # 30 分钟内提醒
                    res.is_noticed = 1
                    changed = True
                    dt = datetime.fromtimestamp(res.event_date)
                    await self._notify(res.group_key,
                        f"🔔 事件 #{res.index}「{res.name}」将于 "
                        f"{dt.strftime('%H:%M')} 开始，请准时参加！")
            except Exception:
                continue
        if changed:
            self._save()

    async def _notify(self, group_key: str, text: str) -> None:
        """通过 context.send_message 主动发送提醒。失败仅记日志。"""
        try:
            from astrbot.api.message_components import MessageChain, Plain
        except Exception:
            try:
                from astrbot.core.platform.message_components import MessageChain
                from astrbot.api.message_components import Plain
            except Exception:
                return
        try:
            await self._ctx.send_message(group_key, MessageChain([Plain(text)]))
        except Exception:
            # 某些平台（如 qq_official）不支持主动发送，静默失败
            pass
