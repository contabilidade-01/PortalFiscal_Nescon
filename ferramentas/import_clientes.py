# -*- coding: utf-8 -*-
"""Fase 1 - Importa os clientes do GClick para a base UNICA do Portal Fiscal.
   Fonte (somente leitura): projeto GCLICK -> data/dados.db, tabela clientes.
   Cada cliente entra com puxa_nfe=1, puxa_nfse=1 e metodo_saida='a_verificar'.
"""
import os, re, sys, sqlite3
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz do projeto
import models

GCLICK_DB = r"C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\01_Jean\00_Claude\00_PROJETOS\GCLICK\data\dados.db"

def limpa_nome(apelido, nome_completo):
    ap = (apelido or '').strip()
    n = re.sub(r'^[\d.\-/\s]+', '', ap).strip()
    return n or (nome_completo or ap or '?')

def run():
    models.init_db()
    src = sqlite3.connect(GCLICK_DB); src.row_factory = sqlite3.Row
    clientes = src.execute(
        "SELECT nome_completo, apelido, cnpj, whatsapp, ativo, responsavel_nome, email "
        "FROM clientes ORDER BY apelido").fetchall()
    src.close()

    novos = 0; atualizados = 0
    with models.con() as c:
        for cl in clientes:
            cnpj = re.sub(r'\D', '', cl['cnpj'] or '')
            if len(cnpj) != 14:
                continue
            nome = limpa_nome(cl['apelido'], cl['nome_completo'])
            ex = c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
            if ex:
                c.execute('UPDATE empresas SET nome=?, whatsapp=?, responsavel=?, email=?, ativo=? WHERE id=?',
                          (nome, cl['whatsapp'], cl['responsavel_nome'], cl['email'],
                           1 if cl['ativo'] else 0, ex['id']))
                atualizados += 1
            else:
                c.execute('''INSERT INTO empresas
                    (cnpj,nome,ativo,puxa_nfe,puxa_nfse,metodo_saida,whatsapp,responsavel,email,origem,criado)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (cnpj, nome, 1 if cl['ativo'] else 0, 1, 1, 'a_verificar',
                     cl['whatsapp'], cl['responsavel_nome'], cl['email'], 'gclick',
                     datetime.now().isoformat(timespec='seconds')))
                novos += 1

    with models.con() as c:
        tot = c.execute('SELECT COUNT(*) FROM empresas').fetchone()[0]
        ativos = c.execute('SELECT COUNT(*) FROM empresas WHERE ativo=1').fetchone()[0]
        com_cert = c.execute('SELECT COUNT(*) FROM empresas WHERE senha_ok=1').fetchone()[0]
    print('Importacao concluida: %d novos, %d atualizados.' % (novos, atualizados))
    print('Base unica: %d empresas (%d ativas) | com certificado vinculado: %d' % (tot, ativos, com_cert))

if __name__ == '__main__':
    run()
