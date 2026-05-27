from app import create_ingestion_app
import logging
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_ingestion_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
    app.logger.info("INGESTION APP STARTED SUCCESSFULLY UNDER GUNICORN")
if __name__ == '__main__':
    IS_DEBUG = False
    log_level = logging.DEBUG if IS_DEBUG else logging.INFO
    logging.getLogger().setLevel(log_level)
    app.logger.setLevel(log_level)
    app.logger.info(f"LOCAL TEST START: LOG LEVEL {'DEBUG' if IS_DEBUG else 'INFO'}")
    app.run(host='0.0.0.0', port=5001, debug=IS_DEBUG)