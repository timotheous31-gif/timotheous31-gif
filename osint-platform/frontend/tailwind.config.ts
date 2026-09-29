import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic tokens only. The palette is declared once in globals.css and
        // consumed by name, so a colour decision is made in one place.
        bg: token("bg"),
        surface: token("surface"),
        panel: token("panel"),
        raised: token("raised"),
        line: token("line"),
        "line-strong": token("line-strong"),
        fg: token("fg"),
        muted: token("muted"),
        faint: token("faint"),
        accent: token("accent"),
        "accent-soft": token("accent-soft"),
        ok: token("ok"),
        caution: token("caution"),
        danger: token("danger"),
        info: token("info"),
        likely: token("likely"),
        probable: token("probable"),
        possible: token("possible"),
        weak: token("weak"),
      },
      borderRadius: {
        // Sharper than the framework default: panels read as instrument
        // housings, not cards on a marketing page.
        DEFAULT: "0.25rem",
        md: "0.3125rem",
        lg: "0.375rem",
        xl: "0.5rem",
      },
      boxShadow: {
        // The only "glow" in the product: a hairline plus a short, dim halo,
        // used to mark the one element that currently has focus or is live.
        glow: "0 0 0 1px rgb(var(--accent) / 0.35), 0 0 18px -6px rgb(var(--accent) / 0.45)",
        panel: "0 1px 0 0 rgb(var(--line) / 0.6)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Inter",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      letterSpacing: {
        label: "0.08em",
      },
      transitionDuration: {
        DEFAULT: "120ms",
      },
    },
  },
  plugins: [],
};

export default config;
