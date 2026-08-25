# -*- coding: utf-8 -*-
"""Importa a Tabela SPED 4.3.10 (Produtos com Incidencia Monofasica de PIS/COFINS)
para a tabela ncm_monofasico do banco, complementando a tabela curada de
engines/monofasico.py.

Uso:
    python ferramentas/importar_monofasico.py <arquivo> [categoria]

Aceita formatos flexiveis: pipe (|), CSV (; ou ,) ou um NCM por linha. Extrai o
NCM (8 digitos, com ou sem pontos) de cada linha e grava com origem='sped'.
Idempotente (INSERT OR REPLACE). Depois, o classificador reconhece esses NCMs.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import models
from engines import monofasico

_NCM8 = re.compile(r'(\d{4}\.?\d{2}\.?\d{2})')

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    arq = sys.argv[1]
    categoria_fixa = sys.argv[2] if len(sys.argv) > 2 else 'Outros (SPED)'
    if not os.path.exists(arq):
        print('!! Arquivo nao encontrado:', arq); return
    models.init_db()
    vistos = set(); linhas = 0
    with open(arq, encoding='utf-8', errors='ignore') as f:
        rows = []
        for ln in f:
            linhas += 1
            m = _NCM8.search(ln)
            if not m:
                continue
            ncm = re.sub(r'\D', '', m.group(1))
            if len(ncm) != 8 or ncm in vistos:
                continue
            vistos.add(ncm)
            # tenta pegar uma descricao/categoria da propria linha (apos o NCM)
            partes = [p.strip() for p in re.split(r'[|;,\t]', ln[m.end():]) if p.strip()]
            cat = (partes[0][:40] if partes else categoria_fixa)
            rows.append((ncm, cat, 'SPED 4.3.10', 'sped'))
    if not rows:
        print('!! Nenhum NCM (8 digitos) encontrado em %d linhas. Confira o arquivo.' % linhas); return
    with models.con() as c:
        c.executemany('INSERT OR REPLACE INTO ncm_monofasico(ncm,categoria,base_legal,origem) '
                      'VALUES(?,?,?,?)', rows)
    monofasico.recarregar()
    print('OK: %d NCMs importados (origem=sped) de %d linhas.' % (len(rows), linhas))
    print('Exemplos:', ', '.join(n for n, *_ in rows[:8]))

if __name__ == '__main__':
    main()
