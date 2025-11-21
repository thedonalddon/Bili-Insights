# snapshot_job.py

from datetime import date
from typing import Dict, Any, Optional, List
import time

import requests

from config import MY_MID
from bili_api import fetch_user_archives, fetch_video_info, fetch_user_fans
from db import get_conn, init_db


def safe_fetch_video_info(
    bvid: str,
    retries: int = 3,
    delay: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """
    对 fetch_video_info 做一层带重试的封装：
    - 网络波动 / SSL EOF / requests 异常 时重试几次
    - 超过重试次数仍失败则返回 None
    日志：
    - 每次失败打印尝试次数和异常
    - 最终失败打印一条 error
    """
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fetch_video_info(bvid)
        except (requests.exceptions.RequestException, Exception) as e:
            last_err = e
            print(
                f"[warn] fetch_video_info {bvid} 失败({attempt}/{retries}): {repr(e)}"
            )
            if attempt < retries:
                time.sleep(delay)
            else:
                print(
                    f"[error] bvid={bvid} 在调用 /x/web-interface/view 时连续失败，"
                    f"已重试 {retries} 次，最后一次异常: {repr(e)}"
                )
                return None
    return None


def run_snapshot(snapshot_date: str | None = None) -> None:
    """
    跑一遍快照任务：

    1. 拉取当前账号所有投稿列表（BV、标题、基础播放）
    2. 对每条 BV 调用 /x/web-interface/view 拿详细 stat
    3. 写入 video_snapshots
    4. 聚合所有 stat + 粉丝数，写入 account_snapshots

    日志增强：
    - 打印总稿件数
    - 每条视频打印 "第几条 / 总数 + bvid + 标题"
    - 失败时在 safe_fetch_video_info 里记录详情
    - 结束时给出成功/失败汇总，并列出失败的 BV 和标题
    """
    init_db()

    if snapshot_date is None:
        snapshot_date = date.today().isoformat()

    print("=" * 80)
    print(f"[snapshot] 开始快照 snapshot_date={snapshot_date}")
    print("[snapshot] 步骤 1：拉取投稿列表 /x/space/wbi/arc/search")

    # 1. 拉投稿列表
    archives = fetch_user_archives(MY_MID)
    total_archives = len(archives)
    print(f"[snapshot] 共获取到 {total_archives} 条投稿记录。")

    if total_archives == 0:
        print("[snapshot] 未获取到任何投稿，直接结束。")
        print("=" * 80)
        return

    conn = get_conn()
    cur = conn.cursor()

    # 清理当天旧记录（防止重复跑）
    print("[snapshot] 步骤 2：清理当日旧快照记录（video_snapshots / account_snapshots）")
    cur.execute("DELETE FROM video_snapshots WHERE snapshot_date = ?;", (snapshot_date,))
    cur.execute("DELETE FROM account_snapshots WHERE snapshot_date = ?;", (snapshot_date,))

    total_view = total_like = total_coin = 0
    total_fav = total_reply = total_dm = total_share = 0

    success_count = 0
    failed_list: List[Dict[str, Any]] = []

    print("[snapshot] 步骤 3：逐条拉取视频详细信息 /x/web-interface/view 并写入 video_snapshots")

    # 2. 针对每个 BV 拉详细 stat，并写入 video_snapshots
    for idx, v in enumerate(archives, start=1):
        bvid = v.get("bvid")
        title = v.get("title") or ""
        if not bvid:
            print(f"[warn] 第 {idx}/{total_archives} 条没有 bvid，跳过。原始记录: {v}")
            failed_list.append({"bvid": None, "title": title, "reason": "no_bvid"})
            continue

        short_title = title if len(title) <= 40 else title[:37] + "..."
        print(
            f"[snapshot] [{idx}/{total_archives}] 准备拉取视频 "
            f"bvid={bvid}，标题=\"{short_title}\""
        )

        info = safe_fetch_video_info(bvid, retries=3, delay=1.0)
        if info is None:
            failed_list.append(
                {"bvid": bvid, "title": title, "reason": "view_api_failed"}
            )
            continue

        # 从 info 中解析字段
        detail_title = info.get("title") or title
        pubdate = info.get("pubdate")
        duration = info.get("duration")
        stat = info.get("stat") or {}

        view = stat.get("view") or 0
        like = stat.get("like") or 0
        coin = stat.get("coin") or 0
        favorite = stat.get("favorite") or 0
        reply = stat.get("reply") or 0
        danmaku = stat.get("danmaku") or 0
        share = stat.get("share") or 0

        try:
            cur.execute(
                """
                INSERT INTO video_snapshots (
                    snapshot_date, bvid, title,
                    view, like, coin, favorite, reply, danmaku, share,
                    pubdate, duration
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot_date,
                    bvid,
                    detail_title,
                    view,
                    like,
                    coin,
                    favorite,
                    reply,
                    danmaku,
                    share,
                    pubdate,
                    duration,
                ),
            )
        except Exception as e:
            print(
                f"[error] bvid={bvid} 在写入 video_snapshots 时失败: {repr(e)}，该视频本次跳过。"
            )
            failed_list.append(
                {"bvid": bvid, "title": detail_title, "reason": f"db_insert_failed: {e}"}
            )
            continue

        # 累加账号维度
        total_view += view
        total_like += like
        total_coin += coin
        total_fav += favorite
        total_reply += reply
        total_dm += danmaku
        total_share += share

        success_count += 1

        if idx % 10 == 0 or idx == total_archives:
            print(
                f"[snapshot] 已处理 {idx}/{total_archives} 条视频，"
                f"当前成功 {success_count} 条，失败 {len(failed_list)} 条。"
            )

    # 3. 获取粉丝数
    print("[snapshot] 步骤 4：拉取粉丝数 /x/relation/stat")
    try:
        fans = fetch_user_fans(MY_MID)
        follower = fans.get("follower") or 0
    except Exception as e:
        print(f"[error] 拉取粉丝数失败: {repr(e)}，follower 记为 0。")
        follower = 0
        failed_list.append({"bvid": None, "title": "粉丝数", "reason": f"fans_api_failed: {e}"})

    # 4. 写入账号维度快照
    print("[snapshot] 步骤 5：写入账号维度快照 account_snapshots")
    try:
        cur.execute(
            """
            INSERT INTO account_snapshots (
                snapshot_date, follower,
                total_view, total_like, total_coin,
                total_favorite, total_reply, total_danmaku, total_share
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                snapshot_date,
                follower,
                total_view,
                total_like,
                total_coin,
                total_fav,
                total_reply,
                total_dm,
                total_share,
            ),
        )
    except Exception as e:
        print(
            f"[error] 写入 account_snapshots 失败: {repr(e)}。"
        )
        failed_list.append({"bvid": None, "title": "account_snapshot", "reason": f"db_insert_failed: {e}"})

    conn.commit()
    conn.close()

    # 汇总日志
    print("[snapshot] 步骤 6：汇总本次快照结果")
    print(
        f"[snapshot] 本次 snapshot_date={snapshot_date} 处理完毕："
        f"成功 {success_count} 条，失败 {len(failed_list)} 条。"
    )
    print(
        f"[snapshot] 汇总账号维度：total_view={total_view}, "
        f"follower={follower}, total_like={total_like}, "
        f"total_coin={total_coin}, total_favorite={total_fav}, "
        f"total_reply={total_reply}, total_danmaku={total_dm}, total_share={total_share}"
    )

    if failed_list:
        print("[snapshot] 失败明细列表：")
        for item in failed_list:
            bvid = item.get("bvid")
            title = item.get("title") or ""
            reason = item.get("reason") or ""
            short_title = title if len(title) <= 40 else title[:37] + "..."
            print(
                f"  - bvid={bvid or 'N/A'}，标题=\"{short_title}\"，原因={reason}"
            )
    else:
        print("[snapshot] 本次无任何失败视频。")

    print("=" * 80)


if __name__ == "__main__":
    # 手动执行一次快照
    run_snapshot()
