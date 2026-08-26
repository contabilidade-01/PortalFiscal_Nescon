# -*- coding: utf-8 -*-
"""Motor de JOBS em background.
   - Roda numa thread do processo web -> executa mesmo com o usuario navegando
     em outra tela ou deslogado (o processo continua vivo).
   - Fila persistente em SQLite (tabela jobs) -> o status sobrevive e e visivel.
   - Claim atomico -> seguro se o run_diario (tarefa agendada) tambem processar.
   - Processa 1 job por vez e 1 empresa por vez (respeita anti-bloqueio).
   - Retomada: cap/limite/656/cooldown viram um job futuro (agendado_para) em vez
     de martelar a SEFAZ. No servidor o loop pega quando o horario chega.
"""
import os, threading, time
from datetime import datetime, timedelta
import models
from engines import nfe, nfse, ciencia, nfce_sp
from engines.pausa import (
    NFE_ENTRE_EMPRESAS, NFCE_ENTRE_EMPRESAS, RETOMAR,
    delay_retomada,
)

_started = False
_lock = threading.Lock()

HORAS_TRAVADO = 6
MAX_RETOMADAS_DIA = 250

def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def reconciliar_travados(tudo=False):
    """Job que ficou 'rodando' porque o processo morreu (reinicio do VPS, deploy,
       queda) nunca mais sai desse estado: a tela passa a mentir 'buscando...'
       (o famoso 'so girando') para sempre. Marca como interrompido.

       tudo=True  -> usado no STARTUP do worker web. Como so 1 processo processa a
                     fila, QUALQUER job em 'rodando' no boot e' orfao (o processo
                     que o rodava morreu). Reconcilia todos na hora — mata o
                     'so girando' no proximo deploy limpo.
       tudo=False -> usado pelo run_diario (pode rodar em paralelo ao web): so os
                     travados ha mais de HORAS_TRAVADO, p/ nao derrubar um job que
                     o outro processo esteja rodando agora."""
    if tudo:
        where, args = "status='rodando'", (agora(),)
    else:
        corte = (datetime.now() - timedelta(hours=HORAS_TRAVADO)).strftime('%Y-%m-%d %H:%M:%S')
        where, args = "status='rodando' AND (iniciado IS NULL OR iniciado < ?)", (agora(), corte)
    with models.con() as c:
        return c.execute("""UPDATE jobs SET status='interrompido', terminado=?, atual='',
                                   mensagem='Interrompida — o serviço reiniciou no meio da busca.'
                            WHERE %s""" % where, args).rowcount

def _ja_na_fila(tipo, escopo):
    with models.con() as c:
        r = c.execute("SELECT id FROM jobs WHERE tipo=? AND escopo=? AND status='fila'",
                      (tipo, escopo or 'todas')).fetchone()
        return r is not None

def _completo_agendado_hoje():
    """True se ja existe um 'completo' de origem 'agendado' criado hoje, ou um
    'completo' ainda ativo (fila/rodando). Evita sweep diario duplicado por restart."""
    dia = datetime.now().strftime('%Y-%m-%d')
    with models.con() as c:
        r = c.execute("""SELECT 1 FROM jobs
                         WHERE tipo='completo'
                           AND ( (origem='agendado' AND criado>=?)
                                 OR status IN ('fila','rodando') )
                         LIMIT 1""", (dia,)).fetchone()
        return r is not None

def _retomadas_hoje():
    dia = datetime.now().strftime('%Y-%m-%d')
    with models.con() as c:
        return c.execute("SELECT COUNT(*) FROM jobs WHERE origem='retomada' AND criado>=?",
                         (dia,)).fetchone()[0]

def enfileirar(tipo, escopo='todas', origem='manual', user_id=None, agendado_para=None):
    """tipo: nfe_entradas | nfe_saidas | nfse | ciencia | nfce | completo"""
    escopo = escopo or 'todas'
    if origem == 'retomada':
        if _ja_na_fila(tipo, escopo):
            return None
        if _retomadas_hoje() >= MAX_RETOMADAS_DIA:
            return None
    with models.con() as c:
        cur = c.execute(
            'INSERT INTO jobs(tipo,escopo,status,criado,origem,user_id,agendado_para) VALUES(?,?,?,?,?,?,?)',
            (tipo, escopo, 'fila', agora(), origem, user_id, agendado_para))
    return cur.lastrowid

