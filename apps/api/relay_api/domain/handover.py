"""Store-manager PIN handover: how a delivery is confirmed at the door, even with no signal.

Every store manager has one fixed delivery PIN (`app_user.delivery_pin`). At the door the driver hands
over the phone, the manager types the PIN into a masked field and hands it back. The phone cannot ask
the server, so the driver's run carries a one-way check value per visit (`pin_check`) and the phone
compares the typed PIN against it offline. On a match it forgets the PIN and keeps a second,
differently labelled hash (`pin_proof`), which travels with the delivery; the server recomputes it
from the manager's PIN. The PIN itself never reaches the phone, the outbox or the log.

The two hashes differ on purpose: the phone already holds `pin_check`, so if the proof were the same
value, sending it back would confirm a delivery without anyone typing the PIN.

Trade-off: the phone gets only a one-way check value, but a determined person holding the run could
still brute-force a 4-digit check offline (10,000 guesses). That is acceptable for a handover
confirmation; production would use per-delivery codes or a signed QR code instead of a fixed PIN.
The client side is `apps/web/src/lib/handover.ts`; the strings below must match it exactly.
"""

from __future__ import annotations

import hashlib
import hmac

from sqlalchemy.orm import Session

from ..models import User


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pin_check(visit_id: int, pin: str) -> str:
    """What the phone compares a typed PIN against. Sent in the driver's run."""
    return _sha256(f"relay-handover:{visit_id}:{pin}")


def pin_proof(visit_id: int, pin: str) -> str:
    """What the phone sends with the delivery once the PIN matched. Never part of a read model."""
    return _sha256(f"relay-handover-proof:{visit_id}:{pin}")


def _managers(db: Session):
    return (
        db.query(User)
        .filter(User.role == "store", User.outlet_id.isnot(None), User.delivery_pin.isnot(None), User.delivery_pin != "")
        .order_by(User.id)
    )


def store_managers(db: Session) -> dict[str, User]:
    """Outlet id -> its store manager with a delivery PIN, in one query (for read models)."""
    out: dict[str, User] = {}
    for u in _managers(db):
        out.setdefault(u.outlet_id, u)
    return out


def store_manager_for(db: Session, outlet_id: str) -> User | None:
    """The store manager of this outlet who has a delivery PIN, if any."""
    return _managers(db).filter(User.outlet_id == outlet_id).first()


def verify(visit_id: int, pin: str, proof: str | None) -> bool:
    """True when `proof` is what the phone computes for this visit after this PIN was typed."""
    if not isinstance(proof, str) or not proof:
        return False
    return hmac.compare_digest(pin_proof(visit_id, pin).encode(), proof.encode())
