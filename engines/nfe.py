# -*- coding: utf-8 -*-
"""Motor NFe - Distribuicao DF-e da SEFAZ (SOAP/mTLS). Portado e validado.
   ENTRADAS: cert de cada empresa. SAIDAS: cert do escritorio via autXML.
"""
import os, re, json, time, base64, gzip, http.client
from datetime import datetime
import models
from engines import certs
from engines import guard
from engines.pausa import NFE_ESPERA, nsu15

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
SAIDA = models.XML_DIR; NFE = CFG['nfe']
HOST = 'www1.nfe.fazenda.gov.br'; PATH = '/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx'
ACTION = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe/nfeDistDFeInteresse'
TPAMB = os.environ.get('FISCAL_TPAMB', '1')  # 1=Producao (default) | 2=Homologacao

def _soap(cuf, cnpj, inner):
    return ('<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
            '<nfeDistDFeInteresse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"><nfeDadosMsg>'
            '<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01"><tpAmb>%s</tpAmb>'
            '<cUFAutor>%s</cUFAutor><CNPJ>%s</CNPJ>%s'
            '</distDFeInt></nfeDadosMsg></nfeDistDFeInteresse></soap12:Body></soap12:Envelope>') % (TPAMB, cuf, cnpj, inner)

def _post(ctx, cuf, cnpj, inner):
    """POST SOAP com retry + backoff em 5xx/rede (nao conta como consumo indevido)."""
    last = None
    for tentativa in range(3):
        try:
            conn = http.client.HTTPSConnection(HOST, 443, context=ctx, timeout=90)
            conn.request('POST', PATH, body=_soap(cuf, cnpj, inner).encode('utf-8'),
                         headers={'Content-Type': 'application/soap+xml; charset=utf-8; action="%s"' % ACTION})
            r = conn.getresponse(); d = r.read().decode('utf-8', 'replace'); conn.close()
            if r.status >= 500 and tentativa < 2:
                time.sleep(2 * (2 ** tentativa))
                last = (r.status, d)
                continue
            return r.status, d
        except (http.client.HTTPException, OSError, TimeoutError) as e:
            last = e
            if tentativa < 2:
                time.sleep(2 * (2 ** tentativa))
                continue
            raise
    if isinstance(last, tuple):
        return last
    raise last

def _campo(b, t):
    m = re.search(r'<%s>([^<]+)</%s>' % (t, t), b); return m.group(1) if m else ''

def _docs_classificar(body, cnpj, eh_escritorio=False):
    """Decodifica os docs do lote e classifica a SUBPASTA certa.
       Para puxar_entradas do CLIENTE:
         <emit> == cnpj                 -> venda propria -> 04_saida
         <emit> != cnpj, <dest> == cnpj -> compra        -> 01_entrada
         senao                           -> resumo/evento (mantem logica do schema)
       Para puxar_saidas_escritorio:
         <emit> == office               -> emissao do escritorio -> 05_propria
         <emit> != office, office in aut -> venda do cliente       -> 04_saida/<emc>
         senao                            -> ignora (nao gravar)
       Retorna lista [(nsu, schema, xml, sub, cnpj_destino)]. cnpj_destino=None significa
       manter o cnpj do cert (caso comum)."""
    if '<docZip' not in body and '&lt;docZip' in body:
        body = body.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    out = []
    for m in re.finditer(r'<(?:[\w]+:)?docZip\s+NSU="(\d+)"\s+schema="([^"]+)">([^<]+)</(?:[\w]+:)?docZip>', body):
        try: xml = gzip.decompress(base64.b64decode(m.group(3))).decode('utf-8', 'replace')
        except Exception: continue
        nsx = m.group(1); sch = m.group(2)
        em = re.search(r'<emit>\s*<CNPJ>(\d+)', xml)
        de = re.search(r'<dest>\s*<CNPJ>(\d+)', xml)
        aut = re.findall(r'<autXML>\s*<CNPJ>(\d+)</CNPJ>', xml)
        emit = em.group(1) if em else None
        dest = de.group(1) if de else None
        if eh_escritorio:
            if not emit:
                continue
            if emit == cnpj:
                out.append((nsx, sch, xml, '05_propria', None))
            elif cnpj in aut:
                out.append((nsx, sch, xml, '04_saida', emit))
            else:
                continue
        else:
            # cliente
            if not ('procNFe' in sch or 'resNFe' in sch or 'procEvento' in sch):
                continue
            if 'procNFe' in sch:
                if not emit or not dest:
                    # nao foi possivel classificar: grava como entrada (comportamento seguro)
                    out.append((nsx, sch, xml, '01_entrada', None))
                elif emit == cnpj:
                    out.append((nsx, sch, xml, '04_saida', None))
                elif dest == cnpj:
                    out.append((nsx, sch, xml, '01_entrada', None))
                else:
                    out.append((nsx, sch, xml, '01_entrada', None))
            elif 'resNFe' in sch:
                out.append((nsx, sch, xml, '02_resumo', None))
            else:
                out.append((nsx, sch, xml, '03_eventos', None))
    return out

