import json
import redis
import time
from sqlalchemy import text
from app import create_ingestion_app, db 
from app.routes.upload import EXISTING_TABLE_CACHE, table_cache_lock

# 1. 初始化 Flask 环境
flask_app = create_ingestion_app()
r = redis.Redis(host='127.0.0.1', port=6379, db=0)

def start_worker():
    with flask_app.app_context():
        print("--- [Worker] Service is up and running. Listening for new messages... ---")
        while True:
            try:
                result = r.brpop("log_upload_queue", timeout=0)
                if not result: continue

                raw_data = result[1]
                data = json.loads(raw_data)

                items_list = data.get('items', []) if isinstance(data, dict) else data
                scan_id = data.get('scan_id', 0) if isinstance(data, dict) else 0
                if not items_list: continue
                global EXISTING_TABLE_CACHE
                if not EXISTING_TABLE_CACHE:
                    with table_cache_lock:
                        rows = db.session.execute(text("SHOW TABLES")).fetchall()
                        for row in rows: EXISTING_TABLE_CACHE.add(row[0])

                table_groups = {}
                for item in items_list:
                    log_time_str = item.get('log_time', '')
                    if len(log_time_str) < 4: continue
                    reported_year_str = log_time_str[:4]
                    table_name = f"log_index_{reported_year_str}"

                    if table_name not in EXISTING_TABLE_CACHE:
                        with table_cache_lock:
                            if table_name not in EXISTING_TABLE_CACHE:
                                db.session.execute(text(f"CREATE TABLE IF NOT EXISTS `{table_name}` LIKE `log_index_template`"))
                                db.session.commit()
                                EXISTING_TABLE_CACHE.add(table_name)
                    item['scan_id'] = scan_id
                    table_groups.setdefault(table_name, []).append(item)

                for table_name, group_items in table_groups.items():
                    # 批量插入 log_index
                    insert_stmt = text(f"""
                        INSERT INTO `{table_name}`
                        (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name, last_scan_id)
                        VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name, :scan_id)
                        ON DUPLICATE KEY UPDATE
                        status=VALUES(status), stage=VALUES(stage), log_time=VALUES(log_time), last_scan_id=VALUES(last_scan_id)
                    """)
                    db.session.execute(insert_stmt, group_items)

                    # 批量更新 log_tree_data
                    path_params = [
                        {"server_name": i['server_name'], "share_name": i['share_name'],
                         "pn": i['pn'], "year": int(table_name.split('_')[-1])}
                        for i in group_items if i.get('server_name') and i.get('share_name') and i.get('pn')
                    ]
                    if path_params:
                        db.session.execute(text("""
                            INSERT INTO log_tree_data (server_name, share_name, pn, last_active_year)
                            VALUES (:server_name, :share_name, :pn, :year)
                            ON DUPLICATE KEY UPDATE
                            last_active_year = GREATEST(last_active_year, VALUES(last_active_year))
                        """), path_params)

                db.session.commit()
                print(f"[{time.strftime('%H:%M:%S')}] Success Process {len(items_list)} data",flush=True)

            except Exception as e:
                db.session.rollback()
                print(f"--- [Error] Write Error: {str(e)}",flush=True)
                time.sleep(1)

if __name__ == '__main__':
    start_worker()