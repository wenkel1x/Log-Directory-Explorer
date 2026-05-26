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
from concurrent.futures import ThreadPoolExecutor, wait  # 引入 wait
class IctLogAgent:
    def __init__(self, args):
        self.root_dir = os.path.abspath(args.dir)
        self.server_name = args.name.lower() if args.name else socket.gethostname().lower()
        self.mode = args.mode
        self.interval = args.interval
        self.dry_run = args.dry_run
        self.ext_list = [e.lower() for e in args.ext.split(',')]
        self.project_key = "ict_log_system"
        self.api_base = "http://10.94.99.153/svc"
        self.upload_url = f"{self.api_base}/upload_batch"
        self.cleanup_url = f"{self.api_base}/cleanup"
        self.report_url = f"{self.api_base}/report_ip"
        current_script_dir = Path(__file__).parent
        self.state_file = current_script_dir / f".state_ict_{self.server_name}.json"
        # ========================================================
        # Regular Expression Rules
        # ========================================================
        self.re_fixture = re.compile(r'^(AA|AB)[A-Z0-9]+$', re.IGNORECASE)
        self.re_pn_standard = re.compile(r'^[123][A-Z0-9]{10}$')
        self.re_history_parse = re.compile(r'^([A-Z0-9\-]+)_(PASS|FAIL)_(ICT|OST)_(\d{8,14grid})(?:\.[a-z0-9]+)?$', re.IGNORECASE)
        self.re_history_dcl_parse = re.compile(r'^([A-Z0-9\-]+)-(\d{8,14})-(PASS|FAIL)(?:\.[a-z0-9]+)?$', re.IGNORECASE)
        self.re_result_parse = re.compile(r'^([A-Z0-9\-]+)_(\d{8,14})(?:\.[a-z0-9]+)?$', re.IGNORECASE)
        self.batch_size = 1000
        self.running = True
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.root_name = os.path.basename(self.root_dir.rstrip(os.sep))
        signal.signal(signal.SIGINT, self.handle_exit)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.handle_exit)
    def handle_exit(self, signum, frame):
        print(f"\n[{datetime.now()}] Exit signal received. Shutting down gracefully...")
        self.running = False
    def parse_data(self, entry, fixture_no, pn, f_stat, anchor_type):
        try:
            file_name = entry.name
            if file_name.lower().endswith('.lnk') or file_name.startswith('.'):
                return None
            sn, status, raw_time_str, stage = None, "PASS", None, "test_history"
            if anchor_type == "history":
                match = self.re_history_parse.match(file_name)
                if match:
                    raw_sn, raw_status, raw_stage, raw_time_str = match.groups()
                    sn = raw_sn.upper()
                    status = raw_status.upper()
                    if raw_stage.upper() == "OST":
                        stage = "OST"
                    else:
                        stage = "test_history"
                else:
                    match_dcl = self.re_history_dcl_parse.match(file_name)
                    if not match_dcl: return None
                    raw_sn, raw_time_str, raw_status = match_dcl.groups()
                    sn = raw_sn.upper()
                    status = raw_status.upper()
                    if "OST" in file_name.upper():
                        stage = "OST"
                    else:
                        stage = "test_history"
            elif anchor_type == "result":
                stage = "testresult"
                status = "FAIL"
                match = self.re_result_parse.match(file_name)
                if not match: return None
                raw_sn, raw_time_str = match.groups()
                sn = raw_sn.upper()
            log_time = None
            if raw_time_str:
                try:
                    if len(raw_time_str) >= 14:
                        dt = datetime.strptime(raw_time_str[:14], '%Y%m%d%H%M%S')
                        log_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                    elif len(raw_time_str) == 8:
                        dt = datetime.strptime(raw_time_str, '%Y%m%d')
                        log_time = dt.strftime('%Y-%m-%d 00:00:00')
                except ValueError:
                    pass
            if not log_time:
                mtime_dt = datetime.fromtimestamp(f_stat.st_mtime)
                log_time = mtime_dt.strftime('%Y-%m-%d %H:%M:%S')
            if not pn:
                p_parts = Path(entry.path).parts
                for idx, part in enumerate(p_parts):
                    if part.lower() in ['test_history', 'testresult'] and idx > 0:
                        pn = p_parts[idx-1]
                        break
                if not pn: pn = "UNKNOWN_PN"
            rel_p = os.path.relpath(entry.path, self.root_dir).replace('\\', '/')
            return {
                "server_name": self.server_name,
                "file_name": file_name,
                "pn": pn,
                "sn": sn,
                "log_time": log_time,
                "status": status,
                "stage": stage,
                "relative_path": f"{self.root_name}/{rel_p}",
                "share_name": self.root_name
            }
        except Exception:
            return None
    def start_scan(self, last_ts):
        if not self.running: return
        mode_label = " (Dry-Run Mode: Local Print Only)" if self.dry_run else ""
        print(f"\n[{datetime.now()}] === Starting directory scan{mode_label}: {self.root_dir} ===")
        yield from self._find_anchors_recursive(self.root_dir, last_ts)
    def _find_anchors_recursive(self, current_path, last_ts):
        try:
            with os.scandir(current_path) as it:
                for entry in it:
                    if not self.running: break
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith('.'):
                            continue
                        dirname_lower = entry.name.lower()
                        if dirname_lower in ['testresult', 'test_history']:
                            anchor_type = "result" if dirname_lower == 'testresult' else "history"
                            yield from self._collect_logs_direct_under_anchor(entry.path, last_ts, anchor_type)
                            yield from self._scan_fixture_dir(entry.path, last_ts, anchor_type)
                        else:
                            yield from self._find_anchors_recursive(entry.path, last_ts)
        except Exception: pass
    def _collect_logs_direct_under_anchor(self, anchor_path, last_ts, anchor_type):
        try:
            with os.scandir(anchor_path) as it:
                for entry in it:
                    if not self.running: break
                    if entry.is_file(follow_symlinks=False):
                        file_name = entry.name
                        if file_name.lower().endswith('.lnk') or file_name.startswith('.'):
                            continue
                        has_valid_ext = any(file_name.lower().endswith(ext) for ext in self.ext_list)
                        is_raw_log = ('.' not in file_name) and ('_' in file_name or '-' in file_name)
                        if file_name.lower().endswith('.dcl'):
                            has_valid_ext = True
                        if has_valid_ext or is_raw_log:
                            f_stat = entry.stat()
                            if f_stat.st_mtime > last_ts:
                                yield entry, None, None, f_stat, anchor_type
        except Exception: pass
    def _scan_fixture_dir(self, anchor_path, last_ts, anchor_type):
        try:
            with os.scandir(anchor_path) as it:
                for entry in it:
                    if not self.running: break
                    if entry.is_dir(follow_symlinks=False):
                        if self.re_fixture.match(entry.name):
                            yield from self._scan_pn_dir(entry.path, entry.name.upper(), last_ts, anchor_type)
        except Exception: pass
    def _scan_pn_dir(self, fixture_path, fixture_no, last_ts, anchor_type):
        try:
            with os.scandir(fixture_path) as it:
                for entry in it:
                    if not self.running: break
                    if entry.is_dir(follow_symlinks=False):
                        pn_upper = entry.name.upper()
                        if self.re_pn_standard.match(pn_upper):
                            yield from self._collect_logs(entry.path, fixture_no, pn_upper, last_ts, anchor_type)
        except Exception: pass
    def _collect_logs(self, pn_path, fixture_no, pn, last_ts, anchor_type):
        try:
            with os.scandir(pn_path) as it:
                for entry in it:
                    if not self.running: break
                    if entry.is_file(follow_symlinks=False):
                        file_name = entry.name
                        if file_name.lower().endswith('.lnk') or file_name.startswith('.'):
                            continue
                        has_valid_ext = any(file_name.lower().endswith(ext) for ext in self.ext_list)
                        is_raw_log = ('.' not in file_name) and ('_' in file_name or '-' in file_name)
                        if file_name.lower().endswith('.dcl'):
                            has_valid_ext = True
                        if has_valid_ext or is_raw_log:
                            f_stat = entry.stat()
                            if f_stat.st_mtime > last_ts:
                                yield entry, fixture_no, pn, f_stat, anchor_type
        except Exception: pass
    def post_data(self, items, scan_id):
        if not items: return True
        if self.dry_run: return True
        max_retries = 3
        for attempt in range(max_retries):
            try:
                payload = {"project_key": self.project_key, "items": items, "scan_id": scan_id}
                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(self.upload_url, data=data, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    if 200 <= response.getcode() < 300: return True
            except:
                if attempt < max_retries - 1: time.sleep((attempt + 1) * 2)
        return False
    def report_self(self):
        if self.dry_run: return
        try:
            payload = {"project_key": self.project_key, "server_name": self.server_name, "ip": ""}
            req = urllib.request.Request(self.report_url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response: pass
        except: pass
    def execute_scan(self):
        self.report_self()
        current_scan_id = int(time.time())
        last_ts = 0
        scanned_count = 0
        if self.mode == 'incr' and self.state_file.exists():
            try: last_ts = json.loads(self.state_file.read_text(encoding='utf-8')).get("last_ts", 0)
            except: last_ts = 0
        batch = []
        futures = set()
        upload_all_success = True
        start_ts = time.time()
        for entry, fixture_no, pn, f_stat, anchor_type in self.start_scan(last_ts):
            if not self.running: break
            parsedata = self.parse_data(entry, fixture_no, pn, f_stat, anchor_type)
            if parsedata:
                scanned_count += 1
                batch.append(parsedata)
                if len(batch) >= self.batch_size:
                    completed = {f for f in futures if f.done()}
                    for f in completed:
                        try:
                            if f.result() is not True: upload_all_success = False
                        except: upload_all_success = False
                        futures.remove(f)
                    if len(futures) >= 10:
                        time.sleep(0.2)
                    f = self.executor.submit(self.post_data, list(batch), current_scan_id)
                    futures.add(f)
                    print(f"[{datetime.now()}] Batch dispatched. Total logs packed into queue: {scanned_count}")
                    batch = []
        if batch and self.running:
            f = self.executor.submit(self.post_data, list(batch), current_scan_id)
            futures.add(f)
            print(f"[{datetime.now()}] Final batch dispatched. Total logs packed into queue: {scanned_count}")
        if futures:
            wait(futures)
            for f in futures:
                try:
                    if f.result() is not True: upload_all_success = False
                except: upload_all_success = False
        if upload_all_success and self.running:
            try:
                if self.mode == 'incr' and not self.dry_run:
                    with open(self.state_file, 'w', encoding='utf-8') as f:
                        json.dump({"last_ts": start_ts - 60}, f)
                print(f"[{datetime.now()}] Scan completed successfully. Total processed logs: {scanned_count}")
                if self.dry_run:
                    print(f"[{datetime.now()}] Dry-Run finished: All records simulated locally. No data transmitted to server.")
                    return
                try:
                    cleanup_payload = {
                        "project_key": self.project_key,
                        "server_name": self.server_name,
                        "scan_id": current_scan_id
                    }
                    cleanup_req = urllib.request.Request(
                        self.cleanup_url,
                        data=json.dumps(cleanup_payload).encode('utf-8'),
                        headers={'Content-Type': 'application/json'}
                    )
                    with urllib.request.urlopen(cleanup_req, timeout=30) as response:
                        if 200 <= response.getcode() < 300:
                            print(f"[{datetime.now()}] Async server data cleanup command sent successfully.")
                except Exception as ce:
                    print(f"[{datetime.now()}] Failed to trigger async server cleanup: {ce}")
            except: pass
        else:
            print(f"[{datetime.now()}] Scan aborted prematurely or encountered transmission errors.")
    def run(self):
        if self.mode == 'full':
            self.execute_scan()
            self.executor.shutdown(wait=True)
            print(f"[{datetime.now()}] Full scan completed. Agent exit.")
        else:
            print(f"[{datetime.now()}] Enters Incr incremental mode, executing every {self.interval} minutes. Press Ctrl+C to exit safely.")
            try:
                while self.running:
                    self.execute_scan()
                    if not self.running: break
                    print(f"[{datetime.now()}] Waiting for the next period...")
                    for _ in range(int(self.interval * 60)):
                        if not self.running:
                            break
                        time.sleep(1)
            finally:
                self.executor.shutdown(wait=True)
                print(f"[{datetime.now()}] The incremental loop has been safely terminated.")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Root directory to scan")
    parser.add_argument("--name", help="Server name identity")
    parser.add_argument("--mode", choices=['full', 'incr'], default='incr', help="Scan mode: full or incremental")
    parser.add_argument("--ext", default=".txt,.log", help="Comma-separated target extensions")
    parser.add_argument("--interval", type=float, default=10.0, help="Scan interval in minutes (only for incr mode)")
    parser.add_argument("--dry-run", action="store_true", help="Print parsing outcomes locally without sending data")
    args = parser.parse_args()
    IctLogAgent(args).run()