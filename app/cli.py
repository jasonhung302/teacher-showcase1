"""命令列工具：
    flask --app wsgi create-admin           建立管理員
    flask --app wsgi seed-demo              建立示範資料（1 位管理員 + 3 位老師）
    flask --app wsgi purge-sessions         清除過期 Session
    flask --app wsgi backup                 建立備份（可放排程，每日執行）
    flask --app wsgi restore-backup <zip>   從備份還原（請先停止網站）
    flask --app wsgi upload-to-azure        把本機 UPLOAD_FOLDER 的既有檔案搬到 Azure Blob Storage
"""
import getpass

import click

from .db import connect, ts
from .security import hash_password, password_problems


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt="管理員帳號")
    @click.option("--name", "display_name", default="系統管理員")
    @click.option("--email", default="")
    def create_admin(username, display_name, email):
        pw = getpass.getpass("密碼：")
        problems = password_problems(pw, username)
        if problems:
            raise click.ClickException("密碼" + "、".join(problems))
        if pw != getpass.getpass("再輸入一次："):
            raise click.ClickException("兩次密碼不一致")
        with connect(app.config["DATABASE_URL"]) as conn:
            conn.execute("INSERT INTO users (username, password_hash, role, display_name, email, is_active,"
                         " must_change_password, created_at, updated_at)"
                         " VALUES (%s, %s, 'admin', %s, %s, 1, 0, %s, %s)",
                         (username, hash_password(pw), display_name, email or None, ts(), ts()))
        click.echo(f"已建立管理員 {username}")

    @app.cli.command("seed-demo")
    def seed_demo():
        from .seed import seed
        with app.app_context():
            seed(app)

    @app.cli.command("purge-sessions")
    def purge_sessions():
        with connect(app.config["DATABASE_URL"]) as conn:
            n = conn.execute("DELETE FROM sessions WHERE expires_at <= %s", (ts(),)).rowcount
        click.echo(f"已清除 {n} 筆過期 Session")

    @app.cli.command("backup")
    def backup_cmd():
        from .backup import create_backup
        path = create_backup(app.config)
        click.echo(f"已建立備份：{path}")

    @app.cli.command("restore-backup")
    @click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--yes", is_flag=True, help="不再詢問直接還原")
    def restore_cmd(zip_path, yes):
        from .backup import restore_backup
        if not yes:
            click.confirm("還原會以備份內容取代目前所有資料（目前資料會先自動另存一份）。請確認網站已停止，是否繼續？",
                          abort=True)
        try:
            safety = restore_backup(app.config, zip_path)
        except ValueError as e:
            raise click.ClickException(str(e))
        click.echo(f"還原完成。還原前的資料已另存為：{safety}")

    @app.cli.command("upload-to-azure")
    def upload_to_azure():
        from .backup import _mime_of
        from .storage import LocalStorage, get_storage
        if not app.config["AZURE_STORAGE_CONNECTION_STRING"]:
            raise click.ClickException("尚未設定 AZURE_STORAGE_CONNECTION_STRING")
        local, blob = LocalStorage(app.config["UPLOAD_FOLDER"]), get_storage(app.config)
        existing = set(blob.names())
        copied = 0
        for name in local.names():
            if name not in existing:
                blob.put(name, local.get(name), _mime_of(name))
                copied += 1
        click.echo(f"已上傳 {copied} 個檔案到容器 {app.config['AZURE_STORAGE_CONTAINER']}"
                   f"（略過已存在的 {len(local.names()) - copied} 個）")
