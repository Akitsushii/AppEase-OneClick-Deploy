/** @type {import('tailwindcss').Config} */
/* AppEase — Tailwind build config.
   This file replaces the tailwind.config block that used to be duplicated
   inline in base.html, login.html and report.html. It is now the single
   source of truth for the design tokens.

   IMPORTANT: the static/js glob is load-bearing. Several classes are only
   ever written from JavaScript (search highlighting, disabled pagination,
   notification severity borders, validation border swaps). Remove that glob
   and those classes are silently dropped from the build with no error. */
module.exports = {
  content: [
    './server/templates/**/*.html',
    './server/static/js/**/*.js',
  ],
  theme: {
    extend: {
      /* PHASE 1 REDESIGN - radii.
         Every one of these keys already appears in the markup (rounded-md is
         the house corner: 15 uses in base.html, 15 in dashboard.html), so
         remapping the scale here re-corners all eleven templates at once with
         zero template edits. Old values in comments for easy revert. */
      borderRadius: {
        sm: '0.375rem',    // 6px  (was 2px)
        DEFAULT: '0.5rem', // 8px  (was 4px)
        md: '0.625rem',    // 10px (was 6px)
        lg: '0.75rem',     // 12px (was 8px)
        xl: '1rem',        // 16px (was 12px)
        '2xl': '1.25rem',  // 20px (was 16px)
      },
      /* Defined now, used in phase 2 when the card markup changes. Unused
         keys cost nothing - Tailwind only emits a class when it is written
         somewhere the content globs can see. */
      boxShadow: {
        card: '0 1px 2px 0 rgb(16 24 40 / 0.04), 0 1px 3px 0 rgb(16 24 40 / 0.06)',
        'card-hover': '0 2px 4px -1px rgb(16 24 40 / 0.06), 0 4px 8px -2px rgb(16 24 40 / 0.08)',
        raised: '0 4px 6px -2px rgb(16 24 40 / 0.05), 0 12px 16px -4px rgb(16 24 40 / 0.10)',
      },
      fontFamily: {
        /* Outfit, self-hosted (see the @font-face at the top of app.css).
           The fallback chain is unchanged, so if the woff2 is missing the app
           still renders in a system sans rather than a serif. */
        sans: ['Outfit', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      colors: {
        // sidebar surface - LIGHT. The dark slab was the strongest single
        // dating signal on the page; a white rail separated from the canvas
        // by one hairline is the current admin idiom. Token NAMES are
        // unchanged so every usage in base.html keeps working - only the
        // values moved, plus the text-white swaps the light rail requires.
        // Graphite values from phase 2 kept in comments for revert.
        sidebar: '#ffffff',           // was #1c1d21
        'sidebar-hover': '#f4f5f8',   // was #26272c
        'sidebar-text': '#4b5563',    // was #a8a9b0
        'sidebar-border': '#e6e8ee',  // was #303138
        'sidebar-muted': '#6b7280',   // was #8a8b93
        'sidebar-label': '#9ca3af',   // was #5a5b63
        // Active nav item. The old active state was a 4px primary left bar
        // (the AdminLTE tell); this replaces it with a lighter surface, one
        // step above sidebar-hover so active still reads clearly against it.
        'sidebar-active': '#e8effc',  // was #31323a - tinted with the brand
                                      // colour so the active item reads as
                                      // selected, not merely hovered.
        // brand - darkened from #4f8ef7. White text on the old blue was only
        // ~3.2:1; this is ~4.8:1. Safe to darken only because phase 2 removed
        // the last use of primary on the dark sidebar (the active-item left
        // border), so primary now appears exclusively on light surfaces.
        primary: '#2f68dd',
        // text ramp
        ink: '#1a202c',      // headings
        body: '#1f2937',     // table text
        label: '#374151',    // form + button text
        muted: '#6b7280',    // secondary text
        faint: '#9ca3af',    // disabled / offline
        // lines + surfaces
        field: '#d1d5db',    // input borders
        line: '#e8eaf0',     // container borders
        rule: '#eef2f7',     // in-table dividers
        canvas: '#f1f2f6',   // page background (was #f7f8fc - only 3% off
                             // white, so cards never read as raised surfaces)
        subtle: '#f8fafc',   // hover / thead background
        // status
        danger: '#ef4444',   // invalid-field border
        online: '#38a169',   // online status dot
        // Software-category accents (deploy panel group headers).
        // Default Tailwind hues (sky-500, pink-500, lime-500...) were far more
        // saturated than anything else in AppEase, so the headers read as
        // borrowed from another app. These are tuned to sit beside primary
        // #2f68dd: same rough lightness, chroma pulled well down, so eleven of
        // them can appear at once and still look like one family. Used as the
        // dot colour at full strength and as the header band at /10.
        cat: {
          browser:  '#3b7dd8',
          ide:      '#7c5cd6',
          media:    '#c2557f',
          office:   '#c98a3c',
          utility:  '#3f9a8a',
          security: '#cf5a52',
          network:  '#5a6fc4',
          design:   '#a45cb8',
          virt:     '#3d93a8',
          runtime:  '#7f9a41',
        },
      },
    },
  },
  plugins: [],
}