"""render.py — 图片渲染引擎（jinja2 + playwright）。

将 QueryResult 渲染为 HTML，用 playwright 截图 #body 元素生成 PNG。
支持 3 种样式（normal 卡片网格 / lite 表格行 / text 纯文本）与 5 套主题 + 夜间模式。

中文渲染策略：引入 Google Fonts Noto Sans SC，并用 page.wait_for_timeout
确保字体下载完成后再截图。同时保留系统字体 fallback。
"""
from __future__ import annotations

from typing import Any

from jinja2 import Template

from .query import get_cfg_name, get_os_icon
from .utils import QueryResult, format_online_time


# ======================================================================
# 通用 CSS 变量与基础样式
# ======================================================================
_CSS_VARS = """
:root {
  --bg: {{ theme.bg }};
  --font: {{ theme.font }};
  --inner: {{ theme.inner }};
  --border: {{ theme.border }};
  --accent: #4B9EF5;
  --accent-hover: #3A8DE4;
  --success: #4CAF50;
  --warning: #FF9800;
  --danger: #F44336;
  --muted: #888888;
  --shadow: rgba(0,0,0,0.06);
  --radius: 10px;
  --font-size-xs: 11px;
  --font-size-sm: 12px;
  --font-size-base: 13px;
  --font-size-md: 14px;
  --font-size-lg: 16px;
  --font-size-xl: 18px;
  --font-size-2xl: 20px;
  --space-xs: 4px;
  --space-sm: 6px;
  --space-md: 8px;
  --space-lg: 12px;
  --space-xl: 16px;
  --space-2xl: 20px;
  --transition: all 0.2s ease;
}
"""

_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  font-family: "Noto Sans SC", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
               "WenQuanYi Micro Hei", "SimSun", "SimHei", sans-serif;
  background: var(--bg);
  color: var(--font);
  font-size: var(--font-size-base);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-display: swap;
}
#body { display: inline-block; min-width: 360px; }

/* 卡片基础 */
.card {
  background: var(--inner);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-lg) var(--space-xl);
  box-shadow: 0 1px 3px var(--shadow), 0 1px 2px rgba(0,0,0,0.04);
  transition: var(--transition);
}
.card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
}

/* 标题 */
.title { font-size: var(--font-size-lg); font-weight: 700; }
.title-xl { font-size: var(--font-size-2xl); font-weight: 700; }
.subtitle { font-size: var(--font-size-md); opacity: 0.85; }
.muted { color: var(--muted); font-size: var(--font-size-sm); }
.small { font-size: var(--font-size-xs); }

/* 分割线 */
.divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--border), transparent);
  margin: var(--space-md) 0;
}

