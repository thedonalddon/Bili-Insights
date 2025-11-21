# db.py

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path("biliinsights.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    初始化数据库：账号维度 + 单视频维度快照表。
    """
    conn = get_conn()
    cur = conn.cursor()

    # 单视频每日快照
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS video_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            bvid TEXT NOT NULL,
            title TEXT,
            view INTEGER,
            like INTEGER,
            coin INTEGER,
            favorite INTEGER,
            reply INTEGER,
            danmaku INTEGER,
            share INTEGER,
            pubdate INTEGER,
            duration INTEGER
        );
        """
    )

    # 账号维度每日快照
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            follower INTEGER,
            total_view INTEGER,
            total_like INTEGER,
            total_coin INTEGER,
            total_favorite INTEGER,
            total_reply INTEGER,
            total_danmaku INTEGER,
            total_share INTEGER
        );
        """
    )

    conn.commit()
    conn.close()


def get_latest_account_snapshot() -> Dict[str, Any] | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM account_snapshots
        ORDER BY snapshot_date DESC, id DESC
        LIMIT 1;
        """
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_last_two_account_snapshots() -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM account_snapshots
        ORDER BY snapshot_date DESC, id DESC
        LIMIT 2;
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_video_snapshots() -> List[Dict[str, Any]]:
    """
    取最新 snapshot_date 的所有视频快照，用于 Web 列表。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(snapshot_date) FROM video_snapshots;")
    row = cur.fetchone()
    if not row or not row[0]:
        conn.close()
        return []

    latest_date = row[0]
    cur.execute(
        """
        SELECT *
        FROM video_snapshots
        WHERE snapshot_date = ?
        ORDER BY view DESC;
        """,
        (latest_date,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_account_history(limit_days: int | None = None) -> List[Dict[str, Any]]:
    """
    账号维度历史记录：
    - 若 limit_days 为 None：返回全部
    - 否则：按日期倒序取最近 limit_days 条，再在 Python 里升序返回
      （这里的 "days" 更准确说是 "最近 N 条快照"）
    """
    conn = get_conn()
    cur = conn.cursor()

    if limit_days is None:
        cur.execute(
            """
            SELECT *
            FROM account_snapshots
            ORDER BY snapshot_date ASC, id ASC;
            """
        )
    else:
        cur.execute(
            """
            SELECT *
            FROM account_snapshots
            ORDER BY snapshot_date DESC, id DESC
            LIMIT ?;
            """,
            (limit_days,),
        )

    rows = cur.fetchall()
    conn.close()

    if limit_days is None:
        return [dict(r) for r in rows]

    # 取了倒序的最近 N 条，这里翻转为按日期升序
    return [dict(r) for r in reversed(rows)]


def get_video_history(bvid: str) -> List[Dict[str, Any]]:
    """
    某条视频的时间序列数据（按 snapshot_date 升序）。
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM video_snapshots
        WHERE bvid = ?
        ORDER BY snapshot_date ASC, id ASC;
        """,
        (bvid,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
