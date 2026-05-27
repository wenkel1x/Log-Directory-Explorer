# Log Directory Explorer

A distributed log file indexing and management system that provides real-time log monitoring, search, and analysis capabilities with Redis-backed task queuing and MySQL-backed storage.

## 📋 Overview

Log Directory Explorer is a Python-based application designed to efficiently scan, index, and explore log files across network directories. It combines Flask web servers with Redis task queuing and MySQL database for robust log management.

**Key Features:**
- 📊 Real-time log file scanning and indexing
- 🔍 Advanced log search and filtering capabilities
- 🔄 Asynchronous batch processing with Redis queues
- 💾 MySQL-based persistent storage with year-partitioned tables
- 🚀 High-performance parallel processing with cleanup tasks
- 📈 Comprehensive error tracking and logging
- 🛡️ Thread-safe cache management

## 🏗️ Architecture

The system consists of three main components:

### 1. **Server App** (`server_app.py`)
- Flask web portal for log browsing and search
- Runs on port 5000
- Serves HTML templates from `/templates` directory
- Static assets from `/static` directory
- ProxyFix middleware support for reverse proxy deployments

### 2. **Ingestion App** (`ingestion_app.py`)
- REST API service for receiving log data
- Runs on port 5001
- Accepts log batch submissions
- Routes data to Redis queue for processing

### 3. **Redis Worker** (`redis_app.py`)
- Background worker consuming from Redis queue
- Processes batch insert operations
- Handles cleanup tasks with configurable pause mechanism
- Automatic database table creation for year-based partitioning
- Thread-safe local caching of table names

## 🗄️ Data Storage

### Database Schema
- **Dynamic table creation**: `log_index_{YYYY}` tables created per year
- **Template table**: `log_index_template` (base schema)
- **Tree data table**: `log_tree_data` (directory structure tracking)

### Table Structure
```sql
CREATE TABLE log_index_YYYY (
    server_name VARCHAR(255),
    file_name VARCHAR(255),
    log_time DATETIME,
    pn VARCHAR(255),              -- Path name
    sn VARCHAR(255),              -- Serial number
    status VARCHAR(50),
    stage VARCHAR(50),
    relative_path TEXT,
    share_name VARCHAR(255),
    last_scan_id BIGINT,
    PRIMARY KEY (server_name, file_name, log_time)
)
```

## 🚀 Getting Started

### Prerequisites
- Python 3.7+
- MySQL 5.7+
- Redis 5.0+

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/wenkel1x/Log-Directory-Explorer.git
   cd Log-Directory-Explorer
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   - Update database connection strings in `app/config.py`
   - Configure Redis connection (default: `127.0.0.1:6379`)
   - Set up log directory paths

4. **Initialize database**
   ```bash
   # Create template tables in MySQL
   mysql -u root -p < schema.sql
   ```

### Running the Application

**Terminal 1 - Start Server Portal:**
```bash
python server_app.py
```
Access at: `http://localhost:5000`

**Terminal 2 - Start Ingestion API:**
```bash
python ingestion_app.py
```
API endpoint: `http://localhost:5001`

**Terminal 3 - Start Redis Worker:**
```bash
python redis_app.py
```

## 📡 API Usage

### Submit Log Batch
```bash
curl -X POST http://localhost:5001/api/logs/upload \
  -H "Content-Type: application/json" \
  -d '{
    "scan_id": 12345,
    "items": [
      {
        "server_name": "server1",
        "share_name": "share1",
        "file_name": "app.log",
        "log_time": "2026-05-23 10:30:45",
        "relative_path": "/var/log/",
        "pn": "/path/name",
        "sn": "serial123",
        "status": "active",
        "stage": "processed"
      }
    ]
  }'
```

### Trigger Cleanup Task
```bash
curl -X POST http://localhost:5001/api/logs/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "type": "cleanup_task",
    "server_name": "server1",
    "share_name": "share1",
    "scan_id": 12345
  }'
```

## 🔧 Configuration

### Redis Keys
- `log_upload_queue`: Main task queue (blocking list)
- `log_system_pause`: Global pause flag (cleanup operations)
- `log_errors`: Error log storage (last 1000 errors)

### Database Connection
Configure in `app/config.py`:
```python
SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://user:password@localhost/logdb'
```

### Logging
- Application logs: `/mnt/mysql/server_logs/flask_app.log`
- Max size: 10MB per file
- Backup files: 5 rotations

## 🛠️ Error Handling

The system includes comprehensive error tracking:

### Error Logging to Redis
```json
{
  "time": "2026-05-23 10:30:45",
  "type": "DB_INSERT_ERROR|CLEANUP_ERROR",
  "error": "Error message details",
  "server": "server_name"
}
```

### Retry Logic
- Database deadlock detection (error codes 1213, 1205)
- 3 retry attempts with exponential backoff
- Automatic rollback on failure

## 🔒 Thread Safety

- `cache_lock`: Protects `LOCAL_TABLE_CACHE` during concurrent table creation
- Local table name caching reduces database queries
- Safe concurrent batch insertions with ON DUPLICATE KEY UPDATE

## 📊 Performance Features

- **Batch Processing**: 5000-row chunks for cleanup operations
- **Rate Limiting**: 50ms sleep between cleanup batches
- **Deadlock Recovery**: Automatic retry with backoff
- **Global Pause**: Prevents data ingestion during maintenance

## 🐛 Troubleshooting

### Redis Connection Failed
```python
# Check Redis status
redis-cli ping  # Should return PONG
```

### Database Connection Issues
```python
# Verify MySQL is running
mysql -u root -p -e "SHOW DATABASES;"
```

### Cleanup Hanging
- Check `log_system_pause` key in Redis
- Manual cleanup: `redis-cli DEL log_system_pause`

## 📝 License

Unlicensed - See repository for details

## 👥 Contributing

Pull requests welcome! For major changes, please open an issue first.

## 📧 Support

For issues and questions, please use GitHub Issues.

---

**Last Updated**: May 2026