/* 标签/徽章 */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: var(--font-size-xs);
  font-weight: 500;
  background: var(--border);
  color: var(--font);
}
.badge-blue { background: #E3F2FD; color: #1565C0; }
.badge-green { background: #E8F5E9; color: #2E7D32; }
.badge-orange { background: #FFF3E0; color: #EF6C00; }
.badge-red { background: #FFEBEE; color: #C62828; }

/* 链接 */
.link { color: var(--accent); text-decoration: none; cursor: pointer; }
.link:hover { color: var(--accent-hover); text-decoration: underline; }

/* 玩家列表 */
.player-list { list-style: none; }
.player-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 0;
  font-size: var(--font-size-sm);
}
.player-name { color: var(--success); font-weight: 500; }
.player-time { color: var(--muted); font-size: var(--font-size-xs); }

/* 表头 */
.info-row {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: 1px 0;
  font-size: var(--font-size-sm);
}
.info-label { opacity: 0.6; min-width: 48px; flex-shrink: 0; }
.info-value { font-weight: 500; }

/* 底部版权 */
.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-xl);
  font-size: var(--font-size-xs);
  color: var(--muted);
  border-top: 1px solid var(--border);
  margin-top: var(--space-md);
}
.footer a { color: var(--accent); text-decoration: none; }

/* 右上角 OS 标签 */
.os-tag {
  position: absolute;
  top: var(--space-md);
  right: var(--space-md);
  font-size: var(--font-size-xs);
  color: var(--muted);
  opacity: 0.6;
}

/* 离线 */
.offline {
  text-align: center;
  padding: var(--space-2xl) var(--space-xl);
  color: var(--muted);
  font-size: var(--font-size-md);
}

/* 网格布局 */
.grid {
  display: grid;
  gap: var(--space-lg);
  padding: var(--space-md);
}
.grid-1 { grid-template-columns: 1fr; max-width: 420px; }
.grid-2 { grid-template-columns: repeat(2, 1fr); max-width: 720px; }
.grid-3 { grid-template-columns: repeat(3, 1fr); max-width: 960px; }

/* 详情卡片 */
.detail-card { position: relative; max-width: 520px; }

/* 表格（lite） */
.lite-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-sm);
}
.lite-table th,
.lite-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.lite-table th {
  font-weight: 600;
  opacity: 0.7;
  font-size: var(--font-size-xs);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.lite-table tr:last-child td { border-bottom: none; }
.lite-table .idx { width: 40px; text-align: center; font-weight: 700; }
.lite-table .name-col { min-width: 280px; }
.lite-table .map-col { min-width: 160px; }
.lite-table .count-col { width: 80px; text-align: center; }
.lite-table .offline-row { opacity: 0.4; }

/* 响应式 */
@media (max-width: 640px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}
"""


# ======================================================================
# 模板 1：normal（列表卡片网格）
# ======================================================================
TEMPLATE_NORMAL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
""" + _CSS_VARS + _BASE_CSS + """
/* 列表专用 */
.header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-sm) var(--space-md);
  background: var(--inner);
  border-radius: var(--radius);
  margin-bottom: var(--space-md);
  border: 1px solid var(--border);
}
.header-bar .group-name { font-weight: 700; font-size: var(--font-size-md); }
.header-bar .hint { font-size: var(--font-size-xs); opacity: 0.6; }

.list-card {
  position: relative;
  padding: var(--space-md) var(--space-lg);
  min-width: 280px;
}
.list-card .sv-name {
  font-size: var(--font-size-lg);
  font-weight: 700;
  margin-bottom: var(--space-xs);
  padding-right: 40px; /* 给 OS 标签留空间 */
  line-height: 1.3;
}
.list-card .sv-meta {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
  font-size: var(--font-size-sm);
}
.list-card .sv-meta .count { font-weight: 600; }
.list-card .sv-map {
  font-size: var(--font-size-sm);
  color: var(--accent);
  margin-bottom: var(--space-xs);
  cursor: pointer;
}
.list-card .sv-cfg {
  font-size: var(--font-size-xs);
  opacity: 0.7;
  margin-bottom: var(--space-xs);
}
.list-card .sv-players {
  margin-top: var(--space-sm);
  padding-top: var(--space-xs);
  border-top: 1px dashed var(--border);
}
.list-card .sv-players .pl-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1px 0;
  font-size: var(--font-size-sm);
}
.list-card .sv-players .pl-name {
  color: #2E7D32;
  font-weight: 500;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.list-card .sv-players .pl-time {
  color: var(--muted);
  font-size: var(--font-size-xs);
  font-variant-numeric: tabular-nums;
}
.list-card .sv-players .pl-score {
  font-size: var(--font-size-xs);
  color: var(--muted);
  margin-right: 4px;
  font-variant-numeric: tabular-nums;
}
.list-card .sv-empty {
  text-align: center;
  color: var(--muted);
  font-size: var(--font-size-xs);
  padding: 2px 0;
  visibility: hidden;
}
</style>
</head>
<body>
<div id="body">

<div class="grid {% if results|length == 1 %}grid-1{% elif results|length == 2 %}grid-2{% else %}grid-3{% endif %}">
{% for r in results %}
  <div class="card list-card">
    {% if r.online %}
      <span class="os-tag">{{ r.os_icon }}</span>
      <div class="sv-name">{{ r.name }}</div>
      <div class="sv-meta">
        <span class="count">{{ r.player_count[0] }}/{{ r.player_count[1] }}</span>
        {% if r.vac %}
          <span class="badge badge-green">VAC</span>
        {% endif %}
      </div>
      {% if r.cfg_name %}<div class="sv-cfg">{{ r.cfg_name }}</div>{% endif %}
      <div class="sv-map">{{ r.map_name }}</div>
      {% if r.players %}
      <div class="sv-players">
        {% for p in r.display_players %}
          {% if p.name %}
          <div class="pl-item">
            <span><span class="pl-score">[{{ p.score }}]</span><span class="pl-name">{{ p.name }}</span></span>
            <span class="pl-time">{{ p.duration }}</span>
          </div>
          {% else %}
          <div class="sv-empty">&nbsp;</div>
          {% endif %}
        {% endfor %}
      </div>
      {% endif %}
    {% else %}
      <div class="sv-name" style="opacity:0.6;">{{ r.server_name }}</div>
      <div class="offline">服务器无响应</div>
    {% endif %}
  </div>
{% endfor %}
</div>

<div class="footer">
  <span>© AstrBot</span>
</div>

</div>
</body>
</html>"""


# ======================================================================
# 模板 2：lite（表格行）
# ======================================================================
TEMPLATE_LITE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
""" + _CSS_VARS + _BASE_CSS + """
.lite-wrap { padding: var(--space-md); }
.lite-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-sm) var(--space-md);
  background: var(--inner);
  border-radius: var(--radius);
  margin-bottom: var(--space-md);
  border: 1px solid var(--border);
}
.lite-header .group-name { font-weight: 700; }
</style>
</head>
<body>
<div id="body">
<div class="lite-wrap">
<div class="card" style="padding:0;">
<table class="lite-table">
<thead>
  <tr>
    <th class="idx">#</th>
    <th class="name-col">名称</th>
    <th class="map-col">地图</th>
    <th class="count-col">人数</th>
    <th style="width:60px;text-align:center;">OS</th>
  </tr>