def _salvar(cnpj, schema, xml, sub):
    dt = re.search(r'<dhEmi>([^<]+)|<dhEvento>([^<]+)', xml)
    data = (dt.group(1) or dt.group(2) or '') if dt else ''
    comp = data[:7] if len(data) >= 7 else 'sem_data'
    ch = re.search(r'Id="NFe(\d{44})"|<chNFe>(\d{44})</chNFe>', xml)
    nome = (ch.group(1) or ch.group(2)) if ch else 'doc'
    pasta = os.path.join(SAIDA, cnpj, comp, 'NFe', sub); os.makedirs(pasta, exist_ok=True)
    open(os.path.join(pasta, '%s_%s.xml' % (nome, schema.replace('.xsd', ''))), 'w', encoding='utf-8').write(xml)

def puxar_entradas(emp):
    """Compras (destinatario) + eventos via distNSU, com anti-656 da NT 2014.002.

    - Continua do ultNSU gravado (nunca zera).
    - cStat 137 ou ultNSU==maxNSU (fim): cooldown 65 min (oficial = 1h).
    - cStat 656: cooldown 65 min; se a SEFAZ devolver ultNSU, grava (NT 1.14).
    - Teto de lotes (cap): nao e cooldown — o worker retoma esta empresa apos uns segundos.
    """
    cnpj, cuf = emp['cnpj'], emp['cuf'] or '35'
    ult = nsu15(emp['ultnsu_nfe'])
    usou_forcado = False
    if emp['forcar_nsu_nfe'] and (emp['nsu_inicial_forcado'] or '').strip():
        ult = nsu15(emp['nsu_inicial_forcado'])
        usou_forcado = True
        with models.con() as c:
            c.execute('UPDATE empresas SET forcar_nsu_nfe=0 WHERE id=?', (emp['id'],))
        models.registrar_consulta(cnpj, 'forcar_nsu')  # auditoria O-2
    d0 = guard.pode(cnpj, 'distNSU')
    if not d0.liberado:
        if d0.parada == 'limite':
            guard.registrar_bloqueio(cnpj, 'distNSU', 'limite', nome=emp['nome'])
        return 0, d0.parada or 'cooldown'
    if not emp['arquivo']:
        return 0, 'sem_certificado'
    try: ctx, tmp = certs.ssl_ctx(emp['arquivo'], emp['senha'])
    except Exception as e: return 0, 'erro_cert:%s' % str(e)[:40]
    total = 0; lote = 0; parada = 'cap'; maxn = '0'
    try:
        while lote < NFE['max_lotes_por_run']:
            lote += 1
            d = guard.pode(cnpj, 'distNSU')
            if not d.liberado:
                parada = d.parada or 'cooldown'
                if parada == 'limite':
                    guard.registrar_bloqueio(cnpj, 'distNSU', 'limite', nome=emp['nome'])
                break
            models.registrar_consulta(cnpj, 'distNSU')
            st, b = _post(ctx, cuf, cnpj, '<distNSU><ultNSU>%s</ultNSU></distNSU>' % ult)
            if st != 200: parada = 'http_%d' % st; break
            cS = _campo(b, 'cStat'); nu = nsu15(_campo(b, 'ultNSU') or ult); maxn = _campo(b, 'maxNSU') or maxn
            if cS == '138':
                for nsu, schema, xml, sub, cnpj_dest in _docs_classificar(b, cnpj, eh_escritorio=False):
                    _salvar(cnpj_dest or cnpj, schema, xml, sub); total += 1
                ult = nu
                with models.con() as c: c.execute('UPDATE empresas SET ultnsu_nfe=? WHERE id=?', (ult, emp['id']))
                if maxn and int(nu) >= int(maxn): parada = 'fim'; break
            elif cS == '137':
                parada = '137'; break
            elif cS == '656':
                parada = '656'
                if _campo(b, 'ultNSU'):
                    ult = nu
                break
            elif cS in ('108', '109'):
                parada = cS; break
            else:
                parada = 'cStat_%s' % cS; break
            guard.sleep_jitter(NFE_ESPERA)
    finally:
        for f in tmp:
            try: os.remove(f)
            except Exception: pass
    with models.con() as c:
        c.execute('UPDATE empresas SET ultnsu_nfe=?, ultima_exec_nfe=?, total_nfe=total_nfe+? WHERE id=?',
                  (ult, datetime.now().strftime('%Y-%m-%d %H:%M'), total, emp['id']))
        c.execute('INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada,detalhe) VALUES(?,?,?,?,?,?,?)',
                  ('nfe_entrada', cnpj, emp['nome'], datetime.now().strftime('%Y-%m-%d %H:%M'), total, parada,
                   'ultNSU=%s maxNSU=%s%s' % (ult, maxn, ' [forcado]' if usou_forcado else '')))
    if parada in ('137', '656', 'fim', '108', '109'):
        guard.registrar_bloqueio(cnpj, 'distNSU', parada, nome=emp['nome'])
    elif parada in ('cap', 'ok') or total:
        guard.registrar_ok(cnpj, 'distNSU')
    return total, parada

