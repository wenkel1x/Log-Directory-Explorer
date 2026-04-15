from flask import Blueprint, request, jsonify, render_template, send_file, url_for
from datetime import datetime
import os
import subprocess
import json
from sqlalchemy import func, text
import zipfile
import io
import time

# 统一从包内导入
from . import db
from .models import get_log_model
from .utils import load_ip_map, clean_cache, CACHE_DIR, IP_MAP_PATH, get_target_year

log_bp = Blueprint('log_bp', __name__)



@log_bp.route('/search')
def index():
    ip_data = load_ip_map()
    year = datetime.now().year
    try:
        # 使用原生 SQL 快速统计
        res = db.session.execute(f"SELECT COUNT(DISTINCT server_name) FROM log_index_{year}")
        total_machines = res.scalar()
    except:
        total_machines = "N/A"
    return render_template('search.html', ip_map=ip_data, total_machines=total_machines)

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
            years = [str(datetime.now().year)]
            
        return jsonify({"status": "success", "years": years})
        
    except Exception as e:
        print(f"Get Years Error: {e}")
        # 发生错误时的保底方案
        return jsonify({"status": "success", "years": [str(datetime.now().year)]})

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
    
    # 强制处理 'all' 或空年份，默认显示今年
    s_year = request.form.get('s_year')
    target_year = s_year if (s_year and s_year != 'all') else datetime.now().year
    
    try:
        # 检查请求的表是否存在
        table_name = f"log_index_{target_year}"
        check = db.session.execute(text(f"SHOW TABLES LIKE '{table_name}'")).fetchone()
        if not check:
            return jsonify({"draw": draw, "recordsTotal": 0, "recordsFiltered": 0, "data": []})

        LogModel = get_log_model(target_year)
        query = LogModel.query
        
        # 过滤搜索条件
        s_machine = request.form.get('s_machine')
        if s_machine: query = query.filter(LogModel.server_name.like(f"%{s_machine}%"))
        s_sn = request.form.get('s_sn')
        if s_sn: query = query.filter(LogModel.sn.like(f"%{s_sn}%"))

        records_total = query.count()
        logs = query.order_by(LogModel.log_time.desc()).offset(start).limit(length).all()

        ip_map = load_ip_map()
        data = []
        for log in logs:
            ip = ip_map.get(log.server_name, '0.0.0.0')
            # 兼容旧版 Python 的 f-string 路径处理
            rel_path_fixed = log.relative_path.replace('/', '\\')
            win_path = f"\\\\{ip}\\{rel_path_fixed}"
            
            data.append({
                "log_time": log.log_time.strftime('%Y-%m-%d %H:%M:%S'),
                "server": log.server_name,  # 统一为 server
                "sn": log.sn,
                "pn": log.pn,
                "status": log.status,
                "stage": log.stage,
                "path": log.relative_path   # 统一为 path
            })

        return jsonify({"draw": draw, "recordsTotal": records_total, "recordsFiltered": records_total, "data": data})
    except Exception as e:
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
@log_bp.route('/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data:
        return jsonify({"status": "error", "msg": "No data received"}), 400

    try:
        # 1. 预先获取数据库中已有的表
        existing_tables = {row[0] for row in db.session.execute(text("SHOW TABLES")).fetchall()}

        # --- [新增] 用于存储本次批上传中涉及的唯一路径组合 ---
        unique_data_paths = set()

        for item in data:
            # 解析日志时间以确定年份表名
            dt = datetime.strptime(item['log_time'], '%Y-%m-%d %H:%M:%S')
            table_name = f"log_index_{dt.year}"

            # --- 自动建表逻辑 ---
            if table_name not in existing_tables:
                create_sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` LIKE `log_index_template`"
                db.session.execute(text(create_sql))
                db.session.commit()
                existing_tables.add(table_name)

            # --- 插入/更新主表数据 ---
            insert_stmt = text(f"""
                INSERT INTO `{table_name}` 
                (server_name, file_name, log_time, pn, sn, status, stage, relative_path, share_name)
                VALUES (:server_name, :file_name, :log_time, :pn, :sn, :status, :stage, :relative_path, :share_name)
                ON DUPLICATE KEY UPDATE 
                status=VALUES(status), stage=VALUES(stage), log_time=VALUES(log_time)
            """)
            db.session.execute(insert_stmt, item)

            # --- [新增] 收集结构元数据 ---
            # 只有当 server, share, pn 都不为空时才记录
            if item.get('server_name') and item.get('share_name') and item.get('pn'):
                unique_data_paths.add((item['server_name'], item['share_name'], item['pn']))

        # --- [新增] 异步同步到元数据小表 ---
        if unique_data_paths:
            # 使用 INSERT IGNORE：如果组合已存在（触发唯一索引），则自动跳过
            data_stmt = text("""
                INSERT IGNORE INTO log_tree_data (server_name, share_name, pn)
                VALUES (:server_name, :share_name, :pn)
            """)
            for srv, shr, pn in unique_data_paths:
                db.session.execute(data_stmt, {
                    "server_name": srv,
                    "share_name": shr,
                    "pn": pn
                })

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
    year = get_target_year()
    table = f"log_index_{year}"
    try:
        # 先检查表是否存在，防止 SQL 报错
        check = db.session.execute(text(f"SHOW TABLES LIKE '{table}'")).fetchone()
        if not check:
            return jsonify({}) # 返回空树

        sql = text(f"SELECT server_name, share_name FROM {table} GROUP BY server_name, share_name")
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
    sql = text("SELECT pn FROM log_tree_data WHERE server_name=:s AND share_name=:sh")
    all_pns = [row[0] for row in db.session.execute(sql, {"s": srv, "sh": shr}).fetchall()]
    sql_check = text(f"SELECT 1 FROM log_index_{year} WHERE pn=:p LIMIT 1")
    active_pns = []
    for p in all_pns:
        if db.session.execute(sql_check, {"p": p}).fetchone():
            active_pns.append(p)
    return jsonify(active_pns)

# 1. 获取月份 (PN -> Months)
@log_bp.route('/api/get_months')
def get_months():
    pn = request.args.get('pn')
    year = request.args.get('year', str(datetime.now().year))

    sql = text(f"""
        SELECT DISTINCT MONTH(log_time) as mon
        FROM log_index_{year}
        WHERE pn=:p
        AND log_time >= :start AND log_time <= :end
        ORDER BY mon DESC
    """)
    params = {
        "p": pn,
        "start": f"{year}-01-01 00:00:00",
        "end": f"{year}-12-31 23:59:59"
    }
    #sql = text(f"SELECT DISTINCT MONTH(log_time) FROM log_index_{year} WHERE pn=:p")
    res = db.session.execute(sql, params).fetchall()
    return jsonify([row[0] for row in res])

@log_bp.route('/api/get_month_logs')
def get_month_logs():
    pn = request.args.get('pn')
    mon = request.args.get('month')
    year = request.args.get('year', str(datetime.now().year))

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