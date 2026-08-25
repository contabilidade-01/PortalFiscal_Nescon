# -*- coding: utf-8 -*-
"""Utilitarios de certificado A1 compartilhados por NFe e NFSe (o MESMO cert serve os dois).
   Abre PFX legado (RC2/CNG) via cryptography e gera PEM temporario para mTLS.
"""
import os, re, ssl, tempfile, warnings
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from cryptography.x509.oid import NameOID

# Os A1 da ICP-Brasil vem codificados em BER (nao DER estrito). A cryptography le
# normalmente, mas avisa a cada PFX aberto e polui o log do run_diario. Aviso
# conhecido e inofensivo -> silenciado. Se a cryptography virar isso em excecao
# (vide pin no requirements.txt), o erro sobe e NAO fica escondido por este filtro.
warnings.filterwarnings('ignore', category=UserWarning,
                        message=r'PKCS#12 bundle could not be parsed as DER')

UF_COD = {'RO':'11','AC':'12','AM':'13','RR':'14','PA':'15','AP':'16','TO':'17','MA':'21',
          'PI':'22','CE':'23','RN':'24','PB':'25','PE':'26','AL':'27','SE':'28','BA':'29',
          'MG':'31','ES':'32','RJ':'33','SP':'35','PR':'41','SC':'42','RS':'43','MS':'50',
          'MT':'51','GO':'52','DF':'53'}

def guess_password(fn):
    nome = os.path.splitext(os.path.basename(fn))[0]
    m = re.search(r'senha[:\s]*([^\s]+)', nome, re.I)
    if m: return m.group(1).strip(' -')
    m = re.search(r'[-\s]([A-Za-z0-9@#$!._]{4,})\s*$', nome)
    return m.group(1) if m else None

def load_pfx(path, senha):
    return pkcs12.load_key_and_certificates(open(path, 'rb').read(), senha.encode() if senha else None)

def cert_info(cert):
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    cn = cn[0].value if cn else ''
    m = re.search(r':(\d{11,14})', cn)
    ident = m.group(1) if m else ''
    tipo = 'CNPJ' if len(ident) == 14 else ('CPF' if len(ident) == 11 else '?')
    nome = cn.split(':')[0].strip()
    st = cert.subject.get_attributes_for_oid(NameOID.STATE_OR_PROVINCE_NAME)
    uf = (st[0].value if st else '').upper()[:2]
    return ident, tipo, nome, uf, cert.not_valid_after_utc.strftime('%Y-%m-%d')

def pem_temp(path, senha):
    """Extrai leaf.pem + key.pem temporarios de um PFX. Retorna (leaf, key)."""
    key, cert, _ = load_pfx(path, senha)
    fc, leaf = tempfile.mkstemp(suffix='.pem'); os.close(fc)
    fk, keyf = tempfile.mkstemp(suffix='.pem'); os.close(fk)
    open(leaf, 'wb').write(cert.public_bytes(Encoding.PEM))
    open(keyf, 'wb').write(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
    return leaf, keyf

def ssl_ctx(path, senha, verificar_servidor=True):
    """Contexto SSL com cert cliente (para mTLS SOAP). verificar_servidor=False
    quando o endpoint SEFAZ manda cadeia incompleta (ex.: NFeRecepcaoEvento4)."""
    leaf, keyf = pem_temp(path, senha)
    ctx = ssl.create_default_context()
    if not verificar_servidor:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(leaf, keyf)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx, (leaf, keyf)
