# -*- coding: utf-8 -*-
"""SefazGuard — porteiro unico anti-ban (NT 2014.002 + SAE NFC-e SP).

Toda consulta passa por pode() / registrar_ok() / registrar_bloqueio().
Os motores nao decidem cooldown, circuito ou cadencia por conta propria.
Desligar: SEFAZ_GUARD_ATIVO=0 (cai no cooldown legado das colunas).
"""
import os, json, logging, random, time, threading
from datetime import datetime, timedelta
import models
from engines.pausa import (
    cooldown_ate, em_cooldown, NFE_COOLDOWN_MIN, NFCE_COOLDOWN_MIN,
    delay_retomada,
)

log = logging.getLogger('sefaz.guard')

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = json.load(open(os.path.join(_BASE, 'config.json'), encoding='utf-8'))
_AB = _CFG.get('antiban') or {}

CIRCUITO_LIMIAR = int(os.environ.get('SEFAZ_CIRCUITO_LIMIAR', _AB.get('circuito_limiar', 5)))
GUARD_ATIVO = os.environ.get('SEFAZ_GUARD_ATIVO', '1') != '0'
# WhatsApp de alerta (Melhoria F): so dispara quando o circuito ABRE. Numero
# configuravel por env; default e o responsavel do escritorio.
ALERTA_WHATSAPP = os.environ.get('SEFAZ_ALERTA_WHATSAPP', '11984630568').strip()

# 656/429 incrementam o disjuntor; 108/109 so cooldown.
INCREMENTA_CIRCUITO = frozenset(('656', '429'))
COOLDOWN_CSTAT = frozenset(('137', '656', 'fim', '108', '109', '429', 'cooldown'))

# servico interno -> colunas em empresas (ciencia compartilha NFe)
_COLS = {
    'distNSU': ('bloqueado_nfe_ate', 'bloqueios_seguidos_nfe', 'circuito_nfe'),
    'ciencia': ('bloqueado_nfe_ate', 'bloqueios_seguidos_nfe', 'circuito_nfe'),
    'nfce': ('bloqueado_nfce_ate', 'bloqueios_seguidos_nfce', 'circuito_nfce'),
    'nfse': ('bloqueado_nfse_ate', 'bloqueios_seguidos_nfse', 'circuito_nfse'),
}