</thead>
<tbody>
{% for r in results %}
  <tr class="{% if not r.online %}offline-row{% endif %}">
    <td class="idx">{{ loop.index }}</td>
    <td class="name-col">
      {% if r.online %}
        {{ r.name }}
        {% if r.cfg_name %}<span class="badge" style="margin-left:4px;">{{ r.cfg_name }}</span>{% endif %}
      {% else %}
        <span style="opacity:0.5;">{{ r.server_name }}</span>
      {% endif %}
    </td>
    <td class="map-col">
      {% if r.online %}
        <span class="link">{{ r.map_name }}</span>
      {% else %}—{% endif %}
    </td>
    <td class="count-col">
      {% if r.online %}{{ r.player_count[0] }}/{{ r.player_count[1] }}{% else %}—{% endif %}
    </td>
    <td style="text-align:center;font-size:11px;color:var(--muted);">
      {% if r.online %}{{ r.os_icon }}{% else %}—{% endif %}
    </td>
  </tr>
{% endfor %}
</tbody>
</table>
</div>
<div class="footer" style="margin-top:var(--space-md);">
  <span>© AstrBot</span>
</div>
</div>
</div>
</body>
</html>"""


# ======================================================================
# 模板 3：详情（单服务器完整卡片）
# ======================================================================
TEMPLATE_DETAIL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
""" + _CSS_VARS + _BASE_CSS + """
.detail-card {
  max-width: 480px;
  padding: var(--space-xl);
  position: relative;
}
.detail-card .sv-name {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  margin-bottom: var(--space-sm);
  padding-right: 50px;
  line-height: 1.3;
}
.detail-card .info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-xs) var(--space-lg);
  margin: var(--space-sm) 0;
}
.detail-card .info-grid .full { grid-column: 1 / -1; }
.detail-card .connect-addr {
  background: #F5F5F5;
  border: 1px dashed #DDD;
  border-radius: 6px;
  padding: 4px 10px;
  font-family: "SF Mono", "Fira Code", Consolas, monospace;
  font-size: var(--font-size-sm);
  color: #555;
  display: inline-block;
  margin-top: var(--space-xs);
}
.detail-card .player-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: var(--space-md);
  font-size: var(--font-size-sm);
}
.detail-card .player-table th,
.detail-card .player-table td {
  padding: 5px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.detail-card .player-table th {
  font-weight: 600;
  font-size: var(--font-size-xs);
  opacity: 0.7;
  background: rgba(0,0,0,0.02);
}
.detail-card .player-table td:first-child { font-variant-numeric: tabular-nums; }
.detail-card .player-table .pl-name { color: #2E7D32; font-weight: 500; }
.detail-card .player-table .pl-time { color: var(--muted); font-size: var(--font-size-xs); font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<div id="body">
<div class="card detail-card">
  {% if result.online %}
    <span class="os-tag">{{ result.os_icon }}</span>
    <div class="sv-name">{{ result.name }}</div>
    <div class="info-grid">
      <div class="info-row full"><span class="info-label">游戏</span><span class="info-value">{{ result.game }}</span></div>
      <div class="info-row full"><span class="info-label">地图</span><span class="info-value link">{{ result.map_name }}</span></div>
      {% if result.cfg_name %}<div class="info-row full"><span class="info-label">模式</span><span class="info-value">{{ result.cfg_name }}</span></div>{% endif %}
      <div class="info-row"><span class="info-label">玩家</span><span class="info-value">{{ result.player_count[0] }}/{{ result.player_count[1] }}</span></div>
      <div class="info-row"><span class="info-label">版本</span><span class="info-value">{{ result.version }}</span></div>
    </div>
    {% if result.players %}
    <div class="divider"></div>
    <table class="player-table">
      <thead>
        <tr><th style="width:50px;">分数</th><th>玩家</th><th style="width:80px;text-align:right;">在线时长</th></tr>
      </thead>
      <tbody>
        {% for p in result.display_players %}
          {% if p.name %}
          <tr>
            <td>{{ p.score }}</td>
            <td class="pl-name">{{ p.name }}</td>
            <td class="pl-time" style="text-align:right;">{{ p.duration }}</td>
          </tr>
          {% endif %}
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
  {% else %}
    <div class="sv-name" style="opacity:0.6;">{{ result.server_name }}</div>
    <div class="offline">服务器无响应</div>
  {% endif %}
</div>
<div class="footer" style="margin-top:var(--space-md);max-width:480px;">
  <span>© AstrBot</span>
</div>
</div>
</body>
</html>"""


