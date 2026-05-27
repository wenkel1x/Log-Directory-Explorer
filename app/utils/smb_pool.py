import os
import logging
import threading
from smbclient import register_session, open_file, delete_session
from smbprotocol.exceptions import SMBException
from app.utils.utils import get_env_variable

SMB_CREDENTIALS = {
    "log_system": {
        "username": get_env_variable("SMB_BFT_USERNAME", required=True),
        "password": get_env_variable("SMB_BFT_PASSWORD", required=True)
    },
    "ict_log_system": {
        "username": get_env_variable("SMB_ICT_USERNAME", required=True),
        "password": get_env_variable("SMB_ICT_PASSWORD", required=True)
    }
}

class SMBSessionPool:
    def __init__(self):
        self.active_sessions = set()
        self.lock = threading.Lock()

    def _init_session(self, ip, project_key):
        with self.lock:
            if ip in self.active_sessions:
                return True
            # 获取当前项目对应的凭据，找不到则用 log_system 兜底
            cred = SMB_CREDENTIALS.get(project_key, SMB_CREDENTIALS["log_system"])
            username = cred["username"]
            password = cred["password"]
            
            try:
                try:
                    delete_session(ip)
                except Exception:
                    pass
                register_session(ip, username=username, password=password)
                self.active_sessions.add(ip)
                return True
            except Exception as e:
                logging.error(f"[SMB Pool] Project [{project_key}] IP [{ip}] connect fail: {str(e)}")
                return False

    def read_file(self, ip, smb_remote_path, project_key):
        if ip not in self.active_sessions:
            if not self._init_session(ip, project_key):
                raise SMBException(f"Unable to connect to SMB server: {ip} with project: {project_key}")
        try:
            with open_file(smb_remote_path, mode="rb") as remote_f:
                return remote_f.read()
        except SMBException as e:
            logging.warning(f"[SMB Pool] Session for IP [{ip}] expired, reconnecting. Reason: {str(e)}")
            with self.lock:
                self.active_sessions.discard(ip)
            if self._init_session(ip, project_key):
                with open_file(smb_remote_path, mode="rb") as remote_f:
                    return remote_f.read()
            else:
                raise SMBException(f"SMB server [{ip}] disconnected and reconnection failed")

    def get_local_cache(self, server_name, rel_path, ip, cache_dir, project_key='log_system'):
        clean_rel_path = rel_path.replace('\\', '/').lstrip('/')
        safe_path = clean_rel_path.replace('/', '_')
        local_file = os.path.join(cache_dir, f"{server_name}_{safe_path}")
        clean_filename = os.path.basename(clean_rel_path)

        if not os.path.exists(local_file):
            win_style_path = clean_rel_path.replace('/', '\\')
            smb_remote_path = f"\\\\{ip}\\{win_style_path}"

            content_bytes = self.read_file(ip, smb_remote_path, project_key)
            with open(local_file, mode="wb") as local_f:
                local_f.write(content_bytes)
        return local_file, clean_filename

smb_pool = SMBSessionPool()