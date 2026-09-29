"""E-Mail-Versand per SMTP. Zugangsdaten kommen ausschließlich aus Umgebungsvariablen."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


class MailConfigError(Exception):
    pass


def send(subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST") or "mail.gmx.net"
    port = int(os.environ.get("SMTP_PORT") or "587")
    # Beim Einfügen in GitHub-Secrets rutschen leicht Leerzeichen/Zeilenumbrüche mit hinein.
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip()
    to = (os.environ.get("MAIL_TO") or "").strip() or user
    sender = (os.environ.get("MAIL_FROM") or "").strip() or user
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
            _login(s, user, password, host)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            _login(s, user, password, host)
            s.send_message(msg)


def _login(s: smtplib.SMTP, user: str, password: str, host: str) -> None:
    try:
        s.login(user, password)
    except smtplib.SMTPAuthenticationError as e:
        print(_diagnose(user, password))
        raise MailConfigError(
            f"{host} hat die Anmeldung für '{user}' abgelehnt ({e.smtp_code}). Prüfen: vollständige "
            "E-Mail-Adresse als SMTP_USER, Passwort ohne Leerzeichen, bei aktiver Zwei-Faktor-"
            "Anmeldung ein anwendungsspezifisches Passwort, Versand via externer Software erlaubt."
        ) from e


def _diagnose(user: str, password: str) -> str:
    """Hinweise zur Fehlersuche, ohne die Zugangsdaten selbst auszugeben."""
    domain = user.rpartition("@")[2] if "@" in user else "(kein @ – vollständige Adresse nötig!)"
    raw_user, raw_pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    return (
        "Diagnose Zugangsdaten:\n"
        f"  SMTP_USER: {len(user)} Zeichen, Domain: {domain}, "
        f"Leerzeichen/Umbruch entfernt: {'ja' if raw_user != user else 'nein'}\n"
        f"  SMTP_PASSWORD: {len(password)} Zeichen, "
        f"Leerzeichen/Umbruch entfernt: {'ja' if raw_pw != password else 'nein'}, "
        f"Nicht-ASCII-Zeichen (z. B. ä, ö, ü, ß, €, §): {'ja' if not password.isascii() else 'nein'}"
    )
