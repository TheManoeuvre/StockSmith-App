import type React from "react";
import { ConfirmDialog } from "stocksmith-ui";

// Overlays are position:fixed; the preview card wraps each story in a transformed box that
// becomes their containing block, so the story needs a height of its own to centre inside.
const Frame = ({ height, children }: { height: number; children: React.ReactNode }) => (
  <div style={{ position: "relative", height }}>{children}</div>
);

/** The default: destructive action in red, safe choice first and focused. */
export const Danger = () => (
  <Frame height={380}>
    <ConfirmDialog
      open
      title="Delete order #1042?"
      body={<p>The order and its 3 lines will be removed. Allocated stock is released back to on hand.</p>}
      confirmLabel="Delete order"
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  </Frame>
);

export const Neutral = () => (
  <Frame height={380}>
    <ConfirmDialog
      open
      tone="default"
      title="Deactivate product?"
      body={<p>Walnut coaster set is hidden from new orders but its history stays.</p>}
      confirmLabel="Deactivate"
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  </Frame>
);

/** Typed confirmation for an unrecoverable action. */
export const TypedGate = () => (
  <Frame height={380}>
    <ConfirmDialog
      open
      title="Restore backup from 12 Sep?"
      body={<p>Everything entered since then will be lost. This cannot be undone.</p>}
      confirmLabel="Restore"
      requireTypedText="RESTORE"
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  </Frame>
);

export const Busy = () => (
  <Frame height={380}>
    <ConfirmDialog
      open
      busy
      title="Delete order #1042?"
      body={<p>The order and its 3 lines will be removed.</p>}
      confirmLabel="Delete order"
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  </Frame>
);
