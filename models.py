# -*- coding: utf-8 -*-
"""Portal Fiscal Nescon - camada de dados.
   - empresas: base UNICA (NFe + NFSe), flags por cliente.
   - usuarios + usuario_empresas + usuario_permissoes: RBAC.
   - password_reset_tokens: recuperacao de senha (hash + validade).
   - jobs: fila de execucoes em background com status persistente.
   - consultas_log: controle anti-bloqueio (limites por CNPJ/certificado).
   DATA_DIR (env FISCAL_DATA_DIR) separa DADOS do CODIGO -> volume em producao.
"""
import os, sqlite3, secrets, hashlib
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

# Rotinas que o admin pode liberar para operadores (admin tem todas sempre).
PERMISSOES = [
    ('exec_nfe_entradas', 'Puxar NF-e entradas (compras)'),
    ('exec_nfe_saidas', 'Puxar NF-e saídas (vendas)'),
    ('exec_nfse', 'Puxar NFS-e'),
    ('exec_nfce', 'Puxar NFC-e'),
    ('exec_ciencia', 'Rodar Ciência 210210'),
    ('exec_completo', 'Busca completa (todas as rotinas)'),
    ('ver_execucoes', 'Acompanhar tela de Execuções'),
    ('ver_protocolos', 'Ver Protocolos da Ciência'),
    ('ver_analises', 'Análises fiscais (conferência, faturamento…)'),
    ('download', 'Baixar XML por competência'),
]
PERM_KEYS = frozenset(k for k, _ in PERMISSOES)
TIPO_PARA_PERM = {
    'nfe_entradas': 'exec_nfe_entradas',
    'nfe_saidas': 'exec_nfe_saidas',
    'nfse': 'exec_nfse',
    'nfce': 'exec_nfce',
    'ciencia': 'exec_ciencia',
    'completo': 'exec_completo',
}

BASE = os.path.dirname(os.path.abspath(__file__))

def parece_caminho_windows(p):
    """True para 'C:\\...' / 'C:/...' — no Linux isso vira pasta DENTRO de /app, fora do volume."""
    p = (p or '').strip()
    if len(p) >= 3 and p[0].isalpha() and p[1] == ':' and p[2] in '\\/':
        return True
    return p.startswith('\\\\')

def em_docker():
    return os.path.exists('/.dockerenv')

def _resolver_data_dir():
    env = (os.environ.get('FISCAL_DATA_DIR') or '').strip()
    if em_docker():
        if env and not parece_caminho_windows(env):
            return env
        return '/app/data'
    if env:
        return env
    return BASE

DATA_DIR = _resolver_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, 'portal_fiscal.db')

def pasta_xml():
    """Onde gravar XML. No Docker ignora pasta_saida_xml do PC (OneDrive C:\\...)."""
    env = (os.environ.get('FISCAL_XML_DIR') or '').strip()
    if env and not parece_caminho_windows(env):
        return env
    if os.name == 'nt' and not em_docker():
        if env:
            return env
        try:
            import json
            cfg = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
            p = (cfg.get('pasta_saida_xml') or '').strip()
            if p:
                return p
        except Exception:
            pass
    return os.path.join(DATA_DIR, 'XML')

XML_DIR = pasta_xml()
os.makedirs(XML_DIR, exist_ok=True)

def con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    # Espera o lock em vez de estourar 'database is locked' na hora (casa com timeout=30s).
    # Sem isso, uma leitura concorrente com o worker vira excecao e derruba /healthz.
    c.execute('PRAGMA busy_timeout=30000')
    return c

def _add_col(c, tabela, coluna, ddl):
    cols = [r[1] for r in c.execute('PRAGMA table_info(%s)' % tabela).fetchall()]
    if coluna not in cols:
        c.execute('ALTER TABLE %s ADD COLUMN %s' % (tabela, ddl))

