from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import urllib.parse

# 全局唯一的 db 对象
db = SQLAlchemy()

def _configure_common(app):
    """
    内部私有函数：封装所有通用的基础配置
    """
    raw_password = "P@ssw0rd"
    safe_password = urllib.parse.quote_plus(raw_password)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_size": 20,
        "max_overflow": 40,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "pool_timeout": 30
    }

    # 执行 db 初始化
    db.init_app(app)

def create_portal_app():
    """前端展示服务"""
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    # 继承通用配置
    _configure_common(app)

    with app.app_context():
        # 只导入和注册前端查询相关的蓝图
        from .main import main_bp
        from .routes.search import search_bp
        from .routes.tree import tree_bp
        app.register_blueprint(main_bp)
        app.register_blueprint(search_bp)
        app.register_blueprint(tree_bp)

    return app

def create_ingestion_app():
    """后端上报服务"""
    app = Flask(__name__)

    # 继承通用配置
    _configure_common(app)

    with app.app_context():
        from .routes.upload import upload_bp
        app.register_blueprint(upload_bp)

    return app