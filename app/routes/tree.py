from flask import Blueprint, request, jsonify, render_template, url_for
from sqlalchemy import text
import calendar
import os
import subprocess
from app import db
from app.utils.utils import load_ip_map, get_target_year, CACHE_DIR

tree_bp = Blueprint('tree_bp', __name__)

@tree_bp.route('/bft/explorer')
def tree_view():
    return render_template('tree.html')

@tree_bp.route('/api/get_tree_base')
def get_tree_base():
    try:
        sql = text("SELECT DISTINCT server_name, share_name FROM log_tree_data")
        results = db.session.execute(sql).fetchall()
        tree = {}
        for srv, shr in results:
            if srv not in tree: tree[srv] = []
            if shr: tree[srv].append(shr)
        return jsonify(tree)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@tree_bp.route('/api/get_pns')
def get_pns():
    srv, shr = request.args.get('server'), request.args.get('share')
    year = get_target_year()
    sql = text(f"""
        SELECT t.pn,
               (SELECT 1 FROM log_index_{year} l WHERE l.pn = t.pn LIMIT 1) as has_data
        FROM log_tree_data t
        WHERE t.server_name = :s AND t.share_name = :sh
        ORDER BY has_data DESC, t.last_active_year DESC, t.pn ASC
    """)
    try:
        results = db.session.execute(sql, {"s": srv, "sh": shr}).fetchall()
        return jsonify([{"name": row[0], "has_data": bool(row[1])} for row in results])
    except Exception as e: return jsonify({"error": str(e)}), 500

@tree_bp.route('/api/get_months')
def get_months():
    pn = request.args.get('pn')
    year = request.args.get('year', default=get_target_year(), type=int)
    sql = text(f"SELECT DISTINCT MONTH(log_time) as mon FROM log_index_{year} WHERE pn=:p ORDER BY mon DESC")
    try:
        res = db.session.execute(sql, {"p": pn}).fetchall()
        return jsonify([{"num": row[0], "name": calendar.month_abbr[row[0]]} for row in res])
    except: return jsonify([])

@tree_bp.route('/api/get_month_logs')
def get_month_logs():
    pn, mon, year = request.args.get('pn'), request.args.get('month'), request.args.get('year', get_target_year())
    start_dt = f"{year}-{int(mon):02d}-01 00:00:00"
    sql = text(f"SELECT sn, pn, server_name, relative_path, log_time, status, stage "
               f"FROM log_index_{year} WHERE pn=:p AND log_time >= :start "
               f"AND log_time < DATE_ADD(:start, INTERVAL 1 MONTH) ORDER BY log_time DESC LIMIT 2000")
    try:
        res = db.session.execute(sql, {"p": pn, "start": start_dt}).fetchall()
        data = [{"sn": r[0], "pn": r[1], "server": r[2], "path": r[3], 
                 "download_url": url_for('search_bp.download_log', server_name=r[2], rel_path=r[3]), 
                 "last_time": r[4].strftime('%Y-%m-%d %H:%M:%S'), "status": r[5], "stage": r[6]} for r in res]
        return jsonify({"data": data})
    except Exception as e: return jsonify({"error": str(e)}), 500

@tree_bp.route('/api/preview_log')
def preview_log():
    server_name, rel_path = request.args.get('server'), request.args.get('path')
    ip = load_ip_map().get(server_name)
    if not ip: return jsonify({"error": "IP not found"}), 404
    safe_path = rel_path.replace('/', '_').replace('\\', '_')
    local_file = os.path.join(CACHE_DIR, f"{server_name}_{safe_path}")
    if not os.path.exists(local_file):
        try: subprocess.run(['smbget', '-a', '-n', f"smb://{ip}/{rel_path.lstrip('/')}", '-o', local_file], timeout=15, check=True)
        except: return jsonify({"error": "SMB failed"}), 500
    try:
        with open(local_file, 'r', encoding='utf-8', errors='ignore') as f:
            return jsonify({"content": f.read(), "filename": os.path.basename(rel_path)})
    except Exception as e: return jsonify({"error": str(e)}), 500