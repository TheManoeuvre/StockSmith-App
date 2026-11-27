import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { workApi } from "../../api/work";

/**
 * The dashboard's door into the Work page: how much is late, due today, and ready to pack. Renders
 * nothing unless work flow is switched on and there is something to do, so it never adds an empty
 * box for people who don't use the feature.
 */
export function WorkTodayTile() {
  const { data } = useQuery({ queryKey: ["work", "summary"], queryFn: workApi.summary, refetchInterval: 60_000 });
  if (!data || !data.enabled || data.open_cards === 0) return null;
  return (
    <Link to="/work" className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm transition hover:shadow-md" aria-label="Work to do">
      <span className="font-medium">Work to do</span>
      <span className={data.late > 0 ? "font-medium text-red-700" : "text-slate-600"}>{data.late} late</span>
      <span className="text-slate-600">{data.due_today} due today</span>
      <span className="text-slate-600">{data.ready_to_ship} ready to ship</span>
      <span className="flex-1" />
      <span className="text-slate-500 underline decoration-slate-300 underline-offset-2">Open Work</span>
    </Link>
  );
}
