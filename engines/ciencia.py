# -*- coding: utf-8 -*-
"""Ciência da Operação (evento 210210) — manifestação do destinatário.
   Libera o XML COMPLETO (procNFe) das compras que vieram só como resumo.
   Exige ASSINATURA XMLDSig (RSA-SHA1 + C14N inclusiva) — feita à mão com lxml.
   Endpoint AN: NFeRecepcaoEvento4.
"""
import os, re, json, time, ssl, http.client, base64, hashlib
from datetime import datetime, timezone, timedelta
from lxml import etree
import xmlsec
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
import models
from engines import certs

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.environ.get('FISCAL_XML_DIR') or json.load(open(os.path.join(_BASE, 'config.json'), encoding='utf-8'))['pasta_saida_xml']

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
NS_SIG = 'http://www.w3.org/2000/09/xmldsig#'
HOST = 'www.nfe.fazenda.gov.br'
PATH = '/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx'
ACTION = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4/nfeRecepcaoEventoNF'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
EXC = 'http://www.w3.org/2001/10/xml-exc-c14n#'
TPAMB = int(os.environ.get('FISCAL_TPAMB', '1'))  # 1=Producao (default) | 2=Homologacao

def _c14n(el):
    # NF-e exige C14N INCLUSIVA (schema). Assinatura valida offline; resta acertar
    # a canonicalizacao no contexto SOAP da SEFAZ (ver HANDOFF - Ciencia P1).
    return etree.tostring(el, method='c14n', exclusive=False, with_comments=False)

def _agora():
    tz = timezone(timedelta(hours=-3))
    return datetime.now(tz).strftime('%Y-%m-%dT%H:%M:%S-03:00')

def montar_evento(cnpj, chNFe, nSeq=1, tpAmb=TPAMB):
    seq = str(nSeq)
    eid = 'ID210210%s%02d' % (chNFe, nSeq)
    E = '{%s}' % NS_NFE
    evento = etree.Element(E + 'evento', versao='1.00', nsmap={None: NS_NFE})
    inf = etree.SubElement(evento, E + 'infEvento', Id=eid)
    def sub(p, t, v): x = etree.SubElement(p, E + t); x.text = v; return x
    sub(inf, 'cOrgao', '91')
    sub(inf, 'tpAmb', str(tpAmb))
    sub(inf, 'CNPJ', cnpj)
    sub(inf, 'chNFe', chNFe)
    sub(inf, 'dhEvento', _agora())
    sub(inf, 'tpEvento', '210210')
    sub(inf, 'nSeqEvento', seq)
    sub(inf, 'verEvento', '1.00')
    det = etree.SubElement(inf, E + 'detEvento', versao='1.00')
    sub(det, 'descEvento', 'Ciencia da Operacao')
    return evento, eid

def assinar(evento, eid, priv_key, cert):
    """Assina o infEvento com XMLDSig via xmlsec (libxmlsec1) — RSA-SHA1 + C14N
    inclusiva, byte-compatível com o .NET SignedXml da SEFAZ (resolve o cStat 297)."""
    key_pem = priv_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    cert_pem = cert.public_bytes(Encoding.PEM)
    xmlsec.tree.add_ids(evento, ['Id'])
    sig = xmlsec.template.create(evento, xmlsec.Transform.C14N, xmlsec.Transform.RSA_SHA1)
    evento.append(sig)
    ref = xmlsec.template.add_reference(sig, xmlsec.Transform.SHA1, uri='#' + eid)
    xmlsec.template.add_transform(ref, xmlsec.Transform.ENVELOPED)
    xmlsec.template.add_transform(ref, xmlsec.Transform.C14N)
    ki = xmlsec.template.ensure_key_info(sig)
    xmlsec.template.add_x509_data(ki)
    ctx = xmlsec.SignatureContext()
    skey = xmlsec.Key.from_memory(key_pem, xmlsec.KeyFormat.PEM, None)
    skey.load_cert_from_memory(cert_pem, xmlsec.KeyFormat.CERT_PEM)
    ctx.key = skey
    ctx.sign(sig)
    return evento

