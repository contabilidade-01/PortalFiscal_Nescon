# -*- coding: utf-8 -*-
"""Motor NFSe - Portal Nacional (ADN). Baseado no projeto recuperado
   (CentralNFSeNacional): GET {base}/contribuintes/DFe/{nsu}?cnpjConsulta=&lote=true com mTLS (mesmo A1),
   payload JSON com arquivo XML em base64+gzip; 404 = fim, 429 = rate limit.
"""
import os, re, json, time, base64, gzip
from datetime import datetime, timedelta
import requests
import models
from engines import certs
from engines import guard

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
SAIDA = os.environ.get('FISCAL_XML_DIR') or CFG['pasta_saida_xml']; NFSE = CFG['nfse']
BASE_URL = NFSE['base_url'].rstrip('/')

def _decode(b64gzip):
    return gzip.decompress(base64.b64decode(b64gzip)).decode('utf-8', 'ignore')

def _extrair_docs(payload):
    """Extrai (nsu, xml, tipo) do JSON da ADN de forma tolerante a variacoes de chave.
       CONFIRMAR contra resposta real: chaves esperadas LoteDFe[].{NSU,ArquivoXml}."""
    lote = None
    for k in ('LoteDFe', 'lotesDFe', 'lote', 'documentos', 'Documentos'):
        if isinstance(payload, dict) and isinstance(payload.get(k), list):
            lote = payload[k]; break
    if lote is None and isinstance(payload, list):
        lote = payload
    if lote is None and isinstance(payload, dict) and any(kk in payload for kk in ('ArquivoXml', 'arquivoXml', 'arquivo_xml')):
        lote = [payload]
    out = []
    for item in (lote or []):
        if not isinstance(item, dict): continue
        nsu = item.get('NSU') or item.get('nsu') or item.get('Nsu')
        b64 = (item.get('ArquivoXml') or item.get('arquivoXml') or item.get('arquivo_xml')
               or item.get('DocumentoXml') or item.get('documento'))
        if not b64: continue
        try: xml = _decode(b64)
        except Exception: continue
        tipo = str(item.get('TipoDocumento') or '').upper()  # NFSE | EVENTO (swagger ADN)
        out.append((int(nsu) if nsu else 0, xml, tipo))
    return out

def _params(cnpj):
    """Query string exigida pela API oficial de distribuicao do ADN
    (GET /contribuintes/DFe/{NSU}?cnpjConsulta=..&lote=true). Sem ela o ADN
    passou a responder HTTP 400 para todas as empresas."""
    return {'cnpjConsulta': re.sub(r'\D', '', cnpj or ''), 'lote': 'true'}

def _motivo_erro(r):
    """Resumo do corpo de erro do ADN (StatusProcessamento + Erros[Codigo/Descricao])
    para gravar no historico; antes o corpo era descartado e so sobrava 'HTTP 400'."""
    try:
        j = r.json()
    except ValueError:
        return (r.text or '').strip()[:200]
    if not isinstance(j, dict):
        return str(j)[:200]
    partes = []
    st = j.get('StatusProcessamento')
    if st: partes.append(str(st))
    for e in (j.get('Erros') or j.get('erros') or []):
        if isinstance(e, dict):
            partes.append('%s %s' % (e.get('Codigo') or e.get('codigo') or '',
                                     e.get('Descricao') or e.get('descricao') or ''))
    return ' | '.join(p.strip() for p in partes if p and p.strip())[:200] or str(j)[:200]

def _doc_do_bloco(xml, tag):
    """CNPJ ou CPF de dentro do bloco <tag>...</tag> (sem sair dele). Antes a busca
    '<emit>.*?<CNPJ>' atravessava o bloco: prestador com CPF (pessoa fisica) pegava o
    CNPJ do TOMADOR e a nota tomada caia em 02_prestado (sumia de NFS-e Tomados)."""
    bloco = re.search(r'<%s\b[^>]*>(.*?)</%s>' % (tag, tag), xml, re.S)
    if not bloco:
        return None
    doc = re.search(r'<(?:CNPJ|CPF)>([^<]+)</', bloco.group(1))
    return re.sub(r'[^0-9A-Za-z]', '', doc.group(1)).upper() if doc else None

def sub_nfse(xml, cnpj):
    """Classifica a NFSe para a empresa: 02_prestado (saida, empresa=prestador)
    ou 01_tomado (entrada, empresa=tomador)."""
    cnpj = re.sub(r'[^0-9A-Za-z]', '', cnpj or '').upper()
    prest = _doc_do_bloco(xml, 'emit') or _doc_do_bloco(xml, 'prest')
    if prest == cnpj:
        return '02_prestado'
    if _doc_do_bloco(xml, 'toma') == cnpj:
        return '01_tomado'
    return '01_tomado'

def sub_doc(xml, cnpj, tipo=''):
    """Pasta do documento: eventos (cancelamento etc.) nao sao nota emitida nem tomada."""
    if tipo == 'EVENTO' or (not tipo and re.search(r'<(?:pedRegEvento|evento|infEvento)\b', xml)):
        return '03_eventos'
    return sub_nfse(xml, cnpj)

ROTULO_SUB = {'02_prestado': 'emitidas', '01_tomado': 'tomadas', '03_eventos': 'eventos'}

