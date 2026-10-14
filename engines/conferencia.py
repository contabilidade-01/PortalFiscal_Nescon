# -*- coding: utf-8 -*-
"""Conferencia fiscal + auditoria de numeracao.
   Le os XMLs em XML/<cnpj>/<AAAA-MM>/<NFe|NFSe|NFCe>/<sub>/*.xml, parseia os campos
   relevantes e devolve agregados para o Departamento Fiscal conferir tributacao
   (qtd + valor, ignorando cancelados cStat=110) e auditoria (quebras de nNF).
   Tolerante a variacoes: nfeProc/NFe (NF-e), nfeProc/NFe (NFC-e modelo 65),
   CompNfse/DPS (NFS-e Nacional padrao ABRASF/ADN).
"""
import os, re, json, gzip, base64
from collections import defaultdict
from engines import cfop as cfopmod
from engines import monofasico as monomod
from engines import st as stmod

# Reaproveita o mesmo XML_DIR (volume no Docker; config.json so no Windows local)
def _saida_path():
    import models
    return models.XML_DIR

SAIDA = _saida_path()

# Cache simples em memoria do processo (mtime do diretorio invalida).
_CACHE = {}

def _cache_get(key):
    e = _CACHE.get(key)
    if not e: return None
    base = os.path.join(SAIDA, e['cnpj_dir']) if 'cnpj_dir' in e else SAIDA
    try:
        m = os.path.getmtime(base)
        if m == e['mtime']:
            return e['value']
    except Exception:
        pass
    return None

def _cache_set(key, value, cnpj_dir=None):
    base = os.path.join(SAIDA, cnpj_dir) if cnpj_dir else SAIDA
    try: m = os.path.getmtime(base)
    except Exception: m = 0
    _CACHE[key] = {'value': value, 'mtime': m, 'cnpj_dir': cnpj_dir}

