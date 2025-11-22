# Bili-Insights

Bili-Insights 是一个用于抓取、统计并可视化 B 站 UP 主数据的轻量级工具。  
特点是：本地部署、每日快照、可视化看板、支持视频数据与账号数据分析。

---

## 功能概览

### ▶ 账号数据
- 获取 UP 主总粉丝、总播放、总点赞、总评论、总收藏等
- 自动记录每日快照，形成 **日增趋势**
- 涨粉量 / 播放量的 7 天 / 15 天 / 30 天增长曲线

### ▶ 视频数据
- 自动拉取所有投稿
- 单条视频统计：
  - 播放量、点赞、投币、收藏、评论、弹幕
  - 日增趋势
  - 各项互动率
- 视频排行榜（按播放 / 点赞 / 投币 / 收藏 / 评论排序）

### ▶ 前端可视化
- 纯静态前端（HTML + JS），无需 Node


## 安装

建议使用虚拟环境：

```bash
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

修改根目录下的配置文件config-example.py，并改名为config.py：
```
BILI_COOKIE = "你的浏览器 Cookie"
MY_MID = "你的 UID"
```
```
mv config-example.py config.py
```

Cookie请使用浏览器登录B站后，自行从开发者模式中获取。

## 使用方式

1. 拉取快照（每日执行一次，如重复执行，只保留当日最后一次结果）

```python snapshot_job.py```

你可以用 crontab 或 schedule 来自动化：

```0 3 * * * /usr/bin/python /path/to/snapshot_job.py```
2. 启动 Web 可视化界面

```python app.py```

在浏览器中打开：

```http://127.0.0.1:8765/```

请注意放行服务器的 8765 端口。

## 许可证（License）

本项目采用 **CC BY-NC-SA 4.0（署名-非商业性使用-相同方式共享）** 授权协议。

你可以：
- 复制、分发、修改本项目代码；
- 在非商业用途下使用；

你不能：
- 将本项目代码或其衍生版本用于任何商业目的；
- 以任何商业形式出售、分发或提供本项目。