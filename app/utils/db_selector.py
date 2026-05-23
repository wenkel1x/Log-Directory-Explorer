from flask import abort, current_app
from sqlalchemy import create_engine
from app import db

PROJECT_MAP = {
    'log_system': 'log_system',
    'ict_log_system': 'ict_log_system',
}

def get_tenant_engine(project_key: str):
    if not project_key:
        abort(400, description="Missing required parameter: 'project_key'")
    clean_key = str(project_key).split(':')[0]
    normalized_key = clean_key.strip().lower()
    if normalized_key not in PROJECT_MAP:
        abort(400, description=f"Unauthorized or unknown project_key: '{project_key}'")
    actual_bind_key = PROJECT_MAP[normalized_key]
    try:
        if actual_bind_key in db.engines:
            return db.engines[actual_bind_key]
    except Exception:
        pass
    try:
        binds = current_app.config.get('SQLALCHEMY_BINDS', {})
        db_url = binds.get(actual_bind_key)

        if db_url:
            print(f"[*] [Tenant Engine Self-Healing] Dynamically recovery engine for bind: [{actual_bind_key}]")
            db.engines[actual_bind_key] = create_engine(
                db_url,
                pool_size=20,
                max_overflow=40,
                pool_recycle=1800,
                pool_pre_ping=True
            )
            return db.engines[actual_bind_key]
        else:
            abort(500, description=f"Database connection string for bind '{actual_bind_key}' missing from configurations.")
    except Exception as e:
        if hasattr(e, 'code'):
            raise e
        abort(500, description=f"Failed to manually initialize database engine for '{actual_bind_key}': {str(e)}")