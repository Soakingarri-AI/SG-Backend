import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // SoakinGarri brand palette (garri gold + deep earth + agbada indigo).
        brand: {
          50: "#fdf7ec",
          100: "#f9e9c9",
          300: "#eec06a",
          500: "#e0a034",
          600: "#c07f1f",
          700: "#8a5a16",
          900: "#3d2708",
        },
        indigoblack: "#0b0e1a",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "system-ui", "sans-serif"],
      },
      backgroundImage: {
        "hero-radial":
          "radial-gradient(60% 60% at 50% 0%, rgba(224,160,52,0.18) 0%, rgba(11,14,26,0) 70%)",
      },
    },
  },
  plugins: [],
};

export default config;
