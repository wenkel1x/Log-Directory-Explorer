from flask import Blueprint, request, jsonify, render_template, send_file, url_for
from datetime import datetime
import os
import subprocess
import zipfile
import io
from sqlalchemy import text
from app import db
from app.utils.utils import load_ip_map, clean_cache, CACHE_DIR, get_target_year

search_bp = Blueprint('search_bp', __name__)

@search_bp.route('/bft/search')
def index():
    ip_data = load_ip_map()
    return render_template('search.html', ip_map=ip_data)

@search_bp.route('/api/get_years', methods=['GET'])
def get_years():
    try:
        result = db.session.execute(text("SHOW TABLES LIKE 'log_index_2%'")).fetchall()
        years = []
        for row in result:
            table_name = row[0]
            if table_name == 'log_index_template': continue
            parts = table_name.split('_')
            if len(parts) >= 3 and parts[-1].isdigit():
                years.append(parts[-1])
        years = sorted(list(set(years)), reverse=True)
        if not years: years = [get_target_year()]
        return jsonify({"status": "success", "years": years})
    except Exception as e:
        return jsonify({"status": "success", "years": [get_target_year()]})

@search_bp.route('/api/logs_server_side', methods=['POST'])
def logs_server_side():
    draw = request.form.get('draw', type=int)
    start = request.form.get('start', type=int, default=0)
    length = request.form.get('length', type=int, default=50)
    s_year, s_pn, s_sn, s_machine = request.form.get('s_year'), request.form.get('s_pn'), request.form.get('s_sn'), request.form.get('s_machine')

    try:
        if s_year and s_year != 'all':
            target_years = [int(s_year)]
        elif s_pn:
            year_sql = text("SELECT DISTINCT last_active_year FROM log_tree_data WHERE pn = :pn")
            year_rows = db.session.execute(year_sql, {"pn": s_pn}).fetchall()
            target_years = [row[0] for row in year_rows if row[0] > 0]
            if not target_years: target_years = [datetime.now().year]
        else:
            target_years = [datetime.now().year]

        subqueries = []
        params = {"pn": s_pn, "sn": f"{s_sn}%" if s_sn else None, "machine": s_machine}
        for y in target_years:
            table_name = f"log_index_{y}"
            check = db.session.execute(text(f"SHOW TABLES LIKE '{table_name}'")).fetchone()
            if not check: continue
            where = ["1=1"]
            if s_pn: where.append("pn = :pn")
            if s_sn: where.append("sn LIKE :sn")
            if s_machine: where.append("server_name = :machine")
            subqueries.append(f"SELECT * FROM `{table_name}` WHERE {' AND '.join(where)}")

        if not subqueries:
            return jsonify({"draw": draw, "recordsTotal": 0, "recordsFiltered": 0, "data": []})

        union_sql = " UNION ALL ".join(subqueries)
        final_sql = text(f"SELECT log_time, server_name, sn, pn, status, stage, relative_path FROM ({union_sql}) as combined ORDER BY log_time DESC LIMIT :start, :length")
        params.update({"start": start, "length": length})
        logs = db.session.execute(final_sql, params).fetchall()

        count_sql = text(f"SELECT COUNT(*) FROM ({union_sql}) as total")
        records_filtered = db.session.execute(count_sql, params).scalar()

        data = [{"log_time": l[0].strftime('%Y-%m-%d %H:%M:%S') if l[0] else "", "server": l[1], "sn": l[2], "pn": l[3], "status": l[4], "stage": l[5], "path": l[6]} for l in logs]
        return jsonify({"draw": draw, "recordsTotal": records_filtered, "recordsFiltered": records_filtered, "data": data})
    except Exception as e:
        return jsonify({"draw": draw, "error": str(e), "data": []})

@search_bp.route('/download/<server_name>/<path:rel_path>')
def download_log(server_name, rel_path):
    clean_cache()
    ip_map = load_ip_map()
    ip = ip_map.get(server_name)
    if not ip: return "IP not found", 404
    safe_filename = rel_path.replace('/', '_').replace('\\', '_')
    local_file = os.path.join(CACHE_DIR, f"{server_name}_{safe_filename}")
    if not os.path.exists(local_file):
        smb_url = f"smb://{ip}/{rel_path.lstrip('/')}"
        try:
            subprocess.run(['smbget', '-a', '-n', smb_url, '-o', local_file], timeout=20, check=True)
        except: return "SMB Download Failed", 500
    return send_file(local_file, as_attachment=True, download_name=os.path.basename(rel_path))

@search_bp.route('/api/batch_download', methods=['POST'])
def batch_download():
    clean_cache()
    data = request.json
    files_to_pack = data.get('files', [])
    if not files_to_pack: return jsonify({"error": "No files selected"}), 400
    memory_output = io.BytesIO()
    with zipfile.ZipFile(memory_output, 'w', zipfile.ZIP_DEFLATED) as zf:
        ip_map = load_ip_map()
        for item in files_to_pack:
            srv, rel_path = item.get('server'), item.get('path')
            ip = ip_map.get(srv)
            if not ip: continue
            safe_name = rel_path.replace('/', '_').replace('\\', '_')
            local_file = os.path.join(CACHE_DIR, f"{srv}_{safe_name}")
            #local_file = os.path.join(CACHE_DIR, f"{srv}_{rel_path.replace('/', '_').replace('\\', '_')}")
            if not os.path.exists(local_file):
                try: subprocess.run(['smbget', '-a', '-n', f"smb://{ip}/{rel_path.lstrip('/')}", '-o', local_file], timeout=15)
                except: continue
            if os.path.exists(local_file):
                zf.write(local_file, arcname=f"{srv}_{os.path.basename(rel_path)}")
    memory_output.seek(0)
    return send_file(memory_output, mimetype='application/zip', as_attachment=True, download_name=f"batch_logs_{datetime.now().strftime('%Y%m%d%H%M')}.zip")