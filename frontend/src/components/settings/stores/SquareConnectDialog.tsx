import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { NamedOption } from "../../../api/listingProfiles";
import { platformsApi, type PlatformEnvironment } from "../../../api/platforms";
import { ErrorBanner } from "../../common/ErrorBanner";
import { Modal } from "../../common/Modal";

/**
 * Square has no OAuth redirect (see docs/plan-square-integration.md) — connecting means
 * pasting a personal access token, then picking which of the account's locations to sync
 * orders from (an order belongs to a location, and this app only ever syncs one). Two
 * backend calls, one dialog: connect validates the token and returns the location list;
 * choosing one confirms it separately, since the seller needs to actually see the options
 * before picking.
 *
 * `mode="change-location"` skips the token step entirely — an already-connected account can
 * re-list its locations (GET /platforms/square/locations) without re-pasting the token.
 */
export function SquareConnectDialog({
  mode = "connect",
  onClose,
}: {
  mode?: "connect" | "change-location";
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [accessToken, setAccessToken] = useState("");
  const [environment, setEnvironment] = useState<PlatformEnvironment>("sandbox");
  const [locations, setLocations] = useState<NamedOption[] | null>(null);
  const [selectedLocationId, setSelectedLocationId] = useState<string>("");

  const connectMutation = useMutation({
    mutationFn: () => platformsApi.connectSquare(accessToken.trim(), environment),
    onSuccess: (options) => {
      setLocations(options);
      setSelectedLocationId(options[0]?.id ?? "");
    },
  });

  const existingLocations = useQuery({
    queryKey: ["platforms", "square", "locations"],
    queryFn: () => platformsApi.listSquareLocations(),
    enabled: mode === "change-location",
  });
  useEffect(() => {
    if (mode === "change-location" && existingLocations.data) {
      setLocations(existingLocations.data);
      setSelectedLocationId(existingLocations.data[0]?.id ?? "");
    }
  }, [mode, existingLocations.data]);

  const confirmMutation = useMutation({
    mutationFn: () => platformsApi.setSquareLocation(selectedLocationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", "square", "status"] });
      queryClient.invalidateQueries({ queryKey: ["platforms", "sync-summary"] });
      onClose();
    },
  });

  if (locations !== null) {
    return (
      <Modal
        title="Choose a Square location"
        subtitle={
          <p className="mt-1 text-sm text-slate-500">
            Orders are synced from one location. You can change this later.
          </p>
        }
        onClose={onClose}
        footer={
          <>
            <button
              type="button"
              onClick={onClose}
              className="h-8 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-900 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => confirmMutation.mutate()}
              disabled={!selectedLocationId || confirmMutation.isPending}
              className="h-8 rounded-md bg-slate-900 px-3 text-sm font-semibold text-white disabled:opacity-50"
            >
              {confirmMutation.isPending ? "Saving…" : "Confirm"}
            </button>
          </>
        }
      >
        {locations.length === 0 ? (
          <p className="text-sm text-slate-600">
            This Square account has no locations. Add one in Square's own dashboard, then reconnect.
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {locations.map((location) => (
              <label
                key={location.id}
                className="flex items-center gap-2 rounded border border-slate-200 px-2.5 py-2 text-sm hover:bg-slate-50 has-[:checked]:border-slate-900 has-[:checked]:bg-slate-50"
              >
                <input
                  type="radio"
                  name="square-location"
                  value={location.id}
                  checked={selectedLocationId === location.id}
                  onChange={() => setSelectedLocationId(location.id)}
                />
                {location.label}
              </label>
            ))}
          </div>
        )}
        <ErrorBanner error={confirmMutation.error ?? existingLocations.error} />
      </Modal>
    );
  }

  if (mode === "change-location") {
    return (
      <Modal title="Choose a Square location" onClose={onClose}>
        {existingLocations.isError ? (
          <ErrorBanner error={existingLocations.error} />
        ) : (
          <p className="text-sm text-slate-500">Loading locations…</p>
        )}
      </Modal>
    );
  }

  return (
    <Modal
      title="Connect Square"
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => connectMutation.mutate()}
            disabled={!accessToken.trim() || connectMutation.isPending}
            className="h-8 rounded-md bg-slate-900 px-3 text-sm font-semibold text-white disabled:opacity-50"
          >
            {connectMutation.isPending ? "Checking…" : "Continue"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-slate-600">
          Paste a Square access token from the{" "}
          <span className="font-medium">Square Developer Console</span> (your app → Sandbox or
          Production → Credentials). It's stored encrypted and never shown again.
        </p>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700">Access token</span>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1.5 font-mono text-xs"
            placeholder="EAAA…"
          />
        </label>
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="font-medium text-slate-700">Environment</legend>
          <div className="flex gap-3">
            {(["sandbox", "production"] as const).map((option) => (
              <label key={option} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="square-environment"
                  value={option}
                  checked={environment === option}
                  onChange={() => setEnvironment(option)}
                />
                {option === "sandbox" ? "Sandbox" : "Production"}
              </label>
            ))}
          </div>
        </fieldset>
        <ErrorBanner error={connectMutation.error} />
      </div>
    </Modal>
  );
}
