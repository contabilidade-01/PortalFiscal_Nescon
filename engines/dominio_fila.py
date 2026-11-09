# -*- coding: utf-8 -*-
"""Fila de envio de XML para a Dominio (API Onvio), direto do servidor.

Fluxo:  pastas XML  ->  varrer()  ->  dominio_envios (catalogo)  ->  processar()  ->  API
                                                                ->  consultar() <- lote
Regras (herdadas do EXE CentralNFSe, que ja fazia isto pelo gateway):
  - 1 linha por XML; a nota e "reservada" (ENVIANDO) antes do envio -> sem duplicar.
  - Cancelamento so sai depois que a nota original foi aceita (ou se ela nunca entra).
  - XML alterado depois do 1o envio = RECUSADO (confere antes de reenviar).
  - Conexao caiu no meio do envio = INCERTO: NUNCA reenvia sozinho.
  - 401/403 = PAUSADO para a empresa; 429/5xx = REPETIR com espera crescente.
Ligado so com DOMINIO_ENVIO_ATIVO=1 (env) + chave "Envio ligado" na tela.
"""
import base64, hashlib, os, random, re, threading, time, uuid
from datetime import datetime, timedelta

import models
from engines import dominio_api as api

ESTADOS = ('NOVO', 'ENVIANDO', 'ENVIADO', 'CONFIRMADO', 'JA_EXISTENTE', 'RECUSADO',
           'PAUSADO', 'REPETIR', 'INCERTO', 'FORA_PERIODO')
ACEITOS = ('CONFIRMADO', 'JA_EXISTENTE')
ROTULOS = {
    'NOVO': 'Na fila', 'ENVIANDO': 'Enviando', 'ENVIADO': 'Aguardando Domínio',
    'CONFIRMADO': 'Confirmado', 'JA_EXISTENTE': 'Já existia', 'RECUSADO': 'Recusado',
    'PAUSADO': 'Pausado', 'REPETIR': 'Nova tentativa', 'INCERTO': 'Conferir na Domínio',
    'FORA_PERIODO': 'Fora do período',
}

# Pastas que entram (doc -> subpastas). 02_resumo (resumo de NF-e) nunca: nao e o XML da nota.
NOTAS = {
    'NFe': ('01_entrada', '04_saida', '09_cancelada'),
    'NFSe': ('01_tomado', '02_prestado'),
    'NFCe': ('01_venda', '09_cancelada'),
}
EVENTOS = '03_eventos'
# Eventos que interessam: cancelamento (NF-e/NFC-e 110111/110112; NFS-e 101101/105102).
RE_CANCEL_NFE = re.compile(rb'<tpEvento>\s*11011[12]\s*</tpEvento>')
RE_CANCEL_NFSE = re.compile(rb'(?:<e101101\b|<e105102\b|<tpEvento>\s*10(?:1101|5102)\s*<)')
RE_EMISSAO = re.compile(rb'<(?:dhEmi|dhEvento|DataEmissao|dCompet|dhProc)>\s*(\d{4}-\d{2}-\d{2})')
RE_CHAVE = re.compile(r'(\d{44,50})')
RE_CHAVE_XML = re.compile(rb'<(?:chNFe|chNFSe|chaveAcesso|ChaveAcesso)>\s*(\d{44,50})|Id="[A-Za-z]*(\d{44,50})')

MAX_TENTATIVAS = 8
MAX_CONSULTAS = 40          # ~ algumas horas sem codigo conhecido -> INCERTO
TRAVADO_MIN = 10            # ENVIANDO ha mais que isso no boot = processo morreu no meio


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _depois(seg):
    return (datetime.now() + timedelta(seconds=int(seg))).strftime('%Y-%m-%d %H:%M:%S')


def _saida():
    from engines import nfse  # mesmo diretorio de XML dos motores
    return nfse.SAIDA


# ---------------------------------------------------------------- cofre das chaves
def _fernet():
    k = os.environ.get('DOMINIO_KEY', '')
    if not k:
        return None
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('dominio-v1:' + k).encode()).digest()))


def cofre_ok():
    return _fernet() is not None


def cifrar(chave):
    f = _fernet()
    if not f:
        raise ValueError('Defina DOMINIO_KEY no servidor antes de cadastrar chaves da Domínio.')
    return f.encrypt(chave.strip().encode()).decode('ascii')


