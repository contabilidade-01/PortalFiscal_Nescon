# -*- coding: utf-8 -*-
"""Importacao manual de XML de vendas (NF-e 55 / NFC-e 65) — ex.: NFC-e da Bahia,
que a SEFAZ nao entrega ao portal."""
import os, sys, io, types, tempfile, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())

from werkzeug.security import generate_password_hash

import models
import worker
worker.iniciar_worker = lambda: None
import app as portal
from engines import importar_xml, conferencia

CNPJ = '11222333000181'        # "Alzirao" (BA)
OUTRO = '44555666000199'


def _chave(mod, n, cnpj=CNPJ):
    return '29' + '2609' + cnpj + mod + '001' + '%09d' % n + '1' + '%08d' % n + '0'


def _venda(mod, n, cstat='100', emit=CNPJ, valor='10.00', dh='2026-09-15T10:00:00-03:00'):
    ch = _chave(mod, n, emit)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe%s" versao="4.00">'
            '<ide><mod>%s</mod><serie>1</serie><nNF>%d</nNF><dhEmi>%s</dhEmi></ide>'
            '<emit><CNPJ>%s</CNPJ><xNome>ALZIRAO</xNome></emit><total><ICMSTot><vNF>%s</vNF></ICMSTot></total>'
            '</infNFe></NFe><protNFe versao="4.00"><infProt><chNFe>%s</chNFe><cStat>%s</cStat>'
            '<nProt>129260000000001</nProt></infProt></protNFe></nfeProc>'
            % (ch, mod, n, dh, emit, valor, ch, cstat)).encode()


def _cancel(mod, n, cstat='135'):
    ch = _chave(mod, n)
    return ('<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00"><evento versao="1.00">'
            '<infEvento Id="ID110111%s01"><chNFe>%s</chNFe><dhEvento>2026-09-16T09:00:00-03:00</dhEvento>'
            '<tpEvento>110111</tpEvento><nSeqEvento>1</nSeqEvento></infEvento></evento>'
            '<retEvento versao="1.00"><infEvento><cStat>%s</cStat><nProt>129260000000002</nProt></infEvento>'
            '</retEvento></procEventoNFe>' % (ch, ch, cstat)).encode()


def _zip(**arqs):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        for nome, dados in arqs.items():
            z.writestr(nome.replace('__', '/') + '.xml', dados)
    return b.getvalue()


def _arquivos(base, *partes):
    d = os.path.join(base, CNPJ, '2026-09', *partes)
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_importa_nfce_e_nfe_de_venda_no_mes_certo():
    base = tempfile.mkdtemp()
    models.DB = os.path.join(base, 't.db'); models.init_db()
    r = importar_xml.importar(CNPJ, [('vendas.zip', _zip(pasta__a=_venda('65', 1), b=_venda('55', 2)))], base)
    assert r.importadas == {'NFe': 1, 'NFCe': 1}
    assert _arquivos(base, 'NFCe', '01_venda') == ['%s.xml' % _chave('65', 1)]
    assert _arquivos(base, 'NFe', '04_saida') == ['%s_procNFe_v4.00.xml' % _chave('55', 2)]
    # Mesmo lote de novo: nada duplicado
    r2 = importar_xml.importar(CNPJ, [('vendas.zip', _zip(a=_venda('65', 1), b=_venda('55', 2)))], base)
    assert r2.total_importadas == 0 and r2.repetidas == 2


def test_recusa_compra_nota_nao_autorizada_e_lixo():
    base = tempfile.mkdtemp()
    models.DB = os.path.join(base, 't.db'); models.init_db()
    r = importar_xml.importar(CNPJ, [('compra.xml', _venda('55', 3, emit=OUTRO)),
                                     ('rejeitada.xml', _venda('65', 4, cstat='539')),
                                     ('lixo.xml', b'<nada/>'), ('quebrado.xml', b'<nfeProc'),
                                     ('leiame.txt', b'oi')], base)
    assert r.total_importadas == 0
    assert (r.outro_emitente, r.nao_autorizadas, r.invalidos) == (1, 1, 2)
    assert not os.path.isdir(os.path.join(base, CNPJ, '2026-09'))


