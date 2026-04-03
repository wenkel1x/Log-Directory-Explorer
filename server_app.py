from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import insert
import urllib.parse
from datetime import datetime

app = Flask(__name__)

raw_password = "P@ssw0rd"
safe_password = urllib.parse.quote_plus(raw_password)

# 数据库配置
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class LogIndex(db.Model):
    __tablename__ = 'log_index'
    # 联合主键：确保同一台服务器下的同一个文件名是唯一的
    server_name = db.Column(db.String(50), primary_key=True)
    file_name = db.Column(db.String(150), primary_key=True)
    
    pn = db.Column(db.String(128)) # 稍微加长，兼容 simple_fixture
    sn = db.Column(db.String(128))
    log_time = db.Column(db.DateTime)
    status = db.Column(db.String(20)) # 兼容 PASS/FAIL/ABORT
    stage = db.Column(db.String(50))
    relative_path = db.Column(db.String(512)) # 匹配数据库瘦身后的字段

@app.route('/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data:
        return jsonify({"status": "fail", "msg": "no data"}), 400

    try:
        print(f"[{datetime.now()}] Incoming batch: {len(data)} items")
        
        for item in data:
            if isinstance(item['log_time'], str):
                item['log_time'] = datetime.strptime(item['log_time'], '%Y-%m-%d %H:%M:%S')

            # 这里的 item 字典必须包含 server_name, file_name, pn, sn, log_time, status, stage, relative_path
            stmt = insert(LogIndex).values(**item)
            
            # Upsert 逻辑：如果主键冲突，更新以下状态
            upsert_stmt = stmt.on_duplicate_key_update(
                status=stmt.inserted.status,
                stage=stmt.inserted.stage,
                log_time=stmt.inserted.log_time,
                relative_path=stmt.inserted.relative_path # 更新最新的路径
            )
            db.session.execute(upsert_stmt)
        
        db.session.commit()
        print(f"[{datetime.now()}] Success: Batch committed")
        return jsonify({"status": "success", "count": len(data)}), 200

    except Exception as e:
        db.session.rollback()
        # 强制在控制台打印错误，方便调试
        print(f"!!! DB ERROR: {str(e)}")
        return jsonify({"status": "error", "msg": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)