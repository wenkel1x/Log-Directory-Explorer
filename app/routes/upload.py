from flask import Blueprint, request, jsonify
import json
import threading
from app.utils.utils import load_ip_map, IP_MAP_PATH
import redis

upload_bp = Blueprint('upload_bp', __name__)

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
    final_ip = manual_ip if (detected_ip == '127.0.0.1' and manual_ip) else detected_ip

    ip_map = load_ip_map()
    dirty = False
    existing_hosts_with_this_ip = [name for name, ip in ip_map.items() if ip == final_ip]

    for old_name in existing_hosts_with_this_ip:
        if old_name != server_name:
            del ip_map[old_name]
            dirty = True
    if ip_map.get(server_name) != final_ip:
        ip_map[server_name] = final_ip
        dirty = True
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