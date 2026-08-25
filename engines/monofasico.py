# -*- coding: utf-8 -*-
"""Produtos MONOFASICOS de PIS/COFINS + parametros do Simples Nacional.

Beneficio (Simples Nacional): a empresa que REVENDE produtos monofasicos pode
SEGREGAR essa receita e NAO pagar PIS/COFINS sobre ela (a contribuicao ja foi
recolhida na industria/importacao). Base: LC 123/2006 art. 18 §4o-A + Resolucao
CGSN 140/2018.

Identificacao = por NCM (item <prod><NCM> da NF-e). Fonte oficial: Tabela SPED
4.3.10 (Incidencia Monofasica). No Simples o CST de PIS/COFINS NAO e confiavel
(usam 49/99), entao o NCM e o sinal autoritativo.

Economia mensal ~= faturamento_monofasico * aliquota_efetiva * share_pis_cofins.
"""
import re

# ----------------------------------------------------------------------------
# Tabela CURADA de NCM monofasico: prefixo -> (categoria, base_legal)
# Casamento por MAIOR prefixo (NCM da NF tem 8 digitos; leis usam 4/6/8).
# Cobre os grupos principais; a Tabela SPED 4.3.10 completa entra via importador
# (tabela ncm_monofasico no banco), complementando esta.
# ----------------------------------------------------------------------------
_TAB = {}

def _reg(categoria, base_legal, *prefixos):
    for p in prefixos:
        _TAB[re.sub(r'\D', '', p)] = (categoria, base_legal)

# Combustiveis (Lei 9.718/1998 art. 4-6; Lei 10.560/2002)
_reg('Combustíveis', 'Lei 9.718/1998',
     '2710', '2711', '2207', '220710', '220720', '3826', '271012', '271019', '271020')
# Medicamentos (Lei 10.147/2000 - lista positiva)
_reg('Medicamentos', 'Lei 10.147/2000', '3003', '3004', '300660')
# Perfumaria / higiene pessoal / cosmeticos (Lei 10.147/2000)
_reg('Perfumaria/Higiene', 'Lei 10.147/2000',
     '3303', '3304', '3305', '3306', '3307',
     '34011190', '34012010', '34013000', '96032100')
# Pneus e camaras de ar (Lei 10.485/2002 art. 5)
_reg('Pneus/Câmaras', 'Lei 10.485/2002', '4011', '4013')
# Autopecas (Lei 10.485/2002 Anexos I e II) - familias principais
_reg('Autopeças', 'Lei 10.485/2002',
     '40169300', '68132000', '68138100', '68138900',
     '7007', '7009', '7320', '830120', '830210', '830230', '830260',
     '8407', '8408', '8409', '8413', '8414', '8415', '8421', '8481', '8482',
     '8483', '8484', '8505', '8507', '8511', '8512', '8527', '8536', '8539',
     '854430', '870600', '8707', '8708', '8714', '9026', '9029', '9031', '9032',
     '9104', '940120')
# Bebidas frias (Lei 13.097/2015 - aguas, refrigerantes, cervejas, energeticos)
_reg('Bebidas frias', 'Lei 13.097/2015',
     '2201', '2202', '2203', '21069010', '22029900')

# categorias -> so p/ rotulo/ordenacao
CATEGORIAS = ['Combustíveis', 'Medicamentos', 'Perfumaria/Higiene', 'Pneus/Câmaras',
              'Autopeças', 'Bebidas frias', 'Outros (SPED)']

# cache dos NCMs importados do SPED (banco) — carregado sob demanda
_DB_NCM = None

def _carregar_db():
    """Le a tabela ncm_monofasico (origem sped/custom) do banco, uma vez."""
    global _DB_NCM
    if _DB_NCM is not None:
        return _DB_NCM
    _DB_NCM = {}
    try:
        import models
        with models.con() as c:
            for r in c.execute("SELECT ncm, categoria, base_legal FROM ncm_monofasico "
                               "WHERE origem<>'curado'").fetchall():
                _DB_NCM[re.sub(r'\D', '', r['ncm'])] = (r['categoria'] or 'Outros (SPED)',
                                                        r['base_legal'] or 'SPED 4.3.10')
    except Exception:
        _DB_NCM = {}
    return _DB_NCM

def recarregar():
    """Invalida o cache de NCMs do banco (chamar apos importar)."""
    global _DB_NCM
    _DB_NCM = None

