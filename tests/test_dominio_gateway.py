# -*- coding: utf-8 -*-
"""Modo gateway (servidor do fornecedor, como o EXE CentralNFSe): ativacao, envio, resultado em
lote, confirmacoes, erros e reenvio. O gateway e simulado por um servidor HTTP local."""
import hashlib, json, os, sys, tempfile, threading, time, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))
os.environ.setdefault('FISCAL_DATA_DIR', tempfile.mkdtemp())
os.environ['DOMINIO_KEY'] = 'chave-de-teste-do-cofre'

import pytest

import models
import worker
worker.iniciar_worker = lambda: None
import app as portal
from engines import dominio_api as api, dominio_fila as fila, dominio_gateway as G

CNPJ = '12345678000195'
CH1 = '35261112345678000195550010000000011000000011'
TICKET = 'ticket-de-teste-1234'


class Gateway(BaseHTTPRequestHandler):
    ops, log, script, acked, retries, tracked = {}, [], [], [], [], []

    def log_message(self, *a):
        pass

    def _r(self, code, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self):
        return self.headers.get('Authorization', '').endswith('.segredo')

    def do_GET(self):
        Gateway.log.append(('GET', self.path, dict(self.headers), b''))
        if not self._auth_ok():
            return self._r(401, {})
        if self.path == '/health':
            return self._r(200, {'ok': True})
        op = Gateway.ops.get(self.path.rsplit('/', 1)[-1])
        return self._r(200, op) if op else self._r(404, {})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        Gateway.log.append(('POST', self.path, dict(self.headers), body))
        if self.path == '/api/v1/ativacoes':
            d = json.loads(body)
            return self._r(200, {'installation_id': d['installation_id'], 'credential': d['installation_id'] + '.segredo',
                                 'expires_at': int(time.time()) + 86400})
        if not self._auth_ok():
            return self._r(401, {})
        if self.path == '/api/v1/envios':
            if Gateway.script:
                code, payload, h = Gateway.script.pop(0)
                return self._r(code, payload, h)
            inst = self.headers['Authorization'][7:].split('.')[0]
            rid = hashlib.sha256(('%s|%s|%s' % (inst, self.headers['X-Company-Cnpj'],
                                                hashlib.sha256(body).hexdigest())).encode()).hexdigest()
            op = {'request_id': rid, 'status': 'RECEBIDO'}
            Gateway.ops[rid] = op
            return self._r(202, op)
        if self.path == '/api/v1/envios/status':
            ids = json.loads(body)['request_ids']
            return self._r(200, {'items': [Gateway.ops[i] for i in ids if i in Gateway.ops],
                                 'missing': [i for i in ids if i not in Gateway.ops]})
        if self.path == '/api/v1/envios/confirmacoes':
            ids = json.loads(body)['request_ids']
            Gateway.acked += ids
            return self._r(200, {'items': [{'request_id': i} for i in ids]})
        if self.path == '/api/v1/envios/acompanhamento':
            Gateway.tracked.append(json.loads(body)['request_id'])
            return self._r(200, dict(Gateway.ops[json.loads(body)['request_id']]))
        if self.path.endswith('/retentativas'):
            Gateway.retries.append(self.path)
            return self._r(404, {})
        return self._r(404, {})


@pytest.fixture(scope='module')
def servidor():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ['DOMINIO_GATEWAY_URL'] = 'http://127.0.0.1:%d' % srv.server_port
    yield srv
    srv.shutdown()
    os.environ.pop('DOMINIO_GATEWAY_URL', None)


@pytest.fixture
def base(tmp_path, monkeypatch, servidor):
    for k in ('ops',):
        getattr(Gateway, k).clear()
    for k in ('log', 'script', 'acked', 'retries', 'tracked'):
        del getattr(Gateway, k)[:]
    models.DB = str(tmp_path / 't.db')
    models.init_db()
    from engines import nfse
    saida = str(tmp_path / 'XML')
    monkeypatch.setattr(nfse, 'SAIDA', saida)
    with models.con() as c:
        c.execute("INSERT INTO empresas(cnpj,nome,uf,cuf,senha_ok,ativo) VALUES(?,?,'SP','35',1,1)", (CNPJ, 'EMPRESA A'))
    fila.salvar_empresa(CNPJ, ativo=True, data_inicial='2026-11-01', chave='chave-dominio-a')
    pasta = os.path.join(saida, CNPJ, '2026-11', 'NFe', '04_saida')
    os.makedirs(pasta)
    caminho = os.path.join(pasta, CH1 + '_procNFe_v4.00.xml')
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write('<nfeProc><infNFe Id="NFe%s"><dhEmi>2026-11-03T10:00:00-03:00</dhEmi></infNFe></nfeProc>' % CH1)
    return caminho


def _linha():
    with models.con() as c:
        return dict(c.execute('SELECT * FROM dominio_envios').fetchone())


def _envios():
    return [e for e in Gateway.log if e[1] == '/api/v1/envios']


def test_modo_padrao_e_gateway(base):
    assert fila.modo() == 'gateway'


def test_ativacao_guarda_credencial_cifrada(base):
    inst = G.ativar(TICKET)
    assert inst['ativa'] and inst['installation_id']
    guardada = models.get_param('dominio_gw_credential')
    assert guardada and 'segredo' not in guardada
    assert G.credencial() == inst['installation_id'] + '.segredo'
    assert G.ativar('')['ativa']  # sem codigo: so confere (health)
    with pytest.raises(ValueError):
        G.ativar('curto')


def test_sem_ativacao_nao_toca_na_fila(base):
    fila.varrer()
    with pytest.raises(api.DominioPausar):
        fila.processar(5)
    assert _linha()['estado'] == 'NOVO'


