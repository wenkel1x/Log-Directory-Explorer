from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import json
import threading
from sqlalchemy import text
from app import db
from app.utils.utils import load_ip_map, IP_MAP_PATH
import time
import redis

upload_bp = Blueprint('upload_bp', __name__)

# 使用锁确保缓存更新和表创建的线程安全
table_cache_lock = threading.Lock()
EXISTING_TABLE_CACHE = set()
r_client = redis.Redis(host='127.0.0.1', port=6379, db=0)

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
    data = request.json
    if not data or 'items' not in data:
        return jsonify({"status": "error", "msg": "Invalid data format"}), 400
    try:
        payload = {
            "type": "data_batch",
            "scan_id": data.get('scan_id', 0),
            "items": data.get('items', [])
        }
        r_client.rpush("log_upload_queue", json.dumps(payload))
        return jsonify({"status": "success", "mode": "async"}), 200
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500

@upload_bp.route('/svc/cleanup', methods=['POST'])
def cleanup_stale_data():
    data = request.json or {}
    s_name = data.get('server_name')
    sh_name = data.get('share_name')
    s_id = data.get('scan_id')

    if not all([s_name, sh_name, s_id]):
        return jsonify({"status": "error", "msg": "Missing params for cleanup"}), 400
    try:
        cleanup_task = {
            "type": "cleanup_task",
            "server_name": s_name,
            "share_name": sh_name,
            "scan_id": s_id
        }
        r_client.rpush("log_upload_queue", json.dumps(cleanup_task))
        return jsonify({"status": "success", "msg": "Cleanup task queued"}), 200
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500
def get_server_stats():
    target_year = datetime.now().year
    table_name = f"log_index_{target_year}"
    now = datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    start_time = yesterday.strftime('%Y-%m-%d 00:00:00')

    # SQL 仅获取原始聚合数据
    sql = text(f"""
        SELECT
            server_name,
            stage,
            SUM(CASE WHEN DATE(log_time) = :today THEN 1 ELSE 0 END) as t_count,
            SUM(CASE WHEN DATE(log_time) = :yesterday THEN 1 ELSE 0 END) as y_count,
            MAX(log_time) as last_up
        FROM `{table_name}`
        WHERE log_time >= :start
        GROUP BY server_name, stage
        ORDER BY last_up DESC
    """)

    try:
        result_proxy = db.session.execute(sql, {"today": today, "yesterday": yesterday, "start": start_time})

        # 在 Python 中进行二次聚合
        server_map = {}
        for row in result_proxy.mappings():
            sn = str(row['server_name']).upper()
            stg = row['stage'] or 'UNKNOWN'

            if sn not in server_map:
                server_map[sn] = {
                    'server': sn,
                    'stages': [],
                    'today_count': 0,
                    'yesterday_count': 0,
                    'last_dt': None,
                    'details': {'today': {}, 'yesterday': {}}
                }

            # 累加总数
            server_map[sn]['today_count'] += int(row['t_count'] or 0)
            server_map[sn]['yesterday_count'] += int(row['y_count'] or 0)

            # 记录各站阶明细
            if row['t_count'] > 0:
                server_map[sn]['details']['today'][stg] = int(row['t_count'])
            if row['y_count'] > 0:
                server_map[sn]['details']['yesterday'][stg] = int(row['y_count'])
            # 记录站阶列表用于显示 Badge
            if stg not in server_map[sn]['stages']:
                server_map[sn]['stages'].append(stg)
            # 比较最后更新时间
            curr_last = row['last_up']
            if curr_last and (not server_map[sn]['last_dt'] or curr_last > server_map[sn]['last_dt']):
                server_map[sn]['last_dt'] = curr_last

        # 格式化输出列表
        stats = []
        for sn in sorted(server_map.keys()):
            item = server_map[sn]
            # 时间格式化
            ldt = item['last_dt']
            display_time = ldt.strftime('%H:%M:%S') if ldt and ldt.date() == today else (ldt.strftime('%m-%d %H:%M') if ldt else "N/A")

            stats.append({
                'server': sn,
                'stage': "|".join(item['stages']),
                'last_time': display_time,
                'today_count': item['today_count'],
                'yesterday_count': item['yesterday_count'],
                'details': item['details'],
                'status': 'Active'
            })
        return stats
    except Exception as e:
        print(f"Error: {e}")
        return []