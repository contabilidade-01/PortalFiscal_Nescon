# -*- coding: utf-8 -*-
"""Portal Fiscal Nescon - sistema UNICO (NFe + NFSe), base de clientes unica.
   Abas NFe/NFSe, flags por cliente (puxa_nfe/puxa_nfse) e metodo de saida.
   Roda local; pronto para servidor. Preserva PuxadorNFe_Web e PORTAL NACIONAL NFSE.
"""
import os, io, re, json, zipfile, threading
from functools import wraps
from datetime import datetime
from flask import (Flask, request, redirect, url_for, session, flash,
                   render_template, send_file, abort, after_this_request)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import models
import worker
from engines import certs, nfe, nfse, conferencia, cfop, monofasico
from engines import backup as bak

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
# Em producao, DATA_DIR (volume) separa dados do codigo. Local: cai nos defaults atuais.
CERT_DIR = os.path.join(models.DATA_DIR, 'Certificados')
SAIDA = os.environ.get('FISCAL_XML_DIR') or CFG.get('pasta_saida_xml') or os.path.join(models.DATA_DIR, 'XML')
os.makedirs(CERT_DIR, exist_ok=True)
os.makedirs(SAIDA, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'portal-fiscal-nescon-dev-trocar-em-producao')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=(os.environ.get('FLASK_HTTPS') == '1'),  # ativar em HTTPS (prod)
    MAX_CONTENT_LENGTH=80 * 1024 * 1024,  # backup (cadastros + certificados)
)
# EasyPanel (e qualquer reverse proxy) manda X-Forwarded-Proto/Host. Sem isso o
# cookie Secure e os redirects HTTPS quebram atras do painel.
if os.environ.get('TRUST_PROXY', os.environ.get('FLASK_HTTPS')) == '1':
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
models.init_db()
worker.iniciar_worker()  # motor de jobs em background (roda mesmo sem usuario logado)

@app.route('/healthz')
def healthz():
    """Healthcheck do EasyPanel/Docker — sem login."""
    try:
        with models.con() as c:
            c.execute('SELECT 1').fetchone()
        return {'ok': True}, 200
    except Exception:
        return {'ok': False}, 503

def login_req(f):
    @wraps(f)
    def w(*a, **k):
        if 'uid' not in session: return redirect(url_for('login'))
        return f(*a, **k)
    return w

def usuario_atual():
    if 'uid' not in session: return None
    with models.con() as c:
        return c.execute('SELECT * FROM usuarios WHERE id=?', (session['uid'],)).fetchone()

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if 'uid' not in session: return redirect(url_for('login'))
        if session.get('papel') != 'admin':
            flash('Acesso restrito ao administrador.', 'erro'); return redirect(url_for('dashboard'))
        return f(*a, **k)
    return w

@app.context_processor
def _inject_papel():
    return {'papel_atual': session.get('papel')}

def _visiveis_ids():
    """IDs de empresas que o usuario logado pode ver. None = todas (admin/todas_empresas)."""
    if session.get('papel') == 'admin':
        return None
    u = usuario_atual()
    return models.empresas_visiveis_ids(u) if u else []

def _scope(rows):
    ids = _visiveis_ids()
    if ids is None:
        return rows
    idset = set(ids)
    return [r for r in rows if r['id'] in idset]

def _pode_ver_cnpj(cnpj):
    ids = _visiveis_ids()
    if ids is None:
        return True
    with models.con() as c:
        r = c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
    return bool(r) and r['id'] in set(ids)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        with models.con() as c:
            row = c.execute('SELECT * FROM usuarios WHERE login=?', (request.form.get('login', '').strip(),)).fetchone()
        if row and row['ativo'] and check_password_hash(row['senha_hash'], request.form.get('senha', '')):
            session['uid'] = row['id']; session['nome'] = row['nome']; session['papel'] = row['papel']
            session['senha_padrao'] = (row['login'] == 'admin' and request.form.get('senha', '') == 'admin')
            return redirect(url_for('dashboard'))
        flash('Login ou senha inválidos (ou usuário inativo).', 'erro')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/trocar-senha', methods=['GET', 'POST'])
@login_req
def trocar_senha():
    if request.method == 'POST':
        atual = request.form.get('atual', ''); nova = request.form.get('nova', '')
        with models.con() as c:
            u = c.execute('SELECT senha_hash FROM usuarios WHERE id=?', (session['uid'],)).fetchone()
        if not check_password_hash(u['senha_hash'], atual):
            flash('Senha atual incorreta.', 'erro'); return redirect(url_for('trocar_senha'))
        if len(nova) < 6:
            flash('A nova senha deve ter ao menos 6 caracteres.', 'erro'); return redirect(url_for('trocar_senha'))
        with models.con() as c:
            c.execute('UPDATE usuarios SET senha_hash=? WHERE id=?',
                      (generate_password_hash(nova), session['uid']))
        session['senha_padrao'] = False
        flash('Senha alterada com sucesso.', 'ok'); return redirect(url_for('dashboard'))
    return render_template('trocar_senha.html')

def _comp_anterior():
    """(ano, mes) do mes ANTERIOR ao atual — padrao do Fiscal (apura o mes passado)."""
    h = datetime.now()
    if h.month == 1:
        return str(h.year - 1), '12'
    return str(h.year), '%02d' % (h.month - 1)

@app.template_filter('brl')
def _brl(v):
    """Formata numero no padrao BR: 65161.25 -> 65.161,25."""
    try:
        v = float(v)
    except Exception:
        v = 0.0
    return ('{:,.2f}'.format(v)).replace(',', '§').replace('.', ',').replace('§', '.')

# Mapeia subpasta fisica -> categoria de MOVIMENTO (compra/venda/tomado/prestado)
_MOV = {
    'NFe':  {'entrada': ('01_entrada',), 'saida': ('04_saida',),
             'resumo': ('02_resumo',), 'eventos': ('03_eventos',)},
    'NFSe': {'tomado': ('01_tomado',), 'prestado': ('02_prestado',)},
    'NFCe': {'venda': ('01_venda',)},
}

def _contagem_movimentos(cnpjs):
    """Conta XMLs por MOVIMENTO (rapido: so listdir, sem abrir/parsear).
       Retorna {nfe_entrada, nfe_saida, nfe_resumo, nfe_eventos, nfse_tomado,
       nfse_prestado, nfce_venda}."""
    tot = {'nfe_entrada': 0, 'nfe_saida': 0, 'nfe_resumo': 0, 'nfe_eventos': 0,
           'nfse_tomado': 0, 'nfse_prestado': 0, 'nfce_venda': 0}
    for cnpj in cnpjs:
        base = os.path.join(SAIDA, cnpj)
        if not os.path.isdir(base):
            continue
        for comp in os.listdir(base):
            if not re.match(r'\d{4}-\d{2}$', comp):
                continue
            for doc, cats in _MOV.items():
                for cat, subs in cats.items():
                    for sub in subs:
                        d = os.path.join(base, comp, doc, sub)
                        if os.path.isdir(d):
                            tot['%s_%s' % (doc.lower(), cat)] += sum(
                                1 for f in os.listdir(d) if f.lower().endswith('.xml'))
    return tot

@app.route('/')
@login_req
def dashboard():
    with models.con() as c:
        emp = _scope(c.execute('SELECT * FROM empresas').fetchall())
        exec_nfe = c.execute("SELECT * FROM execucoes WHERE tipo LIKE 'nfe\\_%' ESCAPE '\\' ORDER BY id DESC LIMIT 12").fetchall()
        exec_nfse = c.execute("SELECT * FROM execucoes WHERE tipo='nfse' ORDER BY id DESC LIMIT 12").fetchall()
        exec_nfce = c.execute("SELECT * FROM execucoes WHERE tipo='nfce' ORDER BY id DESC LIMIT 12").fetchall()
    mov = _contagem_movimentos([e['cnpj'] for e in emp])
    # contadores das abas = XMLs REAIS em disco (batem com a quebra por movimento abaixo)
    kpi = dict(
        total=len(emp), ativas=sum(1 for e in emp if e['ativo']),
        nfe=sum(1 for e in emp if e['puxa_nfe']), nfse=sum(1 for e in emp if e['puxa_nfse']),
        nfce=sum(1 for e in emp if (e['puxa_nfce'] or 0)),
        com_cert=sum(1 for e in emp if e['senha_ok']),
        docs_nfe=mov['nfe_entrada'] + mov['nfe_saida'] + mov['nfe_resumo'] + mov['nfe_eventos'],
        docs_nfse=mov['nfse_tomado'] + mov['nfse_prestado'],
        docs_nfce=mov['nfce_venda'],
    )
    office = models.get_param('office_cnpj')
    rodando, fila, _rec = worker.status()
    msg = None
    if rodando:
        msg = '%s (%s/%s)' % (rodando['atual'] or rodando['mensagem'] or 'processando',
                              rodando['feitos'] or 0, rodando['total'] or 0)
    return render_template('dashboard.html', kpi=kpi, office=office, mov=mov,
                           exec_nfe=exec_nfe, exec_nfse=exec_nfse, exec_nfce=exec_nfce,
                           status_nfe=msg, status_nfse=msg, status_nfce=msg)

