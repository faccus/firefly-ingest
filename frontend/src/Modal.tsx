import { MouseEvent, ReactNode, useEffect, useRef } from "react";

/**
 * Finestra modale. Si chiude con Esc o con un click sullo sfondo, ma solo se
 * il mouse è stato sia premuto sia rilasciato sullo sfondo: trascinare una
 * selezione di testo fuori dal riquadro non la chiude.
 */
export default function Modal({ onClose, wide, children }: { onClose: () => void; wide?: boolean; children: ReactNode }) {
  const pressedOnBackdrop = useRef(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const isBackdrop = (e: MouseEvent) => e.target === e.currentTarget;
  return (
    <div
      className="modal"
      onMouseDown={(e) => (pressedOnBackdrop.current = isBackdrop(e))}
      onClick={(e) => {
        if (pressedOnBackdrop.current && isBackdrop(e)) onClose();
        pressedOnBackdrop.current = false;
      }}
    >
      <div className={`card ${wide ? "wide-modal" : ""}`} role="dialog" aria-modal="true">
        {children}
      </div>
    </div>
  );
}
