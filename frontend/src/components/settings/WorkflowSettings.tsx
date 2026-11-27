import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { workApi } from "../../api/work";
import { HelpLink } from "../help/HelpLink";
import { ErrorBanner } from "../common/ErrorBanner";
import { SettingsCard } from "./SettingsCard";
import { CategoryRoutes } from "./workflow/CategoryRoutes";
import { RouteBuilder } from "./workflow/RouteBuilder";
import { StageLibrary } from "./workflow/StageLibrary";

/**
 * Settings → Work flow: switch the feature on, then define the steps ("stages") your products go
 * through and the order they happen in ("routes"). Nothing here changes how orders, builds or stock
 * behave — the Work page only reads them — so switching it on is always safe and switching it off
 * simply hides the page.
 */
export function WorkflowSettings() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ["work", "settings"], queryFn: workApi.settings });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => workApi.setEnabled(enabled),
    onSuccess: (next) => {
      queryClient.setQueryData(["work", "settings"], next);
      queryClient.invalidateQueries({ queryKey: ["work"] });
    },
  });
  const enabled = settings?.work_flow_enabled ?? false;

  return (
    <div className="flex flex-col gap-4">
      <SettingsCard
        title="Work flow"
        help={
          <>
            A to-do list built from your orders: what to prepare, make, finish and pack next, grouped by where you
            do it. <HelpLink doc="workflow">How it works</HelpLink>
          </>
        }
        action={
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              disabled={!settings || toggle.isPending}
              onChange={(e) => toggle.mutate(e.target.checked)}
            />
            Use work flow
          </label>
        }
      >
        <p className="text-sm text-slate-600">
          {enabled ? (
            <>
              Work flow is on. Open the{" "}
              <Link to="/work" className="underline decoration-slate-400 underline-offset-2">
                Work page
              </Link>{" "}
              to see what to do next. It only reads your orders, builds and stock — switching it off just hides the page.
            </>
          ) : (
            "Off. Turn it on to add a Work page to the sidebar. Until you define your own stages below, every product follows the standard route: make it, then ship it."
          )}
        </p>
        <ErrorBanner error={toggle.error} />
      </SettingsCard>
      <StageLibrary />
      <RouteBuilder />
      <CategoryRoutes />
    </div>
  );
}
