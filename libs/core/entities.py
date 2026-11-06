"""Legal entities: time zone, business hours, rule-set version. Config, not code, in prod."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from libs.rules.config import DEFAULT_RULE_SET, RuleSetConfig


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str
    tz: timezone
    open_h: float = 8.5  # 08:30
    close_h: float = 17.5  # 17:30
    month_end_last: int = 3  # last N business days
    month_end_first: int = 2  # first N business days
    rule_set: RuleSetConfig = DEFAULT_RULE_SET

    def today(self, now: datetime) -> date:
        return now.astimezone(self.tz).date()


SLT = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")

ENTITIES: dict[str, Entity] = {
    "E01": Entity("E01", "Lanka Holdings PLC", SLT),
    "E02": Entity("E02", "Lanka Manufacturing (Pvt) Ltd", SLT),
}


def entity(entity_id: str) -> Entity:
    return ENTITIES[entity_id]