def test_envio_resultado_e_confirmacao(base):
    inst = G.ativar(TICKET)['installation_id']
    fila.varrer(); fila.processar(5)
    _, _, h, body = _envios()[0]
    assert h['X-Company-Cnpj'] == CNPJ and h['X-Company-Key'] == 'chave-dominio-a'
    assert h['Content-Type'] == 'application/xml' and h['X-Idempotency-Key']
    assert body == open(base, 'rb').read()
    d = _linha()
    assert d['estado'] == 'ENVIADO' and d['iniciado'] == 1
    assert d['lote_id'] == hashlib.sha256(('%s|%s|%s' % (inst, CNPJ, hashlib.sha256(body).hexdigest())).encode()).hexdigest()
    Gateway.ops[d['lote_id']].update(status='CONFIRMADO', reference='PROTO-1', remote_codes=['EA00'])
    with models.con() as c:
        c.execute('UPDATE dominio_envios SET proximo=NULL')
    fila.consultar()
    d = _linha()
    assert (d['estado'], d['protocolo'], d['codigo']) == ('CONFIRMADO', 'PROTO-1', 'EA00')
    assert d['lote_id'] in Gateway.acked and d['ack_pendente'] == 0


def test_aguardando_dominio_pede_acompanhamento(base):
    G.ativar(TICKET); fila.varrer(); fila.processar(1)
    d = _linha()
    Gateway.ops[d['lote_id']]['status'] = 'AGUARDANDO_DOMINIO'
    with models.con() as c:
        c.execute('UPDATE dominio_envios SET proximo=NULL')
    fila.consultar()
    assert Gateway.tracked == [d['lote_id']] and _linha()['estado'] == 'ENVIADO'


def test_429_reusa_mesma_idempotencia(base):
    G.ativar(TICKET); fila.varrer()
    Gateway.script.append((429, {}, {'Retry-After': '120'}))
    fila.processar(1)
    d = _linha()
    assert d['estado'] == 'REPETIR'
    with models.con() as c:
        c.execute('UPDATE dominio_envios SET proximo=NULL')
    fila.processar(1)
    e = _envios()
    assert len(e) == 2 and e[0][2]['X-Idempotency-Key'] == e[1][2]['X-Idempotency-Key']
    assert _linha()['estado'] == 'ENVIADO'


def test_operacao_iniciada_consulta_antes_de_reenviar(base):
    G.ativar(TICKET); fila.varrer(); fila.processar(1)
    with models.con() as c:
        c.execute("UPDATE dominio_envios SET estado='REPETIR', proximo=NULL")
    fila.processar(1)
    assert len(_envios()) == 1  # achou a operacao pelo GET e nao mandou de novo


@pytest.mark.parametrize('resp,estado', [
    ((401, {}, None), 'PAUSADO'),
    ((409, {'error': 'IDEMPOTENCIA_CONFLITO'}, None), 'RECUSADO'),
    ((413, {}, None), 'RECUSADO'),
    ((503, {}, {'Retry-After': '30'}), 'REPETIR'),
])
def test_erros_do_gateway(base, resp, estado):
    G.ativar(TICKET); fila.varrer()
    Gateway.script.append(resp)
    fila.processar(1)
    assert _linha()['estado'] == estado


def test_resultados_finais(base):
    G.ativar(TICKET); fila.varrer(); fila.processar(1)
    rid = _linha()['lote_id']
    for st, esperado in (('REJEITADO', 'RECUSADO'), ('INDETERMINADO', 'INCERTO'), ('JA_EXISTENTE', 'JA_EXISTENTE')):
        Gateway.ops[rid]['status'] = st
        with models.con() as c:
            c.execute("UPDATE dominio_envios SET estado='ENVIADO', proximo=NULL")
        fila.consultar()
        assert _linha()['estado'] == esperado


def test_xml_alterado_e_recusado(base):
    G.ativar(TICKET); fila.varrer()
    Gateway.script.append((503, {}, {'Retry-After': '30'}))
    fila.processar(1)
    with open(base, 'a', encoding='utf-8') as f:
        f.write('<!-- mudou -->')
    with models.con() as c:
        c.execute('UPDATE dominio_envios SET proximo=NULL')
    fila.processar(1)
    assert _linha()['estado'] == 'RECUSADO'


def test_reenvio_manual_libera_no_gateway(base):
    G.ativar(TICKET); fila.varrer(); fila.processar(1)
    d = _linha()
    Gateway.ops[d['lote_id']]['status'] = 'REJEITADO'
    with models.con() as c:
        c.execute('UPDATE dominio_envios SET proximo=NULL')
    fila.consultar()
    fila.pedir_reenvio(d['id'])
    assert Gateway.retries == ['/api/v1/envios/%s/retentativas' % d['lote_id']]
    assert _linha()['estado'] == 'REPETIR' and _linha()['lote_id'] == d['lote_id']


def test_tela_ativacao(base):
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = 1; s['nome'] = 'Admin'; s['papel'] = 'admin'; s['_csrf'] = 'tok'
    html = cli.get('/dominio').get_data(as_text=True)
    assert 'Código de ativação' in html and 'ativação necessária' in html
    assert cli.post('/dominio/gateway/ativar', data={'_csrf': 'tok', 'ticket': TICKET}).status_code == 302
    html = cli.get('/dominio').get_data(as_text=True)
    assert 'Gateway: ativo até' in html and 'segredo' not in html
    cli.post('/dominio/modo', data={'_csrf': 'tok', 'modo': 'direto'})
    assert fila.modo() == 'direto' and 'Credenciais do integrador' in cli.get('/dominio').get_data(as_text=True)
