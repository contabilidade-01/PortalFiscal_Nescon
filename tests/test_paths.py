# -*- coding: utf-8 -*-
"""Caminhos Docker vs config.json local (OneDrive C:\\...). Sem SEFAZ."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import models


def test_parece_caminho_windows():
    assert models.parece_caminho_windows(
        'C:/Users/Jeandson/OneDrive/00_Nescon Contabilidade/0011_TEMPORARIOS/TESTENFE/PortalFiscal_Nescon/XML')
    assert models.parece_caminho_windows(r'C:\Users\x\XML')
    assert not models.parece_caminho_windows('/app/data/XML')
    assert not models.parece_caminho_windows('')
    assert not models.parece_caminho_windows(None)


def test_resolver_data_dir_no_docker():
    orig = models.em_docker
    models.em_docker = lambda: True
    old = os.environ.get('FISCAL_DATA_DIR')
    os.environ.pop('FISCAL_DATA_DIR', None)
    try:
        assert models._resolver_data_dir() == '/app/data'
        os.environ['FISCAL_DATA_DIR'] = 'C:/Users/Jeandson/OneDrive/x'
        assert models._resolver_data_dir() == '/app/data'
    finally:
        models.em_docker = orig
        if old is None:
            os.environ.pop('FISCAL_DATA_DIR', None)
        else:
            os.environ['FISCAL_DATA_DIR'] = old


def test_pasta_xml_em_docker_nao_usa_onedrive():
    orig = models.em_docker
    models.em_docker = lambda: True
    old = os.environ.get('FISCAL_XML_DIR')
    os.environ.pop('FISCAL_XML_DIR', None)
    try:
        p = models.pasta_xml()
        assert 'OneDrive' not in p.replace('\\', '/')
        assert 'TESTENFE' not in p
    finally:
        models.em_docker = orig
        if old is None:
            os.environ.pop('FISCAL_XML_DIR', None)
        else:
            os.environ['FISCAL_XML_DIR'] = old


if __name__ == '__main__':
    test_parece_caminho_windows()
    test_resolver_data_dir_no_docker()
    test_pasta_xml_em_docker_nao_usa_onedrive()
    print('ok paths')
