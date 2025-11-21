#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BiliInsights main.py

功能：
1. 使用 WBI + Cookie 从 /x/space/wbi/arc/search 拉取指定 UID 的所有投稿列表
2. 打印总数，预览前若干条
后续你可以在此基础上再接入：
- fetch_video_info(bvid) 做单条视频七要素统计
- 落库（SQLite / CSV）
- 账号维度聚合 & 日增量计算
"""

import time
import hashlib
import urllib.parse
from functools import reduce
from typing import Dict, Any, List, Tuple

import requests

from config import BILI_COOKIE, MY_MID

# =============================
# 基础配置
# =============================

SPACE_ARCHIVE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

HEADERS = {
    # 正常浏览器 UA，别乱改
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://space.bilibili.com/",
    "Origin": "https://space.bilibili.com",
    # 关键：用 config.py 里的 Cookie
    "Cookie": BILI_COOKIE,
}

# =============================
# WBI 签名相关
# 来源：bilibili-API-collect wbi.md
# =============================

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_mixin_key(orig: str) -> str:
    """根据官方混淆表，从 img_key + sub_key 生成 mixin_key"""
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def sign_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    """
    对请求参数进行 WBI 签名，返回新增了 wts / w_rid 的参数字典：
    - 添加 wts（时间戳）
    - 按 key 排序
    - 过滤 value 中 "!'()*"
    - 计算 w_rid = md5(query + mixin_key)
    """
    mixin_key = get_mixin_key(img_key + sub_key)
    curr_time = int(time.time())

    # 拷贝一份，避免修改调用者的 dict
    params = dict(params)
    params["wts"] = curr_time

    # key 排序
    params = dict(sorted(params.items()))

    # 过滤特殊字符
    filtered = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in params.items()
    }

    # urlencode
    query = urllib.parse.urlencode(filtered)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    filtered["w_rid"] = w_rid
    return filtered


# 简单缓存 img_key / sub_key，避免每次都打 nav
_WBI_KEYS: Tuple[str, str] | None = None
_WBI_KEYS_TS: float | None = None
_WBI_KEYS_TTL = 3600  # 1 小时更新一次即可


def get_wbi_keys() -> Tuple[str, str]:
    """
    从 /x/web-interface/nav 拉取 img_key / sub_key
    未登录也能拿到 wbi_img，但我们这里带了 Cookie，更接近真实环境
    """
    global _WBI_KEYS, _WBI_KEYS_TS
    now = time.time()
    if _WBI_KEYS and _WBI_KEYS_TS and (now - _WBI_KEYS_TS < _WBI_KEYS_TTL):
        return _WBI_KEYS

    resp = requests.get(NAV_URL, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    wbi_img = data["data"]["wbi_img"]
    img_url = wbi_img["img_url"]
    sub_url = wbi_img["sub_url"]

    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]

    _WBI_KEYS = (img_key, sub_key)
    _WBI_KEYS_TS = now
    return _WBI_KEYS


# =============================
# 解决 -352：dm_* 风控参数
# 这些值来自前端实际请求，可以直接复用
# =============================

DM_IMG_LIST = "[]"
DM_IMG_STR = "V2ViR0wgMS"
DM_COVER_IMG_STR = "SW50ZWwoUikgSEQgR3JhcGhpY3NJbnRlbA"


def fetch_user_archives(
    mid: int,
    page_size: int = 30,
    max_pages: int = 50,
    sleep_sec: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    使用 /x/space/wbi/arc/search 拉取指定 mid 的投稿列表。

    返回列表中每个元素大致结构：
    {
        "bvid": "...",
        "aid": 123,
        "title": "...",
        "created": 1700000000,
        "length": "35:40",
        "play": 123456,
        "comment": 789,
        ...
    }
    """
    img_key, sub_key = get_wbi_keys()

    all_archives: List[Dict[str, Any]] = []

    for pn in range(1, max_pages + 1):
        base_params = {
            "mid": mid,
            "ps": page_size,
            "tid": 0,
            "pn": pn,
            "keyword": "",
            "order": "pubdate",
            "platform": "web",
            "web_location": 1550101,
            "order_avoided": "true",
            # 风控参数
            "dm_img_list": DM_IMG_LIST,
            "dm_img_str": DM_IMG_STR,
            "dm_cover_img_str": DM_COVER_IMG_STR,
        }

        signed_params = sign_wbi(base_params, img_key, sub_key)

        resp = requests.get(
            SPACE_ARCHIVE_URL,
            params=signed_params,
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        code = data.get("code", 0)
        if code != 0:
            print(
                f"[warn] page {pn} 接口返回 code={code}, "
                f"message={data.get('message')!r}，停止。"
            )
            # -352 直接停，没必要继续撞
            break

        d = data.get("data") or {}
        vlist = (d.get("list") or {}).get("vlist") or []
        page_info = d.get("page") or {}
        total_pages = page_info.get("pages")
        total_count = page_info.get("count")

        if not vlist:
            print(f"[info] page {pn} 无更多视频，结束。")
            break

        all_archives.extend(vlist)

        print(
            f"[info] mid={mid} page {pn}/{total_pages}, "
            f"本页 {len(vlist)} 条，累计 {len(all_archives)} / 总计 {total_count}"
        )

        # 如果已经到最后一页，提前退出
        if total_pages is not None and pn >= int(total_pages):
            break

        time.sleep(sleep_sec)

    return all_archives


def main() -> None:
    print(f"[info] 开始拉取 mid={MY_MID} 的投稿列表...")
    videos = fetch_user_archives(MY_MID, page_size=30, max_pages=100, sleep_sec=0.5)
    print(f"[done] 共获取到 {len(videos)} 条视频记录。")

    # 预览前 10 条
    print("\n[preview] 前 10 条：")
    for v in videos[:10]:
        print(
            f"BV: {v.get('bvid')} | "
            f"标题: {v.get('title')} | "
            f"播放: {v.get('play')} | "
            f"评论: {v.get('comment')}"
        )


if __name__ == "__main__":
    main()
