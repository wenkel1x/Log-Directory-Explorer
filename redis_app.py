#!/usr/bin/env python3
import json
import redis
import time
import threading
import logging
import re
from sqlalchemy import text
from app import create_ingestion_app, db

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

PROJECT_MAP = {
    'log_system': 'log_system',
    'ict_log_system': 'ict_log_system',
}

flask_app = create_ingestion_app()

# Redis连接延迟初始化
_redis_client = None

def get_redis_client():
    """获取Redis客户端延迟初始化"""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.Redis(
                host='127.0.0.1',
                port=6379,
                db=0,
                socket_connect_timeout=5,
                socket_keepalive=True,
                decode_responses=False,
                max_connections=10
            )
            # 测试连接
            _redis_client.ping()
            logger.info("Redis connection established successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    return _redis_client

GLOBAL_PAUSE_KEY = "log_system_pause"
LOCAL_TABLE_CACHE = set()
cache_lock = threading.Lock()

# 表名验证正则
TABLE_NAME_PATTERN = re.compile(r'^log_index_\d{4}$')

def validate_table_name(t_name):
    """验证表名格式防止SQL注入"""
    if not TABLE_NAME_PATTERN.match(t_name):
        raise ValueError(f"Invalid table name format: {t_name}")
    return t_name

def extract_year(log_time):
    """
    从日志时间中提取年份支持多种格式
    允许的格式: YYYY-* 或 YYYY*
    """
    try:
        if isinstance(log_time, str) and len(log_time) >= 4:
            # 提取前4个字符
            year_str = log_time[:4]
            # 验证是否为4位数字
            if year_str.isdigit():
                year = int(year_str)
                # 验证年份在合理范围内
                if 1900 <= year <= 2100:
                    return year_str
            else:
                logger.warning(f"Invalid year format in log_time: {log_time}, using default '0000'")
                return "0000"
    except Exception as e:
        logger.warning(f"Error extracting year from log_time '{log_time}': {e}")

    return "0000"

def handle_cleanup_task(task_data):
    s_name = task_data.get('server_name')
    sh_name = task_data.get('share_name')
    s_id = task_data.get('scan_id')

    project_key = task_data.get('project_key', 'log_system').strip().lower()
    if project_key not in PROJECT_MAP:
        project_key = 'log_system'
    actual_bind_key = PROJECT_MAP[project_key]

    db_engine = db.engines[actual_bind_key]
    logger.info(f"Starting cleanup for DB Bind: [{actual_bind_key}], Server: [{s_name}], Share: [{sh_name}], ScanID: {s_id}")
    # 参数验证
    if not all([s_name, sh_name, s_id]):
        logger.error(f"Invalid cleanup task parameters: {task_data}")
        return
    try:
        all_tables = []
        with db_engine.begin() as conn:
            result = conn.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
            for t_row in result:
                if t_row[0] != 'log_index_template':
                    all_tables.append(t_row[0])

        total_deleted = 0
        for table_name in all_tables:
            try:
                validate_table_name(table_name)
            except ValueError as e:
                logger.warning(f"[{actual_bind_key}] Skipping invalid table: {e}")
                continue

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
                if count < 5000:
                    break
                time.sleep(0.01)

        logger.info(f"[{actual_bind_key} CLEANUP] Log indices cleaned. Total deleted: {total_deleted}")
        logger.debug(f"[{actual_bind_key}] Extracting alive PNs with their respective years...")
        alive_pn_years = set()

        for table_name in all_tables:
            try:
                table_year = int(table_name.replace('log_index_', ''))
                validate_table_name(table_name)
            except ValueError:
                logger.warning(f"[{actual_bind_key}] Invalid table name: {table_name}")
                continue
            with db_engine.begin() as conn:
                pn_sql = text(f"SELECT DISTINCT pn FROM `{table_name}` WHERE server_name = :s_name AND share_name = :sh_name")
                rows = conn.execute(pn_sql, {"s_name": s_name, "sh_name": sh_name}).fetchall()
                for db_data_row in rows:
                    if db_data_row[0]:
                        alive_pn_years.add((table_year, db_data_row[0]))

        with db_engine.begin() as conn:
            current_tree_sql = text("""
                SELECT id, last_active_year, pn FROM `log_tree_data`
                WHERE server_name = :s_name AND share_name = :sh_name
            """)
            tree_rows = conn.execute(current_tree_sql, {"s_name": s_name, "sh_name": sh_name}).fetchall()

            ids_to_delete = []
            for t_id, t_year, t_pn in tree_rows:
                if (t_year, t_pn) not in alive_pn_years:
                    ids_to_delete.append(t_id)

            tree_deleted_count = 0
            if ids_to_delete:
                for i in range(0, len(ids_to_delete), 1000):
                    chunk = ids_to_delete[i:i+1000]
                    conn.execute(text("DELETE FROM `log_tree_data` WHERE id IN :ids"), {"ids": tuple(chunk)})
        logger.info(f"[{actual_bind_key} TREE CLEANUP] FINISH. Removed {tree_deleted_count} stale PN rows from log_tree_data.")
    except Exception as e:
        logger.error(f"Cleanup Error on [{actual_bind_key}]: {e}", exc_info=True)

def process_batch_insert(data):
    global LOCAL_TABLE_CACHE
    items_list = data.get('items', [])
    scan_id = data.get('scan_id', 0)
    if not items_list:
        logger.debug("Empty items list received")
        return

    project_key = data.get('project_key', 'log_system').strip().lower()
    if project_key not in PROJECT_MAP:
        project_key = 'log_system'

    actual_bind_key = PROJECT_MAP[project_key]
    db_engine = db.engines[actual_bind_key]

    table_groups = {}
    tree_updates = {}

    for item in items_list:
        log_time = item.get('log_time', '')
        year = extract_year(log_time)
        t_name = f"log_index_{year}"

        cache_key = f"{actual_bind_key}:{t_name}"
        if cache_key not in LOCAL_TABLE_CACHE:
            with cache_lock:
                if cache_key not in LOCAL_TABLE_CACHE:
                    try:
                        validate_table_name(t_name)
                        with db_engine.begin() as conn:
                            conn.execute(text(f"CREATE TABLE IF NOT EXISTS `{t_name}` LIKE `log_index_template`"))
                        LOCAL_TABLE_CACHE.add(cache_key)
                        logger.info(f"[{actual_bind_key}] Created new table: {t_name}")
                    except Exception as e:
                        logger.error(f"[{actual_bind_key}] Failed to create table {t_name}: {e}")
                        continue

        item['last_scan_id'] = scan_id
        table_groups.setdefault(t_name, []).append(item)

        tree_key = (item.get('server_name'), item.get('share_name'), item.get('pn'))
        if all(tree_key):
            try:
                tree_updates[tree_key] = max(tree_updates.get(tree_key, 0), int(year))
            except ValueError:
                logger.warning(f"[{actual_bind_key}] Invalid year in tree_updates: {year}")

    for attempt in range(3):
        try:
            with db_engine.begin() as conn:
                for t_name, group in table_groups.items():
                    try:
                        validate_table_name(t_name)
                    except ValueError as e:
                        logger.error(f"[{actual_bind_key}] Invalid table name in insert: {e}")
                        continue

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

            logger.info(f"[{time.strftime('%H:%M:%S')}] [{actual_bind_key}] Successfully inserted {len(items_list)} items.")
            return
        except Exception as e:
            if ("1213" in str(e) or "1205" in str(e)) and attempt < 2:
                logger.warning(f"[*] [{actual_bind_key}] Deadlock detected, retrying (attempt {attempt + 1}/3)...")
                time.sleep(0.5 * (attempt + 1))
                continue
            logger.error(f"DB Error on [{actual_bind_key}]: {e}", exc_info=True)
            raise e

def start_worker():
    global LOCAL_TABLE_CACHE
    with flask_app.app_context():
        LISTEN_QUEUES = [f"log_upload_queue:{p_key}" for p_key in PROJECT_MAP.keys()]
        for _, bind_key in PROJECT_MAP.items():
            try:
                engine = db.engines[bind_key]
                with engine.begin() as conn:
                    rows = conn.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
                    for r_item in rows:
                        LOCAL_TABLE_CACHE.add(f"{bind_key}:{r_item[0]}")
            except Exception as e:
                logger.warning(f"Warning: Cannot cache tables for bind [{bind_key}]: {e}")

        redis_client = get_redis_client()
        logger.info(f"[*] Redis Worker Started.")
        logger.info(f"[*] Listening Queues: {LISTEN_QUEUES}")
        logger.info(f"[*] Cached tables count: {len(LOCAL_TABLE_CACHE)}")

        while True:
            try:
                res = redis_client.brpop(LISTEN_QUEUES, timeout=5)
                if not res:
                    continue

                active_queue = res[0].decode('utf-8')
                try:
                    raw_data = json.loads(res[1])
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse task JSON from queue {active_queue}: {e}")
                    continue
                project_key = raw_data.get('project_key', 'log_system').strip().lower()
                pause_key = f"{project_key}_pause"
                retry_count = 0
                max_retries = 30
                while redis_client.get(pause_key) == b"1" and retry_count < max_retries:
                    logger.debug(f"System paused for {project_key}, waiting... ({retry_count}/{max_retries})")
                    time.sleep(1)
                    retry_count += 1

                if retry_count >= max_retries:
                    logger.error(f"Pause timeout for {project_key}, returning task to queue")
                    redis_client.lpush(active_queue, res[1])
                    continue

                if raw_data.get('type') == 'cleanup_task':
                    redis_client.setex(pause_key, 3600, "1")
                    try:
                        handle_cleanup_task(raw_data)
                    except Exception as e:
                        logger.error(f"Cleanup task failed: {e}", exc_info=True)
                    finally:
                        redis_client.delete(pause_key)
                else:
                    try:
                        process_batch_insert(raw_data)
                    except Exception as e:
                        logger.error(f"Batch insert failed: {e}", exc_info=True)
                        retries = raw_data.get('retries', 0) + 1
                        if retries <= 3:
                            raw_data['retries'] = retries
                            redis_client.lpush(active_queue, json.dumps(raw_data))
                            logger.warning(f"Task retried {retries}/3 and returned to queue.")
                        else:
                            redis_client.lpush(f"{active_queue}:dead_letter", json.dumps(raw_data))
                            logger.critical(f"Task exceeded max retries! Moved to dead letter queue. Data: {raw_data}")
            except Exception as e:
                logger.error(f"--- [Worker Global Error]: {str(e)}", exc_info=True)
                time.sleep(1)

if __name__ == '__main__':
    try:
        start_worker()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error in worker: {e}", exc_info=True)
        raise