def decifrar(valor):
    if not valor:
        return ''
    f = _fernet()
    if not f:
        raise api.DominioPausar('DOMINIO_KEY ausente no servidor: as chaves não podem ser abertas.')
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(valor.encode('ascii')).decode()
    except (InvalidToken, ValueError):
        raise api.DominioPausar('Chave da empresa não abre com a DOMINIO_KEY atual. Cadastre de novo.') from None


# ---------------------------------------------------------------- parametros
def envio_ligado():
    return os.environ.get('DOMINIO_ENVIO_ATIVO', '0') == '1' and models.get_param('dominio_envio_ligado') == '1'


def salvar_empresa(cnpj, ativo=None, data_inicial=None, chave=None, limpar_chave=False):
    cnpj = re.sub(r'\D', '', cnpj or '')
    if len(cnpj) not in (11, 14):
        raise ValueError('CNPJ inválido.')
    if data_inicial not in (None, ''):
        datetime.strptime(data_inicial, '%Y-%m-%d')
    with models.con() as c:
        if not c.execute('SELECT 1 FROM empresas WHERE cnpj=?', (cnpj,)).fetchone():
            raise ValueError('CNPJ %s não está na base de clientes.' % cnpj)
        row = c.execute('SELECT * FROM dominio_empresa WHERE cnpj=?', (cnpj,)).fetchone()
        atual = dict(row) if row else {'ativo': 0, 'data_inicial': None, 'chave_cifrada': ''}
        if ativo is not None:
            atual['ativo'] = 1 if ativo else 0
        if data_inicial is not None:
            atual['data_inicial'] = data_inicial or None
        if chave:
            if len(chave) > 4096:
                raise ValueError('Chave da Domínio inválida.')
            atual['chave_cifrada'] = cifrar(chave)
        if limpar_chave:
            atual['chave_cifrada'] = ''
        if atual['ativo'] and not (atual['chave_cifrada'] and atual['data_inicial']):
            raise ValueError('Para ligar o envio da empresa informe a chave e a data inicial.')
        c.execute('INSERT INTO dominio_empresa(cnpj,ativo,data_inicial,chave_cifrada,atualizado) '
                  'VALUES(?,?,?,?,?) ON CONFLICT(cnpj) DO UPDATE SET ativo=excluded.ativo, '
                  'data_inicial=excluded.data_inicial, chave_cifrada=excluded.chave_cifrada, '
                  'atualizado=excluded.atualizado',
                  (cnpj, atual['ativo'], atual['data_inicial'], atual['chave_cifrada'], agora()))
        if chave or limpar_chave:  # chave trocada: a chave de envio antiga nao vale mais
            c.execute("UPDATE dominio_empresa SET chave_envio_cifrada='', chave_info=NULL WHERE cnpj=?", (cnpj,))
        if chave:  # chave nova: libera o que estava pausado por credencial
            c.execute("UPDATE dominio_envios SET estado='REPETIR', proximo=?, mensagem='' "
                      "WHERE cnpj=? AND estado='PAUSADO'", (agora(), cnpj))