def init_db():
    with con() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios(
          id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT UNIQUE, senha_hash TEXT,
          nome TEXT, papel TEXT DEFAULT 'operador', criado TEXT);

        CREATE TABLE IF NOT EXISTS empresas(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cnpj TEXT UNIQUE, nome TEXT, uf TEXT, cuf TEXT,
          arquivo TEXT, senha TEXT, senha_ok INTEGER DEFAULT 0, validade TEXT,
          ativo INTEGER DEFAULT 1,
          puxa_nfe INTEGER DEFAULT 1, puxa_nfse INTEGER DEFAULT 1,
          metodo_saida TEXT DEFAULT 'a_verificar', emissor TEXT, marketplace TEXT,
          ultnsu_nfe TEXT DEFAULT '000000000000000', ultnsu_nfse TEXT DEFAULT '0',
          bloqueado_nfe_ate TEXT, bloqueado_nfse_ate TEXT,
          ultima_exec_nfe TEXT, ultima_exec_nfse TEXT,
          total_nfe INTEGER DEFAULT 0, total_nfse INTEGER DEFAULT 0,
          whatsapp TEXT, responsavel TEXT, email TEXT, obs TEXT,
          origem TEXT DEFAULT 'gclick', criado TEXT);

        CREATE TABLE IF NOT EXISTS parametros(chave TEXT PRIMARY KEY, valor TEXT);

        CREATE TABLE IF NOT EXISTS execucoes(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, cnpj TEXT, nome TEXT,
          quando TEXT, docs INTEGER, parada TEXT, detalhe TEXT);

        CREATE TABLE IF NOT EXISTS ciencia_dada(
          cnpj TEXT, chNFe TEXT, cStat TEXT, nProt TEXT, quando TEXT, PRIMARY KEY(cnpj, chNFe));

        -- RBAC: quais empresas cada usuario ve (se todas_empresas=0)
        CREATE TABLE IF NOT EXISTS usuario_empresas(
          user_id INTEGER, empresa_id INTEGER, PRIMARY KEY(user_id, empresa_id));

        -- RBAC: rotinas liberadas para operadores (admin ignora esta tabela)
        CREATE TABLE IF NOT EXISTS usuario_permissoes(
          user_id INTEGER, permissao TEXT, PRIMARY KEY(user_id, permissao));

        -- Recuperacao de senha (guarda so hash do token)
        CREATE TABLE IF NOT EXISTS password_reset_tokens(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          criado TEXT NOT NULL);

        -- FILA DE JOBS (execucoes em background, status persistente)
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tipo TEXT,               -- nfe_entradas | nfe_saidas | nfse | ciencia | completo
          escopo TEXT,             -- 'todas' ou um CNPJ
          status TEXT DEFAULT 'fila',  -- fila | rodando | ok | erro | cancelado
          criado TEXT, iniciado TEXT, terminado TEXT,
          total INTEGER DEFAULT 0, feitos INTEGER DEFAULT 0, docs INTEGER DEFAULT 0,
          atual TEXT, mensagem TEXT, origem TEXT DEFAULT 'manual', user_id INTEGER);

        -- ANTI-BLOQUEIO: log de cada consulta a SEFAZ/ADN
        CREATE TABLE IF NOT EXISTS consultas_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, cnpj TEXT, servico TEXT, quando TEXT);

        -- Etapa 13: NCMs monofasicos (curados + importados da Tabela SPED 4.3.10)
        CREATE TABLE IF NOT EXISTS ncm_monofasico(
          ncm TEXT PRIMARY KEY, categoria TEXT, base_legal TEXT, origem TEXT DEFAULT 'sped');

        -- Etapa 15: Recibos PGDAS-D importados (parser PDF) - Anexo/receita por mes
        CREATE TABLE IF NOT EXISTS pgdas_recibos(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cnpj TEXT NOT NULL,
          ano INTEGER NOT NULL,
          mes INTEGER NOT NULL,
          receita_total REAL DEFAULT 0,
          anexo TEXT,
          arquivo TEXT,
          parsed_em TEXT,
          hash_linha TEXT,
          UNIQUE(cnpj,ano,mes,hash_linha));

        -- Etapa 15: Extrato bancario (OFX/CSV/PDF) - lancamentos por empresa
        CREATE TABLE IF NOT EXISTS extrato_lancamentos(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          empresa_id INTEGER NOT NULL,
          data TEXT NOT NULL,             -- AAAA-MM-DD
          descricao TEXT,
          valor REAL NOT NULL,            -- positivo=entrada, negativo=saida
          categoria TEXT,                 -- adquirente/fornecedor/folha/tarifa/outros
          adquirente TEXT,                -- CIELO/REDE/SUMUP/etc (se categoria=adquirente)
          origem TEXT,                    -- ofx/csv/pdf
          arquivo TEXT,
          fitid TEXT,                     -- ID unico do OFX quando houver
          hash_linha TEXT NOT NULL,
          parsed_em TEXT,
          UNIQUE(empresa_id,hash_linha));
        CREATE INDEX IF NOT EXISTS idx_extrato_emp_data ON extrato_lancamentos(empresa_id, data);
        CREATE INDEX IF NOT EXISTS idx_extrato_categoria ON extrato_lancamentos(empresa_id, categoria);

        -- Anti-ban: historico de 656/429/108/109/circuito (tela Saude SEFAZ)
        CREATE TABLE IF NOT EXISTS ocorrencias_sefaz(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cnpj TEXT, nome TEXT, servico TEXT,
          cstat TEXT, xmotivo TEXT, tipo TEXT,
          quando TEXT, bloqueado_ate TEXT,
          bloqueios_seguidos INTEGER, resolvido INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_ocorr_cnpj ON ocorrencias_sefaz(cnpj, quando);

        -- Cadencia NFC-e (e outros) compartilhada entre processos
        CREATE TABLE IF NOT EXISTS rate_gate(
          servico TEXT PRIMARY KEY, proximo_permitido_em TEXT);
        ''')
        # migracoes de colunas novas em bancos antigos
        _add_col(c, 'usuarios', 'todas_empresas', 'todas_empresas INTEGER DEFAULT 0')
        _add_col(c, 'usuarios', 'ativo', 'ativo INTEGER DEFAULT 1')
        _add_col(c, 'usuarios', 'email', 'email TEXT')
        _add_col(c, 'ciencia_dada', 'nProt', 'nProt TEXT')  # protocolo da Ciencia
        # NFC-e SP (modelo 65 - varejo)
        _add_col(c, 'empresas', 'puxa_nfce', 'puxa_nfce INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'total_nfce', 'total_nfce INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'ultima_exec_nfce', 'ultima_exec_nfce TEXT')
        if not c.execute("SELECT 1 FROM parametros WHERE chave='nfce_data_inicial'").fetchone():
            c.execute("INSERT INTO parametros(chave,valor) VALUES('nfce_data_inicial',?)",
                      (datetime.now().strftime('%Y-%m-01'),))
        if not c.execute("SELECT 1 FROM parametros WHERE chave='nfce_limite'").fetchone():
            c.execute("INSERT INTO parametros(chave,valor) VALUES('nfce_limite','500')")
        # Etapa 9: Forcar NSU inicial (empresa sem demarcacao confiavel)
        _add_col(c, 'empresas', 'forcar_nsu_nfe', 'forcar_nsu_nfe INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'nsu_inicial_forcado', 'nsu_inicial_forcado TEXT DEFAULT \'000000000000000\'')
        # Etapa 13: parametros do Simples p/ projecao de economia (monofasicos)
        _add_col(c, 'empresas', 'simples_anexo', "simples_anexo TEXT DEFAULT 'I'")
        _add_col(c, 'empresas', 'simples_rbt12', 'simples_rbt12 REAL DEFAULT 0')
        # Anti-656 NFC-e + retomada agendada no worker (servidor)
        _add_col(c, 'empresas', 'bloqueado_nfce_ate', 'bloqueado_nfce_ate TEXT')
        _add_col(c, 'jobs', 'agendado_para', 'agendado_para TEXT')
        # Circuit breaker anti-ban permanente (~50x656 na SEFAZ; disjuntor local em 5)
        _add_col(c, 'empresas', 'bloqueios_seguidos_nfe', 'bloqueios_seguidos_nfe INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'bloqueios_seguidos_nfce', 'bloqueios_seguidos_nfce INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'bloqueios_seguidos_nfse', 'bloqueios_seguidos_nfse INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'circuito_nfe', 'circuito_nfe INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'circuito_nfce', 'circuito_nfce INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'circuito_nfse', 'circuito_nfse INTEGER DEFAULT 0')
        _add_col(c, 'empresas', 'ultimo_bloqueio_motivo', 'ultimo_bloqueio_motivo TEXT')
        # admin inicial (senha vem de env em producao; local usa admin/admin)
        if not c.execute('SELECT 1 FROM usuarios LIMIT 1').fetchone():
            senha_inicial = os.environ.get('ADMIN_SENHA_INICIAL', 'admin')
            c.execute('INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) '
                      'VALUES(?,?,?,?,1,1,?)',
                      ('admin', generate_password_hash(senha_inicial), 'Administrador', 'admin',
                       datetime.now().isoformat(timespec='seconds')))
        # Migracao unica: operadores existentes recebem download + analises
        # (comportamento anterior a RBAC por rotina). Rodada so se a tabela
        # de permissoes ainda estiver vazia.
        if not c.execute('SELECT 1 FROM usuario_permissoes LIMIT 1').fetchone():
            for op in c.execute("SELECT id FROM usuarios WHERE papel!='admin'").fetchall():
                for k in ('download', 'ver_analises'):
                    c.execute('INSERT OR IGNORE INTO usuario_permissoes(user_id,permissao) VALUES(?,?)',
                              (op['id'], k))
        # Operadores ja existentes: preserva leitura basica (download + analises)
        # se ainda nao tiverem nenhuma permissao cadastrada.
        for op in c.execute("SELECT id FROM usuarios WHERE papel!='admin'").fetchall():
            n = c.execute('SELECT COUNT(*) FROM usuario_permissoes WHERE user_id=?',
                          (op['id'],)).fetchone()[0]
            if n == 0:
                for k in ('download', 'ver_analises'):
                    c.execute('INSERT OR IGNORE INTO usuario_permissoes(user_id,permissao) VALUES(?,?)',
                              (op['id'], k))

# ---------- RBAC ----------
def empresas_visiveis_ids(user):
    """IDs de empresas que o usuario pode ver. None = todas."""
    if user['papel'] == 'admin' or user['todas_empresas']:
        return None
    with con() as c:
        return [r['empresa_id'] for r in
                c.execute('SELECT empresa_id FROM usuario_empresas WHERE user_id=?', (user['id'],)).fetchall()]

def pode_ver_empresa(user, empresa_id):
    ids = empresas_visiveis_ids(user)
    return ids is None or empresa_id in ids

def permissoes_do_usuario(user):
    """Set de chaves de permissao. Admin = todas."""
    if not user:
        return set()
    if user['papel'] == 'admin':
        return set(PERM_KEYS)
    with con() as c:
        rows = c.execute('SELECT permissao FROM usuario_permissoes WHERE user_id=?',
                         (user['id'],)).fetchall()
    return {r['permissao'] for r in rows if r['permissao'] in PERM_KEYS}

def tem_permissao(user, chave):
    if not user or not chave:
        return False
    if user['papel'] == 'admin':
        return True
    return chave in permissoes_do_usuario(user)

def salvar_permissoes(user_id, chaves):
    """Substitui o conjunto de permissoes do operador."""
    validas = [k for k in chaves if k in PERM_KEYS]
    with con() as c:
        c.execute('DELETE FROM usuario_permissoes WHERE user_id=?', (user_id,))
        for k in validas:
            c.execute('INSERT OR IGNORE INTO usuario_permissoes(user_id,permissao) VALUES(?,?)',
                      (user_id, k))

def cnpjs_visiveis(user):
    """Lista de CNPJs no escopo do usuario. None = todas."""
    ids = empresas_visiveis_ids(user)
    if ids is None:
        return None
    if not ids:
        return []
    with con() as c:
        rows = c.execute(
            'SELECT cnpj FROM empresas WHERE id IN (%s)' % ','.join('?' * len(ids)),
            ids).fetchall()
    return [r['cnpj'] for r in rows]

# ---------- Recuperacao de senha ----------
def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def reset_expiry_minutes():
    try:
        m = int(os.environ.get('PASSWORD_RESET_EXPIRY_MINUTES', '60'))
    except ValueError:
        m = 60
    return max(5, min(m, 60 * 24 * 7))

def criar_token_reset(user_id):
    """Invalida tokens anteriores, cria um novo. Retorna o token em claro (so para o e-mail)."""
    token = secrets.token_hex(32)
    th = _hash_token(token)
    agora = datetime.now()
    exp = agora + timedelta(minutes=reset_expiry_minutes())
    with con() as c:
        c.execute('UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL',
                  (agora.strftime('%Y-%m-%d %H:%M:%S'), user_id))
        c.execute('INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,criado) VALUES(?,?,?,?)',
                  (user_id, th, exp.strftime('%Y-%m-%d %H:%M:%S'),
                   agora.strftime('%Y-%m-%d %H:%M:%S')))
    return token

def usuario_por_token_reset(token):
    """Retorna row do usuario se o token for valido; senao None."""
    if not token or len(token) < 16:
        return None
    th = _hash_token(token)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with con() as c:
        row = c.execute('''SELECT u.* FROM password_reset_tokens t
                           JOIN usuarios u ON u.id=t.user_id
                           WHERE t.token_hash=? AND t.used_at IS NULL AND t.expires_at>=?
                             AND u.ativo=1''', (th, agora)).fetchone()
    return row

def consumir_token_reset(token, nova_senha_hash):
    """Marca token usado e atualiza a senha. True se ok."""
    th = _hash_token(token)
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with con() as c:
        t = c.execute('''SELECT * FROM password_reset_tokens
                         WHERE token_hash=? AND used_at IS NULL AND expires_at>=?''',
                      (th, agora)).fetchone()
        if not t:
            return False
        c.execute('UPDATE usuarios SET senha_hash=? WHERE id=?', (nova_senha_hash, t['user_id']))
        c.execute('UPDATE password_reset_tokens SET used_at=? WHERE id=?', (agora, t['id']))
    return True

def buscar_usuario_login_email(login, email):
    """Match case-insensitive de login + e-mail cadastrado (ativos)."""
    login = (login or '').strip()
    email = (email or '').strip().lower()
    if not login or not email or '@' not in email:
        return None
    with con() as c:
        row = c.execute(
            'SELECT * FROM usuarios WHERE login=? AND ativo=1 AND lower(trim(coalesce(email,\'\')))=?',
            (login, email)).fetchone()
    return row

# ---------- Ciencia ----------
def ciencia_ja(cnpj, ch):
    with con() as c:
        return c.execute('SELECT 1 FROM ciencia_dada WHERE cnpj=? AND chNFe=?', (cnpj, ch)).fetchone() is not None

def ciencia_registrar(cnpj, ch, cStat, nProt=None):
    with con() as c:
        c.execute('INSERT OR REPLACE INTO ciencia_dada(cnpj,chNFe,cStat,nProt,quando) VALUES(?,?,?,?,?)',
                  (cnpj, ch, cStat, nProt, datetime.now().strftime('%Y-%m-%d %H:%M')))

# ---------- Parametros ----------
def get_param(chave, default=None):
    with con() as c:
        r = c.execute('SELECT valor FROM parametros WHERE chave=?', (chave,)).fetchone()
        return r['valor'] if r else default

def set_param(chave, valor):
    with con() as c:
        c.execute('INSERT INTO parametros(chave,valor) VALUES(?,?) '
                  'ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor', (chave, valor))

# ---------- Anti-bloqueio (controle de consultas) ----------
# distNSU oficial ~600/5min por certificado; usamos metade (300/5min) de folga.
# consChNFe: o portal nao consulta por chave hoje — reserva.
LIMITES = {'distNSU': (300, 5), 'consChNFe': (10, 60), 'nfse': (40, 5)}  # (qtd, minutos)
try:
    import json as _json
    _ab = (_json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8')).get('antiban') or {})
    if _ab.get('distnsu_limite_qtd'):
        LIMITES['distNSU'] = (int(_ab['distnsu_limite_qtd']), int(_ab.get('distnsu_limite_min', 5)))
except Exception:
    pass

def pode_consultar(cnpj, servico='distNSU'):
    """True se ainda esta dentro do limite/hora para aquele CNPJ+servico."""
    qtd, minutos = LIMITES.get(servico, (20, 60))
    desde = (datetime.now() - timedelta(minutes=minutos)).strftime('%Y-%m-%d %H:%M:%S')
    with con() as c:
        n = c.execute('SELECT COUNT(*) FROM consultas_log WHERE cnpj=? AND servico=? AND quando>=?',
                      (cnpj, servico, desde)).fetchone()[0]
    return n < qtd

def registrar_consulta(cnpj, servico='distNSU'):
    with con() as c:
        c.execute('INSERT INTO consultas_log(cnpj,servico,quando) VALUES(?,?,?)',
                  (cnpj, servico, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

def diagnostico_persistencia():
    """No Docker, dados em /app/data so sobrevivem ao deploy se houver VOLUME montado.
    Sem mount, cada 'Implantar' no EasyPanel zera banco, XML e certificados."""
    data = os.path.abspath(DATA_DIR)
    in_container = os.path.exists('/.dockerenv') or os.environ.get('FISCAL_DATA_DIR') == '/app/data'
    mounted = False
    try:
        if os.path.ismount(data):
            mounted = True
        else:
            parent = os.path.dirname(data.rstrip(os.sep)) or os.sep
            mounted = os.stat(data).st_dev != os.stat(parent).st_dev
        if not mounted and os.path.isfile('/proc/self/mountinfo'):
            with open('/proc/self/mountinfo', encoding='utf-8', errors='ignore') as f:
                txt = f.read()
            mounted = (' %s ' % data) in txt or txt.find(' ' + data + '\n') >= 0 or txt.rstrip().endswith(' ' + data)
    except OSError:
        mounted = False
    n_emp = n_jobs = 0
    db_bytes = 0
    try:
        if os.path.isfile(DB):
            db_bytes = os.path.getsize(DB)
        with con() as c:
            n_emp = c.execute('SELECT COUNT(*) FROM empresas').fetchone()[0]
            n_jobs = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    except Exception:
        pass
    xml = os.path.abspath(XML_DIR)
    xml_no_volume = em_docker() and not xml.startswith(os.path.abspath(DATA_DIR) + os.sep) and xml != os.path.abspath(DATA_DIR)
    return {
        'data_dir': data,
        'xml_dir': xml,
        'db': DB,
        'in_container': bool(in_container),
        'volume_montado': bool(mounted),
        'xml_no_volume': bool(xml_no_volume),
        'risco_apagar_no_deploy': bool(in_container and (not mounted or xml_no_volume)),
        'empresas': n_emp,
        'jobs': n_jobs,
        'db_bytes': db_bytes,
    }

if __name__ == '__main__':
    init_db()
    print('Base pronta em', DB)
