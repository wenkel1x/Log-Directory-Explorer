from flask import Blueprint, request, jsonify
import json
import redis
import os
import threading
import logging

from app.utils.utils import IP_MAP_PATH, get_env_variable
logger = logging.getLogger(__name__)
upload_bp = Blueprint('upload_bp', __name__)

REDIS_HOST = get_env_variable("REDIS_HOST",required=True)
REDIS_PORT = get_env_variable("REDIS_PORT",required=True)
r_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

file_lock = threading.Lock()

@upload_bp.route('/svc/report_ip', methods=['POST'])
def report_ip():
    data = request.json or {}
    project_key = data.get('project_key', 'log_system').strip().lower()
    items = data.get('items', [])

    server_name = data.get('server_name')
    if not server_name and items:
        server_name = items[0].get('server_name')

    if not server_name:
        # 警告级别：请求参数缺失
        logger.warning(f"IP report rejected: Missing server_name. Data received: {data}")
        return jsonify({"status": "error", "msg": "Missing required parameter: server_name"}), 400

    detected_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if detected_ip and ',' in detected_ip:
        detected_ip = detected_ip.split(',')[0].strip()

    manual_ip = data.get('ip')
    final_ip = manual_ip if (detected_ip == '127.0.0.1' and manual_ip) else detected_ip

    try:
        with file_lock:
            full_ip_map = {}
            if os.path.exists(IP_MAP_PATH):
                try:
                    with open(IP_MAP_PATH, 'r', encoding='utf-8') as f:
                        full_ip_map = json.load(f)
                except Exception as json_err:
                    # 运行异常但可恢复：读取 JSON 失败，记录警告并重置
                    logger.warning(f"Failed to load existing IP map from {IP_MAP_PATH}, reset to empty. Error: {json_err}")
                    full_ip_map = {}
     
            if project_key not in full_ip_map:
                full_ip_map[project_key] = {}
 
            current_project_map = full_ip_map[project_key]
            dirty = False

            existing_hosts_with_this_ip = [name for name, ip in current_project_map.items() if ip == final_ip]
            for old_name in existing_hosts_with_this_ip:
                if old_name != server_name:
                    del current_project_map[old_name]
                    logger.debug(f"Cleaned up old hostname binding: {old_name} -> {final_ip} under project {project_key}")
                    dirty = True

            if current_project_map.get(server_name) != final_ip:
                current_project_map[server_name] = final_ip
                dirty = True

            if dirty:
                full_ip_map[project_key] = current_project_map
                with open(IP_MAP_PATH, 'w', encoding='utf-8') as f:
                    json.dump(full_ip_map, f, indent=4, ensure_ascii=False)
                logger.debug(f"Successfully updated IP map file. Project: {project_key}, Server: {server_name}, IP: {final_ip}")
          
        return jsonify({
            "status": "success",
            "mode": "sync",
            "ip_recorded": final_ip,
            "updated": dirty,
            "project": project_key
        }), 200

    except Exception as e:
        logger.exception(f"Internal server error in report_ip for project {project_key}")
        return jsonify({"status": "error", "msg": "IP recording failed due to an internal server error."}), 500


@upload_bp.route('/svc/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data or 'items' not in data:
        logger.warning(f"Upload batch rejected: Invalid data format. Remote Addr: {request.remote_addr}")
        return jsonify({"status": "error", "msg": "Invalid data format"}), 400

    project_key = data.get('project_key', 'log_system').strip().lower()
    queue_name = f"log_upload_queue:{project_key}"

    try:
        payload = {
            "type": "data_batch",
            "project_key": project_key,
            "scan_id": data.get('scan_id', 0),
            "items": data.get('items', [])
        }

        r_client.rpush(queue_name, json.dumps(payload))
        
        # 调试/信息级别：批次成功入队
        logger.debug(f"Successfully queued batch data. Queue: {queue_name}, Scan ID: {data.get('scan_id')}, Items count: {len(data.get('items', []))}")
        return jsonify({"status": "success", "mode": "async", "target_queue": queue_name}), 200

    except Exception as e:
        logger.exception(f"Redis rpush failed for queue: {queue_name}")
        return jsonify({"status": "error", "msg": "Queue push failed due to backend storage issue."}), 500


@upload_bp.route('/svc/cleanup', methods=['POST'])
def cleanup_stale_data():
    data = request.json or {}
    s_name = data.get('server_name')
    sh_name = data.get('share_name')
    s_id = data.get('scan_id')

    if not all([s_name, sh_name, s_id]):
        logger.warning(f"Cleanup parameters missing. Received: {data}")
        return jsonify({"status": "error", "msg": "Missing params for cleanup"}), 400

    project_key = data.get('project_key', 'log_system').strip().lower()
    queue_name = f"log_upload_queue:{project_key}"

    try:
        cleanup_task = {
            "type": "cleanup_task",
            "project_key": project_key,
            "server_name": s_name,
            "share_name": sh_name,
            "scan_id": s_id
        }
        r_client.rpush(queue_name, json.dumps(cleanup_task))
        
        logger.info(f"Cleanup task dispatched to Redis. Queue: {queue_name}, Server: {s_name}, ScanID: {s_id}")
        return jsonify({"status": "success", "msg": f"Cleanup task queued for {queue_name}"}), 200
        
    except Exception as e:
        logger.exception(f"Failed to enqueue cleanup task for project: {project_key}")
        return jsonify({"status": "error", "msg": "Cleanup task queuing failed."}), 500