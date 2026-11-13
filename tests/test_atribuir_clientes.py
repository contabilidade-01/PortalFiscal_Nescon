# -*- coding: utf-8 -*-
"""Atribuicao de clientes a usuarios pelo cadastro do cliente (e em lote na lista)."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())

from werkzeug.security import generate_password_hash

import models
import worker
worker.iniciar_worker = lambda: None
import app as portal

A = '11222333000181'
B = '44555666000199'


def _setup():
    models.DB = os.path.join(tempfile.mkdtemp(), 't.db')
    models.init_db()
    with models.con() as c:
        for cnpj, nome in ((A, 'EMPRESA A'), (B, 'EMPRESA B')):
            c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES(?,?,'SP','35',0,1)", (cnpj, nome))
        ids = {}
        for login, papel, todas in (('ana', 'operador', 0), ('bia', 'operador', 0),
                                    ('chefe', 'admin', 1), ('geral', 'operador', 1)):
            ids[login] = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) "
                                   "VALUES(?,?,?,?,?,1,'x')",
                                   (login, generate_password_hash('x'), login.title(), papel, todas)).lastrowid
    models.salvar_permissoes(ids['ana'], ['ver_execucoes'])
    return ids


def _eid(cnpj):
    with models.con() as c:
        return c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()['id']


def _user(uid):
    with models.con() as c:
        return c.execute('SELECT * FROM usuarios WHERE id=?', (uid,)).fetchone()


def _cli(uid, papel):
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = uid; s['nome'] = 'X'; s['papel'] = papel
    return cli


def test_so_operadores_restritos_sao_atribuiveis():
    ids = _setup()
    assert {u['login'] for u in models.usuarios_atribuiveis()} == {'ana', 'bia'}


def test_editar_cliente_atribui_e_operador_passa_a_ver():
    ids = _setup()
    adm = _cli(ids['chefe'], 'admin')
    eid = _eid(A)
    r = adm.post('/clientes/%d/editar' % eid, data={'nome': 'EMPRESA A', 'uf': 'SP', 'ativo': '1',
                                                    'usuarios': [str(ids['ana']), str(ids['chefe'])]})
    assert r.status_code == 302
    assert models.usuarios_da_empresa(eid) == {ids['ana']}  # admin ignorado
    assert models.cnpjs_visiveis(_user(ids['ana'])) == [A]
    assert models.cnpjs_visiveis(_user(ids['bia'])) == []
    # desmarcar tira o acesso
    adm.post('/clientes/%d/editar' % eid, data={'nome': 'EMPRESA A', 'uf': 'SP', 'ativo': '1'})
    assert models.usuarios_da_empresa(eid) == set()


def test_novo_cliente_ja_sai_atribuido():
    ids = _setup()
    _cli(ids['chefe'], 'admin').post('/clientes/novo', data={
        'cnpj': '77.888.999/0001-55', 'nome': 'NOVA', 'uf': 'SP', 'ativo': '1', 'usuarios': [str(ids['bia'])]})
    assert models.usuarios_da_empresa(_eid('77888999000155')) == {ids['bia']}


def test_atribuir_em_lote_e_retirar():
    ids = _setup()
    adm = _cli(ids['chefe'], 'admin')
    adm.post('/clientes/atribuir', data={'usuario': ids['bia'], 'acao': 'atribuir',
                                         'cnpjs': '11.222.333/0001-81\n44555666000199\n99999999000199'})
    assert set(models.cnpjs_visiveis(_user(ids['bia']))) == {A, B}
    adm.post('/clientes/atribuir', data={'usuario': ids['bia'], 'acao': 'remover', 'cnpjs': B})
    assert models.cnpjs_visiveis(_user(ids['bia'])) == [A]
    html = adm.get('/clientes').data.decode()
    assert 'Atribuir clientes a usuário' in html and 'Bia' in html


def test_lote_recusa_admin_e_operador_nao_acessa():
    ids = _setup()
    _cli(ids['chefe'], 'admin').post('/clientes/atribuir', data={'usuario': ids['chefe'], 'cnpjs': A})
    assert models.usuarios_da_empresa(_eid(A)) == set()
    r = _cli(ids['ana'], 'operador').post('/clientes/atribuir', data={'usuario': ids['ana'], 'cnpjs': A})
    assert r.status_code in (302, 403)
    assert models.usuarios_da_empresa(_eid(A)) == set()


def test_editar_nao_mexe_em_quem_nao_e_atribuivel():
    ids = _setup()
    eid = _eid(A)
    with models.con() as c:  # sobra antiga de usuario que virou "todas as empresas"
        c.execute('INSERT INTO usuario_empresas(user_id,empresa_id) VALUES(?,?)', (ids['geral'], eid))
    models.salvar_usuarios_da_empresa(eid, [ids['ana']])
    assert models.usuarios_da_empresa(eid) == {ids['ana'], ids['geral']}


def test_formulario_mostra_usuarios_marcados():
    ids = _setup()
    eid = _eid(A)
    models.salvar_usuarios_da_empresa(eid, [ids['ana']])
    html = _cli(ids['chefe'], 'admin').get('/clientes/%d/editar' % eid).data.decode()
    assert 'Usuários responsáveis' in html
    assert 'value="%d" checked' % ids['ana'] in html and 'value="%d" checked' % ids['bia'] not in html
