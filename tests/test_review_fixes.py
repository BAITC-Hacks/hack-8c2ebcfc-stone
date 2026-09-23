import unittest

from backend import actions_mock
from backend.actions_mock import ActionError, call_action
from backend.confirmation import classify_confirmation
from backend.executor import run_scenario
from backend.state import DialogState
from backend.slot_normalization import normalize_slot
from backend.triage import normalize_phone


class ReviewFixes(unittest.TestCase):
    def test_confirmation_words(self):
        for text in ("не надо", "пока нет", "не подтверждаю", "Жоқ"):
            self.assertEqual(classify_confirmation(text), "no", text)
        for text in ("Иә, растаймын.", "Верно.", "Да"):
            self.assertEqual(classify_confirmation(text), "yes", text)
        self.assertEqual(classify_confirmation("да, нет"), "unclear")

    def test_session_store_lifecycle(self):
        first, second = DialogState(), DialogState()
        kwargs = {"product_type": "travel", "phone": "+77010000001"}
        number = call_action("create_policy", _store=first.mock_data, **kwargs)["policy_number"]
        self.assertEqual(call_action("get_policy", _store=first.mock_data, policy_number=number)["policy_number"], number)
        with self.assertRaises(ActionError):
            call_action("get_policy", _store=second.mock_data, policy_number=number)
        call_action("update_contact", _store=first.mock_data, client_id="C001", contact_field="email", new_value="changed@mail.example")
        self.assertEqual(first.mock_data["clients"][0]["email"], "changed@mail.example")
        self.assertNotEqual(second.mock_data["clients"][0]["email"], "changed@mail.example")
        call_action("renew_policy", _store=first.mock_data, policy_number=number)
        self.assertTrue(first.mock_data["policies"][-1]["end_date"] > "2027-10-01")
        call_action("cancel_policy", _store=first.mock_data, policy_number=number)
        with self.assertRaises(ActionError) as error:
            call_action("cancel_policy", _store=first.mock_data, policy_number=number)
        self.assertEqual(error.exception.code, "already_done")
        self.assertEqual(call_action("get_policy", _store=first.mock_data, policy_number=number)["status"], "cancelled")
        claim = call_action(
            "create_claim", _store=first.mock_data, product_type="ogpo",
            policy_number="SQ-OGPO-104501", incident_date="2026-09-28",
            incident_description="collision", client_id="C001",
        )["claim_number"]
        self.assertEqual(call_action("get_claim", _store=first.mock_data, claim_number=claim)["status"], "registered")
        self.assertIn("slot_datetime", call_action(
            "book_inspection", _store=first.mock_data, claim_number=claim,
            city="Almaty", preferred_date="2026-10-02",
        ))

    def test_kb_product_and_claim_documents(self):
        for scenario_id, slots in (
            ("SC03", {"car_value": 12000000, "car_year": 2019}),
            ("SC07", {"property_type": "apartment", "sum_insured": 5000000}),
            ("SC08", {"sum_insured": 1000000}),
            ("SC09", {}),
            ("SC40", {"topic": "франшиза"}),
        ):
            self.assertEqual(run_scenario(scenario_id, DialogState(slots=slots))["status"], "done", scenario_id)
        for product in ("ogpo", "casco", "property", "accident", "travel", "dms"):
            result = run_scenario("SC18", DialogState(slots={"product_type": product}))
            self.assertEqual(result["status"], "done", product)
            self.assertTrue(result["actions"][0]["result"]["answer"])
            if product == "property":
                self.assertIn("Act from the building", str(result["actions"][0]["result"]))
        self.assertTrue(call_action("kb_lookup", topic="claims.submission")["answer"])
        self.assertEqual(run_scenario("SC24", DialogState(client_id="C001", slots={"phone": "+77010000001"}))["status"], "done")

    def test_optional_sms_and_queues(self):
        for scenario_id in ("SC23", "SC33"):
            result = run_scenario(scenario_id, DialogState(slots={"city": "Almaty"}))
            self.assertEqual(result["status"], "done")
            self.assertEqual(result["actions"][-1]["mode"], "skipped")
        cases = (
            ("SC10", {"company_name": "Test", "employees_count": 10, "phone": "+77010000001"}, "corporate_sales"),
            ("SC15", {"policy_number": "SQ-TRVL-304552", "location": "Germany", "incident_description": "illness"}, "medical_assistance_24_7"),
            ("SC35", {"complaint_text": "оператор нагрубил"}, "complaints_team"),
            ("SC38", {"fraud_details": "сообщил код карты"}, "security_team"),
        )
        for scenario_id, slots, queue in cases:
            state = DialogState(client_id="C001", slots=slots)
            result = run_scenario(scenario_id, state)
            self.assertEqual(result["status"], "done", (scenario_id, result))
            self.assertEqual(result["actions"][-1]["result"]["queue"], queue)
        result = run_scenario("SC37", DialogState())
        self.assertEqual(result["actions"][-1]["result"]["queue"], "operator_general")

    def test_sms_failure_does_not_undo_renewal(self):
        state = DialogState(client_id="C001", slots={"policy_number": "SQ-OGPO-104501"})
        original_end = next(p for p in state.mock_data["policies"] if p["policy_number"] == "SQ-OGPO-104501")["end_date"]
        original_sms = actions_mock._IMPLEMENTED["send_sms"]
        def fail_sms(**_):
            raise ActionError("delivery_failed", "simulated")
        actions_mock._IMPLEMENTED["send_sms"] = fail_sms
        try:
            result = run_scenario("SC27", state, confirmed=True)
        finally:
            actions_mock._IMPLEMENTED["send_sms"] = original_sms
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["actions"][-1]["mode"], "failed")
        self.assertIsNotNone(state.pending_sms)
        new_end = next(p for p in state.mock_data["policies"] if p["policy_number"] == "SQ-OGPO-104501")["end_date"]
        self.assertGreater(new_end, original_end)

    def test_spoken_phone(self):
        self.assertEqual(normalize_phone("Плюс жеті, жеті жүз бір, нөл нөл нөл, нөл нөл, нөл екі."), "+77010000002")
        self.assertEqual(normalize_phone("Телефон: плюс жеті, жеті жүз бір, нөл нөл нөл, нөл нөл, он."), "+77010000010")
        self.assertIsNone(normalize_phone("850314300121"))
        self.assertIsNone(normalize_slot("phone", ["850314300121"]))
        self.assertEqual(normalize_slot("phone", ["8 701 555 12 34"]), "+77015551234")
        self.assertEqual(normalize_slot("culprit_vehicle_plate", ["777abc02"]), "777ABC02")

    def test_previews_are_safe_and_stable(self):
        state = DialogState(client_id="C001", slots={"contact_field": "email", "new_value": "new@mail.example"})
        before = state.mock_data["clients"][0]["email"]
        result = run_scenario("SC29", state)
        self.assertEqual(result["status"], "need_confirmation")
        self.assertIn("n***@mail.example", result["confirmation"])
        self.assertNotIn("new@mail.example", result["confirmation"])
        self.assertEqual(state.mock_data["clients"][0]["email"], before)
        self.assertEqual(run_scenario("SC29", state, confirmed=True)["status"], "done")
        self.assertEqual(state.mock_data["clients"][0]["email"], "new@mail.example")
        state = DialogState(client_id="C001", slots={"policy_number": "SQ-OGPO-104501", "cancel_reason": "moving"})
        result = run_scenario("SC28", state)
        self.assertIn("refund_amount", result["confirmation"])
        self.assertNotEqual(state.mock_data["policies"][0].get("status"), "cancelled")
        state.set_slot("cancel_reason", "different")
        self.assertEqual(run_scenario("SC28", state, confirmed=True)["status"], "confirmation_expired")
        appointment = DialogState(client_id="C002", slots={
            "policy_number": "SQ-DMS-604220", "doctor_specialty": "dentist",
            "city": "Astana", "preferred_date": "2026-10-02",
        })
        result = run_scenario("SC21", appointment)
        self.assertEqual(result["status"], "need_confirmation")
        self.assertIn("clinic_name", result["confirmation"])
        self.assertFalse(appointment.mock_data.get("appointments"))

    def test_dms_coverage(self):
        for name in ("MRI", "МРТ"):
            self.assertTrue(call_action("check_coverage", policy_number="SQ-DMS-604220", service_name=name)["covered"])
        result = call_action("book_appointment", policy_number="SQ-DMS-604220", doctor_specialty="dentist", city="Astana", preferred_date="2026-10-02")
        self.assertIn("clinic_name", result)
        self.assertFalse(call_action("check_coverage", policy_number="SQ-DMS-604220", service_name="протезирование")["covered"])
        state = DialogState()
        basic = dict(next(p for p in state.mock_data["policies"] if p["policy_number"] == "SQ-DMS-604220"))
        basic["policy_number"] = "SQ-DMS-604219"
        basic["details"] = {**basic["details"], "package": "Basic"}
        state.mock_data["policies"].append(basic)
        self.assertFalse(call_action("check_coverage", _store=state.mock_data, policy_number="SQ-DMS-604219", service_name="MRI")["covered"])

    def test_claim_date_and_victim_policy(self):
        result = call_action("create_claim", product_type="ogpo", policy_number="SQ-OGPO-102850", incident_date="2026-09-28", incident_description="accident")
        self.assertEqual(call_action("get_claim", claim_number=result["claim_number"])["status"], "registered")
        with self.assertRaises(ActionError):
            call_action("create_claim", product_type="ogpo", policy_number="SQ-OGPO-102850", incident_date="2025-01-01", incident_description="accident")
        with self.assertRaises(ActionError) as error:
            call_action("create_claim", product_type="ogpo", policy_number="SQ-OGPO-102850", incident_date="bad", incident_description="accident")
        self.assertEqual(error.exception.code, "invalid_input")
        state = DialogState(client_id="C004", slots={"policy_number": "SQ-DMS-604220", "culprit_vehicle_plate": "777ABC02", "incident_date": "2026-09-28", "incident_description": "rear end", "phone": "+77010000004"})
        self.assertEqual(run_scenario("SC12", state, confirmed=True)["status"], "done")
        self.assertEqual(state.mock_data["claims"][-1]["policy_number"], "SQ-OGPO-104501")

    def test_travel_country(self):
        kwargs = {"trip_start": "2026-10-10", "trip_end": "2026-10-16", "travelers_count": 2, "traveler_max_age": 42}
        german = call_action("calc_travel_price", trip_country="Germany", **kwargs)
        self.assertEqual(call_action("calc_travel_price", trip_country="Германия", **kwargs), german)
        self.assertEqual(call_action("calc_travel_price", trip_country="Poland", **kwargs)["zone"], "B")
        with self.assertRaises(ActionError):
            call_action("calc_travel_price", trip_country="unknownland", **kwargs)


if __name__ == "__main__":
    unittest.main()
