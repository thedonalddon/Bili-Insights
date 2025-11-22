#!/bin/bash
cd /path/to/Bili-Insights
source venv/bin/activate
python snapshot_job.py
python esp_render.py