def _enfileirar_retomada(tipo, escopo, parada, ate=None, motor='nfe'):
    delay = delay_retomada(parada, ate=ate, motor=motor)
    if delay is None:
        return None
    quando = (datetime.now() + timedelta(seconds=int(delay))).strftime('%Y-%m-%d %H:%M:%S')
    jid = enfileirar(tipo, escopo=escopo, origem='retomada', agendado_para=quando)
    return jid

def _upd(jid, **kw):
    if not kw: return
    sets = ','.join('%s=?' % k for k in kw)
    with models.con() as c:
        c.execute('UPDATE jobs SET %s WHERE id=?' % sets, tuple(kw.values()) + (jid,))

def _empresas(escopo, col):
    with models.con() as c:
        if escopo and escopo != 'todas':
            return c.execute('SELECT * FROM empresas WHERE cnpj=? AND senha_ok=1', (escopo,)).fetchall()
        return c.execute('SELECT * FROM empresas WHERE ativo=1 AND senha_ok=1 AND %s=1' % col).fetchall()

def _campo_emp(cnpj, col):
    with models.con() as c:
        r = c.execute('SELECT %s FROM empresas WHERE cnpj=?' % col, (cnpj,)).fetchone()
        return r[0] if r else None

def _agendar_se_precisa(tipo_job, cnpj, parada, motor='nfe'):
    if parada not in RETOMAR:
        return
    col = {'nfe': 'bloqueado_nfe_ate', 'nfce': 'bloqueado_nfce_ate',
           'nfse': 'bloqueado_nfse_ate'}.get(motor)
    ate = _campo_emp(cnpj, col) if (col and cnpj) else None
    if motor == 'nfe' and tipo_job == 'nfe_saidas':
        ate = models.get_param('bloqueado_saida_ate')
    _enfileirar_retomada(tipo_job, cnpj or 'todas', parada, ate=ate, motor=motor)

def _executar(job):
    jid, tipo, escopo = job['id'], job['tipo'], job['escopo']
    docs = 0
    def etapa_empresas(nome_etapa, col, fn, tipo_job, motor, com_ciencia=False, espera_seg=0):
        nonlocal docs
        emps = _empresas(escopo, col)
        _upd(jid, total=len(emps), feitos=0, mensagem=nome_etapa)
        feitos = 0
        for e in emps:
            _upd(jid, atual='%s - %s' % (nome_etapa, (e['nome'] or '')[:32]))
            parada = 'ok'
            try:
                d, parada = fn(e); docs += d
            except Exception:
                parada = 'erro'
            _agendar_se_precisa(tipo_job, e['cnpj'], parada, motor=motor)
            if com_ciencia:
                try:
                    # A ciencia roda aqui logo apos a entrada e volta no cron diario.
                    # NAO agenda retomada propria: a retomada da ENTRADA (com_ciencia)
                    # ja re-executa a ciencia. Sem isto, cada empresa em cooldown
                    # empilhava dezenas de jobs 'Ciencia 210210' (estouro de 250/dia).
                    ciencia.dar_ciencia_pendentes(e)
                except Exception:
                    pass
            feitos += 1
            _upd(jid, feitos=feitos, docs=docs)
            if espera_seg:
                time.sleep(espera_seg)
    if tipo in ('nfe_entradas', 'completo'):
        # Ciencia apos cada entrada: libera o XML completo na proxima distNSU.
        etapa_empresas('Entradas NF-e', 'puxa_nfe', nfe.puxar_entradas, 'nfe_entradas', 'nfe',
                       com_ciencia=True, espera_seg=NFE_ENTRE_EMPRESAS)
    if tipo == 'ciencia':
        emps = _empresas(escopo, 'puxa_nfe'); _upd(jid, total=len(emps), feitos=0, mensagem='Ciencia 210210')
        for i, e in enumerate(emps, 1):
            _upd(jid, atual='Ciencia - %s' % (e['nome'] or '')[:32])
            try:
                _n, p_c = ciencia.dar_ciencia_pendentes(e)
                # So reprograma se a PROPRIA ciencia levou 656 (bloqueio real com
                # pendencia). 'cooldown'/'cap' aqui e' redundante — o ciclo de
                # entrada da empresa re-executa a ciencia.
                if p_c == '656':
                    _agendar_se_precisa('ciencia', e['cnpj'], p_c, motor='nfe')
            except Exception:
                pass
            _upd(jid, feitos=i)
            time.sleep(NFE_ENTRE_EMPRESAS)
    if tipo in ('nfe_saidas', 'completo') and models.get_param('office_cnpj'):
        _upd(jid, atual='Saidas NF-e (escritorio/autXML)', mensagem='Saidas NF-e')
        parada = 'ok'
        try:
            d, parada = nfe.puxar_saidas_escritorio(); docs += d
        except Exception:
            parada = 'erro'
        _agendar_se_precisa('nfe_saidas', 'todas', parada, motor='nfe')
    if tipo in ('nfse', 'completo'):
        etapa_empresas('NFS-e', 'puxa_nfse', nfse.puxar_nfse, 'nfse', 'nfse',
                       espera_seg=NFE_ENTRE_EMPRESAS)
    if tipo in ('nfce', 'completo'):
        etapa_empresas('NFC-e (SP)', 'puxa_nfce', nfce_sp.puxar_nfce, 'nfce', 'nfce',
                       espera_seg=NFCE_ENTRE_EMPRESAS)
    return docs

