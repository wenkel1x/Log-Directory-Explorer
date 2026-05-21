from flask import Blueprint, request, jsonify
import json
import redis

upload_bp = Blueprint('upload_bp', __name__)

r_client = redis.Redis(host='127.0.0.1', port=6379, db=0)

REDIS_KEY_LOG_QUEUE = "log_upload_queue"

@upload_bp.route('/svc/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data or 'items' not in data:
        return jsonify({"status": "error", "msg": "Invalid data format"}), 400
    try:
        project_key = data.get('project_key', 'log_system').strip().lower()
        payload = {
            "type": "data_batch",
            "project_key": project_key,
            "scan_id": data.get('scan_id', 0),
            "items": data.get('items', [])
        }
        r_client.rpush(REDIS_KEY_LOG_QUEUE, json.dumps(payload))
        return jsonify({"status": "success", "mode": "async", "project_detected": project_key}), 200
    except Exception as e:
        return jsonify({"status": "error", "msg": f"Queue push failed: {str(e)}"}), 500

@upload_bp.route('/svc/cleanup', methods=['POST'])
def cleanup_stale_data():
    data = request.json or {}
    s_name = data.get('server_name')
    sh_name = data.get('share_name')
    s_id = data.get('scan_id')

    if not all([s_name, sh_name, s_id]):
        return jsonify({"status": "error", "msg": "Missing params for cleanup"}), 400
    try:
        project_key = data.get('project_key', 'log_system').strip().lower()
        cleanup_task = {
            "type": "cleanup_task",
            "project_key": project_key,
            "server_name": s_name,
            "share_name": sh_name,
            "scan_id": s_id
        }
        r_client.rpush(REDIS_KEY_LOG_QUEUE, json.dumps(cleanup_task))
        return jsonify({"status": "success", "msg": f"Cleanup task queued for {project_key}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "msg": f"Queue push failed: {str(e)}"}), 500