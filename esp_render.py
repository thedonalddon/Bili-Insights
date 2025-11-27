#!/usr/bin/env python3
# esp_render.py
#
# 生成单页 800x480 四色墨水屏仪表盘：
# - 顶部：头像 + 账号名 + 简介 + 日期
# - 核心数据：总粉丝 / 总播放 + 今日增量
# - 图表：近 15 日涨粉 / 播放（日增）
# - 最近视频：标题、BV、发布日期、七个指标（总数 + 日增）+ 近 7 日播放日增折线
#
# 输出：
#   esp_output/dashboard_preview.png        # 原始 RGB 预览
#   esp_output/dashboard7c_preview.png      # 7C 调色板量化后的预览
#   esp_output/dashboard7c_800x480.bin      # 给 ESP32 下载的原始 7C 帧缓冲（800*480 字节）
#
# 依赖：Pillow
#   pip install pillow

import os
from typing import Dict, Any, List, Tuple
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from config import ACCOUNT_NAME, ACCOUNT_INTRO, AVATAR_PATH
from db import (
    get_latest_account_snapshot,
    get_last_two_account_snapshots,
    get_account_history,
    get_latest_video_snapshots,
    get_video_history,
)

# ==========================
# 基本参数 & 配色
# ==========================

W, H = 800, 480

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)

# 简单 4x4 Bayer 抖动矩阵，用于灰度 → 黑白抖动
BAYER_4x4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]

OUTPUT_DIR = "esp_output"
V_MARGIN = 18  # 分割线与内容之间的统一竖向留白


# ==========================
# 字体加载（可将自定义中文字体放入esp32/resource/fonts/中）
# ==========================

