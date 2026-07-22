import { useEffect, useRef } from "react";

/**
 * Robot-head mascot — a hand-built SVG in the style of the reference art
 * (teal head, coral concentric eyes, cream grille). It's alive: the head tilts
 * and the pupils parallax toward the cursor, and the whole thing "docks"
 * (shrinks + fades + drifts up) as you scroll, matching the old mascot's role.
 */
export default function RobotMascot() {
  const hostRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<SVGGElement>(null);
  const pupilsRef = useRef<SVGGElement>(null);

  useEffect(() => {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const host = hostRef.current;
    if (!host) return;

    // ── cursor parallax (eased) ──
    let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
    const onMouse = (e: MouseEvent) => {
      tx = (e.clientX / innerWidth - 0.5) * 2; // -1..1
      ty = (e.clientY / innerHeight - 0.5) * 2;
    };
    const tick = () => {
      cx += (tx - cx) * 0.08;
      cy += (ty - cy) * 0.08;
      if (headRef.current)
        headRef.current.style.transform = `translate(${cx * 6}px, ${cy * 6}px) rotate(${cx * 2}deg)`;
      if (pupilsRef.current)
        pupilsRef.current.style.transform = `translate(${cx * 7}px, ${cy * 7}px)`;
      raf = requestAnimationFrame(tick);
    };
    if (!reduced) {
      window.addEventListener("mousemove", onMouse);
      raf = requestAnimationFrame(tick);
    }

    // ── scroll dock ──
    let sraf = 0;
    const onScroll = () => {
      cancelAnimationFrame(sraf);
      sraf = requestAnimationFrame(() => {
        const max = Math.max(document.documentElement.scrollHeight - innerHeight, 1);
        const p = Math.min(window.scrollY / max, 1);
        host.style.transform = `translate(-50%, calc(-50% + ${-p * 12}vh)) scale(${1 - p * 0.4})`;
        host.style.opacity = String(0.9 - p * 0.12);
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      window.removeEventListener("mousemove", onMouse);
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
      cancelAnimationFrame(sraf);
    };
  }, []);

  return (
    <div ref={hostRef} className="robot-mascot" aria-hidden="true">
      <svg viewBox="0 0 260 260" width="440" height="440" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <radialGradient id="rm-glow" cx="50%" cy="46%" r="52%">
            <stop offset="0%" stopColor="#59A79C" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#59A79C" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* soft aura */}
        <circle cx="130" cy="126" r="118" fill="url(#rm-glow)" />

        {/* ambient dots */}
        <g fill="#F3E8C6">
          <circle cx="40" cy="150" r="3.4" />
          <circle cx="222" cy="96" r="3.4" />
          <circle cx="54" cy="70" r="2.6" />
          <circle cx="206" cy="180" r="2.6" />
        </g>
        <g fill="#E9C64C">
          <circle cx="34" cy="110" r="3" />
          <circle cx="226" cy="140" r="3" />
          <circle cx="72" cy="212" r="2.4" />
          <circle cx="196" cy="52" r="2.4" />
        </g>

        <g ref={headRef} style={{ transformOrigin: "130px 130px", transformBox: "fill-box" as never }}>
          {/* antennae */}
          <g stroke="#14302C" strokeWidth="4" strokeLinecap="round">
            <line x1="88" y1="70" x2="74" y2="44" />
            <line x1="172" y1="70" x2="186" y2="44" />
          </g>
          <circle cx="72" cy="40" r="7" fill="#EF8A6B" stroke="#14302C" strokeWidth="3.5" />
          <circle cx="188" cy="40" r="7" fill="#EF8A6B" stroke="#14302C" strokeWidth="3.5" />
          <rect x="123" y="40" width="14" height="18" rx="3" fill="#3E8B82" stroke="#14302C" strokeWidth="3.5" />
          <circle cx="130" cy="38" r="4.5" fill="#E9C64C" stroke="#14302C" strokeWidth="2.5" />

          {/* ears */}
          <rect x="36" y="116" width="26" height="56" rx="7" fill="#EF8A6B" stroke="#14302C" strokeWidth="4" />
          <rect x="198" y="116" width="26" height="56" rx="7" fill="#EF8A6B" stroke="#14302C" strokeWidth="4" />
          <g stroke="#14302C" strokeWidth="2.4" strokeLinecap="round" opacity="0.7">
            <line x1="44" y1="128" x2="54" y2="128" />
            <line x1="44" y1="138" x2="54" y2="138" />
            <line x1="206" y1="128" x2="216" y2="128" />
            <line x1="206" y1="138" x2="216" y2="138" />
          </g>

          {/* head shell */}
          <path
            d="M70 206 L70 120 C70 80 100 58 130 58 C160 58 190 80 190 120 L190 206 C190 213 184 219 176 219 L84 219 C76 219 70 213 70 206 Z"
            fill="#59A79C"
            stroke="#14302C"
            strokeWidth="5"
            strokeLinejoin="round"
          />

          {/* brow crest */}
          <path
            d="M92 108 L100 94 L118 94 L124 104 L136 104 L142 94 L160 94 L168 108 Z"
            fill="#F3E8C6"
            stroke="#14302C"
            strokeWidth="3.5"
            strokeLinejoin="round"
          />

          {/* eyes */}
          {[104, 156].map((ex) => (
            <g key={ex}>
              <circle cx={ex} cy="140" r="26" fill="#1D3B37" stroke="#14302C" strokeWidth="4" />
              <circle cx={ex} cy="140" r="21" fill="none" stroke="#EF8A6B" strokeWidth="6" />
              <circle cx={ex} cy="140" r="12" fill="none" stroke="#F3E8C6" strokeWidth="4" />
            </g>
          ))}
          <g ref={pupilsRef}>
            <circle cx="104" cy="140" r="7" fill="#14302C" />
            <circle cx="156" cy="140" r="7" fill="#14302C" />
            <circle cx="107" cy="137" r="2.4" fill="#F3E8C6" />
            <circle cx="159" cy="137" r="2.4" fill="#F3E8C6" />
          </g>

          {/* nose */}
          <path d="M130 156 L123 170 L137 170 Z" fill="#14302C" />

          {/* grille mouth */}
          <rect x="96" y="180" width="68" height="22" rx="6" fill="#1D3B37" stroke="#14302C" strokeWidth="4" />
          <g fill="#F3E8C6">
            {[104, 114, 124, 134, 144, 154].map((mx) => (
              <rect key={mx} x={mx} y="184" width="5" height="14" rx="1.5" />
            ))}
          </g>

          {/* rivets */}
          <g fill="#14302C" opacity="0.55">
            <circle cx="80" cy="120" r="2.4" />
            <circle cx="180" cy="120" r="2.4" />
            <circle cx="82" cy="204" r="2.4" />
            <circle cx="178" cy="204" r="2.4" />
          </g>
        </g>
      </svg>
    </div>
  );
}
