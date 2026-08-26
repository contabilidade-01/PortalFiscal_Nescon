# -*- coding: utf-8 -*-
"""Testes do plano anti-ban SEFAZ (G1–G5 + Guard). Nunca bate na SEFAZ real."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models
from engines.pausa import delay_retomada, RETOMAR
from engines import guard, nfe

_CNPJ = '11222333000181'


def _db():
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    models.init_db()
    with models.con() as c:
        c.execute(
            "INSERT INTO empresas(cnpj,nome,ativo,senha_ok,arquivo,ultnsu_nfe) VALUES(?,?,1,1,'x.pfx','000000000000000')",
            (_CNPJ, 'Empresa Teste'))
    return d


def _emp():
    with models.con() as c:
        return c.execute('SELECT * FROM empresas WHERE cnpj=?', (_CNPJ,)).fetchone()


def test_g1_pode_consultar_janela():
    _db()
    old = models.LIMITES['distNSU']
    models.LIMITES['distNSU'] = (2, 60)
    try:
        assert models.pode_consultar(_CNPJ, 'distNSU') is True
        models.registrar_consulta(_CNPJ, 'distNSU')
        models.registrar_consulta(_CNPJ, 'distNSU')
        assert models.pode_consultar(_CNPJ, 'distNSU') is False
    finally:
        models.LIMITES['distNSU'] = old


def test_g1_motor_nfe_nao_posta_no_limite():
    _db()
    old = models.LIMITES['distNSU']
    models.LIMITES['distNSU'] = (2, 60)
    called = []
    orig = nfe._post
    nfe._post = lambda *a, **k: called.append(1) or (200, '<cStat>138</cStat>')
    try:
        models.registrar_consulta(_CNPJ, 'distNSU')
        models.registrar_consulta(_CNPJ, 'distNSU')
        docs, parada = nfe.puxar_entradas(_emp())
        assert docs == 0
        assert parada == 'limite'
        assert called == []
    finally:
        nfe._post = orig
        models.LIMITES['distNSU'] = old


def test_g1_delay_limite_nfe_diferente_nfce():
    assert delay_retomada('limite', motor='nfce') == 90
    assert delay_retomada('limite', motor='nfe') == 60


def test_g2_ciencia_656_grava_cooldown():
    _db()
    guard.registrar_bloqueio(_CNPJ, 'ciencia', '656', 'consumo indevido', nome='Empresa Teste')
    e = _emp()
    assert e['bloqueado_nfe_ate']
    d = guard.pode(_CNPJ, 'distNSU')
    assert d.liberado is False
    assert d.parada in ('cooldown', 'circuito_aberto')


def test_g3_circuito_abre_no_limiar():
    _db()
    orig = guard.CIRCUITO_LIMIAR
    guard.CIRCUITO_LIMIAR = 5
    try:
        for _ in range(5):
            guard.registrar_bloqueio(_CNPJ, 'distNSU', '656')
        d = guard.pode(_CNPJ, 'distNSU')
        assert d.liberado is False
        assert d.parada == 'circuito_aberto'
        e = _emp()
        assert e['circuito_nfe'] == 1
        assert e['bloqueios_seguidos_nfe'] >= 5
        guard.rearmar(_CNPJ, 'distNSU', quem='teste')
        d2 = guard.pode(_CNPJ, 'distNSU')
        assert d2.liberado is True
    finally:
        guard.CIRCUITO_LIMIAR = orig


def test_g5_109_retomavel_e_cooldown():
    _db()
    assert '109' in RETOMAR
    from engines.pausa import RETOMAR_BUFFER_SEG
    assert delay_retomada('109') == 65 * 60 + RETOMAR_BUFFER_SEG
    guard.registrar_bloqueio(_CNPJ, 'distNSU', '109')
    e = _emp()
    assert e['bloqueado_nfe_ate']
    assert e['circuito_nfe'] in (0, None)
    d = guard.pode(_CNPJ, 'distNSU')
    assert d.liberado is False
    assert d.parada == 'cooldown'


def test_g4_rate_gate_avanca_relogio():
    _db()
    guard.esperar_cadencia('nfce', 0.01)
    with models.con() as c:
        row = c.execute("SELECT proximo_permitido_em FROM rate_gate WHERE servico='nfce'").fetchone()
    assert row and row['proximo_permitido_em']


def test_ocorrencia_gravada():
    _db()
    guard.registrar_bloqueio(_CNPJ, 'distNSU', '656')
    with models.con() as c:
        n = c.execute('SELECT COUNT(*) FROM ocorrencias_sefaz WHERE cnpj=?', (_CNPJ,)).fetchone()[0]
    assert n >= 1


def test_diagnostico_persistencia_local():
    _db()
    d = models.diagnostico_persistencia()
    assert d['empresas'] >= 1
    assert d['risco_apagar_no_deploy'] is False


def test_f_uazapi_sem_credencial_nao_configura():
    from engines import uazapi
    for k in ('UAZAPI_SUBDOMAIN', 'UAZAPI_TOKEN'):
        os.environ.pop(k, None)
    assert uazapi.configurado() is False
    # status nunca lanca, mesmo sem credencial
    assert uazapi.status_instancia()['categoria'] == 'nao_configurado'


def test_f_circuito_dispara_alerta():
    """Melhoria F: abrir o circuito chama o alerta de WhatsApp (aqui interceptado)."""
    _db()
    chamado = {}
    orig_alerta, orig_lim = guard._alertar_circuito, guard.CIRCUITO_LIMIAR
    guard._alertar_circuito = lambda servico, cnpj, nome, seg: chamado.update(
        servico=servico, cnpj=cnpj, seg=seg)
    guard.CIRCUITO_LIMIAR = 5
    try:
        for _ in range(5):
            guard.registrar_bloqueio(_CNPJ, 'distNSU', '656')
        assert chamado.get('servico') == 'distNSU'
        assert chamado.get('cnpj') == _CNPJ
        assert chamado.get('seg') >= 5
    finally:
        guard._alertar_circuito, guard.CIRCUITO_LIMIAR = orig_alerta, orig_lim


if __name__ == '__main__':
    test_g1_pode_consultar_janela()
    test_g1_motor_nfe_nao_posta_no_limite()
    test_g1_delay_limite_nfe_diferente_nfce()
    test_g2_ciencia_656_grava_cooldown()
    test_g3_circuito_abre_no_limiar()
    test_g5_109_retomavel_e_cooldown()
    test_g4_rate_gate_avanca_relogio()
    test_ocorrencia_gravada()
    test_diagnostico_persistencia_local()
    print('ok antiban')
