@app.route("/api/account/latest")
def api_account_latest():
    latest = get_latest_account_snapshot()
    if not latest:
        return jsonify({"error": "no snapshot"}), 404
    return jsonify(latest)


@app.route("/api/account/snapshot")
def api_account_snapshot():
    """
    返回账号最新 snapshot（相当于快照表中的最新一行）
    """
    latest = get_latest_account_snapshot()
    if not latest:
        return jsonify({"error": "no snapshot"}), 404
    return jsonify(latest)


@app.route("/api/esp32/full")
def api_esp32_full():
    """
    提供给 ESP32 的全量数据，等同 Web 控制台：
    - 最新账号维度数据 latest
    - 账号日增 daily_diff
    - 所有视频的最新快照 videos
    """
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
