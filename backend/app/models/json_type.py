"""A JSON column that survives ``Decimal`` and ``date``.

Frozen render contexts and audit before/after snapshots are full of money and
dates. The stdlib encoder refuses both, so a plain ``JSON`` column would blow
up exactly when we most want the audit row written.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            # str, not float: round-tripping through float is how paise vanish.
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, set):
            return sorted(o)
        return super().default(o)


def dumps(value: Any) -> str:
    return json.dumps(value, cls=_Encoder, ensure_ascii=False)


class JSONText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return dumps(value)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        return json.loads(value)
