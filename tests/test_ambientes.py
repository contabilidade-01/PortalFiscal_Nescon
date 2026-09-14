# -*- coding: utf-8 -*-
"""Ambientes de captura segregados: NF-e Entradas, NFS-e Emitidas, NFS-e Tomados, NFC-e.
Stub de xmlsec p/ importar worker sem a lib nativa."""
import os, sys, types, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))

import models
import worker
from engines import backup


def _db_legado():
    """Banco no formato ANTIGO (so puxa_nfse) -> init_db deve migrar."""
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    c = sqlite3.connect(models.DB)
    c.execute('''CREATE TABLE empresas(id INTEGER PRIMARY KEY AUTOINCREMENT, cnpj TEXT UNIQUE, nome TEXT,
                 ativo INTEGER DEFAULT 1, senha_ok INTEGER DEFAULT 0, puxa_nfe INTEGER DEFAULT 1,
                 puxa_nfse INTEGER DEFAULT 1)''')
    c.execute("INSERT INTO empresas(cnpj,nome,senha_ok,puxa_nfse) VALUES('11111111000111','COM NFSE',1,1)")
    c.execute("INSERT INTO empresas(cnpj,nome,senha_ok,puxa_nfse) VALUES('22222222000122','SEM NFSE',1,0)")
    c.commit(); c.close()
    models.init_db()


def _emp(cnpj):
    with models.con() as c:
        return c.execute('SELECT * FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()


def test_migracao_copia_puxa_nfse_para_emitidas_e_tomados():
    _db_legado()
    a, b = _emp('11111111000111'), _emp('22222222000122')
    assert (a['puxa_nfse_prestado'], a['puxa_nfse_tomado']) == (1, 1)
    assert (b['puxa_nfse_prestado'], b['puxa_nfse_tomado']) == (0, 0)  # nao herdou o DEFAULT 1
    # rodar init_db de novo NAO pode re-sobrescrever marcacoes feitas depois
    with models.con() as c:
        c.execute("UPDATE empresas SET puxa_nfse_tomado=0 WHERE cnpj='11111111000111'")
    models.init_db()
    assert _emp('11111111000111')['puxa_nfse_tomado'] == 0


def test_varredura_nfse_entra_com_emitidas_ou_tomados():
    _db_legado()
    with models.con() as c:
        c.execute("UPDATE empresas SET puxa_nfse_prestado=0, puxa_nfse_tomado=1 WHERE cnpj='11111111000111'")
        c.execute("UPDATE empresas SET puxa_nfse_prestado=0, puxa_nfse_tomado=0 WHERE cnpj='22222222000122'")
    cnpjs = {e['cnpj'] for e in worker._empresas('todas', 'puxa_nfse')}
    assert cnpjs == {'11111111000111'}  # so tomados basta; nenhum dos dois fica fora


def test_sync_puxa_nfse_espelho_legado():
    _db_legado()
    with models.con() as c:
        c.execute("UPDATE empresas SET puxa_nfse_prestado=1, puxa_nfse_tomado=0 WHERE cnpj='22222222000122'")
        c.execute("UPDATE empresas SET puxa_nfse_prestado=0, puxa_nfse_tomado=0 WHERE cnpj='11111111000111'")
        models.sync_puxa_nfse(c)
    assert _emp('22222222000122')['puxa_nfse'] == 1
    assert _emp('11111111000111')['puxa_nfse'] == 0


def test_catalogo_ambientes():
    chaves = [a['chave'] for a in models.AMBIENTES]
    assert chaves == ['nfe_entradas', 'nfse_emitidas', 'nfse_tomados', 'nfce']
    assert models.AMBIENTE_POR_CHAVE['nfse_tomados']['subs'] == ('01_tomado',)
    assert models.AMBIENTE_POR_CHAVE['nfse_emitidas']['subs'] == ('02_prestado',)
    assert 'nfe_saidas' not in models.TIPO_PARA_PERM


def test_saidas_escritorio_desligada_cancela_fila():
    _db_legado()
    assert models.SAIDAS_ESCRITORIO_ATIVO is False
    worker.enfileirar('nfe_saidas', origem='retomada')
    assert worker.cancelar_saidas_desligadas() == 1
    with models.con() as c:
        assert c.execute("SELECT status FROM jobs WHERE tipo='nfe_saidas'").fetchone()[0] == 'cancelado'


def test_restaurar_backup_antigo_nao_marca_nfse_indevida():
    _db_legado()  # destino ja migrado (tem as colunas novas)
    src = sqlite3.connect(':memory:'); src.row_factory = sqlite3.Row
    src.execute('CREATE TABLE empresas(id INTEGER PRIMARY KEY, cnpj TEXT, nome TEXT, puxa_nfe INTEGER, puxa_nfse INTEGER)')
    src.execute("INSERT INTO empresas VALUES(1,'33333333000133','NOVA SEM NFSE',1,0)")
    src.execute("INSERT INTO empresas VALUES(2,'11111111000111','COM NFSE',1,1)")
    with models.con() as dst:
        n = backup._upsert_empresas(src, dst, tempfile.mkdtemp())
    assert n == 2
    novo = _emp('33333333000133')
    assert (novo['puxa_nfse_prestado'], novo['puxa_nfse_tomado']) == (0, 0)
    assert _emp('11111111000111')['puxa_nfse_tomado'] == 1
