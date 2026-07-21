import { useEffect, useState, type RefObject } from "react";

/** True once the referenced element has scrolled near the viewport; stays true afterward
 * (images don't need to re-hide once loaded). Used to defer offscreen thumbnail fetches. */
export function useLazyVisible(ref: RefObject<Element | null>): boolean {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (visible || !ref.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" }
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref, visible]);

  return visible;
}
