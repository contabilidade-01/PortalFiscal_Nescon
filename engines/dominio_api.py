# -*- coding: utf-8 -*-
"""Cliente da API Onvio / Dominio (Thomson Reuters) — envio de XML e consulta do lote.

Documentacao (2026-11): https://developerportal.thomsonreuters.com/onvio-br-accounting-api
  - Token:  POST https://auth.thomsonreuters.com/oauth/token
  - Envio:  POST {API}/invoice/{versao}/batches   multipart: file[] (XML) + query (JSON)
  - Lote:   GET  {API}/invoice/{versao}/batches/{id}
  - Teste:  GET  {API}/status/v2/integration/info
  - Chave:  POST {API}/integration/v1/activation/enable  (troca a chave do cliente, se exigido)

NAO CONFIRMADO ate a Dominio responder (ver docs/DOMINIO.md): grant client_credentials,
audience, versao v2/v3, header x-integration-key, troca da chave e a tabela de codigos.
Tudo isso esta em variaveis de ambiente para ajustar sem mexer no codigo.
DOMINIO_SIMULADO=1 troca a API por um simulador local (tela e fila sem credenciais).
"""
import base64, json, os, threading, time
from datetime import datetime

import requests

TIMEOUT = (10, 60)
LIMITE_BYTES = 2 * 1024 * 1024  # o EXE de referencia limitava em 2 MB


class DominioErro(Exception):
    """Base. `codigo` = codigo/HTTP retornado, quando houver."""
    def __init__(self, msg, codigo=''):
        super().__init__(msg)
        self.codigo = str(codigo or '')


class DominioRepetir(DominioErro):
    """Falha passageira (429, 5xx, rede antes do envio): tentar de novo depois."""
    def __init__(self, msg, codigo='', espera=120):
        super().__init__(msg, codigo)
        self.espera = max(30, min(3600, int(espera or 120)))


class DominioPausar(DominioErro):
    """401/403 ou credencial ausente: para a fila da empresa ate alguem conferir."""


class DominioRecusado(DominioErro):
    """Recusa definitiva do XML ou da requisicao: o operador precisa conferir."""


class DominioIncerto(DominioErro):
    """A conexao caiu DURANTE o envio: o lote pode ter sido criado. Nao reenviar sozinho."""


def config():
    e = os.environ.get
    return {
        'client_id': e('DOMINIO_CLIENT_ID', ''),
        'client_secret': e('DOMINIO_CLIENT_SECRET', ''),
        'audience': e('DOMINIO_AUDIENCE', ''),
        'token_url': e('DOMINIO_TOKEN_URL', 'https://auth.thomsonreuters.com/oauth/token'),
        'api_url': e('DOMINIO_API_URL', 'https://api.onvio.com.br/dominio').rstrip('/'),
        'versao': e('DOMINIO_INVOICE_VERSAO', 'v3'),
        'header_chave': e('DOMINIO_HEADER_CHAVE', 'x-integration-key'),
        'ativar_chave': e('DOMINIO_ATIVAR_CHAVE', '0') == '1',
        'boxe_file': e('DOMINIO_BOXE_FILE', '0') == '1',
        'simulado': e('DOMINIO_SIMULADO', '0') == '1',
    }


def credenciais_ok(cfg=None):
    cfg = cfg or config()
    return cfg['simulado'] or bool(cfg['client_id'] and cfg['client_secret'])


# Codigo de retorno -> estado da fila. So 'EA10' (documento ja existente) e conhecido
# hoje, pelo EXE de referencia. O resto entra pela env DOMINIO_CODIGOS (JSON) quando a
# Dominio mandar a tabela "Retornos de Protocolo". Codigo fora do mapa: consulta de novo
# e, depois de muitas consultas, vira INCERTO (nunca CONFIRMADO por suposicao).
CODIGOS_PADRAO = {'EA10': 'JA_EXISTENTE'}


def mapa_codigos():
    m = dict(CODIGOS_PADRAO)
    if config()['simulado']:
        m['SIM-OK'] = 'CONFIRMADO'
    try:
        extra = json.loads(os.environ.get('DOMINIO_CODIGOS') or '{}')
        if isinstance(extra, dict):
            m.update({str(k).upper(): str(v).upper() for k, v in extra.items()})
    except ValueError:
        pass
    return m


