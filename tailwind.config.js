/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./templates/**/*.html",
    "./static/**/*.js"
  ],

  safelist: [
    // ── Income / Expense dynamic classes (Jinja conditionals) ──
    "bg-income", "bg-income/10",
    "bg-expense", "bg-expense/10",
    "text-income", "text-expense",
    "border-income", "border-income/40",
    "border-expense", "border-expense/40",

    // ── Budget page category palette (loop-generated) ──
    "bg-blue-600/10",    "text-blue-400",
    "bg-emerald-500/10", "text-emerald-400",
    "bg-orange-500/10",  "text-orange-400",
    "bg-purple-500/10",  "text-purple-400",
    "bg-rose-500/10",    "text-rose-400",
    "bg-yellow-500/10",  "text-yellow-400",
    "bg-primary/10",     "text-primary",
    "bg-amber-500/10",   "text-amber-400",

    // ── Status-conditional backgrounds & borders ──
    "border-expense/20", "border-expense/40",
    "border-amber-500/20", "border-amber-500/30", "border-amber-500/40",
    "hover:border-expense/40", "hover:border-expense/50",
    "hover:border-amber-500/30", "hover:border-amber-500/40",

    // ── Dynamic text colors from conditionals ──
    "text-amber-400", "text-white",
    "text-slate-300", "text-slate-400", "text-slate-500", "text-slate-600",

    // ── Transaction type badge colors ──
    "bg-emerald-500/20", "bg-emerald-500/5",
    "bg-red-500/20",     "bg-red-500/5",
    "border-emerald-400","border-red-400",

    // ── Misc dynamic ──
    "uppercase",
    "shadow-[0_0_20px_rgba(32,148,243,0.1)]",
    "group-hover:scale-110",
  ],

  theme: {
    extend: {
      colors: {
        "primary":          "#2094f3",
        "background-light": "#f5f7f8",
        "background-dark":  "#101a22",
        "charcoal":         "#182834",
        "border-muted":     "#314f68",
        "expense":          "#ef4444",
        "income":           "#22c55e",
        "sidebar-bg":       "#0d141c",
        "navbar-bg":        "#0d141c",
        "content-bg":       "#0d141c",
        "card-bg":          "#141d26",
      },
      fontFamily: {
        display: ["Inter", "sans-serif"],
      },
      borderRadius: {
        DEFAULT: "0.125rem",
        lg:    "0.25rem",
        xl:    "0.5rem",
        "2xl": "1rem",
        full:  "0.75rem",
      },
    },
  },

  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/container-queries'),
  ],
}