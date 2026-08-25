# -*- coding: utf-8 -*-
"""Fase 5 - Vincula em massa os certificados A1 da Nescon as empresas da base.
   Tenta VARIAS senhas candidatas por arquivo e diagnostica arquivos ilegiveis
   (OneDrive cloud-only) vs senha nao encontrada.
"""
import os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz do projeto
import models
from engines import certs

PASTAS = [
    r"C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\00_Nescon Contabilidade\0007_CERTIFICADO DIGITAL",
    r"C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\00_Nescon Contabilidade\0003_GERAIS\BACKUP - FORTES",
]

def cand_senhas(fn):
    nome = os.path.splitext(os.path.basename(fn))[0]
    cands = []
    for m in re.finditer(r'senha[:\s#=-]*([A-Za-z0-9@#$!._]{3,})', nome, re.I):
        cands.append(m.group(1))
    for tok in re.split(r'[\s_]+', nome):
        tok = tok.strip('-.()')
        if 4 <= len(tok) <= 20:
            cands.append(tok)
    cands += ['1234', '123456', '12345678']
    seen = set(); out = []
    for c in cands:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out

def run():
    models.init_db()
    with models.con() as c:
        base = {r['cnpj']: r['id'] for r in c.execute('SELECT id,cnpj FROM empresas').fetchall()}

    achados = ilegivel = senha_nao = vinculados = 0
    vistos = set(); t0 = time.time()
    for raiz in PASTAS:
        if not os.path.isdir(raiz):
            continue
        for r, _, arqs in os.walk(raiz):
            for a in arqs:
                if not a.lower().endswith(('.pfx', '.p12')):
                    continue
                achados += 1
                full = os.path.join(r, a)
                # arquivo legivel? (cloud-only do OneDrive falha aqui)
                try:
                    raw = open(full, 'rb').read()
                    if len(raw) < 300:
                        ilegivel += 1; continue
                except Exception:
                    ilegivel += 1; continue
                # tenta candidatos
                ok = False
                for sen in cand_senhas(a):
                    try:
                        _, cert, _ = certs.load_pfx(full, sen)
                        cnpj, tipo, nome, uf, val = certs.cert_info(cert)
                        ok = True
                        break
                    except Exception:
                        continue
                if not ok:
                    senha_nao += 1; continue
                if tipo == 'CNPJ' and cnpj in base and cnpj not in vistos:
                    with models.con() as c:
                        c.execute('UPDATE empresas SET arquivo=?,senha=?,senha_ok=1,validade=?,uf=?,cuf=? WHERE id=?',
                                  (full, sen, val, uf, certs.UF_COD.get(uf, '35'), base[cnpj]))
                    vinculados += 1; vistos.add(cnpj)

    with models.con() as c:
        tot = c.execute('SELECT COUNT(*) FROM empresas').fetchone()[0]
        com = c.execute('SELECT COUNT(*) FROM empresas WHERE senha_ok=1').fetchone()[0]
    print('=== Fase 5 (%.0fs) ===' % (time.time()-t0))
    print('.pfx encontrados: %d' % achados)
    print('  ilegiveis (cloud-only/placeholder): %d' % ilegivel)
    print('  abriram mas senha nao encontrada  : %d' % senha_nao)
    print('  VINCULADOS a base                 : %d' % vinculados)
    print('COBERTURA: %d de %d empresas com certificado (%.0f%%)' % (com, tot, 100.0*com/tot if tot else 0))

if __name__ == '__main__':
    run()
