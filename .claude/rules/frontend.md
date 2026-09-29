---
paths:
  - "**/*.tsx"
  - "**/*.jsx"
  - "**/*.ts"
  - "**/*.js"
  - "**/*.vue"
  - "**/*.svelte"
  - "**/*.css"
  - "**/*.scss"
  - "**/*.html"
  - "**/components/**"
  - "**/pages/**"
  - "**/views/**"
  - "**/layouts/**"
  - "**/styles/**"
---

# Frontend

## Naming and files (TS/JS)

- `PascalCase` for component and class files (`UserProfile.tsx`), kebab-case for utilities and directories (`date-utils.ts`).
- `camelCase` functions and variables (`getUser`). Handlers: `handle*` internally, `on*` as props. Acronyms as words: `userId`, not `userID`.
- Named exports over default. One component per file. Type-only imports go last.

## Design Tokens

Before writing frontend code, find the project's existing tokens file (`tokens.css`, `variables.css`, `theme.ts`, `tailwind.config.*`, `_variables.scss`). If none exists, create one. Never hardcode raw values in components.

Required token categories: colors (semantic names with dark mode variants), spacing scale, border radius, shadows (elevation system), typography (display + body + mono fonts, type scale, weights), breakpoints, transitions (durations + easing), z-index scale.

## Design principle & libraries

Pick one design principle and stick to it — don't mix randomly. Use whatever CSS approach, component primitives, animation, and icon libraries the project already has; never introduce a competing one.

For the full design doctrine — the principle catalog, typography, color, backgrounds, and motion guidance — invoke the **frontend-designer** agent, which owns it. This rule stays scoped to the always-apply constraints below.

## Layout

- CSS Grid for 2D, Flexbox for 1D. Use `gap`, not margin hacks.
- Semantic HTML: `<header>`, `<nav>`, `<main>`, `<section>`, `<article>`, `<footer>`.
- Mobile-first. Touch targets: minimum 44x44px.

## Accessibility (non-negotiable)

- All interactive elements keyboard-accessible.
- Images: meaningful `alt` text. Decorative: `alt=""`.
- Form inputs: associated `<label>` or `aria-label`.
- Contrast: 4.5:1 normal text, 3:1 large text.
- Visible focus indicators. Never `outline: none` without replacement.
- Color never the sole indicator.
- `aria-live` for dynamic content. Respect `prefers-reduced-motion` and `prefers-color-scheme`.

## Performance

- Images: `loading="lazy"` below fold, explicit `width`/`height`.
- Fonts: `font-display: swap`.
- Animations: `transform` and `opacity` only.
- Large lists: virtualize at 100+ items.
- Bundle size: never import a whole library for one function.