def _claim_proximo():
    """Pega o proximo job cuja hora de inicio ja chegou (retomadas agendadas esperam)."""
    now = agora()
    with models.con() as c:
        job = c.execute("""SELECT * FROM jobs WHERE status='fila'
                           AND (agendado_para IS NULL OR agendado_para<=?)
                           ORDER BY id LIMIT 1""", (now,)).fetchone()
        claimed = 0
        if job:
            claimed = c.execute("UPDATE jobs SET status='rodando', iniciado=? WHERE id=? AND status='fila'",
                                (now, job['id'])).rowcount
        return job if claimed else None

def _loop():
    while True:
        try:
            job = _claim_proximo()
            if job:
                try:
                    docs = _executar(job)
                    _upd(job['id'], status='ok', terminado=agora(), docs=docs, atual='',
                         mensagem='Concluido - %d documentos' % docs)
                except Exception as ex:
                    _upd(job['id'], status='erro', terminado=agora(), mensagem='Erro: %s' % str(ex)[:150])
            else:
                time.sleep(3)
        except Exception:
            time.sleep(5)

def processar_fila_ate_vazia(limite=50):
    """Uso pelo run_diario: processa jobs JA LIBERADOS. Retomadas futuras ficam
    na fila para o worker do processo web (servidor) pegar no horario."""
    n = 0
    while n < limite:
        job = _claim_proximo()
        if not job: break
        try:
            docs = _executar(job)
            _upd(job['id'], status='ok', terminado=agora(), docs=docs, atual='', mensagem='Concluido - %d documentos' % docs)
        except Exception as ex:
            _upd(job['id'], status='erro', terminado=agora(), mensagem='Erro: %s' % str(ex)[:150])
        n += 1
    return n

def _loop_agendado():
    """Enfileira o job 'completo' 1x/dia no horario FISCAL_CRON_HORA (HH:MM, TZ do container).
       So dispara na janela de 2 min apos o alvo — restart as 10h nao puxa a SEFAZ de novo."""
    hora_s = os.environ.get('FISCAL_CRON_HORA', '06:00')
    try:
        hh, mm = [int(x) for x in hora_s.split(':', 1)]
    except ValueError:
        hh, mm = 6, 0
    last = None
    while True:
        try:
            now = datetime.now()
            alvo = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            stamp = now.strftime('%Y-%m-%d')
            if last != stamp and now >= alvo and (now - alvo).total_seconds() < 120:
                last = stamp
                # Dedup persistente: se um 'completo' agendado ja foi criado hoje
                # (ou ainda esta na fila/rodando), NAO cria outro. Sem isto, cada
                # restart do container na janela das 06:00 dispara um sweep extra
                # -> varredura duplicada martelando a SEFAZ (656 em massa).
                if not _completo_agendado_hoje():
                    enfileirar('completo', origem='agendado')
        except Exception:
            pass
        time.sleep(20)

def iniciar_worker():
    global _started
    with _lock:
        if _started: return
        _started = True
        # Startup do worker web: limpa TODO job orfao ('so girando' de deploy anterior).
        try: reconciliar_travados(tudo=True)
        except Exception: pass
        threading.Thread(target=_loop, daemon=True).start()
        if os.environ.get('FISCAL_CRON', '0') == '1':
            threading.Thread(target=_loop_agendado, daemon=True, name='fiscal-cron').start()

def status():
    with models.con() as c:
        rodando = c.execute("SELECT * FROM jobs WHERE status='rodando' ORDER BY id DESC LIMIT 1").fetchone()
        fila = c.execute("""SELECT COUNT(*) FROM jobs WHERE status='fila'
                            AND (agendado_para IS NULL OR agendado_para<=?)""", (agora(),)).fetchone()[0]
        recentes = c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 10').fetchall()
    return rodando, fila, recentes