class Decisao:
    def __init__(self, liberado, motivo='', liberado_em=None, parada=''):
        self.liberado = bool(liberado)
        self.motivo = motivo or ''
        self.liberado_em = liberado_em
        self.parada = parada or ''


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _emp(cnpj):
    with models.con() as c:
        return c.execute('SELECT * FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()


def _nome(cnpj):
    e = _emp(cnpj)
    return (e['nome'] if e else '') or ''


def _saida_circuito():
    return (models.get_param('circuito_saida') or '0') == '1'


def _saida_seguidos():
    try:
        return int(models.get_param('bloqueios_seguidos_saida') or '0')
    except ValueError:
        return 0


def pode(cnpj, servico):
    """Decide se a consulta pode sair agora.

    servico: distNSU | ciencia | nfce | nfse | saida
    """
    if not GUARD_ATIVO:
        return _pode_legado(cnpj, servico)

    if servico == 'saida':
        if _saida_circuito():
            return Decisao(False, 'circuito aberto (saídas)', parada='circuito_aberto')
        ate = models.get_param('bloqueado_saida_ate')
        if em_cooldown(ate):
            return Decisao(False, 'cooldown saídas até %s' % ate, ate, 'cooldown')
        office = models.get_param('office_cnpj') or cnpj
        if not models.pode_consultar(office, 'distNSU'):
            return Decisao(False, 'limite horário distNSU (escritório)', parada='limite')
        return Decisao(True)

    cols = _COLS.get(servico)
    if not cols:
        return Decisao(True)
    col_ate, col_seg, col_circ = cols
    e = _emp(cnpj)
    if not e:
        return Decisao(True)
    if e[col_circ]:
        return Decisao(False, 'circuito aberto', parada='circuito_aberto')
    ate = e[col_ate]
    if em_cooldown(ate):
        return Decisao(False, 'cooldown até %s' % ate, ate, 'cooldown')
    janela = 'distNSU' if servico in ('distNSU', 'ciencia') else servico
    if servico in ('distNSU', 'ciencia', 'nfse') and not models.pode_consultar(cnpj, janela):
        return Decisao(False, 'limite da janela deslizante', parada='limite')
    return Decisao(True)


def _pode_legado(cnpj, servico):
    """SEFAZ_GUARD_ATIVO=0: so o cooldown das colunas, sem circuito/janela."""
    if servico == 'saida':
        ate = models.get_param('bloqueado_saida_ate')
        if em_cooldown(ate):
            return Decisao(False, 'cooldown', ate, 'cooldown')
        return Decisao(True)
    cols = _COLS.get(servico)
    if not cols:
        return Decisao(True)
    e = _emp(cnpj)
    if e and em_cooldown(e[cols[0]]):
        return Decisao(False, 'cooldown', e[cols[0]], 'cooldown')
    return Decisao(True)


def registrar_ok(cnpj, servico, nome=''):
    """Consulta bem-sucedida: zera bloqueios consecutivos."""
    if servico == 'saida':
        models.set_param('bloqueios_seguidos_saida', '0')
        return
    cols = _COLS.get(servico)
    if not cols:
        return
    _, col_seg, _ = cols
    with models.con() as c:
        c.execute('UPDATE empresas SET %s=0 WHERE cnpj=?' % col_seg, (cnpj,))
    log.info('ok %s %s', servico, cnpj)


def registrar_bloqueio(cnpj, servico, cstat, xmot='', nome=''):
    """656/429/108/109/137/fim/limite: grava cooldown, ocorrencia e (se 656/429) circuito."""
    cstat = str(cstat or '')
    nome = nome or _nome(cnpj)
    mins = NFCE_COOLDOWN_MIN if servico == 'nfce' else NFE_COOLDOWN_MIN
    ate = None
    if cstat in COOLDOWN_CSTAT or cstat == 'limite':
        if cstat == 'limite':
            ate = cooldown_ate(1)  # janela: retoma em ~1 min (worker usa delay_retomada)
        else:
            ate = cooldown_ate(mins)

    seguidos = 0
    circuito = False

    if servico == 'saida':
        if cstat in INCREMENTA_CIRCUITO:
            seguidos = _saida_seguidos() + 1
            models.set_param('bloqueios_seguidos_saida', str(seguidos))
            if seguidos >= CIRCUITO_LIMIAR:
                models.set_param('circuito_saida', '1')
                circuito = True
                cstat_tipo = 'circuito'
            else:
                cstat_tipo = cstat
        else:
            cstat_tipo = cstat
        if ate and cstat != 'limite':
            models.set_param('bloqueado_saida_ate', ate)
        _gravar_ocorrencia(cnpj, nome, servico, cstat, xmot, cstat_tipo, ate, seguidos)
        if circuito:
            log.warning('CIRCUITO ABERTO saida %s apos %d bloqueios', cnpj, seguidos)
            _alertar_circuito('saida', cnpj, nome, seguidos)
        return ate

    cols = _COLS.get(servico)
    if not cols:
        return ate
    col_ate, col_seg, col_circ = cols
    with models.con() as c:
        e = c.execute('SELECT * FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
        if not e:
            _gravar_ocorrencia(cnpj, nome, servico, cstat, xmot, cstat, ate, 0)
            return ate
        seguidos = int(e[col_seg] or 0)
        if cstat in INCREMENTA_CIRCUITO:
            seguidos += 1
        elif cstat in ('fim', '137', 'ok'):
            seguidos = 0
        sets = ['ultimo_bloqueio_motivo=?']
        args = ['%s:%s' % (servico, cstat)]
        if ate and cstat != 'limite':
            sets.append('%s=?' % col_ate)
            args.append(ate)
        if cstat in INCREMENTA_CIRCUITO or cstat in ('fim', '137', 'ok'):
            sets.append('%s=?' % col_seg)
            args.append(seguidos)
            if seguidos >= CIRCUITO_LIMIAR:
                sets.append('%s=1' % col_circ)
                circuito = True
        args.append(cnpj)
        c.execute('UPDATE empresas SET %s WHERE cnpj=?' % ','.join(sets), args)

    tipo = 'circuito' if circuito else cstat
    _gravar_ocorrencia(cnpj, nome, servico, cstat, xmot, tipo, ate, seguidos)
    if circuito:
        log.warning('CIRCUITO ABERTO %s %s apos %d bloqueios', servico, cnpj, seguidos)
        _alertar_circuito(servico, cnpj, nome, seguidos)
    else:
        log.info('bloqueio %s %s cStat=%s ate=%s', servico, cnpj, cstat, ate)
    return ate


def _gravar_ocorrencia(cnpj, nome, servico, cstat, xmot, tipo, bloqueado_ate, seguidos):
    with models.con() as c:
        c.execute(
            '''INSERT INTO ocorrencias_sefaz
               (cnpj,nome,servico,cstat,xmotivo,tipo,quando,bloqueado_ate,bloqueios_seguidos,resolvido)
               VALUES(?,?,?,?,?,?,?,?,?,0)''',
            (cnpj, nome, servico, cstat, (xmot or '')[:240], tipo, agora(),
             bloqueado_ate, seguidos))


_SERVICO_ROTULO = {'distNSU': 'NF-e (distribuição)', 'ciencia': 'Ciência 210210',
                   'nfce': 'NFC-e (SP)', 'nfse': 'NFS-e', 'saida': 'Saídas NF-e (escritório)'}


def _alertar_circuito(servico, cnpj, nome, seguidos):
    """Avisa no WhatsApp que o disjuntor abriu (exige rearme humano). Nunca bloqueia
    o worker: roda numa thread e engole qualquer falha (uazapi fora do ar, token, etc.)."""
    if not ALERTA_WHATSAPP:
        return

    def _job():
        try:
            from engines import uazapi
            if not uazapi.configurado():
                log.info('circuito aberto sem alerta: uazapi nao configurada')
                return
            texto = (
                '🚨 *Portal Fiscal Nescon — CIRCUITO ABERTO*\n\n'
                'Serviço: %s\n'
                'Empresa: %s (%s)\n'
                'Bloqueios seguidos: %d (limiar %d)\n\n'
                'As consultas desse serviço foram PARADAS para evitar bloqueio '
                'permanente na SEFAZ. É preciso *rearme manual* na tela "Saúde SEFAZ".'
            ) % (_SERVICO_ROTULO.get(servico, servico), nome or '?', cnpj or '?',
                 seguidos, CIRCUITO_LIMIAR)
            uazapi.enviar_texto(ALERTA_WHATSAPP, texto)
            log.info('alerta de circuito enviado para %s', ALERTA_WHATSAPP)
        except Exception:
            log.warning('falha ao enviar alerta WhatsApp de circuito', exc_info=True)

    threading.Thread(target=_job, daemon=True).start()


def rearmar(cnpj, servico, quem=''):
    """Zera circuito e contador. Exige acao humana (tela de Saude SEFAZ)."""
    if servico == 'saida':
        models.set_param('circuito_saida', '0')
        models.set_param('bloqueios_seguidos_saida', '0')
        models.set_param('bloqueado_saida_ate', '')
        _gravar_ocorrencia(cnpj, _nome(cnpj) or 'ESCRITORIO', 'saida', 'rearme',
                           'por %s' % (quem or '?'), 'rearme', None, 0)
        return True
    cols = _COLS.get(servico)
    if not cols:
        return False
    col_ate, col_seg, col_circ = cols
    with models.con() as c:
        c.execute(
            'UPDATE empresas SET %s=0, %s=0, %s=NULL, ultimo_bloqueio_motivo=? WHERE cnpj=?'
            % (col_circ, col_seg, col_ate),
            ('rearme:%s' % (quem or ''), cnpj))
    _gravar_ocorrencia(cnpj, _nome(cnpj), servico, 'rearme',
                       'por %s' % (quem or '?'), 'rearme', None, 0)
    return True


def proxima_retomada(parada, ate=None, motor='nfe'):
    return delay_retomada(parada, ate=ate, motor=motor)


def esperar_cadencia(servico, espera_seg):
    """Cadencia global por banco (cross-process) + jitter ±20%."""
    espera = max(0.2, float(espera_seg) * random.uniform(0.8, 1.2))
    now = datetime.now()
    with models.con() as c:
        row = c.execute('SELECT proximo_permitido_em FROM rate_gate WHERE servico=?',
                        (servico,)).fetchone()
        alvo = now
        if row and row['proximo_permitido_em']:
            try:
                alvo = datetime.strptime(row['proximo_permitido_em'][:19], '%Y-%m-%d %H:%M:%S')
            except (TypeError, ValueError):
                alvo = now
        wait = (alvo - now).total_seconds()
        prox = max(alvo, now) + timedelta(seconds=espera)
        c.execute(
            '''INSERT INTO rate_gate(servico, proximo_permitido_em) VALUES(?,?)
               ON CONFLICT(servico) DO UPDATE SET proximo_permitido_em=excluded.proximo_permitido_em''',
            (servico, prox.strftime('%Y-%m-%d %H:%M:%S')))
    if wait > 0:
        time.sleep(wait)


def sleep_jitter(base):
    time.sleep(max(0.2, float(base) * random.uniform(0.8, 1.2)))
