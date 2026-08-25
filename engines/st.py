# -*- coding: utf-8 -*-
"""Identificacao de receita com ICMS-ST (Substituicao Tributaria) na NF-e.
   Base legal: LC 123/2006 art. 13 §1o XIII 'a' (ST fora do DAS), Res. CGSN
   140/2018 art. 25 §8o II (substituto optante), Res. CGSN 94/2011 art. 5o.
   Beneficio: empresa do Simples que REVENDE produto com ICMS-ST pode
   SEGREGAR essa receita (sai do DAS) e recolher o ICMS-ST por fora (GIA-ST
   estadual). Hoje o sistema baixa a NF; aqui detecta qual parte do
   faturamento tem ST."""
import re

# CSTs ICMS que indicam Substituicao Tributaria (NF-e modelo 55/65):
#   10 = ST + ICMS proprio (substituto)
#   30 = ST isento ou nao tributada
#   60 = ICMS-ST retido anteriormente (caso mais comum p/ o substituido)
#   70 = ST com reducao de base
#   90 = ST Outros
# CSTs NORMAIS (sem ST):
#   00, 20 (reducao), 40 (isento), 41 (nao tributado), 50 (suspensao), 51 (dif)
#   80 (ICMS proprio Outros), 81 (dif)
CST_ST = {'10', '30', '60', '70', '90'}

# Tags ICMS da NF-e: <ICMSNN> ... </ICMSNN> (regime normal, CST 2 digitos)
#                  <ICMSSNNN> ... </ICMSSNNN> (Simples, CSOSN 3 digitos)
# Como o backreference precisa do texto exato do group(1), mantemos 2 patterns.
_ICMS_TAG_RE = re.compile(
    r'<(?:[\w]+:)?ICMS(\d{2})(?:\s[^>]*)?>(.*?)</(?:[\w]+:)?ICMS\1>',
    re.S)
_ICMSSN_TAG_RE = re.compile(
    r'<(?:[\w]+:)?ICMSSN(\d{3})(?:\s[^>]*)?>(.*?)</(?:[\w]+:)?ICMSSN\1>',
    re.S)
# campo do CST explicito dentro da tag (regime normal)
_CST_FIELD_RE = re.compile(r'<CST>(\d{2})</CST>')
# campo do CSOSN dentro da tag (Simples Nacional)
_CSOSN_FIELD_RE = re.compile(r'<CSOSN>(\d{3})</CSOSN>')
# vProd por item (tambem fora da tag ICMS)
_VPROD_RE = re.compile(r'<vProd>([\d.]+)</vProd>')
_VNF_RE = re.compile(r'<vNF>([\d.]+)</vNF>')
_DET_RE = re.compile(r'<det\b[^>]*>(.*?)</det>', re.S)
_PICMS_RE = re.compile(r'<ICMS00>(.*?)</ICMS00>', re.S)
_PICMS_FIELD_RE = re.compile(r'<pICMS>([\d.]+)</pICMS>')


def _extrair_cst(bloco_icms):
    """Dentro do conteudo de uma tag ICMSNN ou ICMSSNNN, acha o CST OU CSOSN.
       No Simples Nacional (CSOSN) os codigos que indicam ICMS-ST sao:
         201, 202, 203, 500, 900."""
    m = _CST_FIELD_RE.search(bloco_icms)
    if m: return m.group(1)
    m = _CSOSN_FIELD_RE.search(bloco_icms)
    if m: return m.group(1)
    return None


# ICMS do Simples Nacional (CSOSN) com ST: 201/202/203/500/900
# Tambem ICMS normal (CST) com ST: 10/30/60/70/90
CSOSN_ST = {'201', '202', '203', '500', '900'}
# uniao (usada para deteccao por codigo)
_ST_CODES = CST_ST | CSOSN_ST


