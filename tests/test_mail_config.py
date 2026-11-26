# -*- coding: utf-8 -*-
import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines import mail


class TestMailConfig(unittest.TestCase):
    def _env(self, **kw):
        return mock.patch.dict(os.environ, kw, clear=True)

    def test_vazio(self):
        with self._env():
            self.assertFalse(mail.is_smtp_configured())
            self.assertEqual(mail.smtp_faltando(), ['SMTP_HOST', 'SMTP_USER', 'SMTP_PASS'])

    def test_padrao_sem_from_usa_user(self):
        with self._env(SMTP_HOST='smtp.gmail.com', SMTP_USER='a@b.com', SMTP_PASS='x'):
            self.assertTrue(mail.is_smtp_configured())
            self.assertEqual(mail._smtp_params()[-1], 'a@b.com')

    def test_user_sem_arroba_exige_from(self):
        with self._env(SMTP_HOST='h', SMTP_USER='apikey', SMTP_PASS='x'):
            self.assertEqual(mail.smtp_faltando(), ['SMTP_FROM'])

    def test_aliases_e_aspas(self):
        with self._env(MAIL_HOST='"smtp.x.com"', SMTP_USERNAME='u@x.com',
                       SMTP_PASSWORD='p', MAIL_PORT='465'):
            self.assertTrue(mail.is_smtp_configured())
            host, port, secure, user, pw, frm = mail._smtp_params()
            self.assertEqual((host, port, secure, user, pw), ('smtp.x.com', 465, True, 'u@x.com', 'p'))

    def test_valor_em_branco_nao_conta(self):
        with self._env(SMTP_HOST='  ', SMTP_USER='a@b.com', SMTP_PASS='x'):
            self.assertFalse(mail.is_smtp_configured())


if __name__ == '__main__':
    unittest.main()
