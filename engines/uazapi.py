# -*- coding: utf-8 -*-
"""Cliente uazapi (WhatsApp) — porte enxuto do `api/src/uazapi.js` do nescon-clientes.

O alerta anti-ban e' texto puro, entao aqui existe so o que ele usa: `/send/text`
e `/instance/status`. Menos superficie, menos coisa para quebrar.

Credenciais por env (mesmos nomes do nescon-clientes):
  UAZAPI_SUBDOMAIN  -> base https://<subdominio>.uazapi.com
  UAZAPI_TOKEN      -> header `token`

Os dois erros sao distinguidos de proposito: token invalido / instancia
desconectada (401) exige acao humana e NAO deve ser repetido; qualquer outra
falha e' transitoria.
"""
import os
import requests


class UazapiNaoConfigurado(Exception):
    pass


class UazapiTokenInvalido(Exception):
    pass


def _cred():
    return (os.environ.get('UAZAPI_SUBDOMAIN', '').strip(),
            os.environ.get('UAZAPI_TOKEN', '').strip())


def configurado():
    sub, tok = _cred()
    return bool(sub and tok)


def _base_url():
    sub, _ = _cred()
    return 'https://%s.uazapi.com' % sub


def _headers():
    _, tok = _cred()
    return {'token': tok, 'Content-Type': 'application/json'}


def _chamar(caminho, metodo='GET', corpo=None, timeout=30):
    if not configurado():
        raise UazapiNaoConfigurado('UAZAPI_SUBDOMAIN/UAZAPI_TOKEN nao configurados.')
    r = requests.request(metodo, _base_url() + caminho, headers=_headers(),
                         json=corpo, timeout=timeout)
    if r.status_code == 401:
        raise UazapiTokenInvalido(
            "Token uazapi rejeitado ou instancia desconectada. Confira o painel (status 'connected'?).")
    if not r.ok:
        raise RuntimeError('uazapi HTTP %s: %s' % (r.status_code, (r.text or '')[:200]))
    try:
        return r.json()
    except ValueError:
        return {}


def enviar_texto(numero, texto, delay_ms=0):
    """Manda um texto. `delay_ms` faz a uazapi exibir 'digitando...' antes de entregar."""
    corpo = {'number': str(numero), 'text': texto}
    if delay_ms and delay_ms > 0:
        corpo['delay'] = int(delay_ms)
    return _chamar('/send/text', 'POST', corpo, timeout=60)


def status_instancia():
    """Diagnostico da instancia, pronto para a tela. Nunca lanca."""
    if not configurado():
        return {'ok': False, 'categoria': 'nao_configurado',
                'mensagem': 'uazapi nao configurada no ambiente.'}
    try:
        j = _chamar('/instance/status', timeout=15)
        inst = j.get('instance') or {}
        status = inst.get('status') or 'desconhecido'
        if status != 'connected':
            return {'ok': False, 'categoria': 'desconectado',
                    'mensagem': "Instancia esta '%s' (esperado 'connected'). Releia o QR code." % status}
        return {'ok': True, 'categoria': 'ok',
                'mensagem': 'Conectado ao numero %s (%s).' % (
                    inst.get('owner') or '?', inst.get('profileName') or 'sem nome'),
                'owner': inst.get('owner')}
    except UazapiTokenInvalido as e:
        return {'ok': False, 'categoria': 'token_invalido', 'mensagem': str(e)}
    except Exception as e:
        return {'ok': False, 'categoria': 'rede', 'mensagem': 'Falha ao falar com a uazapi: %s' % e}
