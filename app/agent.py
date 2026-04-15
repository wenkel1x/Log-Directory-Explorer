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

class LogAgent:
    def __init__(self, args):
        self.root_dir = os.path.abspath(args.dir)
        self.server_name = args.name.lower()
        self.mode = args.mode  
        self.extensions = args.ext.split(',')
        self.api_url = "http://10.94.99.153:5000/upload_batch"
        self.report_url = f"http://10.94.99.153:5000/api/report_ip"
        
        # 状态记录 (只存时间戳)
        state_dir = Path.home() / ".log_agent_state"
        state_dir.mkdir(exist_ok=True)
        self.state_file = state_dir / f"state_{self.server_name}.json"
        
        self.batch_size = 1000
        self.running = True
        
        # --- 规则配置 ---
        self.re_pn_standard = re.compile(r'^[23][A-Z0-9]{9,}')
        self.allow_list = ["simple_fixture","fqa"]
        self.stages = ["FT", "BFT", "FQA", "OFFLINE"]
        self.block_list = ["backup","tmp","routing","temp","bak","process"]
        # 提取 root 文件夹名作为 share_name (例如 store 或 store1)
        self.root_name = os.path.basename(self.root_dir.rstrip(os.sep))
        signal.signal(signal.SIGINT, self.handle_exit)

    def handle_exit(self, signum, frame):
        print(f"\n[{datetime.now()}] Stop signal received. Saving state and exiting...")
        self.running = False

    def report_self(self):
        payload = {
            "server_name": self.server_name,
            "ip": ""
        }
        try:
            json_data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.report_url, 
                data=json_data, 
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode())
                print(f"[{datetime.now()}] Register Success: {res.get('ip')}")
        except Exception as e:
            print(f"[{datetime.now()}] Register Failed: {e}")
    
    def is_valid_dir(self, dir_name):
        """第一层目录准入检查"""
        return self.re_pn_standard.match(dir_name) or dir_name.lower() in self.allow_list

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
        """解析核心：排除短索引日志，保留带时间戳日志"""
        try:
            # 1. 获取文件名和不带后缀的名称
            filename = file_path_obj.name
            stem = file_path_obj.stem

            # 2. 核心拦截逻辑：按 _ . - 切分文件名
            # 例如: "SN_20250101" -> ['SN', '20250101']
            # 例如: "SN_1" -> ['SN', '1']
            name_parts = re.split(r'[_.-]', stem)

            has_timestamp_feature = False
            # 只有当文件名被切分成多段时，才检查后面几段是否包含长数字
            if len(name_parts) > 1:
                for p in name_parts[1:]:
                    # 如果某一部分是纯数字且长度 >= 4，判定为带时间特征
                    if p.isdigit() and len(p) >= 4:
                        has_timestamp_feature = True
                        break

            # 如果不符合时间戳特征，直接跳过
            if not has_timestamp_feature:
                return None

            # --- 校验通过，开始解析元数据 ---
            # 统一路径格式（用于 Stage 和 Status 判断）
            path_str_upper = str(file_path_obj).replace('\\', '/').upper()
            f_stat = file_path_obj.stat()

            # 提取 SN：第一部分作为 SN
            sn = name_parts[0].upper()
            
            # 3. Stage 提取
            stage = "UNKNOWN"
            if "sel" in self.server_name or "meta" in self.server_name:
                stage = "BFT"

            for s in self.stages:
                s_u = s.upper()
                # 严谨匹配：确保 Stage 前后有分隔符
                temp_path = f"/{path_str_upper}/"
                if any(f"{sep}{s_u}{sep}" in temp_path for sep in ["/", "_", "."]):
                    stage = s_u
                    break
            
            # 4. LogTime (文件修改时间)
            log_time = datetime.fromtimestamp(f_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            # 5. Status 判断
            status = "PASS"
            if "FAIL" in path_str_upper: status = "FAIL"
            elif "ABORT" in path_str_upper: status = "ABORT"
            
            # 6. 计算相对路径 (结果: store/PN/xxx/log.txt)
            clean_rel = os.path.relpath(str(file_path_obj), self.root_dir).replace('\\', '/')
            full_rel_path = f"{self.root_name}/{clean_rel}"

            return {
                "server_name": self.server_name,
                "file_name": filename,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": full_rel_path,
                "share_name": self.root_name
            }
        except Exception as e:
            # 调试时可以取消注释查看报错
            # print(f"[{datetime.now()}] Parse Error in {file_path_obj}: {e}")
            return None

    def run(self):
        self.report_self()
        last_ts = self.get_last_scan_ts()
        start_scan_ts = time.time()
        batch = []
        total_count = 0

        print(f"[{datetime.now()}] Scan Started | Mode: {self.mode} | Target: {self.root_dir}")

        if not os.path.exists(self.root_dir):
            print(f"Error: Directory {self.root_dir} does not exist.")
            return

        for root, dirs, files in os.walk(self.root_dir):
            if not self.running: break
            dirs[:] = [d for d in dirs if d.lower() not in self.block_list]

            # 获取相对于根目录的层级
            rel_path = os.path.relpath(root, self.root_dir)
            parts = rel_path.split(os.sep) if rel_path != "." else []
            try:
                root_mtime = os.path.getmtime(root)
                if root_mtime <= last_ts:
                    continue
            except Exception:
                continue

            # --- PN 提取核心逻辑 ---
            current_pn = "UNKNOWN"
            if not parts:
                # 处于根目录时，过滤第一层准入
                dirs[:] = [d for d in dirs if d.lower() in self.allow_list or self.re_pn_standard.match(d.upper())]
                continue
            
            # 情况分流：
            first_dir = parts[0].lower()
            
            if first_dir in self.allow_list:
                # 情况 B：命中白名单 (如 fqa)
                # PN 应该是第二层目录 (parts[1])
                if len(parts) >= 2:
                    current_pn = parts[1].upper()
                else:
                    # 还在 fqa 这一层，没有到 PN 级，先不处理文件
                    continue
            else:
                # 情况 A：普通路径
                # PN 就是第一层目录 (parts[0])
                current_pn = parts[0].upper()

            # --- 开始处理当前目录下的文件 ---
            for filename in files:
                if not self.running: break
                # 后缀检查
                if not any(filename.lower().endswith(ext) for ext in self.extensions):
                    continue

                full_path = os.path.join(root, filename)
                try:
                    # 时间戳增量检查
                    mtime = os.path.getmtime(full_path)
                    if mtime <= last_ts: continue

                    # 解析元数据，传入动态确定的 PN
                    meta = self.parse_metadata(Path(full_path), current_pn)
                    if meta:
                        batch.append(meta)
                        if len(batch) >= self.batch_size:
                            self.post_data(batch)
                            total_count += len(batch)
                            batch = []
                except: continue

        # 上传剩余数据
        if batch:
            self.post_data(batch)
            total_count += len(batch)

        # 更新状态
        with open(self.state_file, 'w') as f:
            json.dump({"last_ts": start_scan_ts}, f)
            
        print(f"[{datetime.now()}] Scan Finished. Total Uploaded: {total_count}")
        sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log Analytics Agent")
    parser.add_argument("--dir", required=True, help="Log root directory")
    parser.add_argument("--name", default=os.uname()[1], help="Machine identity")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr', help="Scan mode")
    parser.add_argument("--ext", default=".log,.txt", help="File extensions (comma separated)")
    
    args = parser.parse_args()
    LogAgent(args).run()