def interpretar_lote(lote):
    """BatchV2Dto -> (estado|None, codigo, mensagem). None = ainda sem resultado final.
    Olha o status do arquivo (apiStatus) e, sem ele, o do lote."""
    if not isinstance(lote, dict):
        return None, '', 'Resposta do lote em formato inesperado.'
    st = None
    arquivos = lote.get('filesExpanded') or []
    if arquivos and isinstance(arquivos[0], dict):
        st = arquivos[0].get('apiStatus') or arquivos[0].get('boxeStatus')
    st = st or lote.get('status') or {}
    codigo = str((st or {}).get('code') or '').upper()
    msg = str((st or {}).get('message') or '')[:300]
    return mapa_codigos().get(codigo), codigo, msg


class DominioClient:
    def __init__(self, cfg=None, session=None):
        self.cfg = cfg or config()
        self.s = session or requests.Session()
        self.s.trust_env = False
        self._token, self._expira = '', 0
        self._lock = threading.Lock()

    # ---------- autenticacao ----------
    def token(self):
        with self._lock:
            if self._token and time.time() < self._expira - 60:
                return self._token
            if not credenciais_ok(self.cfg):
                raise DominioPausar('Client ID / Client Secret da Domínio não configurados no servidor.')
            dados = {'grant_type': 'client_credentials'}
            if self.cfg['audience']:
                dados['audience'] = self.cfg['audience']
            basic = base64.b64encode(('%s:%s' % (self.cfg['client_id'], self.cfg['client_secret'])).encode()).decode()
            try:
                r = self.s.post(self.cfg['token_url'], data=dados, timeout=TIMEOUT,
                                headers={'Authorization': 'Basic ' + basic})
            except requests.RequestException:
                raise DominioRepetir('Sem conexão com o servidor de autenticação da Thomson Reuters.') from None
            if r.status_code in (400, 401, 403):
                raise DominioPausar('Autenticação recusada (HTTP %d). Confira Client ID, Secret e audience.'
                                    % r.status_code, r.status_code)
            if r.status_code != 200:
                raise DominioRepetir('Autenticação indisponível (HTTP %d).' % r.status_code, r.status_code,
                                     _retry_after(r))
            j = _json(r)
            if not j.get('access_token'):
                raise DominioRepetir('Autenticação sem access_token na resposta.')
            self._token = j['access_token']
            self._expira = time.time() + int(j.get('expires_in') or 3600)
            return self._token

    def _headers(self, chave=None):
        h = {'Authorization': 'Bearer ' + self.token(), 'Accept': 'application/json'}
        if chave:
            h[self.cfg['header_chave']] = chave
        return h

    # ---------- chamadas ----------
    def testar(self):
        """Confere token e conectividade. Retorna dict com a mensagem da API."""
        r = self._req('GET', self.cfg['api_url'] + '/status/v2/integration/info', self._headers())
        return _json(r) if r.content else {'message': 'ok'}

    def ativar_chave(self, chave_cliente):
        """Troca a chave do cliente pela chave de envio, se a Dominio exigir (DOMINIO_ATIVAR_CHAVE=1).
        Formato da resposta ainda nao confirmado: aceita 'integrationKey' ou 'key'."""
        h = self._headers()
        h[self.cfg['header_chave']] = chave_cliente
        r = self._req('POST', self.cfg['api_url'] + '/integration/v1/activation/enable', h)
        j = _json(r)
        nova = j.get('integrationKey') or j.get('integration_key') or j.get('key') or ''
        if not nova:
            raise DominioRecusado('A ativação da chave não devolveu a chave de envio.')
        return nova

    def enviar(self, conteudo, nome_arquivo, chave):
        """Cria o lote com 1 XML. Retorna o id do lote."""
        if not 0 < len(conteudo) <= LIMITE_BYTES:
            raise DominioRecusado('XML vazio ou acima de 2 MB.')
        url = '%s/invoice/%s/batches' % (self.cfg['api_url'], self.cfg['versao'])
        files = {
            'file[]': (nome_arquivo, conteudo, 'application/xml'),
            'query': (None, json.dumps({'boxeFile': self.cfg['boxe_file']}), 'application/json'),
        }
        r = self._req('POST', url, self._headers(chave), files=files, upload=True)
        j = _json(r)
        lote = j.get('id') or j.get('batchId') or ''
        if not lote:
            # o lote pode ter sido criado sem devolver o id: nao arriscar duplicar
            raise DominioIncerto('A Domínio aceitou o envio mas não devolveu o id do lote.', r.status_code)
        return str(lote)

    def consultar(self, lote_id, chave):
        url = '%s/invoice/%s/batches/%s' % (self.cfg['api_url'], self.cfg['versao'], lote_id)
        return _json(self._req('GET', url, self._headers(chave)))

    def _req(self, metodo, url, headers, upload=False, **kw):
        try:
            r = self.s.request(metodo, url, headers=headers, timeout=TIMEOUT, allow_redirects=False, **kw)
        except requests.RequestException:
            if upload:
                raise DominioIncerto('A conexão caiu durante o envio. Confira na Domínio antes de reenviar.') from None
            raise DominioRepetir('Sem conexão com a API da Domínio.') from None
        if r.status_code == 401:
            self._token = ''  # token pode ter expirado: renova na proxima
            raise DominioPausar('API recusou o acesso (HTTP 401). Confira as credenciais.', 401)
        if r.status_code == 403:
            raise DominioPausar('Sem permissão na Domínio (HTTP 403). Confira a chave da empresa e a '
                                'habilitação "Notas fiscais" no Onvio.', 403)
        if r.status_code == 429 or r.status_code >= 500:
            if upload and r.status_code >= 500:
                raise DominioIncerto('A Domínio retornou erro %d durante o envio. Confira antes de reenviar.'
                                     % r.status_code, r.status_code)
            raise DominioRepetir('Domínio ocupada/indisponível (HTTP %d).' % r.status_code, r.status_code,
                                 _retry_after(r))
        if r.status_code == 413:
            raise DominioRecusado('XML acima do limite da Domínio.', 413)
        if not 200 <= r.status_code < 300:
            raise DominioRecusado('Domínio recusou (HTTP %d): %s' % (r.status_code, _motivo(r)), r.status_code)
        return r


