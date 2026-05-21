#!/usr/bin/env python3
import os
import json
import re
import argparse
import time
import urllib.request
import socket
from datetime import datetime
from pathlib import Path
import signal
from concurrent.futures import ThreadPoolExecutor
import sys

class LogAgent:
    def __init__(self, args):
        self.root_dir = os.path.abspath(args.dir)
        self.server_name = args.name.lower() if args.name else socket.gethostname().lower()
        self.mode = args.mode  
        self.ext_list = [e.lower() for e in args.ext.split(',')]
        self.project_key = args.project_key.strip().lower()

        self.api_base = "http://10.94.99.153/svc"
        self.upload_url = f"{self.api_base}/upload_batch"
        self.cleanup_url = f"{self.api_base}/cleanup"
        self.report_url = f"{self.api_base}/report_ip"

        current_script_dir = Path(__file__).parent
        self.state_file = current_script_dir / f".state_{self.server_name}.json"

        self.re_pn_standard = re.compile(r'^[123][A-Z0-9]{10}$')
        self.load_config()

        self.batch_size = 1000
        self.running = True
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.root_name = os.path.basename(self.root_dir.rstrip(os.sep))
        #self.re_year = (r'(?:[_/-])(20\d{2})(?:[_/-]|$)')
        self.re_stem_split = re.compile(r'[_.-]')

        self.current_year = datetime.now().year

        signal.signal(signal.SIGINT, self.handle_exit)

    def load_config(self):
        config_path = Path(__file__).parent / "config.json"
        if not config_path.exists():
            print(f"CRITICAL: config.json not found at {config_path}")
            sys.exit(1)
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                conf = json.load(f)
                raw_patterns = [p.upper().split('/') for p in conf.get("patterns", [])]
                self.original_patterns = conf.get("patterns", [])
                self.patterns = [set(p) for p in raw_patterns]
                self.first_level_allowed = {p[0] for p in raw_patterns if len(p) > 0}

                self.pn_parents = [p.upper() for p in conf.get("pn_parent_folders", [])]
                self.valid_stages = [s.upper() for s in conf.get("valid_stages", [])]
                self.valid_status = [s.upper() for s in conf.get("valid_status", [])]
                self.block_list = {b.lower() for b in conf.get("block_list", [])}
                if not self.patterns:
                    raise ValueError("No patterns defined in config.json")
        except Exception as e:
            print(f"CRITICAL: Failed to load config.json: {e}")
            sys.exit(1)

    def handle_exit(self, signum, frame):
        self.running = False

    def match_path(self, file_path_obj):
        try:
            rel_parts = [p.upper() for p in file_path_obj.relative_to(self.root_dir).parts]
            if any(any(b in part.lower() for b in self.block_list) for part in rel_parts):
                return False
            raw_patterns = [p.upper().split('/') for p in self.original_patterns]
            for pattern in raw_patterns:
                idx = 0
                match_count = 0
                for part in rel_parts:
                    target = pattern[idx]
                    is_match = False
                    if target == "PN":
                        if self.re_pn_standard.match(part):
                            is_match = True
                    else:
                        if target == part:
                            is_match = True
                    if is_match:
                        match_count += 1
                        idx += 1
                        if match_count == len(pattern):
                            return True
            return False
        except:
            return False

    def get_fast_time(self, entry_path, f_stat, mtime_dt):
        try:
            ctime_dt = datetime.fromtimestamp(f_stat.st_ctime)
            if 2010 <= ctime_dt.year <= self.current_year:
                return ctime_dt.strftime('%Y-%m-%d %H:%M:%S')
        except: pass
        return mtime_dt.strftime('%Y-%m-%d %H:%M:%S')

    def parse_data(self, entry, pn, f_stat):
        try:
            file_name = entry.name
            dot_idx = file_name.rfind('.')
            stem = file_name[:dot_idx] if dot_idx != -1 else file_name
            name_parts = self.re_stem_split.split(stem)
            if len(name_parts) <= 1 or not any(p.isdigit() and len(p) >= 4 for p in name_parts[1:]):
                return None
            mtime_dt = datetime.fromtimestamp(f_stat.st_mtime)
            if 2010 <= mtime_dt.year <= self.current_year:
                log_time = mtime_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                log_time = self.get_fast_time(entry.path, f_stat, mtime_dt)

            path_str_upper = entry.path.upper().replace('\\', '/')
            sn = self.re_stem_split.split(stem)[0].upper()

            rel_p = entry.path[len(self.root_dir):].replace('\\', '/').lstrip('/')

            # 判定 stage
            stage = "BFT"
            path_parts = set(path_str_upper.replace('\\', '/').replace('_', '/').split('/'))
            for s in self.valid_stages:
                if s in path_parts:
                    stage = s
                    break

            # 判定 status
            status = "PASS"
            for st in self.valid_status:
                if f"/{st}/" in path_str_upper or f"_{st}_" in path_str_upper:
                    status = st
                    break

            return {
                "server_name": self.server_name,
                "file_name": entry.name,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": f"{self.root_name}/{rel_p}",
                "share_name": self.root_name
            }
        except: return None

    def fast_scan(self, current_dir, last_ts, current_pn="UNKNOWN", depth=0):
        if not self.running: return
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.is_symlink(): continue
                        if entry.name.lower() in self.block_list: continue
                        dirname_upper = entry.name.upper()

                        if depth == 0:
                            is_pn = ("PN" in self.first_level_allowed and bool(self.re_pn_standard.match(dirname_upper)))
                            is_keyword = dirname_upper in self.first_level_allowed and dirname_upper != "PN"
                            if not (is_pn or is_keyword): continue
                            next_pn = dirname_upper if is_pn else "UNKNOWN"
                        else:
                            next_pn = current_pn
                            if os.path.basename(current_dir).upper() in self.pn_parents:
                                next_pn = dirname_upper
                            elif self.re_pn_standard.match(dirname_upper):
                                next_pn = dirname_upper
                        yield from self.fast_scan(entry.path, last_ts, next_pn, depth + 1)
                    elif entry.is_file():
                        if any(entry.name.lower().endswith(ext) for ext in self.ext_list):
                            f_stat = entry.stat()
                            if f_stat.st_mtime > last_ts:
                                yield entry, current_pn, f_stat
        except: pass

    def post_data(self, items, scan_id):
        if not items: return True
        # 尝试 3 次
        max_retries = 3
        for attempt in range(max_retries):
            try:
                payload = {
                    "project_key": self.project_key,
                    "items": items,
                    "scan_id": scan_id
                }
                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    self.upload_url,
                    data=data,
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=30) as response:
                    if 200 <= response.getcode() < 300:
                        return True
            except Exception as e:
                wait_time = (attempt + 1) * 2
                print(f"[{datetime.now()}] Upload batch failed (Attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                else:
                    print(f"[{datetime.now()}] CRITICAL: Batch failed after {max_retries} attempts.")
        return False

    def report_self(self):
        """上报自身 IP 及状态"""
        payload = {"server_name": self.server_name, "ip": ""}
        try:
            json_data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(self.report_url, data=json_data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                pass
        except: pass
    def run(self):
        self.report_self()
        current_scan_id = int(time.time())
        last_ts = 0

        # 1. 初始化计数器
        scanned_count = 0
        success_count = 0
        if self.mode == 'incr' and self.state_file.exists():
            try:
                state_content = self.state_file.read_text(encoding='utf-8')
                last_ts = json.loads(state_content).get("last_ts", 0)
            except:
                last_ts = 0

        batch = []
        futures = set()
        upload_all_success = True
        start_ts = time.time()

        print(f"[{datetime.now()}] Scan Start. Mode: {self.mode} | Scan ID: {current_scan_id}")

        for entry, pn, f_stat in self.fast_scan(self.root_dir, last_ts):
            if not self.running: break
            if not self.match_path(Path(entry.path)):
                continue
            parsedata = self.parse_data(entry, pn, f_stat)
            if parsedata:
                scanned_count += 1
                batch.append(parsedata)

                if len(batch) >= self.batch_size:
                    # --- 内存与并发控制闸门 ---
                    while len(futures) >= 20:
                        # 检查已完成的任务
                        done = {(f, b_len) for f, b_len in futures if f.done()}
                        for f, b_len in done:
                            try:
                                if f.result() is True:
                                    success_count += b_len # 只有成功才加到 success_count
                                else:
                                    upload_all_success = False
                            except:
                                upload_all_success = False
                            futures.remove((f, b_len))
                        if len(futures) >= 20:
                            time.sleep(0.1)

                    # 提交任务，并将 (任务, 数量) 作为元组存入 set
                    this_batch_len = len(batch)
                    f = self.executor.submit(self.post_data, list(batch), current_scan_id)
                    futures.add((f, this_batch_len))

                    batch = []
                    # 打印进度（以扫描到的数量为准）
                    if scanned_count % 100000 == 0:
                        print(f"[{datetime.now()}] Progress: {scanned_count} files scanned...")

        # 2. 处理最后一批剩余数据
        if batch and self.running:
            this_batch_len = len(batch)
            f = self.executor.submit(self.post_data, list(batch), current_scan_id)
            futures.add((f, this_batch_len))

        print(f"[{datetime.now()}] Scanning finished. Finalizing {len(futures)} uploads...")

        # 3. 等待所有剩余线程完成并结算
        self.executor.shutdown(wait=True)
        for f, b_len in futures:
            try:
                if f.result() is True:
                    success_count += b_len
                else:
                    upload_all_success = False
            except:
                upload_all_success = False

        # 4. 全量模式下的清理逻辑
        if self.mode == 'full' and scanned_count > 0 and upload_all_success and self.running:
            print(f"[{datetime.now()}] All batches OK. Triggering cleanup...")
            try:
                cleanup_payload = {
                    "project_key": self.project_key,
                    "server_name": self.server_name,
                    "share_name": self.root_name,
                    "scan_id": current_scan_id
                }
                req = urllib.request.Request(
                    self.cleanup_url,
                    data=json.dumps(cleanup_payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read().decode('utf-8'))
                    print(f"[{datetime.now()}] Cleanup Done! Removed: {result.get('deleted_count', 0)}")
            except Exception as e:
                print(f"Cleanup failed: {e}")

        # 5. 保存状态与最终汇报
        if upload_all_success and self.running:
            try:
                with open(self.state_file, 'w', encoding='utf-8') as f:
                    json.dump({"last_ts": start_ts - 60}, f)
                print(f"[{datetime.now()}] SUCCESS!")
                print(f"  - Scanned: {scanned_count}")
                print(f"  - Uploaded: {success_count}")
            except Exception as e:
                print(f"Failed to save state: {e}")
        else:
            print(f"[{datetime.now()}] EXITED WITH ERRORS. Success: {success_count}/{scanned_count}. Progress NOT saved.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--name")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr')
    parser.add_argument("--ext", default=".log,.txt")
    parser.add_argument('--project_key', type=str, default='log_system', help="Target project database: log_system (BFT) or ict_log_system (ICT)")
    args = parser.parse_args()
    LogAgent(args).run()