def importar_chaves(texto, data_inicial=None, ligar=False):
    """Linhas 'CNPJ;chave' (ou ',' / tab). Retorna (gravadas, erros[])."""
    ok, erros = 0, []
    for n, linha in enumerate((texto or '').splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.lower().startswith('cnpj'):
            continue
        partes = [p.strip() for p in re.split(r'[;,\t]', linha) if p.strip()]
        if len(partes) < 2:
            erros.append('Linha %d: use CNPJ;chave.' % n)
            continue
        try:
            salvar_empresa(partes[0], chave=partes[1],
                           data_inicial=(partes[2] if len(partes) > 2 else data_inicial),
                           ativo=True if ligar else None)
            ok += 1
        except ValueError as e:
            erros.append('Linha %d: %s' % (n, e))
    return ok, erros


# ---------------------------------------------------------------- varredura
def _ler_meta(caminho):
    """(emissao AAAA-MM-DD|None, chave do XML|None, e_cancelamento) lendo o inicio do arquivo."""
    with open(caminho, 'rb') as f:
        dados = f.read(200000)
    m = RE_EMISSAO.search(dados)
    ch = RE_CHAVE_XML.search(dados)
    chave = (ch.group(1) or ch.group(2)).decode() if ch else None
    cancel = bool(RE_CANCEL_NFE.search(dados) or RE_CANCEL_NFSE.search(dados))
    return (m.group(1).decode() if m else None), chave, cancel


def varrer(cnpj=None):
    """Cataloga XMLs novos das empresas com envio ativo. Retorna quantos entraram."""
    base_xml = _saida()
    with models.con() as c:
        q = 'SELECT * FROM dominio_empresa WHERE ativo=1'
        emps = c.execute(q + (' AND cnpj=?' if cnpj else ''), ((cnpj,) if cnpj else ())).fetchall()
    novos = 0
    for emp in emps:
        ini = emp['data_inicial'] or '9999-12-31'
        raiz = os.path.join(base_xml, emp['cnpj'])
        if not os.path.isdir(raiz):
            continue
        with models.con() as c:
            conhecidos = {(r['doc'], r['arquivo']): (r['id'], r['caminho']) for r in c.execute(
                'SELECT id, doc, arquivo, caminho FROM dominio_envios WHERE cnpj=?', (emp['cnpj'],))}
        linhas = []
        for comp in sorted(os.listdir(raiz)):
            if not re.match(r'\d{4}-\d{2}$', comp) or comp < ini[:7]:
                continue
            for doc, subs in NOTAS.items():
                for sub in subs + (EVENTOS,):
                    pasta = os.path.join(raiz, comp, doc, sub)
                    if not os.path.isdir(pasta):
                        continue
                    for nome in os.listdir(pasta):
                        if not nome.lower().endswith('.xml'):
                            continue
                        caminho = os.path.join(pasta, nome)
                        k = (doc, nome)
                        if k in conhecidos:
                            if conhecidos[k][1] != caminho:  # 04_saida -> 09_cancelada
                                linhas.append(('mover', conhecidos[k][0], caminho, sub))
                            continue
                        try:
                            emissao, chave_xml, cancel = _ler_meta(caminho)
                        except OSError:
                            continue
                        if sub == EVENTOS and not cancel:
                            continue  # ciencia, CC-e etc. nao vao para a Dominio
                        m = RE_CHAVE.search(nome)
                        chave = chave_xml or (m.group(1) if m else None)
                        if sub == EVENTOS and m:
                            chave = m.group(1)  # nome do evento comeca pela chave da nota
                        estado = 'NOVO' if (emissao or comp) >= ini[:len(emissao or comp)] else 'FORA_PERIODO'
                        linhas.append(('novo', emp['cnpj'], doc, sub,
                                       'cancelamento' if sub == EVENTOS else 'nota',
                                       nome, chave, caminho, comp, emissao, estado))
        with models.con() as c:
            for l in linhas:
                if l[0] == 'mover':
                    c.execute('UPDATE dominio_envios SET caminho=?, sub=?, atualizado=? WHERE id=?',
                              (l[2], l[3], agora(), l[1]))
                    continue
                r = c.execute('INSERT OR IGNORE INTO dominio_envios(cnpj,doc,sub,tipo,arquivo,chave,caminho,'
                              'competencia,emissao,estado,criado,atualizado) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                              l[1:] + (agora(), agora()))
                novos += r.rowcount if l[10] == 'NOVO' else 0
    return novos


# ---------------------------------------------------------------- envio
def _espera(tentativas, minimo=60):
    return max(minimo, min(3600, 60 * 2 ** max(0, tentativas - 1))) + random.randint(1, 30)


def _atualizar(c, eid, **kw):
    kw['atualizado'] = agora()
    c.execute('UPDATE dominio_envios SET %s WHERE id=?' % ','.join('%s=?' % k for k in kw),
              tuple(kw.values()) + (eid,))


def _cnpjs_na_resposta(obj):
    """CNPJs (14 digitos) em qualquer campo da resposta de activation/info (nomes nao documentados)."""
    achados = []
    if isinstance(obj, dict):
        for v in obj.values():
            achados += _cnpjs_na_resposta(v)
    elif isinstance(obj, list):
        for v in obj:
            achados += _cnpjs_na_resposta(v)
    elif isinstance(obj, (str, int)):
        d = re.sub(r'\D', '', str(obj))
        if len(d) == 14:
            achados.append(d)
    return achados


def conferir_chave(cnpj, cli=None):
    """activation/info com a chave do contador. Guarda o retorno (escritorio, cliente, CNPJ) e
    PAUSA se a chave for de outro CNPJ. Retorna o dict da resposta."""
    cli = cli or api.cliente()
    with models.con() as c:
        r = c.execute('SELECT chave_cifrada FROM dominio_empresa WHERE cnpj=?', (cnpj,)).fetchone()
    if not r or not r['chave_cifrada']:
        raise api.DominioPausar('Empresa sem chave da Domínio cadastrada.')
    try:
        info = cli.info_chave(decifrar(r['chave_cifrada']))
    except api.DominioRecusado as e:
        raise api.DominioPausar('A Domínio não reconheceu a chave desta empresa: %s' % e, e.codigo) from None
    import json as _j
    with models.con() as c:
        c.execute('UPDATE dominio_empresa SET chave_info=?, atualizado=? WHERE cnpj=?',
                  (_j.dumps(info, ensure_ascii=False)[:2000], agora(), cnpj))
    cnpjs = _cnpjs_na_resposta(info)
    if cnpjs and cnpj not in cnpjs:
        raise api.DominioPausar('A chave cadastrada é do CNPJ %s, não desta empresa (%s). Confira com o '
                                'Onvio e cadastre a chave correta.' % (cnpjs[0], cnpj))
    return info


def _chave_envio(cnpj, cli):
    """Chave que vai no header dos envios. Com DOMINIO_ATIVAR_CHAVE=1 (padrao), troca a chave
    do cliente 1x em activation/enable e guarda o resultado cifrado; nas proximas, reusa."""
    with models.con() as c:
        r = c.execute('SELECT chave_cifrada, chave_envio_cifrada FROM dominio_empresa '
                      'WHERE cnpj=? AND ativo=1', (cnpj,)).fetchone()
    if not r or not r['chave_cifrada']:
        return ''
    if not api.config()['ativar_chave']:
        return decifrar(r['chave_cifrada'])
    if r['chave_envio_cifrada']:
        return decifrar(r['chave_envio_cifrada'])
    conferir_chave(cnpj, cli)  # guia oficial: confirmar que a chave e deste CNPJ antes de ativar
    try:
        nova = cli.ativar_chave(decifrar(r['chave_cifrada']))
    except api.DominioRecusado as e:  # chave do cliente invalida: para a empresa toda
        raise api.DominioPausar('A Domínio não ativou a chave desta empresa: %s Gere outra no Onvio '
                                '(API → Chave) e cadastre de novo.' % e, e.codigo) from None
    with models.con() as c:
        c.execute("UPDATE dominio_empresa SET chave_envio_cifrada=?, atualizado=? WHERE cnpj=?",
                  (cifrar(nova), agora(), cnpj))
    return nova


def _chave_empresa(cnpj, cache, cli):
    if cnpj not in cache:
        try:
            cache[cnpj] = _chave_envio(cnpj, cli)
        except api.DominioErro as e:
            cache[cnpj] = e
    v = cache[cnpj]
    if isinstance(v, Exception):
        raise v
    if not v:
        raise api.DominioPausar('Empresa sem chave da Domínio ou com envio desligado.')
    return v


def _pendentes(limite):
    """Notas primeiro; cancelamento so quando a nota original foi aceita ou nao esta na fila."""
    agora_s = agora()
    with models.con() as c:
        return c.execute(f"""
            SELECT d.* FROM dominio_envios d
            JOIN dominio_empresa e ON e.cnpj=d.cnpj AND e.ativo=1
            WHERE d.estado IN ('NOVO','REPETIR') AND (d.proximo IS NULL OR d.proximo<=?)
              AND (d.tipo='nota' OR NOT EXISTS (
                    SELECT 1 FROM dominio_envios n WHERE n.cnpj=d.cnpj AND n.tipo='nota'
                      AND n.chave=d.chave AND n.estado NOT IN ('CONFIRMADO','JA_EXISTENTE','FORA_PERIODO')))
            ORDER BY CASE d.tipo WHEN 'nota' THEN 0 ELSE 1 END, d.proximo IS NOT NULL, d.id
            LIMIT ?""", (agora_s, limite)).fetchall()


# ---------------------------------------------------------------- modo de conexao
MODOS = {'gateway': 'Gateway do sistema (código de ativação)', 'direto': 'API direta (Client ID/Secret)'}


def modo():
    """'gateway' = servidor do fornecedor, como no EXE (padrao); 'direto' = API Onvio com Client ID."""
    m = models.get_param('dominio_modo') or 'gateway'
    return m if m in MODOS else 'gateway'


def _gw():
    from engines import dominio_gateway
    return dominio_gateway


def _chave_cliente(cnpj, cache):
    """Chave do contador (sem troca): no modo gateway quem ativa na Dominio e o gateway."""
    if cnpj not in cache:
        with models.con() as c:
            r = c.execute('SELECT chave_cifrada FROM dominio_empresa WHERE cnpj=? AND ativo=1', (cnpj,)).fetchone()
        try:
            cache[cnpj] = decifrar(r['chave_cifrada']) if r and r['chave_cifrada'] else ''
        except api.DominioErro as e:
            cache[cnpj] = e
    v = cache[cnpj]
    if isinstance(v, Exception):
        raise v
    if not v:
        raise api.DominioPausar('Cadastre a chave Domínio desta empresa.')
    return v


ESTADO_GW = {'CONFIRMADO': 'CONFIRMADO', 'JA_EXISTENTE': 'JA_EXISTENTE', 'REJEITADO': 'RECUSADO',
             'INDETERMINADO': 'INCERTO'}
MSG_GW = {'REJEITADO': 'Domínio recusou o XML. Confira o documento e o código retornado.',
          'INDETERMINADO': 'Recebimento incerto na Domínio. Confira no destino; o sistema não reenviará '
                           'automaticamente.'}


def _aplicar_gw(eid, rid, res, tentativas=1):
    """Resultado do gateway -> estado da fila (mesma regra do apply_result do EXE)."""
    if res.get('request_id') != rid:
        raise api.DominioRepetir('Identificação divergente no gateway. A operação original foi preservada.')
    st = res['status']
    kw = {'codigo': ','.join(str(x) for x in (res.get('remote_codes') or []))[:80], 'mensagem': ''}
    if res.get('reference'):
        kw['protocolo'] = str(res['reference'])[:255]
    if st in ESTADO_GW:
        kw.update(estado=ESTADO_GW[st], proximo=None, ack_pendente=1, mensagem=MSG_GW.get(st, ''))
    elif st == 'FALHA_TRANSITORIA':
        if res.get('retryable'):
            minimo = max(30, int(res.get('next_attempt_at') or 0) - int(time.time()))
            kw.update(estado='REPETIR', proximo=_depois(_espera(tentativas, minimo)),
                      mensagem='Destino temporariamente indisponível. Nova tentativa agendada.')
        else:
            kw.update(estado='PAUSADO', proximo=None,
                      mensagem='Gateway pausou esta operação. Solicite liberação ao suporte; não gere outro envio.')
    else:  # RECEBIDO / AGUARDANDO_DOMINIO
        kw.update(estado='ENVIADO', proximo=_depois(30 + random.randint(1, 10)))
    with models.con() as c:
        if kw.get('estado') == 'ENVIADO':
            c.execute('UPDATE dominio_envios SET enviado_em=COALESCE(enviado_em, ?) WHERE id=?', (agora(), eid))
        _atualizar(c, eid, **kw)


def _processar_gateway(limite, gw=None):
    G = _gw()
    gw = gw or G.cliente()  # sem ativacao: DominioPausar sobe e nenhuma linha e tocada
    inst = G.instalacao()['installation_id']
    chaves, feitos = {}, 0
    for d in _pendentes(limite):
        with models.con() as c:
            if not c.execute("UPDATE dominio_envios SET estado='ENVIANDO', tentativas=tentativas+1, atualizado=? "
                             "WHERE id=? AND estado=?", (agora(), d['id'], d['estado'])).rowcount:
                continue
        feitos += 1
        tent = d['tentativas'] + 1
        try:
            chave = _chave_cliente(d['cnpj'], chaves)
            with open(d['caminho'], 'rb') as f:
                conteudo = f.read()
            if not 0 < len(conteudo) <= G.LIMITE_BYTES:
                raise api.DominioRecusado('XML vazio ou acima do limite de 2 MB do gateway.')
            h = hashlib.sha256(conteudo).hexdigest()
            if d['hash'] and d['hash'] != h:
                raise api.DominioRecusado('XML alterado após iniciar o envio. Confira a operação existente '
                                          'antes de reenviar.')
            cnpj = re.sub('[^A-Z0-9]', '', d['cnpj'].upper())
            rid, idem = d['lote_id'], d['idem_key']
            if not rid:  # identidade da operacao: a mesma do EXE
                rid = hashlib.sha256(('%s|%s|%s' % (inst, cnpj, h)).encode()).hexdigest()
                idem = idem or str(uuid.uuid4())
                with models.con() as c:
                    _atualizar(c, d['id'], hash=h, lote_id=rid, idem_key=idem)
            res = gw.status(rid, cnpj, chave) if d['iniciado'] else None
            if res is None:
                with models.con() as c:
                    _atualizar(c, d['id'], iniciado=1)
                res = gw.send(conteudo, cnpj, chave, idem)
            elif (res['status'] == 'FALHA_TRANSITORIA' and res.get('retryable')
                  and int(res.get('next_attempt_at') or 0) <= time.time()):
                res = gw.send(conteudo, cnpj, chave, idem)
            _aplicar_gw(d['id'], rid, res, tent)
        except FileNotFoundError:
            with models.con() as c:
                _atualizar(c, d['id'], estado='RECUSADO', proximo=None,
                           mensagem='Não foi possível ler o XML original. Confira o arquivo local.')
        except api.DominioPausar as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='PAUSADO', codigo=e.codigo, mensagem=str(e), proximo=None)
            chaves[d['cnpj']] = e
        except api.DominioRepetir as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='REPETIR', codigo=e.codigo, mensagem=str(e),
                           proximo=_depois(_espera(tent, e.espera)))
        except api.DominioErro as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='RECUSADO', codigo=e.codigo, mensagem=str(e), proximo=None)
        except Exception as e:  # mesma operacao (request_id/idempotencia) sera recuperada
            with models.con() as c:
                _atualizar(c, d['id'], estado='REPETIR', proximo=_depois(_espera(tent)),
                           mensagem='Execução interrompida (%s). A mesma operação será recuperada com segurança.'
                                    % type(e).__name__)
    return feitos


