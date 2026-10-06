/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface-1)", page: "var(--page)", ink: "var(--text-primary)", ink2: "var(--text-secondary)",
        muted: "var(--muted)", line: "var(--grid)", edge: "var(--border)", accent: "var(--series-1)",
      },
      fontFamily: { sans: ['system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'] },
    },
  },
  plugins: [],
};