def puxar_saidas_escritorio():
    """Cert do escritorio (parametros office_*) -> vendas de clientes via autXML."""
    office = models.get_param('office_cnpj'); arq = models.get_param('office_arquivo')
    sen = models.get_param('office_senha'); cuf = models.get_param('office_cuf') or '35'
    ult = nsu15(models.get_param('ultnsu_saida') or '000000000000000')
    if not (office and arq and sen): return 0, 'escritorio_nao_configurado'
    d0 = guard.pode(office, 'saida')
    if not d0.liberado:
        return 0, d0.parada or 'cooldown'
    try: ctx, tmp = certs.ssl_ctx(arq, sen)
    except Exception as e: return 0, 'erro_cert:%s' % str(e)[:40]
    total = 0; lote = 0; parada = 'cap'; maxn = '0'
    try:
        while lote < NFE['max_lotes_por_run']:
            lote += 1
            d = guard.pode(office, 'saida')
            if not d.liberado:
                parada = d.parada or 'cooldown'
                if parada == 'limite':
                    guard.registrar_bloqueio(office, 'saida', 'limite', nome='ESCRITORIO')
                break
            models.registrar_consulta(office, 'distNSU')
            st, b = _post(ctx, cuf, office, '<distNSU><ultNSU>%s</ultNSU></distNSU>' % ult)
            if st != 200: parada = 'http_%d' % st; break
            cS = _campo(b, 'cStat'); nu = nsu15(_campo(b, 'ultNSU') or ult); maxn = _campo(b, 'maxNSU') or maxn
            if cS == '138':
                for nsu, schema, xml, sub, cnpj_dest in _docs_classificar(b, office, eh_escritorio=True):
                    _salvar(cnpj_dest or office, schema, xml, sub); total += 1
                ult = nu; models.set_param('ultnsu_saida', ult)
                if maxn and int(nu) >= int(maxn): parada = 'fim'; break
            elif cS == '137':
                parada = '137'; break
            elif cS == '656':
                parada = '656'
                if _campo(b, 'ultNSU'):
                    ult = nu; models.set_param('ultnsu_saida', ult)
                break
            elif cS in ('108', '109'):
                parada = cS; break
            else:
                parada = 'cStat_%s' % cS; break
            guard.sleep_jitter(NFE_ESPERA)
    finally:
        for f in tmp:
            try: os.remove(f)
            except Exception: pass
    if parada in ('137', '656', 'fim', '108', '109'):
        guard.registrar_bloqueio(office, 'saida', parada, nome='ESCRITORIO')
    elif parada in ('cap', 'ok') or total:
        guard.registrar_ok(office, 'saida')
    with models.con() as c:
        c.execute('INSERT INTO execucoes(tipo,cnpj,nome,quando,docs,parada,detalhe) VALUES(?,?,?,?,?,?,?)',
                  ('nfe_saida', office, 'ESCRITORIO', datetime.now().strftime('%Y-%m-%d %H:%M'), total, parada,
                   'ultNSU=%s maxNSU=%s' % (ult, maxn)))
    return total, parada
