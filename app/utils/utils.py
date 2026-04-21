import os
import json
import time
from datetime import datetime
from flask import request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
IP_MAP_PATH = os.path.join(APP_DIR,'ip_map.json')
# 缓存放在项目根目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(APP_DIR)), 'static','cache')
MAX_CACHE_GB = 1.5
MAX_CACHE_BYTES = MAX_CACHE_GB * 1024 * 1024 * 1024

def load_ip_map():
    if not os.path.exists(IP_MAP_PATH): return {}
    try:
        with open(IP_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def get_target_year():
    # 优先看 URL 参数里有没有传 year，没有则用今年
    return request.args.get('year', str(datetime.now().year))
    #return str(datetime.now().year)

def clean_cache():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
        return

    files = []
    total_size = 0

    for f in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, f)
        if os.path.isfile(path):
            try:
                stat = os.stat(path)
                # 激进策略：顺便清理掉超过 12 小时没人看的文件
                if (time.time() - stat.st_atime) > 43200:
                    os.remove(path)
                    continue
                files.append({
                    'path': path,
                    'atime': stat.st_atime,
                    'size': stat.st_size
                })
                total_size += stat.st_size
            except: continue

    # 空间触发清理
    if total_size > MAX_CACHE_BYTES:
        files.sort(key=lambda x: x['atime']) # 最旧的在前

        # 释放到只剩下 40% 的占用，为大文件预留更多空间
        target_to_free = total_size - (MAX_CACHE_BYTES * 0.4)
        freed = 0
        for f_info in files:
            try:
                os.remove(f_info['path'])
                freed += f_info['size']
                if freed >= target_to_free: break
            except: pass