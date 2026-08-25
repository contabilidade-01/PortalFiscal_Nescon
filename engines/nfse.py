# -*- coding: utf-8 -*-
"""Motor NFSe - Portal Nacional (ADN). Baseado no projeto recuperado
   (CentralNFSeNacional): GET {base}/contribuintes/DFe/{nsu} com mTLS (mesmo A1),
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
    """Extrai (nsu, xml) do JSON da ADN de forma tolerante a variacoes de chave.
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
        out.append((int(nsu) if nsu else 0, xml))
    return out

def sub_nfse(xml, cnpj):
    """Classifica a NFSe para a empresa: 02_prestado (saida, empresa=prestador)
    ou 01_tomado (entrada, empresa=tomador)."""
    emit = re.search(r'<emit>.*?<CNPJ>(\d+)', xml, re.S) or re.search(r'<prest>.*?<CNPJ>(\d+)', xml, re.S)
    prest = emit.group(1) if emit else None
    if prest == cnpj:
        return '02_prestado'
    toma = re.search(r'<toma>.*?<(?:CNPJ|CPF)>(\d+)', xml, re.S)
    if toma and toma.group(1) == cnpj:
        return '01_tomado'
    return '02_prestado' if prest == cnpj else '01_tomado'

def _salvar(cnpj, xml, nsu):
    dt = re.search(r'<dhEmi>([^<]+)|<DataEmissao>([^<]+)|<dhProc>([^<]+)|<competencia>([^<]+)', xml, re.I)
    data = ''.join(g or '' for g in dt.groups()) if dt else ''
    comp = data[:7] if len(data) >= 7 else 'sem_data'
    ch = re.search(r'Id="[A-Za-z]*(\d{40,60})"|<[cC]haveAcesso>(\d{40,60})', xml)
    nome = (ch.group(1) or ch.group(2)) if ch else ('NSU%s' % nsu)
    pasta = os.path.join(SAIDA, cnpj, comp, 'NFSe', sub_nfse(xml, cnpj)); os.makedirs(pasta, exist_ok=True)
    open(os.path.join(pasta, '%s.xml' % nome), 'w', encoding='utf-8').write(xml)

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
    ult = int(emp['ultnsu_nfse'] or 0); total = 0; rounds = 0; parada = 'cap'
    try:
        while rounds < NFSE['max_rounds_por_empresa']:
            rounds += 1
            url = '%s/contribuintes/DFe/%d' % (BASE_URL, ult)
            try:
                r = sess.get(url, cert=cert, timeout=NFSE['timeout_seg'])
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
            if r.status_code != 200: parada = 'http_%d' % r.status_code; break
            try: payload = r.json()
            except ValueError: parada = 'resposta_invalida'; break
            docs = _extrair_docs(payload)
            if not docs: parada = 'fim'; break
            for nsu, xml in docs:
                _salvar(cnpj, xml, nsu); total += 1
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
                  ('nfse', cnpj, emp['nome'], datetime.now().strftime('%Y-%m-%d %H:%M'), total, parada, 'ultNSU=%s' % ult))
    if parada == '429':
        guard.registrar_bloqueio(cnpj, 'nfse', '429', nome=emp['nome'])
    elif parada in ('fim', 'cap') or total:
        guard.registrar_ok(cnpj, 'nfse')
    return total, parada
