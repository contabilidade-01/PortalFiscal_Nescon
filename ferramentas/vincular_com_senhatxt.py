# -*- coding: utf-8 -*-
"""Vincula certificados usando as senhas dos arquivos .txt (Senha.txt) que ficam
   na MESMA pasta do certificado, alem das senhas deduzidas do nome do arquivo."""
import os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz do projeto
import models
from engines import certs

PASTAS = [
    r"C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\00_Nescon Contabilidade\0007_CERTIFICADO DIGITAL",
    r"C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\00_Nescon Contabilidade\0003_GERAIS\BACKUP - FORTES",
]

def cand_nome(fn):
    nome = os.path.splitext(os.path.basename(fn))[0]
    cs = []
    for m in re.finditer(r'senha[:\s#=-]*([A-Za-z0-9@#$!._*]{3,})', nome, re.I):
        cs.append(m.group(1))
    for tok in re.split(r'[\s_]+', nome):
        tok = tok.strip('-.()')
        if 4 <= len(tok) <= 22:
            cs.append(tok)
    return cs

def cand_txt(folder):
    cs = []
    try:
        arqs = os.listdir(folder)
    except Exception:
        return cs
    for f in arqs:
        if not f.lower().endswith('.txt'):
            continue
        try:
            txt = open(os.path.join(folder, f), encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        for linha in txt.splitlines():
            linha = linha.strip()
            if not linha:
                continue
            m = re.search(r'senha[:\s]*([^\s]+)', linha, re.I)
            if m:
                cs.append(m.group(1))
            if 4 <= len(linha) <= 25 and ' ' not in linha:
                cs.append(linha)
    return cs

def run():
    models.init_db()
    with models.con() as c:
        base = {r['cnpj']: r['id'] for r in c.execute('SELECT id,cnpj FROM empresas').fetchall()}
        ja = {r['cnpj'] for r in c.execute('SELECT cnpj FROM empresas WHERE senha_ok=1').fetchall()}

    novos = 0; achou_txt = 0; ileg = 0; falhou = 0; vistos = set()
    t0 = time.time()
    for raiz in PASTAS:
        if not os.path.isdir(raiz):
            continue
        for r, _, arqs in os.walk(raiz):
            txts = cand_txt(r)  # senhas dos .txt da pasta (uma vez por pasta)
            for a in arqs:
                if not a.lower().endswith(('.pfx', '.p12')):
                    continue
                full = os.path.join(r, a)
                try:
                    raw = open(full, 'rb').read()
                    if len(raw) < 300:
                        ileg += 1; continue
                except Exception:
                    ileg += 1; continue
                cands = []
                for s in (cand_nome(a) + txts):
                    if s and s not in cands:
                        cands.append(s)
                aberto = None
                for s in cands:
                    try:
                        _, cert, _ = certs.load_pfx(full, s)
                        aberto = (cert, s); break
                    except Exception:
                        continue
                if not aberto:
                    falhou += 1; continue
                cert, senha = aberto
                cnpj, tipo, nome, uf, val = certs.cert_info(cert)
                if tipo == 'CNPJ' and cnpj in base and cnpj not in vistos:
                    if cnpj not in ja:
                        novos += 1
                        if senha in txts:
                            achou_txt += 1
                    with models.con() as c:
                        c.execute('UPDATE empresas SET arquivo=?,senha=?,senha_ok=1,validade=?,uf=?,cuf=? WHERE id=?',
                                  (full, senha, val, uf, certs.UF_COD.get(uf, '35'), base[cnpj]))
                    vistos.add(cnpj)

    with models.con() as c:
        tot = c.execute('SELECT COUNT(*) FROM empresas').fetchone()[0]
        com = c.execute('SELECT COUNT(*) FROM empresas WHERE senha_ok=1').fetchone()[0]
    print('=== Vinculo com Senha.txt (%.0fs) ===' % (time.time() - t0))
    print('novos vinculados nesta rodada: %d (destes, %d pela senha do .txt)' % (novos, achou_txt))
    print('ainda ilegiveis (cloud-only): %d | ainda sem senha: %d' % (ileg, falhou))
    print('COBERTURA TOTAL: %d de %d empresas (%.0f%%)' % (com, tot, 100.0 * com / tot if tot else 0))

if __name__ == '__main__':
    run()