def _retry_after(r):
    try:
        return int(r.headers.get('Retry-After') or 120)
    except ValueError:
        return 120


def _json(r):
    try:
        j = r.json()
    except ValueError:
        raise DominioRepetir('Resposta da Domínio não é JSON.') from None
    return j if isinstance(j, dict) else {'items': j}


def _motivo(r):
    try:
        j = r.json()
        if isinstance(j, dict):
            return str(j.get('message') or j.get('error') or j)[:200]
    except ValueError:
        pass
    return (r.text or '')[:200]


class SimuladorClient:
    """DOMINIO_SIMULADO=1: aceita tudo e confirma na 1a consulta com o codigo 'SIM-OK'.
    Serve para ver a tela e a fila funcionando antes das credenciais reais."""
    cfg = {'simulado': True}

    def __init__(self, *a, **k):
        self.lotes = {}

    def testar(self):
        return {'message': 'Simulador local ativo (DOMINIO_SIMULADO=1). Nada é enviado à Domínio.'}

    def ativar_chave(self, chave_cliente):
        return chave_cliente

    def enviar(self, conteudo, nome_arquivo, chave):
        if not 0 < len(conteudo) <= LIMITE_BYTES:
            raise DominioRecusado('XML vazio ou acima de 2 MB.')
        lid = 'SIM-%s-%d' % (datetime.now().strftime('%H%M%S'), len(self.lotes) + 1)
        self.lotes[lid] = nome_arquivo
        return lid

    def consultar(self, lote_id, chave):
        return {'id': lote_id, 'status': {'code': 'SIM-OK', 'message': 'Simulado: arquivo recebido.'}}


_cliente = None


def cliente():
    """Instancia unica (o token fica em cache entre ciclos)."""
    global _cliente
    cfg = config()
    tipo = SimuladorClient if cfg['simulado'] else DominioClient
    if not isinstance(_cliente, tipo):
        _cliente = tipo(cfg)
    return _cliente
