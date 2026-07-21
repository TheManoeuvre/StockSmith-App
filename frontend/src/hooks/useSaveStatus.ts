import { useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "saving" | "saved" | "error";

export function useSaveStatus(mutationStatus: "idle" | "pending" | "success" | "error"): SaveStatus {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    clearTimeout(timeoutRef.current);
    if (mutationStatus === "pending") {
      setStatus("saving");
    } else if (mutationStatus === "success") {
      setStatus("saved");
      timeoutRef.current = setTimeout(() => setStatus("idle"), 2000);
    } else if (mutationStatus === "error") {
      setStatus("error");
    }
    return () => clearTimeout(timeoutRef.current);
  }, [mutationStatus]);

  return status;
}
