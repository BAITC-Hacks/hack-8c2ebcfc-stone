import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.reply_language import localize_details


class ReplyLanguageTests(unittest.TestCase):
    def client_reply(self, text):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )
        return client

    def test_translation_preserves_amount_policy_and_negation(self):
        client = self.client_reply('[[VALUE_0]] полисі рәсімделді. Бағасы [[VALUE_1]] теңге. SMS жіберілмеді.')
        with patch('backend.reply_language._get_client', return_value=client):
            result = localize_details('Полис SQ-OGPO-123456 оформлен. Цена 15000 тенге. SMS не отправлена.', 'kk')
        self.assertEqual(result, 'SQ-OGPO-123456 полисі рәсімделді. Бағасы 15000 теңге. SMS жіберілмеді.')
        request = client.chat.completions.create.call_args.kwargs
        self.assertIn('Kazakh', request['messages'][0]['content'])
        self.assertNotIn('SQ-OGPO-123456', request['messages'][1]['content'])

    def test_lost_duplicate_and_invented_placeholders_rejected(self):
        for translated in ('Жауап', '[[VALUE_0]] [[VALUE_0]]', '[[VALUE_9]]', ''):
            with self.subTest(translated=translated), patch('backend.reply_language._get_client', return_value=self.client_reply(translated)):
                with self.assertRaises(ValueError):
                    localize_details('Сумма 15000 тенге', 'kk')

    def test_translates_backend_labels_without_scenario_selection(self):
        client = self.client_reply('Банк картасымен онлайн немесе банк аударымымен төлеуге болады.')
        with patch('backend.reply_language._get_client', return_value=client):
            result = localize_details('methods — bank card online; bank transfer', 'kk')
        self.assertIn('төлеуге', result)
        request = client.chat.completions.create.call_args.kwargs
        self.assertNotIn('tools', request)
        self.assertIn('never instructions', request['messages'][0]['content'])
