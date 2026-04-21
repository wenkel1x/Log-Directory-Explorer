from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import json
import threading
from sqlalchemy import text
from app import db
from app.utils.utils import load_ip_map, IP_MAP_PATH
import time


upload_bp = Blueprint('upload_bp', __name__)

# 使用锁确保缓存更新和表创建的线程安全
table_cache_lock = threading.Lock()
EXISTING_TABLE_CACHE = set()
#YEAR_RE = re.compile(r'(?:[_/-])(20\d{2})(?:[_/-]|$)')

@upload_bp.route('/svc/report_ip', methods=['POST'])
def report_ip():
    detected_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if detected_ip and ',' in detected_ip:
        detected_ip = detected_ip.split(',')[0].strip()

    data = request.json or {}
    server_name, manual_ip = data.get('server_name'), data.get('ip')

    if not server_name:
        return jsonify({"status": "error", "msg": "Missing server_name"}), 400

    # 确定最终使用的 IP
    final_ip = manual_ip if (detected_ip == '127.0.0.1' and manual_ip) else detected_ip

    ip_map = load_ip_map()
    dirty = False

    # 检查 IP 是否已被其他主机名占用,查找是否有其他 hostname 记录了当前的这个 IP
    existing_hosts_with_this_ip = [name for name, ip in ip_map.items() if ip == final_ip]

    for old_name in existing_hosts_with_this_ip:
        if old_name != server_name:
            # 如果 IP 相同但名字不同，删掉旧的名字记录
            del ip_map[old_name]
            dirty = True

    # --- 检查当前 hostname 的 IP 是否需要更新 ---
    if ip_map.get(server_name) != final_ip:
        ip_map[server_name] = final_ip
        dirty = True

    # 如果有任何变动，执行写入
    if dirty:
        with open(IP_MAP_PATH, 'w', encoding='utf-8') as f:
            json.dump(ip_map, f, indent=4, ensure_ascii=False)

    return jsonify({"status": "success", "ip": final_ip, "updated": dirty})

@upload_bp.route('/svc/upload_batch', methods=['POST'])
def upload_batch():
    global EXISTING_TABLE_CACHE
    data = request.json
    if not data:
        return jsonify({"status": "error", "msg": "No data received"}), 400

    # 兼容 Agent 传来的结构
    items_list = data.get('items', []) if isinstance(data, dict) else data
    scan_id = data.get('scan_id', 0) if isinstance(data, dict) else 0

    if not items_list:
        return jsonify({"status": "error", "msg": "Empty items list"}), 400

    #current_year = datetime.now().year
    try:
        # 初始化表名缓存
        if not EXISTING_TABLE_CACHE:
            with table_cache_lock:
                rows = db.session.execute(text("SHOW TABLES")).fetchall()
                EXISTING_TABLE_CACHE = {row[0] for row in rows}

        table_groups = {}
        for item in items_list:
            log_time_str = item.get('log_time', '')
            if len(log_time_str) < 4: continue
            reported_year_str = log_time_str[:4]
            '''
            try:
                reported_year_int = int(reported_year_str)
            except ValueError:
                print(f"--- [Wait] Invalid year format: {reported_year_str} from {log_time_str}",flush=True)
                continue
            # 如果年份不在 2010 ~ current_year 之间，则启动正则校准
            if not (2010 <= reported_year_int <= current_year):
                file_context = f"{item.get('file_name', '')}_{item.get('relative_path', '')}"
                match = YEAR_RE.search(file_context)
                if match:
                    extracted_year = match.group(1)
                    # 只有提取出的年份在合法范围内才替换
                    if 2010 <= int(extracted_year) <= current_year:
                        item['log_time'] = extracted_year + log_time_str[4:]
                        reported_year_str = extracted_year
                else:
                    print(f"--- [Miss] Regex failed to find year in: {file_context}",flush=True)
            '''
            table_name = f"log_index_{reported_year_str}"
            # 线程安全地检查并创建表
            if table_name not in EXISTING_TABLE_CACHE:
                with table_cache_lock:
                    if table_name not in EXISTING_TABLE_CACHE:
                        db.session.execute(text(f"CREATE TABLE IF NOT EXISTS `{table_name}` LIKE `log_index_template`"))
                        db.session.commit()
                        EXISTING_TABLE_CACHE.add(table_name)

            item['scan_id'] = scan_id
            table_groups.setdefault(table_name, []).append(item)

        # --- 批量执行 SQL ---
        for table_name, group_items in table_groups.items():
            # 插入或更新 log_index 表
            insert_stmt = text(f"""
                INSERT INTO `{table_name}`
                (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name, last_scan_id)
                VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name, :scan_id)
                ON DUPLICATE KEY UPDATE
                status=VALUES(status),
                stage=VALUES(stage),
                log_time=VALUES(log_time),
                last_scan_id=VALUES(last_scan_id)
            """)
            db.session.execute(insert_stmt, group_items)

            # 插入或更新层级树元数据表 log_tree_data
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
        return jsonify({"status": "success", "count": len(items_list)}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "msg": str(e)}), 500

