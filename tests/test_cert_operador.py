# -*- coding: utf-8 -*-
"""Operador (nao-admin) vinculando certificado: precisa da permissao
'vincular_certificado' e so vincula a empresas do proprio escopo."""
import os, sys, io, types, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ['FISCAL_DATA_DIR'] = tempfile.mkdtemp()

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from werkzeug.security import generate_password_hash

import models
import worker
worker.iniciar_worker = lambda: None  # sem thread de jobs no teste
import app as portal

CNPJ_ESCOPO = '11222333000181'
CNPJ_FORA = '44555666000199'
CNPJ_NOVO = '77888999000155'


def _pfx(cnpj, senha='123'):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'EMPRESA TESTE LTDA:%s' % cnpj),
                      x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, 'SP')])
    agora = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - timedelta(days=1))
            .not_valid_after(agora + timedelta(days=365)).sign(k, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b'x', k, cert, None, serialization.BestAvailableEncryption(senha.encode()))


def _setup(perms):
    models.DB = os.path.join(tempfile.mkdtemp(), 't.db')
    models.init_db()
    with models.con() as c:
        for cnpj in (CNPJ_ESCOPO, CNPJ_FORA):
            c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES(?,?,'SP','35',0,1)",
                      (cnpj, 'EMP ' + cnpj))
        uid = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) "
                        "VALUES('op',?,'Op','operador',0,1,'x')",
                        (generate_password_hash('x'),)).lastrowid
        eid = c.execute('SELECT id FROM empresas WHERE cnpj=?', (CNPJ_ESCOPO,)).fetchone()['id']
        c.execute('INSERT INTO usuario_empresas(user_id,empresa_id) VALUES(?,?)', (uid, eid))
    models.salvar_permissoes(uid, perms)
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = uid; s['nome'] = 'Op'; s['papel'] = 'operador'
    return cli


def _envia(cli, cnpj):
    return cli.post('/cert/upload', data={'senha': '123', 'cert': (io.BytesIO(_pfx(cnpj)), 'certificado.pfx')},
                    content_type='multipart/form-data')


def _emp(cnpj):
    with models.con() as c:
        return c.execute('SELECT * FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()


def test_operador_com_permissao_vincula_empresa_do_escopo():
    cli = _setup(['vincular_certificado'])
    _envia(cli, CNPJ_ESCOPO)
    e = _emp(CNPJ_ESCOPO)
    assert e['senha_ok'] == 1 and e['uf'] == 'SP'
    assert os.path.basename(e['arquivo']) == '%s_certificado.pfx' % CNPJ_ESCOPO
    assert os.path.isfile(e['arquivo'])


def test_operador_sem_permissao_nao_vincula():
    cli = _setup([])
    _envia(cli, CNPJ_ESCOPO)
    assert _emp(CNPJ_ESCOPO)['senha_ok'] == 0


def test_operador_nao_vincula_fora_do_escopo_nem_cria_empresa():
    cli = _setup(['vincular_certificado'])
    _envia(cli, CNPJ_FORA)
    _envia(cli, CNPJ_NOVO)
    assert _emp(CNPJ_FORA)['senha_ok'] == 0
    assert _emp(CNPJ_NOVO) is None
    assert not [a for a in os.listdir(portal.CERT_DIR) if a.startswith((CNPJ_FORA, CNPJ_NOVO, '.upload_'))]


def test_tela_mostra_upload_so_com_permissao():
    assert b'cert/upload' not in _setup([]).get('/certificados').data
    html = _setup(['vincular_certificado']).get('/certificados').data
    assert b'cert/upload' in html and b'cert/scan' not in html
