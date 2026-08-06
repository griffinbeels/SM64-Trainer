import * as React from "react";
import { Modal } from "sm64-trainer-ui";

// The backdrop is `position: fixed; inset: 0`, so on a card it escapes its
// root and the capture clips to a strip. A `transform` on an ancestor makes
// that ancestor the containing block for fixed positioning, so the backdrop
// fills THIS box instead of the viewport. The component is untouched — this
// is the card giving it somewhere to be.
const Stage = ({ children }: any) => (
  <div style={{ position: "relative", transform: "translateZ(0)", width: "100%",
                height: 460, background: "var(--bg)", overflow: "hidden",
                fontFamily: "Consolas, monospace" }}>
    {children}
  </div>
);

/** The dialog shell. The body is the caller's; the shell owns the title row,
 *  the close affordance and focus. Abandoning it must write nothing. */
export const Confirming = () => (
  <Stage>
    <Modal
      title="Add this time to your comparison"
      description="It will sit beside your own run, synced to the same star grab."
      icon="compare"
      onClose={() => {}}
      footer={<>
        <button type="button" className="ghost">Cancel</button>
        <button type="button" className="primary">Add to comparison</button>
      </>}
    >
      <p style={{ color: "var(--muted)", margin: 0 }}>
        Kanno — 0'21"63, Xcam, JP. Documented in the Ultimate Star Sheet with a
        video, so it can be played back frame by frame against yours.
      </p>
    </Modal>
  </Stage>
);
