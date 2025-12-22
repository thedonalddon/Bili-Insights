# Bili-Insights

<p align="left">
  <img src="esp32/Bili-Insights.jpeg" width="80%">
</p>

Bili-Insights 是一个用于抓取、统计并可视化 B 站 UP 主数据的轻量级工具。  
特点是：本地部署、每日快照、可视化看板、支持视频数据与账号数据分析。  
可使用 ESP32 墨水屏看板监视每日数据。

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
- 在服务端渲染 800×480 图像，供 ESP32 拉取并显示在 7.3 寸墨水屏上。

## 安装
1. 建议Python版本：3.13.5

建议使用虚拟环境：

```bash
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

2. 修改根目录下的配置文件 config-example.py：
```
BILI_COOKIE = "你的 Cookie"
MY_MID = "你的 UID"
ACCOUNT_NAME = "你的 B 站账户昵称"
ACCOUNT_INTRO = "你的 B 站账户简介"
AVATAR_PATH = "esp32/resources/你的 B 站账户头像.jpg"
```
- *Cookie 可以用浏览器登录B站后，自行从开发者模式中获取。*  

3. 将 config-example.py 改名为 config.py：
```
mv config-example.py config.py
```


## 使用方法

1. 从 B 站拉取统计数据（每日执行一次即可，如重复执行，只保留当日最后一次结果）：

```python snapshot_job.py```


2. 启动 Web 可视化界面服务器：

```python app.py```

3. 在浏览器中打开：

```http://127.0.0.1:8765/```

*请注意放行服务器的 8765 端口。*


## 渲染 ESP32 墨水屏看板图片（可选）：

1. 运行渲染脚本：

```python esp_render.py```

运行后会在 `esp_output/` 目录下生成：

- dashboard_preview.png：原始 RGB 预览图（可选）；
- dashboard7c_preview.png：量化到 7C 调色板后的预览图（可选）；
- dashboard7c_800x480.bin：供 ESP32 下载的帧缓冲文件。

2. 从 ES232 中拉取 bin 文件并显示在墨水屏上。  
详见 [esp32/README.md](https://github.com/thedonalddon/Bili-Insights/tree/main/esp32/)

## 服务器部署与定时任务示例（可选）

1. 在服务器上可以通过一个简单的脚本统一执行「抓取数据 + 渲染墨水屏看板」，例如新建 `daily_update.sh`：

```bash
#!/bin/bash
cd /path/to/Bili-Insights
source venv/bin/activate
python snapshot_job.py
python esp_render.py
```

2. 赋予执行权限：

```bash
chmod +x /path/to/Bili-Insights/daily_update.sh
```

3. 通过 crontab 每天早上 6 点自动执行一次：

```bash
0 6 * * * /bin/bash /path/to/Bili-Insights/daily_update.sh >> /path/to/Bili-Insights/snapshot.log 2>&1
```
*把 `/path/to/Bili-Insights` 替换为你在服务器上的实际项目路径。*



## 可视化前端 Web 服务自启动（可选）

若希望服务器在重启后自动启动可视化 Web 服务，可创建一个 systemd 服务：

1. 新建服务文件：

```bash
sudo nano /etc/systemd/system/bili-insights.service
```

2. 写入以下内容：

```ini
[Unit]
Description=Bili-Insights Web Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/Bili-Insights
ExecStart=/path/to/Bili-Insights/venv/bin/python app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
*把 `/path/to/Bili-Insights` 替换为你在服务器上的实际项目路径。*  

保存： `Ctrl + O`，退出： `Ctrl + X`。  

3. 启动服务：

```bash
sudo systemctl start bili-insights.service
```

设置开机自启：

```bash
sudo systemctl enable bili-insights.service
```

查看运行状态：

```bash
sudo systemctl status bili-insights.service
```

## 相关项目
Bilibili 野生 API 收集：  
https://github.com/SocialSisterYi/bilibili-API-collect