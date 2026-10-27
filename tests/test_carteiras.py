# -*- coding: utf-8 -*-
"""Carteiras (Nescon / Jean) na base unica: o operador so ve as carteiras liberadas,
mas o robo continua rodando para todas as empresas."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())

from werkzeug.security import generate_password_hash

import models
import worker
worker.iniciar_worker = lambda: None  # sem thread de jobs no teste
import app as portal

NESCON = '11222333000181'
JEAN = '44555666000199'


def _setup(carteiras=(), avulsas=(), todas=0):
    models.DB = os.path.join(tempfile.mkdtemp(), 't.db')
    models.init_db()
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo,puxa_nfe) VALUES(?,?,'SP','35',1,1,1)",
                  (NESCON, 'EMPRESA NESCON'))
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo,puxa_nfe,carteira) "
                  "VALUES(?,?,'SP','35',1,1,1,'Jean')", (JEAN, 'EMPRESA DO JEAN'))
        uid = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) "
                        "VALUES('op',?,'Op','operador',?,1,'x')",
                        (generate_password_hash('x'), todas)).lastrowid
        for cnpj in avulsas:
            eid = c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()['id']
            c.execute('INSERT INTO usuario_empresas(user_id,empresa_id) VALUES(?,?)', (uid, eid))
    models.salvar_carteiras(uid, list(carteiras))
    models.salvar_permissoes(uid, ['ver_execucoes', 'ver_protocolos'])
    return uid


def _cli(uid, papel='operador'):
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = uid; s['nome'] = 'X'; s['papel'] = papel
    return cli


def _user(uid):
    with models.con() as c:
        return c.execute('SELECT * FROM usuarios WHERE id=?', (uid,)).fetchone()


def _carteira(cnpj):
    with models.con() as c:
        return c.execute('SELECT carteira FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()['carteira']


def test_empresa_sem_carteira_cai_na_nescon():
    _setup()
    assert _carteira(NESCON) == 'Nescon'


def test_operador_da_nescon_nao_ve_o_jean_e_ve_as_novas():
    uid = _setup(carteiras=['Nescon'])
    assert models.cnpjs_visiveis(_user(uid)) == [NESCON]
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,senha_ok,ativo) VALUES('77888999000155','NOVA',0,1)")
    assert set(models.cnpjs_visiveis(_user(uid))) == {NESCON, '77888999000155'}
    html = _cli(uid).get('/clientes').data
    assert b'Empresa Nescon' in html and b'Empresa Do Jean' not in html


def test_empresa_avulsa_soma_com_a_carteira():
    uid = _setup(carteiras=['Nescon'], avulsas=[JEAN])
    assert set(models.cnpjs_visiveis(_user(uid))) == {NESCON, JEAN}


def test_todas_as_empresas_ve_tudo():
    uid = _setup(todas=1)
    assert models.cnpjs_visiveis(_user(uid)) is None


def test_robo_roda_para_todas_as_carteiras():
    _setup(carteiras=['Nescon'])
    assert {e['cnpj'] for e in worker._empresas('todas', 'puxa_nfe')} == {NESCON, JEAN}


def test_execucoes_status_e_protocolos_escondem_o_jean():
    uid = _setup(carteiras=['Nescon'])
    with models.con() as c:
        for cnpj, nome in ((NESCON, 'EMPRESA NESCON'), (JEAN, 'EMPRESA DO JEAN')):
            c.execute("INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada) "
                      "VALUES('nfe_entrada',?,?,'2026-10-27 10:00:00',1,'ok')", (cnpj, nome))
            c.execute("INSERT INTO ciencia_dada(cnpj,chNFe,cStat,nProt,quando) VALUES(?,?,'135','1','x')",
                      (cnpj, 'ch' + cnpj))
        c.execute("INSERT INTO jobs(tipo,escopo,status,criado,atual) "
                  "VALUES('completo','todas','rodando','x','EMPRESA DO JEAN')")
    cli = _cli(uid)
    for url in ('/execucoes', '/protocolos', '/status', '/', '/ambiente/nfe_entradas'):
        html = cli.get(url).data.upper()
        assert b'EMPRESA DO JEAN' not in html and JEAN.encode() not in html, url
    assert b'EMPRESA NESCON' in cli.get('/execucoes').data.upper()
    # O admin continua vendo tudo
    assert b'EMPRESA DO JEAN' in _cli(uid, papel='admin').get('/execucoes').data.upper()


def test_operador_nao_cancela_job_de_toda_a_base():
    uid = _setup(carteiras=['Nescon'])
    with models.con() as c:
        jid = c.execute("INSERT INTO jobs(tipo,escopo,status,criado) VALUES('completo','todas','fila','x')").lastrowid
    _cli(uid).post('/jobs/%d/cancelar' % jid)
    with models.con() as c:
        assert c.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()['status'] == 'fila'


def test_admin_move_em_lote_e_salva_carteiras_do_usuario():
    uid = _setup()
    with models.con() as c:
        adm = c.execute("SELECT id FROM usuarios WHERE papel='admin'").fetchone()['id']
    adm_cli = _cli(adm, papel='admin')
    adm_cli.post('/clientes/carteira', data={'carteira': 'Jean', 'cnpjs': '11.222.333/0001-81\n999'})
    assert _carteira(NESCON) == 'Jean'
    adm_cli.post('/usuarios/%d/editar' % uid, data={'nome': 'Op', 'papel': 'operador', 'ativo': '1',
                                                    'carteiras': ['Jean', 'Inventada']})
    assert models.carteiras_do_usuario(uid) == {'Jean'}
    # operador nao muda carteira
    _cli(uid).post('/clientes/carteira', data={'carteira': 'Nescon', 'cnpjs': NESCON})
    assert _carteira(NESCON) == 'Jean'
