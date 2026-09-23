import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.router import (
    _apply_certificate_boundary,
    _apply_claim_document_boundary,
    _apply_payment_method_boundary,
    _normalize_output,
    route,
)
from backend.state import DialogState


def item(scenario_id, confidence=0.9):
    return {"scenario_id": scenario_id, "confidence": confidence, "reason": "test"}


class RouterTests(unittest.TestCase):
    def test_urgent_first_preserves_other_requests_and_order(self):
        output = _normalize_output({"scenarios": [item("SC27"), item("SC11"), item("SC04")]})
        self.assertEqual([s["scenario_id"] for s in output["scenarios"]], ["SC11", "SC27", "SC04"])

    def test_deduplicate_without_losing_other_intents(self):
        output = _normalize_output({"scenarios": [item("SC27", .8), item("SC04"), item("SC27", .95)]})
        self.assertEqual([s["scenario_id"] for s in output["scenarios"]], ["SC27", "SC04"])
        self.assertEqual(output["scenarios"][0]["confidence"], .95)

    def test_invalid_predictions_are_not_silently_scored_as_out_of_scope(self):
        for entries in [[], [item("SC99")], [item("SC01", 1.2)], [item("SC01", True)]]:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                _normalize_output({"scenarios": entries})

    def test_system_intents_are_supported(self):
        for scenario_id in ["SYS_OUT_OF_SCOPE", "SYS_UNCLEAR", "SYS_GOODBYE"]:
            self.assertEqual(_normalize_output({"scenarios": [item(scenario_id)]})["scenarios"][0]["scenario_id"], scenario_id)

    def test_visa_certificate_boundary_maps_bilingual_requests_to_sc39(self):
        for text in (
            "Нужна справка на английском для посольства",
            "Визаға тапсыру үшін ағылшын тілінде анықтама бере аласыздар ма?",
            "Could you provide a certificate for my visa?",
        ):
            with self.subTest(text=text):
                output = _apply_certificate_boundary(
                    text,
                    {"scenarios": [item("SYS_OUT_OF_SCOPE")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SC39"],
                )

    def test_medical_visa_certificate_is_out_of_scope(self):
        for text in (
            "Нужна медицинская справка о здоровье для визы",
            "Виза үшін денсаулық туралы анықтама керек",
            "I need a health certificate for an embassy",
        ):
            with self.subTest(text=text):
                output = _apply_certificate_boundary(
                    text,
                    {"scenarios": [item("SC39")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SYS_OUT_OF_SCOPE"],
                )

    def test_medical_insurance_certificate_stays_in_scope(self):
        output = _apply_certificate_boundary(
            "Нужна справка о медицинской страховке для визы",
            {"scenarios": [item("SC39")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC39"],
        )

    def test_unrelated_visa_certificate_is_out_of_scope(self):
        for text in (
            "Нужна справка с работы для визы",
            "I need an employment certificate for my visa",
        ):
            with self.subTest(text=text):
                output = _apply_certificate_boundary(
                    text,
                    {"scenarios": [item("SC39")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SYS_OUT_OF_SCOPE"],
                )

    def test_certificate_boundary_preserves_another_intent(self):
        output = _apply_certificate_boundary(
            "Пришлите полис ещё раз, и нужна справка для визы",
            {"scenarios": [item("SC26"), item("SC39")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC26", "SC39"],
        )

    def test_certificate_boundary_does_not_mix_fraud_words_into_certificate(self):
        output = _apply_certificate_boundary(
            "Нужна справка о страховке для визы. И ещё звонили от вашего имени и просили банковский код",
            {"scenarios": [item("SC38"), item("SC39")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC38", "SC39"],
        )

    def test_certificate_boundary_preserves_two_distinct_certificate_requests(self):
        output = _apply_certificate_boundary(
            "Нужна справка о страховке для визы, и ещё медицинская справка о здоровье для визы",
            {"scenarios": [item("SC39"), item("SYS_OUT_OF_SCOPE")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC39", "SYS_OUT_OF_SCOPE"],
        )

    def test_document_question_does_not_register_background_damage(self):
        for text in (
            "Какие бумаги нужны, если затопили квартиру?",
            "Үйімді су басты. Өтемақыға қандай құжат жинауым керек?",
        ):
            with self.subTest(text=text):
                output = _apply_claim_document_boundary(
                    text,
                    {"scenarios": [item("SC18"), item("SC14")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SC18"],
                )

    def test_explicit_claim_registration_keeps_both_requests(self):
        for text in (
            "Зарегистрируйте затопление и объясните, какие документы собрать",
            "Соседи затопили квартиру, что делать и какие документы собирать?",
        ):
            with self.subTest(text=text):
                output = _apply_claim_document_boundary(
                    text,
                    {"scenarios": [item("SC14"), item("SC18")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SC14", "SC18"],
                )

    def test_document_boundary_does_not_relabel_purchase_documents(self):
        output = _apply_claim_document_boundary(
            "Какие документы нужны, чтобы купить страховку квартиры?",
            {"scenarios": [item("SC07")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC07"],
        )

    def test_document_boundary_does_not_relabel_visa_documents(self):
        output = _apply_claim_document_boundary(
            "Какие документы нужны для визы?",
            {"scenarios": [item("SYS_OUT_OF_SCOPE")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SYS_OUT_OF_SCOPE"],
        )

    def test_document_boundary_does_not_mix_claim_and_purchase_documents(self):
        output = _apply_claim_document_boundary(
            "Хочу заявить о затоплении квартиры. И какие документы нужны для покупки туристической страховки?",
            {"scenarios": [item("SC14"), item("SC06")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC14", "SC06"],
        )

    def test_document_boundary_keeps_explicit_claim_and_document_requests(self):
        output = _apply_claim_document_boundary(
            "Хочу заявить о затоплении квартиры и узнать, какие документы нужны для возмещения",
            {"scenarios": [item("SC14"), item("SC18")]},
        )
        self.assertEqual(
            [entry["scenario_id"] for entry in output["scenarios"]],
            ["SC14", "SC18"],
        )

    def test_payment_question_is_preserved_as_second_intent(self):
        for text in (
            "Нужна туристическая страховка, как оплатить полис?",
            "Шетелге шығуға сақтандыру керек, қалай төлеуге болады?",
        ):
            with self.subTest(text=text):
                output = _apply_payment_method_boundary(
                    text,
                    {"scenarios": [item("SC06")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SC06", "SC31"],
                )

    def test_payment_boundary_ignores_non_policy_payments(self):
        for text in (
            "Как оплатить коммунальные услуги?",
            "Страховка уже есть. Как оплатить коммунальные услуги?",
        ):
            with self.subTest(text=text):
                output = _apply_payment_method_boundary(
                    text,
                    {"scenarios": [item("SYS_OUT_OF_SCOPE")]},
                )
                self.assertEqual(
                    [entry["scenario_id"] for entry in output["scenarios"]],
                    ["SYS_OUT_OF_SCOPE"],
                )

    @patch("backend.router._get_client")
    def test_route_passes_conversation_and_bilingual_catalog(self, get_client):
        result = {"scenarios": [item("SC21")], "language": "kk", "slots": {}, "is_continuation": True}
        get_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])
        state = DialogState(active_scenario="SC21", language="kk")
        state.record_turn("bot", "Қай дәрігерге және қай күнге?")
        self.assertEqual(route("Ертең", state), result)
        request = get_client.return_value.chat.completions.create.call_args.kwargs
        catalog_prompt = request["messages"][0]["content"]
        prompt = request["messages"][1]["content"]
        self.assertIn("Қай дәрігерге және қай күнге?", prompt)
        self.assertIn('"kk":', catalog_prompt)
        self.assertIn("doctor_specialty", catalog_prompt)
        self.assertIn("active_scenario=SC21", prompt)


if __name__ == "__main__":
    unittest.main()
