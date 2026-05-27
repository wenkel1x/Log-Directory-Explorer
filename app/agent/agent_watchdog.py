#!/usr/bin/env python3
import os
import json
import re
import argparse
import time
import urllib.request
from datetime import datetime
from pathlib import Path
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
# 确保环境有 watchdog
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("Error: pip install watchdog is required.")
    sys.exit(1)

class LogHandler(FileSystemEventHandler):
    def __init__(self, agent):
        self.agent = agent

    def on_closed(self, event):
        """完全对齐 agent.py 的解析触发逻辑"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        
        # 1. 后缀校验 (对齐 agent.py)
        if not any(file_path.name.lower().endswith(ext) for ext in self.agent.extensions):
            return

        # 2. PN 提取逻辑 (完全对齐 agent.py 的 rel_parts 逻辑)
        rel_path = os.path.relpath(str(file_path), self.agent.root_dir)
        rel_parts = rel_path.split(os.sep)
        
        current_pn = "UNKNOWN"
        if len(rel_parts) >= 1:
            if rel_parts[0].lower() in self.agent.allow_list:
                current_pn = rel_parts[1].upper() if len(rel_parts) > 1 else "UNKNOWN"
            else:
                current_pn = rel_parts[0].upper()

        # 3. 调用标准的解析方法
        meta = self.agent.parse_metadata(file_path, current_pn)
        if meta:
            self.agent.executor.submit(self.agent.post_data, [meta])
            print(f"[{datetime.now()}] [Real-time] Uploaded: {file_path.name}")

class LogAgent:
    def __init__(self, args):
        self.root_dir = os.path.abspath(args.dir)
        self.server_name = args.name.lower()
        self.mode = args.mode  
        self.extensions = args.ext.split(',')
        self.api_url = "http://10.94.99.153:5000/upload_batch"
        self.report_url = f"http://10.94.99.153:5000/api/report_ip"

        # --- 状态记录 (对齐 agent.py) ---
        current_script_dir = Path(__file__).parent
        self.state_file = current_script_dir / f".state_{self.server_name}.json"

        self.batch_size = 1000
        self.running = True
        self.executor = ThreadPoolExecutor(max_workers=4)

        # --- 规则配置 (完全对齐 agent.py) ---
        self.re_pn_standard = re.compile(r'^[23][A-Z0-9]{9,}')
        self.allow_list = ["simple_fixture", "fqa"]
        self.stages = ["FT", "BFT", "FQA", "OFFLINE"]
        self.block_list = ["backup", "tmp", "routing", "temp", "bak", "process"]
        self.root_name = os.path.basename(self.root_dir.rstrip(os.sep))
        
        signal.signal(signal.SIGINT, self.handle_exit)

    def handle_exit(self, signum, frame):
        print(f"\n[{datetime.now()}] Stop signal received.")
        self.running = False

    def post_data(self, data):
        """完全对齐 agent.py 的上传逻辑"""
        if not data: return
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(self.api_url, data=json_data, headers={'Content-Type': 'application/json'})
        for i in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.read()
            except Exception as e:
                if i < 2: time.sleep(2 * (i + 1))

    def parse_metadata(self, file_path_obj, pn):
        """【核心对齐】此处逻辑与你提供的 agent.py 源码完全一致"""
        try:
            filename = file_path_obj.name
            stem = file_path_obj.stem
            name_parts = re.split(r'[_.-]', stem)
            
            # 排除短索引日志
            if len(name_parts) <= 1 or not any(p.isdigit() and len(p) >= 4 for p in name_parts[1:]):
                return None

            path_str_upper = str(file_path_obj).replace('\\', '/').upper()
            f_stat = file_path_obj.stat()
            sn = name_parts[0].upper()

            stage = "UNKNOWN"
            if any(x in self.server_name for x in ["sel", "meta"]): stage = "BFT"
            for s in self.stages:
                s_u = s.upper()
                if f"_{s_u}_" in f"_{path_str_upper}_" or f".{s_u}." in f".{path_str_upper}.":
                    stage = s_u
                    break

            return {
                "server_name": self.server_name,
                "file_name": filename,
                "pn": pn,
                "sn": sn,
                "log_time": datetime.fromtimestamp(f_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                "status": "FAIL" if "FAIL" in path_str_upper else ("ABORT" if "ABORT" in path_str_upper else "PASS"),
                "stage": stage,
                "relative_path": f"{self.root_name}/{os.path.relpath(file_path_obj, self.root_dir).replace('\\', '/')}",
                "share_name": self.root_name
            }
        except: return None

    def fast_scan(self, current_dir, last_ts, depth=0):
        """【核心对齐】启动时执行一次 agent.py 的高效扫描"""
        if not self.running: return
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if entry.is_dir():
                        d_name = entry.name.lower()
                        if d_name in self.block_list: continue
                        if depth == 0 and not (self.re_pn_standard.match(d_name.upper()) or d_name in self.allow_list):
                            continue
                        if entry.stat().st_mtime <= last_ts: continue
                        yield from self.fast_scan(entry.path, last_ts, depth + 1)
                    elif entry.is_file():
                        if any(entry.name.lower().endswith(ext) for ext in self.extensions):
                            if entry.stat().st_mtime > last_ts:
                                # 计算 PN
                                rel_p = os.path.relpath(entry.path, self.root_dir).split(os.sep)
                                pn = "UNKNOWN"
                                if len(rel_p) >= 1:
                                    if rel_p[0].lower() in self.allow_list:
                                        pn = rel_p[1].upper() if len(rel_p) > 1 else "UNKNOWN"
                                    else: pn = rel_p[0].upper()
                                yield entry.path, pn
        except: pass

    def run(self):
        # 1. 补课：扫描停机期间的日志
        last_ts = 0
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f: last_ts = json.load(f).get("last_ts", 0)
            except: pass
        
        print(f"[{datetime.now()}] Step 1: Syncing missed logs since {last_ts}...")
        sync_batch = []
        for f_path, pn in self.fast_scan(self.root_dir, last_ts):
            meta = self.parse_metadata(Path(f_path), pn)
            if meta:
                sync_batch.append(meta)
                if len(sync_batch) >= self.batch_size:
                    self.executor.submit(self.post_data, list(sync_batch))
                    sync_batch = []
        if sync_batch: self.executor.submit(self.post_data, sync_batch)

        # 2. 实时：启动 Watchdog
        print(f"[{datetime.now()}] Step 2: Real-time monitoring started.")
        observer = Observer()
        observer.schedule(LogHandler(self), self.root_dir, recursive=True)
        observer.start()

        try:
            while self.running:
                # 周期性更新时间戳，确保意外断电后“补课”范围缩小
                with open(self.state_file, 'w') as f:
                    json.dump({"last_ts": time.time()}, f)
                time.sleep(10)
        finally:
            observer.stop()
            observer.join()
            self.executor.shutdown(wait=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--name", default="log_agent")
    parser.add_argument("--mode", default="incr")
    parser.add_argument("--ext", default=".log,.txt")
    args = parser.parse_args()
    LogAgent(args).run()