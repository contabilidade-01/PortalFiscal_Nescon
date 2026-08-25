# -*- coding: utf-8 -*-
"""Backup e restauracao de cadastros e certificados A1.
   Zip de migracao Windows -> EasyPanel. Reescreve caminhos dos .pfx para o volume.
   XML fica de fora (o servidor puxa de novo na SEFAZ).
"""
import json, os, shutil, sqlite3, zipfile
from datetime import datetime

MANIFEST = 'manifest.json'
DB_IN_ZIP = 'portal_fiscal.db'
CERT_IN_ZIP = 'Certificados'
RESTAURAR_VOLUME = 'restaurar.zip'

# Tabelas que viajam no backup. Nao leva usuarios/jobs (login do servidor fica).


def fmt_bytes(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            if u == 'B':
                return '%d B' % int(n)
            return ('%.1f %s' % (n, u)).replace('.', ',')
        n /= 1024.0


def checkpoint_db(db_path):
    if not os.path.isfile(db_path):
        return
    c = sqlite3.connect(db_path, timeout=60)
    try:
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        c.close()


def _basename(path):
    if not path:
        return ''
    return os.path.basename(str(path).replace('\\', '/').rstrip('/'))


def _coletar_certs(cert_dir, extra_paths):
    """{nome_no_zip: caminho_origem} — um .pfx por empresa (cnpj.pfx) + escritorio."""
    seen = {}
    if os.path.isdir(cert_dir):
        for a in os.listdir(cert_dir):
            if a.lower().endswith(('.pfx', '.p12')):
                seen[a] = os.path.join(cert_dir, a)
    for p in extra_paths or []:
        if not p or not os.path.isfile(p):
            continue
        nome = _basename(p)
        if nome not in seen:
            seen[nome] = p
    return seen


def extra_caminhos_cert(db_path):
    if not os.path.isfile(db_path):
        return []
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        out = [r['arquivo'] for r in c.execute(
            "SELECT arquivo FROM empresas WHERE arquivo IS NOT NULL AND arquivo!=''")]
        r = c.execute("SELECT valor FROM parametros WHERE chave='office_arquivo'").fetchone()
        if r and r['valor']:
            out.append(r['valor'])
        return out
    finally:
        c.close()


def resumo(db_path, cert_dir):
    n_emp = n_ok = 0
    if os.path.isfile(db_path):
        c = sqlite3.connect(db_path)
        try:
            n_emp = c.execute('SELECT COUNT(*) FROM empresas').fetchone()[0]
            n_ok = c.execute('SELECT COUNT(*) FROM empresas WHERE senha_ok=1').fetchone()[0]
        except sqlite3.Error:
            pass
        finally:
            c.close()
    certs = _coletar_certs(cert_dir, extra_caminhos_cert(db_path))
    cert_b = 0
    for p in certs.values():
        try:
            cert_b += os.path.getsize(p)
        except OSError:
            pass
    db_b = os.path.getsize(db_path) if os.path.isfile(db_path) else 0
    return {
        'empresas': n_emp, 'com_cert': n_ok, 'pfx': len(certs),
        'db_fmt': fmt_bytes(db_b), 'cert_fmt': fmt_bytes(cert_b),
        'leve_fmt': fmt_bytes(db_b + cert_b),
    }


def criar_zip(destino, db_path, cert_dir, incluir_db=True, incluir_certs=True):
    os.makedirs(os.path.dirname(os.path.abspath(destino)) or '.', exist_ok=True)
    if incluir_db:
        checkpoint_db(db_path)
    extra = extra_caminhos_cert(db_path) if incluir_certs else []
    certs = _coletar_certs(cert_dir, extra) if incluir_certs else {}
    man = {
        'versao': 1,
        'quando': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'inclui_db': bool(incluir_db),
        'inclui_certs': bool(incluir_certs),
        'inclui_xml': False,
    }
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(MANIFEST, json.dumps(man, ensure_ascii=False, indent=2))
        if incluir_db and os.path.isfile(db_path):
            zf.write(db_path, DB_IN_ZIP)
        for nome, origem in sorted(certs.items()):
            zf.write(origem, '%s/%s' % (CERT_IN_ZIP, nome))
    return destino


def _cols(c, tabela):
    return [r[1] for r in c.execute('PRAGMA table_info(%s)' % tabela).fetchall()]


def _tem_tabela(c, tabela):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                     (tabela,)).fetchone() is not None


def _copiar_tabela(src, dst, tabela, conflito=None):
    """Copia linhas. conflito = colunas do UNIQUE/PK p/ upsert (ex.: 'cnpj, chNFe')."""
    if not _tem_tabela(src, tabela) or not _tem_tabela(dst, tabela):
        return 0
    scols = _cols(src, tabela)
    dcols = _cols(dst, tabela)
    cols = [c for c in scols if c in dcols]
    if not cols:
        return 0
    n = 0
    ph = ','.join('?' * len(cols))
    names = ','.join(cols)
    if conflito:
        sql = ('INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(%s) DO UPDATE SET %s' % (
            tabela, names, ph, conflito,
            ','.join('%s=excluded.%s' % (c, c) for c in cols)))
    else:
        sql = 'INSERT INTO %s (%s) VALUES (%s)' % (tabela, names, ph)
    for row in src.execute('SELECT %s FROM %s' % (names, tabela)):
        dst.execute(sql, [row[c] for c in cols])
        n += 1
    return n


