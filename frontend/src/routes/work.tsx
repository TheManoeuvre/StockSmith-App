import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { workApi, type WorkEvent, type WorkList } from "../api/work";
import { EmptyState } from "../components/common/EmptyState";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { HelpLink } from "../components/help/HelpLink";
import { OrderCard, TaskCard } from "../components/work/WorkCards";
import { PAGE_HELP } from "../lib/help";

const NO_STATION = "Other";

export const Route = createFileRoute("/work")({
  // The station lives in the URL so a station is linkable and the browser's Back button steps through
  // the ones you looked at.
  validateSearch: (search: Record<string, unknown>): { station?: string } =>
    typeof search.station === "string" && search.station ? { station: search.station } : {},
  component: WorkPage,
});

function WorkPage() {
  const queryClient = useQueryClient();
  const { station } = Route.useSearch();
  const navigate = Route.useNavigate();
  const { data, error } = useQuery({ queryKey: ["work", "list"], queryFn: workApi.list, refetchInterval: 60_000 });
  const [lastEvents, setLastEvents] = useState<WorkEvent[]>([]);
  const undo = useMutation({
    mutationFn: async () => {
      // Newest first, so a multi-variant tick is reversed as a whole.
      for (const event of lastEvents) await workApi.undo(event.id);
    },
    onSuccess: () => {
      setLastEvents([]);
      queryClient.invalidateQueries({ queryKey: ["work"] });
    },
  });

  const cards = useMemo(() => (data?.cards ?? []).filter((c) => !station || (c.station ?? NO_STATION) === station), [data, station]);
  // Ship and Waiting belong to the Ship step's station, so they show under All and under that station.
  const showPackSections = !station || (data?.ship_station ?? NO_STATION) === station;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-semibold tracking-[-.35px]">Work</h1>
        <HelpLink doc={PAGE_HELP.workflow.doc} anchor={PAGE_HELP.workflow.anchor} />
      </div>
      <ErrorBanner error={error} />

      {data && !data.enabled && (
        <EmptyState
          title="Work flow is off"
          actions={
            <Link to="/settings" search={{ page: "workflow" }} className="underline decoration-slate-400 underline-offset-2">
              Turn it on in Settings
            </Link>
          }
        >
          Work flow turns your orders into a to-do list — what to prepare, make, finish and pack next. It is optional and only reads your
          orders and stock.
        </EmptyState>
      )}

      {data?.enabled && <WorkBody data={data} station={station} cards={cards} showPackSections={showPackSections} onStation={(s) => navigate({ search: s ? { station: s } : {} })} onRecorded={setLastEvents} />}

      {lastEvents.length > 0 && (
        <div className="flex items-center gap-3 rounded-md bg-slate-100 px-3 py-2 text-sm">
          <span>
            Recorded {lastEvents.reduce((n, e) => n + e.qty, 0)}.
          </span>
          <button type="button" disabled={undo.isPending} className="underline decoration-slate-400 underline-offset-2" onClick={() => undo.mutate()}>
            Undo
          </button>
          <ErrorBanner error={undo.error} />
        </div>
      )}
    </div>
  );
}

function WorkBody({
  data,
  station,
  cards,
  showPackSections,
  onStation,
  onRecorded,
}: {
  data: WorkList;
  station: string | undefined;
  cards: WorkList["cards"];
  showPackSections: boolean;
  onStation: (station: string | undefined) => void;
  onRecorded: (events: WorkEvent[]) => void;
}) {
  const nothing = data.cards.length === 0 && data.ship_cards.length === 0 && data.waiting_orders.length === 0;
  if (nothing) {
    return (
      <EmptyState title="Nothing to do">
        No order needs preparing, making, finishing or packing right now. When an order comes in that isn&apos;t covered by stock, the steps for it appear here.
      </EmptyState>
    );
  }
  const stations = data.stations;
  return (
    <>
      {stations.length > 1 && (
        <div role="group" aria-label="Station" className="flex flex-wrap gap-1.5">
          <StationChip label={`All (${data.cards.length + data.ship_cards.length})`} active={!station} onClick={() => onStation(undefined)} />
          {stations.map((s) => (
            <StationChip key={s.name ?? NO_STATION} label={`${s.name ?? NO_STATION} (${s.count})`} active={station === (s.name ?? NO_STATION)} onClick={() => onStation(s.name ?? NO_STATION)} />
          ))}
        </div>
      )}

      {cards.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-slate-700">To do</h2>
          <ul className="flex flex-col gap-2">
            {cards.map((card, i) => (
              <TaskCard key={card.key} card={card} next={i === 0} onRecorded={onRecorded} />
            ))}
          </ul>
        </section>
      )}

      {showPackSections && data.ship_cards.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-slate-700">Ready to ship</h2>
          <ul className="flex flex-col gap-2">
            {data.ship_cards.map((card) => (
              <OrderCard key={card.order_id} card={card} />
            ))}
          </ul>
        </section>
      )}

      {showPackSections && data.waiting_orders.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-slate-700">Waiting</h2>
          <ul className="flex flex-col gap-2">
            {data.waiting_orders.map((card) => (
              <OrderCard key={card.order_id} card={card} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function StationChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm ${active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
    >
      {label}
    </button>
  );
}
