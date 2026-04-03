from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import insert
import urllib

app = Flask(__name__)

raw_password = "P@ssw0rd"
safe_password = urllib.parse.quote_plus(raw_password)
# 数据库连接配置 (注意：如果 MySQL 在 Docker，IP 用 127.0.0.1)
# 格式：mysql+pymysql://用户名:密码@IP:端口/数据库名
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 映射刚才创建的表
class LogIndex(db.Model):
    __tablename__ = 'log_index'
    server_name = db.Column(db.String(50), primary_key=True)
    file_name = db.Column(db.String(150), primary_key=True)

    server_name = db.Column(db.String(50))
    pn = db.Column(db.String(50))
    sn = db.Column(db.String(100))
    log_time = db.Column(db.DateTime)
    status = db.Column(db.String(10))
    stage = db.Column(db.String(20))
    share_name = db.Column(db.String(50))
    relative_path = db.Column(db.String(500))
    full_path = db.Column(db.String(500))

@app.route('/upload_batch', methods=['POST'])
def upload_batch():
    data = request.json
    if not data:
        return jsonify({"status": "fail", "msg": "no data"}), 400

    try:
        # 使用批量插入/更新逻辑 (Upsert)
        for item in data:
            stmt = insert(LogIndex).values(**item)
            # 如果文件名重复，则更新路径和状态等信息
            upsert_stmt = stmt.on_duplicate_key_update(
                full_path=stmt.inserted.full_path,
                status=stmt.inserted.status,
                stage=stmt.inserted.stage
            )
            db.session.execute(upsert_stmt)
        
        db.session.commit()
        return jsonify({"status": "success", "count": len(data)}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "msg": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)  