def _consultar_gateway(limite=100, gw=None):
    """Le resultados em lote (nunca reenvia), pede acompanhamento e confirma os finais."""
    gw = gw or _gw().cliente()
    with models.con() as c:
        rows = c.execute("SELECT * FROM dominio_envios WHERE estado='ENVIADO' AND lote_id IS NOT NULL "
                         'AND (proximo IS NULL OR proximo<=?) ORDER BY proximo, id LIMIT ?',
                         (agora(), limite)).fetchall()
    por_id = {r['lote_id']: r for r in rows}
    if por_id:
        res = gw.batch_status(list(por_id))
        for it in res['items']:
            _aplicar_gw(por_id[it['request_id']]['id'], it['request_id'], it)
        with models.con() as c:
            for rid in res.get('missing', []):
                _atualizar(c, por_id[rid]['id'], estado='INCERTO', proximo=None,
                           mensagem='Lote já recebido, mas operação indisponível no gateway. Confira antes de reenviar.')
        chaves = {}
        for it in [i for i in res['items'] if i['status'] == 'AGUARDANDO_DOMINIO'
                   and not i.get('background_tracking')][:2]:
            d = por_id[it['request_id']]
            try:
                gw.track_existing(it['request_id'], d['cnpj'], _chave_cliente(d['cnpj'], chaves))
            except api.DominioErro:
                pass
    with models.con() as c:
        pend = [r['lote_id'] for r in c.execute(
            'SELECT lote_id FROM dominio_envios WHERE ack_pendente=1 AND lote_id IS NOT NULL LIMIT 100')]
    if pend:
        resp = gw.acknowledge(pend)
        aceitos = {i.get('request_id') for i in (resp.get('items') or []) if isinstance(i, dict)}
        with models.con() as c:
            for rid in aceitos & set(pend):
                c.execute('UPDATE dominio_envios SET ack_pendente=0 WHERE lote_id=?', (rid,))
    return len(rows)


