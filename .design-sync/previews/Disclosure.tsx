import { useState, type ReactNode } from "react";
import { Disclosure } from "stocksmith-ui";

/** The card the rows sit in — Disclosure draws only its own row and the divider between rows. */
const Card = ({ children }: { children: ReactNode }) => (
  <div className="w-[560px] rounded-[9px] border border-slate-200 bg-white">{children}</div>
);

/** Several rows stacked inside a card, as the store page's tools section does; one open. */
export const Stacked = () => (
  <Card>
    <Disclosure title="Compatibility report" summary="every product fits Etsy's limits">
      <p className="text-[12.5px] text-slate-600">All 128 products pass Etsy's title, tag and image limits.</p>
    </Disclosure>
    <Disclosure
      title="Backfill from Etsy"
      summary="copy descriptions, prices and hero images from linked listings"
      defaultOpen
    >
      <div className="flex flex-col gap-2 text-[12.5px] text-slate-600">
        <p>Fills in empty product fields from the linked Etsy listing. Existing values are left alone.</p>
        <button type="button" className="w-fit rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white">
          Run backfill
        </button>
      </div>
    </Disclosure>
    <Disclosure title="Unlinked listings" summary="live listings whose SKU StockSmith doesn't know">
      <p className="text-[12.5px] text-slate-600">No unlinked listings.</p>
    </Disclosure>
  </Card>
);

/** Summary tones: a problem reads before the row is opened. */
export const Tones = () => (
  <Card>
    <Disclosure title="Credentials" summary={<span className="font-mono">client id ab12…f9</span>}>
      <p className="text-[12.5px] text-slate-600">Credentials form</p>
    </Disclosure>
    <Disclosure
      title="Fee reporting signature"
      tone="warning"
      summary="not set up — eBay won't report fees on your orders until it is"
    >
      <p className="text-[12.5px] text-slate-600">Signing key form</p>
    </Disclosure>
    <Disclosure title="Connection" tone="danger" summary="token expired 3 days ago">
      <p className="text-[12.5px] text-slate-600">Reconnect</p>
    </Disclosure>
  </Card>
);

/** Controlled: the caller owns `open`, so it can veto a collapse while a form is dirty. */
export const Controlled = () => {
  const [open, setOpen] = useState(true);
  return (
    <Card>
      <Disclosure title="Credentials" summary="unsaved changes" tone="warning" open={open} onOpenChange={setOpen}>
        <p className="text-[12.5px] text-slate-600">Editing — collapsing would discard the draft, so the caller decides.</p>
      </Disclosure>
    </Card>
  );
};