def classificar_st(texto_xml):
    """Analisa um XML de NF-e/NFC-e. Retorna:
       {tem_st: bool, csts_encontrados: set, valor_total: float,
        valor_com_st: float, aliquota_interna: float|None}
       Onde:
         valor_total = soma de vProd (todos os itens)
         valor_com_st = soma de vProd dos itens onde ICMS tem CST em CST_ST
         aliquota_interna = pICMS do ICMS00 (primeiro que aparecer)"""
    csts = set()
    valor_total = 0.0
    valor_com_st = 0.0
    for m in _DET_RE.finditer(texto_xml):
        blk = m.group(1)
        # vProd deste item
        vp_m = _VPROD_RE.search(blk)
        if vp_m:
            try: v = float(vp_m.group(1))
            except Exception: v = 0.0
        else:
            v = 0.0
        valor_total += v
        # percorre tags ICMS (regime normal, CST) e ICMSSN (Simples, CSOSN)
        tem_st_no_item = False
        for ic in _ICMS_TAG_RE.finditer(blk):
            codigo = _extrair_cst(ic.group(2)) or ic.group(1)
            csts.add(codigo)
            if codigo in _ST_CODES:
                tem_st_no_item = True
        for ic in _ICMSSN_TAG_RE.finditer(blk):
            codigo = _extrair_cst(ic.group(2)) or ic.group(1)
            csts.add(codigo)
            if codigo in _ST_CODES:
                tem_st_no_item = True
        if tem_st_no_item:
            valor_com_st += v

    # valor total da NF (so pra conferencia — usa vNF se existir)
    valor_nf = valor_total
    vnf_m = _VNF_RE.search(texto_xml)
    if vnf_m:
        try: valor_nf = float(vnf_m.group(1))
        except Exception: pass

    # aliquota interna: pICMS de <ICMS00> (primeira ocorrencia em qq parte do XML)
    aliquota_interna = None
    for pm in _PICMS_RE.finditer(texto_xml):
        pic_m = _PICMS_FIELD_RE.search(pm.group(1))
        if pic_m:
            try: aliquota_interna = float(pic_m.group(1))
            except Exception: pass
            break

    return {
        'tem_st': valor_com_st > 0,
        'csts_encontrados': sorted(csts),
        'valor_total': round(valor_total, 2),
        'valor_com_st': round(valor_com_st, 2),
        'valor_nf': round(valor_nf, 2),
        'aliquota_interna': aliquota_interna,
    }


# aliquota ICMS por estado (UF -> aliquota intraestadual padrao).
# Faltando: usa 18% como default. SP=18, RJ=20, MG=18, PR=19.5, RS=18, SC=17,
# BA=20.5, PE=20.5, CE=20, GO=19, DF=20, ES=17, MT=17, MS=17, PB=20, RN=20,
# MA=22, AL=19, PI=21, SE=19, AM=20, PA=19, RO=19.5, AC=19, RR=20, AP=18, TO=20.
ALIQ_INTERNA_POR_UF = {
    'SP': 18.0, 'RJ': 20.0, 'MG': 18.0, 'PR': 19.5, 'RS': 18.0, 'SC': 17.0,
    'BA': 20.5, 'PE': 20.5, 'CE': 20.0, 'GO': 19.0, 'DF': 20.0, 'ES': 17.0,
    'MT': 17.0, 'MS': 17.0, 'PB': 20.0, 'RN': 20.0, 'MA': 22.0, 'AL': 19.0,
    'PI': 21.0, 'SE': 19.0, 'AM': 20.0, 'PA': 19.0, 'RO': 19.5, 'AC': 19.0,
    'RR': 20.0, 'AP': 18.0, 'TO': 20.0,
}
ALIQUOTA_DEFAULT = 18.0

def aliquota_interna(uf):
    return ALIQ_INTERNA_POR_UF.get((uf or '').upper().strip(), ALIQUOTA_DEFAULT)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding='utf-8', errors='replace') as f:
            t = f.read()
        r = classificar_st(t)
        print(f'Codigos encontrados: {r["csts_encontrados"]}')
        print(f'Tem ST: {r["tem_st"]}')
        print(f'Valor total NF: R$ {r["valor_total"]:,.2f}')
        print(f'Valor com ST:   R$ {r["valor_com_st"]:,.2f}')
        print(f'Valor <vNF>:    R$ {r["valor_nf"]:,.2f}')
        print(f'Aliquota interna (do XML): {r["aliquota_interna"]}%')