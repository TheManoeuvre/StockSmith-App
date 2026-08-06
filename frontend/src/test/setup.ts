import "@testing-library/jest-dom/vitest";

// jsdom implements neither of these, and the materials list uses IntersectionObserver
// (via useLazyVisible) to defer rendering rows — without a stub the page throws and renders
// its error boundary instead.
//
// The stub reports every observed element as visible immediately, which is what a test
// wants: lazy content is present without having to fake a scroll.
class ImmediateIntersectionObserver implements IntersectionObserver {
  readonly root: Element | Document | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];

  constructor(private callback: IntersectionObserverCallback) {}

  observe(target: Element): void {
    this.callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver
    );
  }
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

globalThis.IntersectionObserver = ImmediateIntersectionObserver as unknown as typeof IntersectionObserver;
window.scrollTo = () => {};
