// A side panel dialog (M7 review fixes D1–D4).
// - Focus moves in once when it opens and back when it closes; parent re-renders don't move it.
// - Closing (Escape, ×, backdrop) with unsaved changes asks first.
// - Only a click that starts and ends on the backdrop closes it, so a text selection dragged
//   outside the panel can't throw a draft away.
// - Tab and Shift+Tab stay inside the dialog.
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef } from "react";
import { CloseIcon } from "../icons";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Drawer({
  title,
  onClose,
  dirty = false,
  children,
}: {
  title: string;
  onClose: () => void;
  dirty?: boolean;
  children: ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const downOnBackdrop = useRef(false);
  // Latest props for the once-only effect below.
  const latest = useRef({ onClose, dirty });
  latest.current = { onClose, dirty };

  const requestClose = () => {
    const { onClose: close, dirty: unsaved } = latest.current;
    if (!unsaved || window.confirm("Discard your unsaved changes?")) close();
  };

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") requestClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const trapTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !panel.current) return;
    const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnBackdrop.current) requestClose();
        downOnBackdrop.current = false;
      }}
    >
      <div
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={panel}
        onKeyDown={trapTab}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={requestClose}>
            <CloseIcon />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  );
}