def load_font(size: int) -> ImageFont.ImageFont:
    # 优先使用项目内的自定义中文字体，其次尝试常见系统字体
    candidates = [
        # 项目自带字体
        "esp32/resources/fonts/LXGWFasmartGothicMN.ttf",
        # "esp32/resources/fonts/LXGWHeartSerifCL.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        # Linux Noto
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


# 字体层级：标题 > 核心数字 > 普通文字 > 标注
FONT_NAME = load_font(36)
FONT_TAGLINE = load_font(18)
FONT_DATE = load_font(20)
FONT_METRIC_BIG = load_font(40)
FONT_METRIC_INC = load_font(32)
FONT_METRIC_LABEL = load_font(20)
FONT_SMALL = load_font(18)
FONT_TINY = load_font(14)

# ==========================
# 圆角矩形工具
# ==========================

def draw_round_rect(draw: ImageDraw.ImageDraw,
                    bbox: Tuple[int, int, int, int],
                    radius: int = 8,
                    fill=None,
                    outline=None,
                    width: int = 1):
    """
    使用 rounded_rectangle 统一绘制圆角矩形。
    """
    try:
        draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        # 兼容旧 Pillow：退化为普通矩形
        draw.rectangle(bbox, fill=fill, outline=outline, width=width)

# ==========================
# 字体测量工具
# ==========================

def measure_text(text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    """
    Pillow 新版本中不推荐使用 draw.textsize，这里统一用 font.getbbox 计算文字宽高。
    """
    if not text:
        return 0, 0
    box = font.getbbox(text)  # (x0, y0, x1, y1)
    return box[2] - box[0], box[3] - box[1]


# ==========================
# 工具函数
# ==========================

def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def short_number(n: int) -> str:
    """不缩写，直接返回完整数字字符串。"""
    return str(int(n))


# 中文数字格式化（万单位）
def format_cn_number(n: int) -> str:
    """
    将数字格式化为适合界面显示的形式：
    小于 10000：原样整数
    大于等于 10000：x.x万（去掉多余的 0 和小数点）
    """
    n_int = int(n)
    if abs(n_int) < 10000:
        return str(n_int)
    sign = "-" if n_int < 0 else ""
    val = abs(n_int) / 10000.0
    txt = f"{val:.1f}".rstrip("0").rstrip(".")
    return f"{sign}{txt}万"


def trunc_text(draw: ImageDraw.ImageDraw, text: str,
               max_width: int, font: ImageFont.ImageFont) -> str:
    w, _ = measure_text(text, font)
    if w <= max_width:
        return text
    for i in range(len(text), 0, -1):
        t = text[:i] + "…"
        w2, _ = measure_text(t, font)
        if w2 <= max_width:
            return t
    return text


def draw_line_chart(draw: ImageDraw.ImageDraw,
                    rect: Tuple[int, int, int, int],
                    values: List[int],
                    title: str,
                    labels: List[str] = None,
                    line_color=RED):
    """
    在 rect 内画一条简单折线图：
    rect: (x0, y0, x1, y1)
    values: 按时间顺序排列的日增
    """
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return

    title_h = 20
    chart_top = y0 + title_h + 4
    chart_bottom = y1 - 14
    chart_left = x0 + 10
    chart_right = x1 - 10

    # 标题
    if title:
        draw.text((x0 + 4, y0), title, font=FONT_SMALL, fill=BLACK)

    if chart_right <= chart_left or chart_bottom <= chart_top:
        return

    # 坐标框（圆角）
    draw_round_rect(draw,
                    (chart_left, chart_top, chart_right, chart_bottom),
                    radius=6, fill=None, outline=BLACK, width=1)

    if not values:
        return

    n = len(values)
    max_v = max(values)
    min_v = min(values)
    if max_v == min_v:
        max_v = min_v + 1  # 防止除 0

    def val_to_y(v: int) -> int:
        scale = (v - min_v) / (max_v - min_v)
        return int(chart_bottom - 4 - scale * (chart_bottom - chart_top - 8))

    # x 坐标等分
    if n == 1:
        xs = [(chart_left + chart_right) // 2]
    else:
        span = (chart_right - chart_left - 8)
        xs = [chart_left + 4 + int(i * span / (n - 1)) for i in range(n)]

    ys = [val_to_y(v) for v in values]

    # 折线
    for i in range(1, n):
        draw.line((xs[i - 1], ys[i - 1], xs[i], ys[i]),
                  fill=line_color, width=4)

    # 每个点画一个小圆点（红点）
    for i in range(n):
        r = 4
        draw.ellipse((xs[i] - r, ys[i] - r, xs[i] + r, ys[i] + r),
                     fill=line_color, outline=line_color)

    # 下方标注日期：优先为每个点标注，其次回退到首尾日期
    if labels:
        if len(labels) == n:
            # 每个点下方都有对应日期
            for i, lab in enumerate(labels):
                lw, lh = measure_text(lab, FONT_TINY)
                draw.text((xs[i] - lw // 2, chart_bottom + 2),
                          lab, font=FONT_TINY, fill=BLACK)
        elif len(labels) >= 2:
            # 回退：只标起止日期
            left_label = labels[0]
            right_label = labels[-1]
            lw, lh = measure_text(left_label, FONT_TINY)
            rw, rh = measure_text(right_label, FONT_TINY)
            draw.text((chart_left, chart_bottom + 2),
                      left_label, font=FONT_TINY, fill=BLACK)
            draw.text((chart_right - rw, chart_bottom + 2),
                      right_label, font=FONT_TINY, fill=BLACK)


# ==========================
# 数据准备（全部从本地 db 读）
# ==========================

def compute_deltas(seq: List[int]) -> List[int]:
    if len(seq) < 2:
        return []
    return [seq[i] - seq[i - 1] for i in range(1, len(seq))]


def build_account_context() -> Dict[str, Any]:
    latest = get_latest_account_snapshot()
    snaps = get_last_two_account_snapshots()
    history = get_account_history(30)  # 从中取 15 日

    daily_diff = None
    if len(snaps) >= 2:
        latest_snap, prev = snaps[0], snaps[1]

        def diff(field: str) -> int:
            return int(latest_snap.get(field) or 0) - int(prev.get(field) or 0)

        daily_diff = {
            "inc_follower": diff("follower"),
            "inc_total_view": diff("total_view"),
        }

    # 15 日涨粉/播放日增
    history_sorted = sorted(
        history,
        key=lambda r: str(r.get("snapshot_date") or "")
    )
    date_list: List[str] = []
    for r in history_sorted:
        d = r.get("snapshot_date")
        if d:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                # 只显示日
                date_list.append(f"{dt.day}")
            except Exception:
                date_list.append(str(d))
        else:
            date_list.append("")

    follower_list: List[int] = [int(r.get("follower") or 0) for r in history_sorted]
    view_list: List[int] = [int(r.get("total_view") or 0) for r in history_sorted]

    follower_deltas = compute_deltas(follower_list)
    view_deltas = compute_deltas(view_list)

    # deltas 的日期标签与第二个点开始对齐
    delta_labels = date_list[1:] if len(date_list) > 1 else []
    follower_labels = delta_labels
    view_labels = delta_labels

    # 只保留最近 7 天的日增，用于中间两张卡片
    follower_deltas_15 = follower_deltas[-7:] if len(follower_deltas) > 7 else follower_deltas
    view_deltas_15 = view_deltas[-7:] if len(view_deltas) > 7 else view_deltas

    follower_labels_15 = follower_labels[-len(follower_deltas_15):] if follower_deltas_15 else []
    view_labels_15 = view_labels[-len(view_deltas_15):] if view_deltas_15 else []

    return {
        "latest": latest,
        "daily_diff": daily_diff,
        "follower_deltas_15": follower_deltas_15,
        "view_deltas_15": view_deltas_15,
        "follower_labels_15": follower_labels_15,
        "view_labels_15": view_labels_15,
    }


def build_video_context() -> Dict[str, Any]:
    videos = get_latest_video_snapshots() or []
    if not videos:
        return {
            "latest_video": None,
            "metric_deltas": {},
            "view_deltas_7": [],
        }

    # 按发布时间选最近一条
    def get_pub_ts(v: Dict[str, Any]) -> int:
        return int(v.get("pubdate") or v.get("ctime") or 0)

    latest_video = max(videos, key=get_pub_ts)
    bvid = latest_video.get("bvid")

    # 视频历史（用于总量趋势 & 7 日增量）
    history_rows = get_video_history(bvid) or []
    history_sorted = sorted(
        history_rows,
        key=lambda r: str(r.get("snapshot_date") or "")
    )

    date_list: List[str] = []
    for r in history_sorted:
        d = r.get("snapshot_date")
        if d:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                # 只显示日
                date_list.append(f"{dt.day}")
            except Exception:
                date_list.append(str(d))
        else:
            date_list.append("")

    # 最近 7 日播放日增
    views = [int(r.get("view") or 0) for r in history_sorted]
    view_deltas = compute_deltas(views)
    view_deltas_7 = view_deltas[-7:] if len(view_deltas) > 7 else view_deltas
    delta_labels = date_list[1:] if len(date_list) > 1 else []
    if view_deltas_7:
        view_labels_7 = delta_labels[-len(view_deltas_7):]
    else:
        view_labels_7 = []

    # 最近一次与前一次的各项日增
    metric_deltas: Dict[str, int] = {}
    if len(history_sorted) >= 2:
        last = history_sorted[-1]
        prev = history_sorted[-2]

        def d(field: str) -> int:
            return int(last.get(field) or 0) - int(prev.get(field) or 0)

        metric_deltas = {
            "view": d("view"),
            "like": d("like"),
            "coin": d("coin"),
            "favorite": d("favorite"),
            "reply": d("reply"),
            "danmaku": d("danmaku"),
            "share": d("share"),
        }

    return {
        "latest_video": latest_video,
        "metric_deltas": metric_deltas,
        "view_deltas_7": view_deltas_7,
        "view_labels_7": view_labels_7,
    }


# ==========================
# 头像绘制（预览保留灰度，墨水屏按亮度转黑）
# ==========================

def draw_avatar(img: Image.Image, x: int, y: int, size: int = 120):
    draw = ImageDraw.Draw(img)
    radius = size // 6  # 适中的圆角半径

    # 阴影（圆角）
    shadow_offset = 4
    shadow_box = (x + shadow_offset, y + shadow_offset,
                  x + size + shadow_offset, y + size + shadow_offset)
    draw_round_rect(draw, shadow_box, radius=radius,
                    fill=(200, 200, 200), outline=None, width=0)

    if not os.path.exists(AVATAR_PATH):
        # 占位框（圆角）
        box = (x, y, x + size, y + size)
        draw_round_rect(draw, box, radius=radius,
                        fill=None, outline=BLACK, width=2)
        # 画个 X
        draw.line((x, y, x + size, y + size), fill=BLACK, width=2)
        draw.line((x + size, y, x, y + size), fill=BLACK, width=2)
        return

    try:
        av = Image.open(AVATAR_PATH).convert("L")  # 灰度
        av = av.resize((size, size), Image.LANCZOS)
        av_rgb = Image.merge("RGB", (av, av, av))

        # 创建圆角 mask
        mask = Image.new("L", (size, size), 0)
        mdraw = ImageDraw.Draw(mask)
        try:
            mdraw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
        except AttributeError:
            mdraw.rectangle((0, 0, size, size), fill=255)

        # 用 mask 粘贴，实现头像四角裁掉
        img.paste(av_rgb, (x, y), mask)
    except Exception:
        box = (x, y, x + size, y + size)
        draw_round_rect(draw, box, radius=radius,
                        fill=None, outline=BLACK, width=2)


# ==========================
# 页面绘制：单页仪表盘
# ==========================

def render_dashboard(account_ctx: Dict[str, Any],
                     video_ctx: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    latest = account_ctx.get("latest") or {}
    daily = account_ctx.get("daily_diff") or {}
    follower_deltas_15 = account_ctx.get("follower_deltas_15") or []
    view_deltas_15 = account_ctx.get("view_deltas_15") or []
    follower_labels_15 = account_ctx.get("follower_labels_15") or []
    view_labels_15 = account_ctx.get("view_labels_15") or []

    snapshot_date = latest.get("snapshot_date") or ""
    follower = int(latest.get("follower") or 0)
    total_view = int(latest.get("total_view") or 0)
    inc_follower = int(daily.get("inc_follower") or 0)
    inc_view = int(daily.get("inc_total_view") or 0)

    latest_video = video_ctx.get("latest_video")
    metric_deltas: Dict[str, int] = video_ctx.get("metric_deltas") or {}
    view_deltas_7 = video_ctx.get("view_deltas_7") or []
    view_labels_7 = video_ctx.get("view_labels_7") or []

    # ===== 顶部区域：头像 + 名称 + 简介 + 日期 =====
    avatar_x, avatar_y, avatar_size = 30, 18, 80
    draw_avatar(img, x=avatar_x, y=avatar_y, size=avatar_size)

    # 名称 / 简介整体向左，贴近头像
    name_x = avatar_x + avatar_size + 20
    name_y = avatar_y + 8
    tagline_x = name_x
    tagline_y = avatar_y + 48

    # 名称
    draw.text((name_x, name_y), ACCOUNT_NAME, font=FONT_NAME, fill=BLACK)
    # 简介
    draw.text((tagline_x, tagline_y), ACCOUNT_INTRO, font=FONT_TAGLINE, fill=BLACK)

    # 日期，与简介同一高度，右对齐
    if snapshot_date:
        w_d, h_d = measure_text(snapshot_date, FONT_DATE)
        draw.text((W - w_d - 20, tagline_y), snapshot_date, font=FONT_DATE, fill=BLACK)

    # 计算 header 内容的最下边，用于统一分割线留白
    name_w, name_h = measure_text(ACCOUNT_NAME, FONT_NAME)
    tag_w, tag_h = measure_text(ACCOUNT_INTRO, FONT_TAGLINE)
    header_bottom = max(
        avatar_y + avatar_size,
        name_y + name_h,
        tagline_y + tag_h,
    )

    header_h = header_bottom + V_MARGIN
    # 第一条下划线
    draw.line((20, header_h, W - 20, header_h), fill=BLACK, width=1)

    # ===== 中段：两张综合卡片（涨粉 / 播放） =====
    cards_top = header_h + V_MARGIN
    cards_bottom = cards_top + 210
    card_w = (W - 60) // 2
    gap_x = 20

    def draw_stat_card(x0: int, y0: int, w: int, h: int,
                       title: str, total: int, inc: int, series: List[int], labels: List[str]):
        # 阴影（圆角）
        shadow_offset = 4
        shadow_box = (x0 + shadow_offset, y0 + shadow_offset,
                      x0 + w + shadow_offset, y0 + h + shadow_offset)
        draw_round_rect(draw, shadow_box, radius=10,
                        fill=(200, 200, 200), outline=None, width=0)

        # 主卡片浅黄底（圆角）
        card_box = (x0, y0, x0 + w, y0 + h)
        draw_round_rect(draw, card_box, radius=10,
                        fill=(255, 255, 220), outline=BLACK, width=2)

        # 标题
        draw.text((x0 + 10, y0 + 6), title, font=FONT_METRIC_LABEL, fill=BLACK)

        # 总数 & 日增：略下移，但整体压缩高度，为折线图腾出空间
        total_text = short_number(total)
        w_t, h_t = measure_text(total_text, FONT_METRIC_BIG)
        num_center_y = y0 + 50
        draw.text((x0 + 10, num_center_y - h_t // 2), total_text, font=FONT_METRIC_BIG, fill=BLACK)

        sign = "+" if inc >= 0 else ""
        inc_text = f"{sign}{inc}"
        w_i, h_i = measure_text(inc_text, FONT_METRIC_INC)
        draw.text((x0 + w - w_i - 12, num_center_y - h_i // 2), inc_text, font=FONT_METRIC_INC, fill=RED)

        # 折线图区域：整体拉高一点，利用中间空白
        chart_rect = (x0 + 10, y0 + 66, x0 + w - 10, y0 + h - 12)
        draw_line_chart(draw, chart_rect, series, "", labels=labels, line_color=RED)

    # 左：涨粉卡片
    draw_stat_card(
        x0=20,
        y0=cards_top,
        w=card_w,
        h=210,
        title="涨粉",
        total=follower,
        inc=inc_follower,
        series=follower_deltas_15,
        labels=follower_labels_15,
    )

    # 右：播放卡片
    draw_stat_card(
        x0=20 + card_w + gap_x,
        y0=cards_top,
        w=card_w,
        h=210,
        title="播放",
        total=total_view,
        inc=inc_view,
        series=view_deltas_15,
        labels=view_labels_15,
    )

    charts_bottom = cards_bottom
    second_line_y = charts_bottom + V_MARGIN
    # 第二条分割线
    draw.line((20, second_line_y, W - 20, second_line_y), fill=BLACK, width=1)

    # ===== 底部：最近视频 =====
    base_y = second_line_y + V_MARGIN

    if not latest_video:
        draw.text((20, base_y), "最近发布：暂无视频数据", font=FONT_METRIC_LABEL, fill=BLACK)
        return img

    title = latest_video.get("title") or ""
    view = int(latest_video.get("view") or 0)
    like = int(latest_video.get("like") or 0)
    coin = int(latest_video.get("coin") or 0)
    fav = int(latest_video.get("favorite") or 0)
    reply = int(latest_video.get("reply") or 0)
    danmaku = int(latest_video.get("danmaku") or 0)
    share = int(latest_video.get("share") or 0)

    pub_ts = int(latest_video.get("pubdate") or latest_video.get("ctime") or 0)
    pub_str = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d") if pub_ts else ""

    # 第一行：最近发布：标题（黑色） + 右侧日期
    prefix = "最近发布："
    prefix_w, _ = measure_text(prefix, FONT_METRIC_LABEL)
    max_title_w = W - 40 - 120  # 右侧预留日期宽度
    title_shown = trunc_text(draw, title, max_title_w - prefix_w, FONT_METRIC_LABEL)

    draw.text((20, base_y), prefix, font=FONT_METRIC_LABEL, fill=BLACK)
    draw.text((20 + prefix_w, base_y), title_shown, font=FONT_METRIC_LABEL, fill=BLACK)

    if pub_str:
        pub_w, _ = measure_text(pub_str, FONT_METRIC_LABEL)
        draw.text((W - pub_w - 20, base_y), pub_str, font=FONT_METRIC_LABEL, fill=BLACK)

    metrics_y1 = base_y + 30
    metrics_y2 = metrics_y1 + 28

    def draw_metric_small_card(x0: int, y0: int, w: int, h: int,
                               label: str, total: int, inc: int):
        # 阴影（圆角）
        shadow_offset = 3
        shadow_box = (x0 + shadow_offset, y0 + shadow_offset,
                      x0 + w + shadow_offset, y0 + h + shadow_offset)
        draw_round_rect(draw, shadow_box, radius=8,
                        fill=(200, 200, 200), outline=None, width=0)

        # 主卡片浅黄底（圆角）
        card_box = (x0, y0, x0 + w, y0 + h)
        draw_round_rect(draw, card_box, radius=8,
                        fill=(255, 255, 220), outline=BLACK, width=1)

        # 左侧标签：小号文字 + 方形边框（播 / 赞 / 币 / 藏）
        lw, lh = measure_text(label, FONT_SMALL)
        tag_pad_x = 6
        tag_pad_y = 2  # 略减小上下 padding，让标签高度更紧凑
        tag_x0 = x0 + 10
        # 先让文字在整个小卡片内垂直居中，再整体下移 1px
        text_y = y0 + (h - lh) // 2 + 1
        # 再根据文字位置反推标签矩形，使文字在矩形内同样大致居中
        tag_y0 = text_y - tag_pad_y
        tag_y1 = text_y + lh + tag_pad_y
        tag_x1 = tag_x0 + lw + 2 * tag_pad_x

        # 标签外框（圆角）
        draw_round_rect(draw,
                        (tag_x0, tag_y0, tag_x1, tag_y1),
                        radius=6, fill=None, outline=BLACK, width=1)
        # 标签文字
        text_x = tag_x0 + tag_pad_x
        draw.text((text_x, text_y), label, font=FONT_SMALL, fill=BLACK)

        # 总数（紧挨标签，略大） & 日增（右侧，小一点）
        total_text = format_cn_number(total)
        sign = "+" if inc >= 0 else ""
        inc_text = f"{sign}{inc}"

        w_t, h_t = measure_text(total_text, FONT_METRIC_LABEL)
        w_i, h_i = measure_text(inc_text, FONT_SMALL)

        total_x = tag_x1 + 8
        total_y = y0 + (h - h_t) // 2
        draw.text((total_x, total_y), total_text, font=FONT_METRIC_LABEL, fill=BLACK)

        inc_x = x0 + w - w_i - 10
        inc_y = y0 + (h - h_i) // 2
        draw.text((inc_x, inc_y), inc_text, font=FONT_SMALL, fill=RED)

    def get_inc(field: str) -> int:
        return int(metric_deltas.get(field) or 0)

    card_h = 48
    total_width = W - 40  # 左右各留 20px
    gap_x = 20
    card_w = int((total_width - 3 * gap_x) / 4)
    start_x = 20
    start_y = metrics_y1

    # 四个小方块：播 / 赞 / 币 / 藏
    labels = ["播", "赞", "币", "藏"]
    totals = [view, like, coin, fav]
    fields = ["view", "like", "coin", "favorite"]

    for i in range(4):
        x0 = start_x + i * (card_w + gap_x)
        draw_metric_small_card(x0, start_y, card_w, card_h, labels[i], totals[i], get_inc(fields[i]))

    metrics_bottom = start_y + card_h

    # 底部保留一定留白，避免画面过于拥挤
    # （如后续想利用这块区域再添加元素，可以在这里扩展）
    return img



def clamp01(a):
    return np.clip(a, 0.0, 1.0)


# ==========================
# PNG -> GxEPD2 7C 原始帧缓冲（二进制）
# ==========================

# GoodDisplay / GxEPD2 7色色码（这里只用其中 4 色）
# code, (R,G,B)
PALETTE_7C = [
    (0xFF, (255, 255, 255)),  # white
    (0x00, (0,   0,   0  )),  # black
    (0xE5, (230, 0,   18 )),  # red
    (0xFC, (255, 242, 0  )),  # yellow
]


def export_dashboard_7c_bin(img: Image.Image,
                            out_bin_name: str = "dashboard7c_800x480.bin",
                            preview_name: str = "dashboard7c_preview.png"):
    """
    将 RGB 仪表盘图像量化为 GoodDisplay / GxEPD2 7C 专用格式（1 byte / pixel 色码），
    输出：
      - esp_output/{out_bin_name} : 原始 7C 帧缓冲二进制（800*480 字节）
      - esp_output/{preview_name} : 使用 7C 调色板重新着色后的预览 PNG
    """
    # 保存一份原始预览（完整 RGB）
    preview_rgb_path = os.path.join(OUTPUT_DIR, "dashboard_preview.png")
    img_rgb = img.convert("RGB")
    if img_rgb.size != (W, H):
        img_rgb = img_rgb.resize((W, H), Image.LANCZOS)
    img_rgb.save(preview_rgb_path)

    # 准备量化输入
    arr = np.asarray(img_rgb, dtype=np.float32) / 255.0  # H x W x 3

    codes = np.array([c for c, _rgb in PALETTE_7C], dtype=np.uint8)
    colors = np.array([_rgb for _c, _rgb in PALETTE_7C], dtype=np.float32) / 255.0  # N x 3

    h, w, _ = arr.shape
    out = np.zeros((h, w), dtype=np.uint8)

    # Floyd–Steinberg 抖动
    for y in range(h):
        if y % 40 == 0:
            print(f"[esp_render] dither row {y}/{h}")
        for x in range(w):
            old = arr[y, x]
            diff = colors - old
            dist2 = np.sum(diff * diff, axis=1)
            idx = int(np.argmin(dist2))
            new = colors[idx]
            out[y, x] = codes[idx]
            err = old - new

            if x + 1 < w:
                arr[y, x + 1] = clamp01(arr[y, x + 1] + err * (7.0 / 16.0))
            if y + 1 < h:
                if x > 0:
                    arr[y + 1, x - 1] = clamp01(arr[y + 1, x - 1] + err * (3.0 / 16.0))
                arr[y + 1, x] = clamp01(arr[y + 1, x] + err * (5.0 / 16.0))
                if x + 1 < w:
                    arr[y + 1, x + 1] = clamp01(arr[y + 1, x + 1] + err * (1.0 / 16.0))

    flat = out.flatten()
    assert flat.size == W * H

    # 写 C 头文件

    # 写二进制帧缓冲
    out_bin_path = os.path.join(OUTPUT_DIR, out_bin_name)
    with open(out_bin_path, "wb") as f:
        f.write(flat.tobytes())
    print(f"[esp_render] 7C bin written: {out_bin_path}  ({flat.size} bytes)")

    # 生成 7C 量化后的预览图
    sim = Image.new("RGB", (W, H), WHITE)
    sim_px = sim.load()

    # 将色码映射回对应 RGB
    code_to_rgb = {code: rgb for code, rgb in PALETTE_7C}
    for y in range(H):
        for x in range(W):
            code = int(out[y, x])
            rgb = code_to_rgb.get(code, (255, 255, 255))
            sim_px[x, y] = rgb

    preview_path = os.path.join(OUTPUT_DIR, preview_name)
    sim.save(preview_path)

    print(f"[esp_render] 7C preview: {preview_path}")


# ==========================
# 主流程
# ==========================

def main():
    ensure_output_dir()

    account_ctx = build_account_context()
    video_ctx = build_video_context()

    img = render_dashboard(account_ctx, video_ctx)
    export_dashboard_7c_bin(img)

    print("[esp_render] dashboard rendered & 7C bin generated.")


if __name__ == "__main__":
    main()