# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
from engines.pausa import nsu15, janela_nfce, delay_retomada, em_cooldown, RETOMAR, RETOMAR_BUFFER_SEG

def test_nsu15():
    assert nsu15(None) == '000000000000000'
    assert nsu15('') == '000000000000000'
    assert nsu15('12') == '000000000000012'
    assert nsu15('000000000000138') == '000000000000138'
    assert nsu15(138) == '000000000000138'

def test_janela_nfce_corta_100_dias():
    fim = datetime(2026, 8, 25, 12, 0, 0)
    ini = datetime(2025, 1, 1)
    a, b = janela_nfce(ini, fim, max_dias=100)
    assert b == fim
    assert a == fim - timedelta(days=100)

def test_janela_nfce_dentro_do_teto():
    fim = datetime(2026, 8, 25, 12, 0, 0)
    ini = datetime(2026, 8, 1)
    a, b = janela_nfce(ini, fim, max_dias=100)
    assert a == ini
    assert b == fim

def test_delay_retomada():
    assert delay_retomada('cap') == 15
    assert delay_retomada('limite', motor='nfce') == 90
    assert delay_retomada('limite', motor='nfe') == 60
    assert delay_retomada('fim') is None
    assert delay_retomada('137') is None
    assert delay_retomada('656') == 65 * 60 + RETOMAR_BUFFER_SEG
    assert delay_retomada('109') == 65 * 60 + RETOMAR_BUFFER_SEG

def test_em_cooldown():
    assert em_cooldown(None) is False
    assert em_cooldown('2099-01-01 00:00:00') is True
    assert em_cooldown('2000-01-01 00:00:00') is False

def test_retomar_conjunto():
    assert 'cap' in RETOMAR
    assert 'limite' in RETOMAR
    assert '656' in RETOMAR
    assert '109' in RETOMAR
    assert '137' not in RETOMAR
    assert 'fim' not in RETOMAR
    assert 'circuito_aberto' not in RETOMAR

if __name__ == '__main__':
    test_nsu15()
    test_janela_nfce_corta_100_dias()
    test_janela_nfce_dentro_do_teto()
    test_delay_retomada()
    test_em_cooldown()
    test_retomar_conjunto()
    print('ok')
