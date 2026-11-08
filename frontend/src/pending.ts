// Avvisa la barra di navigazione che la coda "Da inviare" è cambiata.
const EVENT = "pending-changed";

export function notifyPending() {
  window.dispatchEvent(new Event(EVENT));
}

export function onPendingChange(fn: () => void): () => void {
  window.addEventListener(EVENT, fn);
  return () => window.removeEventListener(EVENT, fn);
}
