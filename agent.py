#!/usr/bin/env python3
import os
import json
import re
import argparse
import time
import urllib.request
from datetime import datetime
from pathlib import Path

class LogAgent:
    def __init__(self, args):
        self.root_dir = Path(args.dir).resolve()
        self.server_name = args.name
        self.mode = args.mode  
        self.extensions = args.ext.split(',')
        self.api_url = "http://10.94.99.153:5000/upload_batch"
        
        # 状态记录 (只存时间戳，不删数据)
        state_dir = Path.home() / ".log_agent_state"
        state_dir.mkdir(exist_ok=True)
        self.state_file = state_dir / f"state_{self.server_name}.json"
        
        self.batch_size = 1000
        
        # --- 白名单与规则配置 ---
        self.re_pn_standard = re.compile(r'^[23][A-Z0-9]{9,}')
        self.allow_list = ["simple_fixture"] # 后续有新目录在此添加
        self.stages = ["FT", "BFT", "FQA", "OFFLINE"]

    def is_valid_dir(self, dir_name):
        """第一层目录准入检查"""
        return self.re_pn_standard.match(dir_name) or dir_name in self.allow_list

    def get_last_scan_ts(self):
        """增量扫描锚点"""
        if self.mode == 'full': return 0
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f).get("last_ts", 0)
            except: return 0
        return 0

    def post_data(self, data):
        """健壮上报，失败仅打印，不中断"""
        if not data: return
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(self.api_url, data=json_data, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.read()
        except Exception as e:
            print(f"[{datetime.now()}] Network/Server Error: {e}")

    def parse_metadata(self, file_path, pn):
        """解析核心：无任何修改/删除操作"""
        try:
            f_stat = file_path.stat()
            path_str = str(file_path).upper()
            stem = file_path.stem
            
            # 1. SN 提取：支持 SN_PASS 或 SN.BFT 格式
            sn = re.split(r'[_.]', stem)[0]
            
            # 2. Stage 提取：支持多种分隔符包裹
            stage = "UNKNOWN"
            for s in self.stages:
                s_u = s.upper()
                if any(f"{sep}{s_u}{sep}" in f"/{path_str}/" for sep in ["/", "_", "."]):
                    stage = s_u
                    break
            
            # 3. LogTime: 直接取物理修改时间
            dt = datetime.fromtimestamp(f_stat.st_mtime)
            log_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            
            # 4. 三态逻辑 (PASS / FAIL / ABORT)
            if "FAIL" in path_str:
                status = "FAIL"
            elif "ABORT" in path_str:
                status = "ABORT"
            else:
                status = "PASS"
            
            # 5. 相对路径 (由前端根据 server_name 拼装完整路径)
            rel_path = file_path.relative_to(self.root_dir)
                    
            return {
                "server_name": self.server_name,
                "file_name": file_path.name,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": str(rel_path).replace("\\", "/") 
            }
        except Exception as e:
            print(f"[{datetime.now()}] Parse Error on {file_path}: {e}")
            return None

    def run(self):
        last_ts = self.get_last_scan_ts()
        current_ts = time.time()
        batch = []
        total_count = 0

        print(f"[{datetime.now()}] Scan Started | Host: {self.server_name} | Mode: {self.mode}")

        if not self.root_dir.exists():
            print(f"Error: {self.root_dir} not found")
            return

        # 遍历第一层
        for pn_dir in self.root_dir.iterdir():
            if not pn_dir.is_dir() or not self.is_valid_dir(pn_dir.name):
                continue
            
            current_pn = pn_dir.name
            # 递归搜索子目录所有匹配后缀的文件
            for ext in self.extensions:
                for file_path in pn_dir.rglob(f"*{ext}"):
                    try:
                        # 增量跳过
                        if file_path.stat().st_mtime <= last_ts:
                            continue
                        
                        meta = self.parse_metadata(file_path, current_pn)
                        if meta:
                            batch.append(meta)

                        if len(batch) >= self.batch_size:
                            self.post_data(batch)
                            total_count += len(batch)
                            batch = []
                    except:
                        continue

        if batch:
            self.post_data(batch)
            total_count += len(batch)

        # 更新状态文件
        with open(self.state_file, 'w') as f:
            json.dump({"last_ts": current_ts}, f)
            
        print(f"[{datetime.now()}] Job Completed. Records Uploaded: {total_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Path to log store")
    parser.add_argument("--name", default=os.uname()[1], help="Machine name")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr', help="Scan mode")
    parser.add_argument("--ext", default=".log,.txt", help="Suffixes")
    
    agent = LogAgent(parser.parse_args())
    agent.run()