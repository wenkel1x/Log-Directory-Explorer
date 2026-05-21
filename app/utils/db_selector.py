from flask import abort
from app import db

PROJECT_MAP = {
    'log_system': 'log_system',
    'ict_log_system': 'ict_log_System',
}

def get_tenant_engine(project_key: str):
    if not project_key:
        abort(400, description="Missing required parameter: 'project_key'")
    normalized_key = project_key.strip().lower()

    if normalized_key not in PROJECT_MAP:
        abort(400, description=f"Unauthorized or unknown project_key: '{project_key}'")
    actual_bind_key = PROJECT_MAP[normalized_key]
    return db.get_engine(bind=actual_bind_key)