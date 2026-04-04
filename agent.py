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
        self.root_dir = os.path.abspath(args.dir)
        self.server_name = args.name.lower()
        self.mode = args.mode  
        self.extensions = args.ext.split(',')
        self.api_url = "http://10.94.99.153:5000/upload_batch"
        
        # 状态记录 (只存时间戳)
        state_dir = Path.home() / ".log_agent_state"
        state_dir.mkdir(exist_ok=True)
        self.state_file = state_dir / f"state_{self.server_name}.json"
        
        self.batch_size = 1000
        
        # --- 规则配置 ---
        self.re_pn_standard = re.compile(r'^[23][A-Z0-9]{9,}')
        self.allow_list = ["simple_fixture"]
        self.stages = ["FT", "BFT", "FQA", "OFFLINE"]
        # 提取 root 文件夹名作为 share_name (例如 store 或 store1)
        self.root_name = os.path.basename(self.root_dir.rstrip(os.sep))

    def is_valid_dir(self, dir_name):
        """第一层目录准入检查"""
        return self.re_pn_standard.match(dir_name) or dir_name in self.allow_list

    def get_last_scan_ts(self):
        """获取上次扫描截止时间"""
        if self.mode == 'full': return 0
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f).get("last_ts", 0)
            except: return 0
        return 0

    def post_data(self, data, retries=3):
        """带退避机制的网络重试"""
        if not data: return
        json_data = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(self.api_url, data=json_data, headers={'Content-Type': 'application/json'})
        
        for i in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.read()
            except Exception as e:
                print(f"[{datetime.now()}] Upload failed (Attempt {i+1}/{retries}): {e}")
                if i < retries - 1:
                    time.sleep(5 * (i + 1))
                else:
                    print(f"[{datetime.now()}] Critical: Batch of {len(data)} records dropped.")

    def parse_metadata(self, file_path_obj, pn):
        """解析核心：支持多种分隔符包裹的 Stage 提取"""
        try:
            f_stat = file_path_obj.stat()
            # 统一转为大写并使用正斜杠处理路径字符串
            path_str = str(file_path_obj).replace('\\', '/').upper()
            stem = file_path_obj.stem.upper()

            # 1. SN 提取 (取第一个分隔符前的部分)
            sn = re.split(r'[_.]', stem)[0]
            
            # 2. Stage 提取：支持多种分隔符包裹
            stage = "UNKNOWN"
            if "sel" in self.server_name:
                stage = "BFT"

            # 严谨匹配逻辑：确保 Stage 是被 / _ 或 . 包裹的独立单词
            for s in self.stages:
                s_u = s.upper()
                # 在路径前后加斜杠，模拟完整路径包裹环境
                temp_path = f"/{path_str}/"
                if any(f"{sep}{s_u}{sep}" in temp_path for sep in ["/", "_", "."]):
                    stage = s_u
                    break
            
            # 3. LogTime
            log_time = datetime.fromtimestamp(f_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            # 4. 三态逻辑
            status = "PASS"
            if "FAIL" in path_str: status = "FAIL"
            elif "ABORT" in path_str: status = "ABORT"
            
            # 5. 路径处理
            # 结果示例: store/PN/folder/log.txt
            clean_rel = os.path.relpath(str(file_path_obj), self.root_dir).replace('\\', '/')
            full_rel_path = f"{self.root_name}/{clean_rel}"

            return {
                "server_name": self.server_name,
                "file_name": file_path_obj.name,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": full_rel_path,
                "share_name": self.root_name
            }
        except Exception as e:
            print(f"[{datetime.now()}] Parse Error: {e}")
            return None

    def run(self):
        last_ts = self.get_last_scan_ts()
        start_scan_ts = time.time()
        batch = []
        total_count = 0

        print(f"[{datetime.now()}] Scan Started | Mode: {self.mode} | Target: {self.root_dir}")

        if not os.path.exists(self.root_dir):
            print(f"Error: Directory {self.root_dir} does not exist.")
            return

        # 使用 os.walk (底层 scandir) 进行高效流式遍历
        for root, dirs, files in os.walk(self.root_dir):
            # 计算当前深度
            rel_path_from_root = os.path.relpath(root, self.root_dir)

            # 第一层目录：执行准入过滤（剪枝优化）
            if rel_path_from_root == ".":
                dirs[:] = [d for d in dirs if self.is_valid_dir(d)]
                continue
            
            # 提取 PN (第一层目录名)
            current_pn = rel_path_from_root.split(os.sep)[0]

            for filename in files:
                # 后缀检查
                if not any(filename.lower().endswith(ext) for ext in self.extensions):
                    continue

                full_path = os.path.join(root, filename)
                try:
                    # 获取文件修改时间
                    mtime = os.path.getmtime(full_path)
                    if mtime <= last_ts:
                        continue

                    # 解析元数据
                    meta = self.parse_metadata(Path(full_path), current_pn)
                    if meta:
                        batch.append(meta)

                    # 达到分批大小则上传
                    if len(batch) >= self.batch_size:
                        self.post_data(batch)
                        total_count += len(batch)
                        batch = []
                except:
                    continue

        # 上传剩余数据
        if batch:
            self.post_data(batch)
            total_count += len(batch)

        # 更新状态
        with open(self.state_file, 'w') as f:
            json.dump({"last_ts": start_scan_ts}, f)
            
        print(f"[{datetime.now()}] Scan Finished. Total Uploaded: {total_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log Analytics Agent")
    parser.add_argument("--dir", required=True, help="Log root directory")
    parser.add_argument("--name", default=os.uname()[1], help="Machine identity")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr', help="Scan mode")
    parser.add_argument("--ext", default=".log,.txt", help="File extensions (comma separated)")
    
    args = parser.parse_args()
    LogAgent(args).run()