# ======================================================================
# Renderer 类
# ======================================================================
class Renderer:
    """图片渲染器。playwright 浏览器在 start() 启动一次复用。"""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._available = False

    async def start(self) -> None:
        """启动 playwright 并 launch chromium。失败则 _available=False。"""
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                args=["--lang=zh-CN", "--font-render-hinting=none"]
            )
            self._available = True
        except Exception as e:  # noqa: BLE001
            self._available = False
            self._pw = None
            self._browser = None
            raise RuntimeError(f"playwright 启动失败: {e}") from e

    async def stop(self) -> None:
        """关闭 browser + playwright。"""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    # ----- 列表渲染 -----
    async def render_list(self, results: list[QueryResult], theme: dict[str, str],
                          style: str, max_players: int, output_ip: bool,
                          group_name: str = "") -> bytes | str:
        """渲染服务器列表。返回 PNG bytes（图片样式）或 str（text 样式）。"""
        if style == "text":
            return self.render_text(results, output_ip, group_name)

        # 准备模板数据
        view_data = [_build_list_view(r, max_players, output_ip, idx) for idx, r in enumerate(results, 1)]
        tmpl = TEMPLATE_NORMAL if style != "lite" else TEMPLATE_LITE
        html = Template(tmpl).render(
            results=view_data,
            theme=theme,
            group_name=group_name,
        )
        return await self._screenshot(html)

    # ----- 详情渲染 -----
    async def render_detail(self, result: QueryResult, theme: dict[str, str],
                            max_players: int, output_ip: bool) -> bytes | str:
        """渲染单服务器详情。"""
        view = _build_detail_view(result, max_players, output_ip)
        html = Template(TEMPLATE_DETAIL).render(result=view, theme=theme)
        return await self._screenshot(html)

    # ----- 纯文本 -----
    def render_text(self, results: list[QueryResult], output_ip: bool, group_name: str = "") -> str:
        """纯文本列表输出。"""
        lines: list[str] = []
        for idx, r in enumerate(results, 1):
            if r.online:
                lines.append(f"{idx}. {r.name}")
                lines.append(f"   地图: {r.map_name}")
                on, mx, bots = r.player_count
                lines.append(f"   人数: {on}/{mx}")
                cfg = get_cfg_name(r.rules)
                if cfg:
                    lines.append(f"   模式: {cfg}")
                if r.players:
                    lines.append("   玩家:")
                    for p in r.players:
                        lines.append(f"     [{p.get('score', 0)}] {p.get('name', '')} | {format_online_time(p.get('duration', 0))}")
            else:
                name = r.server.name if r.server else "未知"
                lines.append(f"{idx}. {name} — 服务器无响应")
            lines.append("")
        lines.append("© AstrBot")
        return "\n".join(lines).strip() or "无服务器"

    def render_detail_text(self, result: QueryResult, output_ip: bool) -> str:
        """纯文本详情输出。"""
        if not result.online:
            name = result.server.name if result.server else "未知"
            return f"{name}\n服务器无响应"
        lines = [result.name, f"游戏: {result.info.get('game', '') if result.info else ''}",
                 f"地图: {result.map_name}"]
        cfg = get_cfg_name(result.rules)
        if cfg:
            lines.append(f"模式: {cfg}")
        on, mx, bots = result.player_count
        lines.append(f"玩家: {on}/{mx}")
        if result.players:
            lines.append("")
            lines.append("玩家列表:")
            for p in result.players:
                lines.append(f"  [{p.get('score', 0)}] {p.get('name', '')} | {format_online_time(p.get('duration', 0))}")
        lines.append("")
        lines.append("© AstrBot")
        return "\n".join(lines)

    # ----- 截图 -----
    async def _screenshot(self, html: str) -> bytes:
        """渲染 HTML 并截取 #body 元素为 PNG。

        关键：确保字体加载完成后再截图，避免中文显示为方框。
        策略：
        1. networkidle 等待 Google Fonts 下载
        2. document.fonts.ready 等待字体实际就绪
        3. 额外 sleep 2000ms 给 Chromium 完成字形渲染
        """
        if not self._available or not self._browser:
            raise RuntimeError("renderer 不可用")
        page = await self._browser.new_page()
        try:
            await page.set_viewport_size({"width": 1000, "height": 800})
            await page.set_content(
                html,
                wait_until="networkidle",
                timeout=20000,
            )
            # 等待 Web 字体加载完成
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                pass
            # 给 Chromium 充足时间完成字形渲染（尤其对中文）
            await page.wait_for_timeout(2000)
            # 再调整视口到足够高度
            await page.set_viewport_size({"width": 1000, "height": 5000})
            body = page.locator("#body")
            await body.wait_for(state="attached", timeout=5000)
            png = await body.screenshot(type="png")
            return png
        finally:
            await page.close()


