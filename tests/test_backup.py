# -*- coding: utf-8 -*-
import os, sys, sqlite3, tempfile, shutil, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines import backup as bak

SCHEMA = '''
CREATE TABLE empresas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, cnpj TEXT UNIQUE, nome TEXT,
  arquivo TEXT, senha TEXT, senha_ok INTEGER DEFAULT 0, validade TEXT,
  ativo INTEGER DEFAULT 1, puxa_nfe INTEGER DEFAULT 1);
CREATE TABLE usuarios(
  id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT UNIQUE, senha_hash TEXT,
  nome TEXT, papel TEXT);
CREATE TABLE parametros(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE ciencia_dada(
  cnpj TEXT, chNFe TEXT, cStat TEXT, nProt TEXT, quando TEXT,
  PRIMARY KEY(cnpj, chNFe));
CREATE TABLE ncm_monofasico(
  ncm TEXT PRIMARY KEY, categoria TEXT, base_legal TEXT, origem TEXT);
CREATE TABLE pgdas_recibos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, cnpj TEXT, ano INTEGER, mes INTEGER,
  receita_total REAL, anexo TEXT, arquivo TEXT, parsed_em TEXT, hash_linha TEXT,
  UNIQUE(cnpj,ano,mes,hash_linha));
'''


def _init(path, empresas=(), office=None, user='admin'):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel) VALUES(?,?,?,?)",
              (user, 'hash-' + user, user, 'admin'))
    for e in empresas:
        c.execute('INSERT INTO empresas(cnpj,nome,arquivo,senha,senha_ok) VALUES(?,?,?,?,1)', e)
    if office:
        c.execute("INSERT INTO parametros(chave,valor) VALUES('office_arquivo',?)", (office,))
    c.commit(); c.close()


def test_backup_sem_xml_e_preserva_admin():
    root = tempfile.mkdtemp()
    try:
        origem = os.path.join(root, 'pc')
        dest = os.path.join(root, 'server')
        for p in (origem, dest):
            os.makedirs(os.path.join(p, 'Certificados'))
            os.makedirs(os.path.join(p, 'XML'))
        pfx_pc = os.path.join(origem, 'Certificados', 'empresa.pfx')
        open(pfx_pc, 'wb').write(b'PFX-FAKE')
        xml_pc = os.path.join(origem, 'XML', '111', '2026-07', 'NFe', '01_entrada')
        os.makedirs(xml_pc)
        open(os.path.join(xml_pc, 'n1.xml'), 'w').write('<nfe/>')
        db_pc = os.path.join(origem, 'portal_fiscal.db')
        _init(db_pc, empresas=[('111', 'ACME', pfx_pc, 's3nha')], office=pfx_pc)
        zip_path = os.path.join(root, 'b.zip')
        bak.criar_zip(zip_path, db_pc, os.path.join(origem, 'Certificados'))
        with zipfile.ZipFile(zip_path) as zf:
            nomes = zf.namelist()
        assert not any(n.replace('\\', '/').startswith('XML/') for n in nomes)

        db_sv = os.path.join(dest, 'portal_fiscal.db')
        _init(db_sv, user='servidor')
        r = bak.restaurar_zip(zip_path, db_sv, os.path.join(dest, 'Certificados'))
        assert r['empresas'] == 1
        assert r['pfx'] == 1
        assert 'xml' not in r
        c = sqlite3.connect(db_sv); c.row_factory = sqlite3.Row
        emp = c.execute('SELECT * FROM empresas WHERE cnpj=?', ('111',)).fetchone()
        assert emp['nome'] == 'ACME'
        assert emp['senha'] == 's3nha'
        assert os.path.basename(emp['arquivo']) == 'empresa.pfx'
        assert os.path.isfile(os.path.join(dest, 'Certificados', 'empresa.pfx'))
        assert not os.path.isfile(os.path.join(dest, 'XML', '111', '2026-07', 'NFe', '01_entrada', 'n1.xml'))
        off = c.execute("SELECT valor FROM parametros WHERE chave='office_arquivo'").fetchone()['valor']
        assert 'Certificados' in off.replace('\\', '/')
        users = [u['login'] for u in c.execute('SELECT login FROM usuarios')]
        assert users == ['servidor']
        c.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_resumo_e_fmt():
    assert 'KB' in bak.fmt_bytes(2048) or 'B' in bak.fmt_bytes(10)


if __name__ == '__main__':
    test_backup_sem_xml_e_preserva_admin()
    test_resumo_e_fmt()
    print('ok')
