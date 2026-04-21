from flask import Blueprint, request, jsonify, render_template, send_file, url_for
from datetime import datetime
import os
import subprocess
import json
from sqlalchemy import func, text
import zipfile
import io
import time
import calendar

# 统一从包内导入
from . import db
from .models import get_log_model
from .utils import load_ip_map, clean_cache, CACHE_DIR, IP_MAP_PATH, get_target_year

log_bp = Blueprint('log_bp', __name__)
EXISTING_TABLE_CACHE = set()


@log_bp.route('/search')
def index():
    ip_data = load_ip_map()
    year = get_target_year()
    return render_template('search.html', ip_map=ip_data)

@log_bp.route('/')
def tree_view():
    return render_template('tree.html')

#search function view
@log_bp.route('/api/get_years', methods=['GET'])
def get_years():
    from . import db
    try:
        # 1. 执行原生 SQL 获取所有 log_index_ 开头的表
        # 使用 text() 确保 SQLAlchemy 2.0 兼容性
        result = db.session.execute(text("SHOW TABLES LIKE 'log_index_2%'")).fetchall()
        
        years = []
        for row in result:
            table_name = row[0]
            # 排除模板表和非年份表
            if table_name == 'log_index_template':
                continue
            
            # 提取年份：假设格式为 log_index_2026
            parts = table_name.split('_')
            if len(parts) >= 3:
                year_str = parts[-1]
                if year_str.isdigit():
                    years.append(year_str)
        
        # 2. 去重并倒序排列 (最新的年份在最上面)
        years = sorted(list(set(years)), reverse=True)
        
        # 3. 如果数据库是空的，至少返回今年
        if not years:
            years = [get_target_year()]
            
        return jsonify({"status": "success", "years": years})
        
    except Exception as e:
        print(f"Get Years Error: {e}")
        # 发生错误时的保底方案
        return jsonify({"status": "success", "years": [get_target_year()]})

@log_bp.route('/api/report_ip', methods=['POST'])
def report_ip():
    detected_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if detected_ip and ',' in detected_ip:
        detected_ip = detected_ip.split(',')[0].strip()

    data = request.json or {}
    server_name = data.get('server_name')
    manual_ip = data.get('ip')

    if not server_name:
        return jsonify({"status": "error", "msg": "Missing server_name"}), 400

    final_ip = detected_ip
    if detected_ip == '127.0.0.1' and manual_ip:
        final_ip = manual_ip

    ip_map = load_ip_map()
    if ip_map.get(server_name) != final_ip:
        ip_map[server_name] = final_ip
        with open(IP_MAP_PATH, 'w', encoding='utf-8') as f:
            json.dump(ip_map, f, indent=4, ensure_ascii=False)
        return jsonify({"status": "success", "ip": final_ip})
            
    return jsonify({"status": "success", "ip": final_ip})