# ======================================================================
# 视图数据构建 / View Data Builders
# ======================================================================
def _build_list_view(r: QueryResult, max_players: int, output_ip: bool, idx: int) -> dict[str, Any]:
    """构建列表渲染用的视图 dict。"""
    if r.online:
        raw_players = r.players if not r.player_error else []
        min_rows = 4
        if len(raw_players) < min_rows:
            display = [{"name": p.get("name", ""), "score": p.get("score", 0),
                        "duration": format_online_time(p.get("duration", 0))} for p in raw_players]
            display += [{"name": "", "score": 0, "duration": ""}] * (min_rows - len(raw_players))
        else:
            display = [{"name": p.get("name", ""), "score": p.get("score", 0),
                        "duration": format_online_time(p.get("duration", 0))} for p in raw_players[:max_players]]
        info = r.info or {}
        return {
            "online": True,
            "name": r.name,
            "server_name": r.server.name if r.server else "",
            "map_name": r.map_name,
            "player_count": r.player_count,
            "os_icon": get_os_icon(info.get("platform", "")),
            "cfg_name": get_cfg_name(r.rules),
            "ip_str": f"{r.server.host}:{r.server.port}" if (output_ip and r.server) else "",
            "display_players": display,
            "vac": info.get("vac_enabled", False),
            "version": info.get("version", ""),
        }
    return {
        "online": False,
        "name": "",
        "server_name": r.server.name if r.server else "",
        "map_name": "",
        "player_count": (0, 0, 0),
        "os_icon": "",
        "cfg_name": "",
        "ip_str": "",
        "display_players": [],
        "vac": False,
        "version": "",
    }


def _build_detail_view(r: QueryResult, max_players: int, output_ip: bool) -> dict[str, Any]:
    """构建详情渲染用的视图 dict。"""
    if r.online:
        players = r.players if not r.player_error else []
        display = [{"name": p.get("name", ""), "score": p.get("score", 0),
                    "duration": format_online_time(p.get("duration", 0))} for p in players[:max(max_players, 8)]]
        info = r.info or {}
        return {
            "online": True,
            "name": r.name,
            "game": info.get("game", ""),
            "map_name": r.map_name,
            "player_count": r.player_count,
            "os_icon": get_os_icon(info.get("platform", "")),
            "cfg_name": get_cfg_name(r.rules),
            "ip_str": f"{r.server.host}:{r.server.port}" if (output_ip and r.server) else "",
            "players": players,
            "display_players": display,
            "version": info.get("version", ""),
        }
    return {
        "online": False,
        "name": "",
        "server_name": r.server.name if r.server else "",
        "map_name": "",
        "player_count": (0, 0, 0),
        "os_icon": "",
        "cfg_name": "",
        "ip_str": "",
        "players": [],
        "display_players": [],
        "version": "",
    }
