import type React from "react";
import { UnsavedChangesDialog } from "stocksmith-ui";

// Overlays are position:fixed; the preview card wraps each story in a transformed box that
// becomes their containing block, so the story needs a height of its own to centre inside.
const Frame = ({ height, children }: { height: number; children: React.ReactNode }) => (
  <div style={{ position: "relative", height }}>{children}</div>
);

/** Names the dirty editors so the warning is useful rather than alarming. */
export const NamedEditors = () => (
  <Frame height={320}>
    <UnsavedChangesDialog open labels={["Bill of Materials", "Kitting BOM"]} onDiscard={() => {}} onCancel={() => {}} />
  </Frame>
);

export const Unnamed = () => (
  <Frame height={320}>
    <UnsavedChangesDialog open labels={[]} onDiscard={() => {}} onCancel={() => {}} />
  </Frame>
);