def _remap_pfx(path, cert_dir):
    nome = _basename(path)
    if not nome:
        return path
    return os.path.join(cert_dir, nome)


def restaurar_zip(zip_path, db_path, cert_dir):
    """Restaura um zip de migracao. Login (usuarios) do destino e preservado.
       XML no zip, se houver (backup antigo), e ignorado."""
    if not zipfile.is_zipfile(zip_path):
        raise ValueError('Arquivo nao e um ZIP valido.')
    tmp = zip_path + '.extract'
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    n_emp = n_pfx = 0
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                nome = info.filename.replace('\\', '/').lstrip('/')
                if nome.startswith('XML/') or nome == 'XML':
                    continue
                zf.extract(info, tmp)
        os.makedirs(cert_dir, exist_ok=True)
        pasta_cert = os.path.join(tmp, CERT_IN_ZIP)
        if os.path.isdir(pasta_cert):
            for a in os.listdir(pasta_cert):
                origem = os.path.join(pasta_cert, a)
                if os.path.isfile(origem):
                    shutil.copy2(origem, os.path.join(cert_dir, a))
                    n_pfx += 1
        bak = os.path.join(tmp, DB_IN_ZIP)
        if os.path.isfile(bak) and os.path.isfile(db_path):
            src = sqlite3.connect(bak)
            src.row_factory = sqlite3.Row
            dst = sqlite3.connect(db_path, timeout=60)
            dst.row_factory = sqlite3.Row
            dst.execute('PRAGMA foreign_keys=OFF')
            try:
                n_emp = _upsert_empresas(src, dst, cert_dir)
                _copiar_parametros(src, dst, cert_dir)
                _copiar_tabela(src, dst, 'ciencia_dada', conflito='cnpj, chNFe')
                _copiar_tabela(src, dst, 'ncm_monofasico', conflito='ncm')
                try:
                    _copiar_tabela(src, dst, 'pgdas_recibos',
                                   conflito='cnpj, ano, mes, hash_linha')
                except sqlite3.OperationalError:
                    _copiar_tabela(src, dst, 'pgdas_recibos')
                dst.commit()
            finally:
                dst.close()
                src.close()
        elif os.path.isfile(bak) and not os.path.isfile(db_path):
            shutil.copy2(bak, db_path)
            n_emp = -1
            _remap_db_paths(db_path, cert_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {'empresas': n_emp, 'pfx': n_pfx}


def _upsert_empresas(src, dst, cert_dir):
    if not _tem_tabela(src, 'empresas') or not _tem_tabela(dst, 'empresas'):
        return 0
    scols = _cols(src, 'empresas')
    dcols = _cols(dst, 'empresas')
    cols = [c for c in scols if c in dcols and c != 'id']
    n = 0
    for row in src.execute('SELECT * FROM empresas'):
        data = {c: row[c] for c in cols}
        if 'arquivo' in data:
            data['arquivo'] = _remap_pfx(data.get('arquivo'), cert_dir)
        ex = dst.execute('SELECT id FROM empresas WHERE cnpj=?', (data['cnpj'],)).fetchone()
        if ex:
            sets = ','.join('%s=?' % c for c in cols)
            dst.execute('UPDATE empresas SET %s WHERE id=?' % sets,
                        [data[c] for c in cols] + [ex['id']])
        else:
            dst.execute('INSERT INTO empresas (%s) VALUES (%s)' % (
                ','.join(cols), ','.join('?' * len(cols))),
                [data[c] for c in cols])
        n += 1
    return n


def _copiar_parametros(src, dst, cert_dir):
    if 'parametros' not in [r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        return
    for row in src.execute('SELECT chave, valor FROM parametros'):
        chave, valor = row['chave'], row['valor']
        if chave == 'office_arquivo':
            valor = _remap_pfx(valor, cert_dir)
        dst.execute('INSERT INTO parametros(chave,valor) VALUES(?,?) '
                    'ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor',
                    (chave, valor))


def _remap_db_paths(db_path, cert_dir):
    c = sqlite3.connect(db_path)
    try:
        for row in c.execute("SELECT id, arquivo FROM empresas WHERE arquivo IS NOT NULL"):
            c.execute('UPDATE empresas SET arquivo=? WHERE id=?',
                      (_remap_pfx(row[1], cert_dir), row[0]))
        r = c.execute("SELECT valor FROM parametros WHERE chave='office_arquivo'").fetchone()
        if r:
            c.execute("UPDATE parametros SET valor=? WHERE chave='office_arquivo'",
                      (_remap_pfx(r[0], cert_dir),))
        c.commit()
    finally:
        c.close()


def zip_do_volume(data_dir):
    return os.path.join(data_dir, RESTAURAR_VOLUME)
