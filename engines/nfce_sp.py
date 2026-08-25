# -*- coding: utf-8 -*-
"""Motor NFC-e SP (modelo 65 - vendas de varejo/consumidor).
   Web services ESTADUAIS da SEFAZ-SP (mTLS com o MESMO certificado A1):
     - NFCeListagemChaves.asmx : lista as chaves emitidas num PERIODO (por data).
     - NFCeDownloadXML.asmx     : baixa o XML (nfeProc) de 1 chave (44 digitos).
   Diferente da NF-e (NSU): aqui e por periodo, entao da p/ puxar meses historicos.
   Anti-consumo: cStat 101 -> subdivide o periodo recursivamente.
   Cadencia por IP (servidor): ~1.5s entre downloads; janela max. 100 dias (SAE).
"""
import os, re, json
from datetime import datetime, timedelta
from lxml import etree
import requests, urllib3
import models
from engines import certs
from engines.pausa import (
    NFCE_ESPERA, NFCE_LISTAGEM, NFCE_COOLDOWN_MIN, cooldown_ate, janela_nfce, pausa_nfce,
)
from engines import guard

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(_BASE, 'config.json'), encoding='utf-8'))
SAIDA = os.environ.get('FISCAL_XML_DIR') or CFG['pasta_saida_xml']

URL_LISTAGEM = "https://nfce.fazenda.sp.gov.br/ws/NFCeListagemChaves.asmx"
URL_DOWNLOAD = "https://nfce.fazenda.sp.gov.br/ws/NFCeDownloadXML.asmx"
SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
WSDL_LIST = "http://www.portalfiscal.inf.br/nfe/wsdl/NFCeListagemChaves"
WSDL_DOWN = "http://www.portalfiscal.inf.br/nfe/wsdl/NFCeDownloadXML"
NFE = "http://www.portalfiscal.inf.br/nfe"
NS = {"soap": SOAP12, "nfe": NFE}
VERSAO = "1.00"; TPAMB = int(os.environ.get('FISCAL_TPAMB', '1')); TIMEOUT = 40

class ConsumoIndevido(Exception):
    """SEFAZ-SP recusou por volume/consumo. Worker retoma apos cooldown."""
    def __init__(self, cstat, xmot=''):
        self.cstat = int(cstat) if str(cstat).isdigit() else 656
        self.xmot = xmot or ''
        Exception.__init__(self, 'cStat=%s %s' % (cstat, xmot))

def _xmlb(el):
    return etree.tostring(el, encoding="utf-8", xml_declaration=True)

def _inner_listagem(dt_ini, dt_fim):
    r = etree.Element("nfceListagemChaves", nsmap={None: NFE}, versao=VERSAO)
    etree.SubElement(r, "tpAmb").text = str(TPAMB)
    etree.SubElement(r, "dataHoraInicial").text = dt_ini.strftime("%Y-%m-%dT%H:%M")
    if dt_fim is not None:
        etree.SubElement(r, "dataHoraFinal").text = dt_fim.strftime("%Y-%m-%dT%H:%M")
    return _xmlb(r)

def _inner_download(chave):
    r = etree.Element("nfceDownloadXML", nsmap={None: NFE}, versao=VERSAO)
    etree.SubElement(r, "tpAmb").text = str(TPAMB)
    etree.SubElement(r, "chNFCe").text = chave
    return _xmlb(r)

def _envelope(wsdl_ns, inner):
    env = etree.Element(f"{{{SOAP12}}}Envelope", nsmap={"soap12": SOAP12})
    body = etree.SubElement(env, f"{{{SOAP12}}}Body")
    msg = etree.SubElement(body, f"{{{wsdl_ns}}}nfeDadosMsg")
    msg.append(etree.fromstring(inner))
    return _xmlb(env)

def _post(sess, url, env):
    r = sess.post(url, data=env, timeout=TIMEOUT,
                  headers={"Content-Type": 'application/soap+xml; charset=utf-8; action=""'})
    if r.status_code == 429:
        raise ConsumoIndevido(429, 'HTTP 429')
    r.raise_for_status()
    return etree.fromstring(r.content)