def _salvar(cnpj, xml, nsu, tipo=''):
    dt = re.search(r'<dhEmi>([^<]+)|<DataEmissao>([^<]+)|<dhProc>([^<]+)|<competencia>([^<]+)', xml, re.I)
    data = ''.join(g or '' for g in dt.groups()) if dt else ''
    comp = data[:7] if len(data) >= 7 else 'sem_data'
    ch = re.search(r'Id="[A-Za-z]*(\d{40,60})"|<[cC]haveAcesso>(\d{40,60})', xml)
    nome = (ch.group(1) or ch.group(2)) if ch else ('NSU%s' % nsu)
    sub = sub_doc(xml, cnpj, tipo)
    pasta = os.path.join(SAIDA, cnpj, comp, 'NFSe', sub); os.makedirs(pasta, exist_ok=True)
    open(os.path.join(pasta, '%s.xml' % nome), 'w', encoding='utf-8').write(xml)
    return sub

def reclassificar_disco(base=None):
    """Corrige XML ja salvos em 02_prestado que, pela regra atual, sao TOMADOS
    (prestador pessoa fisica). Move para 01_tomado da mesma competencia; se ja existir
    la, so remove a copia errada. Idempotente. Retorna quantos moveu."""
    base = base or SAIDA
    movidos = 0
    if not os.path.isdir(base):
        return 0
    for cnpj in os.listdir(base):
        dcnpj = os.path.join(base, cnpj)
        if not os.path.isdir(dcnpj):
            continue
        for comp in os.listdir(dcnpj):
            origem = os.path.join(dcnpj, comp, 'NFSe', '02_prestado')
            if not os.path.isdir(origem):
                continue
            for arq in os.listdir(origem):
                f = os.path.join(origem, arq)
                if not arq.lower().endswith('.xml') or not os.path.isfile(f):
                    continue
                try:
                    xml = open(f, encoding='utf-8', errors='ignore').read()
                except OSError:
                    continue
                if sub_doc(xml, cnpj) != '01_tomado':
                    continue
                destino = os.path.join(dcnpj, comp, 'NFSe', '01_tomado')
                os.makedirs(destino, exist_ok=True)
                alvo = os.path.join(destino, arq)
                if os.path.exists(alvo):
                    os.remove(f)
                else:
                    os.replace(f, alvo)
                movidos += 1
    return movidos

def _detalhe(ult, por_sub, erro=''):
    """Detalhe da execucao: o total da coluna Docs mistura tudo o que veio da fila do ADN;
    aqui fica quanto era emitida, tomada e evento."""
    partes = ['ultNSU=%s' % ult]
    if any(por_sub.values()):
        partes.append(', '.join('%s %d' % (ROTULO_SUB[k], por_sub.get(k, 0))
                                for k in ('02_prestado', '01_tomado', '03_eventos')))
    if erro:
        partes.append(erro)
    return ' · '.join(partes)

def puxar_nfse(emp):
    """emp: row de empresas. Varre NFSe da empresa no ADN (NSU incremental)."""
    cnpj = emp['cnpj']
    d0 = guard.pode(cnpj, 'nfse')
    if not d0.liberado:
        return 0, d0.parada or 'cooldown'
    if not emp['arquivo']:
        return 0, 'sem_certificado'
    try:
        leaf, keyf = certs.pem_temp(emp['arquivo'], emp['senha'])
    except Exception as e:
        return 0, 'erro_cert:%s' % str(e)[:40]
    sess = requests.Session(); cert = (leaf, keyf)
    ult = int(emp['ultnsu_nfse'] or 0); total = 0; rounds = 0; parada = 'cap'; erro = ''
    por_sub = {'02_prestado': 0, '01_tomado': 0, '03_eventos': 0}
    try:
        while rounds < NFSE['max_rounds_por_empresa']:
            rounds += 1
            url = '%s/contribuintes/DFe/%d' % (BASE_URL, ult)
            try:
                r = sess.get(url, params=_params(cnpj), cert=cert, timeout=NFSE['timeout_seg'])
            except requests.RequestException as e:
                parada = 'erro_rede:%s' % str(e)[:30]; break
            if r.status_code == 404: parada = 'fim'; break
            if r.status_code == 429:
                ra = r.headers.get('Retry-After')
                extra = int(ra) if ra and ra.isdigit() else NFSE['espera_seg'] * rounds
                if rounds >= NFSE['max_rounds_por_empresa']:
                    parada = '429'
                    break
                time.sleep(extra)
                continue
            if r.status_code != 200:
                parada = 'http_%d' % r.status_code; erro = _motivo_erro(r); break
            try: payload = r.json()
            except ValueError: parada = 'resposta_invalida'; break
            docs = _extrair_docs(payload)
            if not docs: parada = 'fim'; break
            for nsu, xml, tipo in docs:
                sub = _salvar(cnpj, xml, nsu, tipo); total += 1
                por_sub[sub] = por_sub.get(sub, 0) + 1
                if nsu > ult: ult = nsu
            with models.con() as c:
                c.execute('UPDATE empresas SET ultnsu_nfse=? WHERE id=?', (str(ult), emp['id']))
            guard.sleep_jitter(NFSE['espera_seg'])
    finally:
        for f in (leaf, keyf):
            try: os.remove(f)
            except Exception: pass
    with models.con() as c:
        c.execute('UPDATE empresas SET ultnsu_nfse=?, ultima_exec_nfse=?, total_nfse=total_nfse+? WHERE id=?',
                  (str(ult), datetime.now().strftime('%Y-%m-%d %H:%M'), total, emp['id']))
        c.execute('INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada,detalhe) VALUES(?,?,?,?,?,?,?)',
                  ('nfse', cnpj, emp['nome'], datetime.now().strftime('%Y-%m-%d %H:%M'), total, parada, _detalhe(ult, por_sub, erro)))
    if parada == '429':
        guard.registrar_bloqueio(cnpj, 'nfse', '429', nome=emp['nome'])
    elif parada in ('fim', 'cap') or total:
        guard.registrar_ok(cnpj, 'nfse')
    return total, parada
