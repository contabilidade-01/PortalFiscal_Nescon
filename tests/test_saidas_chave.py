# -*- coding: utf-8 -*-
"""NF-e de SAÍDA por chave (consChNFe). A distribuição por NSU não devolve ao emitente o
que ele emitiu (NT 2014.002), mas o consChNFe entrega ao participante — e o emitente é.
Nunca bate na SEFAZ real: o _post é interceptado."""
import os, sys, types, tempfile, base64, gzip
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))

import models
from engines import nfe

CNPJ = '52747415000127'
CHAVE = '35260952747415000127550010000000051300000054'   # 13 VOLTS, NF-e 5 (real)
OUTRA = '35260852747415000127550010000000041300000045'


def _db():
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    models.init_db()
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo,arquivo,senha,puxa_nfe_saida) "
                  "VALUES(?,?,'SP','35',1,1,'x.pfx','x',1)", (CNPJ, '13 VOLTS'))
    nfe.SAIDA = os.path.join(d, 'xml')
    return d


def _emp():
    with models.con() as c:
        return c.execute('SELECT * FROM empresas WHERE cnpj=?', (CNPJ,)).fetchone()


def _resposta(chave, cstat='138'):
    """Monta uma resposta de consChNFe igual à da SEFAZ (docZip = gzip+base64)."""
    xml = ('<nfeProc><NFe><infNFe Id="NFe%s"><ide><dhEmi>2026-09-01T10:00:00-03:00</dhEmi></ide>'
           '<emit><CNPJ>%s</CNPJ></emit><dest><CNPJ>10566697000116</CNPJ></dest></infNFe></NFe></nfeProc>'
           % (chave, CNPJ))
    z = base64.b64encode(gzip.compress(xml.encode())).decode()
    return ('<retDistDFeInt><cStat>%s</cStat><docZip NSU="1" schema="procNFe_v4.00.xsd">%s</docZip>'
            '</retDistDFeInt>' % (cstat, z)) if cstat == '138' else \
           '<retDistDFeInt><cStat>%s</cStat></retDistDFeInt>' % cstat


def test_validacao_de_chave():
    assert models.chave_valida(CHAVE)
    assert not models.chave_valida(CHAVE[:-1] + '9')      # DV errado
    assert not models.chave_valida('123')


def test_extrai_chaves_de_texto_colado():
    txt = 'Nota 5;%s;R$ 58.500,00\nNota 4;%s;R$ 50.230,00\nrepetida %s' % (CHAVE, OUTRA, CHAVE)
    assert models.extrair_chaves(txt) == [CHAVE, OUTRA]


def test_recusa_chave_de_outro_cnpj():
    _db()
    alheia = '35260952747415000128550010000000051300000054'  # CNPJ diferente no meio da chave
    novas, rep, inval, outro = models.adicionar_chaves(CNPJ, [CHAVE, alheia], origem='manual')
    assert novas == 1 and outro + inval == 1
    assert models.chaves_pendentes(CNPJ) == [CHAVE]


def test_baixa_por_chave_salva_em_04_saida(monkeypatch):
    d = _db()
    models.adicionar_chaves(CNPJ, [CHAVE], origem='manual')
    monkeypatch.setattr(nfe.certs, 'ssl_ctx', lambda a, s: (None, []))
    monkeypatch.setattr(nfe.guard, 'sleep_jitter', lambda s: None)
    pedidos = []
    def _post(ctx, cuf, cnpj, inner):
        pedidos.append(inner)
        return 200, _resposta(CHAVE, '138')
    monkeypatch.setattr(nfe, '_post', _post)
    total, parada = nfe.puxar_saidas_por_chave(_emp())
    assert (total, parada) == (1, 'fim')
    assert '<consChNFe><chNFe>%s</chNFe></consChNFe>' % CHAVE in pedidos[0]
    assert models.resumo_chaves(CNPJ) == {'baixada': 1}
    # o XML foi para a pasta de venda propria, na competencia do dhEmi
    destino = os.path.join(nfe.SAIDA, CNPJ, '2026-09', 'NFe', '04_saida')
    assert os.path.isdir(destino) and len(os.listdir(destino)) == 1


def test_nao_localizada_nao_fica_em_loop(monkeypatch):
    _db()
    models.adicionar_chaves(CNPJ, [CHAVE], origem='manual')
    monkeypatch.setattr(nfe.certs, 'ssl_ctx', lambda a, s: (None, []))
    monkeypatch.setattr(nfe.guard, 'sleep_jitter', lambda s: None)
    monkeypatch.setattr(nfe, '_post', lambda *a: (200, _resposta(CHAVE, '137')))
    total, parada = nfe.puxar_saidas_por_chave(_emp())
    assert total == 0 and parada == 'fim'
    assert models.resumo_chaves(CNPJ) == {'nao_encontrada': 1}
    assert models.chaves_pendentes(CNPJ) == []            # nao volta para a fila


def test_656_para_a_rodada_e_registra_bloqueio(monkeypatch):
    _db()
    models.adicionar_chaves(CNPJ, [CHAVE, OUTRA], origem='manual')
    monkeypatch.setattr(nfe.certs, 'ssl_ctx', lambda a, s: (None, []))
    monkeypatch.setattr(nfe.guard, 'sleep_jitter', lambda s: None)
    monkeypatch.setattr(nfe, '_post', lambda *a: (200, _resposta(CHAVE, '656')))
    total, parada = nfe.puxar_saidas_por_chave(_emp())
    assert (total, parada) == (0, '656')
    with models.con() as c:
        e = c.execute('SELECT bloqueado_nfe_ate FROM empresas WHERE cnpj=?', (CNPJ,)).fetchone()
    assert e['bloqueado_nfe_ate']                          # cooldown gravado pelo guard


def test_sem_chaves_nao_consulta(monkeypatch):
    _db()
    monkeypatch.setattr(nfe, '_post', lambda *a: (_ for _ in ()).throw(AssertionError('não podia consultar')))
    assert nfe.puxar_saidas_por_chave(_emp()) == (0, 'sem_chaves')
