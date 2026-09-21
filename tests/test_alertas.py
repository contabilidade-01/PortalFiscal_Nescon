# -*- coding: utf-8 -*-
"""Prevencao: alerta de certificado vencendo, alerta de falha geral e NFC-e so SP.
Nunca envia WhatsApp de verdade (uazapi interceptada)."""
import os, sys, types, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault('xmlsec', types.ModuleType('xmlsec'))

import models
import worker
from engines import alertas, uazapi, guard

HOJE = date(2026, 9, 15)


def _db():
    d = tempfile.mkdtemp()
    models.DB = os.path.join(d, 't.db')
    models.init_db()


def _ins(cnpj, nome, validade, uf='SP', **flags):
    cols = {'cnpj': cnpj, 'nome': nome, 'uf': uf, 'validade': validade, 'senha_ok': 1, 'ativo': 1,
            'arquivo': 'x.pfx', 'puxa_nfe': 1, 'puxa_nfe_saida': 0, 'puxa_nfse_prestado': 0,
            'puxa_nfse_tomado': 0, 'puxa_nfce': 0}
    cols.update(flags)
    with models.con() as c:
        c.execute('INSERT INTO empresas(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))),
                  list(cols.values()))


def _captura(monkeypatch, falha=False):
    enviados = []
    monkeypatch.setattr(alertas, 'ASSINCRONO', False)
    monkeypatch.setattr(uazapi, 'configurado', lambda: True)
    monkeypatch.setattr(guard, 'ALERTA_WHATSAPP', '11984630568')
    def _envia(numero, texto):
        if falha:
            raise RuntimeError('uazapi fora')
        enviados.append((numero, texto))
    monkeypatch.setattr(uazapi, 'enviar_texto', _envia)
    return enviados


def test_marcos_do_certificado():
    assert alertas.marco_certificado(-1) == 'vencido'
    assert alertas.marco_certificado(0) == '7'
    assert alertas.marco_certificado(7) == '7'
    assert alertas.marco_certificado(8) == '15'
    assert alertas.marco_certificado(30) == '30'
    assert alertas.marco_certificado(31) is None


def test_alerta_certificado_agrupa_e_nao_repete(monkeypatch):
    _db()
    env = _captura(monkeypatch)
    _ins('11111111000111', 'VENCIDA', (HOJE - timedelta(days=5)).isoformat())
    _ins('22222222000122', 'VENCE LOGO', (HOJE + timedelta(days=4)).isoformat())
    _ins('33333333000133', 'LONGE', (HOJE + timedelta(days=200)).isoformat())
    _ins('44444444000144', 'SEM AMBIENTE', (HOJE + timedelta(days=4)).isoformat(), puxa_nfe=0)
    r = alertas.verificar_certificados(HOJE)
    assert sorted(n for n, _m in r) == ['VENCE LOGO', 'VENCIDA']
    assert len(env) == 1 and env[0][0] == '11984630568'          # uma mensagem so
    assert 'Vencidos' in env[0][1] and 'Vencem em até 7 dias' in env[0][1]
    assert 'SEM AMBIENTE' not in env[0][1]
    assert alertas.verificar_certificados(HOJE) == []            # mesmo marco: nao repete
    assert len(env) == 1
    # renovou (validade nova) e cruzou marco novo? volta a alertar so o que mudou
    with models.con() as c:
        c.execute("UPDATE empresas SET validade=? WHERE cnpj='22222222000122'",
                  ((HOJE + timedelta(days=14)).isoformat(),))
    r = alertas.verificar_certificados(HOJE)
    assert r == [('VENCE LOGO', '15')] and len(env) == 2


def test_falha_no_envio_libera_para_tentar_de_novo(monkeypatch):
    _db()
    _captura(monkeypatch, falha=True)
    _ins('11111111000111', 'VENCIDA', (HOJE - timedelta(days=1)).isoformat())
    alertas.verificar_certificados(HOJE)
    env = _captura(monkeypatch)
    alertas.verificar_certificados(HOJE)
    assert len(env) == 1


def test_sem_uazapi_nao_reserva(monkeypatch):
    _db()
    monkeypatch.setattr(uazapi, 'configurado', lambda: False)
    _ins('11111111000111', 'VENCIDA', (HOJE - timedelta(days=1)).isoformat())
    alertas.verificar_certificados(HOJE)
    with models.con() as c:
        assert c.execute('SELECT COUNT(*) FROM alertas_enviados').fetchone()[0] == 0


def test_falha_geral_alerta_uma_vez_por_dia(monkeypatch):
    _db()
    env = _captura(monkeypatch)
    with models.con() as c:
        c.execute("INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada,detalhe) VALUES"
                  "('nfce','111','SANDRA','x',0,'cStat_104','desde 2026-06-06 | Rejeicao: anterior a 100 dias')")
    res = [('111', 'SANDRA', 'cStat_104'), ('222', 'QUEIJEIRO', 'cStat_104'), ('333', 'OJOTA', 'fim')]
    assert alertas.avaliar_falha_geral('nfce', res, dia='2026-09-15') is False   # 2/3 < 80%
    res[2] = ('333', 'OJOTA', 'cStat_104')
    assert alertas.avaliar_falha_geral('nfce', res, dia='2026-09-15') is True
    assert 'NFC-e falhou' in env[0][1] and '3 de 3' in env[0][1] and 'cStat 104' in env[0][1]
    assert 'anterior a 100 dias' in env[0][1]
    assert alertas.avaliar_falha_geral('nfce', res, dia='2026-09-15') is False   # mesmo dia
    assert len(env) == 1


def test_bloqueios_e_esperas_nao_contam_como_falha():
    for p in ('fim', '137', '656', 'cooldown', 'cap', 'limite', 'circuito_aberto'):
        assert not alertas.eh_falha(p)
    for p in ('cStat_104', 'http_403', 'erro_rede:x', 'erro', '108'):
        assert alertas.eh_falha(p)
    assert alertas.avaliar_falha_geral('nfe', [('1', 'A', '656'), ('2', 'B', '656')]) is False


def test_nfce_so_sp_no_boot():
    _db()
    _ins('11111111000111', 'LOJA BA', '2099-01-01', uf='BA', puxa_nfce=1)
    _ins('22222222000122', 'LOJA SP', '2099-01-01', uf='SP', puxa_nfce=1)
    _ins('33333333000133', 'SEM UF', '2099-01-01', uf='', puxa_nfce=1)
    models.init_db()
    with models.con() as c:
        v = dict(c.execute('SELECT nome, puxa_nfce FROM empresas').fetchall())
    assert v == {'LOJA BA': 0, 'LOJA SP': 1, 'SEM UF': 1}
    assert models.nfce_permitida('sp') and models.nfce_permitida(None) and not models.nfce_permitida('BA')
