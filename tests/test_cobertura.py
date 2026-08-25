# -*- coding: utf-8 -*-
import os, sys, tempfile, shutil, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime

_tmp_xml = tempfile.mkdtemp(prefix='portal-xml-')
os.environ.setdefault('FISCAL_XML_DIR', _tmp_xml)

if 'xmlsec' not in sys.modules:
    sys.modules['xmlsec'] = types.ModuleType('xmlsec')

import app as portal


def _emp(eid, cnpj, nome, nfe=0, nfse=0, nfce=0, ativo=1):
    return {'id': eid, 'cnpj': cnpj, 'nome': nome, 'ativo': ativo,
            'puxa_nfe': nfe, 'puxa_nfse': nfse, 'puxa_nfce': nfce}


def test_mes_anterior_padrao():
    assert portal._mes_anterior(datetime(2026, 8, 25)) == ('2026', '07')
    assert portal._mes_anterior(datetime(2026, 1, 1)) == ('2025', '12')


def test_cobertura_flags_e_xml(tmp_path=None):
    pasta = tempfile.mkdtemp()
    old = portal.SAIDA
    portal.SAIDA = pasta
    try:
        cnpj_ok = '11111111000111'
        d = os.path.join(pasta, cnpj_ok, '2026-07', 'NFe', '01_entrada')
        os.makedirs(d)
        open(os.path.join(d, 'a.xml'), 'w').write('<nfe/>')
        open(os.path.join(d, 'b.xml'), 'w').write('<nfe/>')
        empresas = [
            _emp(1, cnpj_ok, 'Com entrada', nfe=1),
            _emp(2, '22222222000122', 'Espera NF-e sem XML', nfe=1),
            _emp(3, '33333333000133', 'So servico', nfse=1),
            _emp(4, '44444444000144', 'Sem natureza'),
            _emp(5, '55555555000155', 'Inativa com NF-e', nfe=1, ativo=0),
        ]
        linhas, resumo = portal._cobertura_competencia(empresas, '2026-07')
        nomes = [l['nome'] for l in linhas]
        assert 'Sem natureza' not in nomes
        assert 'Inativa com NF-e' not in nomes
        assert resumo['empresas'] == 3
        assert resumo['nfe']['esperadas'] == 2
        assert resumo['nfe']['com'] == 1
        assert resumo['nfe']['sem'] == 1
        assert resumo['nfe']['xmls'] == 2
        assert resumo['nfse']['esperadas'] == 1
        assert resumo['nfse']['sem'] == 1
        assert resumo['faltando'] == 2

        so_falta, _ = portal._cobertura_competencia(empresas, '2026-07', visao='faltando')
        assert {l['nome'] for l in so_falta} == {'Espera NF-e sem XML', 'So servico'}

        so_ok, _ = portal._cobertura_competencia(empresas, '2026-07', visao='ok')
        assert [l['nome'] for l in so_ok] == ['Com entrada']
        assert so_ok[0]['nfe'] == 2

        nfe_sem, _ = portal._cobertura_competencia(empresas, '2026-07', visao='nfe_sem')
        assert [l['nome'] for l in nfe_sem] == ['Espera NF-e sem XML']

        linhas_in, r_in = portal._cobertura_competencia(
            empresas, '2026-07', incluir_inativos=True)
        assert any(l['nome'] == 'Inativa com NF-e' for l in linhas_in)
        assert r_in['nfe']['esperadas'] == 3
    finally:
        portal.SAIDA = old
        shutil.rmtree(pasta, ignore_errors=True)


def test_dashboard_renderiza_cobertura():
    cli = portal.app.test_client()
    with cli.session_transaction() as s:
        s['uid'] = 1
        s['papel'] = 'admin'
        s['nome'] = 'admin'
    r = cli.get('/')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Cobertura de XML' in html
    assert 'NF-e entrada' in html
    assert 'name="mes"' in html
    r2 = cli.get('/?ano=2026&mes=07&visao=nfe_sem')
    assert r2.status_code == 200
    assert 'value="nfe_sem" selected>' in r2.get_data(as_text=True)


if __name__ == '__main__':
    try:
        test_mes_anterior_padrao()
        test_cobertura_flags_e_xml()
        test_dashboard_renderiza_cobertura()
        print('ok')
    finally:
        shutil.rmtree(_tmp_xml, ignore_errors=True)
