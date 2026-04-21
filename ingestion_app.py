from app import create_ingestion_app
import logging
from logging.handlers import RotatingFileHandler
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_ingestion_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
handler = RotatingFileHandler('/mnt/mysql/server_logs/flask_app.log', maxBytes=10*1024*1024, backupCount=5)
handler.setFormatter(logging.Formatter(
   '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
    #app.run(host='0.0.0.0', port=5000, debug=True)
