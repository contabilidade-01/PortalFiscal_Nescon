# -*- coding: utf-8 -*-
"""Envio de e-mail via SMTP (mesmo padrao do nescon-clientes: SMTP_* + PUBLIC_APP_URL)."""
import os
import smtplib
from email.message import EmailMessage


def is_smtp_configured():
    return bool(
        os.environ.get('SMTP_HOST')
        and os.environ.get('SMTP_USER')
        and os.environ.get('SMTP_PASS')
        and os.environ.get('SMTP_FROM')
    )


def get_public_app_url():
    return (os.environ.get('PUBLIC_APP_URL') or '').strip().rstrip('/')


def _smtp_params():
    host = os.environ.get('SMTP_HOST', '').strip()
    port = int(os.environ.get('SMTP_PORT') or '587')
    secure = os.environ.get('SMTP_SECURE', '').strip().lower() in ('1', 'true', 'yes') or port == 465
    user = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASS', '')
    from_addr = os.environ.get('SMTP_FROM', '').strip()
    return host, port, secure, user, password, from_addr


def send_password_reset_email(to, reset_url):
    if not is_smtp_configured():
        raise RuntimeError('SMTP não configurado')
    host, port, secure, user, password, from_addr = _smtp_params()
    msg = EmailMessage()
    msg['Subject'] = 'Redefinição de senha — Portal Fiscal Nescon'
    msg['From'] = from_addr
    msg['To'] = to
    msg.set_content(
        'Recebemos um pedido para redefinir a senha desta conta.\n\n'
        'Abra o link (válido por tempo limitado):\n%s\n\n'
        'Se não foi você, ignore este e-mail.\n' % reset_url
    )
    msg.add_alternative(
        '<p>Recebemos um pedido para redefinir a senha desta conta.</p>'
        '<p><a href="%s">Redefinir senha</a></p>'
        '<p>Se não foi você, ignore este e-mail.</p>' % reset_url.replace('"', '&quot;'),
        subtype='html',
    )
    if secure:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            try:
                s.starttls()
                s.ehlo()
            except smtplib.SMTPException:
                pass
            s.login(user, password)
            s.send_message(msg)
