# -*- coding: utf-8 -*-
"""Importacao manual de XML de VENDAS (NF-e modelo 55 e NFC-e modelo 65).

Para quando a SEFAZ nao entrega o XML ao portal: NFC-e fora de SP (nao ha web service
de listagem/download como o da SEFAZ-SP, e a Distribuicao DF-e nao cobre o modelo 65)
ou NF-e de saida sem chave na fila. O cliente exporta os XML do proprio emissor/PDV
(e obrigado a guarda-los) e o escritorio sobe aqui um .zip ou os .xml soltos.

Grava no MESMO lugar das notas baixadas da SEFAZ, para Downloads e Analises lerem igual:
  NFC-e : XML/<cnpj>/<AAAA-MM>/NFCe/01_venda/<chave>.xml
  NF-e  : XML/<cnpj>/<AAAA-MM>/NFe/04_saida/<chave>_procNFe_v<versao>.xml
  Eventos (cancelamento, CC-e): .../<NFe|NFCe>/03_eventos/<chave>_<tpEvento>_<seq>.xml
  Nota com cancelamento homologado: .../<NFe|NFCe>/09_cancelada/ (fora do faturamento,
  que so le 04_saida/01_venda; o XML original nao e alterado).

So aceita nota AUTORIZADA (protNFe cStat 100/150) e emitida pela propria empresa.
"""
import io, os, re, zipfile
from lxml import etree
import models

NS = '{http://www.portalfiscal.inf.br/nfe}'
DOC = {'55': 'NFe', '65': 'NFCe'}
SUB_VENDA = {'NFe': '04_saida', 'NFCe': '01_venda'}
SUB_EVENTO = '03_eventos'
SUB_CANCELADA = '09_cancelada'
AUTORIZADA = ('100', '150')
CANCELAMENTO = ('110111', '110112')
EVENTO_OK = ('135', '136', '155')

# Limites contra zip-bomba / upload absurdo
MAX_ARQUIVOS = 50000
MAX_BYTES_TOTAL = 800 * 1024 * 1024
MAX_BYTES_XML = 5 * 1024 * 1024

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False,
                          remove_blank_text=False)


class Resultado:
    def __init__(self):
        self.importadas = {'NFe': 0, 'NFCe': 0}
        self.canceladas = 0      # vendas que foram para 09_cancelada
        self.eventos = 0
        self.repetidas = 0
        self.outro_emitente = 0
        self.nao_autorizadas = 0
        self.invalidos = 0       # nao e XML de NF-e/NFC-e/evento, ou XML quebrado
        self.ignorados = 0       # inutilizacao, resumo, nota de compra etc.
        self.erros = []          # (arquivo, motivo) — so os primeiros, para a tela

    @property
    def total_importadas(self):
        return self.importadas['NFe'] + self.importadas['NFCe']

    def falha(self, nome, motivo, campo='invalidos'):
        setattr(self, campo, getattr(self, campo) + 1)
        if len(self.erros) < 20:
            self.erros.append((nome, motivo))


