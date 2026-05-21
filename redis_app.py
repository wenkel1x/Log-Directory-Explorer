#!/usr/bin/env python3
import json
import redis
import time
import threading
from sqlalchemy import text
from app import create_ingestion_app, db

PROJECT_MAP = {
    'log_system': 'log_system',
    'ict_log_system': 'ict_log_System',
}

flask_app = create_ingestion_app()
r = redis.Redis(host='127.0.0.1', port=6379, db=0)

GLOBAL_PAUSE_KEY = "log_system_pause"
LOCAL_TABLE_CACHE = set()
cache_lock = threading.Lock()

def handle_cleanup_task(task_data):
    s_name = task_data.get('server_name')
    sh_name = task_data.get('share_name')
    s_id = task_data.get('scan_id')
    # 提取项目标签，切到对应的数据库 Engine
    project_key = task_data.get('project_key', 'log_system').strip().lower()
    if project_key not in PROJECT_MAP: project_key = 'log_system'
    actual_bind_key = PROJECT_MAP[project_key]
    db_engine = db.get_engine(bind=actual_bind_key)
    print(f"DEBUG: Starting cleanup for DB Bind: [{actual_bind_key}]")
    try:
        with db_engine.begin() as conn:
            result = conn.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
            all_tables = [row[0] for row in result if row[0] != 'log_index_template']
        total_deleted = 0
        for table_name in all_tables:
            while True:
                with db_engine.begin() as conn:
                    del_sql = text(f"""
                        DELETE FROM `{table_name}`
                        WHERE server_name = :s_name AND share_name = :sh_name AND last_scan_id != :s_id
                        LIMIT 5000
                    """)
                    res = conn.execute(del_sql, {"s_name": s_name, "sh_name": sh_name, "s_id": s_id})
                    count = res.rowcount
                    total_deleted += count
                if count < 5000: break
                time.sleep(0.05)
        print(f"[{actual_bind_key} CLEANUP] FINISH. Total deleted: {total_deleted}")
    except Exception as e:
        print(f"Cleanup Error on [{actual_bind_key}]: {e}")

def process_batch_insert(data):
    global LOCAL_TABLE_CACHE
    items_list = data.get('items', [])
    scan_id = data.get('scan_id', 0)
    if not items_list: return
    # 从 Redis 数据包中识别 project_key
    project_key = data.get('project_key', 'log_system').strip().lower()
    if project_key not in PROJECT_MAP: project_key = 'log_system'

    # 通过绑定的 key 获取对应的物理库连接池 Engine
    actual_bind_key = PROJECT_MAP[project_key]
    db_engine = db.get_engine(bind=actual_bind_key)

    table_groups = {}
    tree_updates = {}

    for item in items_list:
        log_time = item.get('log_time', '')
        year = log_time[:4] if len(log_time) >= 4 else "0000"
        t_name = f"log_index_{year}"

        #动态建表检测（缓存加上实际绑定库前缀 actual_bind_key，防止跨库缓存混淆）
        cache_key = f"{actual_bind_key}:{t_name}"
        if cache_key not in LOCAL_TABLE_CACHE:
            with cache_lock:
                if cache_key not in LOCAL_TABLE_CACHE:
                    with db_engine.begin() as conn:
                        conn.execute(text(f"CREATE TABLE IF NOT EXISTS `{t_name}` LIKE `log_index_template`"))
                    LOCAL_TABLE_CACHE.add(cache_key)
        item['last_scan_id'] = scan_id
        table_groups.setdefault(t_name, []).append(item)

        tree_key = (item.get('server_name'), item.get('share_name'), item.get('pn'))
        if all(tree_key):
            tree_updates[tree_key] = max(tree_updates.get(tree_key, 0), int(year))

    for attempt in range(3):
        try:
            with db_engine.begin() as conn:
                for t_name, group in table_groups.items():
                    sql = text(f"""
                        INSERT INTO `{t_name}`
                        (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name, last_scan_id)
                        VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name, :last_scan_id)
                        ON DUPLICATE KEY UPDATE
                        status=VALUES(status), stage=VALUES(stage), log_time=VALUES(log_time), last_scan_id=VALUES(last_scan_id)
                    """)
                    conn.execute(sql, group)

                if tree_updates:
                    path_params = [{"sn": k[0], "sh": k[1], "pn": k[2], "yr": v} for k, v in tree_updates.items()]
                    conn.execute(text("""
                        INSERT INTO log_tree_data (server_name, share_name, pn, last_active_year)
                        VALUES (:sn, :sh, :pn, :yr)
                        ON DUPLICATE KEY UPDATE last_active_year = GREATEST(last_active_year, VALUES(last_active_year))
                    """), path_params)

            print(f"[{time.strftime('%H:%M:%S')}] [{actual_bind_key}] Successfully inserted {len(items_list)} items.")
            return
        except Exception as e:
            if ("1213" in str(e) or "1205" in str(e)) and attempt < 2:
                print(f"[*] [{actual_bind_key}] Deadlock detected, retrying...")
                time.sleep(0.5 * (attempt + 1))
                continue
            print(f"DB Error on [{actual_bind_key}]: {e}")
            raise e

def start_worker():
    global LOCAL_TABLE_CACHE
    with flask_app.app_context():
        LISTEN_QUEUES = [f"log_upload_queue:{p_key}" for p_key in PROJECT_MAP.keys()]
        for _, bind_key in PROJECT_MAP.items():
            try:
                engine = db.get_engine(bind=bind_key)
                with engine.begin() as conn:
                    rows = conn.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
                    for row in rows:
                        LOCAL_TABLE_CACHE.add(f"{bind_key}:{row[0]}")
            except Exception as e:
                print(f"Warning: Cannot cache tables for bind [{bind_key}]: {e}")

        print(f"[*] Redis Worker Started.")
        print(f"[*] Listening Queues: {LISTEN_QUEUES}")
        print(f"[*] Cached tables count: {len(LOCAL_TABLE_CACHE)}")

        while True:
            try:
                res = r.brpop(LISTEN_QUEUES, timeout=5)
                if not res: continue

                active_queue = res[0].decode('utf-8')
                raw_data = json.loads(res[1])
                project_key = raw_data.get('project_key', 'log_system').strip().lower()

                pause_key = f"{project_key}_pause"
                if r.get(pause_key) == b"1":
                    r.lpush(active_queue, res[1])
                    time.sleep(1)
                    continue

                if raw_data.get('type') == 'cleanup_task':
                    r.setex(pause_key, 3600, "1")
                    try:
                        handle_cleanup_task(raw_data)
                    finally:
                        r.delete(pause_key)
                else:
                    process_batch_insert(raw_data)
            except Exception as e:
                print(f"--- [Worker Global Error]: {str(e)}")
                time.sleep(1)
if __name__ == '__main__':
    start_worker()