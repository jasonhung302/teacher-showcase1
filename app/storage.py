"""上傳檔的存放位置：設定了 AZURE_STORAGE_CONNECTION_STRING 就存到 Azure Blob Storage，否則存在本機 UPLOAD_FOLDER。

兩種後端提供相同的介面；檔名一律是系統產生的隨機檔名，且寫入時絕不覆蓋既有檔案。
Blob 容器保持「私人」存取層級，訪客仍透過 /files/<name> 下載，由網站檢查權限（草稿、獲獎證明等不公開）。
"""
import os
import shutil
import threading

from flask import current_app


class LocalStorage:
    def __init__(self, folder):
        self.folder = folder

    def put(self, name, data, content_type=None):
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)   # 絕不覆蓋既有檔案
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

    def get(self, name):
        path = self.folder / name
        return path.read_bytes() if path.is_file() else None

    def delete(self, name):
        (self.folder / name).unlink(missing_ok=True)

    def names(self):
        if not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir() if p.is_file())

    def clear(self):
        if self.folder.exists():
            shutil.rmtree(self.folder)
        self.folder.mkdir(parents=True)


class AzureBlobStorage:
    def __init__(self, connection_string, container):
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient.from_connection_string(connection_string)
        self.container = service.get_container_client(container)
        try:
            self.container.create_container()      # 預設為私人容器（不開放匿名讀取）
        except ResourceExistsError:
            pass

    def put(self, name, data, content_type=None):
        from azure.storage.blob import ContentSettings
        settings = ContentSettings(content_type=content_type) if content_type else None
        self.container.upload_blob(name, data, overwrite=False, content_settings=settings)

    def get(self, name):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self.container.download_blob(name).readall()
        except ResourceNotFoundError:
            return None

    def delete(self, name):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            self.container.delete_blob(name)
        except ResourceNotFoundError:
            pass

    def names(self):
        return sorted(b.name for b in self.container.list_blobs())

    def clear(self):
        for name in self.names():
            self.delete(name)


_lock = threading.Lock()
_cache = {}


def get_storage(cfg=None):
    """依設定取得儲存後端（同一組設定共用同一個連線）。"""
    cfg = cfg if cfg is not None else current_app.config
    conn_str = cfg.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return LocalStorage(cfg["UPLOAD_FOLDER"])
    key = (conn_str, cfg["AZURE_STORAGE_CONTAINER"])
    with _lock:
        if key not in _cache:
            _cache[key] = AzureBlobStorage(*key)
        return _cache[key]
