from backend.data_loader import actions_catalog, mock_backend, knowledge_base


class ActionError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)


def find_client(phone: str | None = None, iin: str | None = None) -> dict:
    clients = mock_backend()["clients"]
    for c in clients:
        if phone and c.get("phone") == phone:
            return {"client_id": c["client_id"], "full_name": c["full_name"]}
        if iin and c.get("iin") == iin:
            return {"client_id": c["client_id"], "full_name": c["full_name"]}
    raise ActionError("not_found", "no client matches phone/iin")


def get_policies(client_id: str) -> dict:
    policies = [p for p in mock_backend()["policies"] if p.get("client_id") == client_id]
    if not policies:
        raise ActionError("not_found", "no policies for this client")
    return {"policies": policies}


def get_policy(policy_number: str | None = None, vehicle_plate: str | None = None) -> dict:
    for p in mock_backend()["policies"]:
        if policy_number and p.get("policy_number") == policy_number:
            return p
        if vehicle_plate and p.get("vehicle_plate") == vehicle_plate:
            return p
    raise ActionError("not_found", "no policy matches")


def get_bm_class(iin: str) -> dict:
    for p in mock_backend()["policies"]:
        if p.get("iin") == iin and "bm_class" in p:
            return {"bm_class": p["bm_class"]}
    return {"bm_class": 3}


_IMPLEMENTED = {
    "find_client": find_client,
    "get_policies": get_policies,
    "get_policy": get_policy,
    "get_bm_class": get_bm_class,
}


def call_action(name: str, **kwargs) -> dict:
    spec = next((a for a in actions_catalog()["actions"] if a["name"] == name), None)
    if spec is None:
        raise ActionError("not_found", f"unknown action {name}")

    fn = _IMPLEMENTED.get(name)
    if fn is None:
        raise NotImplementedError(
            f"action '{name}' not implemented yet — see actions.json spec: {spec}"
        )
    return fn(**kwargs)
