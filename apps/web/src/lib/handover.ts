/**
 * Store-manager PIN handover, checked on the phone so it works with no signal.
 *
 * The run carries `pin_check` for each visit. The phone compares the typed PIN against it, then keeps only
 * `pinProof` (a second, differently labelled hash) and sends that with the delivery; the server recomputes it
 * from the manager's PIN. The PIN itself is never stored: not in state after the tap, not in IndexedDB, logs
 * or toasts. The strings must match apps/api/relay_api/domain/handover.py exactly.
 *
 * js-sha256 rather than crypto.subtle, which browsers withhold on plain http (phones testing over the LAN).
 */
import { sha256 } from "js-sha256";

/** What the run's `pin_check` is compared with. */
export function pinCheck(visitId: number, pin: string): string {
  return sha256(`relay-handover:${visitId}:${pin}`);
}

/** What the phone sends as `pin_proof` once the PIN matched. */
export function pinProof(visitId: number, pin: string): string {
  return sha256(`relay-handover-proof:${visitId}:${pin}`);
}
