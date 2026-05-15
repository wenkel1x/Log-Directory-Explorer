import json
import redis
import time
import threading
from sqlalchemy import text
from app import create_ingestion_app, db

flask_app = create_ingestion_app()
r = redis.Redis(host='127.0.0.1', port=6379, db=0)

GLOBAL_PAUSE_KEY = "log_system_pause"
ERROR_LOG_KEY = "log_errors"
LOCAL_TABLE_CACHE = set()
cache_lock = threading.Lock()

def log_error_to_redis(err_type, raw_data, err_msg):
    try:
        error_info = {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "type": err_type,
            "error": err_msg,
            "server": raw_data.get('items', [{}])[0].get('server_name', 'UNKNOWN') if isinstance(raw_data, dict) else 'N/A'
        }
        r.lpush(ERROR_LOG_KEY, json.dumps(error_info))
        r.ltrim(ERROR_LOG_KEY, 0, 999)
    except:
        pass

def handle_cleanup_task(task_data):
    s_name = task_data.get('server_name')
    sh_name = task_data.get('share_name')
    s_id = task_data.get('scan_id')
    print(f"DEBUG: Starting cleanup for Server: [{s_name}], Share: [{sh_name}], ScanID: {s_id}")
    try:
        result = db.session.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
        all_tables = [row[0] for row in result if row[0] != 'log_index_template']
        print(f"DEBUG: Found tables to scan: {all_tables}")
        total_deleted = 0
        for table_name in all_tables:
            while True:
                del_sql = text(f"""
                    DELETE FROM `{table_name}`
                    WHERE server_name = :s_name AND share_name = :sh_name AND last_scan_id != :s_id
                    LIMIT 5000
                """)
                res = db.session.execute(del_sql, {"s_name": s_name, "sh_name": sh_name, "s_id": s_id})
                db.session.commit()

                count = res.rowcount
                total_deleted += count
                if count < 5000: break
                time.sleep(0.05)
        print(f"[CLEANUP] FINISH, Total: {total_deleted}")
    except Exception as e:
        db.session.rollback()
        log_error_to_redis("CLEANUP_ERROR", task_data, str(e))
        print(f"Cleanup Error: {e}")

def process_batch_insert(data):
    global LOCAL_TABLE_CACHE
    items_list = data.get('items', [])
    scan_id = data.get('scan_id', 0)
    if not items_list: return

    table_groups = {}
    tree_updates = {}

    for item in items_list:
        log_time = item.get('log_time', '')
        year = log_time[:4] if len(log_time) >= 4 else "0000"
        t_name = f"log_index_{year}"

        if t_name not in LOCAL_TABLE_CACHE:
            with cache_lock:
                if t_name not in LOCAL_TABLE_CACHE:
                    db.session.execute(text(f"CREATE TABLE IF NOT EXISTS `{t_name}` LIKE `log_index_template`"))
                    db.session.commit()
                    LOCAL_TABLE_CACHE.add(t_name)
        item['last_scan_id'] = scan_id
        table_groups.setdefault(t_name, []).append(item)

        tree_key = (item.get('server_name'), item.get('share_name'), item.get('pn'))
        if all(tree_key):
            tree_updates[tree_key] = max(tree_updates.get(tree_key, 0), int(year))

    for attempt in range(3):
        try:
            for t_name, group in table_groups.items():
                sql = text(f"""
                    INSERT INTO `{t_name}`
                    (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name, last_scan_id)
                    VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name, :last_scan_id)
                    ON DUPLICATE KEY UPDATE
                    status=VALUES(status), stage=VALUES(stage), log_time=VALUES(log_time), last_scan_id=VALUES(last_scan_id)
                """)
                db.session.execute(sql, group)

            if tree_updates:
                path_params = [{"sn": k[0], "sh": k[1], "pn": k[2], "yr": v} for k, v in tree_updates.items()]
                db.session.execute(text("""
                    INSERT INTO log_tree_data (server_name, share_name, pn, last_active_year)
                    VALUES (:sn, :sh, :pn, :yr)
                    ON DUPLICATE KEY UPDATE last_active_year = GREATEST(last_active_year, VALUES(last_active_year))
                """), path_params)

            db.session.commit()
            print(f"[{time.strftime('%H:%M:%S')}] success deal with {len(items_list)}")
            return
        except Exception as e:
            db.session.rollback()
            # 捕获死锁或锁超时
            if ("1213" in str(e) or "1205" in str(e)) and attempt < 2:
                print(f"[*] 检测到死锁，正在进行第 {attempt+1} 次重试...")
                time.sleep(0.5 * (attempt + 1))
                continue
            log_error_to_redis("DB_INSERT_ERROR", data, str(e))
            raise e

def start_worker():
    global LOCAL_TABLE_CACHE
    with flask_app.app_context():
        rows = db.session.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
        LOCAL_TABLE_CACHE = {row[0] for row in rows}
        while True:
            try:
                if r.get(GLOBAL_PAUSE_KEY) == b"1":
                    time.sleep(2)
                    continue

                res = r.brpop("log_upload_queue", timeout=5)
                if not res: continue

                raw_data = json.loads(res[1])
                # 任务分发
                if raw_data.get('type') == 'cleanup_task':
                    r.setex(GLOBAL_PAUSE_KEY, 3600, "1")
                    try:
                        handle_cleanup_task(raw_data)
                    finally:
                        r.delete(GLOBAL_PAUSE_KEY)
                else:
                    process_batch_insert(raw_data)
            except Exception as e:
                print(f"--- [ERROR]: {str(e)}")
                time.sleep(1)
if __name__ == '__main__':
    start_worker()