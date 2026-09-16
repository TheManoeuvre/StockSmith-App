import type React from "react";
import { Modal } from "stocksmith-ui";

// Overlays are position:fixed; the preview card wraps each story in a transformed box that
// becomes their containing block, so the story needs a height of its own to centre inside.
const Frame = ({ height, children }: { height: number; children: React.ReactNode }) => (
  <div style={{ position: "relative", height }}>{children}</div>
);

/** Title, subtitle line, scrolling body and a right-aligned footer. */
export const WithFooter = () => (
  <Frame height={440}>
    <Modal
      title="Pick a listing"
      subtitle={<p className="text-sm text-slate-500">3 of 5 listings look eligible</p>}
      onClose={() => {}}
      footer={
        <>
          <button className="rounded-md border border-slate-300 px-4 py-2 text-sm">Cancel</button>
          <button className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white">Link listing</button>
        </>
      }
    >
      <ul className="flex flex-col gap-2 text-sm">
        <li className="rounded border border-slate-200 p-2">Walnut coaster set - 4 pack</li>
        <li className="rounded border border-slate-200 p-2">Oak coaster set - 4 pack</li>
        <li className="rounded border border-slate-200 p-2 text-slate-400">Coaster gift box (no SKU)</li>
      </ul>
    </Modal>
  </Frame>
);

export const Plain = () => (
  <Frame height={440}>
    <Modal title="Export orders" onClose={() => {}}>
      <p className="text-sm text-slate-600">A CSV of the 12 orders currently shown will be saved to your Downloads folder.</p>
    </Modal>
  </Frame>
);
