# -*- coding: utf-8 -*-
"""Parser do Recibo de Entrega da Declaracao PGDAS-D (Extrato do Simples Nacional).
   O PDF eh gerado pelo portal do Simples Nacional ou pelo programa PGDAS-Download.
   Conteudo tipico (texto extraido por pdfplumber):

       RECIBO DE ENTREGA DA DECLARACAO
       Data e Horario da Transmissao (Data e Horario de Brasilia): 17/06/2024 09:57:33

       Identificacao do Contribuinte
       CNPJ: 39.408.046/0020-42
       Razao Social: EMPRESA EXEMPLO LTDA

       Periodo de Apuracao: 05/2024

       Declaracao
       Receita Bruta Total do Periodo: R$ 150.000,00
       Anexo: III
       Aliquota Efetiva: 6,0000%
       Valor do DAS devido: R$ 9.000,00

       Receita Bruta por Atividade (se houver mais de uma)
       Atividade    Receita Bruta    Aliquota
       Comercio     R$ 100.000,00    4,50%
   ...

   Este parser extrai: cnpj, razao_social, ano, mes, receita_total, anexo,
   aliquota_efetiva, das_devido, rbt12 (se constar).
"""
import re, hashlib
from datetime import datetime

_CNPJ_RE = re.compile(r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})')
_PERIODO_RE = re.compile(r'Per[ií]odo de Apura[çc][ãa]o[:\s]*(\d{2})/(\d{4})', re.I)
_RECEITA_RE = re.compile(r'Receita\s+Bruta\s+Total[^:]*:\s*R\$\s*([\d.]+,\d{2})', re.I)
_ANEXO_RE = re.compile(r'Anexo[:\s]+([IVX]+)', re.I)
_ALIQ_RE = re.compile(r'Al[ií]quota\s+Efetiva[:\s]+([\d.,]+)\s*%', re.I)
_DAS_RE = re.compile(r'Valor\s+do\s+DAS\s+devido[:\s]+R\$\s*([\d.]+,\d{2})', re.I)
_RBT12_RE = re.compile(r'Receita\s+Bruta\s+Acumulada.*?R\$\s*([\d.]+,\d{2})', re.I)

def _norm_cnpj(s):
    return re.sub(r'\D', '', s or '')

def parse_recibo(conteudo_bytes):
    """Recebe bytes do PDF e retorna dict {cnpj, razao_social, ano, mes, receita_total,
       anexo, aliquota_efetiva, das_devido, rbt12} ou None."""
    try:
        import pdfplumber
    except ImportError:
        return None
    texto = ''
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
            tf.write(conteudo_bytes); tmp = tf.name
        try:
            with pdfplumber.open(tmp) as pdf:
                for p in pdf.pages:
                    t = p.extract_text() or ''
                    texto += '\n' + t
        finally:
            try:
                import os; os.unlink(tmp)
            except Exception: pass
    except Exception:
        return None
    if not texto: return None

    # CNPJ - pegar o PRIMEIRO (geralmente eh o do contribuinte)
    m = _CNPJ_RE.search(texto)
    if not m: return None
    cnpj = _norm_cnpj(m.group(1))
    if len(cnpj) != 14: return None

    # Periodo de apuracao
    m = _PERIODO_RE.search(texto)
    if not m: return None
    mes = int(m.group(1)); ano = int(m.group(2))

    # Receita Bruta Total
    m = _RECEITA_RE.search(texto)
    if not m: return None
    receita_str = m.group(1).replace('.', '').replace(',', '.')
    receita_total = float(receita_str)

    rec = {
        'cnpj': cnpj,
        'ano': ano,
        'mes': mes,
        'receita_total': receita_total,
    }

    # Campos opcionais
    m = _ANEXO_RE.search(texto)
    if m: rec['anexo'] = m.group(1).strip().upper()

    m = _ALIQ_RE.search(texto)
    if m:
        try:
            v = m.group(1).replace(',', '.')
            rec['aliquota_efetiva'] = float(v)
        except Exception: pass

    m = _DAS_RE.search(texto)
    if m:
        try:
            v = m.group(1).replace('.', '').replace(',', '.')
            rec['das_devido'] = float(v)
        except Exception: pass

    m = _RBT12_RE.search(texto)
    if m:
        try:
            v = m.group(1).replace('.', '').replace(',', '.')
            rec['rbt12'] = float(v)
        except Exception: pass

    return rec

def hash_recibo(cnpj, ano, mes, receita_total):
    base = '%s|%04d|%02d|%.2f' % (cnpj, ano, mes, receita_total)
    return hashlib.sha1(base.encode('utf-8')).hexdigest()


def _make_test_pdf(out_path):
    """Gera um PDF de recibo PGDAS-D sintetico (para teste)."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        c = canvas.Canvas(out_path, pagesize=A4)
        c.setFont('Courier-Bold', 14)
        c.drawString(50, 800, 'RECIBO DE ENTREGA DA DECLARA敲O')
        c.setFont('Courier', 10)
        c.drawString(50, 780, 'Data e Horario da Transmissao (Data e Horario de Brasilia): 17/06/2024 09:57:33')
        c.drawString(50, 760, '')
        c.drawString(50, 740, 'Identificacao do Contribuinte')
        c.drawString(50, 725, 'CNPJ: 39.408.046/0020-42')
        c.drawString(50, 710, 'Razao Social: EMPRESA EXEMPLO LTDA')
        c.drawString(50, 685, '')
        c.drawString(50, 665, 'Periodo de Apuracao: 06/2026')
        c.drawString(50, 640, '')
        c.drawString(50, 620, 'Declaracao')
        c.drawString(50, 605, 'Receita Bruta Total do Periodo: R$ 152.430,50')
        c.drawString(50, 590, 'Anexo: I')
        c.drawString(50, 575, 'Aliquota Efetiva: 5,6500%')
        c.drawString(50, 560, 'Valor do DAS devido: R$ 1.789,80')
        c.save()
        return True
    except ImportError:
        return False


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as f:
            rec = parse_recibo(f.read())
        print(rec)
    else:
        # gera PDF teste
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
            tmp = tf.name
        if _make_test_pdf(tmp):
            with open(tmp, 'rb') as f:
                rec = parse_recibo(f.read())
            print('Sintetico:', rec)