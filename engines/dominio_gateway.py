# -*- coding: utf-8 -*-
"""Cliente do gateway do fornecedor (mesmo do EXE CentralNFSe, modulo dominio_gateway_client).

O gateway guarda a credencial OAuth da Dominio; a instalacao so tem um codigo de ativacao
individual ("Cole aqui o codigo recebido do criador do sistema") e a chave Dominio de cada
empresa. Rotas (todas sob GATEWAY_URL):
  POST /api/v1/ativacoes                     {ticket, installation_id} -> credential, expires_at
  GET  /health                               Bearer credential
  POST /api/v1/envios                        XML (application/xml) + X-Company-Cnpj/Key + X-Idempotency-Key
  GET  /api/v1/envios/{request_id}           estado de uma operacao (404 = desconhecida)
  POST /api/v1/envios/status                 {request_ids} -> {items, missing}
  POST /api/v1/envios/confirmacoes           {request_ids}: resultado final ja gravado localmente
  POST /api/v1/envios/acompanhamento         {request_id}: gateway passa a acompanhar sozinho
  POST /api/v1/envios/{request_id}/retentativas {retry_id, confirmed}: libera reenvio manual
Estados do gateway: RECEBIDO, AGUARDANDO_DOMINIO, CONFIRMADO, JA_EXISTENTE, REJEITADO,
FALHA_TRANSITORIA, INDETERMINADO. A credencial fica cifrada com DOMINIO_KEY (no EXE era DPAPI).
"""
import os, re, time, uuid

import certifi
import requests

import models
from engines import dominio_api as api

GATEWAY_URL = 'https://yzmp6lmumi.execute-api.sa-east-1.amazonaws.com'
STATES = frozenset({'REJEITADO', 'FALHA_TRANSITORIA', 'CONFIRMADO', 'INDETERMINADO',
                    'AGUARDANDO_DOMINIO', 'JA_EXISTENTE', 'RECEBIDO'})
LIMITE_BYTES = 2000000
RE_TICKET = re.compile(r'[A-Za-z0-9_-]{16,128}')


def url_base():
    return (os.environ.get('DOMINIO_GATEWAY_URL') or GATEWAY_URL).rstrip('/')


# ---------------------------------------------------------------- instalacao (parametros)
def _fila():
    from engines import dominio_fila
    return dominio_fila


def instalacao():
    """Estado da instalacao no gateway (nunca devolve a credencial)."""
    iid = models.get_param('dominio_gw_installation_id') or ''
    exp = int(float(models.get_param('dominio_gw_expires_at') or 0))
    tem_cred = bool(models.get_param('dominio_gw_credential'))
    return {'installation_id': iid, 'expires_at': exp, 'ativa': tem_cred and exp > time.time(),
            'vencida': tem_cred and 0 < exp <= time.time()}


def _installation_id():
    iid = models.get_param('dominio_gw_installation_id')
    if not iid:
        iid = str(uuid.uuid4())
        models.set_param('dominio_gw_installation_id', iid)
    return iid


def credencial():
    v = models.get_param('dominio_gw_credential') or ''
    if not v:
        raise api.DominioPausar('Instalação não ativada no gateway. Informe o código de ativação.')
    if int(float(models.get_param('dominio_gw_expires_at') or 0)) <= time.time():
        raise api.DominioPausar('Ativação do gateway vencida. Ative de novo com o código.')
    return _fila().decifrar(v)


def ativar(ticket=''):
    """Ativa (ou confere) a instalacao. Igual ao activate() do EXE."""
    if not _fila().cofre_ok():
        raise ValueError('Defina DOMINIO_KEY no servidor antes de ativar.')
    iid = _installation_id()
    inst = instalacao()
    if not ticket and inst['ativa']:
        GatewayClient(credencial()).health()
        return instalacao()
    ticket = (ticket or '').strip()
    if not RE_TICKET.fullmatch(ticket):
        raise ValueError('Código de ativação inválido.')
    data = GatewayClient().activate(ticket, iid)
    cred = str(data.get('credential') or '')
    if data.get('installation_id') != iid or not cred.startswith(iid + '.'):
        raise api.DominioRepetir('Ativação sem confirmação do gateway. Tente de novo com o mesmo código.')
    models.set_param('dominio_gw_credential', _fila().cifrar(cred))
    models.set_param('dominio_gw_expires_at', str(int(data.get('expires_at') or 0)))
    with models.con() as c:  # o que pausou por autorizacao volta para a fila
        c.execute("UPDATE dominio_envios SET estado='REPETIR', proximo=?, mensagem='' WHERE estado='PAUSADO'",
                  (_fila().agora(),))
    return instalacao()


def desativar():
    models.set_param('dominio_gw_credential', '')
    models.set_param('dominio_gw_expires_at', '0')