@app.route('/clientes')
@login_req
def clientes():
    q = (request.args.get('q') or '').strip()
    with models.con() as c:
        if q:
            emp = c.execute("SELECT * FROM empresas WHERE nome LIKE ? OR cnpj LIKE ? ORDER BY nome",
                            ('%'+q+'%', '%'+q+'%')).fetchall()
        else:
            emp = c.execute('SELECT * FROM empresas ORDER BY nome').fetchall()
    return render_template('clientes.html', empresas=_scope(emp), q=q)

@app.route('/certificados')
@login_req
def certificados():
    hoje = datetime.now().date()
    def status(v):
        try:
            d = datetime.strptime(v, '%Y-%m-%d').date(); dias = (d - hoje).days
            return ('vencido' if dias < 0 else 'vencendo' if dias <= 30 else 'ok'), dias
        except Exception:
            return 'ok', None
    with models.con() as c:
        com_rows = _scope(c.execute('SELECT * FROM empresas WHERE senha_ok=1 ORDER BY validade').fetchall())
        sem = _scope(c.execute('SELECT * FROM empresas WHERE senha_ok=0 ORDER BY ativo DESC, nome').fetchall())
    com = []; venc = 0; vencendo = 0
    for e in com_rows:
        st, dias = status(e['validade'])
        if st == 'vencido': venc += 1
        elif st == 'vencendo': vencendo += 1
        com.append({'e': e, 'st': st, 'dias': dias})
    kpi = {'com': len(com), 'sem': len(sem), 'sem_ativas': sum(1 for s in sem if s['ativo']),
           'venc': venc, 'vencendo': vencendo}
    return render_template('certificados.html', com=com, sem=sem, kpi=kpi, office=models.get_param('office_cnpj'))

@app.route('/clientes/<int:eid>/salvar', methods=['POST'])
@admin_required
def cliente_salvar(eid):
    f = request.form
    with models.con() as c:
        c.execute('''UPDATE empresas SET puxa_nfe=?, puxa_nfse=?, puxa_nfce=?, metodo_saida=?, emissor=?, ativo=? WHERE id=?''',
                  (1 if f.get('puxa_nfe') else 0, 1 if f.get('puxa_nfse') else 0, 1 if f.get('puxa_nfce') else 0,
                   f.get('metodo_saida') or 'a_verificar', f.get('emissor') or '',
                   1 if f.get('ativo') else 0, eid))
    flash('Cliente atualizado.', 'ok')
    return redirect(url_for('clientes', q=request.args.get('q', '')))

def _uf_cuf(uf):
    uf = (uf or '').upper()[:2]
    return uf, certs.UF_COD.get(uf, '35')

def _rbt12(s):
    """Converte o RBT12 digitado (aceita 180000 / 180.000 / 180.000,00) em float."""
    s = re.sub(r'[^\d.,]', '', s or '')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif s.count('.') >= 1:
        s = s.replace('.', '')            # pontos = separador de milhar
    try:
        return float(s or 0)
    except Exception:
        return 0.0

def _anexo(s):
    s = (s or 'I').upper().strip()
    return s if s in ('I', 'II', 'III', 'IV', 'V') else 'I'

