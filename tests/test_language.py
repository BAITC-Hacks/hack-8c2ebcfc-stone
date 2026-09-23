import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.language import detect_language, update_language


class ConversationLanguageTests(unittest.TestCase):
    def test_cyrillic_code_switching(self):
        for text in (
            "Здравствуйте, менің полисім бойынша сұрақ бар",
            "Полис нужен, сақтандыру бағасы қанша?",
            "Мен онлайн полис алсам бола ма, можно оформить?",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_language(text), "mixed")

    def test_kazakh_without_distinctive_letters(self):
        self.assertEqual(detect_language("Мен онлайн полис алсам бола ма"), "kk")

    def test_shared_acronyms_do_not_imply_mixed_language(self):
        for text, expected in (
            ("КАСКО бойынша SMS керек", "kk"),
            ("Пришлите SMS на телефон", "ru"),
            ("ДМС полисім бойынша сұрақ", "kk"),
            ("Хочу оформить OGPO", "ru"),
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_language(text), expected)

    def test_neutral_identity_preserves_conversation_language(self):
        for text in ("+77010000001", "900101300123", "SQ-OGPO-000123",
                     "CL-000003", "person@example.com", "SMS", "", "123 ABC 02"):
            with self.subTest(text=text):
                state = SimpleNamespace(language="kk", response_language="kk")
                update_language(state, text, router_language="ru")
                self.assertEqual(state.language, "kk")
                self.assertEqual(state.response_language, "kk")
                self.assertEqual(detect_language(text, previous="kk"), "kk")

    def test_router_language_overrides_fallback_for_real_utterance(self):
        state = SimpleNamespace(language="ru", response_language="ru")
        # Simulates a linguistic request the inexpensive fallback cannot identify.
        update_language(state, "Полис жасату", router_language="kk")
        self.assertEqual(state.language, "kk")
        self.assertEqual(state.response_language, "kk")

    def test_mixed_reply_uses_kazakh_and_next_russian_turn_switches(self):
        state = SimpleNamespace(language="ru", response_language="ru")
        update_language(state, "Здравствуйте, менің полисім бойынша сұрақ бар", "mixed")
        self.assertEqual((state.language, state.response_language), ("mixed", "kk"))
        update_language(state, "77010000001")
        self.assertEqual((state.language, state.response_language), ("mixed", "kk"))
        update_language(state, "Хорошо, скажите по русски")
        self.assertEqual((state.language, state.response_language), ("ru", "ru"))

    def test_kazakh_confirmation_uses_kazakh_without_router_call(self):
        state = SimpleNamespace(language="ru", response_language="ru")
        update_language(state, "Иә, растаймын")
        self.assertEqual((state.language, state.response_language), ("kk", "kk"))

    def test_invalid_router_label_uses_local_fallback(self):
        state = SimpleNamespace(language="ru", response_language="ru")
        update_language(state, "Сақтандыру керек", router_language="unknown")
        self.assertEqual((state.language, state.response_language), ("kk", "kk"))

    def test_mixed_language_reply_follows_dominant_words(self):
        for text, expected in (
            ("Здравствуйте, менің полисім бойынша сұрақ бар", "kk"),
            ("Мне нужен полис, пожалуйста, расскажите как оформить, рахмет", "ru"),
            ("Хочу узнать сколько нужно оплатить за полис, рахмет", "ru"),
        ):
            with self.subTest(text=text):
                state = SimpleNamespace(language="ru", response_language="ru")
                update_language(state, text, router_language="mixed")
                self.assertEqual(state.language, "mixed")
                self.assertEqual(state.response_language, expected)

    def test_explicit_mixed_reply_language_overrides_local_estimate(self):
        text = "Здравствуйте, менің полисім бойынша сұрақ бар"
        for response in ("ru", "kk"):
            with self.subTest(response=response):
                state = SimpleNamespace(language="ru", response_language="ru")
                update_language(state, text, "mixed", response_language=response)
                self.assertEqual((state.language, state.response_language), ("mixed", response))

    def test_pure_language_wins_over_inconsistent_reply_language(self):
        for text, language, inconsistent in (
            ("Хочу купить полис", "ru", "kk"),
            ("Сақтандыру керек", "kk", "ru"),
        ):
            with self.subTest(language=language):
                state = SimpleNamespace(language="mixed", response_language="kk")
                update_language(state, text, language, response_language=inconsistent)
                self.assertEqual((state.language, state.response_language), (language, language))

    def test_clear_current_language_wins_over_incorrect_router_labels(self):
        for text, expected in (
            ("Добрый день! Сколько будет стоить обязательная страховка на машину?", "ru"),
            ("Сақтандыру керек", "kk"),
            ("Мен онлайн полис алсам бола ма", "kk"),
        ):
            for previous in ("ru", "kk", "mixed"):
                for model_language in ("ru", "kk", "mixed"):
                    with self.subTest(text=text, previous=previous, model=model_language):
                        state = SimpleNamespace(language=previous, response_language="kk")
                        update_language(state, text, model_language, response_language="kk")
                        self.assertEqual((state.language, state.response_language), (expected, expected))

    def test_invalid_mixed_reply_language_falls_back_to_dominant_words(self):
        state = SimpleNamespace(language="ru", response_language="ru")
        update_language(state, "Здравствуйте, менің полисім бойынша сұрақ бар", "mixed", "en")
        self.assertEqual(state.response_language, "kk")

    def test_sparse_local_evidence_preserves_model_code_switching(self):
        for text, reply_language in (
            ("Расскажите про сақтандыру", "ru"),
            ("Добрый день, сақтандыру керек", "ru"),
            ("Здравствуйте, полис жасату", "kk"),
        ):
            with self.subTest(text=text):
                state = SimpleNamespace(language="ru", response_language="ru")
                update_language(state, text, "mixed", reply_language)
                self.assertEqual((state.language, state.response_language), ("mixed", reply_language))

    def test_neutral_identity_ignores_both_model_language_fields(self):
        state = SimpleNamespace(language="mixed", response_language="ru")
        update_language(state, "SQ-OGPO-000123", "mixed", "kk")
        self.assertEqual((state.language, state.response_language), ("mixed", "ru"))

    def test_equal_mixed_signals_keep_previous_reply_language(self):
        state = SimpleNamespace(language="kk", response_language="kk")
        update_language(state, "Здравствуйте, рақмет", "mixed")
        self.assertEqual((state.language, state.response_language), ("mixed", "kk"))

    def test_dataset_mixed_turns_have_appropriate_reply_language(self):
        for text, previous, expected in (
            ("Кеше аварияға түстім, но я не виноват, виновник у вас застрахован", "ru", "ru"),
            ("Маған справка керек для посольства, на английском", "ru", "ru"),
            ("Сәлеметсіз бе, маған терапевтке жазылу керек, завтра утром можно?", "ru", "kk"),
            ("Иә, жазыңыз. А анализы тоже бесплатно по страховке?", "kk", "kk"),
        ):
            with self.subTest(text=text):
                state = SimpleNamespace(language=previous, response_language=previous)
                update_language(state, text, "mixed")
                self.assertEqual(state.response_language, expected)

    def test_clear_code_switching_corrects_pure_router_labels(self):
        for text in (
            "Сәлеметсіз бе, ОГПО оформить етейін деп едім",
            "Сәлеметсіз бе, полисім действует ли ещё, тексеріп беріңізші",
            "Қосымшаға кіре алмай жатырмын, код не приходит",
        ):
            for model_language in ("ru", "kk"):
                with self.subTest(text=text, model_language=model_language):
                    state = SimpleNamespace(language="ru", response_language="ru")
                    update_language(state, text, model_language)
                    self.assertEqual(state.language, "mixed")

    def test_ambiguous_kazakh_particles_do_not_trigger_mixed(self):
        for text in (
            "Кінәлі жүргізушінің полисі Saqta-да, маған төлем керек",
            "Үйде өрт шықты, сақтандыру бойынша не істеймін?",
            "Маған да полис керек",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_language(text), "kk")
                state = SimpleNamespace(language="ru", response_language="ru")
                update_language(state, text, "kk")
                self.assertEqual(state.language, "kk")

    def test_local_correction_preserves_all_dataset_language_labels(self):
        dataset = Path(__file__).resolve().parents[1] / "data/case_2/voice_router_dataset/dev_utterances.json"
        rows = json.loads(dataset.read_text(encoding="utf-8"))["utterances"]
        for row in rows:
            with self.subTest(utterance=row["id"]):
                state = SimpleNamespace(language="ru", response_language="ru")
                update_language(state, row["text"], row["lang"])
                self.assertEqual(state.language, row["lang"])


if __name__ == "__main__":
    unittest.main()
