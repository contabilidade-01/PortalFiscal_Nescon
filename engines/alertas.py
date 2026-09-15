# -*- coding: utf-8 -*-
"""Alertas proativos por WhatsApp (uazapi) — avisar ANTES de o problema virar dias
de nota sem baixar. Mesmo numero/instancia do alerta de circuito (engines.guard).

1) Certificado A1 vencendo: marcos 30, 15 e 7 dias e no dia em que vence. Uma
   mensagem por dia agrupando as empresas; cada (cnpj, validade, marco) so uma vez —
   renovou o certificado (validade nova) o ciclo recomeca.
2) Falha geral de um ambiente: se a rodada falhou na maioria das empresas
   consultadas (ex.: NFC-e inteira com cStat 104), avisa no mesmo dia. 1 por ambiente/dia.

Nunca derruba o robo: sem uazapi configurada so registra log; falha de envio
libera a chave para tentar de novo na proxima rodada.
"""
import logging, threading
from collections import Counter
from datetime import datetime
import models

log = logging.getLogger('sefaz.alertas')

ASSINCRONO = True  # testes desligam para verificar o envio na hora

MARCOS_CERT = (30, 15, 7)
MIN_EMPRESAS_FALHA = 2      # com 1 empresa so, nao da para dizer que "o ambiente" caiu
FRACAO_FALHA = 0.8          # 80%+ das consultadas falharam

# paradas normais/esperadas: NAO sao falha do ambiente (anti-ban e fluxo incremental)
_NORMAIS = frozenset(('ok', 'fim', '137', 'cap', 'limite', 'cooldown', '656', '429',
                      'circuito_aberto', 'sem_certificado', 'uf_nao_sp', 'cert_vencido'))
_ROTULO_MOTOR = {'nfe': 'NF-e Entradas', 'nfse': 'NFS-e', 'nfce': 'NFC-e'}
_TIPO_EXEC = {'nfe': 'nfe_entrada', 'nfse': 'nfse', 'nfce': 'nfce'}


def _agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _reservar(chave, texto):
    """True se a chave ainda nao foi usada (e a reserva agora)."""
    with models.con() as c:
        return c.execute('INSERT OR IGNORE INTO alertas_enviados(chave, quando, texto) VALUES(?,?,?)',
                         (chave, _agora(), texto[:1000])).rowcount == 1


def _liberar(chaves):
    with models.con() as c:
        c.executemany('DELETE FROM alertas_enviados WHERE chave=?', [(k,) for k in chaves])


def _enviar(chaves, texto):
    """Reserva as chaves e envia. Retorna True se disparou (ou agendou) o envio."""
    from engines import uazapi, guard
    if not guard.ALERTA_WHATSAPP or not uazapi.configurado():
        log.info('alerta nao enviado (uazapi/numero nao configurado): %s', texto[:120])
        return False
    novas = [k for k in chaves if _reservar(k, texto)]
    if not novas:
        return False

    def _job():
        try:
            uazapi.enviar_texto(guard.ALERTA_WHATSAPP, texto)
            log.info('alerta enviado (%d chave(s))', len(novas))
        except Exception:
            log.warning('falha ao enviar alerta; libera para tentar de novo', exc_info=True)
            _liberar(novas)

    if ASSINCRONO:
        threading.Thread(target=_job, daemon=True).start()
    else:
        _job()
    return True


def _cnpj_fmt(c):
    c = ''.join(ch for ch in (c or '') if ch.isdigit())
    return '%s.%s.%s/%s-%s' % (c[:2], c[2:5], c[5:8], c[8:12], c[12:]) if len(c) == 14 else c


def _data_br(iso):
    return '%s/%s/%s' % (iso[8:10], iso[5:7], iso[:4]) if iso and len(iso) >= 10 else (iso or '?')


def marco_certificado(dias):
    """'vencido' | '7' | '15' | '30' | None. Pula marcos antigos (servidor parado dias)."""
    if dias < 0:
        return 'vencido'
    for m in sorted(MARCOS_CERT):
        if dias <= m:
            return str(m)
    return None