def classificar(ncm):
    """ncm (str/int) -> {ncm, monofasico(bool), categoria, base_legal}."""
    n = re.sub(r'\D', '', str(ncm or ''))
    if len(n) < 4:
        return {'ncm': n, 'monofasico': False, 'categoria': '', 'base_legal': ''}
    # 1) NCM exato importado do SPED
    db = _carregar_db()
    if n in db:
        cat, bl = db[n]
        return {'ncm': n, 'monofasico': True, 'categoria': cat, 'base_legal': bl}
    # 2) tabela curada por MAIOR prefixo
    melhor = None
    for i in range(len(n), 3, -1):
        p = n[:i]
        if p in _TAB:
            melhor = p; break
    if melhor:
        cat, bl = _TAB[melhor]
        return {'ncm': n, 'monofasico': True, 'categoria': cat, 'base_legal': bl}
    return {'ncm': n, 'monofasico': False, 'categoria': '', 'base_legal': ''}

def eh_monofasico(ncm):
    return classificar(ncm)['monofasico']

# ----------------------------------------------------------------------------
# Simples Nacional — tabelas (LC 123/2006, LC 155/2016) p/ aliquota efetiva
# faixa = (limite_RBT12, aliquota_nominal, parcela_deduzir)
# ----------------------------------------------------------------------------
ANEXOS = {
    'I': [  # Comercio
        (180000.00, 0.0400, 0.00), (360000.00, 0.0730, 5940.00),
        (720000.00, 0.0950, 13860.00), (1800000.00, 0.1070, 22500.00),
        (3600000.00, 0.1430, 87300.00), (4800000.00, 0.1900, 378000.00)],
    'II': [  # Industria
        (180000.00, 0.0450, 0.00), (360000.00, 0.0780, 5940.00),
        (720000.00, 0.1000, 13860.00), (1800000.00, 0.1120, 22500.00),
        (3600000.00, 0.1470, 85500.00), (4800000.00, 0.3000, 720000.00)],
}
# Repartição PIS+COFINS por faixa (fracao da aliquota efetiva)
_SHARE = {
    'I':  [0.1550, 0.1550, 0.1550, 0.1550, 0.1550, 0.3440],  # PIS 2,76 + COFINS 12,74
    'II': [0.1400, 0.1400, 0.1400, 0.1400, 0.1400, 0.2825],  # PIS 2,49 + COFINS 11,51 (aprox)
}

def aliquota_efetiva(anexo, rbt12):
    """Retorna (aliquota_efetiva, faixa 1..6). rbt12<=0 -> (0, 0)."""
    faixas = ANEXOS.get((anexo or 'I').upper(), ANEXOS['I'])
    try:
        rbt12 = float(rbt12 or 0)
    except Exception:
        rbt12 = 0.0
    if rbt12 <= 0:
        return 0.0, 0
    for i, (limite, aliq, ded) in enumerate(faixas):
        if rbt12 <= limite:
            return max((rbt12 * aliq - ded) / rbt12, 0.0), i + 1
    limite, aliq, ded = faixas[-1]
    return max((rbt12 * aliq - ded) / rbt12, 0.0), len(faixas)

def share_pis_cofins(anexo, faixa):
    sh = _SHARE.get((anexo or 'I').upper(), _SHARE['I'])
    if not faixa:
        return sh[0]
    return sh[min(int(faixa), len(sh)) - 1]

def economia_pis_cofins(fat_monofasico, anexo, rbt12):
    """Economia mensal estimada de PIS/COFINS sobre a receita monofasica."""
    ef, faixa = aliquota_efetiva(anexo, rbt12)
    if not faixa:
        return 0.0, ef, faixa
    return round(float(fat_monofasico or 0) * ef * share_pis_cofins(anexo, faixa), 2), ef, faixa


if __name__ == '__main__':
    for n in ('30049099', '40111000', '54075210', '22021000', '33049910',
              '87089990', '27101259', '96032100', '99999999'):
        r = classificar(n)
        print(n, '->', 'MONO' if r['monofasico'] else 'nao ', '|', r['categoria'], '|', r['base_legal'])
    print('--- Simples ---')
    for anexo, rbt in (('I', 180000), ('I', 500000), ('II', 300000)):
        ef, fx = aliquota_efetiva(anexo, rbt)
        print('Anexo %s RBT12 %d -> efetiva %.4f faixa %d share %.3f'
              % (anexo, rbt, ef, fx, share_pis_cofins(anexo, fx)))
    print('Economia de R$1000 monofasico (Anexo I, RBT 180k):',
          economia_pis_cofins(1000, 'I', 180000))
