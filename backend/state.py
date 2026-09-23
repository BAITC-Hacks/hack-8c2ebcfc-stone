from dataclasses import dataclass, field


@dataclass
class DialogState:
    language: str = "ru"
    client_id: str | None = None
    active_scenario: str | None = None
    scenario_stack: list[str] = field(default_factory=list)
    slots: dict[str, str] = field(default_factory=dict)
    turn: int = 0
    history: list[dict] = field(default_factory=list)

    def push_scenario(self, scenario_id: str) -> None:
        if self.active_scenario:
            self.scenario_stack.append(self.active_scenario)
        self.active_scenario = scenario_id

    def pop_scenario(self) -> str | None:
        self.active_scenario = self.scenario_stack.pop() if self.scenario_stack else None
        return self.active_scenario

    def set_slot(self, name: str, value: str) -> None:
        self.slots[name] = value

    def record_turn(self, speaker: str, text: str, **extra) -> None:
        self.turn += 1
        self.history.append({"turn": self.turn, "speaker": speaker, "text": text, **extra})
