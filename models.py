# -*- coding: utf-8 -*-
"""Portal Fiscal Nescon - camada de dados.
   - empresas: base UNICA (NFe + NFSe), flags por cliente.
   - usuarios + usuario_empresas: RBAC (admin/operador, escopo por empresa).
   - jobs: fila de execucoes em background com status persistente.
   - consultas_log: controle anti-bloqueio (limites por CNPJ/certificado).
   DATA_DIR (env FISCAL_DATA_DIR) separa DADOS do CODIGO -> volume em producao.
"""
import os, sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('FISCAL_DATA_DIR', BASE)
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, 'portal_fiscal.db')

def con():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
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
        ''')
        # migracoes de colunas novas em bancos antigos
        _add_col(c, 'usuarios', 'todas_empresas', 'todas_empresas INTEGER DEFAULT 0')
        _add_col(c, 'usuarios', 'ativo', 'ativo INTEGER DEFAULT 1')
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
        # admin inicial (senha vem de env em producao; local usa admin/admin)
        if not c.execute('SELECT 1 FROM usuarios LIMIT 1').fetchone():
            senha_inicial = os.environ.get('ADMIN_SENHA_INICIAL', 'admin')
            c.execute('INSERT INTO usuarios(login,senha_hash,nome,papel,todas_empresas,ativo,criado) '
                      'VALUES(?,?,?,?,1,1,?)',
                      ('admin', generate_password_hash(senha_inicial), 'Administrador', 'admin',
                       datetime.now().isoformat(timespec='seconds')))

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
LIMITES = {'distNSU': (20, 60), 'consChNFe': (10, 60), 'nfse': (20, 60)}  # (qtd, minutos)

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

if __name__ == '__main__':
    init_db()
    print('Base pronta em', DB)
