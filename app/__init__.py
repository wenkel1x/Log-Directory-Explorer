from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import urllib.parse

# 全局唯一的 db 对象
db = SQLAlchemy()

def _configure_common(app):
    raw_password = "P@ssw0rd"
    safe_password = urllib.parse.quote_plus(raw_password)

    app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['SQLALCHEMY_BINDS'] = {
        'log_system': f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/log_system?charset=utf8mb4',
        'ict_log_System': f'mysql+pymysql://root:{safe_password}@127.0.0.1:3306/ict_log_System?charset=utf8mb4'
    }

    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_size": 20,
        "max_overflow": 40,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "pool_timeout": 30
    }

    db.init_app(app)

def create_portal_app():
    """前端展示服务"""
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    _configure_common(app)

    with app.app_context():
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
    _configure_common(app)

    with app.app_context():
        from .routes.upload import upload_bp
        app.register_blueprint(upload_bp)

    return app