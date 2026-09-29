"""E-Mail-Versand per SMTP. Zugangsdaten kommen ausschließlich aus Umgebungsvariablen."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


class MailConfigError(Exception):
    pass


def send(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST") or "smtp.gmx.net"
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    sender = os.environ.get("MAIL_FROM") or user
    if not (user and password and to):
        raise MailConfigError("SMTP_USER, SMTP_PASSWORD und MAIL_TO müssen als Secrets gesetzt sein.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