def verificar_certificados(hoje=None):
    """Chamado 1x por rodada completa. Retorna a lista de (empresa, marco) alertados."""
    hoje = hoje or datetime.now().date()
    marcadas = ' OR '.join('COALESCE(%s,0)=1' % a['col'] for a in models.AMBIENTES)
    with models.con() as c:
        emps = c.execute('SELECT cnpj, nome, validade FROM empresas WHERE ativo=1 AND senha_ok=1 '
                         "AND COALESCE(validade,'')!='' AND (%s) ORDER BY validade" % marcadas).fetchall()
        ja = {r['chave'] for r in c.execute("SELECT chave FROM alertas_enviados WHERE chave LIKE 'cert:%'")}
    novos = []
    for e in emps:
        try:
            dias = (datetime.strptime(e['validade'][:10], '%Y-%m-%d').date() - hoje).days
        except ValueError:
            continue
        marco = marco_certificado(dias)
        if not marco:
            continue
        chave = 'cert:%s:%s:%s' % (e['cnpj'], e['validade'][:10], marco)
        if chave not in ja:
            novos.append((e, marco, dias, chave))
    if not novos:
        return []
    blocos = []
    vencidos = [x for x in novos if x[1] == 'vencido']
    if vencidos:
        blocos.append('⛔ *Vencidos* — o robô parou de consultar estas empresas:\n' + '\n'.join(
            '• %s (%s) — venceu em %s' % ((e['nome'] or '').title(), _cnpj_fmt(e['cnpj']), _data_br(e['validade']))
            for e, _m, _d, _k in vencidos))
    for m in sorted(MARCOS_CERT):
        grupo = [x for x in novos if x[1] == str(m)]
        if grupo:
            blocos.append('⚠️ *Vencem em até %d dias:*\n' % m + '\n'.join(
                '• %s (%s) — %s (%s)' % ((e['nome'] or '').title(), _cnpj_fmt(e['cnpj']), _data_br(e['validade']),
                                        'hoje' if d == 0 else '%d dia%s' % (d, 's' if d != 1 else ''))
                for e, _m, d, _k in grupo))
    texto = ('🔐 *Portal Fiscal Nescon — Certificados A1*\n\n' + '\n\n'.join(blocos) +
             '\n\nSem certificado válido a SEFAZ recusa a consulta e as notas param de ser baixadas. '
             'Renove e vincule o novo A1 em *Certificados*.')
    _enviar([k for _e, _m, _d, k in novos], texto)
    return [(e['nome'], m) for e, m, _d, _k in novos]


def eh_falha(parada):
    p = str(parada or '')
    return bool(p) and p not in _NORMAIS and p.split(':')[0] not in _NORMAIS


def _rotulo_parada(p):
    base = str(p or '').split(':')[0]
    if base.startswith('cStat_'):
        return 'SEFAZ rejeitou (cStat %s)' % base[6:]
    if base == 'http_403':
        return 'certificado recusado (HTTP 403)'
    if base.startswith('http_'):
        return 'erro HTTP %s' % base[5:]
    return {'erro_rede': 'erro de rede/conexão', 'erro_cert': 'erro ao abrir o certificado',
            'erro': 'erro interno', '108': 'SEFAZ instável (108)', '109': 'SEFAZ paralisada (109)',
            'resposta_invalida': 'resposta inválida'}.get(base, base)


def avaliar_falha_geral(motor, resultados, dia=None):
    """resultados: [(cnpj, nome, parada)] das empresas REALMENTE consultadas na etapa.
    Retorna True se alertou."""
    if len(resultados) < MIN_EMPRESAS_FALHA:
        return False
    falhas = [r for r in resultados if eh_falha(r[2])]
    if len(falhas) / float(len(resultados)) < FRACAO_FALHA:
        return False
    comum, _n = Counter(str(p).split(':')[0] for _c, _nm, p in falhas).most_common(1)[0]
    exemplo = next(r for r in falhas if str(r[2]).split(':')[0] == comum)
    with models.con() as c:
        ult = c.execute('SELECT detalhe FROM execucoes WHERE tipo=? AND cnpj=? ORDER BY id DESC LIMIT 1',
                        (_TIPO_EXEC.get(motor, motor), exemplo[0])).fetchone()
    detalhe = (ult['detalhe'] if ult else '') or str(exemplo[2])
    rot = _ROTULO_MOTOR.get(motor, motor)
    texto = ('⚠️ *Portal Fiscal Nescon — %s falhou*\n\n'
             'Na rodada de %s, *%d de %d* empresas consultadas falharam.\n'
             'Motivo mais comum: *%s*\n'
             'Exemplo: %s — %s\n\n'
             'Abra a página *%s* no sistema para ver cada empresa.') % (
        rot, datetime.now().strftime('%d/%m %H:%M'), len(falhas), len(resultados),
        _rotulo_parada(comum), (exemplo[1] or '').title(), detalhe[:200], rot)
    chave = 'falha:%s:%s' % (motor, dia or datetime.now().strftime('%Y-%m-%d'))
    return _enviar([chave], texto)