def processar(limite=5, cliente=None, gateway=None):
    """Envia ate `limite` XMLs. Retorna quantos foram tentados."""
    if gateway is not None or (cliente is None and modo() == 'gateway'):
        return _processar_gateway(limite, gateway)
    cli = cliente or api.cliente()
    chaves, feitos = {}, 0
    for d in _pendentes(limite):
        with models.con() as c:  # reserva atomica: so um processo envia esta linha
            if not c.execute("UPDATE dominio_envios SET estado='ENVIANDO', tentativas=tentativas+1, atualizado=? "
                             "WHERE id=? AND estado=?", (agora(), d['id'], d['estado'])).rowcount:
                continue
        feitos += 1
        tent = d['tentativas'] + 1
        try:
            chave = _chave_empresa(d['cnpj'], chaves, cli)
            with open(d['caminho'], 'rb') as f:
                conteudo = f.read()
            h = hashlib.sha256(conteudo).hexdigest()
            if d['hash'] and d['hash'] != h:
                raise api.DominioRecusado('O XML mudou depois da primeira tentativa. Confira antes de reenviar.')
            with models.con() as c:
                _atualizar(c, d['id'], hash=h)
            lote = cli.enviar(conteudo, d['arquivo'], chave)
            with models.con() as c:
                _atualizar(c, d['id'], estado='ENVIADO', lote_id=lote, enviado_em=agora(), codigo='',
                           mensagem='', consultas=0, proximo=_depois(45))
        except FileNotFoundError:
            with models.con() as c:
                _atualizar(c, d['id'], estado='RECUSADO', proximo=None,
                           mensagem='Arquivo XML não encontrado no servidor (excluído ou movido).')
        except api.DominioPausar as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='PAUSADO', codigo=e.codigo, mensagem=str(e), proximo=None)
            chaves[d['cnpj']] = e  # nao insiste nas outras notas da empresa neste ciclo
        except api.DominioRepetir as e:
            est = 'REPETIR' if tent < MAX_TENTATIVAS else 'PAUSADO'
            with models.con() as c:
                _atualizar(c, d['id'], estado=est, codigo=e.codigo, mensagem=str(e),
                           proximo=_depois(_espera(tent, e.espera)) if est == 'REPETIR' else None)
        except api.DominioIncerto as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='INCERTO', codigo=e.codigo, mensagem=str(e), proximo=None)
        except (api.DominioRecusado, api.DominioErro) as e:
            with models.con() as c:
                _atualizar(c, d['id'], estado='RECUSADO', codigo=e.codigo, mensagem=str(e), proximo=None)
        except Exception as e:  # erro inesperado DEPOIS da reserva: nao sabemos se saiu
            with models.con() as c:
                _atualizar(c, d['id'], estado='INCERTO', proximo=None,
                           mensagem='Envio interrompido (%s). Confira na Domínio.' % type(e).__name__)
    return feitos


