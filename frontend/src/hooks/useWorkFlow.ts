import { useQuery } from "@tanstack/react-query";
import { workApi } from "../api/work";

/** Whether the work-flow feature is switched on. Off until a seller opts in, and while the answer is
 * loading, so nothing flashes in for people who never use it. */
export function useWorkFlowEnabled(): boolean {
  const { data } = useQuery({ queryKey: ["work", "settings"], queryFn: workApi.settings, staleTime: 30_000 });
  return data?.work_flow_enabled ?? false;
}
