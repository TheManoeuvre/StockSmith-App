import { ErrorBanner } from "stocksmith-ui";

export const WithMessage = () => (
  <div className="w-96">
    <ErrorBanner error={new Error("Supplier Filamentive already exists.")} />
  </div>
);

/** A non-Error value gets the generic message. */
export const Generic = () => (
  <div className="w-96">
    <ErrorBanner error={{ status: 500 }} />
  </div>
);