@log_bp.route('/api/logs_server_side', methods=['POST'])
def logs_server_side():
    draw = request.form.get('draw', type=int)
    start = request.form.get('start', type=int, default=0)
    length = request.form.get('length', type=int, default=50)
    
    s_year = request.form.get('s_year')
    s_pn = request.form.get('s_pn')
    s_sn = request.form.get('s_sn')
    s_machine = request.form.get('s_machine')

    try:
        # 1. 确定要查询的年份范围
        if s_year and s_year != 'all':
            target_years = [int(s_year)]
        elif s_pn:
            # 【核心优化】如果是 All Years 但搜特定 PN，先查它在哪些年份活跃
            year_sql = text("SELECT DISTINCT last_active_year FROM log_tree_data WHERE pn = :pn")
            year_rows = db.session.execute(year_sql, {"pn": s_pn}).fetchall()
            target_years = [row[0] for row in year_rows if row[0] > 0]
            # 如果没查到活跃年份，保底查今年
            if not target_years: target_years = [datetime.now().year]
        else:
            # 如果既没选年份也没搜 PN，默认只查今年（防止全表扫描导致卡死）
            target_years = [datetime.now().year]

        # 2. 构造 SQL 语句
        subqueries = []
        params = {"pn": s_pn, "sn": f"{s_sn}%" if s_sn else None, "machine": s_machine}

        for y in target_years:
            # 校验表是否存在 (防止删表导致的 crash)
            table_name = f"log_index_{y}"
            check = db.session.execute(text(f"SHOW TABLES LIKE '{table_name}'")).fetchone()
            if not check: continue
            where_clauses = ["1=1"]
            if s_pn: where_clauses.append("pn = :pn")
            if s_sn: where_clauses.append("sn LIKE :sn")
            if s_machine: where_clauses.append("server_name = :machine")
            subqueries.append(f"SELECT * FROM `{table_name}` WHERE {' AND '.join(where_clauses)}")
        if not subqueries:
            return jsonify({"draw": draw, "recordsTotal": 0, "recordsFiltered": 0, "data": []})

        union_sql = " UNION ALL ".join(subqueries)

        # 3. 分页查询记录内容
        final_sql = text(f"""
            SELECT log_time, server_name, sn, pn, status, stage, relative_path
            FROM ({union_sql}) as combined
            ORDER BY log_time DESC
            LIMIT :start, :length
        """)
        params.update({"start": start, "length": length})
        logs = db.session.execute(final_sql, params).fetchall()
        # 4. 获取总数 (只在有必要时执行)
        count_sql = text(f"SELECT COUNT(*) FROM ({union_sql}) as total")
        records_filtered = db.session.execute(count_sql, params).scalar()
        # 5. 格式化输出
        data = []
        for log in logs:
            data.append({
                "log_time": log[0].strftime('%Y-%m-%d %H:%M:%S') if log[0] else "",
                "server": log[1],
                "sn": log[2],
                "pn": log[3],
                "status": log[4],
                "stage": log[5],
                "path": log[6]
            })

        return jsonify({
            "draw": draw,
            "recordsTotal": records_filtered,
            "recordsFiltered": records_filtered,
            "data": data
        })

    except Exception as e:
        print(f"DEBUG ERROR: {e}")
        return jsonify({"draw": draw, "error": str(e), "data": []})

@log_bp.route('/download/<server_name>/<path:rel_path>')
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
        except:
            return "SMB Download Failed", 500
    return send_file(local_file, as_attachment=True, download_name=os.path.basename(rel_path))

