# -*- coding: utf-8 -*-
"""ADN: a distribuicao exige ?cnpjConsulta=&lote=true e o corpo do erro vai pro historico."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines import nfse


class _Resp:
    def __init__(self, corpo, texto=None):
        self._c = corpo; self.text = texto if texto is not None else json.dumps(corpo)
    def json(self):
        if self._c is None: raise ValueError('nao e json')
        return self._c


def test_params_cnpjconsulta_e_lote():
    assert nfse._params('25.278.860/0001-12') == {'cnpjConsulta': '25278860000112', 'lote': 'true'}


def test_motivo_erro_le_erros_do_adn():
    r = _Resp({'StatusProcessamento': 'REJEICAO',
               'Erros': [{'Codigo': 'E2243', 'Descricao': 'CNPJ de outra raiz'}]})
    assert nfse._motivo_erro(r) == 'REJEICAO | E2243 CNPJ de outra raiz'


def test_motivo_erro_sem_json():
    assert nfse._motivo_erro(_Resp(None, 'Bad Request')) == 'Bad Request'


def test_extrair_docs_traz_tipo():
    import base64, gzip
    b = base64.b64encode(gzip.compress(b'<NFSe/>')).decode()
    docs = nfse._extrair_docs({'LoteDFe': [{'NSU': 5, 'TipoDocumento': 'EVENTO', 'ArquivoXml': b}]})
    assert docs == [(5, '<NFSe/>', 'EVENTO')]


def test_evento_vai_para_pasta_propria():
    assert nfse.sub_doc('<evento><infEvento/></evento>', '35736034000123', 'EVENTO') == '03_eventos'
    assert nfse.sub_doc('<pedRegEvento/>', '35736034000123') == '03_eventos'


def test_nota_tomada_e_emitida():
    cn = '35736034000123'
    emit = '<NFSe><emit><CNPJ>%s</CNPJ></emit><toma><CNPJ>11111111000111</CNPJ></toma></NFSe>' % cn
    toma = '<NFSe><emit><CNPJ>11111111000111</CNPJ></emit><toma><CNPJ>%s</CNPJ></toma></NFSe>' % cn
    assert nfse.sub_doc(emit, cn, 'NFSE') == '02_prestado'
    assert nfse.sub_doc(toma, cn, 'NFSE') == '01_tomado'


def test_detalhe_separa_contagem():
    d = nfse._detalhe(134, {'02_prestado': 0, '01_tomado': 10, '03_eventos': 2})
    assert d == 'ultNSU=134 · emitidas 0, tomadas 10, eventos 2'
