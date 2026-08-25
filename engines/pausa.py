# -*- coding: utf-8 -*-
"""Pausas e retomada anti-656 (NT 2014.002 distNSU + SAE NFC-e SP).

No servidor o worker NAO dorme 1 hora bloqueando a fila: grava cooldown,
pula a empresa e enfileira a continuacao para depois do horario.
"""
import json, os, re, threading, time
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(_BASE, 'config.json'), encoding='utf-8'))

NFE = CFG.get('nfe') or {}
NFCE = CFG.get('nfce') or {}
CIENCIA = CFG.get('ciencia') or {}

# distNSU: 2s entre lotes; 65 min apos 137/fim/656 (NT: 1h; 5 min de folga).
NFE_ESPERA = float(NFE.get('espera_seg', 2))
NFE_ENTRE_EMPRESAS = float(NFE.get('espera_entre_empresas_seg', 2))
NFE_COOLDOWN_MIN = int(NFE.get('cooldown_min', 65))
NFE_RETOMAR_CAP_SEG = int(NFE.get('retomar_cap_seg', 15))

# NFC-e: limite e por IP no servidor. 1.5s ~ 40 req/min.
NFCE_ESPERA = float(NFCE.get('espera_seg', 1.5))
NFCE_LISTAGEM = float(NFCE.get('espera_listagem_seg', 1.0))
NFCE_ENTRE_EMPRESAS = float(NFCE.get('espera_entre_empresas_seg', 3))
NFCE_COOLDOWN_MIN = int(NFCE.get('cooldown_min', 65))
NFCE_MAX_DIAS = int(NFCE.get('max_dias', 100))
NFCE_RETOMAR_LIMITE_SEG = int(NFCE.get('retomar_limite_seg', 90))

CIENCIA_ESPERA = float(CIENCIA.get('espera_seg', 2))
CIENCIA_LIMITE = int(CIENCIA.get('limite_por_run', 30))
CIENCIA_COOLDOWN_MIN = int(CIENCIA.get('cooldown_min', 65))

# Paradas que ainda tem trabalho: o servidor retoma sozinho apos a pausa.
RETOMAR = frozenset(('cap', 'limite', '656', '429', 'cooldown', '108'))

_nfce_lock = threading.Lock()
_nfce_ultimo = 0.0


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def nsu15(v):
    """ultNSU da distNSU: 15 digitos, zeros a esquerda."""
    s = re.sub(r'\D', '', str(v or '')) or '0'
    return s.zfill(15)[-15:]


def cooldown_ate(minutos=None):
    m = NFE_COOLDOWN_MIN if minutos is None else int(minutos)
    return (datetime.now() + timedelta(minutes=m)).strftime('%Y-%m-%d %H:%M:%S')


def em_cooldown(ate):
    return bool(ate) and agora() < ate


def segundos_ate(ate):
    """Segundos ate um timestamp 'YYYY-MM-DD HH:MM:SS'. Minimo 5s se ja passou."""
    if not ate:
        return 5
    try:
        alvo = datetime.strptime(ate[:19], '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return 5
    s = int((alvo - datetime.now()).total_seconds())
    return max(5, s)


def janela_nfce(dt_ini, dt_fim=None, max_dias=None):
    """Corta o periodo da listagem SAE no teto de 100 dias da SEFAZ-SP."""
    dias = NFCE_MAX_DIAS if max_dias is None else int(max_dias)
    fim = dt_fim or datetime.now()
    chao = fim - timedelta(days=dias)
    if dt_ini < chao:
        dt_ini = chao
    if dt_ini > fim:
        dt_ini = fim
    return dt_ini, fim


def pausa_nfce(seg=None):
    """Cadencia global de NFC-e (mesmo IP do servidor para todas as empresas)."""
    global _nfce_ultimo
    espera = NFCE_ESPERA if seg is None else float(seg)
    with _nfce_lock:
        agora_m = time.monotonic()
        falta = espera - (agora_m - _nfce_ultimo)
        if falta > 0:
            time.sleep(falta)
        _nfce_ultimo = time.monotonic()


def delay_retomada(parada, ate=None, motor='nfe'):
    """Quanto esperar antes de o worker enfileirar a continuacao."""
    if parada == 'cap':
        return NFE_RETOMAR_CAP_SEG
    if parada == 'limite':
        return NFCE_RETOMAR_LIMITE_SEG
    if parada == '429':
        return segundos_ate(ate) if ate else 1800
    if parada in ('656', 'cooldown', '108'):
        if ate:
            return segundos_ate(ate)
        mins = NFCE_COOLDOWN_MIN if motor == 'nfce' else NFE_COOLDOWN_MIN
        return mins * 60
    return None
