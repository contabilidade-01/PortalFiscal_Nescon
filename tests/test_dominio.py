# -*- coding: utf-8 -*-
"""Envio de XML para a Dominio: varredura, fila, erros, cancelamento e tela.
Nada fala com a API real: o cliente HTTP e trocado por um falso."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())
os.environ['DOMINIO_KEY'] = 'chave-de-teste-do-cofre'
os.environ.pop('DOMINIO_ENVIO_ATIVO', None)
os.environ.pop('DOMINIO_SIMULADO', None)

import pytest
import requests

import models
import worker
worker.iniciar_worker = lambda: None
import app as portal
from engines import dominio_api as api, dominio_fila as fila

CNPJ = '12345678000195'
CH1 = '35261112345678000195550010000000011000000011'   # NF-e 44
CH2 = '35261112345678000195550010000000021000000022'
NFSE = '3550308' + '2' * 43                              # NFS-e 50


class Falso:
    """Cliente da API em memoria. `erros` = excecoes a levantar no proximo enviar()."""
    def __init__(self):
        self.enviados, self.erros, self.lotes = [], [], {}
        self.ativacoes, self.erro_ativar = [], None

    def ativar_chave(self, chave):
        if self.erro_ativar:
            raise self.erro_ativar
        self.ativacoes.append(chave)
        return 'envio:' + chave

    def enviar(self, conteudo, nome, chave):
        if self.erros:
            raise self.erros.pop(0)
        self.enviados.append((nome, chave, conteudo))
        lid = 'L%d' % len(self.enviados)
        self.lotes[lid] = 'EA00'
        return lid

    def consultar(self, lote_id, chave):
        return {'id': lote_id, 'filesExpanded': [{'apiStatus': {'code': self.lotes[lote_id], 'message': 'm'}}]}


def _xml(path, texto):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(texto)
    return path


@pytest.fixture
def base(tmp_path, monkeypatch):
    models.DB = str(tmp_path / 't.db')
    models.init_db()
    saida = str(tmp_path / 'XML')
    from engines import nfse
    monkeypatch.setattr(nfse, 'SAIDA', saida)
    monkeypatch.setenv('DOMINIO_CODIGOS', '{"EA00":"CONFIRMADO","EA99":"RECUSADO"}')
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES(?,?,'SP','35',1,1)", (CNPJ, 'EMPRESA A'))
    fila.salvar_empresa(CNPJ, ativo=True, data_inicial='2026-11-01', chave='chave-onvio-a')
    r = os.path.join(saida, CNPJ)
    _xml(os.path.join(r, '2026-11', 'NFe', '04_saida', CH1 + '_procNFe_v4.00.xml'),
         '<nfeProc><NFe><infNFe Id="NFe%s"><ide><dhEmi>2026-11-03T10:00:00-03:00</dhEmi></ide></infNFe></NFe></nfeProc>' % CH1)
    _xml(os.path.join(r, '2026-11', 'NFSe', '01_tomado', NFSE + '.xml'),
         '<NFSe><infNFSe Id="NFS%s"><dhProc>2026-11-04T08:00:00</dhProc></infNFSe></NFSe>' % NFSE)
    _xml(os.path.join(r, '2026-11', 'NFe', '02_resumo', CH2 + '_resNFe.xml'), '<resNFe><chNFe>%s</chNFe></resNFe>' % CH2)
    _xml(os.path.join(r, '2026-11', 'NFe', '03_eventos', CH1 + '_210210_1.xml'),
         '<procEventoNFe><tpEvento>210210</tpEvento><dhEvento>2026-11-05T00:00:00</dhEvento></procEventoNFe>')
    _xml(os.path.join(r, '2026-10', 'NFe', '04_saida', CH2 + '_procNFe_v4.00.xml'),
         '<nfeProc><infNFe Id="NFe%s"><dhEmi>2026-10-20T10:00:00-03:00</dhEmi></infNFe></nfeProc>' % CH2)
    return r


def _linhas():
    with models.con() as c:
        return {r['arquivo']: dict(r) for r in c.execute('SELECT * FROM dominio_envios')}


def test_varredura_pega_so_o_que_deve(base):
    assert fila.varrer() == 2
    l = _linhas()
    assert set(l) == {CH1 + '_procNFe_v4.00.xml', NFSE + '.xml'}   # sem resumo, sem ciencia, sem outubro
    assert l[CH1 + '_procNFe_v4.00.xml']['chave'] == CH1
    assert l[NFSE + '.xml']['chave'] == NFSE
    assert fila.varrer() == 0  # idempotente


def test_chave_fica_cifrada(base):
    with models.con() as c:
        v = c.execute('SELECT chave_cifrada FROM dominio_empresa WHERE cnpj=?', (CNPJ,)).fetchone()[0]
    assert v and 'chave-onvio-a' not in v and fila.decifrar(v) == 'chave-onvio-a'


def test_envio_e_confirmacao(base):
    fila.varrer(); cli = Falso()
    assert fila.processar(10, cliente=cli) == 2
    assert {e[1] for e in cli.enviados} == {'envio:chave-onvio-a'}  # chave trocada em activation/enable
    assert cli.ativacoes == ['chave-onvio-a']  # uma unica ativacao para as 2 notas
    assert all(r['estado'] == 'ENVIADO' for r in _linhas().values())
    with models.con() as c:
        c.execute("UPDATE dominio_envios SET proximo=NULL")
    fila.consultar(cliente=cli)
    assert all(r['estado'] == 'CONFIRMADO' for r in _linhas().values())
    assert fila.processar(10, cliente=cli) == 0  # nada e reenviado


def test_codigo_desconhecido_nunca_confirma(base, monkeypatch):
    fila.varrer(); cli = Falso(); fila.processar(1, cliente=cli)
    cli.lotes['L1'] = 'XYZ'
    monkeypatch.setattr(fila, 'MAX_CONSULTAS', 2)
    for _ in range(2):
        with models.con() as c:
            c.execute("UPDATE dominio_envios SET proximo=NULL")
        fila.consultar(cliente=cli)
    assert [r['estado'] for r in _linhas().values() if r['lote_id']] == ['INCERTO']


@pytest.mark.parametrize('erro,estado', [
    (api.DominioPausar('401', 401), 'PAUSADO'),
    (api.DominioRepetir('429', 429, 120), 'REPETIR'),
    (api.DominioRecusado('400', 400), 'RECUSADO'),
    (api.DominioIncerto('caiu'), 'INCERTO'),
])
def test_erros_viram_estado(base, erro, estado):
    fila.varrer(); cli = Falso(); cli.erros = [erro]
    fila.processar(1, cliente=cli)
    assert estado in [r['estado'] for r in _linhas().values()]


def test_incerto_so_volta_com_conferencia(base):
    fila.varrer(); cli = Falso(); cli.erros = [api.DominioIncerto('caiu')]
    fila.processar(1, cliente=cli)
    eid = next(r['id'] for r in _linhas().values() if r['estado'] == 'INCERTO')
    with pytest.raises(ValueError):
        fila.pedir_reenvio(eid)
    fila.pedir_reenvio(eid, conferido=True)
    assert next(r for r in _linhas().values() if r['id'] == eid)['estado'] == 'REPETIR'


def test_xml_alterado_e_recusado(base):
    fila.varrer(); cli = Falso(); cli.erros = [api.DominioRepetir('503', 503, 30)]
    fila.processar(1, cliente=cli)
    d = next(r for r in _linhas().values() if r['estado'] == 'REPETIR')
    with open(d['caminho'], 'a', encoding='utf-8') as f:
        f.write('<!-- mudou -->')
    with models.con() as c:
        c.execute("UPDATE dominio_envios SET proximo=NULL WHERE id=?", (d['id'],))
    fila.processar(5, cliente=cli)
    assert next(r for r in _linhas().values() if r['id'] == d['id'])['estado'] == 'RECUSADO'


def test_cancelamento_espera_a_nota(base):
    _xml(os.path.join(base, '2026-11', 'NFe', '03_eventos', CH1 + '_110111_1.xml'),
         '<procEventoNFe><chNFe>%s</chNFe><tpEvento>110111</tpEvento><dhEvento>2026-11-06T00:00:00</dhEvento></procEventoNFe>' % CH1)
    fila.varrer(); cli = Falso()
    cli.erros = [api.DominioRepetir('503', 503, 30)]
    fila.processar(10, cliente=cli)  # nota de CH1 falha -> evento nao pode sair
    assert not any('110111' in e[0] for e in cli.enviados)
    with models.con() as c:
        c.execute("UPDATE dominio_envios SET proximo=NULL")
    fila.processar(10, cliente=cli)
    assert not any('110111' in e[0] for e in cli.enviados)  # nota enviada, ainda sem resultado
    with models.con() as c:
        c.execute("UPDATE dominio_envios SET proximo=NULL")
    fila.consultar(cliente=cli)
    fila.processar(10, cliente=cli)
    assert any('110111' in e[0] for e in cli.enviados)
    assert _linhas()[CH1 + '_110111_1.xml']['tipo'] == 'cancelamento'


def test_nota_movida_para_cancelada_nao_duplica(base):
    fila.varrer()
    antigo = os.path.join(base, '2026-11', 'NFe', '04_saida', CH1 + '_procNFe_v4.00.xml')
    novo = os.path.join(base, '2026-11', 'NFe', '09_cancelada', CH1 + '_procNFe_v4.00.xml')
    os.makedirs(os.path.dirname(novo)); os.replace(antigo, novo)
    fila.varrer()
    l = _linhas()[CH1 + '_procNFe_v4.00.xml']
    assert l['caminho'] == novo and l['sub'] == '09_cancelada' and len(_linhas()) == 2


def test_sem_chave_pausa(base):
    fila.salvar_empresa(CNPJ, ativo=False)
    fila.varrer(); fila.salvar_empresa(CNPJ, limpar_chave=True)
    with models.con() as c:
        c.execute("UPDATE dominio_empresa SET ativo=1 WHERE cnpj=?", (CNPJ,))
    fila.varrer(); fila.processar(5, cliente=Falso())
    assert {r['estado'] for r in _linhas().values()} == {'PAUSADO'}


def test_importar_chaves(base):
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES('11222333000181','B','SP','35',1,1)")
    ok, erros = fila.importar_chaves('CNPJ;chave\n11.222.333/0001-81;k2;2026-11-01\n99999999000199;x\nlixo',
                                     ligar=True)
    assert ok == 1 and len(erros) == 2


def test_tela_e_permissao(base):
    fila.varrer()
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = 1; s['nome'] = 'Admin'; s['papel'] = 'admin'
    r = cli.get('/dominio')
    assert r.status_code == 200 and 'Envio de XML para a Domínio' in r.get_data(as_text=True)
    assert 'chave-onvio-a' not in r.get_data(as_text=True)
    assert cli.post('/dominio/varrer', data={}).status_code == 400  # sem CSRF
    with cli.session_transaction() as s:
        tok = s['_csrf']
    assert cli.post('/dominio/varrer', data={'_csrf': tok}).status_code == 302
    with models.con() as c:  # operador sem a permissao nao entra
        uid = c.execute("INSERT INTO usuarios(login,senha_hash,nome,papel,ativo,criado) "
                        "VALUES('op','x','Op','operador',1,'x')").lastrowid
    op = portal.app.test_client()
    with op.session_transaction() as s:
        s['uid'] = uid; s['papel'] = 'operador'
    assert op.get('/dominio').status_code == 302


class _Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}
        self.content = b'x'; self.text = str(body)

    def json(self):
        if self._b is None:
            raise ValueError()
        return self._b


class _Sessao:
    def __init__(self, respostas):
        self.r, self.chamadas, self.trust_env = list(respostas), [], True

    def post(self, url, **kw):
        self.chamadas.append(('POST', url, kw)); return self.r.pop(0)

    def request(self, metodo, url, **kw):
        self.chamadas.append((metodo, url, kw))
        r = self.r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _cfg(**kw):
    c = api.config(); c.update(client_id='id', client_secret='sec', simulado=False); c.update(kw); return c


def test_cliente_http_envio():
    s = _Sessao([_Resp(200, {'access_token': 'T', 'expires_in': 3600}), _Resp(201, {'id': 'B1'})])
    cli = api.DominioClient(_cfg(versao='v3'), session=s)
    assert cli.enviar(b'<x/>', 'a.xml', 'K') == 'B1'
    tok = s.chamadas[0]
    assert tok[2]['data']['audience'] == api.AUDIENCE_ONVIO
    assert tok[2]['data']['grant_type'] == 'client_credentials' and tok[2]['headers']['Authorization'].startswith('Basic ')
    metodo, url, kw = s.chamadas[1]
    assert url.endswith('/dominio/invoice/v3/batches')
    assert kw['headers']['Authorization'] == 'Bearer T' and kw['headers']['x-integration-key'] == 'K'
    assert 'file[]' in kw['files'] and kw['files']['query'][1] == '{"boxeFile": false}'


@pytest.mark.parametrize('resp,exc', [
    (_Resp(401, {}), api.DominioPausar),
    (_Resp(429, {}, {'Retry-After': '90'}), api.DominioRepetir),
    (_Resp(500, {}), api.DominioIncerto),
    (_Resp(400, {'message': 'XML invalido'}), api.DominioRecusado),
    (requests.ConnectionError(), api.DominioIncerto),
    (_Resp(201, {}), api.DominioIncerto),
])
def test_cliente_http_erros(resp, exc):
    s = _Sessao([_Resp(200, {'access_token': 'T'}), resp])
    with pytest.raises(exc):
        api.DominioClient(_cfg(), session=s).enviar(b'<x/>', 'a.xml', 'K')


def test_sem_credenciais_pausa():
    with pytest.raises(api.DominioPausar):
        api.DominioClient(_cfg(client_id='', client_secret=''), session=_Sessao([])).token()


def test_chave_de_envio_e_guardada_e_reusada(base):
    fila.varrer(); cli = Falso()
    fila.processar(1, cliente=cli)
    with models.con() as c:
        v = c.execute('SELECT chave_envio_cifrada FROM dominio_empresa WHERE cnpj=?', (CNPJ,)).fetchone()[0]
    assert v and fila.decifrar(v) == 'envio:chave-onvio-a'
    cli2 = Falso()  # novo processo: nao ativa de novo
    fila.processar(5, cliente=cli2)
    assert cli2.ativacoes == [] and {e[1] for e in cli2.enviados} == {'envio:chave-onvio-a'}


def test_trocar_chave_refaz_ativacao(base):
    fila.varrer(); fila.processar(1, cliente=Falso())
    fila.salvar_empresa(CNPJ, chave='chave-nova')
    cli = Falso(); fila.processar(5, cliente=cli)
    assert cli.ativacoes == ['chave-nova']


def test_ativacao_recusada_pausa_empresa(base):
    fila.varrer(); cli = Falso(); cli.erro_ativar = api.DominioRecusado('chave invalida', 400)
    fila.processar(5, cliente=cli)
    assert {r['estado'] for r in _linhas().values()} == {'PAUSADO'} and not cli.enviados
    assert 'não ativou a chave' in next(iter(_linhas().values()))['mensagem']


def test_sem_ativacao_envia_chave_do_cliente(base, monkeypatch):
    monkeypatch.setenv('DOMINIO_ATIVAR_CHAVE', '0')
    fila.varrer(); cli = Falso(); fila.processar(5, cliente=cli)
    assert cli.ativacoes == [] and {e[1] for e in cli.enviados} == {'chave-onvio-a'}


def test_audience_padrao_e_desligavel(monkeypatch):
    monkeypatch.delenv('DOMINIO_AUDIENCE', raising=False)
    assert api.config()['audience'] == api.AUDIENCE_ONVIO
    monkeypatch.setenv('DOMINIO_AUDIENCE', '-')
    assert api.config()['audience'] == ''


def test_ativacao_http():
    s = _Sessao([_Resp(200, {'access_token': 'T'}), _Resp(200, {'integrationKey': 'K2'})])
    assert api.DominioClient(_cfg(), session=s).ativar_chave('K1') == 'K2'
    metodo, url, kw = s.chamadas[1]
    assert url.endswith('/dominio/integration/v1/activation/enable') and kw['headers']['x-integration-key'] == 'K1'
