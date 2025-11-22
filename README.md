# Bili-Insights

Bili-Insights 是一个用于抓取、统计并可视化 B 站 UP 主数据的轻量级工具。  
特点是：本地部署、每日快照、可视化看板、支持视频数据与账号数据分析。  
可使用 ESP32 制作的墨水屏看板监视每日数据。

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

### ▶ ESP32 墨水屏看板展示
- 在服务端渲染三色 bitmap，由ESP32定时下载并显示在墨水屏上。


## 安装
建议Python版本：3.13.5

建议使用虚拟环境：

```bash
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

修改根目录下的配置文件 config-example.py：
```
# Cookie 请使用浏览器登录B站后，自行从开发者模式中获取。
BILI_COOKIE = "你的浏览器 Cookie"
MY_MID = "你的 UID"
ACCOUNT_NAME = "你的 B 站账户昵称"
ACCOUNT_INTRO = "你的 B 站账户简介"
AVATAR_PATH = "esp32/resources/你的 B 站账户头像.jpg"
```
Cookie请使用浏览器登录B站后，自行从开发者模式中获取。

将 config-example.py 改名为 config.py：
```
mv config-example.py config.py
```


## 使用方式

1. 从 B 站获取统计数据（每日执行一次即可，如重复执行，只保留当日最后一次结果）：

```python snapshot_job.py```


2. 启动 Web 可视化界面服务器：

```python app.py```

在浏览器中打开：

```http://127.0.0.1:8765/```

请注意放行服务器的 8765 端口。

3. 生成 ESP32 墨水屏看板图片（可选）：

```python esp_render.py```

运行后会在 `esp_output/` 目录下生成：

- `dashboard_preview.png`：原始 800×480 仪表盘预览图；
- `dashboard_black.bin` / `dashboard_red.bin` / `dashboard_yellow.bin`：三路位平面数据，可直接供 ESP32 向四色墨水屏写入；
- `dashboard_merged.png`：根据三路位平面合成的「实际墨水屏显示效果」预览图。


## 服务器部署与定时任务示例（可选）

在服务器上可以通过一个简单的脚本统一执行「抓取数据 + 渲染墨水屏看板」，例如新建 `daily_update.sh`：

```bash
#!/bin/bash
cd /path/to/Bili-Insights
source venv/bin/activate
python snapshot_job.py
python esp_render.py
```

赋予执行权限：

```bash
chmod +x /path/to/Bili-Insights/daily_update.sh
```

然后通过 crontab 每天早上 6 点自动执行一次：

```bash
0 6 * * * /bin/bash /path/to/Bili-Insights/daily_update.sh >> /path/to/Bili-Insights/snapshot.log 2>&1
```

请将 `/path/to/Bili-Insights` 替换为你在服务器上的实际项目路径。

## License

本项目采用 **CC BY-NC-SA 4.0（署名-非商业性使用-相同方式共享）** 授权协议。

你可以：
- 复制、分发、修改本项目代码；
- 在非商业用途下使用；

你不能：
- 将本项目代码或其衍生版本用于任何商业目的；
- 以任何商业形式出售、分发或提供本项目。