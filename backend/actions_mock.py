import random
import re
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime

from backend.data_loader import actions_catalog, mock_backend, knowledge_base

SNAPSHOT_DATE = date(2026, 10, 1)
_DEFAULT_STORE = deepcopy(mock_backend())
_CURRENT_STORE: ContextVar[dict | None] = ContextVar("mock_store", default=None)


def _data() -> dict:
    return _CURRENT_STORE.get() or _DEFAULT_STORE

_PRODUCT_CODE = {
    "ogpo": "OGPO",
    "casco": "CASCO",
    "travel": "TRVL",
    "property": "PROP",
    "accident": "NS",
    "dms": "DMS",
}

_TRAVEL_ZONE_BY_COUNTRY = {
    # Zone A: CIS + Georgia
    "russia": "A", "uzbekistan": "A", "kyrgyzstan": "A", "tajikistan": "A",
    "armenia": "A", "azerbaijan": "A", "belarus": "A", "georgia": "A",
    # Zone B: Schengen + UK
    "germany": "B", "france": "B", "italy": "B", "spain": "B", "greece": "B",
    "uk": "B", "united kingdom": "B", "netherlands": "B", "austria": "B",
    # Zone D: USA + Canada
    "usa": "D", "united states": "D", "us": "D", "canada": "D",
    # Zone C: everything else (default)
    "turkey": "C", "uae": "C", "thailand": "C", "egypt": "C",
}
_TRAVEL_ZONE_BY_COUNTRY.update({
    country: "B" for country in (
        "poland", "belgium", "bulgaria", "croatia", "czech republic", "denmark",
        "estonia", "finland", "hungary", "iceland", "latvia", "liechtenstein",
        "lithuania", "luxembourg", "malta", "norway", "portugal", "romania",
        "slovakia", "slovenia", "sweden", "switzerland",
    )
})
_COUNTRY_ALIASES = {
    "германия": "germany", "германияға": "germany", "алмания": "germany",
    "польша": "poland", "польшаға": "poland",
    "франция": "france", "италия": "italy", "испания": "spain",
    "турция": "turkey", "түркия": "turkey", "қазақстан": "kazakhstan",
    "оаэ": "uae", "біріккен араб әмірліктері": "uae",
    "сша": "usa", "ақш": "usa", "канада": "canada",
    "россия": "russia", "ресей": "russia", "узбекистан": "uzbekistan",
    "өзбекстан": "uzbekistan", "грузия": "georgia", "грузияға": "georgia",
    "таиланд": "thailand", "тайланд": "thailand", "египет": "egypt",
    "мысыр": "egypt", "великобритания": "uk", "ұлыбритания": "uk",
}


class ActionError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


# ---------- helpers ----------

def _find_client(client_id: str | None = None, phone: str | None = None, iin: str | None = None) -> dict | None:
    for c in _data()["clients"]:
        if client_id and c.get("client_id") == client_id:
            return c
        if phone and c.get("phone") == phone:
            return c
        if iin and c.get("iin") == iin:
            return c
    return None


def _find_policy(policy_number: str | None = None, vehicle_plate: str | None = None) -> dict | None:
    for p in _data()["policies"]:
        if policy_number and p.get("policy_number") == policy_number:
            return p
        if vehicle_plate and p.get("details", {}).get("vehicle_plate") == vehicle_plate:
            return p
    return None