def _arquivos(uploads):
    """(nome, bytes) de cada .xml — abre .zip (um nivel de .zip dentro de .zip)."""
    total = n = 0

    def _conta(tam):
        nonlocal total, n
        total += tam; n += 1
        if n > MAX_ARQUIVOS or total > MAX_BYTES_TOTAL:
            raise ValueError('Arquivo grande demais (limite %d XML / %d MB).'
                             % (MAX_ARQUIVOS, MAX_BYTES_TOTAL // 1024 // 1024))

    def _do_zip(dados, nivel):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                low = info.filename.lower()
                if low.endswith('.xml'):
                    if info.file_size > MAX_BYTES_XML:
                        yield info.filename, None
                        continue
                    _conta(info.file_size)
                    yield info.filename, z.read(info)
                elif low.endswith('.zip') and nivel == 0:
                    _conta(info.file_size)
                    yield from _do_zip(z.read(info), 1)

    for nome, dados in uploads:
        low = (nome or '').lower()
        if low.endswith('.zip'):
            try:
                yield from _do_zip(dados, 0)
            except zipfile.BadZipFile:
                yield nome, None
        elif low.endswith('.xml'):
            _conta(len(dados))
            yield nome, (dados if len(dados) <= MAX_BYTES_XML else None)


def _txt(el, path):
    r = el.find(path)
    return (r.text or '').strip() if r is not None and r.text else ''


def _classificar(dados):
    """('venda', info) | ('evento', info) | ('ignorar', motivo) | ('invalido', motivo)."""
    try:
        raiz = etree.fromstring(dados, _PARSER)
    except etree.XMLSyntaxError:
        return 'invalido', 'XML quebrado'
    tag = etree.QName(raiz).localname
    if tag in ('nfeProc', 'NFe'):
        inf = raiz.find('.//%sinfNFe' % NS)
        if inf is None:
            return 'invalido', 'sem infNFe'
        chave = (inf.get('Id') or '')[3:]
        prot = raiz.find('.//%sprotNFe/%sinfProt' % (NS, NS))
        return 'venda', {
            'chave': chave,
            'mod': _txt(inf, '%side/%smod' % (NS, NS)),
            'dhEmi': _txt(inf, '%side/%sdhEmi' % (NS, NS)) or _txt(inf, '%side/%sdEmi' % (NS, NS)),
            'emit': _txt(inf, '%semit/%sCNPJ' % (NS, NS)) or _txt(inf, '%semit/%sCPF' % (NS, NS)),
            'cStat': _txt(prot, '%scStat' % NS) if prot is not None else '',
            'versao': raiz.get('versao') or inf.get('versao') or '4.00',
        }
    if tag == 'procEventoNFe':
        inf = raiz.find('.//%sevento/%sinfEvento' % (NS, NS))
        ret = raiz.find('.//%sretEvento/%sinfEvento' % (NS, NS))
        if inf is None:
            return 'invalido', 'evento sem infEvento'
        return 'evento', {
            'chave': _txt(inf, '%schNFe' % NS),
            'tp': _txt(inf, '%stpEvento' % NS),
            'seq': _txt(inf, '%snSeqEvento' % NS) or '1',
            'dh': _txt(inf, '%sdhEvento' % NS),
            'cStat': _txt(ret, '%scStat' % NS) if ret is not None else '',
        }
    if tag in ('procInutNFe', 'inutNFe', 'resNFe', 'resEvento', 'retConsSitNFe'):
        return 'ignorar', tag
    return 'invalido', 'não é NF-e/NFC-e (%s)' % tag


def _comp(dh):
    return dh[:7] if re.match(r'\d{4}-\d{2}', dh or '') else 'sem_data'


def _existentes(base_cnpj, doc):
    """{chave: caminho} das vendas ja gravadas (01_venda/04_saida/09_cancelada, qualquer mes)."""
    achou = {}
    if not os.path.isdir(base_cnpj):
        return achou
    for comp in os.listdir(base_cnpj):
        for sub in (SUB_VENDA[doc], SUB_CANCELADA):
            d = os.path.join(base_cnpj, comp, doc, sub)
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f[:44].isdigit() and f.lower().endswith('.xml'):
                        achou[f[:44]] = os.path.join(d, f)
    return achou


def _cancelamentos_em_disco(base_cnpj, doc):
    """Chaves com evento de cancelamento ja gravado em 03_eventos."""
    ch = set()
    if not os.path.isdir(base_cnpj):
        return ch
    for comp in os.listdir(base_cnpj):
        d = os.path.join(base_cnpj, comp, doc, SUB_EVENTO)
        if os.path.isdir(d):
            for f in os.listdir(d):
                partes = f.split('_')
                if len(partes) >= 2 and partes[0].isdigit() and partes[1] in CANCELAMENTO:
                    ch.add(partes[0])
    return ch


def importar(cnpj, uploads, pasta_xml):
    """Importa XML de vendas de `cnpj`. uploads = [(nome_arquivo, bytes)]. Retorna Resultado."""
    r = Resultado()
    base = os.path.join(pasta_xml, cnpj)
    vendas, eventos = [], []
    for nome, dados in _arquivos(uploads):
        if dados is None:
            r.falha(nome, 'arquivo grande demais ou .zip corrompido'); continue
        tipo, info = _classificar(dados)
        if tipo == 'invalido':
            r.falha(nome, info); continue
        if tipo == 'ignorar':
            r.ignorados += 1; continue
        chave = info['chave']
        if not (len(chave) == 44 and chave.isdigit()):
            r.falha(nome, 'chave de acesso ausente'); continue
        doc = DOC.get(chave[20:22])
        if not doc:
            r.falha(nome, 'modelo %s não é NF-e (55) nem NFC-e (65)' % chave[20:22]); continue
        if tipo == 'venda':
            if info['emit'] != cnpj:
                # NF-e de compra (emitente e o fornecedor) ou nota de outra empresa
                r.falha(nome, 'emitente %s não é esta empresa' % (info['emit'] or '?'), 'outro_emitente')
                continue
            if info['cStat'] not in AUTORIZADA:
                r.falha(nome, 'nota sem autorização (cStat %s)' % (info['cStat'] or 'sem protocolo'),
                        'nao_autorizadas')
                continue
            vendas.append((nome, doc, info, dados))
        else:
            if chave[6:20] != cnpj:
                r.falha(nome, 'evento de nota de outro emitente', 'outro_emitente'); continue
            if info['cStat'] not in EVENTO_OK:
                r.falha(nome, 'evento não homologado (cStat %s)' % (info['cStat'] or '?'), 'nao_autorizadas')
                continue
            eventos.append((nome, doc, info, dados))

    # 1) Eventos primeiro: um cancelamento no mesmo lote ja manda a venda para 09_cancelada.
    cancelar = {'NFe': set(), 'NFCe': set()}
    for _nome, doc, info, dados in eventos:
        pasta = os.path.join(base, _comp(info['dh']), doc, SUB_EVENTO)
        arq = os.path.join(pasta, '%s_%s_%s.xml' % (info['chave'], info['tp'], info['seq']))
        if os.path.exists(arq):
            r.repetidas += 1
        else:
            os.makedirs(pasta, exist_ok=True)
            with open(arq, 'wb') as f:
                f.write(dados)
            r.eventos += 1
        if info['tp'] in CANCELAMENTO:
            cancelar[doc].add(info['chave'])

    # 2) Vendas
    for doc in ('NFe', 'NFCe'):
        lote = [v for v in vendas if v[1] == doc]
        if not lote and not cancelar[doc]:
            continue
        existentes = _existentes(base, doc)
        canceladas = cancelar[doc] | _cancelamentos_em_disco(base, doc)
        for _nome, _doc, info, dados in lote:
            ch = info['chave']
            if ch in existentes:
                r.repetidas += 1; continue
            sub = SUB_CANCELADA if ch in canceladas else SUB_VENDA[doc]
            pasta = os.path.join(base, _comp(info['dhEmi']), doc, sub)
            os.makedirs(pasta, exist_ok=True)
            nome_arq = ('%s.xml' % ch) if doc == 'NFCe' else ('%s_procNFe_v%s.xml' % (ch, info['versao']))
            caminho = os.path.join(pasta, nome_arq)
            with open(caminho, 'wb') as f:
                f.write(dados)
            existentes[ch] = caminho
            r.importadas[doc] += 1
            if sub == SUB_CANCELADA:
                r.canceladas += 1
            if doc == 'NFe':
                _baixar_da_fila(ch)
        # 3) Cancelamento que chegou agora para venda que ja estava gravada como valida
        for ch in cancelar[doc]:
            atual = existentes.get(ch)
            if atual and os.path.basename(os.path.dirname(atual)) == SUB_VENDA[doc]:
                destino = os.path.join(os.path.dirname(os.path.dirname(atual)), SUB_CANCELADA)
                os.makedirs(destino, exist_ok=True)
                os.replace(atual, os.path.join(destino, os.path.basename(atual)))
                r.canceladas += 1
    return r


def _baixar_da_fila(chave):
    """Se a chave estava na fila de NF-e de saida, ja temos o XML: nao consulta a SEFAZ."""
    with models.con() as c:
        c.execute("UPDATE chaves_saida SET status='baixada', detalhe='importada (XML do emissor)' "
                  "WHERE chave=? AND status!='baixada'", (chave,))
