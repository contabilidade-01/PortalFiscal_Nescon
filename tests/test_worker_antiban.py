# -*- coding: utf-8 -*-
"""Melhorias operacionais do worker (jobs orfaos, dedup do completo, buffer de
retomada, sem enxurrada de ciencia). Stub de xmlsec p/ importar worker sem a lib
nativa (so a Ciencia usa xmlsec, e nao em import-time)."""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))  # ciencia importa xmlsec

import models
from engines.pausa import delay_retomada, RETOMAR_BUFFER_SEG
import worker


def _db():
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    models.init_db()
    return d


def _novo_job(status='rodando', iniciado=None, tipo='nfce', origem='manual', criado=None):
    with models.con() as c:
        cur = c.execute(
            'INSERT INTO jobs(tipo,escopo,status,criado,iniciado,origem) VALUES(?,?,?,?,?,?)',
            (tipo, 'todas', status, criado or worker.agora(), iniciado, origem))
        return cur.lastrowid


def test_item1_reconcilia_orfao_no_startup():
    _db()
    jid = _novo_job(status='rodando', iniciado=worker.agora())  # acabou de comecar
    # tudo=False (run_diario): nao mexe num job recente
    worker.reconciliar_travados(tudo=False)
    with models.con() as c:
        assert c.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'rodando'
    # tudo=True (startup do worker web): qualquer 'rodando' e orfao -> interrompe
    n = worker.reconciliar_travados(tudo=True)
    with models.con() as c:
        assert c.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'interrompido'
    assert n >= 1


def test_item3_dedup_completo_agendado():
    _db()
    assert worker._completo_agendado_hoje() is False
    worker.enfileirar('completo', origem='agendado')
    assert worker._completo_agendado_hoje() is True  # ja criado hoje -> nao cria outro


def test_item3_dedup_completo_em_execucao():
    _db()
    _novo_job(status='rodando', tipo='completo', origem='manual')
    assert worker._completo_agendado_hoje() is True  # ha um completo ativo


def test_item2_buffer_no_retomar():
    _db()
    # sem 'ate': cooldown NFe = 65 min + buffer
    assert delay_retomada('cooldown', motor='nfe') == 65 * 60 + RETOMAR_BUFFER_SEG
    assert delay_retomada('656', motor='nfce') == 65 * 60 + RETOMAR_BUFFER_SEG
    # 'cap' e 'limite' NAO ganham buffer (nao sao pausa da SEFAZ)
    assert delay_retomada('cap') == worker_cap()


def worker_cap():
    from engines.pausa import NFE_RETOMAR_CAP_SEG
    return NFE_RETOMAR_CAP_SEG


if __name__ == '__main__':
    test_item1_reconcilia_orfao_no_startup()
    test_item3_dedup_completo_agendado()
    test_item3_dedup_completo_em_execucao()
    test_item2_buffer_no_retomar()
    print('ok worker antiban')
