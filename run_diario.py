# -*- coding: utf-8 -*-
"""Robo diario (Tarefa Agendada do Windows / cron).
   Enfileira um job 'completo' e processa a fila -> unifica o status na tabela jobs
   (visivel na interface). Funciona mesmo sem ninguem logado.
"""
import os, sys
from datetime import datetime
BASE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BASE)
import models, worker

LOG = os.path.join(os.environ.get('FISCAL_DATA_DIR', BASE), 'logs')
os.makedirs(LOG, exist_ok=True)

def log(m):
    linha = '%s  %s' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m)
    print(linha)
    with open(os.path.join(LOG, 'run_diario.log'), 'a', encoding='utf-8') as f:
        f.write(linha + '\n')

def main():
    models.init_db()
    travados = worker.reconciliar_travados()
    if travados:
        log('%d job(s) orfao(s) de execucao anterior marcados como interrompidos' % travados)
    log('===== run_diario: enfileirando job completo =====')
    jid = worker.enfileirar('completo', origem='agendado')
    n = worker.processar_fila_ate_vazia()
    with models.con() as c:
        j = c.execute('SELECT status,docs,mensagem FROM jobs WHERE id=?', (jid,)).fetchone()
    log('===== fim: job#%d status=%s docs=%s (%d job(s) processado(s)) ====='
        % (jid, j['status'] if j else '?', j['docs'] if j else '?', n))

if __name__ == '__main__':
    main()