# function upload data
@log_bp.route('/api/upload_batch', methods=['POST'])
def upload_batch():
    global EXISTING_TABLE_CACHE
    data = request.json
    if not data:
        return jsonify({"status": "error", "msg": "No data received"}), 400

    try:
        if not EXISTING_TABLE_CACHE:
            rows = db.session.execute(text("SHOW TABLES")).fetchall()
            EXISTING_TABLE_CACHE = {row[0] for row in rows}

        # 1. 预取表名（减少 SHOW TABLES 次数）
        existing_tables = EXISTING_TABLE_CACHE.copy()
        # 按表名对数据进行分组，实现真正的批量插入
        table_groups = {}
        unique_data_paths = set()

        for item in data:
            dt = datetime.strptime(item['log_time'], '%Y-%m-%d %H:%M:%S')
            table_name = f"log_index_{dt.year}"
            
            # 自动建表（这里保持现状，因为年份表不经常创建）
            if table_name not in existing_tables:
                db.session.execute(text(f"CREATE TABLE IF NOT EXISTS `{table_name}` LIKE `log_index_template`"))
                db.session.commit() # 建表必须立即提交，避免 DDL 锁
                existing_tables.add(table_name)
                EXISTING_TABLE_CACHE.add(table_name)

            # 分组收集数据
            if table_name not in table_groups:
                table_groups[table_name] = []
            table_groups[table_name].append(item)

            if item.get('server_name') and item.get('share_name') and item.get('pn'):
                unique_data_paths.add((item['server_name'], item['share_name'], item['pn']))

        # 2. 批量执行主表插入 (关键优化！)
        for table_name, items in table_groups.items():
            insert_stmt = text(f"""
                INSERT INTO `{table_name}` 
                (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name)
                VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name)
                ON DUPLICATE KEY UPDATE 
                status=VALUES(status), stage=VALUES(stage), log_time=VALUES(log_time)
            """)
            # 直接传入整个列表，SQLAlchemy 会自动处理成批量发送
            db.session.execute(insert_stmt, items)

        # 3. 批量更新元数据表 (精准匹配日志年份)
        if unique_data_paths:
            data_stmt = text("""
                INSERT INTO log_tree_data (server_name, share_name, pn, last_active_year)
                VALUES (:server_name, :share_name, :pn, :year)
                ON DUPLICATE KEY UPDATE 
                last_active_year = GREATEST(last_active_year, VALUES(last_active_year))
            """)
            # 从 table_groups 中提取真实的年份，而不是用 now()
            path_params = []
            for table_name, items in table_groups.items():
                # 从表名 log_index_2026 中提取 2026
                log_year = int(table_name.split('_')[-1])
                
                # 提取该年份涉及到的唯一路径
                year_paths = {(i['server_name'], i['share_name'], i['pn']) for i in items 
                             if i.get('server_name') and i.get('share_name') and i.get('pn')}

                for s, h, p in year_paths:
                    path_params.append({
                        "server_name": s, 
                        "share_name": h, 
                        "pn": p, 
                        "year": log_year
                    })

            if path_params:
                db.session.execute(data_stmt, path_params)
        db.session.commit()
        return jsonify({"status": "success", "count": len(data)}), 200

    except Exception as e:
        db.session.rollback()
        print(f"UPLOAD ERROR: {str(e)}")
        return jsonify({"status": "error", "msg": str(e)}), 500

# tree view function
# 1: 获取基础树 (Server 和 Share)
@log_bp.route('/api/get_tree_base')
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

