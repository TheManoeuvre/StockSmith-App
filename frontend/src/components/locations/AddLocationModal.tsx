import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { locationsApi, type LocationRow } from "../../api/locations";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";
import { LocationPicker } from "./LocationPicker";
import { useInvalidateLocations } from "./useLocations";

export function AddLocationModal({
  rows,
  initialParentId,
  onClose,
  onCreated,
}: {
  rows: LocationRow[];
  initialParentId: number | null;
  onClose: () => void;
  onCreated: (created: LocationRow) => void;
}) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<number | null>(initialParentId);
  const invalidate = useInvalidateLocations();
  const create = useMutation({
    mutationFn: () => locationsApi.create({ name: name.trim(), parent_id: parentId }),
    onSuccess: (created) => {
      invalidate();
      onCreated(created);
    },
  });

  return (
    <Modal
      title="Add a location"
      onClose={onClose}
      footer={
        <>
          <button onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            form="add-location-form"
            type="submit"
            disabled={!name.trim() || create.isPending}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add location
          </button>
        </>
      }
    >
      <form
        id="add-location-form"
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          Name
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Shelf 01, Grey Box 03, Loft…"
            className="rounded border border-slate-300 px-2 py-1.5 text-sm font-normal"
          />
        </label>
        <div className="flex flex-col gap-1 text-xs font-medium text-slate-700">
          Inside
          <LocationPicker rows={rows} value={parentId} onChange={setParentId} allowTopLevel label="Create inside" />
        </div>
        <p className="text-xs text-slate-500">
          Locations can sit inside each other as deeply as you like: a box on a shelf in a room.
        </p>
        <ErrorBanner error={create.error} />
      </form>
    </Modal>
  );
}
