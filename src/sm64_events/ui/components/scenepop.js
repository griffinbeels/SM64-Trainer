// src/sm64_events/ui/components/scenepop.js -- the Ghost Lab's small menus that open over the page (lab only):
// the sound and the bar's "More". One rule for both: open on their button, close on Esc or a press anywhere
// else, and take no room on the page while open or shut.
import { useEffect, useRef, useState } from "preact/hooks";

// {open, setOpen, place}: `place` goes on the element that holds the button and the menu.
export function usePop() {
  const [open, setOpen] = useState(false);
  const place = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const away = (event) => { if (!place.current?.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return { open, setOpen, place };
}