def _policy_active(policy: dict) -> bool:
    if policy.get("status") == "cancelled":
        return False
    try:
        end = datetime.strptime(policy["end_date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return True
    return end >= SNAPSHOT_DATE


def _bm_class_for_iin(iin: str) -> str:
    for p in _data()["policies"]:
        if iin in p.get("details", {}).get("drivers_iin", []) and "bm_class" in p:
            return p["bm_class"]
    for c in _data()["clients"]:
        if c.get("iin") == iin and c.get("bm_class"):
            return c["bm_class"]
    return _data()["defaults"]["unknown_iin_bm_class"]


def _new_id(prefix: str, width: int = 6) -> str:
    return f"{prefix}-{random.randint(0, 10 ** width - 1):0{width}d}"


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


# ---------- already implemented (kept as-is) ----------

def find_client(client_id: str | None = None, phone: str | None = None, iin: str | None = None, **_) -> dict:
    # client_id is injected automatically once the caller has already been
    # identified earlier in the dialog (see executor._call_kwargs), so a
    # scenario pipeline that re-runs find_client() downstream should just
    # confirm the already-known client rather than requiring phone/iin again
    if client_id:
        c = _find_client(client_id=client_id)
        if c:
            return {"client_id": c["client_id"], "full_name": c["full_name"]}
    c = _find_client(phone=phone, iin=iin)
    if not c:
        raise ActionError("not_found", "no client matches phone/iin")
    return {"client_id": c["client_id"], "full_name": c["full_name"]}


def get_policies(client_id: str, **_) -> dict:
    policies = [p for p in _data()["policies"] if p.get("client_id") == client_id]
    if not policies:
        raise ActionError("not_found", "no policies for this client")
    return {"policies": policies}


def get_policy(policy_number: str | None = None, vehicle_plate: str | None = None, **_) -> dict:
    p = _find_policy(policy_number=policy_number, vehicle_plate=vehicle_plate)
    if not p:
        raise ActionError("not_found", "no policy matches")
    return p


def get_bm_class(iin: str, **_) -> dict:
    return {"bm_class": _bm_class_for_iin(iin)}


# ---------- pricing ----------

def calc_ogpo_price(region: str | None = None, vehicle_type: str = "car",
                     drivers_iin: list | None = None, vehicle_plate: str | None = None,
                     term_months: int = 12, **_) -> dict:
    pricing = knowledge_base()["products"]["ogpo"]["pricing"]

    if not region and vehicle_plate:
        code = vehicle_plate[-2:]
        region = pricing["region_by_plate_code"].get(code, pricing["region_by_plate_code"]["default"])
    region = (region or "other").lower()
    base = pricing["base_by_region_kzt"].get(region)
    if base is None:
        raise ActionError("invalid_input", f"unknown region '{region}'")

    vt_coef = pricing["vehicle_type_coef"].get(vehicle_type)
    if vt_coef is None:
        raise ActionError("invalid_input", f"unknown vehicle_type '{vehicle_type}'")

    drivers_iin = drivers_iin or []
    if isinstance(drivers_iin, str):
        drivers_iin = [drivers_iin]
    if not drivers_iin:
        raise ActionError("invalid_input", "at least one driver IIN is required")
    bm_coefs = [pricing["bm_coef"].get(_bm_class_for_iin(iin), 1.0) for iin in drivers_iin]
    bm_coef = max(bm_coefs)

    term_coef = pricing["term_coef"].get(str(term_months), 1.0)

    price = round(base * vt_coef * bm_coef * term_coef)
    return {"price": price}


def calc_casco_price(car_value: int, car_year: int, franchise: int = 0,
                      package: str = "Standard", **_) -> dict:
    pricing = knowledge_base()["products"]["casco"]["pricing"]
    max_age = knowledge_base()["products"]["casco"]["pricing"].get("max_car_age", {"Standard": 10, "Lite": 15})
    car_age = SNAPSHOT_DATE.year - int(car_year)
    if car_age > max_age.get(package, 10):
        raise ActionError("not_eligible", f"car too old for CASCO {package} (age {car_age})")

    if car_age <= 3:
        rate = pricing["rate_by_car_age"]["0-3"]
    elif car_age <= 7:
        rate = pricing["rate_by_car_age"]["4-7"]
    elif car_age <= 10:
        rate = pricing["rate_by_car_age"]["8-10"]
    else:
        raise ActionError("not_eligible", f"car too old for CASCO (age {car_age})")

    franchise_coef = pricing["franchise_coef"].get(str(franchise))
    if franchise_coef is None:
        raise ActionError("invalid_input", f"unknown franchise '{franchise}'")
    package_coef = pricing["package_coef"].get(package)
    if package_coef is None:
        raise ActionError("invalid_input", f"unknown package '{package}'")

    price = round(car_value * rate * franchise_coef * package_coef)
    return {"price": price}


def calc_travel_price(trip_country: str, trip_start: str, trip_end: str,
                       travelers_count: int = 1, traveler_max_age: int = 30, **_) -> dict:
    zones = knowledge_base()["products"]["travel"]["zones"]
    country = trip_country.strip().lower()
    country = _COUNTRY_ALIASES.get(country, country)
    zone = _TRAVEL_ZONE_BY_COUNTRY.get(country)
    if zone is None:
        raise ActionError("invalid_input", f"unknown travel zone for '{trip_country}'")
    zone_info = zones[zone]

    if traveler_max_age > 75:
        raise ActionError("not_eligible", "travelers over 75 are only insured via an operator")
    age_coef = 2.0 if traveler_max_age >= 65 else 1.0

    try:
        start = _parse_date(trip_start)
        end = _parse_date(trip_end)
    except ValueError:
        raise ActionError("invalid_input", "trip_start/trip_end must be YYYY-MM-DD")
    days = (end - start).days + 1
    if days <= 0:
        raise ActionError("invalid_input", "trip_end must be after trip_start")

    price = round(zone_info["rate_per_day_kzt"] * days * travelers_count * age_coef)
    return {"price": price, "zone": zone, "coverage": zone_info["coverage"]}


def calc_property_price(property_type: str = "apartment", sum_insured: int = 5000000, **_) -> dict:
    table = knowledge_base()["products"]["property"]["price_per_year_kzt"]
    key = str(sum_insured)
    if key not in table:
        raise ActionError("invalid_input", f"unsupported sum_insured '{sum_insured}'")
    price = table[key]
    if property_type == "house":
        price = round(price * knowledge_base()["products"]["property"]["house_coef"])
    return {"price": price}


def calc_accident_price(sum_insured: int = 1000000, **_) -> dict:
    table = knowledge_base()["products"]["accident"]["price_per_year_kzt"]
    key = str(sum_insured)
    if key not in table:
        raise ActionError("invalid_input", f"unsupported sum_insured '{sum_insured}'")
    return {"price": table[key]}


# ---------- irreversible / policy lifecycle ----------

def create_policy(product_type: str, phone: str, **_) -> dict:
    if product_type not in _PRODUCT_CODE:
        raise ActionError("invalid_input", f"unknown product_type '{product_type}'")
    if not re.match(r"^\+7\d{10}$", phone or ""):
        raise ActionError("invalid_input", "phone must match +7XXXXXXXXXX")
    existing = {p["policy_number"] for p in _data()["policies"]}
    while True:
        number = f"SQ-{_PRODUCT_CODE[product_type]}-{random.randint(100000, 999999)}"
        if number not in existing:
            break
    client = _find_client(phone=phone)
    _data()["policies"].append({
        "policy_number": number,
        "client_id": client["client_id"] if client else None,
        "product": product_type,
        "start_date": SNAPSHOT_DATE.isoformat(),
        "end_date": SNAPSHOT_DATE.replace(year=SNAPSHOT_DATE.year + 1).isoformat(),
        "premium": int(_.get("premium") or 0),
        "details": {k: v for k, v in _.items() if k in (
            "vehicle_plate", "drivers_iin", "trip_country", "trip_start", "trip_end",
            "travelers_count", "property_type", "sum_insured")},
    })
    return {"policy_number": number}


def renew_policy(policy_number: str, **_) -> dict:
    p = _find_policy(policy_number=policy_number)
    if not p:
        raise ActionError("not_found", "policy not found")
    if p.get("status") == "cancelled":
        raise ActionError("already_done", "policy was cancelled")
    end = _parse_date(p["end_date"])
    renewed_from = max(end, SNAPSHOT_DATE)
    p["end_date"] = renewed_from.replace(year=renewed_from.year + 1).isoformat()
    return {"policy_number": policy_number, "price": p.get("premium")}


def update_policy(policy_number: str, **_) -> dict:
    p = _find_policy(policy_number=policy_number)
    if not p:
        raise ActionError("not_found", "policy not found")
    if not _policy_active(p):
        raise ActionError("policy_inactive", "policy is expired")
    extra_premium = round(p.get("premium", 0) * 0.05)
    p.setdefault("details", {}).update({k: v for k, v in _.items() if k in (
        "new_driver_iin", "vehicle_plate", "new_vehicle_plate")})
    p["premium"] = p.get("premium", 0) + extra_premium
    return {"extra_premium": extra_premium}


def cancel_policy(policy_number: str, cancel_reason: str | None = None, preview: bool = False, **_) -> dict:
    p = _find_policy(policy_number=policy_number)
    if not p:
        raise ActionError("not_found", "policy not found")
    if p.get("status") == "cancelled":
        raise ActionError("already_done", "policy already cancelled")
    if not _policy_active(p):
        raise ActionError("policy_inactive", "policy already expired")

    paid_claim = any(
        c.get("policy_number") == policy_number and c.get("status") == "paid"
        for c in _data()["claims"]
    )
    if paid_claim:
        if not preview:
            p["status"] = "cancelled"
        return {"refund_amount": 0, "note": "no refund - a claim was already paid under this policy"}

    end = _parse_date(p["end_date"])
    unused_full_months = max(0, (end.year - SNAPSHOT_DATE.year) * 12 + (end.month - SNAPSHOT_DATE.month))
    refund = round(p.get("premium", 0) * unused_full_months / 12 * 0.9)
    if not preview:
        p["status"] = "cancelled"
        p["cancel_reason"] = cancel_reason
    return {"refund_amount": refund}


# ---------- claims ----------

def create_claim(product_type: str, incident_date: str, incident_description: str,
                  client_id: str | None = None, policy_number: str | None = None,
                  culprit_vehicle_plate: str | None = None, **_) -> dict:
    # Self-claims (SC13/SC14/SC16) carry policy_number as a required slot and
    # should be validated against that exact policy. Third-party claims
    # (e.g. SC12 - a victim claiming against the AT-FAULT driver's policy)
    # have no policy of the caller's own to check - the relevant policy was
    # already looked up earlier in the pipeline via get_policy - so we don't
    # block those on ownership, matching the declared action inputs.
    try:
        incident = _parse_date(incident_date)
    except ValueError as e:
        raise ActionError("invalid_input", "incident_date must be YYYY-MM-DD") from e
    policy = None
    if culprit_vehicle_plate:
        policy = _find_policy(vehicle_plate=culprit_vehicle_plate)
        if not policy:
            raise ActionError("not_found", "culprit policy not found")
    elif policy_number:
        policy = _find_policy(policy_number=policy_number)
        if not policy:
            raise ActionError("not_found", "policy not found")
    elif client_id:
        policy = next(
            (p for p in _data()["policies"]
             if p.get("client_id") == client_id and p.get("product") == product_type),
            None,
        )
        if not policy:
            raise ActionError("not_found", f"no {product_type} policy for this client")

    if policy and (
        policy.get("status") == "cancelled"
        or not _parse_date(policy["start_date"]) <= incident <= _parse_date(policy["end_date"])
    ):
        raise ActionError("policy_inactive", "policy is not active on the incident date")

    existing = {c["claim_number"] for c in _data()["claims"]}
    claim_number = _new_id("CL")
    while claim_number in existing:
        claim_number = _new_id("CL")
    _data()["claims"].append({
        "claim_number": claim_number, "client_id": client_id,
        "policy_number": policy["policy_number"] if policy else None,
        "claim_type": product_type, "incident_date": incident_date,
        "incident_description": incident_description, "status": "registered",
        "next_step": "Awaiting review",
    })
    return {"claim_number": claim_number}


def get_claim(claim_number: str | None = None, client_id: str | None = None, **_) -> dict:
    claims = _data()["claims"]
    claim = None
    if claim_number:
        claim = next((c for c in claims if c.get("claim_number") == claim_number), None)
    elif client_id:
        client_claims = [c for c in claims if c.get("client_id") == client_id]
        claim = sorted(client_claims, key=lambda c: c.get("incident_date", ""), reverse=True)[0] if client_claims else None
    if not claim:
        raise ActionError("not_found", "claim not found")
    return {
        "claim_number": claim["claim_number"],
        "status": claim["status"],
        "next_step": claim.get("next_step", ""),
    }


def create_dispute(claim_number: str, complaint_text: str, **_) -> dict:
    claim = next((c for c in _data()["claims"] if c.get("claim_number") == claim_number), None)
    if not claim:
        raise ActionError("not_found", "claim not found")
    return {"ticket_id": _new_id("DSP", 5)}


def book_inspection(claim_number: str, city: str, preferred_date: str, **_) -> dict:
    claim = next((c for c in _data()["claims"] if c.get("claim_number") == claim_number), None)
    if not claim:
        raise ActionError("not_found", "claim not found")
    points = knowledge_base()["inspection_points"]
    point = next((pt for pt in points if pt["city"] == city), None)
    if not point:
        point = next((pt for pt in points if pt["city"] == "other"), None)
        if not point:
            raise ActionError("no_availability", f"no inspection point for {city}")
    return {"slot_datetime": f"{preferred_date} 10:00", "address": point["address"]}


# ---------- DMS / health ----------

def _service_kind(name: str) -> str:
    text = name.strip().lower()
    if any(x in text for x in ("протез", "implant", "имплант", "prosthetic")):
        return "dental_prosthetics"
    if any(x in text for x in ("мрт", "mri", "кт", "ct scan", "томограф")):
        return "mri"
    if any(x in text for x in ("dent", "стомат", "тіс", "зуб")):
        return "dentist"
    if any(x in text for x in ("анализ", "талдау", "lab", "лаборат")):
        return "lab"
    if any(x in text for x in ("терап", "therap")):
        return "therapist"
    if any(x in text for x in ("гинек", "gynec")):
        return "gynecologist"
    if any(x in text for x in ("карди", "cardio")):
        return "cardiologist"
    if any(x in text for x in ("лор", "ent", "отолар")):
        return "ent"
    return text


def _coverage(package: str, service_name: str) -> tuple[bool, str]:
    kind = _service_kind(service_name)
    if kind == "dental_prosthetics":
        return False, "prosthetics and implants are excluded"
    if kind == "mri":
        return (package == "Comfort", "MRI/CT is covered by Comfort with a referral, up to 2 per year")
    if kind == "dentist":
        return (package == "Comfort", "dental treatment is covered by Comfort, excluding prosthetics")
    if kind == "lab":
        return True, "lab tests require a doctor's referral"
    if kind in ("therapist", "gynecologist", "cardiologist", "ent"):
        return True, "specialists require a therapist referral in Basic" if package == "Basic" and kind != "therapist" else "covered by your package"
    pkg = knowledge_base()["products"]["dms"]["packages"].get(package, {})
    excluded = [s.lower() for s in pkg.get("not_covered", [])]
    if any(kind in item or item in kind for item in excluded):
        return False, f"not covered by {package} package"
    covered = [s.lower() for s in pkg.get("covered", [])]
    matched = any(kind in item or item in kind for item in covered)
    return matched, "covered by your package" if matched else f"not covered by {package} package"

def check_coverage(policy_number: str, service_name: str, **_) -> dict:
    p = _find_policy(policy_number=policy_number)
    if not p or p.get("product") != "dms":
        raise ActionError("not_found", "DMS policy not found")
    if not _policy_active(p):
        raise ActionError("policy_inactive", "policy is expired")
    package = p.get("details", {}).get("package", "Basic")
    covered, note = _coverage(package, service_name)
    return {"covered": covered, "note": note}


def list_clinics(city: str, **_) -> dict:
    clinics = [c for c in knowledge_base()["clinics"] if c.get("city") == city]
    if not clinics:
        raise ActionError("not_found", f"no partner clinics in {city}")
    return {"clinics": clinics}


def book_appointment(policy_number: str, doctor_specialty: str, city: str,
                      preferred_date: str, preview: bool = False, **_) -> dict:
    p = _find_policy(policy_number=policy_number)
    if not p or p.get("product") != "dms":
        raise ActionError("not_found", "DMS policy not found")
    if not _policy_active(p):
        raise ActionError("policy_inactive", "policy is expired")

    package = p.get("details", {}).get("package", "Basic")
    covered, _note = _coverage(package, doctor_specialty)
    if not covered:
        raise ActionError("not_covered", f"{doctor_specialty} is not covered by {package} package")

    specialty = _service_kind(doctor_specialty)

    clinic = next(
        (c for c in knowledge_base()["clinics"]
         if c.get("city") == city and specialty in [s.lower() for s in c.get("specialties", [])]),
        None,
    )
    if not clinic:
        raise ActionError("no_availability", f"no clinic in {city} for {doctor_specialty}")
    result = {"clinic_name": clinic["name"], "slot_datetime": f"{preferred_date} 10:00"}
    if not preview:
        _data().setdefault("appointments", []).append({
            "policy_number": policy_number, "specialty": doctor_specialty,
            "city": city, **result,
        })
    return result


# ---------- servicing / info ----------

def resend_documents(policy_number: str | None = None, client_id: str | None = None, **_) -> dict:
    # policy_number is only optional in SC26 (policy_documents_resend) - the
    # pipeline identifies the client first (find_client/get_policies), so
    # when no specific policy is named we resend for the client's own
    # (first active) policy instead of requiring them to recite the number.
    p = None
    if policy_number:
        p = _find_policy(policy_number=policy_number)
        if not p:
            raise ActionError("not_found", "policy not found")
    elif client_id:
        policies = [pol for pol in _data()["policies"] if pol.get("client_id") == client_id]
        if not policies:
            raise ActionError("not_found", "no policies for this client")
        p = next((pol for pol in policies if _policy_active(pol)), policies[0])
    else:
        raise ActionError("invalid_input", "policy_number or client_id is required")

    if not _policy_active(p):
        raise ActionError("policy_inactive", "policy is expired")
    client = _find_client(client_id=p.get("client_id"))
    return {"sent_to": client.get("email") if client else "unknown"}


def check_payment(client_id: str, payment_date: str | None = None, **_) -> dict:
    payments = [pay for pay in _data()["payments"] if pay.get("client_id") == client_id]
    if payment_date:
        payments = [pay for pay in payments if pay.get("date") == payment_date]
    if not payments:
        raise ActionError("not_found", "no matching payment")
    payment = payments[0]
    return {"payment_status": payment["status"], "amount": payment["amount"]}


def update_contact(client_id: str, contact_field: str, new_value: str, **_) -> dict:
    if contact_field not in ("phone", "email", "address"):
        raise ActionError("invalid_input", f"unknown contact_field '{contact_field}'")
    client = _find_client(client_id=client_id)
    if not client:
        raise ActionError("not_found", "client not found")
    client[contact_field] = new_value
    return {"updated_field": contact_field, "new_value": new_value}


def request_document(policy_number: str, document_type: str, email: str, **_) -> dict:
    if document_type not in knowledge_base()["documents_available"]:
        raise ActionError("invalid_input", f"unknown document_type '{document_type}'")
    p = _find_policy(policy_number=policy_number)
    if not p:
        raise ActionError("not_found", "policy not found")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
        raise ActionError("invalid_input", "invalid email")
    return {"sent_to": email}


def get_offices(city: str, **_) -> dict:
    office = next((o for o in knowledge_base()["offices"] if o.get("city") == city), None)
    if not office:
        raise ActionError("not_found", f"no office in {city}")
    return {"address": office["address"], "hours": office["hours"]}


_KB_TOPICS = {
    "fraud": "fraud_policy",
    "мошенн": "fraud_policy",
    "app": "app_help",
    "прилож": "app_help",
    "cancel": "cancellation",
    "растор": "cancellation",
    "payment": "payments",
    "оплат": "payments",
    "bonus": "bonus_malus",
    "бонус": "bonus_malus",
    "malus": "bonus_malus",
    "complaint": "complaints",
    "жалоб": "complaints",
    "document": "documents_available",
    "докум": "documents_available",
    "claim": "claims",
    "заявлен": "claims",
    "убыт": "claims",
}


def kb_lookup(topic: str, **_) -> dict:
    kb = knowledge_base()
    topic_lower = (topic or "").lower()
    if "." in topic_lower or topic_lower in kb:
        value = kb
        for part in topic_lower.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ActionError("not_found", f"no knowledge base entry for '{topic}'")
            value = value[part]
        return {"answer": value}
    for product in ("ogpo", "casco", "travel", "property", "accident", "dms"):
        if product in topic_lower:
            return {"answer": kb["products"][product]}
    for keyword, section in _KB_TOPICS.items():
        if keyword in topic_lower:
            return {"answer": kb[section]}
    if any(term in topic_lower for term in ("франш", "franch")):
        return {"answer": kb["products"]["casco"]}
    if any(term in topic_lower for term in ("лимит", "limit", "покры", "өтем", "кірмей", "исключ")):
        return {"answer": kb["products"]}
    raise ActionError("not_found", f"no knowledge base entry for '{topic}'")


def send_sms(phone: str, **_) -> dict:
    if not re.match(r"^\+7\d{10}$", phone or ""):
        raise ActionError("invalid_input", "phone must match +7XXXXXXXXXX")
    return {}


def create_callback(phone: str, callback_time: str, **_) -> dict:
    if not re.match(r"^\+7\d{10}$", phone or ""):
        raise ActionError("invalid_input", "phone must match +7XXXXXXXXXX")
    return {}


def create_complaint(complaint_text: str, **_) -> dict:
    return {"ticket_id": _new_id("CMP", 5)}


def report_fraud(fraud_details: str, **_) -> dict:
    return {"ticket_id": _new_id("FRD", 5)}


def transfer_to_operator(queue: str = "operator_general", **_) -> dict:
    valid_queues = actions_catalog()["queues"]
    if queue not in valid_queues:
        queue = "operator_general"
    return {"queue": queue}


_IMPLEMENTED = {
    "find_client": find_client,
    "get_policies": get_policies,
    "get_policy": get_policy,
    "get_bm_class": get_bm_class,
    "calc_ogpo_price": calc_ogpo_price,
    "calc_casco_price": calc_casco_price,
    "calc_travel_price": calc_travel_price,
    "calc_property_price": calc_property_price,
    "calc_accident_price": calc_accident_price,
    "create_policy": create_policy,
    "renew_policy": renew_policy,
    "update_policy": update_policy,
    "cancel_policy": cancel_policy,
    "create_claim": create_claim,
    "get_claim": get_claim,
    "create_dispute": create_dispute,
    "book_inspection": book_inspection,
    "book_appointment": book_appointment,
    "check_coverage": check_coverage,
    "list_clinics": list_clinics,
    "resend_documents": resend_documents,
    "check_payment": check_payment,
    "update_contact": update_contact,
    "request_document": request_document,
    "get_offices": get_offices,
    "kb_lookup": kb_lookup,
    "send_sms": send_sms,
    "create_callback": create_callback,
    "create_complaint": create_complaint,
    "report_fraud": report_fraud,
    "transfer_to_operator": transfer_to_operator,
}


def action_spec(name: str) -> dict | None:
    return next((a for a in actions_catalog()["actions"] if a["name"] == name), None)


def call_action(name: str, **kwargs) -> dict:
    spec = action_spec(name)
    if spec is None:
        raise ActionError("not_found", f"unknown action {name}")

    fn = _IMPLEMENTED.get(name)
    if fn is None:
        raise NotImplementedError(
            f"action '{name}' not implemented yet - see actions.json spec: {spec}"
        )
    store = kwargs.pop("_store", None)
    token = _CURRENT_STORE.set(store)
    try:
        try:
            return fn(**kwargs)
        except TypeError as e:
            raise ActionError("invalid_input", f"missing or invalid arguments for {name}: {e}") from e
    finally:
        _CURRENT_STORE.reset(token)
