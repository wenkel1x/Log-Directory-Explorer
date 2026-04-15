from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import urllib.parse

db = SQLAlchemy()

def create_app():
    app = Flask(__name__, 
                template_folder='../templates', 
                static_folder='../static')
    
    # 数据库配置
    raw_password = "P@ssw0rd"
    safe_password = urllib.parse.quote_plus(raw_password)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # 连接池优化
    app.config['SQLALCHEMY_POOL_SIZE'] = 10
    app.config['SQLALCHEMY_MAX_OVERFLOW'] = 20
    app.config['SQLALCHEMY_POOL_RECYCLE'] = 1800

    db.init_app(app)

    # 注册蓝图
    with app.app_context():
        from .route_app import log_bp
        app.register_blueprint(log_bp)

    return app