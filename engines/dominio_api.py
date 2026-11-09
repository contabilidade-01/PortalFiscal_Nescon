# -*- coding: utf-8 -*-
"""Cliente da API Onvio / Dominio (Thomson Reuters) — envio de XML e consulta do lote.

Guia oficial "Documentacao Integracao API para ERPs" (suporte Dominio, solucao 8476), lido em
2026-11-09:
  1. Token:  POST https://auth.thomsonreuters.com/oauth/token  (Basic client_id:client_secret +
             form grant_type=client_credentials, client_id, client_secret, audience). Vale 24 h;
             gerar SO 1x ao dia e usar o MESMO token para todos os clientes (acima de 40
             requisicoes a Dominio bloqueia a integracao).
  2. Conferir a chave do cliente: GET {API}/integration/v1/activation/info  (Bearer + x-integration-key
             = chave do contador) -> escritorio, cliente e CNPJ.
  3. Gerar a integrationKey:      POST {API}/integration/v1/activation/enable (mesmos headers).
  4. Envio:  POST {API}/invoice/v3/batches  (Bearer + x-integration-key = integrationKey; form-data
             file[] XML, query {"boxeFile": false}). Nunca mandar a chave do contador aqui.
  5. Consulta: GET {API}/invoice/v3/batches/{id}; "Arquivo armazenado na API" = sucesso.
Ainda sem tabela oficial de codigos de erro: entra pela env DOMINIO_CODIGOS.
DOMINIO_SIMULADO=1 troca a API por um simulador local (tela e fila sem credenciais).
"""
import base64, json, os, threading, time
from datetime import datetime

import requests

TIMEOUT = (10, 60)
AUDIENCE_ONVIO = '409f91f6-dc17-44c8-a5d8-e0a1bafd8b67'
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


TOKEN_MAX_DIA = 5  # o guia pede 1 token/dia; folga para reinicio/renovacao, nunca perto de 40


def _credenciais_tela():
    """Client ID/Secret cadastrados na tela (parametros; secret cifrado com DOMINIO_KEY)."""
    try:
        import models
        from engines import dominio_fila
        cid = models.get_param('dominio_client_id') or ''
        sec = models.get_param('dominio_client_secret') or ''
        return cid, (dominio_fila.decifrar(sec) if sec else '')
    except Exception:
        return '', ''


