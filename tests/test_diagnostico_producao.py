# -*- coding: utf-8 -*-
"""Correcoes da analise de producao (15/09/2026):
- NFC-e: SEFAZ-SP rejeita janela exatamente em 100 dias (cStat 104) -> folga.
- Certificado vencido: nao consulta (a SEFAZ devolve 403) e registra o motivo.
- NFC-e marcada fora de SP: registra o motivo em vez de sumir ('nunca').
Nunca bate na SEFAZ real."""
import os, sys, types, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))

import models
import worker
from engines import nfce_sp, pausa


def _db():
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    models.init_db()


def _ins(cnpj, nome, uf='SP', validade='2099-01-01', **flags):
    cols = {'cnpj': cnpj, 'nome': nome, 'uf': uf, 'validade': validade, 'senha_ok': 1, 'ativo': 1,
            'arquivo': 'x.pfx', 'senha': 'x', 'puxa_nfe': 0, 'puxa_nfse_prestado': 0,
            'puxa_nfse_tomado': 0, 'puxa_nfce': 0}
    cols.update(flags)
    with models.con() as c:
        c.execute('INSERT INTO empresas(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))),
                  list(cols.values()))


def test_janela_nfce_nunca_encosta_nos_100_dias():
    agora = datetime(2026, 9, 15, 0, 28)
    ini, fim = pausa.janela_nfce(datetime(2026, 6, 1), agora)
    assert (fim - ini).days < 100                      # 100 exatos = cStat 104 na SEFAZ-SP
    assert (fim - ini).days == 100 - pausa.NFCE_MARGEM_DIAS
    ini2, _ = pausa.janela_nfce(agora - timedelta(days=10), agora)
    assert ini2 == agora - timedelta(days=10)          # dentro do limite: nao mexe


def test_nfce_rejeicao_vira_cstat_com_motivo(monkeypatch):
    _db()
    _ins('11111111000111', 'LOJA SP', puxa_nfce=1)
    with models.con() as c:
        emp = c.execute("SELECT * FROM empresas WHERE cnpj='11111111000111'").fetchone()
    monkeypatch.setattr(nfce_sp, 'sessao', lambda a, s: (object(), []))
    def _rejeita(*a, **k):
        raise RuntimeError('listagem cStat=104 Rejeicao: dataHoraInicial anterior a 100 dias')
    monkeypatch.setattr(nfce_sp, 'listar_chaves', _rejeita)
    total, parada = nfce_sp.puxar_nfce(emp)
    assert (total, parada) == (0, 'cStat_104')
    with models.con() as c:
        det = c.execute("SELECT detalhe FROM execucoes WHERE tipo='nfce' ORDER BY id DESC").fetchone()[0]
    assert 'anterior a 100 dias' in det


def test_worker_pula_certificado_vencido_sem_consultar(monkeypatch):
    _db()
    ontem = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    _ins('22222222000122', 'VENCIDA', validade=ontem, puxa_nfe=1)
    _ins('33333333000133', 'VALIDA', puxa_nfe=1)
    chamadas = []
    monkeypatch.setattr(worker.nfe, 'puxar_entradas', lambda e: (chamadas.append(e['cnpj']) or (0, 'fim')))
    monkeypatch.setattr(worker.ciencia, 'dar_ciencia_pendentes', lambda e: (0, 'ok'))
    monkeypatch.setattr(worker, 'NFE_ENTRE_EMPRESAS', 0)
    jid = worker.enfileirar('nfe_entradas', origem='manual')
    job = worker._claim_proximo()
    worker._executar(job)
    assert chamadas == ['33333333000133']              # vencida nao foi para a SEFAZ
    with models.con() as c:
        r = c.execute("SELECT parada, detalhe FROM execucoes WHERE cnpj='22222222000122'").fetchone()
    assert r['parada'] == 'cert_vencido' and ontem in r['detalhe']


def test_worker_nfce_fora_de_sp_registra_motivo(monkeypatch):
    _db()
    _ins('44444444000144', 'LOJA BA', uf='BA', puxa_nfce=1)
    chamadas = []
    monkeypatch.setattr(worker.nfce_sp, 'puxar_nfce', lambda e: (chamadas.append(e['cnpj']) or (0, 'fim')))
    monkeypatch.setattr(worker, 'NFCE_ENTRE_EMPRESAS', 0)
    worker.enfileirar('nfce', origem='manual')
    worker._executar(worker._claim_proximo())
    assert chamadas == []
    with models.con() as c:
        assert c.execute("SELECT parada FROM execucoes WHERE cnpj='44444444000144'").fetchone()[0] == 'uf_nao_sp'
