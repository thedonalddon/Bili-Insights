# app.py

from flask import Flask, jsonify, request, send_from_directory, send_file
import os
from config import MY_MID
from db import (
    init_db,
    get_latest_account_snapshot,
    get_last_two_account_snapshots,
    get_latest_video_snapshots,
    get_account_history,
    get_video_history,
)

app = Flask(__name__)

with app.app_context():
    init_db()


# ===== 前端页面 =====

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ===== 账号 API =====

@app.route("/api/account/profile")
def api_account_profile():
    latest = get_latest_account_snapshot()
    snaps = get_last_two_account_snapshots()

    daily_diff = None
    if len(snaps) >= 2:
        latest_snap, prev = snaps[0], snaps[1]

        def diff(field: str) -> int:
            return int(latest_snap.get(field) or 0) - int(prev.get(field) or 0)

        daily_diff = {
            "snapshot_date": latest_snap.get("snapshot_date"),
            "follower": latest_snap.get("follower"),
            "inc_follower": diff("follower"),
            "inc_total_view": diff("total_view"),
            "inc_total_like": diff("total_like"),
            "inc_total_coin": diff("total_coin"),
            "inc_total_favorite": diff("total_favorite"),
            "inc_total_reply": diff("total_reply"),
            "inc_total_danmaku": diff("total_danmaku"),
            "inc_total_share": diff("total_share"),
        }

    resp = {
        "mid": MY_MID,
        "name": None,
        "face": None,
        "sign": None,
        "snapshot": latest,
        "daily_diff": daily_diff,
    }
    return jsonify(resp)


@app.route("/api/account/latest")
def api_account_latest():
    snapshot = get_latest_account_snapshot()
    if not snapshot:
        return jsonify({"error": "no account snapshot"}), 404
    return jsonify(snapshot)


@app.route("/api/account/snapshot")
def api_account_snapshot():
    latest = get_latest_account_snapshot()
    if not latest:
        return jsonify({"error": "no snapshot"}), 404
    return jsonify(latest)


@app.route("/api/account/daily_diff")
def api_account_daily_diff():
    rows = get_last_two_account_snapshots()
    if len(rows) < 2:
        return jsonify({"error": "not enough data"}), 400

    latest, prev = rows[0], rows[1]

    def diff(field: str) -> int:
        return int(latest.get(field) or 0) - int(prev.get(field) or 0)

    result = {
        "snapshot_date": latest.get("snapshot_date"),
        "follower": latest.get("follower"),
        "inc_follower": diff("follower"),
        "inc_total_view": diff("total_view"),
        "inc_total_like": diff("total_like"),
        "inc_total_coin": diff("total_coin"),
        "inc_total_favorite": diff("total_favorite"),
        "inc_total_reply": diff("total_reply"),
        "inc_total_danmaku": diff("total_danmaku"),
        "inc_total_share": diff("total_share"),
    }
    return jsonify(result)


@app.route("/api/account/history")
def api_account_history():
    days = request.args.get("days", type=int)
    rows = get_account_history(days)
    return jsonify(rows)


# ===== 视频 API =====

@app.route("/api/videos/latest")
def api_videos_latest():
    rows = get_latest_video_snapshots()
    return jsonify(rows)


@app.route("/api/videos/overview")
def api_videos_overview():
    rows = get_latest_video_snapshots()
    result = []

    for r in rows:
        view = int(r.get("view") or 0)
        like = int(r.get("like") or 0)
        coin = int(r.get("coin") or 0)
        favorite = int(r.get("favorite") or 0)
        reply = int(r.get("reply") or 0)
        danmaku = int(r.get("danmaku") or 0)

        if view > 0:
            like_rate = like / view
            coin_rate = coin / view
            fav_rate = favorite / view
            reply_rate = reply / view
            danmaku_rate = danmaku / view
            engagement_rate = (like + coin + favorite + reply + danmaku) / view
        else:
            like_rate = coin_rate = fav_rate = reply_rate = danmaku_rate = 0.0
            engagement_rate = 0.0

        item = dict(r)
        item.update(
            {
                "like_rate": like_rate,
                "coin_rate": coin_rate,
                "fav_rate": fav_rate,
                "reply_rate": reply_rate,
                "danmaku_rate": danmaku_rate,
                "engagement_rate": engagement_rate,
            }
        )
        result.append(item)

    return jsonify(result)


@app.route("/api/video/<bvid>/history")
def api_video_history(bvid: str):
    rows = get_video_history(bvid)
    if not rows:
        return jsonify({"error": "no data for this bvid"}), 404
    return jsonify(rows)


@app.route("/api/esp32/full")
def api_esp32_full():
    latest = get_latest_account_snapshot()
    snaps = get_last_two_account_snapshots()
    videos = get_latest_video_snapshots()

    daily_diff = None
    if len(snaps) >= 2:
        latest_snap, prev = snaps[0], snaps[1]

        def diff(field: str) -> int:
            return int(latest_snap.get(field) or 0) - int(prev.get(field) or 0)

        daily_diff = {
            "inc_follower": diff("follower"),
            "inc_total_view": diff("total_view"),
            "inc_total_like": diff("total_like"),
            "inc_total_coin": diff("total_coin"),
            "inc_total_favorite": diff("total_favorite"),
            "inc_total_reply": diff("total_reply"),
            "inc_total_danmaku": diff("total_danmaku"),
            "inc_total_share": diff("total_share"),
        }

    return jsonify({
        "latest": latest,
        "daily_diff": daily_diff,
        "videos": videos
    })

@app.route("/api/esp32/dashboard.bin")
def api_esp32_dashboard_bin():
    bin_path = os.path.join("esp_output", "dashboard7c_800x480.bin")
    if not os.path.exists(bin_path):
        return jsonify({"error": "dashboard bin not found"}), 404
    return send_file(bin_path, mimetype="application/octet-stream", as_attachment=False)

# ===== ESP32 简化接口（备用） =====

@app.route("/api/esp32/summary")
def api_esp32_summary():
    rows = get_last_two_account_snapshots()
    if len(rows) < 2:
        return jsonify({"error": "not enough data"}), 400

    latest, prev = rows[0], rows[1]

    def diff(field: str) -> int:
        return int(latest.get(field) or 0) - int(prev.get(field) or 0)

    result = {
        "date": latest.get("snapshot_date"),
        "follower": latest.get("follower"),
        "inc_follower": diff("follower"),
        "inc_view": diff("total_view"),
        "inc_like": diff("total_like"),
        "inc_coin": diff("total_coin"),
        "inc_fav": diff("total_favorite"),
    }
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
