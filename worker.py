# -*- coding: utf-8 -*-
"""Motor de JOBS em background.
   - Roda numa thread do processo web -> executa mesmo com o usuario navegando
     em outra tela ou deslogado (o processo continua vivo).
   - Fila persistente em SQLite (tabela jobs) -> o status sobrevive e e visivel.
   - Claim atomico -> seguro se o run_diario (tarefa agendada) tambem processar.
   - Processa 1 job por vez e 1 empresa por vez (respeita anti-bloqueio).
"""
import threading, time
from datetime import datetime, timedelta
import models
from engines import nfe, nfse, ciencia, nfce_sp

_started = False
_lock = threading.Lock()

# Nenhuma busca real chega perto disso; acima daqui o processo morreu no meio.
HORAS_TRAVADO = 6

def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def reconciliar_travados():
    """Job que ficou 'rodando' porque o processo morreu (reinicio do VPS, deploy,
       queda) nunca mais sai desse estado: a tela passa a mentir 'buscando...'
       para sempre. Marca como interrompido. O corte de horas evita derrubar um
       job que o run_diario esteja processando agora, em paralelo ao web."""
    corte = (datetime.now() - timedelta(hours=HORAS_TRAVADO)).strftime('%Y-%m-%d %H:%M:%S')
    with models.con() as c:
        return c.execute("""UPDATE jobs SET status='interrompido', terminado=?, atual='',
                                   mensagem='Interrompida — o serviço parou no meio da busca.'
                            WHERE status='rodando' AND (iniciado IS NULL OR iniciado < ?)""",
                         (agora(), corte)).rowcount

def enfileirar(tipo, escopo='todas', origem='manual', user_id=None):
    """tipo: nfe_entradas | nfe_saidas | nfse | ciencia | completo"""
    with models.con() as c:
        cur = c.execute('INSERT INTO jobs(tipo,escopo,status,criado,origem,user_id) VALUES(?,?,?,?,?,?)',
                        (tipo, escopo, 'fila', agora(), origem, user_id))
        return cur.lastrowid

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

def _executar(job):
    jid, tipo, escopo = job['id'], job['tipo'], job['escopo']
    docs = 0
    def etapa_empresas(nome_etapa, col, fn, com_ciencia=False):
        nonlocal docs
        emps = _empresas(escopo, col)
        _upd(jid, total=len(emps), feitos=0, mensagem=nome_etapa)
        feitos = 0
        for e in emps:
            _upd(jid, atual='%s - %s' % (nome_etapa, (e['nome'] or '')[:32]))
            try:
                d, _p = fn(e); docs += d
                if com_ciencia:
                    try: ciencia.dar_ciencia_pendentes(e)
                    except Exception: pass
            except Exception:
                pass
            feitos += 1
            _upd(jid, feitos=feitos, docs=docs)
    if tipo in ('nfe_entradas', 'completo'):
        etapa_empresas('Entradas NF-e', 'puxa_nfe', nfe.puxar_entradas, com_ciencia=(tipo == 'completo'))
    if tipo == 'ciencia':
        emps = _empresas(escopo, 'puxa_nfe'); _upd(jid, total=len(emps), feitos=0, mensagem='Ciencia 210210')
        for i, e in enumerate(emps, 1):
            _upd(jid, atual='Ciencia - %s' % (e['nome'] or '')[:32])
            try: ciencia.dar_ciencia_pendentes(e)
            except Exception: pass
            _upd(jid, feitos=i)
    if tipo in ('nfe_saidas', 'completo') and models.get_param('office_cnpj'):
        _upd(jid, atual='Saidas NF-e (escritorio/autXML)', mensagem='Saidas NF-e')
        try:
            d, _p = nfe.puxar_saidas_escritorio(); docs += d
        except Exception:
            pass
    if tipo in ('nfse', 'completo'):
        etapa_empresas('NFS-e', 'puxa_nfse', nfse.puxar_nfse)
    if tipo in ('nfce', 'completo'):
        etapa_empresas('NFC-e (SP)', 'puxa_nfce', nfce_sp.puxar_nfce)
    return docs

def _loop():
    while True:
        try:
            with models.con() as c:
                job = c.execute("SELECT * FROM jobs WHERE status='fila' ORDER BY id LIMIT 1").fetchone()
                claimed = 0
                if job:
                    claimed = c.execute("UPDATE jobs SET status='rodando', iniciado=? WHERE id=? AND status='fila'",
                                        (agora(), job['id'])).rowcount
            if job and claimed:
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
    """Uso pelo run_diario: processa jobs enfileirados sem depender do web."""
    n = 0
    while n < limite:
        with models.con() as c:
            job = c.execute("SELECT * FROM jobs WHERE status='fila' ORDER BY id LIMIT 1").fetchone()
            if not job: break
            claimed = c.execute("UPDATE jobs SET status='rodando', iniciado=? WHERE id=? AND status='fila'",
                                (agora(), job['id'])).rowcount
        if not claimed: continue
        try:
            docs = _executar(job)
            _upd(job['id'], status='ok', terminado=agora(), docs=docs, atual='', mensagem='Concluido - %d documentos' % docs)
        except Exception as ex:
            _upd(job['id'], status='erro', terminado=agora(), mensagem='Erro: %s' % str(ex)[:150])
        n += 1
    return n

def iniciar_worker():
    global _started
    with _lock:
        if _started: return
        _started = True
        try: reconciliar_travados()   # limpa job orfao do processo anterior
        except Exception: pass
        threading.Thread(target=_loop, daemon=True).start()

def status():
    with models.con() as c:
        rodando = c.execute("SELECT * FROM jobs WHERE status='rodando' ORDER BY id DESC LIMIT 1").fetchone()
        fila = c.execute("SELECT COUNT(*) FROM jobs WHERE status='fila'").fetchone()[0]
        recentes = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 10").fetchall()
    return rodando, fila, recentes
