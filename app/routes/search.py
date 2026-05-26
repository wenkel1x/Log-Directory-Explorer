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
    project_key = request.form.get('project_key')
    draw = request.form.get('draw', type=int)
    start = request.form.get('start', type=int, default=0)
    length = request.form.get('length', type=int, default=50)
    # 1. 接收基础过滤参数
    s_pn = request.form.get('s_pn')
    s_sn_raw = request.form.get('s_sn')
    s_machine = request.form.get('s_machine')
    s_status = request.form.get('s_status')
    s_stage = request.form.get('s_stage')
    s_year = request.form.get('s_year')

    sn_list = []
    if s_sn_raw:
        sn_list = [sn.strip() for sn in s_sn_raw.split(',') if sn.strip()]
    filters = {
        "pn": s_pn if s_pn else None,
        "machine": s_machine if s_machine else None,
        "status": s_status if s_status else None,
        "stage": s_stage if s_stage else None
    }
    is_blank_search = not any(filters.values()) and not sn_list
    missing_sns = []
    try:
        engine = get_tenant_engine(project_key)
        with engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES LIKE 'log_index_2%'")).fetchall()
            db_years = []
            for row in result:
                table_name = row[0]
                if table_name == 'log_index_template': continue
                parts = table_name.split('_')
                if len(parts) >= 3 and parts[-1].isdigit():
                    db_years.append(int(parts[-1]))
            db_years = sorted(db_years, reverse=True)
            if s_year and s_year.isdigit():
                target_years = [int(s_year)]
            else:
                if is_blank_search:
                    target_years = [db_years[0]] if db_years else [datetime.now().year]
                else:
                    target_years = db_years
            subqueries = []
            sql_params = {**{k: v for k, v in filters.items() if v is not None}, "start": start, "length": length}
            is_batch_mode = len(sn_list) > 1
            if len(sn_list) == 1:
                sql_params["sn_single"] = f"{sn_list[0]}%"
            elif is_batch_mode:
                for idx, sn_val in enumerate(sn_list):
                    sql_params[f"sn_b_{idx}"] = sn_val
            # 4. 构建 UNION ALL 子查询链
            for y in target_years:
                table_name = f"log_index_{y}"
                if y not in db_years: continue
                where = ["1=1"]
                if filters["pn"]:      where.append("pn = :pn")
                if filters["machine"]: where.append("server_name = :machine")
                if filters["status"]:  where.append("status = :status")
                if filters["stage"]:   where.append("stage = :stage")
                # 针对 SN 的形态追加不同的 WHERE 条件
                if len(sn_list) == 1:
                    where.append("sn LIKE :sn_single")
                elif is_batch_mode:
                    # 拼接类似 sn IN (:sn_b_0, :sn_b_1) 的结构
                    placeholders = ", ".join([f":sn_b_{i}" for i in range(len(sn_list))])
                    where.append(f"sn IN ({placeholders})")
                if is_blank_search:
                    sql_line = f"SELECT log_time, server_name, sn, pn, status, stage, relative_path FROM `{table_name}` WHERE {' AND '.join(where)} ORDER BY log_time DESC LIMIT 5000"
                else:
                    sql_line = f"SELECT log_time, server_name, sn, pn, status, stage, relative_path FROM `{table_name}` WHERE {' AND '.join(where)}"
                subqueries.append(sql_line)

            if not subqueries:
                return jsonify({"draw": draw, "recordsTotal": 0, "recordsFiltered": 0, "data": []})

            union_sql = " UNION ALL ".join(subqueries)
            # 5. 计算过滤后的数据总量
            if is_blank_search:
                records_filtered = 5000
            else:
                count_sql = text(f"SELECT COUNT(*) FROM ({union_sql}) as total")
                records_filtered = conn.execute(count_sql, sql_params).scalar()

            # 6. 执行分页查询获取表格行数据
            final_sql = text(f"SELECT * FROM ({union_sql}) as combined ORDER BY log_time DESC LIMIT :start, :length")
            logs = conn.execute(final_sql, sql_params).fetchall()

            # 7.差异比对
            if is_batch_mode and logs:
                check_subqueries = []
                for y in target_years:
                    table_name = f"log_index_{y}"
                    if y not in db_years: continue
                    placeholders = ", ".join([f":sn_b_{i}" for i in range(len(sn_list))])
                    check_subqueries.append(f"SELECT DISTINCT sn FROM `{table_name}` WHERE sn IN ({placeholders})")
                if check_subqueries:
                    check_union_sql = text(" UNION ".join(check_subqueries))
                    # 仅需要传递 sn_b_xx 的映射参数字典
                    check_params = {f"sn_b_{i}": sn_list[i] for i in range(len(sn_list))}
                    db_sns = conn.execute(check_union_sql, check_params).fetchall()
                    found_sns = set([row[0] for row in db_sns])
                    requested_sns = set(sn_list)
                    # 差集计算
                    missing_sns = list(requested_sns - found_sns)

        # 8. 格式化输出
        data = [{
            "log_time": l[0].strftime('%Y-%m-%d %H:%M:%S') if l[0] else "",
            "server": l[1], "sn": l[2], "pn": l[3], "status": l[4], "stage": l[5], "path": l[6]
        } for l in logs]

        return jsonify({
            "draw": draw,
            "recordsTotal": records_filtered,
            "recordsFiltered": records_filtered,
            "data": data,
            "missing_sns": missing_sns
        })

    except Exception as e:
        return jsonify({"draw": draw, "error": str(e), "data": []})

@search_bp.route('/api/download/<server_name>/<path:rel_path>')
def download_log(server_name, rel_path):
    project_key = request.args.get('project_key')
    get_tenant_engine(project_key)

    clean_cache()
    ip = load_ip_map().get(server_name)
    if not ip:
        return "IP not found", 404
    try:
        local_file, filename = smb_pool.get_local_cache(server_name, rel_path, ip, CACHE_DIR, project_key=project_key)
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
                local_file, filename = smb_pool.get_local_cache(srv, rel_path, ip, CACHE_DIR, project_key=project_key)
                if os.path.exists(local_file):
                    zf.write(local_file, arcname=f"{filename}")
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