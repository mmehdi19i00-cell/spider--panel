# Spider Panel — UI Redesign Prompt (Dribbble-inspired, iOS 26 "Liquid Glass" aesthetic)

> Use this as the design brief for the dashboard, login, and subscription pages.
> Referenced style source: https://dribbble.com (liquid-glass / glassmorphism /
> neumorphic dashboards, iOS 26 "Liquid Glass" language).

## 0. Global language
- Platform: single-page panel (FastAPI serves static/index.html, sub.html, login).
- Mobile-first. Every breakpoint ≥320px must be usable; no horizontal scroll.
- Motion is a feature, not decoration: 60fps, GPU-only transforms (translate/scale/
  blur/opacity), never layout-thrashing properties. Respect `prefers-reduced-motion`.
- One accent = Spider neon red `#ff1744` → purple `#7c4dff` gradient. Ghost text
  "amir" watermark on red backgrounds.

## 1. Entry / Landing (first paint)
- Background: a slow-rotating **Earth** (CSS sphere w/ layered radial gradients +
  subtle cloud noise), full-bleed, behind everything.
- On tap/click anywhere on the Earth: **motion blur** ripple (backdrop-filter blur
  spikes then eases), parallax shift. Pointermove = gentle tilt.
- Floating, side-by-side **liquid-glass circular consoles** (3): Memory, CPU, Traffic.
  Each: frosted disc (backdrop-filter: blur(20px) saturate(160%)), 1.5px inner light
  border, soft drop shadow, a thin neon arc showing live % (SVG stroke-dashoffset).
  Values stream from `/api/server/resources` + `/api/server/status` every 2s.
- Scroll down → top elements **fade + translateY(-24px) + scale(.96)** out; Users
  section fades in (IntersectionObserver, stagger 60ms).

## 2. Users section
- "افزودن کاربر" is a glowing glass pill; tapping opens an **iOS-style sheet**
  (bottom sheet on mobile, centered modal on desktop) with spring (cubic-bezier
  .34,1.56,.64,1) slide-up + backdrop blur. Inputs: username, volume (GB), expiry
  (days), IP-limit, inbound select, protocol.
- Each user row is a glass card. Fully **sharp, legible icons** (stroke 2px, currentColor):
  sub (link), QR, copy, edit, delete, status dot. Icons must read at a glance.
- A per-user **neon gauge** (like a tiny rocket/fuel bar) shows used% of volume +
  remaining time, animated fill. Color shifts green→gold→red as it fills.
- Live: "متصل / آنلاین" chips pulled from `/api/user/{uuid}/connections`.

## 3. Inbounds section
- Distinct glass cards, tappable to expand (accordion) showing protocol/transport/
  security/sni/fp. Earth "amir" watermark faint behind. Add/Edit sheet same iOS style.

## 4. Settings
- "Natural / calm" feel: larger type, airy spacing, segmented toggles, glass panels.
  All panel toggles present (theme, accent, IP-limit default, reality debug, domains).
- Domain Manager UI: active-domain dropdown + add/rename/delete/activate wired to
  `/api/domains`.

## 5. Login
- Full glass, blur, animated gradient orb behind the form, magnetic focus rings on
  inputs, spring button. Wrong password → shake + red ghost.

## 6. Subscription page (sub.html) — iOS 26 "Liquid Glass"
- Header: plan name + neon status.
- **Real usage**: used/total volume (gauge), expiry countdown, **live** connected IPs
  & online sessions from `/api/sub/{uuid}`.
- Per-config cards: protocol icon (VLESS-WS / XHTTP / Reality / gRPC), status dot,
  active connections, copy + QR.
- **Client apps grid**: iOS (Shadowrocket / Streisand / V2Box), Android (v2rayNG /
  Hiddify / NekoBox), Windows (v2rayN / Hiddify / Nekoray) — each with its real app
  icon (SVG/asset), one-tap "copy config" or "open deeplink".
- Background: dark fog ("مه تاریک") with "spider" wordmark barely visible; glass cards
  float; tap = motion-blur pulse.
- Fade-on-scroll like the dashboard.

## 7. Performance budget
- No layout shift; lazy mount offscreen sections; cap concurrent animations;
- throttle resource polling to 2s; debounce resize. Target LCP < 1.5s, INP < 200ms.

## Implementation notes (current code)
- `static/index.html` already has CSS-var theming + ring Live Server + Domain Manager
  wired to `/api/domains`. Extend it to the full brief above.
- `static/sub.html` already shows live connected_ips/online_sessions + configs_live.
  Add the client-apps grid + iOS glass.
- Keep backend contract unchanged (APIs already exist: /api/server/resources,
  /api/server/status, /api/ip-limit, /api/user/{uuid}/connections, /api/domains,
  /api/sub/{uuid}).
