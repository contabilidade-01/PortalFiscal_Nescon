# -*- coding: utf-8 -*-
"""Gera o ZIP de migracao (cadastros + certificados, SEM XML) na Area de trabalho.

   Uso:
     python ferramentas/backup_dados.py
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import models
from engines import backup as bak

CERT_DIR = os.path.join(models.DATA_DIR, 'Certificados')


def _area_trabalho():
    home = os.path.expanduser('~')
    for nome in ('Desktop', 'Área de Trabalho', 'OneDrive/Desktop'):
        p = os.path.join(home, nome)
        if os.path.isdir(p):
            return p
    return home


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--saida', default='')
    args = ap.parse_args()
    nome = 'portal-fiscal-cadastros-certificados-%s.zip' % datetime.now().strftime('%Y%m%d-%H%M')
    dest = args.saida or os.path.join(_area_trabalho(), nome)
    print('Gerando', dest)
    print('Banco:', models.DB)
    print('Certificados:', CERT_DIR)
    bak.criar_zip(dest, models.DB, CERT_DIR)
    print('Pronto:', dest, '(%s)' % bak.fmt_bytes(os.path.getsize(dest)))