# 2: 点击 Share 展开 PN
@log_bp.route('/api/get_pns')
def get_pns():
    srv = request.args.get('server')
    shr = request.args.get('share')
    year = get_target_year()
    sql = text(f"""
        SELECT t.pn, 
               (SELECT 1 FROM log_index_{year} l WHERE l.pn = t.pn LIMIT 1) as has_data
        FROM log_tree_data t
        WHERE t.server_name = :s 
          AND t.share_name = :sh 
        ORDER BY t.last_active_year DESC, t.pn ASC
    """)
    try:
        results = db.session.execute(sql, {"s": srv, "sh": shr}).fetchall()
        # 返回结构化数据：包含 PN 名称和是否有数据的布尔值
        pns_data = [{"name": row[0], "has_data": bool(row[1])} for row in results]
        return jsonify(pns_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@log_bp.route('/api/get_months')
def get_months():
    pn = request.args.get('pn')
    year = request.args.get('year', default=get_target_year(), type=int)
    sql = text(f"SELECT DISTINCT MONTH(log_time) as mon FROM log_index_{year} WHERE pn=:p ORDER BY mon DESC")
    try:
        res = db.session.execute(sql, {"p": pn}).fetchall()
        if not res:
            return jsonify([]) 
        return jsonify([{"num": row[0], "name": calendar.month_abbr[row[0]]} for row in res])
    except Exception as e:
        return jsonify([])

@log_bp.route('/api/get_month_logs')
def get_month_logs():
    pn = request.args.get('pn')
    mon = request.args.get('month')
    year = request.args.get('year', get_target_year())

    # 1. 构造该月的时间范围
    start_dt = f"{year}-{int(mon):02d}-01 00:00:00"
    
    # 2. 编写 SQL (合并原有的 sn_details 字段)
    # 使用范围查询以利用 (pn, log_time) 索引
    sql = text(f"""
        SELECT
            sn,
            pn,
            server_name,
            relative_path,
            log_time,
            status,
            stage
        FROM log_index_{year}
        WHERE pn=:p
        AND log_time >= :start
        AND log_time < DATE_ADD(:start, INTERVAL 1 MONTH)
        ORDER BY log_time DESC
        LIMIT 2000
    """)

    try:
        res = db.session.execute(sql, {"p": pn, "start": start_dt}).fetchall()

        data = []
        for row in res:
            sn_val, pn_val, srv, rel_path, ltime, status, stage = row
            # 生成下载 URL (保留你之前的 download_log 逻辑)
            download_url = url_for('log_bp.download_log', server_name=srv, rel_path=rel_path)
            data.append({
                "sn": sn_val,
                "pn": pn_val,
                "server": srv,
                "path": rel_path,
                "download_url": download_url,
                "last_time": ltime.strftime('%Y-%m-%d %H:%M:%S'),
                "status": status,
                "stage": stage
            })
        return jsonify({"data": data})

    except Exception as e:
        print(f"Error in get_month_logs: {e}")
        return jsonify({"error": str(e)}), 500

@log_bp.route('/api/preview_log')
def preview_log():
    server_name = request.args.get('server')
    rel_path = request.args.get('path')
    ip_map = load_ip_map()
    ip = ip_map.get(server_name)
    if not ip:
        return jsonify({"error": "IP not found for this server"}), 404

    # 1. 定义内存盘(tmpfs)路径
    # sudo mount -t tmpfs -o size=1G tmpfs /mnt/mysql/.server_api/static/cache
    safe_filename = rel_path.replace('/', '_').replace('\\', '_')
    local_file = os.path.join(CACHE_DIR, f"{server_name}_{safe_filename}")

    # 2. 如果内存盘没有缓存，静默下载
    if not os.path.exists(local_file):
        smb_url = f"smb://{ip}/{rel_path.lstrip('/')}"
        try:
            # 使用 smbget 抓取
            subprocess.run(['smbget', '-a', '-n', smb_url, '-o', local_file], timeout=15, check=True)
        except subprocess.CalledProcessError:
            return jsonify({"error": "SMB connection failed or file not found"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 3. 从内存中读取文本 (设置 errors='ignore' 防止二进制乱码导致崩溃)
    try:
        with open(local_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({
            "content": content,
            "filename": os.path.basename(rel_path)
        })
    except Exception as e:
        return jsonify({"error": f"Read error: {str(e)}"}), 500

@log_bp.route('/api/batch_download', methods=['POST'])
def batch_download():
    clean_cache()
    data = request.json
    files_to_pack = data.get('files', [])

    if not files_to_pack:
        return jsonify({"error": "No files selected"}), 400

    # 1. 在内存中创建一个二进制流对象
    memory_output = io.BytesIO()

    # 2. 创建 ZIP 文件对象，写入这个内存流
    with zipfile.ZipFile(memory_output, 'w', zipfile.ZIP_DEFLATED) as zf:
        ip_map = load_ip_map()
        for item in files_to_pack:
            srv = item.get('server')
            rel_path = item.get('path')
            ip = ip_map.get(srv)
            if not ip: continue
            # 确定缓存路径 (tmpfs)
            safe_filename = rel_path.replace('/', '_').replace('\\', '_')
            local_file = os.path.join(CACHE_DIR, f"{srv}_{safe_filename}")
            # 如果内存盘没缓存，先抓取
            if not os.path.exists(local_file):
                smb_url = f"smb://{ip}/{rel_path.lstrip('/')}"
                try:
                    subprocess.run(['smbget', '-a', '-n', smb_url, '-o', local_file], timeout=15)
                except:
                    continue # 抓取失败跳过此文件

            # 将文件写入压缩包 (从内存盘读取)
            if os.path.exists(local_file):
                # arcname 是文件在 ZIP 里的名字，不带长路径
                zf.write(local_file, arcname=f"{srv}_{os.path.basename(rel_path)}")
    # 3. 指针回到开头，准备读取发送
    memory_output.seek(0)
    return send_file(
        memory_output,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"batch_logs_{datetime.now().strftime('%Y%m%d%H%M')}.zip"
    )