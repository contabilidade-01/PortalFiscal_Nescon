# -*- coding: utf-8 -*-
"""Envio de e-mail via SMTP (mesmo padrao do nescon-clientes: SMTP_* + PUBLIC_APP_URL)."""
import os
import smtplib
from email.message import EmailMessage


import logging

_log = logging.getLogger(__name__)

# Nomes aceitos para cada parametro (o primeiro preenchido vence). Assim o mesmo
# bloco de variaveis copiado de outros projetos (nescon-clientes, central, Node/
# nodemailer, Django, Flask-Mail) funciona sem renomear nada.
_ALIASES = {
    'host':   ('SMTP_HOST', 'SMTP_SERVER', 'MAIL_HOST', 'MAIL_SERVER', 'EMAIL_HOST'),
    'port':   ('SMTP_PORT', 'MAIL_PORT', 'EMAIL_PORT'),
    'secure': ('SMTP_SECURE', 'SMTP_SSL', 'MAIL_USE_SSL', 'EMAIL_USE_SSL'),
    'user':   ('SMTP_USER', 'SMTP_USERNAME', 'SMTP_LOGIN', 'MAIL_USER', 'MAIL_USERNAME',
               'EMAIL_HOST_USER', 'EMAIL_USER'),
    'pass':   ('SMTP_PASS', 'SMTP_PASSWORD', 'SMTP_SENHA', 'MAIL_PASS', 'MAIL_PASSWORD',
               'EMAIL_HOST_PASSWORD', 'EMAIL_PASSWORD', 'EMAIL_PASS'),
    'from':   ('SMTP_FROM', 'MAIL_FROM', 'MAIL_DEFAULT_SENDER', 'DEFAULT_FROM_EMAIL',
               'EMAIL_FROM'),
}


def _env(chave):
    for nome in _ALIASES[chave]:
        v = os.environ.get(nome)
        if v is not None and v.strip() != '':
            # Remove aspas que o painel as vezes grava literalmente ("valor")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1].strip()
            return v
    return ''


def smtp_faltando():
    """Nomes (principais) das variaveis obrigatorias que estao vazias.
       SMTP_FROM nao e obrigatoria: sem ela usa o proprio SMTP_USER."""
    falta = []
    if not _env('host'):
        falta.append('SMTP_HOST')
    if not _env('user'):
        falta.append('SMTP_USER')
    if not _env('pass'):
        falta.append('SMTP_PASS')
    if _env('user') and not (_env('from') or '@' in _env('user')):
        falta.append('SMTP_FROM')
    return falta


def is_smtp_configured():
    falta = smtp_faltando()
    if falta:
        _log.warning('SMTP desligado: variaveis ausentes no ambiente do servico: %s',
                     ', '.join(falta))
    return not falta


def get_public_app_url():
    return (os.environ.get('PUBLIC_APP_URL') or '').strip().rstrip('/')


def _smtp_params():
    host = _env('host')
    port = int(_env('port') or '587')
    secure = _env('secure').lower() in ('1', 'true', 'yes', 'sim', 'ssl') or port == 465
    user = _env('user')
    password = _env('pass')
    from_addr = _env('from') or user
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
