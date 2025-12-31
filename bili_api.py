# bili_api.py
# 本项目参考并使用了 bilibili-API-collect 项目中的部分 API：https://github.com/SocialSisterYi/bilibili-API-collect
# Original project licensed under the MIT License.
# Copyright © SocialSisterYi

import time
import hashlib
import urllib.parse
from functools import reduce
from typing import Dict, Any, List, Tuple

import requests

from config import BILI_COOKIE

# =============================
# 基础配置
# =============================

SPACE_ARCHIVE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
RELATION_STAT_URL = "https://api.bilibili.com/x/relation/stat"
SPACE_ACC_INFO_URL = "https://api.bilibili.com/x/space/acc/info"

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Cookie": BILI_COOKIE,
}

# =============================
# WBI 签名
# =============================

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def _sign_wbi(params: Dict[str, Any], img_key: str, sub_key: str) -> Dict[str, Any]:
    mixin_key = _get_mixin_key(img_key + sub_key)
    curr_time = int(time.time())

    params = dict(params)
    params["wts"] = curr_time

    params = dict(sorted(params.items()))

    filtered = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in params.items()
    }

    query = urllib.parse.urlencode(filtered)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    filtered["w_rid"] = w_rid
    return filtered


_WBI_KEYS: Tuple[str, str] | None = None
_WBI_KEYS_TS: float | None = None
_WBI_KEYS_TTL = 3600  # 1h


def _get_wbi_keys() -> Tuple[str, str]:
    global _WBI_KEYS, _WBI_KEYS_TS
    now = time.time()

    if _WBI_KEYS and _WBI_KEYS_TS and (now - _WBI_KEYS_TS < _WBI_KEYS_TTL):
        return _WBI_KEYS

    resp = requests.get(NAV_URL, headers=COMMON_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    wbi_img = data["data"]["wbi_img"]
    img_key = wbi_img["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi_img["sub_url"].rsplit("/", 1)[-1].split(".")[0]

    _WBI_KEYS = (img_key, sub_key)
    _WBI_KEYS_TS = now
    return _WBI_KEYS


# =============================
# /x/space/wbi/arc/search 拉投稿列表
# =============================

DM_IMG_LIST = "[]"
DM_IMG_STR = "V2ViR0wgMS"
DM_COVER_IMG_STR = "SW50ZWwoUikgSEQgR3JhcGhpY3NJbnRlbA"


def fetch_user_archives(
    mid: int,
    page_size: int = 30,
    max_pages: int = 100,
    sleep_sec: float = 0.5,
) -> List[Dict[str, Any]]:

    img_key, sub_key = _get_wbi_keys()
    all_videos: List[Dict[str, Any]] = []

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
            "dm_img_list": DM_IMG_LIST,
            "dm_img_str": DM_IMG_STR,
            "dm_cover_img_str": DM_COVER_IMG_STR,
        }

        signed_params = _sign_wbi(base_params, img_key, sub_key)

        resp = requests.get(
            SPACE_ARCHIVE_URL,
            params=signed_params,
            headers=COMMON_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        code = data.get("code", 0)
        if code != 0:
            print(
                f"[warn] arc.search page {pn} code={code} msg={data.get('message')!r}"
            )
            break

        d = data.get("data") or {}
        vlist = (d.get("list") or {}).get("vlist") or []
        page_info = d.get("page") or {}
        total_pages = page_info.get("pages") or pn
        total_count = page_info.get("count") or "?"

        if not vlist:
            print(f"[info] page {pn} 无更多视频，结束。")
            break

        all_videos.extend(vlist)

        print(
            f"[info] mid={mid} page {pn}/{total_pages}, "
            f"本页 {len(vlist)} 条，累计 {len(all_videos)}/{total_count}"
        )

        if pn >= int(total_pages):
            break

        time.sleep(sleep_sec)

    return all_videos


# =============================
# 单视频详细信息
# =============================

def fetch_video_info(bvid: str) -> Dict[str, Any]:
    """
    调用 /x/web-interface/view 获取单视频完整信息：
    - title / desc / pubdate / duration
    - owner
    - stat（view/like/coin/favorite/reply/danmaku/share/...）
    """
    params = {"bvid": bvid}
    headers = dict(COMMON_HEADERS)
    headers["Referer"] = f"https://www.bilibili.com/video/{bvid}"

    resp = requests.get(VIEW_URL, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"view 接口错误: {data}")

    info = data["data"]
    return info


# =============================
# 粉丝数
# =============================

def fetch_user_fans(mid: int) -> Dict[str, Any]:
    """
    获取指定 mid 的粉丝数 / 关注数。
    """
    params = {"vmid": mid}
    headers = dict(COMMON_HEADERS)
    headers["Referer"] = f"https://space.bilibili.com/{mid}"

    resp = requests.get(RELATION_STAT_URL, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"relation.stat 接口错误: {data}")

    return data.get("data", {})


# =============================
#  UP 主资料
# =============================

def fetch_user_profile(mid: int) -> Dict[str, Any]:
    """
    获取 UP 主空间资料：
    - mid, name, face, sign 等
    """
    params = {"mid": mid}
    headers = dict(COMMON_HEADERS)
    headers["Referer"] = f"https://space.bilibili.com/{mid}"

    resp = requests.get(SPACE_ACC_INFO_URL, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"space.acc.info 接口错误: {data}")
    return data.get("data", {})