def config():
    e = os.environ.get
    cid, sec = e('DOMINIO_CLIENT_ID', ''), e('DOMINIO_CLIENT_SECRET', '')
    origem = 'ambiente' if (cid and sec) else ''
    if not origem:
        cid, sec = _credenciais_tela()
        origem = 'tela' if (cid and sec) else ''
    return {
        'client_id': cid,
        'client_secret': sec,
        'origem_credenciais': origem,
        # audience oficial do Onvio (pagina "Authenticating with OAuth 2.0"); "-" = nao enviar
        'audience': '' if e('DOMINIO_AUDIENCE', '') == '-' else e('DOMINIO_AUDIENCE', '') or AUDIENCE_ONVIO,
        'token_url': e('DOMINIO_TOKEN_URL', 'https://auth.thomsonreuters.com/oauth/token'),
        'api_url': e('DOMINIO_API_URL', 'https://api.onvio.com.br/dominio').rstrip('/'),
        'versao': e('DOMINIO_INVOICE_VERSAO', 'v3'),
        'header_chave': e('DOMINIO_HEADER_CHAVE', 'x-integration-key'),
        # Guia de integracao: a chave gerada no Onvio e trocada 1x em activation/enable pela
        # chave de envio. Padrao ligado; DOMINIO_ATIVAR_CHAVE=0 envia a chave do cliente direto.
        'ativar_chave': e('DOMINIO_ATIVAR_CHAVE', '1') == '1',
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
# Mensagens do guia oficial (o codigo da resposta v3 ainda nao foi documentado).
MENSAGENS = (('armazenado na api', 'CONFIRMADO'),)
AGUARDANDO = ('aguardando processamento',)


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
    if isinstance(st, str):
        st = {'message': st}
    codigo = str((st or {}).get('code') or '').upper()
    msg = str((st or {}).get('message') or lote.get('message') or '')[:300]
    estado = mapa_codigos().get(codigo)
    if not estado:
        baixo = msg.lower()
        if not any(a in baixo for a in AGUARDANDO):
            estado = next((e for t, e in MENSAGENS if t in baixo), None)
    return estado, codigo, msg


def _token_salvo():
    try:
        import models
        from engines import dominio_fila
        tok, exp = models.get_param('dominio_token') or '', float(models.get_param('dominio_token_expira') or 0)
        return (dominio_fila.decifrar(tok), exp) if tok and dominio_fila.cofre_ok() else ('', 0)
    except Exception:
        return '', 0


def _guardar_token(tok, exp):
    try:
        import models
        from engines import dominio_fila
        if dominio_fila.cofre_ok():
            models.set_param('dominio_token', dominio_fila.cifrar(tok) if tok else '')
            models.set_param('dominio_token_expira', str(exp))
    except Exception:
        pass


def tokens_hoje(incrementar=False):
    """Quantos tokens foram pedidos hoje (controle do limite da Dominio)."""
    import models
    hoje = datetime.now().strftime('%Y-%m-%d')
    dia, _, n = (models.get_param('dominio_token_contagem') or '').partition(':')
    n = int(n) if dia == hoje and n.isdigit() else 0
    if incrementar:
        n += 1
        models.set_param('dominio_token_contagem', '%s:%d' % (hoje, n))
    return n


def estado_token():
    tok, exp = _token_salvo()
    return {'valido_ate': datetime.fromtimestamp(exp).strftime('%d/%m/%Y %H:%M') if tok and exp > time.time() else '',
            'pedidos_hoje': tokens_hoje()}


class DominioClient:
    def __init__(self, cfg=None, session=None):
        self.cfg = cfg or config()
        self.s = session or requests.Session()
        self.s.trust_env = False
        self._token, self._expira = _token_salvo()
        self._lock = threading.Lock()

    # ---------- autenticacao ----------
    def token(self):
        with self._lock:
            if self._token and time.time() < self._expira - 60:
                return self._token
            if not credenciais_ok(self.cfg):
                raise DominioPausar('Client ID / Client Secret da Domínio não configurados no servidor.')
            if tokens_hoje() >= TOKEN_MAX_DIA:
                raise DominioRepetir('Limite diário de tokens atingido (%d). A Domínio pede 1 token por dia; '
                                     'nova tentativa mais tarde.' % TOKEN_MAX_DIA, espera=3600)
            dados = {'grant_type': 'client_credentials', 'client_id': self.cfg['client_id'],
                     'client_secret': self.cfg['client_secret']}
            if self.cfg['audience']:
                dados['audience'] = self.cfg['audience']
            tokens_hoje(incrementar=True)
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
            self._expira = time.time() + int(j.get('expires_in') or 86400)
            _guardar_token(self._token, self._expira)
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

    def info_chave(self, chave_cliente):
        """activation/info: escritorio, cliente e CNPJ da chave que o contador gerou."""
        h = self._headers()
        h[self.cfg['header_chave']] = chave_cliente
        return _json(self._req('GET', self.cfg['api_url'] + '/integration/v1/activation/info', h))

    def ativar_chave(self, chave_cliente):
        """activation/enable: troca a chave do contador pela integrationKey (usada nos envios)."""
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
            self._token, self._expira = '', 0  # token pode ter expirado: renova na proxima
            _guardar_token('', 0)
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

    def info_chave(self, chave_cliente):
        return {'simulado': True, 'message': 'Simulador: a chave não é conferida.'}

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
    if not isinstance(_cliente, tipo) or (tipo is DominioClient and (
            _cliente.cfg['client_id'], _cliente.cfg['client_secret']) != (cfg['client_id'], cfg['client_secret'])):
        _cliente = tipo(cfg)  # credenciais trocadas na tela: novo cliente (token salvo continua valendo)
    return _cliente
