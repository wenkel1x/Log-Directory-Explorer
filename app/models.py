from . import db

_model_cache = {}

class LogIndexTemplate(db.Model):
    __abstract__ = True
    server_name = db.Column(db.String(50), primary_key=True)
    file_name = db.Column(db.String(150), primary_key=True)
    log_time = db.Column(db.DateTime, primary_key=True)
    pn = db.Column(db.String(128))
    sn = db.Column(db.String(128), index=True)
    status = db.Column(db.String(20))
    stage = db.Column(db.String(50))
    relative_path = db.Column(db.String(512))
    share_name = db.Column(db.String(50))

def get_log_model(year):
    """
    动态获取模型，增加缓存机制防止重复定义报错
    """
    table_name = f'log_index_{year}'
    
    # 1. 检查缓存中是否已经有了这个模型
    if table_name in _model_cache:
        return _model_cache[table_name]
    
    # 2. 如果没有，则动态创建
    # 增加 extend_existing=True 容错处理
    model = type(
        f'LogIndex_{year}', 
        (LogIndexTemplate,), 
        {
            '__tablename__': table_name,
            '__table_args__': {'extend_existing': True} 
        }
    )
    
    # 3. 存入缓存并返回
    _model_cache[table_name] = model
    return model