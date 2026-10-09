import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { NewProductStoreChips } from "./NewProductStoreChips";

const STORES = [
  { platform: "etsy" as const, defaultOn: true },
  { platform: "ebay" as const, defaultOn: false },
];

it("starts each chip at its store's default", () => {
  render(<NewProductStoreChips stores={STORES} choices={{}} onChange={() => {}} />);
  expect(screen.getByRole("switch", { name: "Etsy" })).toBeChecked();
  expect(screen.getByRole("switch", { name: "eBay" })).not.toBeChecked();
});

it("reports only the chip that was flipped", async () => {
  const onChange = vi.fn();
  render(<NewProductStoreChips stores={STORES} choices={{}} onChange={onChange} />);
  await userEvent.click(screen.getByRole("switch", { name: "eBay" }));
  expect(onChange).toHaveBeenCalledWith({ ebay: true });
});

it("renders nothing when no store is connected", () => {
  const { container } = render(<NewProductStoreChips stores={[]} choices={{}} onChange={() => {}} />);
  expect(container).toBeEmptyDOMElement();
});
