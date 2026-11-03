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
