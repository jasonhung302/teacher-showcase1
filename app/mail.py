"""寄信：有設定 SMTP 就寄出；沒有則寫入伺服器 log（開發環境用）。"""
import smtplib
from email.message import EmailMessage

from flask import current_app


def send_mail(to, subject, body):
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        current_app.logger.warning("[MAIL 未設定 SMTP，以下內容僅寫入 log]\nTo: %s\nSubject: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
            if cfg["SMTP_USE_TLS"]:
                smtp.starttls()
            if cfg["SMTP_USER"]:
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        current_app.logger.exception("寄信失敗：%s", to)
        return False
