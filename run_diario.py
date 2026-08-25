# -*- coding: utf-8 -*-
"""Robo diario (Tarefa Agendada do Windows / cron).
   Enfileira um job 'completo'. Quem PROCESSA a fila depende de FISCAL_ROLE:

   - web  : so enfileira (o worker do app.py processa). Use no EasyPanel
            quando FISCAL_CRON=1 ja esta ligado — evita dois processos NFC-e
            no mesmo IP (G4).
   - cron : enfileira E processa (Tarefa Agendada no Windows, app desligado).
   - auto : se FISCAL_CRON=1 assume web; senao cron.
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

def _papel():
    role = (os.environ.get('FISCAL_ROLE') or '').strip().lower()
    if role in ('web', 'cron'):
        return role
    return 'web' if os.environ.get('FISCAL_CRON') == '1' else 'cron'

def main():
    models.init_db()
    papel = _papel()
    travados = worker.reconciliar_travados()
    if travados:
        log('%d job(s) orfao(s) de execucao anterior marcados como interrompidos' % travados)
    log('===== run_diario: enfileirando job completo (papel=%s) =====' % papel)
    jid = worker.enfileirar('completo', origem='agendado')
    if papel == 'web':
        log('papel=web: so enfileirou job#%s — o worker do app processa (nao dobra NFC-e no IP)'
            % jid)
        return
    n = worker.processar_fila_ate_vazia()
    with models.con() as c:
        j = c.execute('SELECT status,docs,mensagem FROM jobs WHERE id=?', (jid,)).fetchone()
    log('===== fim: job#%d status=%s docs=%s (%d job(s) processado(s)) ====='
        % (jid, j['status'] if j else '?', j['docs'] if j else '?', n))

if __name__ == '__main__':
    main()
