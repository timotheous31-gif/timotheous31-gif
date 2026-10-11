import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic tokens only; the palette is declared once in globals.css.
        bg: token("bg"),
        "bg-lift": token("bg-lift"),

        // The four materials, for the cases where a utility is clearer than
        // the surface class (an inner well, a header band).
        command: token("command"),
        instrument: token("instrument"),
        sheet: token("sheet"),
        telemetry: token("telemetry"),
        raised: token("raised"),

        // Edges, by how much they are meant to say.
        "rule-quiet": token("rule-quiet"),
        rule: token("rule"),
        "rule-strong": token("rule-strong"),
        edge: token("edge"),
        // Phase 1/2 wrote `border-line`/`border-line-strong` in a hundred
        // places; these keep that vocabulary pointing at the new scale.
        line: token("rule"),
        "line-strong": token("edge"),

        fg: token("text"),
        muted: token("text-2"),
        faint: token("text-3"),

        accent: token("accent"),
        "accent-dim": token("accent-dim"),
        blue: token("blue"),
        brass: token("brass"),
        "brass-dim": token("brass-dim"),

        ok: token("ok"),
        caution: token("caution"),
        danger: token("alert"),
        info: token("blue"),

        likely: token("likely"),
        probable: token("probable"),
        possible: token("possible"),
        weak: token("weak"),
      },
      borderRadius: {
        DEFAULT: "var(--r-chip)",
        sm: "var(--r-chip)",
        md: "var(--r-control)",
        lg: "var(--r-surface)",
        xl: "var(--r-surface)",
      },
      boxShadow: {
        recessed: "var(--shadow-recessed)",
        raised: "var(--shadow-raised)",
        command: "var(--shadow-command)",
        bloom: "var(--bloom)",
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
      fontSize: {
        // The typographic roles. Before this the scale topped out at 26px and
        // 76% of the product sat at one weight, which is why nothing on screen
        // looked more important than anything else.
        eyebrow: ["10px", { lineHeight: "1.2", letterSpacing: "0.16em", fontWeight: "600" }],
        meta: ["11.5px", { lineHeight: "1.45" }],
        body: ["13px", { lineHeight: "1.55" }],
        lede: ["14px", { lineHeight: "1.6" }],
        section: ["17px", { lineHeight: "1.25", letterSpacing: "-0.01em", fontWeight: "600" }],
        subject: ["24px", { lineHeight: "1.15", letterSpacing: "-0.02em", fontWeight: "600" }],
        display: ["34px", { lineHeight: "1.1", letterSpacing: "-0.025em", fontWeight: "600" }],
        "readout-sm": ["20px", { lineHeight: "1", letterSpacing: "-0.02em" }],
        readout: ["30px", { lineHeight: "1", letterSpacing: "-0.03em" }],
        "readout-lg": ["40px", { lineHeight: "1", letterSpacing: "-0.035em" }],
      },
      letterSpacing: {
        label: "0.16em",
        wide: "0.08em",
      },
      transitionDuration: {
        DEFAULT: "140ms",
      },
    },
  },
  plugins: [],
};

export default config;
