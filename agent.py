#!/usr/bin/env python3
import os
import sys
import json
import re
import argparse
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

class LogAgent:
    def __init__(self, args):
        self.root_dir = Path(args.dir).resolve()
        self.server_name = args.name
        self.mode = args.mode  
        self.extensions = args.ext.split(',')
        self.api_url = "http://10.94.99.153:5000/upload_batch"
        
        # 状态文件：记录上次扫描的时间戳，存放在用户家目录隐藏文件夹下
        state_dir = Path.home() / ".log_agent_state"
        state_dir.mkdir(exist_ok=True)
        self.state_file = state_dir / f"state_{self.server_name}.json"
        
        self.batch_size = 1000
        self.re_pn = re.compile(r'^[23][A-Z0-9]{9,}')
        self.stages = ["FT", "BFT", "FQA"]

    def get_last_scan_ts(self):
        """获取增量扫描的起始时间点"""
        if self.mode == 'full': return 0
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f).get("last_ts", 0)
            except Exception: return 0
        return 0

    def post_data(self, data):
        """批量上传健壮性处理：超时控制与异常拦截"""
        if not data: return
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(
            self.api_url, 
            data=json_data, 
            headers={'Content-Type': 'application/json'}
        )
        try:
            # 60秒超时保护，应对海量数据压力
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            print(f"[{datetime.now()}] Server Error (HTTP {e.code}): {e.reason}")
        except urllib.error.URLError as e:
            print(f"[{datetime.now()}] Network Error: {e.reason}")
        except Exception as e:
            print(f"[{datetime.now()}] Unexpected Post Error: {e}")

    def parse_metadata(self, file_path, pn):
        """核心解析：显式接收 PN，利用 mtime 提取时间"""
        try:
            f_stat = file_path.stat()
            path_str = str(file_path).upper()
            stem = file_path.stem
            
            # 1. SN: 文件名第一段
            sn = stem.split('_')[0]
            
            # 2. Stage: 路径/文件名匹配 (排除大小写)
            stage = "UNKNOWN"
            for s in self.stages:
                s_u = s.upper()
                if f"/{s_u}/" in path_str or f"_{s_u}_" in path_str:
                    stage = s_u
                    break
            
            # 3. LogTime: 直接取文件修改时间 (mtime)，物理客观且高效
            dt = datetime.fromtimestamp(f_stat.st_mtime)
            log_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            
            # 4. Status: 路径含 FAIL (不区分大小写) 即为失败
            status = "FAIL" if "FAIL" in path_str else "PASS"
            
            # 5. 计算相对路径 (用于前端动态拼接)
            rel_path = file_path.relative_to(self.root_dir)
                    
            return {
                "server_name": self.server_name,
                "file_name": file_path.name,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": str(rel_path).replace("\\", "/") # 统一 Linux 斜杠
            }
        except Exception as e:
            print(f"[{datetime.now()}] Parse error on {file_path}: {e}")
            return None

    def run(self):
        last_ts = self.get_last_scan_ts()
        current_ts = time.time()
        batch = []
        total_count = 0

        print(f"[{datetime.now()}] Starting {self.mode} scan on {self.server_name}...")

        if not self.root_dir.exists():
            print(f"Error: Directory {self.root_dir} not found.")
            return

        # 遍历第一层：仅处理 2/3 开头的 PN 文件夹
        for pn_dir in self.root_dir.iterdir():
            if not pn_dir.is_dir() or not self.re_pn.match(pn_dir.name):
                continue
            
            # 锁定当前 PN
            current_pn = pn_dir.name
            print(f"Scanning PN: {current_pn}")

            for ext in self.extensions:
                for file_path in pn_dir.rglob(f"*{ext}"):
                    try:
                        # 增量逻辑：只处理比上次扫描更新的文件
                        if file_path.stat().st_mtime <= last_ts:
                            continue
                        
                        meta = self.parse_metadata(file_path, current_pn)
                        if meta:
                            batch.append(meta)

                        if len(batch) >= self.batch_size:
                            self.post_data(batch)
                            total_count += len(batch)
                            batch = []
                    except Exception:
                        continue

        # 扫尾处理
        if batch:
            self.post_data(batch)
            total_count += len(batch)

        # 记录本次成功完成的时间点
        try:
            with open(self.state_file, 'w') as f:
                json.dump({"last_ts": current_ts}, f)
        except Exception as e:
            print(f"Failed to save scan state: {e}")
            
        print(f"[{datetime.now()}] Finished. Uploaded {total_count} records.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log Indexing Agent")
    parser.add_argument("--dir", required=True, help="Root directory to scan")
    parser.add_argument("--name", default=os.uname()[1], help="Custom server name")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr', help="Scan mode")
    parser.add_argument("--ext", default=".log,.txt", help="Suffixes (comma separated)")
    
    agent = LogAgent(parser.parse_args())
    agent.run()