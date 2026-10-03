/** IndexedDB (Dexie): the outbox of records made on this device, and cached snapshots. */
import Dexie, { type Table } from "dexie";

export type OutboxStatus = "pending" | "synced" | "rejected";

export interface OutboxItem {
  id: string; // client_event_id, also the idempotency key on the server
  type: string;
  payload: Record<string, unknown>;
  at: string; // virtual time on the device when it happened
  offline: boolean;
  status: OutboxStatus;
  createdAt: number;
  syncedAt?: number;
  syncedVirtual?: string; // workspace (virtual) time when the server accepted it
  attempts: number;
  message?: string;
  label: string; // human description for the Sync screen
}

export interface CacheRow {
  key: string;
  value: unknown;
  at: number;
}

class RelayDB extends Dexie {
  outbox!: Table<OutboxItem, string>;
  cache!: Table<CacheRow, string>;
  constructor() {
    super("relay");
    this.version(1).stores({ outbox: "id, createdAt, status", cache: "key" });
  }
}

export const db = new RelayDB();

export async function cachePut(key: string, value: unknown) {
  try {
    await db.cache.put({ key, value, at: Date.now() });
  } catch {
    /* private mode: no persistence */
  }
}

export async function cacheGet<T>(key: string): Promise<T | undefined> {
  try {
    return (await db.cache.get(key))?.value as T | undefined;
  } catch {
    return undefined;
  }
}
