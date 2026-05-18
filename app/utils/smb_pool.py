import os
import logging
import threading
from smbclient import register_session, open_file, delete_session
from smbprotocol.exceptions import SMBException

class SMBSessionPool:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.active_sessions = set()
        self.lock = threading.Lock()

    def _init_session(self, ip):
        with self.lock:
            if ip in self.active_sessions:
                return True
            try:
                try:
                    delete_session(ip)
                except Exception:
                    pass
                register_session(ip, username=self.username, password=self.password)
                self.active_sessions.add(ip)
                return True
            except Exception as e:
                logging.error(f"[SMB Pool] IP [{ip}] connect fail: {str(e)}")
                return False

    def read_file(self, ip, smb_remote_path):
        if ip not in self.active_sessions:
            if not self._init_session(ip):
                raise SMBException(f"Unable to connect to SMB server: {ip}")
        try:
            with open_file(smb_remote_path, mode="rb") as remote_f:
                return remote_f.read()
        except SMBException as e:
            logging.warning(f"[SMB Pool] Session for IP [{ip}] expired, attempting to reconnect. Reason: {str(e)}")
            with self.lock:
                self.active_sessions.discard(ip)
            if self._init_session(ip):
                with open_file(smb_remote_path, mode="rb") as remote_f:
                    return remote_f.read()
            else:
                raise SMBException(f"SMB server [{ip}] disconnected and reconnection failed")

    def get_local_cache(self, server_name, rel_path, ip, cache_dir):
        """
        高层复用函数：确保远程文件安全下载到本地缓存中
        返回: (local_file_path, clean_filename)
        """
        clean_rel_path = rel_path.replace('\\', '/').lstrip('/')
        safe_path = clean_rel_path.replace('/', '_')
        local_file = os.path.join(cache_dir, f"{server_name}_{safe_path}")
        clean_filename = os.path.basename(clean_rel_path)

        if not os.path.exists(local_file):
            win_style_path = clean_rel_path.replace('/', '\\')
            smb_remote_path = f"\\\\{ip}\\{win_style_path}"
            content_bytes = self.read_file(ip, smb_remote_path)
            with open(local_file, mode="wb") as local_f:
                local_f.write(content_bytes)
        return local_file, clean_filename
smb_pool = SMBSessionPool(username="test", password="qcitest")