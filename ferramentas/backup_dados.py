# -*- coding: utf-8 -*-
"""Gera o ZIP de migracao (cadastros + certificados + XML) na Area de trabalho.

   Uso:
     python ferramentas/backup_dados.py
     python ferramentas/backup_dados.py --leve
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import json
import models
from engines import backup as bak

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
CERT_DIR = os.path.join(models.DATA_DIR, 'Certificados')
SAIDA = os.environ.get('FISCAL_XML_DIR') or CFG.get('pasta_saida_xml') or os.path.join(models.DATA_DIR, 'XML')


def _area_trabalho():
    home = os.path.expanduser('~')
    for nome in ('Desktop', 'Área de Trabalho', 'OneDrive/Desktop'):
        p = os.path.join(home, nome)
        if os.path.isdir(p):
            return p
    return home


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--leve', action='store_true', help='so cadastros + certificados (sem XML)')
    ap.add_argument('--saida', default='')
    args = ap.parse_args()
    nome = 'portal-fiscal-%s-%s.zip' % (
        'cadastros-certificados' if args.leve else 'completo',
        datetime.now().strftime('%Y%m%d-%H%M'))
    dest = args.saida or os.path.join(_area_trabalho(), nome)
    print('Gerando', dest)
    print('Banco:', models.DB)
    print('Certificados:', CERT_DIR)
    print('XML:', SAIDA)
    bak.criar_zip(dest, models.DB, CERT_DIR, SAIDA,
                  incluir_db=True, incluir_certs=True, incluir_xml=not args.leve)
    print('Pronto:', dest, '(%s)' % bak.fmt_bytes(os.path.getsize(dest)))
