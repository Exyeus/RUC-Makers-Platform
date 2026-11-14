# Hackathon UI components

Progressive React islands for the existing Django hackathon pages. They use the
actual shadcn/ui Radix components, adapted to the site's current colors and
server-rendered routes. Django continues to own authorization, form handling,
CSRF, identity selection and navigation visibility.

## Build

Run in WSL Ubuntu from this directory, using its Linux Node/npm:

```sh
npm ci
npm run check
npm run build
```

Node 18 or later is sufficient for the esbuild build. `node_modules/` is local
to this Linux source directory and ignored by Git. The lock file records exact
dependency versions. Generated `ui.js`, `ui-components.css`, and
`hackathon-ui-LICENSE.txt` are written to `../../static/hackathon/` and checked in
so deployment does not require npm or any runtime CDN.

The November 14 build used Windows solely to download npm metadata/tarballs
because this WSL instance's existing network/proxy route was unavailable.
Installation, TypeScript checks and compilation ran in WSL with the downloaded
npm cache; Windows native modules were not reused.

## Template integration

Load `hackathon/ui-components.css` after existing styles and `hackathon/ui.js`
as a module. No Tailwind reset or global CSS is loaded.

An account island is an element with `data-ui-account`, `data-label`, and an
optional `data-subtitle`. Inside it, provide the normal native fallback
`details`/navigation links and optional logout POST form. The enhancement reads
these existing links, renders a shadcn DropdownMenu, and hides the fallback only
after React has mounted successfully. The original form stays in place with
its original action and CSRF token; its `requestSubmit()` handles logout. If
rendering fails, the complete native fallback is restored.

`data-variant="login"` renders a compact text trigger without an avatar, for
the public entrance's alternative identity links. No logout form is required.

A `data-ui-navigation` mount enables the shadcn Sheet for the existing
`#sidebar` and `[data-nav-toggle]` controls. It reads only the server-visible
navigation links. At widths below 1200px, its capture listener supersedes the
legacy drawer's click handler after successful initialization. Desktop sidebar
behavior and native mobile navigation remain intact when JavaScript is absent.
Radix provides modal focus trapping, Escape dismissal and keyboard navigation;
focus returns to the original opener. Failed enhancement restores the legacy
controls' ARIA references.

## Source and license

`upstream/` contains unmodified source downloaded on 2026-11-14 from:

<https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/new-york-v4/ui>

The working Button, Sheet, DropdownMenu, Separator and Badge components in
`src/components/ui/` retain the upstream component structure and Radix behavior.
They add scoped `hu-*` hooks for handwritten CSS instead of introducing
Tailwind into the Django site. Existing Tailwind class strings are retained as
source provenance; the scoped CSS supplies the component styling. The `cn`
utility uses `clsx` because no competing Tailwind utility overrides are applied.

The upstream MIT license is preserved in `LICENSE.shadcn.md` and copied beside
the generated assets. esbuild preserves bundled dependency license comments.
`upstream/SHA256SUMS` records the downloaded component snapshots.

The component styles resolve workbench colors from the existing `--ruc-*`
tokens. Public entry components reuse the existing purple colors and `--text`.
Page backgrounds, artwork, particle canvas, and brand assets are untouched.
Controls have 44px minimum targets, visible focus, safe-area support, and
reduced-motion overrides.