# ---------------------------------------------------------------- HTTP
class GatewayClient:
    def __init__(self, credential='', session=None):
        self.credential = credential
        self.s = session or requests.Session()
        self.s.trust_env = False

    def request(self, metodo, caminho, company=None, key=None, **kw):
        headers = kw.pop('headers', {})
        if self.credential:
            headers['Authorization'] = 'Bearer ' + self.credential
        if company:
            headers['X-Company-Cnpj'] = re.sub('[^A-Z0-9]', '', company.upper())
            headers['X-Company-Key'] = key
        try:
            r = self.s.request(metodo, url_base() + caminho, headers=headers, timeout=(3, 35),
                               verify=certifi.where(), allow_redirects=False, **kw)
        except requests.RequestException:
            raise api.DominioRepetir('Conexão com o gateway interrompida. A fila recupera a mesma operação.',
                                     espera=60) from None
        try:
            res = r.json()
            if not isinstance(res, dict):
                raise ValueError()
        except ValueError:
            res = {}
        code = r.status_code
        if metodo == 'GET' and code == 404:
            return None
        if metodo == 'POST' and caminho.endswith('/retentativas') and code == 404:
            return None
        if code in (401, 403):
            raise api.DominioPausar('Instalação ou empresa não autorizada no gateway. Confira a ativação e a '
                                    'chave da empresa; a fila foi preservada.', code)
        if code == 409 and res.get('error') == 'IDEMPOTENCIA_CONFLITO':
            raise api.DominioRecusado('O XML mudou após a primeira tentativa. Confira antes de reenviar.', 409)
        if code == 409 and res.get('error') == 'REENVIO_NAO_PERMITIDO':
            raise api.DominioRecusado('O gateway já tem protocolo ou a operação está em andamento. '
                                      'Atualize o estado antes de reenviar.', 409)
        if code in (200, 202, 503) and res.get('status') in STATES:
            if not re.fullmatch('[0-9a-f]{64}', str(res.get('request_id', ''))):
                raise api.DominioRepetir('Resposta do gateway sem identificação válida.')
            return res
        if code in (429, 409) or code >= 500 or 300 <= code < 400:
            try:
                espera = int(r.headers.get('Retry-After', 60))
            except (TypeError, ValueError):
                espera = 60
            raise api.DominioRepetir('Gateway ocupado ou indisponível (HTTP %d).' % code, code, espera)
        if code == 413:
            raise api.DominioRecusado('XML acima do limite de 2 MB do gateway.', 413)
        if not 200 <= code < 300:
            raise api.DominioRecusado('Gateway recusou a requisição (HTTP %d). Confira o código de ativação, '
                                      'o XML e os dados da empresa.' % code, code)
        if not res:
            raise api.DominioRepetir('Resposta incompleta do gateway. A operação será recuperada.')
        return res

    def activate(self, ticket, installation_id):
        return self.request('POST', '/api/v1/ativacoes', json={'ticket': ticket, 'installation_id': installation_id})

    def health(self):
        return self.request('GET', '/health')

    def send(self, conteudo, cnpj, key, idempotency_key):
        return self.request('POST', '/api/v1/envios', company=cnpj, key=key, data=conteudo,
                            headers={'Content-Type': 'application/xml', 'X-Idempotency-Key': idempotency_key})

    def status(self, request_id, cnpj, key):
        return self.request('GET', '/api/v1/envios/' + request_id, company=cnpj, key=key)

    def batch_status(self, request_ids):
        res = self.request('POST', '/api/v1/envios/status', json={'request_ids': request_ids})
        items, missing = res.get('items'), res.get('missing')
        if not (isinstance(items, list) and isinstance(missing, list)):
            raise api.DominioRepetir('Consulta incompleta no gateway; estados locais preservados.')
        vistos = []
        for it in items:
            if not isinstance(it, dict) or it.get('request_id') not in request_ids or it.get('status') not in STATES:
                raise api.DominioRepetir('Consulta divergente no gateway; estados locais preservados.')
            vistos.append(it['request_id'])
        todos = vistos + missing
        if any(i not in request_ids for i in missing) or len(todos) != len(set(todos)) or set(todos) != set(request_ids):
            raise api.DominioRepetir('Consulta parcial no gateway; estados locais preservados.')
        return res

    def acknowledge(self, request_ids):
        return self.request('POST', '/api/v1/envios/confirmacoes', json={'request_ids': request_ids})

    def track_existing(self, request_id, cnpj, key):
        return self.request('POST', '/api/v1/envios/acompanhamento', company=cnpj, key=key,
                            json={'request_id': request_id})

    def authorize_retry(self, request_id, retry_id):
        return self.request('POST', '/api/v1/envios/%s/retentativas' % request_id,
                            json={'retry_id': retry_id, 'confirmed': True})


def cliente():
    return GatewayClient(credencial())