def test_cancelada_fica_fora_do_faturamento():
    base = tempfile.mkdtemp()
    models.DB = os.path.join(base, 't.db'); models.init_db()
    # Lote 1: duas NFC-e validas. Lote 2: cancelamento da segunda.
    importar_xml.importar(CNPJ, [('a.xml', _venda('65', 1, valor='10.00')),
                                 ('b.xml', _venda('65', 2, valor='99.00'))], base)
    r = importar_xml.importar(CNPJ, [('c.xml', _cancel('65', 2))], base)
    assert r.eventos == 1 and r.canceladas == 1
    assert _arquivos(base, 'NFCe', '01_venda') == ['%s.xml' % _chave('65', 1)]
    assert _arquivos(base, 'NFCe', '09_cancelada') == ['%s.xml' % _chave('65', 2)]
    # Cancelamento e venda no mesmo lote: a venda ja vai direto para 09_cancelada
    r = importar_xml.importar(CNPJ, [('d.xml', _venda('65', 3)), ('e.xml', _cancel('65', 3))], base)
    assert r.canceladas == 1 and len(_arquivos(base, 'NFCe', '09_cancelada')) == 2
    antes, conferencia.SAIDA = conferencia.SAIDA, base
    try:
        conf = [x for x in conferencia.conferencia([CNPJ]) if x['modelo'] == 'NFCe']
        # Auditoria de numeracao: as canceladas ocupam o numero (sem buraco falso na sequencia)
        aud = conferencia.auditoria_numeracao([CNPJ], modelos={'NFCe'})
    finally:
        conferencia.SAIDA = antes
    assert sum(x['valor'] for x in conf) == 10.0 and sum(x['canceladas'] for x in conf) == 2
    assert aud and all(not x['quebra'] and x['canceladas'] == 2 for x in aud), aud


def test_nfe_importada_sai_da_fila_de_chaves():
    base = tempfile.mkdtemp()
    models.DB = os.path.join(base, 't.db'); models.init_db()
    ch = _chave('55', 7)
    with models.con() as c:
        c.execute("INSERT INTO chaves_saida(chave,cnpj,status,tentativas) VALUES(?,?,'pendente',0)", (ch, CNPJ))
    importar_xml.importar(CNPJ, [('n.xml', _venda('55', 7))], base)
    assert models.chaves_pendentes(CNPJ) == []


def _cli(perms, escopo=True):
    models.DB = os.path.join(tempfile.mkdtemp(), 't.db'); models.init_db()
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo,puxa_nfce) VALUES(?,'ALZIRAO','BA','29',0,1,0)",
                  (CNPJ,))
        uid = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) "
                        "VALUES('op',?,'Op','operador',0,1,'x')", (generate_password_hash('x'),)).lastrowid
        if escopo:
            c.execute('INSERT INTO usuario_empresas(user_id,empresa_id) SELECT ?, id FROM empresas', (uid,))
    models.salvar_permissoes(uid, perms)
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = uid; s['nome'] = 'Op'; s['papel'] = 'operador'
    return cli


def _envia(cli):
    return cli.post('/ambiente/nfce/importar', content_type='multipart/form-data',
                    data={'cnpj': CNPJ, 'arquivos': [(io.BytesIO(_venda('65', 11)), 'n.xml')]})


def _tem_nfce():
    d = os.path.join(portal.SAIDA, CNPJ, '2026-09', 'NFCe', '01_venda')
    return os.path.isfile(os.path.join(d, '%s.xml' % _chave('65', 11)))


def test_tela_importa_para_empresa_da_bahia_com_permissao():
    cli = _cli(['importar_xml'])
    html = cli.get('/ambiente/nfce').data.decode()
    assert 'Importar XML de vendas' in html and 'Alzirao' in html   # aparece mesmo sem NFC-e marcada
    _envia(cli)
    assert _tem_nfce()
    with models.con() as c:
        assert c.execute("SELECT parada FROM execucoes WHERE cnpj=?", (CNPJ,)).fetchone()['parada'] == 'importado'


def test_tela_exige_permissao_e_escopo():
    import shutil
    shutil.rmtree(os.path.join(portal.SAIDA, CNPJ), ignore_errors=True)
    cli = _cli([])
    assert 'Importar XML de vendas' not in cli.get('/ambiente/nfce').data.decode()
    _envia(cli)
    assert not _tem_nfce()
    _envia(_cli(['importar_xml'], escopo=False))
    assert not _tem_nfce()