@app.route('/clientes/novo', methods=['GET', 'POST'])
@admin_required
def cliente_novo():
    if request.method == 'POST':
        f = request.form
        cnpj = re.sub(r'\D', '', f.get('cnpj', ''))
        if len(cnpj) != 14:
            flash('CNPJ inválido (informe 14 dígitos).', 'erro'); return redirect(url_for('cliente_novo'))
        uf, cuf = _uf_cuf(f.get('uf'))
        with models.con() as c:
            if c.execute('SELECT 1 FROM empresas WHERE cnpj=?', (cnpj,)).fetchone():
                flash('Já existe cliente com esse CNPJ.', 'erro'); return redirect(url_for('clientes'))
            c.execute('''INSERT INTO empresas(cnpj,nome,uf,cuf,whatsapp,responsavel,email,emissor,marketplace,
                         metodo_saida,puxa_nfe,puxa_nfse,puxa_nfce,ativo,obs,simples_anexo,simples_rbt12,origem,criado)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (cnpj, f.get('nome', ''), uf, cuf, f.get('whatsapp', ''), f.get('responsavel', ''),
                       f.get('email', ''), f.get('emissor', ''), f.get('marketplace', ''),
                       f.get('metodo_saida', 'a_verificar'), 1 if f.get('puxa_nfe') else 0,
                       1 if f.get('puxa_nfse') else 0, 1 if f.get('puxa_nfce') else 0,
                       1 if f.get('ativo') else 0, f.get('obs', ''),
                       _anexo(f.get('simples_anexo')), _rbt12(f.get('simples_rbt12')),
                       'manual', datetime.now().isoformat(timespec='seconds')))
        flash('Cliente cadastrado.', 'ok'); return redirect(url_for('clientes'))
    return render_template('cliente_form.html', e=None)

@app.route('/clientes/<int:eid>/editar', methods=['GET', 'POST'])
@admin_required
def cliente_editar(eid):
    with models.con() as c:
        e = c.execute('SELECT * FROM empresas WHERE id=?', (eid,)).fetchone()
    if not e:
        abort(404)
    if request.method == 'POST':
        f = request.form
        uf, cuf = _uf_cuf(f.get('uf'))
        with models.con() as c:
            c.execute('''UPDATE empresas SET nome=?,uf=?,cuf=?,whatsapp=?,responsavel=?,email=?,emissor=?,
                         marketplace=?,metodo_saida=?,puxa_nfe=?,puxa_nfse=?,puxa_nfce=?,ativo=?,obs=?,
                         simples_anexo=?,simples_rbt12=? WHERE id=?''',
                      (f.get('nome', ''), uf, cuf, f.get('whatsapp', ''), f.get('responsavel', ''),
                       f.get('email', ''), f.get('emissor', ''), f.get('marketplace', ''),
                       f.get('metodo_saida', 'a_verificar'), 1 if f.get('puxa_nfe') else 0,
                       1 if f.get('puxa_nfse') else 0, 1 if f.get('puxa_nfce') else 0,
                       1 if f.get('ativo') else 0, f.get('obs', ''),
                       _anexo(f.get('simples_anexo')), _rbt12(f.get('simples_rbt12')), eid))
        flash('Cliente atualizado.', 'ok'); return redirect(url_for('clientes'))
    return render_template('cliente_form.html', e=e)

@app.route('/clientes/<int:eid>/excluir', methods=['POST'])
@admin_required
def cliente_excluir(eid):
    with models.con() as c:
        r = c.execute('SELECT nome FROM empresas WHERE id=?', (eid,)).fetchone()
        c.execute('DELETE FROM empresas WHERE id=?', (eid,))
    flash('Cliente %s excluído da base. (Os XML já baixados permanecem na pasta.)' % (r['nome'] if r else eid), 'ok')
    return redirect(url_for('clientes'))

# ---- Modulo DAS (placeholder — expansao futura) ----
@app.route('/das')
@login_req
def das():
    return render_template('das.html')

# ---- Ajuda / glossario / passo-a-passo (para novos usuarios / estagiarios) ----
@app.route('/ajuda')
@login_req
def ajuda():
    return render_template('ajuda.html')

# ---- Backup / restauracao (admin) — migrar Windows -> EasyPanel ----
@app.route('/backup')
@admin_required
def backup():
    info = bak.resumo(models.DB, CERT_DIR)
    vol = bak.zip_do_volume(models.DATA_DIR)
    return render_template('backup.html', info=info,
                           volume_zip=os.path.isfile(vol),
                           volume_nome=bak.RESTAURAR_VOLUME)

@app.route('/backup/baixar')
@admin_required
def backup_baixar():
    nome = 'portal-fiscal-cadastros-certificados-%s.zip' % datetime.now().strftime('%Y%m%d-%H%M')
    pasta = os.path.join(models.DATA_DIR, 'backups')
    os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, nome)
    bak.criar_zip(dest, models.DB, CERT_DIR)

    @after_this_request
    def _apagar(resp):
        try:
            os.remove(dest)
        except OSError:
            pass
        return resp

    return send_file(dest, as_attachment=True, download_name=nome,
                     mimetype='application/zip')

def _aplicar_zip(path):
    r = bak.restaurar_zip(path, models.DB, CERT_DIR)
    flash('Restaurado: %s cadastros e %s certificados. XML não entra neste backup — o servidor puxa de novo.' % (
        r['empresas'], r['pfx']), 'ok')

@app.route('/backup/restaurar', methods=['POST'])
@admin_required
def backup_restaurar():
    fobj = request.files.get('zip')
    if not fobj or not fobj.filename:
        flash('Escolha o arquivo .zip do backup.', 'erro')
        return redirect(url_for('backup'))
    pasta = os.path.join(models.DATA_DIR, 'backups')
    os.makedirs(pasta, exist_ok=True)
    dest = os.path.join(pasta, 'upload-%s.zip' % datetime.now().strftime('%Y%m%d-%H%M%S'))
    fobj.save(dest)
    try:
        _aplicar_zip(dest)
    except Exception as e:
        flash('Não deu para restaurar: %s' % str(e)[:180], 'erro')
    finally:
        try:
            os.remove(dest)
        except OSError:
            pass
    return redirect(url_for('backup'))

@app.route('/backup/restaurar-volume', methods=['POST'])
@admin_required
def backup_restaurar_volume():
    vol = bak.zip_do_volume(models.DATA_DIR)
    if not os.path.isfile(vol):
        flash('Não achei %s na pasta de dados do servidor. Envie o zip pelo formulário ou coloque o arquivo nesse nome no volume.' % bak.RESTAURAR_VOLUME, 'erro')
        return redirect(url_for('backup'))
    try:
        _aplicar_zip(vol)
    except Exception as e:
        flash('Não deu para restaurar: %s' % str(e)[:180], 'erro')
    return redirect(url_for('backup'))

# ---- Configuracoes (admin) — inclui a busca de NFC-e SP ----
@app.route('/configuracoes', methods=['GET', 'POST'])
@admin_required
def configuracoes():
    if request.method == 'POST':
        ano = re.sub(r'\D', '', request.form.get('ano', ''))[:4]
        mes = re.sub(r'\D', '', request.form.get('mes', '')).zfill(2)[:2]
        dia = re.sub(r'\D', '', request.form.get('dia', '')).zfill(2)[:2]
        try:
            datetime.strptime('%s-%s-%s' % (ano, mes, dia), '%Y-%m-%d')
            models.set_param('nfce_data_inicial', '%s-%s-%s' % (ano, mes, dia))
        except Exception:
            flash('Data inválida (confira ano/mês/dia).', 'erro'); return redirect(url_for('configuracoes'))
        models.set_param('nfce_limite', re.sub(r'\D', '', request.form.get('limite', '500')) or '500')
        flash('Configurações salvas.', 'ok'); return redirect(url_for('configuracoes'))
    di = (models.get_param('nfce_data_inicial') or datetime.now().strftime('%Y-%m-01'))[:10]
    return render_template('configuracoes.html', ano=di[:4], mes=di[5:7], dia=di[8:10],
                           limite=(models.get_param('nfce_limite') or '500'))

# ---- Gestao de execucoes (admin) ----
# Rotulos em portugues claro: o banco guarda slug ('nfe_entradas', 'ok'), a tela
# nunca mostra slug. Usados no HTML e tambem no JS do painel ao vivo.
TIPO_LABEL = {
    'completo': 'Busca completa',
    'nfe_entradas': 'Entradas NF-e (compras)',
    'nfe_saidas': 'Saídas NF-e (vendas)',
    'nfse': 'NFS-e (serviços)',
    'nfce': 'NFC-e (varejo · SP)',
    'ciencia': 'Ciência 210210',
}
STATUS_LABEL = {
    'fila': 'Na fila', 'rodando': 'Rodando agora', 'ok': 'Concluída',
    'erro': 'Falhou', 'cancelado': 'Cancelada', 'interrompido': 'Interrompida',
}

@app.route('/execucoes')
@admin_required
def execucoes():
    with models.con() as c:
        jobs = c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 50').fetchall()
        execs = c.execute('SELECT * FROM execucoes ORDER BY id DESC LIMIT 100').fetchall()
        # Saude do robo: a ultima rodada AUTOMATICA que terminou. Num VPS ninguem
        # olha terminal — esta linha e o unico jeito de saber se o agendador rodou.
        ultimo_auto = c.execute("""SELECT * FROM jobs WHERE origem='agendado'
                                   AND status IN ('ok','erro','interrompido')
                                   ORDER BY id DESC LIMIT 1""").fetchone()
    horas_auto = None
    if ultimo_auto and ultimo_auto['terminado']:
        try:
            horas_auto = (datetime.now()
                          - datetime.strptime(ultimo_auto['terminado'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
        except Exception:
            horas_auto = None
    # "O que sera baixado" — janela/periodo por tipo (para o usuario entender o alcance)
    nfce_di = (models.get_param('nfce_data_inicial') or datetime.now().strftime('%Y-%m-01'))[:10]
    periodos = {
        'nfe_entradas': 'Incremental por NSU — continua de onde parou (não repete o que já baixou).',
        'nfe_saidas': 'Incremental por NSU, pelo certificado do escritório (autXML).',
        'nfse': 'Incremental por NSU no Portal Nacional (ADN).',
        'nfce': 'Por período: de %s até hoje (limite %s por rodada). Ajuste em Configurações.'
                % (nfce_di, models.get_param('nfce_limite') or '500'),
        'ciencia': 'Só as NF-e de entrada com ciência pendente (evento 210210).',
    }
    return render_template('execucoes.html', jobs=jobs, execs=execs, periodos=periodos,
                           tipo_label=TIPO_LABEL, status_label=STATUS_LABEL,
                           ultimo_auto=ultimo_auto, horas_auto=horas_auto)

@app.route('/execucoes/nova', methods=['POST'])
@admin_required
def execucao_nova():
    tipo = request.form.get('tipo', 'completo')
    if tipo not in ('completo', 'nfe_entradas', 'nfe_saidas', 'nfse', 'nfce', 'ciencia'):
        tipo = 'completo'
    worker.enfileirar(tipo, origem='manual', user_id=session.get('uid'))
    flash('Execução "%s" enfileirada.' % tipo, 'ok'); return redirect(url_for('execucoes'))

@app.route('/jobs/<int:jid>/cancelar', methods=['POST'])
@admin_required
def job_cancelar(jid):
    with models.con() as c:
        r = c.execute("UPDATE jobs SET status='cancelado', terminado=? WHERE id=? AND status='fila'",
                      (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), jid)).rowcount
    flash('Job cancelado.' if r else 'Não deu para cancelar (já iniciou ou terminou).', 'ok' if r else 'erro')
    return redirect(url_for('execucoes'))

# ---- Protocolos da Ciencia 210210 (admin) ----
@app.route('/protocolos')
@admin_required
def protocolos():
    with models.con() as c:
        rows = c.execute('''SELECT cd.cnpj, cd.chNFe, cd.cStat, cd.nProt, cd.quando, e.nome
                            FROM ciencia_dada cd LEFT JOIN empresas e ON e.cnpj=cd.cnpj
                            ORDER BY cd.quando DESC LIMIT 500''').fetchall()
        total = c.execute('SELECT COUNT(*) FROM ciencia_dada').fetchone()[0]
        com_prot = c.execute("SELECT COUNT(*) FROM ciencia_dada WHERE nProt IS NOT NULL AND nProt!=''").fetchone()[0]
    return render_template('protocolos.html', rows=rows, total=total, com_prot=com_prot)

# ---- Usuarios (admin) ----
@app.route('/usuarios')
@admin_required
def usuarios():
    with models.con() as c:
        us = c.execute('SELECT * FROM usuarios ORDER BY papel, nome').fetchall()
        cont = {r['user_id']: r['n'] for r in
                c.execute('SELECT user_id, COUNT(*) n FROM usuario_empresas GROUP BY user_id').fetchall()}
    return render_template('usuarios.html', usuarios=us, cont=cont)

@app.route('/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def usuario_novo():
    if request.method == 'POST':
        return _salvar_usuario(None)
    with models.con() as c:
        emp = c.execute('SELECT id,cnpj,nome FROM empresas ORDER BY nome').fetchall()
    return render_template('usuario_form.html', u=None, empresas=emp, sel=set())

@app.route('/usuarios/<int:uid>/editar', methods=['GET', 'POST'])
@admin_required
def usuario_editar(uid):
    with models.con() as c:
        u = c.execute('SELECT * FROM usuarios WHERE id=?', (uid,)).fetchone()
        if not u:
            abort(404)
        sel = set(r['empresa_id'] for r in
                  c.execute('SELECT empresa_id FROM usuario_empresas WHERE user_id=?', (uid,)).fetchall())
        emp = c.execute('SELECT id,cnpj,nome FROM empresas ORDER BY nome').fetchall()
    if request.method == 'POST':
        return _salvar_usuario(uid)
    return render_template('usuario_form.html', u=u, empresas=emp, sel=sel)

def _salvar_usuario(uid):
    f = request.form
    login = (f.get('login') or '').strip()
    nome = f.get('nome') or login
    papel = 'admin' if f.get('papel') == 'admin' else 'operador'
    ativo = 1 if f.get('ativo') else 0
    todas = 1 if (f.get('todas_empresas') or papel == 'admin') else 0
    empresas_sel = [int(x) for x in request.form.getlist('empresas')]
    senha = f.get('senha') or ''
    with models.con() as c:
        if uid is None:
            if not login or not senha:
                flash('Login e senha são obrigatórios.', 'erro'); return redirect(url_for('usuario_novo'))
            if c.execute('SELECT 1 FROM usuarios WHERE login=?', (login,)).fetchone():
                flash('Já existe usuário com esse login.', 'erro'); return redirect(url_for('usuarios'))
            cur = c.execute('INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) '
                            'VALUES(?,?,?,?,?,?,?)',
                            (login, generate_password_hash(senha), nome, papel, todas, ativo,
                             datetime.now().isoformat(timespec='seconds')))
            uid = cur.lastrowid
        else:
            c.execute('UPDATE usuarios SET nome=?,papel=?,todas_empresas=?,ativo=? WHERE id=?',
                      (nome, papel, todas, ativo, uid))
            if senha:
                c.execute('UPDATE usuarios SET senha_hash=? WHERE id=?', (generate_password_hash(senha), uid))
        c.execute('DELETE FROM usuario_empresas WHERE user_id=?', (uid,))
        if not todas:
            for eid in empresas_sel:
                c.execute('INSERT OR IGNORE INTO usuario_empresas(user_id,empresa_id) VALUES(?,?)', (uid, eid))
    flash('Usuário salvo.', 'ok'); return redirect(url_for('usuarios'))

@app.route('/usuarios/<int:uid>/excluir', methods=['POST'])
@admin_required
def usuario_excluir(uid):
    if uid == session.get('uid'):
        flash('Você não pode excluir o próprio usuário.', 'erro'); return redirect(url_for('usuarios'))
    with models.con() as c:
        admins = c.execute("SELECT COUNT(*) FROM usuarios WHERE papel='admin' AND ativo=1").fetchone()[0]
        alvo = c.execute('SELECT papel FROM usuarios WHERE id=?', (uid,)).fetchone()
        if alvo and alvo['papel'] == 'admin' and admins <= 1:
            flash('Não é possível excluir o último administrador.', 'erro'); return redirect(url_for('usuarios'))
        c.execute('DELETE FROM usuario_empresas WHERE user_id=?', (uid,))
        c.execute('DELETE FROM usuarios WHERE id=?', (uid,))
    flash('Usuário excluído.', 'ok'); return redirect(url_for('usuarios'))

# ---- Certificados ----
@app.route('/cert/upload', methods=['POST'])
@admin_required
def cert_upload():
    fobj = request.files.get('cert'); senha = request.form.get('senha', '')
    if not fobj or not fobj.filename:
        flash('Selecione um .pfx', 'erro'); return redirect(url_for('certificados'))
    dest = os.path.join(CERT_DIR, secure_filename(fobj.filename)); fobj.save(dest)
    try:
        _, cert, _ = certs.load_pfx(dest, senha)
        cnpj, tipo, nome, uf, val = certs.cert_info(cert)
    except Exception as e:
        flash('Certificado inválido: %s' % str(e)[:50], 'erro'); return redirect(url_for('certificados'))
    if tipo != 'CNPJ':
        flash('Não é e-CNPJ.', 'erro'); return redirect(url_for('certificados'))
    with models.con() as c:
        ex = c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
        if ex:
            c.execute('UPDATE empresas SET arquivo=?,senha=?,senha_ok=1,validade=?,uf=?,cuf=? WHERE id=?',
                      (dest, senha, val, uf, certs.UF_COD.get(uf, '35'), ex['id']))
            flash('Certificado vinculado a %s.' % nome, 'ok')
        else:
            c.execute('INSERT INTO empresas(cnpj,nome,uf,cuf,arquivo,senha,senha_ok,validade,ativo,criado) '
                      'VALUES(?,?,?,?,?,?,1,?,1,?)', (cnpj, nome, uf, certs.UF_COD.get(uf, '35'),
                       dest, senha, val, datetime.now().isoformat(timespec='seconds')))
            flash('Empresa criada pelo certificado: %s.' % nome, 'ok')
    return redirect(url_for('certificados'))

@app.route('/cert/scan', methods=['POST'])
@admin_required
def cert_scan():
    pasta = (request.form.get('pasta') or '').strip()
    if not os.path.isdir(pasta):
        flash('Pasta não encontrada.', 'erro'); return redirect(url_for('certificados'))
    n = 0
    with models.con() as c:
        for raiz, _, arqs in os.walk(pasta):
            for a in arqs:
                if not a.lower().endswith(('.pfx', '.p12')): continue
                senha = certs.guess_password(a)
                if not senha: continue
                try:
                    _, cert, _ = certs.load_pfx(os.path.join(raiz, a), senha)
                    cnpj, tipo, nome, uf, val = certs.cert_info(cert)
                except Exception: continue
                if tipo != 'CNPJ': continue
                ex = c.execute('SELECT id FROM empresas WHERE cnpj=?', (cnpj,)).fetchone()
                if ex:
                    c.execute('UPDATE empresas SET arquivo=?,senha=?,senha_ok=1,validade=?,uf=?,cuf=? WHERE id=?',
                              (os.path.join(raiz, a), senha, val, uf, certs.UF_COD.get(uf, '35'), ex['id'])); n += 1
    flash('Vinculados %d certificados às empresas da base.' % n, 'ok')
    return redirect(url_for('certificados'))

@app.route('/cert/office', methods=['POST'])
@admin_required
def cert_office():
    fobj = request.files.get('ocert'); path = (request.form.get('opath') or '').strip(); senha = request.form.get('osenha', '')
    if fobj and fobj.filename:
        path = os.path.join(CERT_DIR, secure_filename(fobj.filename)); fobj.save(path)
    if not path or not os.path.isfile(path):
        flash('Informe o certificado do escritório.', 'erro'); return redirect(url_for('certificados'))
    try:
        _, cert, _ = certs.load_pfx(path, senha); cnpj, tipo, nome, uf, val = certs.cert_info(cert)
    except Exception as e:
        flash('Certificado do escritório inválido: %s' % str(e)[:50], 'erro'); return redirect(url_for('certificados'))
    models.set_param('office_cnpj', cnpj); models.set_param('office_arquivo', path)
    models.set_param('office_senha', senha); models.set_param('office_cuf', certs.UF_COD.get(uf, '35'))
    flash('Escritório configurado: %s. Saídas NFe habilitadas.' % nome, 'ok')
    return redirect(url_for('certificados'))

# ---- Execução (fila de jobs em background) ----
@app.route('/run/nfe/entradas', methods=['POST'])
@admin_required
def run_nfe_entradas():
    worker.enfileirar('nfe_entradas', origem='manual', user_id=session.get('uid'))
    flash('Entradas NF-e enfileiradas — acompanhe o status no topo.', 'ok'); return redirect(url_for('dashboard'))

@app.route('/run/nfe/saidas', methods=['POST'])
@admin_required
def run_nfe_saidas():
    worker.enfileirar('nfe_saidas', origem='manual', user_id=session.get('uid'))
    flash('Saídas NF-e enfileiradas.', 'ok'); return redirect(url_for('dashboard'))

@app.route('/run/nfse', methods=['POST'])
@admin_required
def run_nfse():
    worker.enfileirar('nfse', origem='manual', user_id=session.get('uid'))
    flash('NFS-e enfileirada.', 'ok'); return redirect(url_for('dashboard'))

@app.route('/run/nfce', methods=['POST'])
@admin_required
def run_nfce():
    worker.enfileirar('nfce', origem='manual', user_id=session.get('uid'))
    flash('NFC-e (SP) enfileirada — lista e baixa de todas as empresas marcadas.', 'ok'); return redirect(url_for('dashboard'))

@app.route('/status')
@login_req
def status_json():
    rodando, fila, recentes = worker.status()
    # 'agora' vem do servidor: o tempo decorrido e calculado com as DUAS pontas no
    # relogio do servidor. Sem isso, VPS em UTC + navegador em BRT dariam "-3h".
    return {'rodando': (dict(rodando) if rodando else None), 'fila': fila,
            'agora': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'recentes': [dict(r) for r in recentes]}

# mapeia tipo (do filtro) -> subpastas fisicas; None = todas
TIPOS = {
    'NFe':  {'todos': None, 'entrada': ['01_entrada', '02_resumo', '03_eventos'], 'saida': ['04_saida']},
    'NFSe': {'todos': None, 'tomado': ['01_tomado'], 'prestado': ['02_prestado']},
    'NFCe': {'todos': None, 'venda': ['01_venda']},
}

def _disponivel(cnpj):
    """{'NFe': {comp}, 'NFSe': {comp}, 'NFCe': {comp}} das competencias com XML."""
    base = os.path.join(SAIDA, cnpj); res = {'NFe': {}, 'NFSe': {}, 'NFCe': {}}
    if os.path.isdir(base):
        for comp in sorted(os.listdir(base), reverse=True):
            if not re.match(r'\d{4}-\d{2}$', comp): continue
            for doc in ('NFe', 'NFSe', 'NFCe'):
                if os.path.isdir(os.path.join(base, comp, doc)):
                    res[doc][comp] = True
    return res

@app.route('/downloads')
@login_req
def downloads():
    aplicado = request.args.get('aplicado')
    q = (request.args.get('q') or '').strip().lower()
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    doc_f = (request.args.get('doc') or '').strip()  # '' | NFe | NFSe | NFCe
    if not aplicado and not ano and not mes and not q:  # padrao Fiscal: mes anterior
        ano, mes = _comp_anterior()
    def _casa(comp):
        return (not ano or comp[:4] == ano) and (not mes or comp[5:7] == mes)
    with models.con() as c:
        emp = _scope(c.execute('SELECT * FROM empresas ORDER BY nome').fetchall())
    itens = []
    anos = set(); n_zip = 0
    for e in emp:
        disp = _disponivel(e['cnpj'])
        for d in ('NFe', 'NFSe', 'NFCe'):
            for comp in disp[d]:
                anos.add(comp[:4])
        if q and q not in (e['nome'] or '').lower() and q not in (e['cnpj'] or ''):
            continue
        nfe = sorted([c2 for c2 in disp['NFe'] if _casa(c2)], reverse=True) if doc_f in ('', 'NFe') else []
        nfse = sorted([c2 for c2 in disp['NFSe'] if _casa(c2)], reverse=True) if doc_f in ('', 'NFSe') else []
        nfce = sorted([c2 for c2 in disp['NFCe'] if _casa(c2)], reverse=True) if doc_f in ('', 'NFCe') else []
        if nfe or nfse or nfce:
            n_zip += bool(nfe) + bool(nfse) + bool(nfce)
            itens.append({'e': e, 'nfe': nfe, 'nfse': nfse, 'nfce': nfce})
    return render_template('downloads.html', itens=itens, q=request.args.get('q', ''),
                           ano=ano, mes=mes, doc_f=doc_f, anos=sorted(anos, reverse=True),
                           n_emp=len(itens), n_zip=n_zip)

# ---- Download por competencia + tipo (ZIP) ----
@app.route('/download')
@login_req
def download():
    cnpj = request.args.get('cnpj', ''); doc = request.args.get('doc', 'NFe')
    comp = request.args.get('comp', 'TODAS'); tipo = request.args.get('tipo', 'todos')
    ano = request.args.get('ano', ''); mes = request.args.get('mes', '')
    if not _pode_ver_cnpj(cnpj):
        abort(403)
    subs = TIPOS.get(doc, {}).get(tipo)          # None = todas as subpastas
    base = os.path.join(SAIDA, cnpj)
    comps = ([comp] if comp != 'TODAS' else
             sorted([d for d in os.listdir(base) if re.match(r'\d{4}-\d{2}$', d)]) if os.path.isdir(base) else [])
    if comp == 'TODAS':  # respeita o filtro Ano/Mês da tela
        comps = [cp for cp in comps if (not ano or cp[:4] == ano) and (not mes or cp[5:7] == mes)]
    buf = io.BytesIO(); n = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for cp in comps:
            docdir = os.path.join(base, cp, doc)
            if not os.path.isdir(docdir): continue
            for sub in os.listdir(docdir):
                subdir = os.path.join(docdir, sub)
                if not os.path.isdir(subdir) or (subs and sub not in subs): continue
                for a in os.listdir(subdir):
                    full = os.path.join(subdir, a)
                    if os.path.isfile(full):
                        z.write(full, arcname='%s/%s/%s/%s' % (cp, doc, sub, a)); n += 1
    if n == 0:
        flash('Nenhum XML para o filtro escolhido.', 'erro'); return redirect(url_for('downloads'))
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name='%s_%s_%s_%s.zip' % (doc, tipo, comp, cnpj))

# ---- Exclusao manual de XML (admin) ----
@app.route('/xml/excluir', methods=['POST'])
@admin_required
def xml_excluir():
    cnpj = request.form.get('cnpj', ''); doc = request.form.get('doc', 'NFe')
    comp = request.form.get('comp', 'TODAS'); tipo = request.form.get('tipo', 'todos')
    ano = request.form.get('ano', ''); mes = request.form.get('mes', '')
    if not _pode_ver_cnpj(cnpj):
        abort(403)
    subs = TIPOS.get(doc, {}).get(tipo)
    base = os.path.join(SAIDA, cnpj)
    comps = ([comp] if comp != 'TODAS' else
             sorted([d for d in os.listdir(base) if re.match(r'\d{4}-\d{2}$', d)]) if os.path.isdir(base) else [])
    if comp == 'TODAS':
        comps = [cp for cp in comps if (not ano or cp[:4] == ano) and (not mes or cp[5:7] == mes)]
    n = 0
    for cp in comps:
        docdir = os.path.join(base, cp, doc)
        if not os.path.isdir(docdir):
            continue
        for sub in os.listdir(docdir):
            subdir = os.path.join(docdir, sub)
            if not os.path.isdir(subdir) or (subs and sub not in subs):
                continue
            for a in os.listdir(subdir):
                full = os.path.join(subdir, a)
                if os.path.isfile(full):
                    try:
                        os.remove(full); n += 1
                    except Exception:
                        pass
            try:  # remove subpasta se ficou vazia
                if not os.listdir(subdir):
                    os.rmdir(subdir)
            except Exception:
                pass
    flash('%d XML excluídos (%s · %s · %s). O contador de NSU foi preservado.' % (n, doc, comp, tipo), 'ok')
    return redirect(url_for('downloads'))

# Colunas de MOVIMENTO da conferencia (tipos em colunas). ultimo campo True = tributado (saida)
_COLS_CONF = [
    ('entrada',  'NFe',  '01_entrada',  'Entradas',        'compras',    '📥', False),
    ('saida',    'NFe',  '04_saida',    'Saídas',          'vendas',     '📤', True),
    ('tomado',   'NFSe', '01_tomado',   'Serv. tomados',   'contratados', '🛒', False),
    ('prestado', 'NFSe', '02_prestado', 'Serv. prestados', 'prestados',  '🧾', True),
    ('venda',    'NFCe', '01_venda',    'Vendas NFC-e',    'varejo',     '🛍️', True),
]

# ---- Etapa 9/10: Conferencia fiscal (qtd + valor) com filtros Ano/Mes/Empresa ----
@app.route('/fiscal/conferencia')
@login_req
def fiscal_conferencia():
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome FROM empresas ORDER BY nome').fetchall())
    todos_cnpjs = [e['cnpj'] for e in emp_rows]
    cnome = {e['cnpj']: e['nome'] for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    emp_sel = (request.args.get('empresa') or '').strip()  # cnpj
    incluir = bool(request.args.get('incluir_canceladas'))
    # Padrao do Fiscal: mes ANTERIOR (apuracao). So na 1a abertura (sem interacao).
    if not aplicado and not ano and not mes:
        ano, mes = _comp_anterior()
    cnpjs = [emp_sel] if (emp_sel and emp_sel in todos_cnpjs) else todos_cnpjs
    ano_mes = ('%s-%s' % (ano, mes)) if (ano and mes) else None
    regs = conferencia.conferencia(cnpjs, ano_mes=ano_mes, incluir_canceladas=incluir)
    if ano and not mes:
        regs = [r for r in regs if r['competencia'][:4] == ano]
    elif mes and not ano:
        regs = [r for r in regs if r['competencia'][5:7] == mes]
    # exportacao CSV (todos os subs, flat)
    if request.args.get('formato') == 'csv':
        import csv
        buf = io.StringIO(); w = csv.writer(buf, delimiter=';')
        w.writerow(['CNPJ', 'Empresa', 'Competencia', 'Tipo', 'Subpasta', 'Qtd', 'Valor', 'Canceladas'])
        for r in sorted(regs, key=lambda x: (cnome.get(x['cnpj'], ''), x['competencia'])):
            w.writerow([r['cnpj'], cnome.get(r['cnpj'], ''), r['competencia'], r['modelo'],
                        r['sub'], r['qtd'], ('%.2f' % r['valor']).replace('.', ','), r['canceladas']])
        out = io.BytesIO(('﻿' + buf.getvalue()).encode('utf-8'))
        return send_file(out, mimetype='text/csv', as_attachment=True,
                         download_name='conferencia_%s%s.csv' % (ano or 'tudo', mes and ('-' + mes) or ''))
    # MATRIZ empresa x tipo-de-movimento (colunas)
    sub2col = {(doc, sub): key for key, doc, sub, _l, _s, _e, _t in _COLS_CONF}
    col_tot = {key: {'qtd': 0, 'valor': 0.0} for key, *_ in _COLS_CONF}
    matriz = {}
    resumo = {'qtd': 0, 'valor': 0.0, 'canceladas': 0}
    for r in regs:
        resumo['canceladas'] += r['canceladas']
        key = sub2col.get((r['modelo'], r['sub']))
        if not key:
            continue  # resumos/eventos nao entram na apuracao
        m = matriz.setdefault(r['cnpj'], {
            'nome': cnome.get(r['cnpj'], ''),
            'cols': {k: {'qtd': 0, 'valor': 0.0, 'canc': 0} for k, *_ in _COLS_CONF},
            'tot_qtd': 0, 'tot_valor': 0.0})
        cell = m['cols'][key]
        cell['qtd'] += r['qtd']; cell['valor'] += r['valor']; cell['canc'] += r['canceladas']
        m['tot_qtd'] += r['qtd']; m['tot_valor'] += r['valor']
        col_tot[key]['qtd'] += r['qtd']; col_tot[key]['valor'] += r['valor']
        resumo['qtd'] += r['qtd']; resumo['valor'] += r['valor']
    matriz = dict(sorted(matriz.items(), key=lambda kv: kv[1]['nome'].lower()))
    resumo['valor'] = round(resumo['valor'], 2); resumo['empresas'] = len(matriz)
    anos = sorted({c2[:4] for c2 in conferencia.competencias_disponiveis(todos_cnpjs)}, reverse=True)
    return render_template('fiscal_conferencia.html', matriz=matriz, col_tot=col_tot,
                           cols=_COLS_CONF, anos=anos, empresas=emp_rows,
                           ano=ano, mes=mes, empresa_sel=emp_sel,
                           incluir_canceladas=incluir, resumo=resumo,
                           comp_label=('%s/%s' % (mes, ano) if (ano and mes) else
                                       (ano or 'Todas as competências')))

# ---- Etapa 9: Auditoria de numeracao (quebras de nNF) - apenas NFes EMITIDAS ----
@app.route('/fiscal/auditoria')
@login_req
def fiscal_auditoria():
    """Auditoria por empresa CLIENTE: somente NF EMITIDAS pela propria empresa.
       - NF-e saidas (04_saida) e NFC-e vendas (01_venda): audita por nNF.
       - NFS-e prestadas (02_prestado): audita por nNF (modelo municipal).
       Entradas (compras) e tomadas (servicos contratados) NAO entram.
       Canceladas CONTAM na sequencia (recomendacao contabil)."""
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome FROM empresas ORDER BY nome').fetchall())
    todos_cnpjs = [e['cnpj'] for e in emp_rows]
    cnome = {e['cnpj']: e['nome'] for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    emp_sel = (request.args.get('empresa') or '').strip()
    mostrar = request.args.get('mostrar') or 'problemas'   # 'problemas' | 'tudo'
    contar_canceladas = not bool(request.args.get('excluir_canceladas_da_sequencia'))
    modelos = request.args.getlist('modelo') or ['NFe', 'NFCe', 'NFSe']
    if not aplicado and not ano and not mes:   # padrao Fiscal: mes anterior
        ano, mes = _comp_anterior()
    cnpjs = [emp_sel] if (emp_sel and emp_sel in todos_cnpjs) else todos_cnpjs
    ano_mes = ('%s-%s' % (ano, mes)) if (ano and mes) else None
    regs = conferencia.auditoria_numeracao(cnpjs, ano_mes=ano_mes,
                                           modelos=set(modelos),
                                           contar_canceladas=contar_canceladas)
    if ano and not mes:
        regs = [r for r in regs if r['competencia'][:4] == ano]
    elif mes and not ano:
        regs = [r for r in regs if r['competencia'][5:7] == mes]
    for r in regs:
        r['nome'] = cnome.get(r['cnpj'], '')
    # agrupa por empresa (com quebra primeiro)
    por_empresa = {}
    for r in regs:
        d = por_empresa.setdefault(r['cnpj'], {'nome': cnome.get(r['cnpj'], ''), 'linhas': [],
                                               'n_quebra': 0, 'faltam': 0})
        d['linhas'].append(r)
        if r['quebra']:
            d['n_quebra'] += 1; d['faltam'] += r['faltam']
    # ordena: empresas com quebra primeiro, depois por nome
    ordenadas = sorted(por_empresa.items(), key=lambda kv: (kv[1]['n_quebra'] == 0, kv[1]['nome'].lower()))
    resumo = {
        'empresas': len(por_empresa),
        'com_quebra': sum(1 for _cn, d in ordenadas if d['n_quebra'] > 0),
        'series_quebra': sum(1 for r in regs if r['quebra']),
        'faltam': sum(r['faltam'] for r in regs),
    }
    comps = conferencia.competencias_disponiveis(todos_cnpjs)
    anos = sorted({c2[:4] for c2 in comps}, reverse=True)
    return render_template('fiscal_auditoria.html', ordenadas=ordenadas, resumo=resumo,
                           anos=anos, empresas=emp_rows, ano=ano, mes=mes, empresa_sel=emp_sel,
                           mostrar=mostrar, contar_canceladas=contar_canceladas,
                           modelos_selecionados=set(modelos),
                           comp_label=('%s/%s' % (mes, ano) if (ano and mes) else
                                       (ano or 'Todas as competências')))

# ---- Etapa 12: Faturamento por CFOP (isola a base de tributacao) ----
@app.route('/fiscal/faturamento')
@login_req
def fiscal_faturamento():
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome FROM empresas ORDER BY nome').fetchall())
    todos = [e['cnpj'] for e in emp_rows]
    cnome = {e['cnpj']: e['nome'] for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    emp_sel = (request.args.get('empresa') or '').strip()
    if not aplicado and not ano and not mes:
        ano, mes = _comp_anterior()
    cnpjs = [emp_sel] if (emp_sel and emp_sel in todos) else todos
    raw = conferencia.faturamento_cfop(cnpjs, ano=ano, mes=mes)
    # monta linhas por empresa + totais, separando base x fora-da-base
    linhas = []
    tot = {'fat': 0.0, 'fat_q': 0, 'fora': 0.0, 'fora_q': 0, 'compra': 0.0, 'compra_q': 0}
    for cn, d in raw.items():
        emp = {'cnpj': cn, 'nome': cnome.get(cn, ''),
               'fat': 0.0, 'fat_q': 0, 'fora': 0.0, 'fora_q': 0, 'compra': 0.0, 'compra_q': 0,
               'saida_grp': [], 'entrada_grp': []}
        for grp, v in sorted(d.get('saida', {}).items(), key=lambda kv: -kv[1]['valor']):
            base = cfop.GRUPOS[grp][1]
            emp['saida_grp'].append({'grp': grp, 'rotulo': cfop.GRUPOS[grp][0], 'base': base,
                                     'qtd': v['qtd'], 'valor': v['valor'], 'cfops': v['cfops']})
            if base:
                emp['fat'] += v['valor']; emp['fat_q'] += v['qtd']
            else:
                emp['fora'] += v['valor']; emp['fora_q'] += v['qtd']
        for grp, v in sorted(d.get('entrada', {}).items(), key=lambda kv: -kv[1]['valor']):
            base = cfop.GRUPOS[grp][1]
            emp['entrada_grp'].append({'grp': grp, 'rotulo': cfop.GRUPOS[grp][0], 'base': base,
                                       'qtd': v['qtd'], 'valor': v['valor'], 'cfops': v['cfops']})
            if base:
                emp['compra'] += v['valor']; emp['compra_q'] += v['qtd']
        emp['fat'] = round(emp['fat'], 2); emp['fora'] = round(emp['fora'], 2); emp['compra'] = round(emp['compra'], 2)
        tot['fat'] += emp['fat']; tot['fat_q'] += emp['fat_q']
        tot['fora'] += emp['fora']; tot['fora_q'] += emp['fora_q']
        tot['compra'] += emp['compra']; tot['compra_q'] += emp['compra_q']
        linhas.append(emp)
    linhas.sort(key=lambda e: -e['fat'])
    for k in ('fat', 'fora', 'compra'):
        tot[k] = round(tot[k], 2)
    anos = sorted({c2[:4] for c2 in conferencia.competencias_disponiveis(todos)}, reverse=True)
    return render_template('fiscal_faturamento.html', linhas=linhas, tot=tot, anos=anos,
                           empresas=emp_rows, ano=ano, mes=mes, empresa_sel=emp_sel,
                           comp_label=('%s/%s' % (mes, ano) if (ano and mes) else
                                       (ano or mes or 'Todas as competências')))

# ---- Etapa 13+14: Economia Fiscal (produtos monofasicos) ----
@app.route('/fiscal/economia')
@login_req
def fiscal_economia():
    """Dois modos:
       - modo=venda (default Etapa 13): usa as SAIDAS reais (04_saida/NFCe 01_venda) do mes escolhido.
       - modo=estimativa (Etapa 14): usa as COMPRAS REAIS dos ultimos 12 meses rolling
         para estimar a % monofasica. Receita: PGDAS > venda propria > compras*markup.
       Tudo auditavel por NCM, base legal LC 123/2006 art. 18 §4o-A + Res CGSN 140/2018."""
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome, simples_anexo, simples_rbt12 '
                                    'FROM empresas ORDER BY nome').fetchall())
    todos = [e['cnpj'] for e in emp_rows]
    emeta = {e['cnpj']: e for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    emp_sel = (request.args.get('empresa') or '').strip()
    ocultar = bool(request.args.get('ocultar'))
    modo = (request.args.get('modo') or 'venda').strip()
    if modo not in ('venda', 'estimativa'): modo = 'venda'
    janela = int(request.args.get('janela') or 12)
    if not (5 <= janela <= 24): janela = 12
    if not aplicado and not ano and not mes and modo == 'venda':
        ano, mes = _comp_anterior()
    cnpjs = [emp_sel] if (emp_sel and emp_sel in todos) else todos

    if modo == 'estimativa':
        raw = conferencia.economia_mono_estimada_compras(cnpjs, janela=janela, markup=1.5)
    else:
        raw = conferencia.economia_monofasico(cnpjs, ano=ano, mes=mes)

    linhas = []
    tot = {'economia': 0.0, 'fat_mono': 0.0, 'fat_venda': 0.0, 'receita': 0.0,
           'n_benef': 0, 'sem_rbt12': 0, 'com_compras': 0}
    for cn, d in raw.items():
        e = emeta.get(cn)
        anexo = (e['simples_anexo'] if e and e['simples_anexo'] else '')
        # RBT12: recibos PGDAS-D importados tem prioridade; cadastro e' o fallback
        rb = conferencia.rbt12_efetivo(cn, (e['simples_rbt12'] if e else 0) or 0)
        rbt12 = rb['rbt12']; rbt12_fonte = rb['fonte']
        # modo venda usa fat_mono, modo estimativa usa receita*pct
        if modo == 'estimativa':
            receita_base = d['receita']
            pct_mono = d['pct_mono']
            fat_mono_equiv = receita_base * (pct_mono / 100.0)
            economia = d['economia_estimada']
            ec_max = d['economia_maxima']
            row = {
                'cnpj': cn, 'id': (e['id'] if e else 0), 'nome': (e['nome'] if e else ''),
                'anexo': (anexo or d.get('anexo') or '-'),
                'rbt12': d.get('rbt12', rbt12),
                'rbt12_fonte': d.get('rbt12_fonte', rbt12_fonte),
                'ef': d['aliquota_efetiva'], 'faixa': d['faixa'],
                'fat_venda': receita_base, 'fat_mono': fat_mono_equiv,
                'pct': pct_mono,
                'economia': economia, 'ec_max': ec_max,
                'por_categoria': d.get('por_categoria', {}),
                'total_comprado': d.get('total_comprado', 0),
                'janela_meses': d.get('janela_meses', 0),
                'qtd_compras': d.get('qtd_compras', 0),
                'qtd_vendas_proprias': d.get('qtd_vendas_proprias', 0),
                'receita_fonte': d.get('receita_fonte', '-'),
                'receita_periodo': d.get('receita_periodo', '-'),
                'notas_mono': 0,
            }
        else:
            anexo = anexo or 'I'
            economia, ef, faixa = monofasico.economia_pis_cofins(d['fat_mono'], anexo, rbt12)
            ec100, _, _ = monofasico.economia_pis_cofins(d['fat_venda'], anexo, rbt12)
            row = dict(d); row.update({'cnpj': cn, 'nome': (e['nome'] if e else ''),
                                       'id': (e['id'] if e else 0),
                                       'anexo': anexo, 'rbt12': rbt12,
                                       'rbt12_fonte': rbt12_fonte,
                                       'ef': ef, 'faixa': faixa,
                                       'economia': economia,
                                       'ec_max': ec100,
                                       'receita_fonte': 'saida_real',
                                       'receita_periodo': '%s/%s' % (mes, ano) if (ano and mes) else 'mes',
                                       'total_comprado': 0, 'janela_meses': 1,
                                       'qtd_compras': 0, 'qtd_vendas_proprias': 0})
        tot['economia'] += row['economia']
        tot['fat_mono'] += row['fat_mono']
        tot['fat_venda'] += row['fat_venda']
        tot['receita'] += row.get('fat_venda', 0)
        if row['fat_mono'] > 0 or row.get('pct', 0) > 0:
            tot['n_benef'] += 1
            if not rbt12:
                tot['sem_rbt12'] += 1
        if row.get('total_comprado', 0) > 0:
            tot['com_compras'] += 1
        linhas.append(row)
    if ocultar:
        if modo == 'estimativa':
            linhas = [r for r in linhas if r.get('total_comprado', 0) > 0]
        else:
            linhas = [r for r in linhas if r['fat_mono'] > 0]
    linhas.sort(key=lambda r: (-(r['economia'] or 0), -(r.get('total_comprado') or r['fat_mono'] or 0)))
    for k in ('economia', 'fat_mono', 'fat_venda', 'receita'):
        tot[k] = round(tot[k], 2)
    anos = sorted({c2[:4] for c2 in conferencia.competencias_disponiveis(todos)}, reverse=True)
    return render_template('fiscal_economia.html', linhas=linhas, tot=tot, anos=anos,
                           empresas=emp_rows, ano=ano, mes=mes, empresa_sel=emp_sel,
                           ocultar=ocultar, modo=modo, janela=janela,
                           comp_label=('%s/%s' % (mes, ano) if (ano and mes) else
                                       (ano or mes or
                                        ('%d meses (rolling)' % janela if modo == 'estimativa'
                                         else 'Todas as competências'))))

# ---- Etapa 15: Importar Recibos PGDAS-D (Extrato do Simples Nacional) ----
# O "Extrato do Simples" eh o PDF do Recibo de Entrega da Declaracao PGDAS-D
# emitido pelo portal do Simples Nacional ou pelo programa PGDAS-Download.
# Contem: CNPJ, periodo (MM/AAAA), receita bruta total, anexo, RBT12, DAS devido.
# Parser extrai esses campos e grava em pgdas_recibos (ja usado pela Etapa 14).
@app.route('/importar/pgdas', methods=['GET', 'POST'])
@admin_required
def importar_pgdas():
    from engines import pgdas as pg
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome FROM empresas ORDER BY nome').fetchall())

    if request.method == 'POST':
        eid = int(request.form.get('empresa_id') or 0)
        arq = request.files.get('arquivo')
        if not eid or not arq or not arq.filename:
            flash('Selecione empresa e arquivo.', 'erro')
            return redirect(url_for('importar_pgdas'))
        conteudo = arq.read()
        rec = pg.parse_recibo(conteudo)
        if not rec:
            flash('Nao consegui ler o PDF (formato nao reconhecido ou campos ausentes). '
                  'Verifique se eh um Recibo PGDAS-D oficial.', 'erro')
            return redirect(url_for('importar_pgdas'))
        cnpj_doc = rec['cnpj']
        # validar CNPJ: o do PDF deve bater com o da empresa selecionada
        with models.con() as c:
            emp_cnpj = c.execute('SELECT cnpj FROM empresas WHERE id=?', (eid,)).fetchone()
        if emp_cnpj and emp_cnpj['cnpj'] != cnpj_doc:
            flash(f'Atencao: CNPJ do PDF ({cnpj_doc}) difere da empresa selecionada ({emp_cnpj["cnpj"]}). '
                  f'Selecione a empresa correta.', 'erro')
            return redirect(url_for('importar_pgdas'))
        # insere com dedup
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        hl = pg.hash_recibo(cnpj_doc, rec['ano'], rec['mes'], rec['receita_total'])
        with models.con() as c:
            c.execute("INSERT OR REPLACE INTO pgdas_recibos"
                      "(cnpj,ano,mes,receita_total,anexo,arquivo,parsed_em,hash_linha) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (cnpj_doc, rec['ano'], rec['mes'], rec['receita_total'],
                       rec.get('anexo') or '', arq.filename[:200], agora, hl))
            # se trouxe RBT12 e a empresa nao tinha, atualiza o cadastro
            if rec.get('rbt12'):
                c.execute('UPDATE empresas SET simples_rbt12=? WHERE id=? AND '
                          '(simples_rbt12 IS NULL OR simples_rbt12=0)',
                          (rec['rbt12'], eid))
            if rec.get('anexo'):
                c.execute('UPDATE empresas SET simples_anexo=? WHERE id=? AND '
                          '(simples_anexo IS NULL OR simples_anexo="")',
                          (rec['anexo'], eid))
        flash(f'PGDAS {rec["mes"]:02d}/{rec["ano"]} importado: receita R$ {rec["receita_total"]:,.2f} '
              f'(anexo {rec.get("anexo") or "?"}, RBT12 R$ {(rec.get("rbt12") or 0):,.0f}). '
              f'Cadastro da empresa atualizado.'.replace(',', 'X').replace('.', ',').replace('X', '.'),
              'ok')
        return redirect(url_for('importar_pgdas'))

    # GET - lista PGDAS importados
    eid_filtro = int(request.args.get('empresa_id') or 0)
    recibos = []
    if eid_filtro:
        with models.con() as c:
            emp_cnpj = c.execute('SELECT cnpj FROM empresas WHERE id=?', (eid_filtro,)).fetchone()
        if emp_cnpj:
            with models.con() as c:
                for r in c.execute('SELECT ano, mes, receita_total, anexo, arquivo, parsed_em '
                                   'FROM pgdas_recibos WHERE cnpj=? ORDER BY ano DESC, mes DESC',
                                   (emp_cnpj['cnpj'],)).fetchall():
                    recibos.append(r)
    return render_template('importar_pgdas.html', empresas=emp_rows, eid_filtro=eid_filtro,
                           recibos=recibos)


# ---- Etapa 15: Mensuracao 3-fontes (vendas + compras + extrato) ----
@app.route('/fiscal/economia/mensuracao')
@login_req
def fiscal_economia_mensuracao():
    """Tela de mensuracao: cruza vendas + compras + extrato (3 fontes)."""
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome, simples_anexo, simples_rbt12 '
                                    'FROM empresas ORDER BY nome').fetchall())
    todos = [e['cnpj'] for e in emp_rows]
    emeta = {e['cnpj']: e for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip() or str(datetime.now().year)
    mes = (request.args.get('mes') or '').strip() or '%02d' % datetime.now().month
    ano_i = int(ano); mes_i = int(mes)
    if not aplicado:
        # default: mes anterior
        d = datetime.now().replace(day=1)
        d_ant = d.replace(month=d.month - 1) if d.month > 1 else d.replace(year=d.year - 1, month=12)
        ano_i, mes_i = d_ant.year, d_ant.month; ano, mes = str(ano_i), '%02d' % mes_i
    janela = int(request.args.get('janela') or 12)
    if not (5 <= janela <= 24): janela = 12
    mensuracao = conferencia.mensuracao_beneficio(todos, ano_i, mes_i, janela=janela)
    linhas = []
    tot = {'economia': 0.0, 'verde': 0, 'amarelo': 0, 'vermelho': 0, 'com_pgdas': 0}
    for cn, d in mensuracao.items():
        e = emeta.get(cn)
        linhas.append({
            'cnpj': cn,
            'id': (e['id'] if e else 0),
            'nome': (e['nome'] if e else ''),
            'anexo': d['anexo'] or '-',
            'rbt12': d['rbt12'],
            'receita': d['receita'],
            'fonte_receita': d['fonte_receita'],
            'pct_v': d['pct_mono_vendas'],
            'pct_c': d['pct_mono_compras'],
            'pct_usado': d['pct_mono_usado'],
            'economia': d['economia'],
            'semaforo': d['semaforo'],
            'tem_pgdas': d['tem_pgdas'],
        })
        tot['economia'] += d['economia']
        tot[d['semaforo']] += 1
        if d['tem_pgdas']: tot['com_pgdas'] += 1
    tot['economia'] = round(tot['economia'], 2)
    linhas.sort(key=lambda r: -(r['economia'] or 0))
    return render_template('fiscal_economia_mensuracao.html', linhas=linhas, tot=tot,
                           empresas=emp_rows, ano=ano, mes=mes, janela=janela,
                           comp_label='%s/%s' % (mes, ano))


# ---- Etapa 16: Receita com ICMS-ST (segregacao no Simples) ----
@app.route('/fiscal/economia/st')
@login_req
def fiscal_economia_st():
    """Identifica receita com ICMS-ST nas saidas reais e mostra a parcela que
       pode ser SEGREGADA do DAS (LC 123 art. 13 §1o XIII 'a' + Res CGSN 140
       art. 25 §8o II). O ICMS-ST em si e recolhido fora do DAS (GIA-ST)."""
    from engines import st as stmod
    with models.con() as c:
        emp_rows = _scope(c.execute('SELECT id, cnpj, nome, uf FROM empresas ORDER BY nome').fetchall())
    todos = [e['cnpj'] for e in emp_rows]
    emeta = {e['cnpj']: e for e in emp_rows}
    aplicado = request.args.get('aplicado')
    ano = (request.args.get('ano') or '').strip()
    mes = (request.args.get('mes') or '').strip()
    emp_sel = (request.args.get('empresa') or '').strip()
    ocultar = bool(request.args.get('ocultar'))
    if not aplicado and not ano and not mes:
        ano, mes = _comp_anterior()
    cnpjs = [emp_sel] if (emp_sel and emp_sel in todos) else todos

    raw = conferencia.receita_com_st(cnpjs, ano=ano, mes=mes)
    linhas = []
    tot = {'fat_total': 0.0, 'fat_com_st': 0.0, 'economia_total': 0.0,
           'n_com_st': 0, 'aliquotas': {}}
    for cn, d in raw.items():
        e = emeta.get(cn)
        # projecao: empresa do Simples que REVENDER (substituida) tem ST embutido no
        # preco de compra. O ganho vem de SEGREGAR a receita com ST da base do DAS
        # (LC 123 art. 13 §1o XIII 'a' + Res CGSN 140 art. 25 §8o II): o ICMS
        # proprio deixa de incidir sobre essa receita. A empresa NAO recolhe
        # ICMS-ST por fora (quem recolheu foi a industria na origem).
        # Quem recolhe ICMS-ST por fora (GIA-ST) e o SUBSTITUTO (industria/
        # atacadista) optante do Simples, que repassa o ICMS-ST do substituido.
        # Aqui, para revenda (caso comum), mostramos a ECONOMIA no DAS.
        aliq = (d['aliquota_interna'] or 0.0) / 100.0
        fat_com_st = d['fat_com_st']
        share_icms = 0.32  # parcela ICMS dentro do Simples Anexo I faixa 1 (simplificado)
        economia_das = fat_com_st * aliq * share_icms
        row = dict(d)
        row.update({
            'cnpj': cn,
            'nome': (e['nome'] if e else ''),
            'id': (e['id'] if e else 0),
            'uf': (e['uf'] if e else ''),
            'economia_das': round(economia_das, 2),
            'share_icms': share_icms,
        })
        tot['fat_total'] += d['fat_total']
        tot['fat_com_st'] += d['fat_com_st']
        tot['economia_total'] += economia_das
        if d['fat_com_st'] > 0:
            tot['n_com_st'] += 1
            aliq_str = str(d['aliquota_interna'] or 0)
            tot['aliquotas'][aliq_str] = tot['aliquotas'].get(aliq_str, 0) + 1
        linhas.append(row)
    if ocultar:
        linhas = [r for r in linhas if r['fat_com_st'] > 0]
    linhas.sort(key=lambda r: (-(r['economia_das'] or 0), -(r['fat_com_st'] or 0)))
    for k in ('fat_total', 'fat_com_st', 'economia_total'):
        tot[k] = round(tot[k], 2)
    anos = sorted({c2[:4] for c2 in conferencia.competencias_disponiveis(todos)}, reverse=True)
    return render_template('fiscal_economia_st.html', linhas=linhas, tot=tot, anos=anos,
                           empresas=emp_rows, ano=ano, mes=mes, empresa_sel=emp_sel,
                           ocultar=ocultar,
                           comp_label=('%s/%s' % (mes, ano) if (ano and mes) else
                                       (ano or mes or 'Todas as competências')))


# ---- Etapa 9: Forcar NSU inicial em uma empresa (cobertura de empresas sem demarcacao) ----
@app.route('/clientes/<int:eid>/forcar-nsu', methods=['POST'])
@admin_required
def cliente_forcar_nsu(eid):
    nsu = re.sub(r'\D', '', request.form.get('nsu', ''))[:15].zfill(15) or '000000000000000'
    flag = 1 if request.form.get('forcar') else 0
    with models.con() as c:
        c.execute('UPDATE empresas SET forcar_nsu_nfe=?, nsu_inicial_forcado=? WHERE id=?',
                  (flag, nsu, eid))
    flash('NSU forcado configurado (proxima execucao NFeentradas comecara em %s).' % nsu, 'ok')
    return redirect(url_for('clientes'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=CFG.get('porta', 5001), debug=False)