def _open_xml(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        try:
            with open(path, 'rb') as f:
                return gzip.decompress(f.read()).decode('utf-8', 'ignore')
        except Exception:
            return ''

# --------- Extratores ---------
def _campo(texto, tag):
    m = re.search(r'<(?:\w+:)?%s>([^<]+)</(?:\w+:)?%s>' % (tag, tag), texto, re.I)
    return m.group(1).strip() if m else ''

def _campo_n(texto, tag):
    """Retorna float do campo; 0.0 se nao houver ou invalido."""
    v = _campo(texto, tag)
    if not v: return 0.0
    try: return float(v)
    except Exception: return 0.0

def _modelo(texto):
    m = re.search(r'<(?:\w+:)?mod>(\d+)<', texto)
    return m.group(1) if m else ''

def _cstat(texto):
    """cStat da NFe/NFCe. Em nfeProc vem em <cStat> (autorizacao) ou <nProt>."""
    c = _campo(texto, 'cStat')
    if c: return c
    # eventos e resNFe tem cStat proprio; se nao tem, assume 100 (autorizada)
    return ''

def _cancelada_nfe(texto):
    """True se NF-e/NFC-e cancelada. cStat=110 na autorizacao OU evento 110111 com nProt."""
    cs = _cstat(texto)
    if cs == '110': return True
    # Eventos de cancelamento (110111) trazem <tpEvento>110111</tpEvento>
    if 'tpEvento>110111' in texto or 'tpEvento>110112' in texto:
        # se nProt presente, cancelamento homologado
        if _campo(texto, 'nProt'):
            return True
    return False

def _cancelada_nfse(texto):
    """NFS-e cancelada: tag <cStat>2</cStat> ou <situacao>2</situacao> ou <trib>...cancelada..."""
    cs = _campo(texto, 'cStat')
    if cs in ('2', '02'): return True
    sit = (_campo(texto, 'situacao') or _campo(texto, 'Situacao') or _campo(texto, 'sitDoc'))
    if sit in ('2', '02', 'Cancelada', 'cancelada'): return True
    if 'situacao>Cancelada' in texto or 'sit>Cancelada' in texto: return True
    return False

def parse_nfe(texto):
    """NF-e (modelo 55) ou NFC-e (65)."""
    # Emitente (fornecedor ou a propria empresa) -> chave de auditoria
    emit = (_campo(texto, 'CNPJ') or
            re.search(r'<emit>\s*<CNPJ>(\d+)', texto).group(1) if re.search(r'<emit>\s*<CNPJ>(\d+)', texto) else '')
    return {
        'modelo': _modelo(texto) or '55',
        'serie': _campo(texto, 'serie') or '0',
        'nNF': _campo(texto, 'nNF') or '0',
        'dhEmi': _campo(texto, 'dhEmi') or '',
        'valor': _campo_n(texto, 'vNF'),
        'cStat': _cstat(texto),
        'cancelada': _cancelada_nfe(texto),
        'emit_cnpj': emit,
        'emit_nome': _campo(texto, 'xNome'),
    }

def parse_nfse(texto):
    """NFS-e - estrutura varia por municipio/padrao. Tenta varios campos."""
    nNF = (_campo(texto, 'nNFSe') or _campo(texto, 'Numero') or _campo(texto, 'nDoc')
           or _campo(texto, 'numero') or '0')
    serie = _campo(texto, 'serie') or '0'
    data = (_campo(texto, 'dhEmi') or _campo(texto, 'DataEmissao')
            or _campo(texto, 'dhProc') or _campo(texto, 'competencia') or '')
    valor = (_campo_n(texto, 'vLiq') or _campo_n(texto, 'vServ') or
             _campo_n(texto, 'valorServicos') or _campo_n(texto, 'valorLiquido') or
             _campo_n(texto, 'vNF') or _campo_n(texto, 'valor'))
    return {
        'modelo': 'nfse',
        'serie': serie,
        'nNF': nNF,
        'dhEmi': data,
        'valor': valor,
        'cStat': '',
        'cancelada': _cancelada_nfse(texto),
    }

def parse_doc(path):
    texto = _open_xml(path)
    if not texto: return None
    # NFC-e = mesma estrutura nfeProc mas modelo 65
    if '<nfeProc' in texto or '<NFe' in texto or '<infNFe' in texto:
        return parse_nfe(texto)
    # NFS-e (varios namespaces)
    if any(t in texto for t in ('<CompNfse', '<DPS', '<nfse', '<NFS-e')):
        return parse_nfse(texto)
    return None

# --------- Varredura ---------
def _listar_xmls(cnpj_dir):
    """Itera (competencia, modelo, sub, path) para todos XMLs da empresa."""
    if not os.path.isdir(cnpj_dir): return
    for comp in sorted(os.listdir(cnpj_dir)):
        if not re.match(r'\d{4}-\d{2}$', comp): continue
        for modelo in ('NFe', 'NFSe', 'NFCe'):
            d = os.path.join(cnpj_dir, comp, modelo)
            if not os.path.isdir(d): continue
            for sub in os.listdir(d):
                sd = os.path.join(d, sub)
                if not os.path.isdir(sd): continue
                for f in os.listdir(sd):
                    if f.lower().endswith('.xml'):
                        yield comp, modelo, sub, os.path.join(sd, f)

# --------- API publica ---------
def conferencia(cnpjs, ano_mes=None, incluir_canceladas=False):
    """Retorna lista [{cnpj, competencia, modelo, sub, qtd, valor, canceladas}]."""
    saida = SAIDA
    out = []
    for cnpj in cnpjs:
        cdir = os.path.join(saida, cnpj)
        if not os.path.isdir(cdir): continue
        bucket = {}  # (comp, modelo, sub) -> {'qtd':0,'valor':0.0,'canceladas':0}
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if ano_mes and comp != ano_mes: continue
            d = parse_doc(path)
            if not d: continue
            if d['cancelada']:
                key = (comp, modelo, sub)
                bucket.setdefault(key, {'qtd':0,'valor':0.0,'canceladas':0})
                bucket[key]['canceladas'] += 1
                if incluir_canceladas:
                    bucket[key]['qtd'] += 1
                    bucket[key]['valor'] += d['valor']
                continue
            key = (comp, modelo, sub)
            b = bucket.setdefault(key, {'qtd':0,'valor':0.0,'canceladas':0})
            b['qtd'] += 1
            b['valor'] += d['valor']
        for (comp, modelo, sub), b in sorted(bucket.items()):
            out.append({
                'cnpj': cnpj, 'competencia': comp, 'modelo': modelo, 'sub': sub,
                'qtd': b['qtd'], 'valor': round(b['valor'], 2),
                'canceladas': b['canceladas'],
            })
    return out

# Subpastas que representam NF EMITIDAS pela propria empresa (auditoria contábil):
#   NFe/04_saida  -> vendas proprias (emitidas pela empresa ou via autXML do escritorio)
#   NFe/05_propria -> NFe propria do escritorio (cert da Nescon emitiu)
#   NFCe/01_venda -> vendas NFC-e (modelo 65, varejo)
#   NFSe/02_prestado -> servicos PRESTADOS pela empresa
# NFe entradas (compras) e NFSe tomadas (servicos contratados) NAO entram.
SUBS_PROPRIAS = {
    'NFe':  {'04_saida', '05_propria'},
    'NFCe': {'01_venda'},
    'NFSe': {'02_prestado'},
}


def auditoria_numeracao(cnpjs, ano_mes=None, modelos=None, contar_canceladas=True):
    """Auditoria por EMPRESA CLIENTE: apenas NF EMITIDAS pela propria empresa.
       Nunca entradas (compras) nem tomadas (servicos contratados).
       modelos: set/None -> padrao: {'NFe','NFCe','NFSe'} (so os que tem subpasta propria).
       contar_canceladas: True (padrao) -> canceladas ocupam o nNF na sequencia
       (recomendacao contabil/fiscal: cancelada faz parte da numeracao)."""
    if modelos is None:
        modelos = {'NFe', 'NFCe', 'NFSe'}
    saida = SAIDA
    out = []
    for cnpj in cnpjs:
        cdir = os.path.join(saida, cnpj)
        if not os.path.isdir(cdir): continue
        grupo = {}  # (comp, modelo, serie) -> {'presentes': set(), 'canceladas': int, 'canceladas_set': set()}
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if ano_mes and comp != ano_mes: continue
            if modelo not in modelos: continue
            if sub not in SUBS_PROPRIAS.get(modelo, set()): continue
            d = parse_doc(path)
            if not d: continue
            try: nnf = int(d['nNF'])
            except Exception: continue
            serie = d['serie'] or '0'
            key = (comp, modelo, serie)
            b = grupo.setdefault(key, {'presentes': set(), 'canceladas': 0, 'canceladas_set': set()})
            if d['cancelada']:
                b['canceladas'] += 1
                b['canceladas_set'].add(nnf)
                if contar_canceladas:
                    b['presentes'].add(nnf)
                continue
            b['presentes'].add(nnf)
        for (comp, modelo, serie), b in sorted(grupo.items()):
            ns = sorted(b['presentes'])
            canc_set = b['canceladas_set']
            canc = b['canceladas']
            if not ns and not canc: continue
            qtd = len(ns)
            # validas (nao canceladas) = total - canceladas que estao no set
            canc_no_set = len(canc_set & b['presentes'])
            validas = qtd - canc_no_set
            if ns:
                mn, mx = min(ns), max(ns)
                esperados = mx - mn + 1
                faltam = esperados - qtd  # buracos = fora do set (mesmo canceladas contando)
                buracos = []
                range_grande = esperados >= 200000
                maioria_faltando = faltam * 2 > esperados
                if faltam > 0 and not range_grande and not maioria_faltando:
                    presentes = set(ns)
                    cap = 200
                    for x in range(mn, mx + 1):
                        if x not in presentes:
                            buracos.append(x)
                            if len(buracos) > cap:
                                buracos = buracos[:cap] + ['...+%d a mais' % (faltam - cap)]
                                break
                elif faltam > 0:
                    cap = 200
                    if not range_grande:
                        for x in range(mn, mx + 1):
                            if x not in set(ns):
                                buracos.append(x)
                                if len(buracos) >= cap:
                                    break
                    if range_grande:
                        buracos = []
            else:
                mn = mx = esperados = qtd = validas = faltam = 0
                buracos = []
            if len(buracos) > 12:
                resumo = '%s ... (+%d)' % (','.join(str(x) for x in buracos[:10]), len(buracos)-10)
            else:
                resumo = ','.join(str(x) for x in buracos) if buracos else '-'
            out.append({
                'cnpj': cnpj, 'competencia': comp, 'modelo': modelo, 'serie': serie,
                'emit_cnpj': cnpj, 'emit_nome': '',
                'qtd': qtd, 'validas': validas,
                'minimo': mn, 'maximo': mx, 'esperados': esperados,
                'faltam': faltam, 'buracos': buracos, 'buracos_resumido': resumo,
                'canceladas': canc,
                'quebra': faltam > 0,
            })
    return out

# ---------- Faturamento por CFOP (Etapa 12) ----------
_DET_RE = re.compile(r'<det\b[^>]*>(.*?)</det>', re.S)
_CFOP_RE = re.compile(r'<CFOP>(\d{4})</CFOP>')
_VPROD_RE = re.compile(r'<vProd>([\d.]+)</vProd>')

def _itens_nfe(texto):
    """Yield (cfop, vProd) de cada item (det/prod) da NF-e."""
    for m in _DET_RE.finditer(texto):
        blk = m.group(1)
        cf = _CFOP_RE.search(blk)
        if not cf:
            continue
        vp = _VPROD_RE.search(blk)
        try:
            v = float(vp.group(1)) if vp else 0.0
        except Exception:
            v = 0.0
        yield cf.group(1), v

def _chave_de(texto):
    m = re.search(r'Id="NFe(\d{44})"|<chNFe>(\d{44})</chNFe>', texto)
    return (m.group(1) or m.group(2)) if m else None

_NCM_RE = re.compile(r'<NCM>(\d+)</NCM>')

def _itens_nfe_full(texto):
    """Yield (cfop, ncm, vProd) de cada item da NF-e."""
    for m in _DET_RE.finditer(texto):
        blk = m.group(1)
        cf = _CFOP_RE.search(blk)
        if not cf:
            continue
        nc = _NCM_RE.search(blk)
        vp = _VPROD_RE.search(blk)
        try:
            v = float(vp.group(1)) if vp else 0.0
        except Exception:
            v = 0.0
        yield cf.group(1), (nc.group(1) if nc else ''), v

def economia_monofasico(cnpjs, ano='', mes=''):
    """Sobre as SAIDAS reais (NFe 04_saida, nao canceladas): apura o faturamento de
       VENDA (CFOP base) e a parcela cujo NCM e monofasico (PIS/COFINS aliquota zero
       na revenda). Retorna {cnpj: {fat_venda, fat_mono, pct, notas_mono,
       por_categoria{cat:{valor, ncms[]}}, por_ncm{ncm:valor}}}."""
    ano = (ano or '').strip(); mes = (mes or '').strip()
    out = {}
    for cnpj in cnpjs:
        cdir = os.path.join(SAIDA, cnpj)
        if not os.path.isdir(cdir):
            continue
        fat_venda = 0.0; fat_mono = 0.0
        por_cat = {}; por_ncm = {}; notas_mono = set()
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if (ano and comp[:4] != ano) or (mes and comp[5:7] != mes):
                continue
            # aceita NFe/04_saida (venda propria + autXML) e NFe/05_propria (escritorio)
            # aceita NFCe/01_venda (varejo modelo 65)
            if modelo == 'NFe' and sub not in ('04_saida', '05_propria'):
                continue
            if modelo == 'NFCe' and sub != '01_venda':
                continue
            texto = _open_xml(path)
            if not texto or _cancelada_nfe(texto):
                continue
            ch = _chave_de(texto) or path
            for cf, ncm, vprod in _itens_nfe_full(texto):
                if not cfopmod.classificar(cf)['base']:   # so venda (nao remessa/retorno)
                    continue
                fat_venda += vprod
                mc = monomod.classificar(ncm)
                if mc['monofasico']:
                    fat_mono += vprod
                    g = por_cat.setdefault(mc['categoria'], {'valor': 0.0, 'ncms': set(),
                                                             'base_legal': mc['base_legal']})
                    g['valor'] += vprod; g['ncms'].add(ncm[:8])
                    por_ncm[ncm] = por_ncm.get(ncm, 0.0) + vprod
                    notas_mono.add(ch)
        if fat_venda > 0 or fat_mono > 0:
            for cat, g in por_cat.items():
                g['ncms'] = sorted(g.pop('ncms')); g['valor'] = round(g['valor'], 2)
            out[cnpj] = {
                'fat_venda': round(fat_venda, 2), 'fat_mono': round(fat_mono, 2),
                'pct': round(100.0 * fat_mono / fat_venda, 1) if fat_venda else 0.0,
                'notas_mono': len(notas_mono),
                'por_categoria': dict(sorted(por_cat.items(), key=lambda kv: -kv[1]['valor'])),
                'por_ncm': {k: round(v, 2) for k, v in sorted(por_ncm.items(), key=lambda kv: -kv[1])},
            }
    return out

# =============================================================================
# Etapa 15 — MENSURACAO 3-FONTES (vendas + compras + extrato do Simples)
# Combina sinais de tres origens para validar a % monofasica real do cliente:
#   1. Vendas reais (NF/NFCe saida) -> % mono da venda (mais confiavel)
#   2. Compras reais (NF entrada, 12m rolling) -> % mono da compra (teto)
#   3. Extrato do Simples (PGDAS-D importado) -> receita oficial declarada
# Semaforo:
#   verde  = vendas confirmam (compra vs venda dentro de 5 p.p.)
#   amarelo= extrapolacao (so compras; vendas parciais)
#   vermelho= sem fonte confiavel
# =============================================================================

def _receita_pgdas(cnpj, ano, mes):
    """Receita do PGDAS importado para o mes. None se nao ha."""
    try:
        import models
        with models.con() as c:
            r = c.execute('SELECT receita_total FROM pgdas_recibos WHERE cnpj=? AND ano=? AND mes=?',
                          (cnpj, ano, mes)).fetchone()
        return float(r['receita_total']) if r else None
    except Exception:
        return None

MIN_MESES_RBT12 = 6   # abaixo disso NAO extrapola (evita distorcer a faixa do Simples)

def rbt12_de_pgdas(lista_pgdas, rbt12_cadastro=0.0, limite=12):
    """Deriva o RBT12 dos recibos PGDAS-D importados (lista ordenada do mais recente).

       Politica prudente (o RBT12 define a faixa/aliquota — errar aqui distorce tudo):
         - 12+ meses   -> soma real dos 12 ultimos ....... fonte 'pgdas' (VENCE o cadastro)
         - 6..11 meses -> proporcionaliza (media*12) SO se o cadastro estiver vazio
                          ................................ fonte 'pgdas_proporcional'
         - <6 meses    -> nao usa; mantem o cadastro (extrapolar 1-2 meses e' perigoso)
       Retorna {rbt12, fonte, meses, proporcional}."""
    cadastro = float(rbt12_cadastro or 0)
    base = {'rbt12': cadastro, 'fonte': ('cadastro' if cadastro else 'ausente'),
            'meses': 0, 'proporcional': False}
    if not lista_pgdas:
        return base
    ult = [p for p in lista_pgdas[:limite] if float(p.get('receita_total') or 0) > 0]
    n = len(ult)
    if n == 0:
        return base
    soma = sum(float(p.get('receita_total') or 0) for p in ult)
    if n >= limite:
        return {'rbt12': round(soma, 2), 'fonte': 'pgdas', 'meses': n, 'proporcional': False}
    if n >= MIN_MESES_RBT12 and not cadastro:
        return {'rbt12': round(soma / n * limite, 2), 'fonte': 'pgdas_proporcional',
                'meses': n, 'proporcional': True}
    base['meses'] = n
    return base

def rbt12_efetivo(cnpj, rbt12_cadastro=0.0, limite=12):
    """Igual a rbt12_de_pgdas, buscando os recibos do CNPJ direto no banco."""
    lista = []
    try:
        import models
        with models.con() as c:
            for r in c.execute("SELECT receita_total FROM pgdas_recibos WHERE cnpj=?"
                               " ORDER BY ano DESC, mes DESC LIMIT ?",
                               (cnpj, limite)).fetchall():
                lista.append({'receita_total': r['receita_total']})
    except Exception:
        pass
    return rbt12_de_pgdas(lista, rbt12_cadastro, limite)

def mensuracao_beneficio(cnpjs, ano, mes, janela=12, markup=1.5, tolerancia_pp=5.0):
    """Cruza 3 fontes para cada CNPJ. Retorna dict {cnpj: {...semaforo, economia_validada, ...}}.
       Hierarquia de receita:
         - PGDAS (receita oficial declarada) — MAIS confiavel para o calculo do DAS.
         - VENDAS REAIS (NF saida) — checagem de consistencia do % mono.
         - compras * markup (proxy fraco).
       A % monofasica:
         - se ha vendas REAIS: usa % das vendas (% da receita que foi monofasica).
         - senao: usa % das compras (proxy - relevante se revenda = mesmo mix)."""
    # 1) vendas reais (mes)
    vendas = economia_monofasico(cnpjs, ano=str(ano), mes='%02d' % mes)
    # 2) compras reais (rolling 12m)
    compras = economia_mono_estimada_compras(cnpjs, janela=janela, markup=markup)

    out = {}
    for cnpj in cnpjs:
        c_v = vendas.get(cnpj, {})
        c_c = compras.get(cnpj, {})
        rec_pgdas = _receita_pgdas(cnpj, ano, mes)

        pct_v = c_v.get('pct', 0.0) if c_v else 0.0
        pct_c = c_c.get('pct_mono', 0.0) if c_c else 0.0
        receita_v = c_v.get('fat_venda', 0.0) if c_v else 0.0

        # hierarquia de receita (PGDAS > venda > markup)
        fonte_receita = 'sem_dados'; receita = 0.0
        if rec_pgdas and rec_pgdas > 0:
            receita = rec_pgdas; fonte_receita = 'pgdas'
        elif receita_v > 0:
            receita = receita_v; fonte_receita = 'venda_real'
        elif c_c and c_c.get('total_comprado', 0) > 0:
            receita = c_c['total_comprado'] * markup
            fonte_receita = 'markup'

        # % mono: vendas se disponivel, senao compras
        if receita_v > 0:
            pct_mono = pct_v
            pct_origem = 'vendas'
        elif pct_c > 0:
            pct_mono = pct_c
            pct_origem = 'compras'
        else:
            pct_mono = 0.0
            pct_origem = 'nenhuma'

        # economia
        cad = c_c
        anexo = cad.get('anexo') or ''
        rbt12 = cad.get('rbt12') or 0
        ec, ef, fx = monomod.economia_pis_cofins(receita * pct_mono / 100.0, anexo, rbt12)

        # semaforo (com base em vendas vs compras)
        if receita_v > 0 and c_c.get('total_comprado', 0) > 0:
            diff = abs(pct_v - pct_c)
            semaforo = 'verde' if diff <= tolerancia_pp else 'vermelho'
        elif (rec_pgdas or receita_v) and c_c.get('total_comprado', 0) > 0:
            semaforo = 'amarelo'  # receita OK mas sem vendas p/ comparar
        elif c_c.get('total_comprado', 0) > 0:
            semaforo = 'amarelo'
        else:
            semaforo = 'vermelho'

        out[cnpj] = {
            'fonte_receita': fonte_receita,
            'receita': round(receita, 2),
            'pct_mono_vendas': pct_v,
            'pct_mono_compras': pct_c,
            'pct_mono_usado': pct_mono,
            'economia': ec,
            'aliquota_efetiva': ef, 'faixa': fx,
            'anexo': anexo, 'rbt12': rbt12,
            'rbt12_fonte': cad.get('rbt12_fonte') or 'ausente',
            'rbt12_meses': cad.get('rbt12_meses') or 0,
            'semaforo': semaforo,
            'tem_pgdas': rec_pgdas is not None,
        }
    return out


# =============================================================================
# Etapa 14 — SIMULACAO por compras (rolling 12m)
# Onde nao temos saidas (autXML nao ativo), estimamos a economia de PIS/COFINS
# monofasicos pela PROPORCAO de NCM monofasico nas COMPRAS REAIS (rolling 12m).
#
# Compra real = NF em 01_entrada onde <dest> == cnpj (cliente e destinatario).
# Venda propria do cliente = NF em 04_saida onde <emit> == cnpj (gravada corretamente).
#
# Receita: PGDAS importado -> saida_real_em_disco -> compras_base_total * markup 1.5.
# =============================================================================

_CNPJ_RE = re.compile(r'<emit>\s*<CNPJ>(\d+)')
_DEST_RE = re.compile(r'<dest>\s*<CNPJ>(\d+)')

def _classificar_nf(texto, cnpj_pasta):
    """'compra_real' | 'venda_propria' | 'outros' (resumo/evento/sem emit/dest)."""
    em = _CNPJ_RE.search(texto); de = _DEST_RE.search(texto)
    emit = em.group(1) if em else None
    dest = de.group(1) if de else None
    if emit and dest:
        if dest == cnpj_pasta and emit != cnpj_pasta:
            return 'compra_real'
        if emit == cnpj_pasta:
            return 'venda_propria'
    return 'outros'

def _ultimas_competencias(cnpj_dir, janela=12):
    """Retorna as ultimas N competencias (AAAA-MM) existentes em disco para a empresa."""
    if not os.path.isdir(cnpj_dir):
        return []
    comps = [c for c in os.listdir(cnpj_dir) if re.match(r'\d{4}-\d{2}$', c)]
    return sorted(comps)[-janela:]

def economia_mono_estimada_compras(cnpjs, janela=12, markup=1.5):
    """Para cada CNPJ:
      - % monofasico = valor_mono(ult N meses, compras com CFOP base ENTRADA) / total(compras REAIS)
      - Receita       = PGDAS_importado (ult mes disponivel, se houver)
                       -> saida_real_em_disco (ult mes, mesmo filtro do metodo real)
                       -> compras_base_total * markup
      - Economia      = receita * pct_mono * aliquota_efetiva(anexo, rbt12) * share_pis_cofins
      Retorna {cnpj: {janela_meses, total_comprado, valor_mono, pct_mono,
                       receita, receita_fonte, receita_periodo,
                       economia_estimada, economia_maxima,
                       aliquota_efetiva, faixa, anexo, rbt12,
                       por_categoria, qtd_compras, qtd_vendas_proprias}}.
    """
    # PGDAS importado (silencioso se a tabela nao existir ainda / modelo nao migrado)
    pgdas = {}
    cad = {}
    try:
        import models
        with models.con() as c:
            try:
                for r in c.execute("SELECT cnpj, ano, mes, receita_total, anexo FROM pgdas_recibos"
                                   " ORDER BY ano DESC, mes DESC").fetchall():
                    pgdas.setdefault(r['cnpj'], []).append({
                        'ano': r['ano'], 'mes': r['mes'],
                        'receita_total': float(r['receita_total'] or 0),
                        'anexo': r['anexo'] or '',
                    })
            except Exception:
                pass
            for r in c.execute("SELECT cnpj, simples_anexo, simples_rbt12 FROM empresas").fetchall():
                cad[r['cnpj']] = {'anexo': r['simples_anexo'],
                                  'rbt12': float(r['simples_rbt12'] or 0)}
    except Exception:
        pass

    out = {}
    for cnpj in cnpjs:
        cdir = os.path.join(SAIDA, cnpj)
        if not os.path.isdir(cdir): continue
        comps = _ultimas_competencias(cdir, janela=janela)
        if not comps:
            continue
        comp_set = set(comps)
        # ---- COMPRAS REAIS (rolling): so NFs onde <dest> == cnpj E CFOP base ENTRADA ----
        tot_compras = 0.0; tot_mono = 0.0; qtd_compras = 0
        por_cat = {}
        # tambem aproveitar p/ receita fallback: VENDAS PROPRIAS do cliente
        venda_por_comp = {}  # comp -> valor de venda
        qtd_vendas = 0
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if comp not in comp_set: continue
            if modelo != 'NFe' or sub not in ('01_entrada', '04_saida', '05_propria'):
                continue
            texto = _open_xml(path)
            if not texto or _cancelada_nfe(texto): continue
            tipo = _classificar_nf(texto, cnpj)
            if tipo == 'compra_real':
                # checa CFOP base para a base de tributacao. IMPORTANTE: o CFOP na NF
                # fala da perspectiva do EMITENTE. Para uma COMPRA do cliente (NF
                # esta em 01_entrada, destinatario == cliente), o CFOP 5xxx/6xxx/7xxx
                # do emitente e' a direcao SAIDA para o fornecedor, mas para o
                # cliente e' COMPRA. Entao: se a NF e' compra_real (dest==cnpj),
                # ACEITAMOS CFOPs de qualquer direcao (5/6/7 viram "entrada" no
                # contexto do cliente, e 1/2/3 sao "entrada" do emitente).
                for cf, ncm, vprod in _itens_nfe_full(texto):
                    cls = cfopmod.classificar(cf)
                    if not cls['base']: continue
                    tot_compras += vprod
                    mc = monomod.classificar(ncm)
                    if mc['monofasico']:
                        tot_mono += vprod
                        por_cat[mc['categoria']] = por_cat.get(mc['categoria'], 0.0) + vprod
                qtd_compras += 1
            elif tipo == 'venda_propria':
                v_venda = 0.0
                for cf, ncm, vprod in _itens_nfe_full(texto):
                    cls = cfopmod.classificar(cf)
                    if cls['base'] and cls['direcao'] == 'saida':
                        v_venda += vprod
                if v_venda > 0:
                    venda_por_comp[comp] = venda_por_comp.get(comp, 0.0) + v_venda
                    qtd_vendas += 1

        # ---- Receita: PGDAS -> venda_propria -> markup ----
        rec_fonte = 'markup'; receita = 0.0; rec_periodo = 'rolling_12m'
        lista_pgdas = pgdas.get(cnpj, [])
        if lista_pgdas:
            p = lista_pgdas[0]
            receita = p['receita_total']
            rec_periodo = '%02d/%04d' % (p['mes'], p['ano'])
            rec_fonte = 'pgdas'
        elif venda_por_comp:
            comp_top = max(venda_por_comp, key=venda_por_comp.get)
            receita = venda_por_comp[comp_top]
            rec_periodo = comp_top
            rec_fonte = 'venda_propria'
        else:
            receita = tot_compras * (markup or 1.5)
            rec_periodo = 'rolling_12m'
            rec_fonte = 'markup'

        # ---- Anexo / RBT12 (PGDAS tem prioridade; cadastro e' o fallback) ----
        info_cad = cad.get(cnpj, {}) or {}
        anexo = info_cad.get('anexo') or ''
        rb = rbt12_de_pgdas(lista_pgdas, info_cad.get('rbt12') or 0.0)
        rbt12 = rb['rbt12']
        if rec_fonte == 'pgdas' and lista_pgdas and lista_pgdas[0].get('anexo'):
            anexo = lista_pgdas[0]['anexo']

        # ---- Economia ----
        pct = (tot_mono / tot_compras) if tot_compras else 0.0
        receita_mono = receita * pct
        ec, ef, fx = monomod.economia_pis_cofins(receita_mono, anexo, rbt12)
        ec100, _, _ = monomod.economia_pis_cofins(receita, anexo, rbt12)
        out[cnpj] = {
            'janela_meses': len(comps),
            'meses_considerados': comps,
            'total_comprado': round(tot_compras, 2),
            'valor_mono': round(tot_mono, 2),
            'pct_mono': round(100.0 * pct, 1),
            'qtd_compras': qtd_compras,
            'qtd_vendas_proprias': qtd_vendas,
            'por_categoria': {k: round(v, 2) for k, v in sorted(por_cat.items(), key=lambda kv: -kv[1])},
            'receita': round(receita, 2),
            'receita_fonte': rec_fonte,
            'receita_periodo': rec_periodo,
            'anexo': anexo,
            'rbt12': round(rbt12, 2),
            'rbt12_fonte': rb['fonte'],
            'rbt12_meses': rb['meses'],
            'rbt12_proporcional': rb['proporcional'],
            'aliquota_efetiva': ef,
            'faixa': fx,
            'economia_estimada': ec,
            'economia_maxima': ec100,
        }
    return out

def faturamento_cfop(cnpjs, ano='', mes=''):
    """Classifica as NF-e (modelo 55) por GRUPO de CFOP, separando saida (faturamento)
       de entrada (compras), com valor por item (vProd) e contagem de notas distintas.
       Ignora canceladas. So NFe 01_entrada e 04_saida (as que tem itens/CFOP).
       ano/mes: filtros opcionais (qualquer combinacao).
       Retorna {cnpj: {'saida': {grupo: {qtd,valor,cfops}}, 'entrada': {...}}}."""
    ano = (ano or '').strip(); mes = (mes or '').strip()
    out = {}
    for cnpj in cnpjs:
        cdir = os.path.join(SAIDA, cnpj)
        if not os.path.isdir(cdir):
            continue
        d = {'saida': {}, 'entrada': {}}
        notas = {'saida': defaultdict(set), 'entrada': defaultdict(set)}
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if (ano and comp[:4] != ano) or (mes and comp[5:7] != mes):
                continue
            if modelo != 'NFe':
                continue
            if sub == '04_saida':
                direc = 'saida'
            elif sub == '01_entrada':
                direc = 'entrada'
            else:
                continue
            texto = _open_xml(path)
            if not texto or _cancelada_nfe(texto):
                continue
            ch = _chave_de(texto) or path
            for cf, vprod in _itens_nfe(texto):
                grp = cfopmod.classificar(cf)['grupo']
                g = d[direc].setdefault(grp, {'qtd': 0, 'valor': 0.0, 'cfops': {}})
                g['valor'] += vprod
                g['cfops'][cf] = g['cfops'].get(cf, 0.0) + vprod
                notas[direc][grp].add(ch)
        for direc in ('saida', 'entrada'):
            for grp, chset in notas[direc].items():
                d[direc][grp]['qtd'] = len(chset)
                d[direc][grp]['valor'] = round(d[direc][grp]['valor'], 2)
                d[direc][grp]['cfops'] = {k: round(v, 2)
                                          for k, v in sorted(d[direc][grp]['cfops'].items())}
        if d['saida'] or d['entrada']:
            out[cnpj] = d
    return out


# =============================================================================
# Etapa 16 — RECEITA COM ICMS-ST (segregacao no Simples Nacional)
# Detecta receita com ICMS-ST nas SAIDAS REAIS (NFe 04_saida, NFCe 01_venda) a
# partir do CST do ICMS no XML (CST 10/30/60/70/90 = tem ST; 00/20/40/41 = normal).
# Retorna {cnpj: {fat_total, fat_com_st, pct_st, aliquota_interna, notas, csts}}.
# Aliquota interna vem do proprio XML (ICMS00/pICMS) ou do cadastro (UF -> tabela).
# =============================================================================

def receita_com_st(cnpjs, ano='', mes=''):
    """Itera NFe 04_saida + NFCe 01_venda nao canceladas; classifica por CST ICMS.
       Retorna dict {cnpj: {fat_total, fat_com_st, pct_st, aliquota_interna,
       notas_total, notas_com_st, csts_encontrados, aliquota_origem}}."""
    ano = (ano or '').strip(); mes = (mes or '').strip()
    out = {}
    for cnpj in cnpjs:
        cdir = os.path.join(SAIDA, cnpj)
        if not os.path.isdir(cdir): continue
        fat_total = 0.0; fat_com_st = 0.0
        notas_total = 0; notas_com_st = 0
        csts = set()
        aliquota_interna = None
        aliquota_origem = 'desconhecida'
        for comp, modelo, sub, path in _listar_xmls(cdir):
            if (ano and comp[:4] != ano) or (mes and comp[5:7] != mes):
                continue
            # mesma regra do economia_monofasico: 04_saida + 05_propria + NFCe 01_venda
            if modelo == 'NFe' and sub not in ('04_saida', '05_propria'):
                continue
            if modelo == 'NFCe' and sub != '01_venda':
                continue
            texto = _open_xml(path)
            if not texto or _cancelada_nfe(texto): continue
            notas_total += 1
            r = stmod.classificar_st(texto)
            fat_total += r['valor_total']
            csts.update(r['csts_encontrados'])
            # aliquota interna: pega a primeira do XML
            if aliquota_interna is None and r['aliquota_interna']:
                aliquota_interna = r['aliquota_interna']
                aliquota_origem = 'xml'
            if r['tem_st']:
                fat_com_st += r['valor_com_st']
                notas_com_st += 1
        if notas_total == 0 and not csts:
            continue
        # fallback: se nao achou no XML, busca UF do cadastro
        if aliquota_interna is None:
            try:
                import models
                with models.con() as c:
                    row = c.execute('SELECT uf FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
                uf = row['uf'] if row else ''
                aliquota_interna = stmod.aliquota_interna(uf)
                aliquota_origem = 'uf_cadastro' if uf else 'padrao'
            except Exception:
                aliquota_interna = stmod.ALIQUOTA_DEFAULT
                aliquota_origem = 'padrao'
        pct_st = round(100.0 * fat_com_st / fat_total, 1) if fat_total else 0.0
        out[cnpj] = {
            'fat_total': round(fat_total, 2),
            'fat_com_st': round(fat_com_st, 2),
            'pct_st': pct_st,
            'notas_total': notas_total,
            'notas_com_st': notas_com_st,
            'csts_encontrados': sorted(csts),
            'aliquota_interna': aliquota_interna,
            'aliquota_origem': aliquota_origem,
        }
    return out


def competencias_disponiveis(cnpjs):
    """Lista anos-mes que existem em disco para o conjunto de CNPJs."""
    saida = SAIDA
    seen = set()
    for cnpj in cnpjs:
        cdir = os.path.join(saida, cnpj)
        if not os.path.isdir(cdir): continue
        for comp in os.listdir(cdir):
            if re.match(r'\d{4}-\d{2}$', comp):
                seen.add(comp)
    return sorted(seen, reverse=True)