def consultar(limite=20, cliente=None, gateway=None):
    """Consulta lotes ENVIADO cujo horario chegou."""
    if gateway is not None or (cliente is None and modo() == 'gateway'):
        return _consultar_gateway(100, gateway)
    cli = cliente or api.cliente()
    chaves, n = {}, 0
    with models.con() as c:
        rows = c.execute("SELECT * FROM dominio_envios WHERE estado='ENVIADO' AND (proximo IS NULL OR proximo<=?) "
                         'ORDER BY proximo, id LIMIT ?', (agora(), limite)).fetchall()
    for d in rows:
        n += 1
        try:
            lote = cli.consultar(d['lote_id'], _chave_empresa(d['cnpj'], chaves, cli))
            estado, codigo, msg = api.interpretar_lote(lote)
            consultas = d['consultas'] + 1
            with models.con() as c:
                if estado:
                    _atualizar(c, d['id'], estado=estado, codigo=codigo, mensagem=msg, proximo=None,
                               consultas=consultas)
                elif consultas >= MAX_CONSULTAS:
                    _atualizar(c, d['id'], estado='INCERTO', codigo=codigo, consultas=consultas, proximo=None,
                               mensagem='Sem resultado conhecido da Domínio (%s %s). Confira no destino.'
                                        % (codigo or 'sem código', msg))
                else:
                    _atualizar(c, d['id'], codigo=codigo, mensagem=msg, consultas=consultas,
                               proximo=_depois(min(1800, 60 * consultas)))
        except api.DominioPausar as e:
            with models.con() as c:  # fica ENVIADO: so adia a consulta
                _atualizar(c, d['id'], mensagem=str(e), proximo=_depois(900))
        except api.DominioErro as e:
            with models.con() as c:
                _atualizar(c, d['id'], mensagem=str(e), proximo=_depois(300))
    return n


