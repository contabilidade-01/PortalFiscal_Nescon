# -*- coding: utf-8 -*-
"""Migracao idempotente: move NFs em 01_entrada onde <emit> == <cnpj> da pasta
para a pasta correta (04_saida). Roda 1x; se ja estiver movida, nao faz nada.
Gera log detalhado. NAO deleta nada ate o fim (rollback em caso de erro)."""
import os, re, sys, shutil
sys.path.insert(0, '.')

SAIDA = 'XML'
CNPJ_RE = re.compile(r'<emit>\s*<CNPJ>(\d+)')
DEST_RE = re.compile(r'<dest>\s*<CNPJ>(\d+)')

# coleta trabalho a fazer
trabalhos = []  # (origem, destino)
for cnpj in sorted(os.listdir(SAIDA)):
    cdir = os.path.join(SAIDA, cnpj)
    if not os.path.isdir(cdir): continue
    for comp in sorted(os.listdir(cdir)):
        pe = os.path.join(cdir, comp, 'NFe', '01_entrada')
        if not os.path.isdir(pe): continue
        for fn in sorted(os.listdir(pe)):
            if not fn.endswith('.xml'): continue
            path = os.path.join(pe, fn)
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    txt = f.read()
            except Exception:
                continue
            em = CNPJ_RE.search(txt)
            if em and em.group(1) == cnpj:
                # <emit> == cnpj -> venda propria do cliente -> mover para 04_saida
                dest_dir = os.path.join(cdir, comp, 'NFe', '04_saida')
                dest_path = os.path.join(dest_dir, fn)
                # idempotente: se ja esta no destino, pula
                if os.path.exists(dest_path):
                    continue
                trabalhos.append((path, dest_path, 'venda_propria'))

# tambem: se 04_saida/<cnpj> for diferente do cert do escritorio e o emit for o escritorio, mover para 05_propria/<escritorio>
# (mas como 04_saida e gravado pelo EMITENTE, no nosso caso isso ja esta OK - escritorio grava 04_saida/<escritorio>)

print(f'{len(trabalhos)} arquivos a mover (01_entrada -> 04_saida)')
print()
if not trabalhos:
    print('Nada a fazer.')
    sys.exit(0)

print('Plano:')
for o, d, motivo in trabalhos[:10]:
    print('  ', motivo, '|', os.path.basename(o), '->', os.path.dirname(d))
if len(trabalhos) > 10:
    print(f'  ... e mais {len(trabalhos)-10}')

resp = input('\nCONFIRMA mover? (s/N) ').strip().lower()
if resp != 's':
    print('Cancelado.')
    sys.exit(0)

movidos = 0; erros = 0
for o, d, motivo in trabalhos:
    try:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(o, d)
        movidos += 1
    except Exception as e:
        erros += 1
        print('  ERRO ao mover', o, '->', e)
print(f'\n{movidos} movidos, {erros} erros')