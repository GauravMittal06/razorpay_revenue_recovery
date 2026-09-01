import { useEffect, useRef, useState } from "react";

// Scroll-triggered fade-up wrapper. Purely presentational (no data
// dependency), ported from the V0 reference as-is — this is the one V0
// primitive that's safe to reuse directly rather than just as inspiration,
// since it carries no mock data, fake logic, or fictional content.
export default function Reveal({ children, className = "", delay = 0, as: Tag = "div" }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true);
            io.disconnect();
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      className={className}
      style={
        shown
          ? { animation: "fade-up 0.6s cubic-bezier(0.16,1,0.3,1) both", animationDelay: `${delay}ms` }
          : { opacity: 0 }
      }
    >
      {children}
    </Tag>
  );
}