def reconciliar_travados():
    """No boot: ENVIANDO antigo = o processo morreu no meio do envio -> INCERTO."""
    corte = (datetime.now() - timedelta(minutes=TRAVADO_MIN)).strftime('%Y-%m-%d %H:%M:%S')
    with models.con() as c:
        return c.execute("UPDATE dominio_envios SET estado='INCERTO', proximo=NULL, atualizado=?, "
                         "mensagem='O servidor reiniciou durante o envio. Confira na Domínio.' "
                         "WHERE estado='ENVIANDO' AND atualizado<?", (agora(), corte)).rowcount


def pedir_reenvio(eid, conferido=False):
    """Volta a linha para a fila. INCERTO exige que alguem tenha conferido na Dominio."""
    with models.con() as c:
        d = c.execute('SELECT * FROM dominio_envios WHERE id=?', (eid,)).fetchone()
        if not d:
            raise ValueError('Envio não encontrado.')
        if d['estado'] in ACEITOS or d['estado'] in ('ENVIANDO', 'ENVIADO', 'NOVO'):
            raise ValueError('Este envio não precisa de reenvio (%s).' % ROTULOS[d['estado']])
        if d['estado'] == 'INCERTO' and not conferido:
            raise ValueError('Confira na Domínio que o arquivo não chegou e marque a confirmação.')
        if d['estado'] == 'FORA_PERIODO':
            raise ValueError('Documento anterior à data inicial da empresa.')
        if d['iniciado'] and d['lote_id']:  # operacao do gateway: o reenvio precisa ser liberado la
            if d['protocolo']:
                raise ValueError('O gateway já tem protocolo deste documento. Confira o resultado antes de '
                                 'outro envio.')
            _reenvio_gateway(d)
            return
        # Reenvio manual cria um lote novo: o hash zera para aceitar XML corrigido.
        _atualizar(c, eid, estado='REPETIR', proximo=agora(), mensagem='Reenvio pedido manualmente.',
                   hash=None, lote_id=None, codigo='', tentativas=0)


