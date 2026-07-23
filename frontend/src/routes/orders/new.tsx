import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { productsApi } from "../../api/products";
import { ordersApi, type OrderLineInput } from "../../api/orders";
import { appSettingsApi, type CurrencyCode } from "../../api/appSettings";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import { OrderLineEditor } from "../../components/orders/OrderLineEditor";
import { ErrorBanner } from "../../components/common/ErrorBanner";

export const Route = createFileRoute("/orders/new")({
  component: NewOrder,
});

const CURRENCY_OPTIONS: CurrencyCode[] = ["GBP", "USD", "EUR"];
const CURRENCY_LABELS: Record<CurrencyCode, string> = { GBP: "£ GBP", USD: "$ USD", EUR: "€ EUR" };

function NewOrder() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: products } = useQuery({ queryKey: ["products"], queryFn: productsApi.list });
  const { data: shippingProfiles } = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    queryFn: shippingProfilesApi.list,
  });
  const { data: defaultCurrency } = useQuery({
    queryKey: ["settings", "default-currency"],
    queryFn: appSettingsApi.getDefaultCurrency,
  });

  const [buyerName, setBuyerName] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<OrderLineInput[]>([]);
  const [currency, setCurrency] = useState<string>("");
  const [shippingProfileId, setShippingProfileId] = useState("");
  const [shippingCharged, setShippingCharged] = useState("");

  // Pre-fills the currency from the shop-wide default the first time it loads — the user
  // can still change it per order afterward, and this effect never overwrites that choice.
  useEffect(() => {
    if (defaultCurrency && !currency) setCurrency(defaultCurrency.default_currency);
  }, [defaultCurrency, currency]);

  const profiles = shippingProfiles ?? [];

  const createMutation = useMutation({
    mutationFn: () =>
      ordersApi.create({
        buyer_name: buyerName || null,
        notes: notes || null,
        currency: currency || null,
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        shipping_charged: shippingCharged || null,
        lines,
      }),
    onSuccess: (order) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      navigate({ to: "/orders/$orderId", params: { orderId: String(order.id) } });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">New order</h1>

      <div className="flex flex-wrap gap-4 rounded bg-white p-4 shadow-sm">
        <label className="flex flex-col gap-1">
          <span className="text-sm">Buyer name</span>
          <input
            className="rounded border border-slate-300 px-2 py-1"
            value={buyerName}
            onChange={(e) => setBuyerName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Notes</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Currency</span>
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          >
            {CURRENCY_OPTIONS.map((code) => (
              <option key={code} value={code}>
                {CURRENCY_LABELS[code]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-4 rounded bg-white p-4 shadow-sm">
        <label className="flex flex-col gap-1">
          <span className="text-sm">Shipping profile</span>
          <select
            className="w-48 rounded border border-slate-300 px-2 py-1"
            value={shippingProfileId}
            onChange={(e) => {
              const id = e.target.value;
              setShippingProfileId(id);
              const profile = profiles.find((p) => String(p.id) === id);
              if (profile) setShippingCharged(profile.price);
            }}
          >
            <option value="">No profile</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Shipping charged (£)</span>
          <input
            className="w-28 rounded border border-slate-300 px-2 py-1"
            placeholder="0.00"
            value={shippingCharged}
            onChange={(e) => setShippingCharged(e.target.value)}
          />
        </label>
      </div>

      <OrderLineEditor products={products ?? []} lines={lines} onChange={setLines} />

      <div>
        <button
          onClick={() => createMutation.mutate()}
          disabled={lines.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Save order
        </button>
      </div>
      <ErrorBanner error={createMutation.error} />
    </div>
  );
}
