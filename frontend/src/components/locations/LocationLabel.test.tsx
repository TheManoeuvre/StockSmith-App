import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LARGE_LABEL, SMALL_LABEL } from "../../lib/labels";
import { LocationLabel } from "./LocationLabel";

describe("LocationLabel", () => {
  it("small: the location's own name leads, with its path above and the code underneath", () => {
    render(<LocationLabel ancestors={["Workshop", "Oils rack"]} name="Bin 1" code="LOC-000042" size={SMALL_LABEL} mm="1mm" />);
    expect(screen.getByText("Workshop › Oils rack")).toBeInTheDocument();
    expect(screen.getByText("Bin 1")).toBeInTheDocument();
    expect(screen.getByText("LOC-000042")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Code 128 barcode for LOC-000042" })).toBeInTheDocument();
  });

  it("small: a deep path leaves out the readable code so the barcode can grow", () => {
    render(
      <LocationLabel
        ancestors={["Warehouse Annexe", "Rack 04", "Shelf C"]}
        name="Grey Box 03"
        code="LOC-000043"
        size={SMALL_LABEL}
        mm="1mm"
      />,
    );
    expect(screen.getByText("Warehouse Annexe › Rack 04 › Shelf C")).toBeInTheDocument();
    expect(screen.queryByText("LOC-000043")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /LOC-000043/ })).toBeInTheDocument();
  });

  it("small: a top-level location has no path line and no code text", () => {
    render(<LocationLabel ancestors={[]} name="Stockroom" code="LOC-000044" size={SMALL_LABEL} mm="1mm" />);
    expect(screen.getByText("Stockroom")).toBeInTheDocument();
    expect(screen.queryByText("LOC-000044")).not.toBeInTheDocument();
  });

  it("large: the full path is the headline and the code is always printed", () => {
    render(
      <LocationLabel
        ancestors={["Warehouse Annexe", "Rack 04", "Shelf C"]}
        name="Grey Box 03"
        code="LOC-000043"
        size={LARGE_LABEL}
        mm="1mm"
      />,
    );
    expect(screen.getByText("Warehouse Annexe › Rack 04 › Shelf C ›")).toBeInTheDocument();
    expect(screen.getByText("Grey Box 03")).toBeInTheDocument();
    expect(screen.getByText("LOC-000043")).toBeInTheDocument();
  });

  it("is sized from the label's own dimensions, in the unit it's given", () => {
    const { container } = render(
      <LocationLabel ancestors={[]} name="X" code="LOC-000001" size={SMALL_LABEL} mm="6px" />,
    );
    const frame = container.firstElementChild as HTMLElement;
    expect(frame.style.width).toBe("calc(306px)"); // 51 mm at 6 px per mm
    expect(frame.style.height).toBe("calc(150px)"); // 25 mm
  });

  it("encodes the code as Code 128 bars", () => {
    const { container } = render(
      <LocationLabel ancestors={["A"]} name="B" code="LOC-000045" size={SMALL_LABEL} mm="1mm" />,
    );
    const svg = container.querySelector("svg")!;
    expect(svg.querySelectorAll("rect").length).toBeGreaterThan(20);
    expect(svg.getAttribute("viewBox")).toMatch(/^0 0 \d+/);
    expect(svg.getAttribute("preserveAspectRatio")).toBe("none");
  });
});
