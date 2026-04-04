from flask import Flask, request, jsonify, render_template, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import insert
from sqlalchemy import func
import urllib.parse
from datetime import datetime
import os
import subprocess
import json
import zipfile

app = Flask(__name__)

# --- 1. 配置与基础路径 ---
raw_password = "P@ssw0rd"
safe_password = urllib.parse.quote_plus(raw_password)
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# --- 数据库连接池优化 ---
app.config['SQLALCHEMY_POOL_SIZE'] = 10
app.config['SQLALCHEMY_MAX_OVERFLOW'] = 20
app.config['SQLALCHEMY_POOL_RECYCLE'] = 1800
app.config['SQLALCHEMY_POOL_TIMEOUT'] = 30

db = SQLAlchemy(app)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
IP_MAP_PATH = os.path.join(BASE_DIR, 'ip_map.json')
CACHE_DIR = os.path.join(BASE_DIR, 'download_cache')
os.makedirs(CACHE_DIR, exist_ok=True)

# --- 2. 数据库模型定义 ---
class LogIndex(db.Model):
    __tablename__ = 'log_index'
    server_name = db.Column(db.String(50), primary_key=True)
    file_name = db.Column(db.String(150), primary_key=True)
    pn = db.Column(db.String(128))
    sn = db.Column(db.String(128))
    log_time = db.Column(db.DateTime)
    status = db.Column(db.String(20))
    stage = db.Column(db.String(50))
    relative_path = db.Column(db.String(512))
    share_name = db.Column(db.String(50))

# --- 3. 工具函数 ---
def load_ip_map():
    if not os.path.exists(IP_MAP_PATH): return {}
    try:
        with open(IP_MAP_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return {}

def clean_cache(max_size_mb=500, max_days=1):
    import time
    now = time.time()
    try:
        for f in os.listdir(CACHE_DIR):
            fp = os.path.join(CACHE_DIR, f)
            if os.path.getmtime(fp) < now - (max_days * 86400):
                os.remove(fp)
    except Exception as e:
        print(f"Cache cleaning error: {e}")

# --- 4. 路由定义 ---
'''
@app.route('/download_batch_zip', methods=['POST'])
def download_batch_zip():
    selected_files = request.json.get('files') # 接收前端传来的文件列表
    memory_file = io.BytesIO()
    
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for file in selected_files:
            # 这里的 file 应该是 server_name/path 格式
            # 先确认缓存里有，没有就 smbget 抓下来，再写入 zip
            # zf.write(local_path, arcname=filename)
            pass
            
    memory_file.seek(0)
    return send_file(memory_file, download_name="batch_logs.zip", as_attachment=True)
'''

@app.route('/api/logs_server_side', methods=['POST'])
def logs_server_side():
    # 1. 获取 DataTables 基础参数
    draw = request.form.get('draw', type=int)
    start = request.form.get('start', type=int)
    length = request.form.get('length', type=int)
    
    # 2. 获取自定义搜索参数 (对应前端各个搜索框)
    s_machine = request.form.get('s_machine')
    s_sn = request.form.get('s_sn')
    s_pn = request.form.get('s_pn')
    s_status = request.form.get('s_status')
    s_stage = request.form.get('s_stage')

    # 3. 基础查询
    query = LogIndex.query

    # 4. 精准过滤逻辑
    if s_machine: query = query.filter(LogIndex.server_name.like(f"%{s_machine}%"))
    if s_sn:      query = query.filter(LogIndex.sn.like(f"%{s_sn}%"))
    if s_pn:      query = query.filter(LogIndex.pn == s_pn)
    if s_status:  query = query.filter(LogIndex.status == s_status)
    if s_stage:   query = query.filter(LogIndex.stage == s_stage)

    # 5. 统计总数
    records_total = LogIndex.query.count()
    records_filtered = query.count()

    # 6. 分页查询
    logs = query.order_by(LogIndex.log_time.desc()).offset(start).limit(length).all()

    # 加载 IP 映射用于拼接 Windows 路径
    ip_map = load_ip_map()

    data = []
    for log in logs:
        ip = ip_map.get(log.server_name, '0.0.0.0')
        # 转换为 Windows 路径格式: \\IP\share\path\to\file
        win_rel_path = log.relative_path.replace('/', '\\')
        win_path = "\\\\{}\\{}".format(ip, win_rel_path)
        
        data.append({
            "log_time": log.log_time.strftime('%Y-%m-%d %H:%M:%S'),
            "server_name": log.server_name,
            "sn": log.sn,
            "pn": log.pn,
            "status": log.status,
            "stage": log.stage,
            "relative_path": log.relative_path,
            "win_path": win_path,  # 👈 传给前端用于复制
            "id": f"{log.server_name}_{log.log_time.timestamp()}" # 唯一ID
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "data": data
    })

@app.route('/')
def index():
    # 只需要统计机台数，不再查具体的 logs
    ip_data = load_ip_map()
    total_machines = db.session.query(func.count(func.distinct(LogIndex.server_name))).scalar()
    # 这里的 logs=[] 传空列表，数据交给 Ajax
    return render_template('index.html', logs=[], ip_map=ip_data, total_machines=total_machines)

@app.route('/download/<server_name>/<path:rel_path>')
def download_log(server_name, rel_path):
    clean_cache()
    ip_map = load_ip_map()
    ip = ip_map.get(server_name)
    if not ip: return f"Error: IP for {server_name} not found", 404

    # 缓存文件名：server_name + path(替换斜杠)
    safe_filename = rel_path.replace('/', '_').replace('\\', '_')
    local_file = os.path.join(CACHE_DIR, f"{server_name}_{safe_filename}")

    if not os.path.exists(local_file):
        # 确保路径开头没有多余斜杠
        clean_rel_path = rel_path.lstrip('/')
        smb_url = f"smb://{ip}/{clean_rel_path}"
        try:
            # 确保服务器已安装 smbclient
            subprocess.run(['smbget', '-a', '-n', smb_url, '-o', local_file],
                           timeout=20, check=True)
        except Exception as e:
            return f"SMB Download Failed: {e}", 500

    return send_file(local_file, as_attachment=True, download_name=os.path.basename(rel_path))

@app.route('/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data: return jsonify({"status": "fail", "msg": "no data"}), 400

    try:
        for item in data:
            # 1. 自动提取 share_name
            rel_path = item.get('relative_path', '')
            if rel_path and '/' in rel_path:
                item['share_name'] = rel_path.split('/')[0]
            else:
                item['share_name'] = 'store'

            # 2. 安全转换时间
            if isinstance(item.get('log_time'), str):
                try:
                    item['log_time'] = datetime.strptime(item['log_time'], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass

            # 3. 构造 Upsert (注意逗号！)
            stmt = insert(LogIndex).values(**item)
            upsert_stmt = stmt.on_duplicate_key_update(
                status=stmt.inserted.status,
                stage=stmt.inserted.stage,
                log_time=stmt.inserted.log_time,
                relative_path=stmt.inserted.relative_path,
                share_name=stmt.inserted.share_name
            )
            db.session.execute(upsert_stmt)
        
        db.session.commit()
        return jsonify({"status": "success", "count": len(data)}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "msg": str(e)}), 500

if __name__ == '__main__':
    # 生产环境请务必关闭 debug=True
    app.run(host='0.0.0.0', port=5000, debug=True)