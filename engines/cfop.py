# -*- coding: utf-8 -*-
"""Classificacao de CFOP (Codigo Fiscal de Operacoes e Prestacoes).

Objetivo: separar o que E base de tributacao (venda/faturamento, compra) do que
NAO E (remessa, retorno, brinde/bonificacao, devolucao, transferencia). Assim a
Conferencia mostra o faturamento LIMPO, sem influencia de notas de passagem.

Estrutura do CFOP (4 digitos):
  1o digito = ambito/direcao:
    1 = entrada estadual   2 = entrada interestadual   3 = entrada exterior
    5 = saida estadual     6 = saida interestadual     7 = saida exterior
  Os 3 ultimos digitos = NATUREZA (paralela entre entrada e saida).

Filosofia: cobrimos as naturezas conhecidas; CFOP desconhecido cai em 'outros'
(FORA da base) — conservador, nao infla tributo. A tela sempre mostra o detalhe
por CFOP para o fiscal validar/reclassificar.
"""
import re

# natureza (3 ultimos digitos) -> (grupo, base_de_tributacao)
_NAT = {}


def _reg(grupo, base, *naturezas):
    for n in naturezas:
        _NAT['%03d' % n] = (grupo, base)


# --- BASE: venda / compra de mercadoria ou producao ---
_reg('venda_compra', True, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110,
     111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 122, 123)
# --- BASE: industrializacao efetuada para outra empresa (servico industrial) ---
_reg('industrializacao', True, 124, 125)
# --- BASE: venda/compra com Substituicao Tributaria ---
_reg('venda_compra_st', True, 401, 402, 403, 404, 405)
# --- BASE: venda de combustivel/lubrificante ---
_reg('venda_compra', True, 651, 652, 653, 654, 655, 656)
# --- FORA DA BASE: devolucoes ---
_reg('devolucao', False, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210,
     410, 411, 412, 413, 414, 415, 660, 661, 662)
# --- FORA DA BASE: transferencias (entre estabelecimentos da mesma empresa) ---
_reg('transferencia', False, 151, 152, 153, 155, 156, 408, 409, 657, 659)
# --- FORA DA BASE: bonificacao / brinde / doacao / amostra gratis ---
_reg('bonificacao_brinde', False, 910, 911)
# --- FORA DA BASE: retorno (de industrializacao, deposito, conserto...) ---
_reg('retorno', False, 902, 903, 906, 907, 909, 913, 914, 916, 918, 919, 921, 925, 926)
# --- FORA DA BASE: remessas diversas (comodato, conserto, deposito, industrializacao...) ---
_reg('remessa', False, 901, 904, 905, 908, 912, 915, 917, 920, 922, 923, 924,
     658, 931, 932, 934)

# rotulo amigavel + se compoe a base
GRUPOS = {
    'venda_compra':       ('Venda / Compra',          True),
    'venda_compra_st':    ('Venda / Compra (ST)',     True),
    'industrializacao':   ('Industrialização',        True),
    'devolucao':          ('Devolução',               False),
    'transferencia':      ('Transferência',           False),
    'bonificacao_brinde': ('Bonificação / Brinde',    False),
    'retorno':            ('Retorno',                  False),
    'remessa':            ('Remessa',                  False),
    'outros':             ('Outros / não classificado', False),
}


def classificar(cfop):
    """cfop -> dict {cfop, direcao ('entrada'|'saida'|'?'), grupo, rotulo, base(bool)}."""
    c = re.sub(r'\D', '', str(cfop or ''))
    if len(c) != 4:
        return {'cfop': c, 'direcao': '?', 'grupo': 'outros',
                'rotulo': GRUPOS['outros'][0], 'base': False}
    direcao = 'entrada' if c[0] in '123' else ('saida' if c[0] in '567' else '?')
    grupo, base = _NAT.get(c[1:], ('outros', False))
    return {'cfop': c, 'direcao': direcao, 'grupo': grupo,
            'rotulo': GRUPOS[grupo][0], 'base': base}


def eh_base(cfop):
    """True se o CFOP compoe a base de tributacao (venda/compra efetiva)."""
    return classificar(cfop)['base']


if __name__ == '__main__':
    for c in ('5124', '6902', '5102', '5910', '1202', '5405', '5152', '9999', '5655'):
        r = classificar(c)
        print(c, '->', r['direcao'], r['grupo'], '| base=' + str(r['base']))