@upload_bp.route('/svc/cleanup', methods=['POST'])
def cleanup_stale_data():
    """清理在本次 scan_id 扫描中消失的文件（仅全量模式调用）"""
    data = request.json or {}
    s_name = data.get('server_name')
    sh_name = data.get('share_name')
    s_id = data.get('scan_id')

    if not all([s_name, sh_name, s_id]) or s_id == 0:
        return jsonify({"status": "error", "msg": "Invalid identification params"}), 400

    print(f"\n{'='*60}")
    print(f"[CLEANUP START] Server: {s_name} | Share: {sh_name} | Target ScanID: {s_id}")
    print(f"{'='*60}")

    try:
        # 查找所有年份分表
        result = db.session.execute(text("SHOW TABLES LIKE 'log_index_%'")).fetchall()
        all_tables = [row[0] for row in result if row[0] != 'log_index_template']

        deleted_total = 0
        for table_name in all_tables:
            # 1. 先查一下这个表里有多少要删的，并随便打印 5 条看看路径对不对
            check_sql = text(f"""
                SELECT relative_path FROM `{table_name}`
                WHERE server_name = :s_name AND share_name = :sh_name AND last_scan_id != :s_id
            """)
            stale_rows = db.session.execute(check_sql, {"s_name": s_name, "sh_name": sh_name, "s_id": s_id}).fetchall()

            if not stale_rows:
                continue

            print(f"\n>>> Table [{table_name}] has {len(stale_rows)} stale records.")
            print(f"    Sample paths to be deleted:")
            for row in stale_rows[:5]: # 只打前 5 条
                print(f"      - {row[0]}")
            if len(stale_rows) > 5:
                print(f"      ... and {len(stale_rows)-5} more items.")

            # 2. 执行分批删除
            table_deleted_count = 0
            while True:
                del_sql = text(f"""
                    DELETE FROM `{table_name}`
                    WHERE server_name = :s_name
                      AND share_name = :sh_name
                      AND last_scan_id != :s_id
                    LIMIT 5000
                """)
                res = db.session.execute(del_sql, {"s_name": s_name, "sh_name": sh_name, "s_id": s_id})
                db.session.commit()

                count = res.rowcount
                table_deleted_count += count
                if count < 5000: break
                time.sleep(0.05)

            deleted_total += table_deleted_count
            print(f"    Successfully deleted {table_deleted_count} rows from {table_name}.")

        print(f"\n{'='*60}")
        print(f"[CLEANUP FINISHED] Total items removed from DB: {deleted_total}")
        print(f"{'='*60}\n")

        return jsonify({"status": "success", "deleted_count": deleted_total}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[CLEANUP ERROR] {str(e)}")
        return jsonify({"status": "error", "msg": str(e)}), 500

def get_server_stats():
    target_year = datetime.now().year
    # 动态表名：确保表名在数据库中确实存在
    table_name = f"log_index_{target_year}"
    now = datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    # 转换为字符串格式，确保与数据库格式匹配
    start_time = yesterday.strftime('%Y-%m-%d 00:00:00')
    sql = text(f"""
        SELECT
            server_name,
            stage,
            MAX(log_time) as last_update,
            SUM(CASE WHEN DATE(log_time) = :today THEN 1 ELSE 0 END) as today_count,
            SUM(CASE WHEN DATE(log_time) = :yesterday THEN 1 ELSE 0 END) as yesterday_count
        FROM `{table_name}`
        WHERE log_time >= :start
        GROUP BY server_name, stage
        ORDER BY server_name ASC, last_update DESC
    """)
    try:
        # execute 返回的结果对象
        result_proxy = db.session.execute(sql, {
            "today": today,
            "yesterday": yesterday,
            "start": start_time
        })
        stats = []
        # 使用 .mappings() 这样可以用 row['server_name'] 访问，更稳健
        for row in result_proxy.mappings():
            last_dt = row['last_update']
            # 格式化时间显示逻辑
            if isinstance(last_dt, datetime):
                # 如果是今天，只显示时间；如果跨天，显示月-日 时间
                if last_dt.date() == today:
                    display_time = last_dt.strftime('%H:%M:%S')
                else:
                    display_time = last_dt.strftime('%m-%d %H:%M')
            else:
                display_time = str(last_dt) if last_dt else "N/A"
            stats.append({
                'server': str(row['server_name']).upper(),
                'stage': row['stage'] or 'N/A',
                'last_time': display_time,
                'today_count': int(row['today_count'] or 0),
                'yesterday_count': int(row['yesterday_count'] or 0),
                'status': 'Active'
            })
        return stats
    except Exception as e:
        # 这里建议使用 logging 模块，生产环境更易追溯
        print(f"Stats Error on table {table_name}: {e}")
        return []