def _ft(root, xp):
    n = root.find(xp, namespaces=NS)
    return n.text if n is not None else None

def _dh_corte(root):
    """SAE documenta dhEmisUltNfce; o motor antigo lia dhUltimaReceita."""
    return (_ft(root, ".//nfe:retNfceListagemChaves/nfe:dhEmisUltNfce")
            or _ft(root, ".//nfe:retNfceListagemChaves/nfe:dhUltimaReceita"))

def _listar_raw(sess, dt_ini, dt_fim):
    pausa_nfce(NFCE_LISTAGEM)
    root = _post(sess, URL_LISTAGEM, _envelope(WSDL_LIST, _inner_listagem(dt_ini, dt_fim)))
    cstat = int(_ft(root, ".//nfe:retNfceListagemChaves/nfe:cStat") or "0")
    xmot = _ft(root, ".//nfe:retNfceListagemChaves/nfe:xMotivo") or ""
    chaves = [n.text for n in root.findall(".//nfe:retNfceListagemChaves/nfe:chNFCe", namespaces=NS) if n.text]
    dh = _dh_corte(root)
    return cstat, xmot, chaves, dh

def listar_chaves(sess, dt_ini, dt_fim, split_min=1440):
    """Lista chaves NFC-e no periodo, subdividindo se cStat 101 (lista incompleta)."""
    total = []
    def collect(ini, fim):
        cstat, xmot, chaves, dh = _listar_raw(sess, ini, fim)
        if cstat in (656, 429):
            raise ConsumoIndevido(cstat, xmot)
        if cstat in (100, 107):
            total.extend(chaves); return
        if cstat == 101:
            end = fim or datetime.now()
            cut = None
            if dh:
                try: cut = datetime.fromisoformat(dh.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception: cut = None
            mid = cut if (cut and ini < cut < end) else ini + (end - ini) / 2
            if (end - ini) <= timedelta(minutes=split_min):
                step = timedelta(minutes=max(5, split_min // 24)); cur = ini
                while cur < end:
                    nxt = min(cur + step, end); collect(cur, nxt); cur = nxt
                return
            collect(ini, mid); collect(mid, end); return
        raise RuntimeError("listagem cStat=%s %s" % (cstat, xmot))
    collect(dt_ini, dt_fim)
    seen = set(); out = []
    for c in total:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out

def baixar_xml(sess, chave):
    """Baixa o nfeProc de 1 chave. Retorna (bytes|None, cstat)."""
    pausa_nfce(NFCE_ESPERA)
    root = _post(sess, URL_DOWNLOAD, _envelope(WSDL_DOWN, _inner_download(chave)))
    cstat = int(_ft(root, ".//nfe:retNfceDownloadXML/nfe:cStat") or "0")
    xmot = _ft(root, ".//nfe:retNfceDownloadXML/nfe:xMotivo") or ""
    if cstat in (656, 429):
        raise ConsumoIndevido(cstat, xmot)
    if cstat != 200:
        return None, cstat
    proc = root.find(".//nfe:retNfceDownloadXML/nfe:proc/nfe:nfeProc", namespaces=NS)
    return (_xmlb(proc) if proc is not None else None), cstat

def sessao(cert_path, senha):
    """requests.Session com mTLS (nosso certs.pem_temp) + verify=False (cadeia SP)."""
    leaf, keyf = certs.pem_temp(cert_path, senha)
    s = requests.Session(); s.cert = (leaf, keyf); s.verify = False
    s.headers.update({"User-Agent": "PortalFiscalNescon/NFCe-SP"})
    return s, (leaf, keyf)

def _comp_de(xml_bytes):
    """Competencia (AAAA-MM) da nota, pelo dhEmi."""
    m = re.search(r'<dhEmi>([^<]+)', xml_bytes.decode('utf-8', 'ignore'))
    return m.group(1)[:7] if (m and len(m.group(1)) >= 7) else 'sem_data'

def _baixados(cnpj):
    """Chaves NFC-e ja baixadas (todas as competencias) de um CNPJ."""
    base = os.path.join(SAIDA, cnpj); ch = set()
    if os.path.isdir(base):
        for comp in os.listdir(base):
            d = os.path.join(base, comp, 'NFCe')
            if os.path.isdir(d):
                for sub in os.listdir(d):
                    sd = os.path.join(d, sub)
                    if os.path.isdir(sd):
                        for f in os.listdir(sd):
                            if f[:44].isdigit():
                                ch.add(f[:44])
    return ch

def puxar_nfce(emp, limite=None):
    """Puxa as NFC-e (vendas) de uma empresa SP. Periodo (data inicial ate agora) e
    limite por rodada vem dos parametros nfce_data_inicial / nfce_limite (editaveis em
    Configuracoes). Salva cada nota na sua competencia (dhEmi). Idempotente.
    parada=limite/656/429 -> o worker retoma esta empresa apos a pausa."""
    cnpj = emp['cnpj']
    bloq_ate = emp['bloqueado_nfce_ate'] if 'bloqueado_nfce_ate' in emp.keys() else None
    d0 = guard.pode(cnpj, 'nfce')
    if not d0.liberado:
        return 0, d0.parada or 'cooldown'
    if (emp['uf'] or '').upper() not in ('', 'SP'):
        return 0, 'uf_nao_sp'
    if not emp['arquivo']:
        return 0, 'sem_certificado'
    di = models.get_param('nfce_data_inicial') or datetime.now().strftime('%Y-%m-01')
    limite = int(limite if limite is not None else (models.get_param('nfce_limite') or 500))
    try:
        dt_ini = datetime.strptime(di[:10], '%Y-%m-%d')
    except Exception:
        dt_ini = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    dt_ini, dt_fim = janela_nfce(dt_ini, datetime.now())
    try:
        sess, tmp = sessao(emp['arquivo'], emp['senha'])
    except Exception as e:
        return 0, 'erro_cert:%s' % str(e)[:40]
    total = 0; parada = 'fim'; n_chaves = 0; bloq = None
    try:
        chaves = listar_chaves(sess, dt_ini, dt_fim); n_chaves = len(chaves)
        ja = _baixados(cnpj)
        pend = [c for c in chaves if c not in ja]
        if len(pend) > limite:
            pend = pend[:limite]; parada = 'limite'
        for ch in pend:
            xml, cstat = baixar_xml(sess, ch)
            if xml:
                pasta = os.path.join(SAIDA, cnpj, _comp_de(xml), 'NFCe', '01_venda')
                os.makedirs(pasta, exist_ok=True)
                open(os.path.join(pasta, ch + '.xml'), 'wb').write(xml); total += 1
            elif cstat in (108, 109):
                parada = str(cstat); bloq = cooldown_ate(NFCE_COOLDOWN_MIN)
                guard.registrar_bloqueio(cnpj, 'nfce', str(cstat), nome=emp['nome']); break
    except ConsumoIndevido as e:
        parada = '656' if e.cstat == 656 else str(e.cstat)
        bloq = cooldown_ate(NFCE_COOLDOWN_MIN)
        guard.registrar_bloqueio(cnpj, 'nfce', parada, e.xmot, nome=emp['nome'])
    except Exception as e:
        parada = 'erro:%s' % str(e)[:60]
    finally:
        for f in tmp:
            try: os.remove(f)
            except Exception: pass
    if parada in ('fim', 'limite') and total:
        guard.registrar_ok(cnpj, 'nfce')
    with models.con() as c:
        c.execute('UPDATE empresas SET total_nfce=COALESCE(total_nfce,0)+?, ultima_exec_nfce=? WHERE id=?',
                  (total, datetime.now().strftime('%Y-%m-%d %H:%M'), emp['id']))
        c.execute('INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada,detalhe) VALUES(?,?,?,?,?,?,?)',
                  ('nfce', cnpj, emp['nome'], datetime.now().strftime('%Y-%m-%d %H:%M'),
                   total, parada, 'desde %s | chaves=%d | janela<=%dd' % (
                       dt_ini.strftime('%Y-%m-%d'), n_chaves, max(1, (dt_fim - dt_ini).days))))
    return total, parada
