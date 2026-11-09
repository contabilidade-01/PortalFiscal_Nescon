# -*- coding: utf-8 -*-
"""Exclusao de clientes (um a um e em lote): some da base e da integracao Dominio."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())
os.environ['DOMINIO_KEY'] = 'chave-de-teste-do-cofre'

import pytest

import models
import worker
worker.iniciar_worker = lambda: None
import app as portal
from engines import dominio_fila as fila

A, B, C = '12345678000195', '11222333000181', '44555666000199'


@pytest.fixture
def cli(tmp_path):
    models.DB = str(tmp_path / 't.db')
    models.init_db()
    with models.con() as c:
        for cnpj, nome in ((A, 'EMPRESA A'), (B, 'EMPRESA B'), (C, 'EMPRESA C')):
            c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES(?,?,'SP','35',1,1)", (cnpj, nome))
    fila.salvar_empresa(A, ativo=True, data_inicial='2026-11-01', chave='k-a')
    with models.con() as c:
        c.execute("INSERT INTO dominio_envios(cnpj,doc,arquivo,estado) VALUES(?,'NFe','x.xml','NOVO')", (A,))
    t = portal.app.test_client()
    with t.session_transaction() as s:
        s['uid'] = 1; s['nome'] = 'Admin'; s['papel'] = 'admin'
    return t


def _cnpjs():
    with models.con() as c:
        return {r['cnpj'] for r in c.execute('SELECT cnpj FROM empresas')}


def _dominio(cnpj):
    with models.con() as c:
        return (c.execute('SELECT COUNT(*) FROM dominio_empresa WHERE cnpj=?', (cnpj,)).fetchone()[0],
                c.execute('SELECT COUNT(*) FROM dominio_envios WHERE cnpj=?', (cnpj,)).fetchone()[0])


def test_excluir_um_limpa_dominio(cli):
    with models.con() as c:
        eid = c.execute('SELECT id FROM empresas WHERE cnpj=?', (A,)).fetchone()[0]
    r = cli.get('/clientes/%d/excluir' % eid)  # confirmacao na pagina, sem confirm() do navegador
    assert r.status_code == 200 and 'Excluir 1 cliente(s)' in r.get_data(as_text=True) and A in _cnpjs()
    assert cli.post('/clientes/%d/excluir' % eid).status_code == 200 and A in _cnpjs()  # sem confirmar=1
    assert cli.post('/clientes/%d/excluir' % eid, data={'confirmar': '1'}).status_code == 302
    assert A not in _cnpjs() and _dominio(A) == (0, 0)


def test_botao_excluir_nao_depende_de_confirm(cli):
    html = cli.get('/clientes').get_data(as_text=True)
    assert 'confirm(' not in html and '/excluir"' in html


def test_lote_confere_antes_de_excluir(cli):
    lista = '12.345.678/0001-95\n11222333000181\n99.999.999/0001-99'
    r = cli.post('/clientes/excluir-lote', data={'cnpjs': lista})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'Excluir 2 cliente(s)' in html and '99999999000199' in html
    assert _cnpjs() == {A, B, C}  # so conferiu
    r = cli.post('/clientes/excluir-lote', data={'cnpjs': lista, 'confirmar': '1'})
    assert r.status_code == 302 and _cnpjs() == {C} and _dominio(A) == (0, 0)


def test_lote_sem_nenhum_da_base(cli):
    assert cli.post('/clientes/excluir-lote', data={'cnpjs': '99999999000199'}).status_code == 302
    assert _cnpjs() == {A, B, C}


def test_operador_nao_exclui_em_lote(cli):
    with models.con() as c:
        uid = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,ativo,criado) "
                        "VALUES('op','x','Op','operador',1,'x')").lastrowid
    op = portal.app.test_client()
    with op.session_transaction() as s:
        s['uid'] = uid; s['papel'] = 'operador'
    op.post('/clientes/excluir-lote', data={'cnpjs': A, 'confirmar': '1'})
    assert A in _cnpjs()