def _reenvio_gateway(d, gw=None):
    """POST /envios/{id}/retentativas (como request_manual_retry do EXE). Mantem a mesma operacao."""
    try:
        res = (gw or _gw().cliente()).authorize_retry(d['lote_id'], str(uuid.uuid4()))
    except api.DominioErro as e:
        raise ValueError('Gateway não liberou o reenvio: %s' % e) from None
    with models.con() as c:
        if res is not None and res['status'] == 'FALHA_TRANSITORIA' and res.get('reference'):
            _atualizar(c, d['id'], estado='INCERTO', protocolo=str(res['reference'])[:255], proximo=None,
                       mensagem='O gateway já possui um protocolo. Confira o resultado antes de outro envio.')
            return
        if res is None or res['status'] == 'FALHA_TRANSITORIA':
            _atualizar(c, d['id'], estado='REPETIR', proximo=agora(), tentativas=0,
                       mensagem='Reenvio liberado no gateway.')
            return
    _aplicar_gw(d['id'], d['lote_id'], res)


# ---------------------------------------------------------------- resumo p/ tela
def resumo(cnpjs=None):
    filtro, args = '', ()
    if cnpjs is not None:
        filtro = ' WHERE cnpj IN (%s)' % (','.join('?' * len(cnpjs)) or "''")
        args = tuple(cnpjs)
    with models.con() as c:
        rows = c.execute('SELECT estado, COUNT(*) n FROM dominio_envios%s GROUP BY estado' % filtro, args).fetchall()
    return {r['estado']: r['n'] for r in rows}


# ---------------------------------------------------------------- worker
_iniciado = False
_ultimo = {'varredura': None, 'ciclo': None, 'erro': ''}


def estado_worker():
    return dict(_ultimo, ativo_env=os.environ.get('DOMINIO_ENVIO_ATIVO', '0') == '1', thread=_iniciado)


def ciclo():
    """Uma volta: varre (a cada 5 min), envia e consulta."""
    if not _ultimo['varredura'] or time.time() - _ultimo['varredura'] > 300:
        varrer()
        _ultimo['varredura'] = time.time()
    if modo() == 'gateway':  # como o EXE: 1 XML por volta e resultados a cada 30 s
        processar(1)
        if not _ultimo.get('consulta') or time.time() - _ultimo['consulta'] > 30:
            _ultimo['consulta'] = time.time()
            consultar()
    else:
        processar(int(os.environ.get('DOMINIO_LOTE_CICLO', '5')))
        consultar()
    _ultimo['ciclo'] = agora()


def _loop():
    while True:
        try:
            if envio_ligado():
                ciclo()
                _ultimo['erro'] = ''
        except Exception as e:
            _ultimo['erro'] = '%s: %s' % (type(e).__name__, str(e)[:150])
        padrao = 4 if modo() == 'gateway' else 15
        time.sleep(float(os.environ.get('DOMINIO_INTERVALO_SEG') or padrao))


def iniciar():
    """Thread propria (nao disputa a fila de jobs da SEFAZ). So no processo web."""
    global _iniciado
    if _iniciado or os.environ.get('DOMINIO_ENVIO_ATIVO', '0') != '1':
        return
    _iniciado = True
    try:
        reconciliar_travados()
    except Exception:
        pass
    threading.Thread(target=_loop, daemon=True, name='dominio-envio').start()
