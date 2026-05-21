from flask import Blueprint, request, jsonify, render_template, send_file, url_for
from datetime import datetime
import os
import zipfile
import io
from sqlalchemy import text
from app.utils.utils import load_ip_map, clean_cache, CACHE_DIR, get_target_year
from app.utils.db_selector import get_tenant_engine
from app.utils.smb_pool import smb_pool

search_bp = Blueprint('search_bp', __name__)

# 统一收拢原本硬编码的 /bft/search
@search_bp.route('/search')
def index():
    ip_data = load_ip_map()
    return render_template('search.html', ip_map=ip_data)

@search_bp.route('/api/get_years', methods=['GET'])
def get_years():
    # 抓取钥匙，动态切库
    project_key = request.args.get('project_key')
    try:
        engine = get_tenant_engine(project_key)

        # 使用目标数据库的连接执行 SHOW TABLES 动态发现年份表
        with engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES LIKE 'log_index_2%'")).fetchall()
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

@search_bp.route('/api/get_servers', methods=['GET'])
def get_servers():
    project_key = request.args.get('project_key')
    try:
        engine = get_tenant_engine(project_key)
        sql = text("SELECT DISTINCT server_name FROM log_tree_data ORDER BY server_name ASC")

        with engine.connect() as conn:
            rows = conn.execute(sql).fetchall()

        servers = [row[0] for row in rows if row[0]]
        return jsonify({"status": "success", "servers": servers})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@search_bp.route('/api/logs_server_side', methods=['POST'])
def logs_server_side():
    # 重点：DataTables 的 POST 请求通常通过 form 表单提交参数
    project_key = request.form.get('project_key')
    draw = request.form.get('draw', type=int)
    start = request.form.get('start', type=int, default=0)
    length = request.form.get('length', type=int, default=50)

    # 提取搜索参数
    filters = {
        "pn": request.form.get('s_pn'),
        "sn": request.form.get('s_sn'),
        "machine": request.form.get('s_machine'),
        "status": request.form.get('s_status'),
        "stage": request.form.get('s_stage')
    }
    s_year = request.form.get('s_year')

    # 判断是否为无条件的首页加载
    is_blank_search = not any(filters.values())

    try:
        # 动态切库安全验证
        engine = get_tenant_engine(project_key)

        # 确定目标表年份
        target_years = [datetime.now().year]
        if s_year and s_year.isdigit():
            target_years = [int(s_year)]

        subqueries = []
        sql_params = {**filters, "start": start, "length": length}
        if filters["sn"]: sql_params["sn"] = f"{filters['sn']}%"

        with engine.connect() as conn:
            for y in target_years:
                table_name = f"log_index_{y}"
                # 检查对应的年份分表在当前的租户库中是否存在
                check = conn.execute(text(f"SHOW TABLES LIKE '{table_name}'")).fetchone()
                if not check: continue

                where = ["1=1"]
                if filters["pn"]:      where.append("pn = :pn")
                if filters["sn"]:      where.append("sn LIKE :sn")
                if filters["machine"]: where.append("server_name = :machine")
                if filters["status"]:  where.append("status = :status")
                if filters["stage"]:   where.append("stage = :stage")

                sub_limit = ""
                if is_blank_search:
                    sub_limit = "LIMIT 5000"

                subqueries.append(f"SELECT log_time, server_name, sn, pn, status, stage, relative_path FROM `{table_name}` WHERE {' AND '.join(where)} ORDER BY log_time DESC {sub_limit}")

            if not subqueries:
                return jsonify({"draw": draw, "recordsTotal": 0, "recordsFiltered": 0, "data": []})

            union_sql = " UNION ALL ".join(subqueries)

            # 优化计数：如果是初始化，直接返回一个假的总数
            if is_blank_search:
                records_filtered = 5000
            else:
                count_sql = text(f"SELECT COUNT(*) FROM ({union_sql}) as total")
                records_filtered = conn.execute(count_sql, sql_params).scalar()

            # 分页查询
            final_sql = text(f"SELECT * FROM ({union_sql}) as combined ORDER BY log_time DESC LIMIT :start, :length")
            logs = conn.execute(final_sql, sql_params).fetchall()

        data = [{
            "log_time": l[0].strftime('%Y-%m-%d %H:%M:%S') if l[0] else "",
            "server": l[1], "sn": l[2], "pn": l[3], "status": l[4], "stage": l[5], "path": l[6]
        } for l in logs]

        return jsonify({
            "draw": draw,
            "recordsTotal": records_filtered,
            "recordsFiltered": records_filtered,
            "data": data
        })
    except Exception as e:
        return jsonify({"draw": draw, "error": str(e), "data": []})

@search_bp.route('/download/<server_name>/<path:rel_path>')
def download_log(server_name, rel_path):
    project_key = request.args.get('project_key')
    get_tenant_engine(project_key)

    clean_cache()
    ip = load_ip_map().get(server_name)
    if not ip:
        return "IP not found", 404
    try:
        local_file, filename = smb_pool.get_local_cache(server_name, rel_path, ip, CACHE_DIR)
        return send_file(local_file, as_attachment=True, download_name=filename)
    except Exception as e:
        return f"SMB Download Failed: {str(e)}", 500

@search_bp.route('/api/batch_download', methods=['POST'])
def batch_download():
    data = request.json or {}
    project_key = data.get('project_key')
    get_tenant_engine(project_key)

    clean_cache()
    files_to_pack = data.get('files', [])
    if not files_to_pack:
        return jsonify({"error": "No files selected"}), 400

    memory_output = io.BytesIO()
    ip_map = load_ip_map()

    with zipfile.ZipFile(memory_output, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in files_to_pack:
            srv, rel_path = item.get('server'), item.get('path')
            ip = ip_map.get(srv)
            if not ip:
                continue
            try:
                local_file, filename = smb_pool.get_local_cache(srv, rel_path, ip, CACHE_DIR)
                if os.path.exists(local_file):
                    zf.write(local_file, arcname=f"{srv}_{filename}")
            except Exception:
                continue

    memory_output.seek(0)
    time_str = datetime.now().strftime('%Y%m%d%H%M')
    return send_file(
        memory_output,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"batch_logs_{time_str}.zip"
    )