def manifestar(cert_path, senha, cnpj, chNFe, nSeq=1, tpAmb=TPAMB):
    """Assina e envia a Ciência 210210. Retorna (cStat, xMotivo, xml_resposta)."""
    key, cert, _ = certs.load_pfx(cert_path, senha)
    evento, eid = montar_evento(cnpj, chNFe, nSeq, tpAmb)
    assinar(evento, eid, key, cert)
    ev_xml = etree.tostring(evento, encoding='unicode')
    env = ('<envEvento versao="1.00" xmlns="%s"><idLote>1</idLote>%s</envEvento>' % (NS_NFE, ev_xml))
    soap = ('<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope"><soap12:Body>'
            '<nfeDadosMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4">%s</nfeDadosMsg>'
            '</soap12:Body></soap12:Envelope>' % env)
    ctx, tmp = certs.ssl_ctx(cert_path, senha, verificar_servidor=False)
    try:
        conn = http.client.HTTPSConnection(HOST, 443, context=ctx, timeout=60)
        conn.request('POST', PATH, body=soap.encode('utf-8'),
                     headers={'Content-Type': 'application/soap+xml; charset=utf-8; action="%s"' % ACTION})
        r = conn.getresponse(); data = r.read().decode('utf-8', 'replace'); conn.close()
    finally:
        import os
        for f in tmp:
            try: os.remove(f)
            except Exception: pass
    import re
    cst = re.findall(r'<cStat>(\d+)</cStat>', data)
    xmot = re.findall(r'<xMotivo>([^<]+)</xMotivo>', data)
    # o cStat do evento (registrado) costuma ser o ultimo
    cStat = cst[-1] if cst else '?'
    xMot = xmot[-1] if xmot else ''
    return cStat, xMot, data

def _chaves_pasta(cnpj, sub):
    """Chaves (44) dos XML salvos em XML/<cnpj>/*/NFe/<sub>/ (pelo nome do arquivo)."""
    chaves = set()
    base = os.path.join(SAIDA, cnpj)
    if not os.path.isdir(base):
        return chaves
    for comp in os.listdir(base):
        d = os.path.join(base, comp, 'NFe', sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            m = f[:44]
            if m.isdigit():
                chaves.add(m)
    return chaves

def dar_ciencia_pendentes(emp, limite=None, sleep_seg=None):
    """Manifesta Ciencia 210210 nos resumos (resNFe) que ainda nao viraram
    procNFe completo e que ainda nao tiveram ciencia (dedup em ciencia_dada).
    A varredura seguinte (retomada ou diario) traz o XML completo dessas notas."""
    from engines.pausa import CIENCIA_ESPERA, CIENCIA_LIMITE
    from engines import guard
    cnpj = emp['cnpj']
    d0 = guard.pode(cnpj, 'ciencia')
    if not d0.liberado:
        return 0, d0.parada or 'cooldown'
    if not emp['arquivo']:
        return 0, 'sem_certificado'
    if limite is None:
        limite = CIENCIA_LIMITE
    if sleep_seg is None:
        sleep_seg = CIENCIA_ESPERA
    resumos = _chaves_pasta(cnpj, '02_resumo')
    completos = _chaves_pasta(cnpj, '01_entrada')
    pendentes = [ch for ch in (resumos - completos) if not models.ciencia_ja(cnpj, ch)]
    n = 0
    parada = 'ok'
    for ch in pendentes[:limite]:
        nProt = None
        try:
            cStat, xMot, raw = manifestar(emp['arquivo'], emp['senha'], cnpj, ch)
            m = re.search(r'<nProt>(\d+)</nProt>', raw)
            if m:
                nProt = m.group(1)
        except Exception as e:
            cStat = 'erro:%s' % str(e)[:30]
        if cStat in ('135', '573'):
            models.ciencia_registrar(cnpj, ch, cStat, nProt)
            n += 1
        elif cStat == '656':
            parada = '656'
            guard.registrar_bloqueio(cnpj, 'ciencia', '656', xMot or '', nome=emp['nome'])
            break
        elif cStat in ('108', '109'):
            parada = cStat
            guard.registrar_bloqueio(cnpj, 'ciencia', cStat, nome=emp['nome'])
            break
        guard.sleep_jitter(sleep_seg)
    else:
        if len(pendentes) > limite:
            parada = 'cap'
        elif n:
            guard.registrar_ok(cnpj, 'ciencia')
    return n, parada

if __name__ == '__main__':
    import sys
    cert_path, senha, cnpj, chNFe = sys.argv[1:5]
    print(manifestar(cert_path, senha, cnpj, chNFe)[:2])
