import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { onboardingApi, type SetupStatus, type SetupStepKey } from "../../api/onboarding";
import type { HelpSlug } from "../../lib/help";
import { HelpLink } from "../help/HelpLink";

type StepLink =
  | { to: "/materials" | "/products" | "/stock-takes" | "/locations" }
  | { to: "/settings"; page: "lists" | "shipping-packaging" | "stores-sync" | "backup-restore" };

interface StepCopy {
  title: string;
  description: string;
  action: string;
  link: StepLink;
  help: { doc: HelpSlug; anchor?: string };
}

/**
 * Wording and destinations for each step. The backend (services/onboarding.py) decides which steps
 * exist and whether each is done; everything a person reads lives here. Every help anchor is checked
 * against the real headings in SetupChecklist.test.tsx.
 */
export const STEP_COPY: Record<SetupStepKey, StepCopy> = {
  business_type: {
    title: "Tell StockSmith what you make",
    description: "Sets up material categories and units to suit your business.",
    action: "Review categories",
    link: { to: "/settings", page: "lists" },
    help: { doc: "material-categories" },
  },
  materials: {
    title: "Add your materials",
    description: "Everything you buy and use up: raw materials, components and packaging.",
    action: "Go to Materials",
    link: { to: "/materials" },
    help: { doc: "getting-started", anchor: "2-add-your-materials" },
  },
  opening_stock: {
    title: "Count what you have",
    description: "Record how much of each material is on your shelves right now.",
    action: "Start a stock take",
    link: { to: "/stock-takes" },
    help: { doc: "getting-started", anchor: "3-count-what-you-have" },
  },
  locations: {
    title: "Set up your storage locations",
    description: "Record where each material and product is kept, so you can find it and print shelf labels.",
    action: "Set up locations",
    link: { to: "/locations" },
    help: { doc: "locations", anchor: "what-locations-are" },
  },
  kitting: {
    title: "Set up your usual packaging",
    description: "Before adding products: new products start with this packaging.",
    action: "Open packaging settings",
    link: { to: "/settings", page: "shipping-packaging" },
    help: { doc: "concepts", anchor: "kitting" },
  },
  products: {
    title: "Add a product and its recipe",
    description: "A product's BOM says what it's made from, so StockSmith can cost it and know how many you can make.",
    action: "Go to Products",
    link: { to: "/products" },
    help: { doc: "getting-started", anchor: "5-add-your-products-and-their-recipes" },
  },
  orders: {
    title: "Connect a store, or add an order",
    description: "Square takes about 10 minutes. Etsy and eBay need a developer account.",
    action: "Open Stores & sync",
    link: { to: "/settings", page: "stores-sync" },
    help: { doc: "connecting-square" },
  },
  offsite_backup: {
    title: "Keep a backup somewhere else",
    description: "Copy each day's backup to OneDrive or Dropbox, in case this computer fails.",
    action: "Open backup settings",
    link: { to: "/settings", page: "backup-restore" },
    help: { doc: "backup-and-restore", anchor: "keep-a-copy-somewhere-else" },
  },
};

function StepAction({ link, children }: { link: StepLink; children: string }) {
  const className = "text-xs font-medium text-slate-900 underline decoration-slate-400 underline-offset-2 hover:decoration-slate-900";
  return link.to === "/settings" ? (
    <Link to="/settings" search={{ page: link.page }} className={className}>
      {children}
    </Link>
  ) : (
    <Link to={link.to} className={className}>
      {children}
    </Link>
  );
}

/**
 * The Dashboard's setup checklist — docs/plan-onboarding.md §5.3. Shows until every step is done or
 * it's hidden; Help's home page brings it back. Renders nothing on a failed or unexpected response,
 * so it can never get in the way of the Dashboard itself.
 */
export function SetupChecklist() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["onboarding-status"], queryFn: onboardingApi.status });
  const hide = useMutation({
    mutationFn: () => onboardingApi.setDismissed(true),
    onSuccess: (next) => queryClient.setQueryData<SetupStatus>(["onboarding-status"], next),
  });

  if (!data || !Array.isArray(data.steps) || data.dismissed || data.complete) return null;

  const doneCount = data.steps.filter((s) => s.done).length;
  // The first step not yet done is the one to do next, and the only one given a call to action,
  // so the list reads as a path rather than seven equal demands.
  const nextKey = data.steps.find((s) => !s.done)?.key;

  return (
    <section aria-labelledby="setup-checklist-title" className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="setup-checklist-title" className="text-sm font-semibold text-slate-900">
            Set up StockSmith
          </h2>
          <p className="text-xs text-slate-500">
            {doneCount} of {data.steps.length} done. Steps tick themselves off as you go.{" "}
            <HelpLink doc="getting-started">Getting started guide</HelpLink>
          </p>
        </div>
        <button
          type="button"
          onClick={() => hide.mutate()}
          disabled={hide.isPending}
          className="text-xs text-slate-500 underline underline-offset-2 hover:text-slate-900"
          title="You can bring it back from Help"
        >
          Hide checklist
        </button>
      </div>
      <div
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100"
        role="progressbar"
        aria-label="Setup progress"
        aria-valuemin={0}
        aria-valuemax={data.steps.length}
        aria-valuenow={doneCount}
      >
        <div className="h-full bg-emerald-500" style={{ width: `${(doneCount / data.steps.length) * 100}%` }} />
      </div>
      <ol className="mt-3 flex flex-col divide-y divide-slate-100">
        {data.steps.map((step) => {
          const copy = STEP_COPY[step.key];
          if (!copy) return null;
          const isNext = step.key === nextKey;
          return (
            <li key={step.key} className="flex items-start gap-3 py-2">
              <span
                aria-hidden="true"
                className={`mt-0.5 flex h-4 w-4 flex-none items-center justify-center rounded-full text-[10px] font-bold ${
                  step.done ? "bg-emerald-500 text-white" : isNext ? "border-2 border-slate-900" : "border border-slate-300"
                }`}
              >
                {step.done ? "✓" : ""}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className={`text-sm ${step.done ? "text-slate-400 line-through" : "font-medium text-slate-900"}`}>
                  {copy.title}
                  <span className="sr-only">{step.done ? " (done)" : " (to do)"}</span>
                  {step.optional && !step.done && (
                    <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10.5px] font-medium text-slate-500">
                      Optional
                    </span>
                  )}
                </span>
                {!step.done && <span className="text-xs text-slate-500">{copy.description}</span>}
                {isNext && (
                  <span className="mt-1 flex items-center gap-3 text-xs">
                    <StepAction link={copy.link}>{copy.action}</StepAction>
                    <HelpLink doc={copy.help.doc} anchor={copy.help.anchor}>
                      How to do this
                    </HelpLink>
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** For Help's home page: brings a hidden checklist back. Renders nothing unless it's hidden. */
export function ShowSetupChecklist() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["onboarding-status"], queryFn: onboardingApi.status });
  const show = useMutation({
    mutationFn: () => onboardingApi.setDismissed(false),
    onSuccess: (next) => queryClient.setQueryData<SetupStatus>(["onboarding-status"], next),
  });
  if (!data || data.dismissed !== true) return null;
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
      <span className="text-slate-600">
        {show.isSuccess ? "The setup checklist is back on the Dashboard." : "The setup checklist is hidden."}
      </span>
      {!show.isSuccess && (
        <button
          type="button"
          onClick={() => show.mutate()}
          disabled={show.isPending}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-900 hover:bg-slate-50"
        >
          Show it on the Dashboard
        </button>
      )}
    </div>
  );
}
