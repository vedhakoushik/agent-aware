/** Agent-Aware chat-shell design system — indigo reference language
 *  (Manrope headings, Hanken Grotesk body, #2c2abc primary, #f9f9ff canvas).
 *  Token NAMES are stable (orange/flame/etc. read as "primary/primary-hot")
 *  so components never change when the brand palette does. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        cream: "#F9F9FF",   // page background
        card: "#FFFFFF",    // raised surfaces
        warm: "#EEF0FA",    // recessed surfaces / hover
        line: "#E4E6F4",    // borders
        ink: "#16163F",     // primary text
        ink2: "#31325C",    // secondary text
        muted: "#5B5D80",   // tertiary text
        faint: "#8C8EAD",   // captions / placeholders
        orange: "#2C2ABC",  // primary (indigo)
        flame: "#4F46E5",   // primary hot end
        gold: "#6366F1",    // accent
        choco: "#1E1B7B",   // deep accent
        rust: "#3730A3",    // pressed / dark accent
        good: "#1E7F4F",    // success
        bad: "#C0392B",     // error
        warn: "#B45309"     // warning
      },
      fontFamily: {
        display: ["Manrope", "sans-serif"],
        sans: ["'Hanken Grotesk'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "monospace"],
      },
      boxShadow: {
        card: "0 1px 3px rgba(22,22,63,.06), 0 8px 24px rgba(22,22,63,.05)",
        pop: "0 12px 34px rgba(22,22,63,.14)",
        glow: "0 0 0 3px rgba(44,42,188,.14), 0 6px 22px rgba(79,70,229,.18)",
      },
      keyframes: {
        ping2: { "75%, 100%": { transform: "scale(2.1)", opacity: "0" } },
        rise: { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        shimmer: { to: { backgroundPosition: "200% 0" } },
      },
      animation: {
        ping2: "ping2 1.5s cubic-bezier(0,0,.2,1) infinite",
        rise: "rise .35s ease-out both",
        shimmer: "shimmer 2.2s linear infinite",
      },
    },
  },
  plugins: [],
};
