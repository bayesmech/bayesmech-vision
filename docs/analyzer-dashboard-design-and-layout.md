# Analyzer Dashboard Design And Layout

This document defines the visual design, layout rules, color system, highlight behavior, and panel composition for a clean analyzer dashboard rewrite. It complements `docs/analyzer-dashboard-clean-rewrite-design.md`, which covers data flow, endpoints, seeking, and worker architecture.

The goal is a dense engineering dashboard: dark, sharp, fast to scan, and built around live video and analysis artifacts rather than marketing-style presentation.

## Design Direction

The dashboard should feel like an instrumentation console for video analysis:

- Dense but readable information layout.
- Square edges and exact alignment.
- Monospaced typography throughout.
- Black-first palette with restrained neon accents.
- Minimal decoration. Data, playback state, and endpoint health are the visual priority.
- Every panel must have a clear fixed role and stable dimensions so seeking, loading, and data updates do not cause layout shifts.
- Visual changes must map to state: connected, disconnected, active tab, selected frame, selected point, active marker, unavailable data, or error.

Avoid:

- Rounded card-heavy marketing layouts.
- Large hero copy.
- Decorative gradients, blobs, or ornaments.
- Viewport-scaled font sizes.
- Nested decorative cards.
- Text overlays that hide critical video or geometry.

## Global Tokens

Use CSS custom properties at the app root so the dashboard can be themed without rewriting component logic.

```css
:root {
  --bg-dark: #000000;
  --bg-card: #0a0a0a;
  --bg-card-hover: #111111;
  --accent: #00ff88;
  --accent-dim: #00cc6a;
  --accent-glow: rgba(0, 255, 136, 0.08);
  --text: #e0e0e0;
  --text-dim: #707070;
  --success: #00ff88;
  --warning: #ffb833;
  --error: #ff3344;
  --border: #1a1a1a;
  --header-bg: #000000;
}
```

### Surface Colors

| Token | Value | Usage |
| --- | --- | --- |
| `--bg-dark` | `#000000` | Page body, fullscreen backing, video gutters. |
| `--header-bg` | `#000000` | Sticky top header. |
| `--bg-card` | `#0a0a0a` | Primary panels, cards, controls bar. |
| `#050505` | `#050505` | Modal/dialog surfaces and nested code/tool details. |
| `#030303` | `#030303` | Canvas backgrounds, textareas, segmented controls. |
| `--bg-card-hover` | `#111111` | Recording row hover, subtle hover surfaces. |
| `--border` | `#1a1a1a` | Panel borders, row dividers, chart wrappers. |

### Text Colors

| Token | Value | Usage |
| --- | --- | --- |
| `--text` | `#e0e0e0` | Primary labels and readable body text. |
| `--text-dim` | `#707070` | Descriptions, empty states, endpoint detail, metadata. |
| `#505050` | `#505050` | Secondary recording metadata. |
| `#001b0f` | `#001b0f` | Text placed on accent-green active backgrounds. |
| `#ffffff` | `#ffffff` | High contrast chart/current markers and live badge text. |

### Status Colors

| Status | Color | Usage |
| --- | --- | --- |
| Success/connected | `#00ff88` | Endpoint OK, connected badge, primary accent, good coverage. |
| Accent dim | `#00cc6a` | Lower-emphasis accent if needed. |
| Warning | `#ffb833` | Caution states and medium coverage. |
| Error/disconnected | `#ff3344` | Disconnected state, failed endpoint, inline errors. |
| Live red | `#ee0033` / `#e03` | Live recording option and live playback badge. |
| Medium coverage | `#e6a817` | 40-79 percent signal coverage. |
| Poor coverage | `#c0392b` | Below 40 percent signal coverage. |

### Data Visualization Colors

Use a limited but varied data palette so overlays remain distinguishable on video:

- IMU chart axes: `#ff4466`, `#00ff88`, `#00d4ff`, `#ffaa00`.
- Motion capture tracks:
  - `[255, 200, 0]`
  - `[50, 255, 50]`
  - `[80, 80, 255]`
  - `[200, 50, 255]`
  - `[0, 220, 255]`
  - `[255, 100, 100]`
  - `[200, 255, 0]`
  - `[255, 0, 200]`
  - `[0, 180, 255]`
  - `[255, 128, 0]`
- Segmentation semantic masks:
  - Person: `rgba(145, 145, 145, 0.59)`.
  - Wooden: `rgba(92, 55, 28, 0.59)`.
  - Black: `rgba(24, 24, 24, 0.65)`.
  - White: `rgba(248, 248, 248, 0.59)`.
  - Red: `rgba(239, 68, 68, 0.57)`.
  - Green: `rgba(34, 197, 94, 0.57)`.
  - Blue: `rgba(59, 130, 246, 0.57)`.
  - Brown: `rgba(150, 91, 42, 0.59)`.
- Segmentation fallback mask sequence:
  - `rgba(255, 99, 132, 0.55)`
  - `rgba(54, 162, 235, 0.55)`
  - `rgba(255, 206, 86, 0.55)`
  - `rgba(75, 192, 192, 0.55)`
  - `rgba(153, 102, 255, 0.55)`
  - `rgba(255, 159, 64, 0.55)`
  - Additional fallback colors may continue the same saturation/value range.
- Geometry point cloud: `rgb(31, 188, 210)`.
- Detected planes:
  - Type 1 fill `rgba(64, 156, 255, 0.25)`, stroke `rgba(64, 156, 255, 0.8)`.
  - Type 2 fill `rgba(255, 200, 64, 0.25)`, stroke `rgba(255, 200, 64, 0.8)`.
  - Type 3 fill `rgba(100, 220, 100, 0.25)`, stroke `rgba(100, 220, 100, 0.8)`.
  - Unknown fill `rgba(200, 200, 200, 0.2)`, stroke `rgba(200, 200, 200, 0.6)`.
- SLAM and localization:
  - Canvas background `#030303`.
  - Canvas grid `#161616`.
  - Trajectory line `#ffffff`.
  - Current point `#ffd400`.
  - Road mask overlay `rgba(47, 136, 255, 0.37)` in image space and `rgba(47, 136, 255, 0.58)` on ground projection.
  - Road left edge `#ff5bd5`.
  - Road right edge `#46d884`.
  - Road midline `rgba(255, 255, 255, 0.75)`.
  - SIFT road correspondences `rgba(255, 64, 64, 0.55)` and road points `#ff3838`.
  - SIFT non-road correspondences `rgba(255, 255, 255, 0.22)` and non-road points `#ffffff`.
- GPS:
  - Route line `#3498db`.
  - Current GPS marker `#e74c3c`.
  - Map placeholder background `#1a1a2e`.
- Sport understanding 2D overlay:
  - Table outline `#ffffff`.
  - Net `#ff35ff`.
  - Midline `#ff3030`.
  - Ball fill `#ffd84d`.
  - Ball stroke `#07120b`.
  - Offscreen boundary `#ff3344`.
- Sport understanding 3D:
  - Scene background `#050706`.
  - Ping pong table surface `#145c40`.
  - Snooker table surface `#0f5638`.
  - Snooker cushion `#0a3b29`.
  - Table lines `#f8fbf4`.
  - Floor `#111611`.
  - Legs `#161b18`.
  - Net panel `#2c2f35`.
  - Net top strip `#f2f2f2`.
  - Active bounce marker `#ff5d35`.
  - Corrected bounce marker `#5fd1ff`.
  - Inside-table bounce marker `#ffd84d`.
  - Outside-table bounce marker `#a7acb2`.
  - Snooker balls:
    - White `#f5f0dc`.
    - Yellow `#f3d23b`.
    - Green `#2fa84f`.
    - Brown `#8b5a2b`.
    - Blue `#3388ff`.
    - Pink `#ff8fc5`.
    - Black `#111111`.
    - Red `#d92626`.

## Typography

Use one monospaced stack everywhere:

```css
font-family: 'JetBrains Mono', 'SF Mono', 'Fira Code', 'Roboto Mono', monospace;
```

General type rules:

- Body text color: `--text`.
- Metadata text color: `--text-dim`.
- Letter spacing is `0` for normal paragraphs and values.
- Uppercase labels may use `0.04em` to `0.10em` letter spacing.
- Do not use negative letter spacing.
- Do not scale font sizes with viewport width.
- Use tabular numbers for playback time, frame counters, FPS, and endpoint latency.

Type scale:

| Element | Size | Weight | Transform |
| --- | --- | --- | --- |
| App title | `1.1rem` | 600 | Uppercase, `0.05em`. |
| Section title | `0.85rem` | 600 | Uppercase, `0.10em`. |
| Panel title | `0.8rem` | 500 | Uppercase, `0.06em`. |
| Tab label | `0.86rem` | 600 | Uppercase, `0.04em`. |
| Tab description | `0.75rem` | Regular | Sentence case, line-height `1.5`. |
| Info value | `1.5rem` to `1.75rem` | 700 | No transform. |
| Info label | `0.7rem` to `0.8rem` | Regular | Uppercase, `0.08em`. |
| Button/control label | `0.68rem` to `0.75rem` | 700 | Uppercase, `0.06em` to `0.08em`. |
| Empty state | `0.78rem` to `0.8rem` | Regular | Sentence case. |
| Endpoint detail | `0.72rem` | Regular | No transform. |
| Markdown body | `0.8rem` | Regular | Line-height `1.55`. |
| Code blocks | `0.76rem` | Regular | Preserve whitespace. |

## Layout Shell

### Page

- Body background: `#000000`.
- Body minimum height: `100vh`.
- Horizontal overflow hidden.
- All elements use `box-sizing: border-box`.

### Sticky Header

The header is a persistent control/status bar at the top of the page.

- Position: `sticky`.
- Top: `0`.
- Z-index: `1000`.
- Height is content-driven.
- Padding: `0.75rem 2rem` on desktop, `0.75rem 1rem` on small screens.
- Background: `--header-bg`.
- Bottom border: `1px solid --border`.
- Inner width: max `1600px`, centered.
- Inner layout: flex row, `justify-content: space-between`, `align-items: center`.
- On small screens, stack logo and actions vertically with `1rem` gap.

Logo:

- Logo image height: `32px`.
- Text: uppercase app name, `1.1rem`, `600`, accent green.
- Logo group gap: `1rem`.

Header actions:

- Right side contains `LOAD` and connection status.
- Horizontal gap: `0.5rem`.
- Controls must not wrap over the logo on desktop.

### Main Container

- Max width: `1600px`.
- Centered with `margin: 0 auto`.
- Desktop padding: `1.5rem 2rem`.
- Mobile padding: `1rem`.
- Main content order:
  1. Playback controls.
  2. Top analysis grid.
  3. Dashboard workspace with side tabs and active panel.

## Shared Panel Model

Use square, bordered panels for all repeated analysis surfaces.

Panel:

- Background: `--bg-card`.
- Border: `1px solid --border`.
- Radius: `0`.
- Padding: `1rem`.
- Overflow: hidden unless the panel is explicitly scrollable.
- `min-width: 0` so long labels truncate instead of pushing grid columns.

Panel header:

- Display: flex.
- `justify-content: space-between`.
- `align-items: center`.
- Gap: `1rem` where controls exist.
- Bottom margin: `0.75rem`.

Panel title:

- `0.8rem`, weight `500`, uppercase, letter spacing `0.06em`.
- Color: `--text`.

Viewer frame:

- Background: black.
- Border: `1px solid --border`.
- Aspect ratio: normally `16 / 9`.
- Object fit: `contain`.
- Center placeholder content.
- No rounded corners.
- Canvas or image fills width and height.

Empty viewer state:

- Text color `--text-dim`.
- Font size `0.8rem`.
- Opacity may be `0.5`.
- Centered flex column with `0.5rem` gap.
- Icons, if used, are secondary and should not become the dominant visual.

## Connection Status And Endpoint Health

### Header Status Button

The connected/disconnected indicator is a clickable button, not just a badge.

Base:

- Display: inline flex, centered.
- Gap: `0.5rem`.
- Padding: `0.4rem 0.8rem`.
- Transparent background.
- Border: `1px solid --success`.
- Radius: `0`.
- Text: `0.75rem`, uppercase, `0.08em`.
- Color: `--success`.
- Dot: square `6px x 6px`, background current state color.

Disconnected:

- Border and text color: `--error`.
- Dot color: `--error`.

Hover:

- Connected hover background: `rgba(0, 255, 136, 0.06)`.
- Disconnected hover background: `rgba(255, 51, 68, 0.08)`.

Click behavior:

- Opens the endpoint health dialog.
- The button text remains the high-level aggregate state: `CONNECTED` or `DISCONNECTED`.
- The dialog provides endpoint-level detail.

### Endpoint Health Dialog

The health dialog appears as a right-aligned, dismissible panel over a dark backdrop.

Backdrop:

- Fixed inset: `0`.
- Z-index: `2000`.
- Display: flex.
- Align items: flex-start.
- Justify content: flex-end.
- Padding: `4.5rem 2rem 2rem`.
- Background: `rgba(0, 0, 0, 0.55)`.
- Clicking the backdrop dismisses the dialog.

Dialog:

- Width: `min(760px, calc(100vw - 2rem))`.
- Max height: `min(760px, calc(100vh - 6rem))`.
- Background: `#050505`.
- Border: `1px solid --border`.
- Layout: flex column.
- Overflow: hidden.

Header:

- Padding: `1rem`.
- Border bottom: `1px solid --border`.
- Layout: flex row, `justify-content: space-between`, `align-items: flex-start`.
- Title: `0.9rem`, accent, uppercase, `0.08em`.
- Subtitle: `0.75rem`, `--text-dim`.
- Close button: square `2rem`, transparent, border `--border`, hover border/text accent.

Actions:

- Padding: `0.75rem 1rem`.
- Border bottom: `1px solid --border`.
- Refresh button: uppercase `0.75rem`, padding `0.45rem 0.75rem`.
- Disabled refresh: dim text and default cursor.

Rows:

- Grid columns: `5.2rem minmax(0, 1fr)`.
- Gap: `0.75rem`.
- Padding: `0.75rem 0`.
- Row separator: `1px solid --border`.
- State label: uppercase `0.7rem`, `0.06em`.
- State dot: square `0.45rem`.
- OK state color: `--success`.
- Failed state color: `--error`.
- Endpoint name: `0.8rem`, `--text`.
- Kind pill: `0.62rem`, uppercase, dim text, border `--border`, padding `0.1rem 0.3rem`.
- URL/detail: `0.72rem`, dim, line-height `1.35`, `overflow-wrap: anywhere`.

## Recording Load Modal

The `LOAD` control opens the recordings modal.

Load button:

- Transparent background.
- Border and text: accent green.
- Font size `0.75rem`, weight `600`.
- Padding: `0.4rem 0.8rem`.
- Letter spacing: `0.08em`.
- Right margin: `0.5rem`.

Backdrop:

- Fixed inset.
- Background: `rgba(0, 0, 0, 0.85)`.
- Z-index: `9999`.
- Centered flex layout.
- Escape key and backdrop click dismiss the modal.

Modal:

- Width: `100%`.
- Max width: `560px`.
- Max height: `70vh`.
- Background: `#0a0a0a`.
- Border: `1px solid #1a1a1a`.
- Layout: flex column.
- Overflow: hidden.

Modal header:

- Padding: `1rem 1.25rem`.
- Bottom border: `1px solid #1a1a1a`.
- Title: `0.8rem`, weight `600`, accent, uppercase, `0.1em`.
- Close button: transparent, no border, dim text, `1rem`, padding `0.25rem`.

Rows:

- Padding: `0.75rem 1.25rem`.
- Row border: `1px solid #111` or `#222`.
- Cursor pointer.
- Hover background: `#111`.
- Live row active background: `rgba(238, 0, 51, 0.08)`.
- Live row active hover: `rgba(238, 0, 51, 0.12)`.
- Live dot: `8px` circle, red when active, `#555` when inactive.
- Active live dot shadow: `0 0 6px rgba(238, 0, 51, 0.6)`.
- Recording title: `0.82rem`, text, ellipsis.
- Recording metadata: `0.68rem`, `#505050`.
- Analysis badges: `0.58rem`, weight `600`, border in badge color, padding `0.15rem 0.4rem`.
- Badge colors:
  - Motioncap `#ffaa00`.
  - Pongtown `#ff35ff`.
  - Segmentation `#00ff88`.
  - IDOSLAM text `#69a8ff`, border `#2f88ff`.

## Playback Controls

Playback controls must be full width above all analysis panels.

Container:

- Display: flex row.
- Align items: center.
- Gap: `0.75rem`.
- Padding: `0.625rem 1rem`.
- Background: `--bg-card`.
- Border: `1px solid --border`.
- Bottom margin: `1.5rem`.

Buttons:

- Transparent background.
- No border.
- Font size: `1.2rem`.
- Padding: `0.25rem 0.4rem`.
- Radius: `0`.
- Line-height: `1`.
- Cursor pointer.
- Use recognizable icons for skip back, play/pause, and skip forward. In a rewrite, prefer icon components over emoji glyphs.

File mode:

- Controls: skip back, play/pause, skip forward, position text, range slider.
- Position text min width: `7rem`.
- Position text: `0.82rem`, opacity `0.7`, centered, tabular numbers.
- FPS text: opacity `0.6`, left margin `0.4rem`.
- Range input flexes to fill remaining width.
- Range accent: `--accent`.
- Range track and thumb should use square styling, not pill styling.

Live mode:

- Controls: `LIVE` badge, play/pause, frame count or `PAUSED`.
- Live badge:
  - Font `0.7rem`, weight `700`, `0.05em`.
  - Padding `0.15rem 0.45rem`.
  - Background red `#e03` when playing, `#555` when paused.
  - Text white.

Highlight behavior:

- While dragging/seeking, pause playback before applying seek.
- The scrubber position is the source of truth for highlighted frame-dependent panels.
- All current-frame visual highlights must update from the same selected frame index.

## Top Analysis Grid

The first content band must show the currently selected RGB stream and the Model Musings conversation.

Desktop:

- CSS variable: `--top-analysis-panel-height: clamp(252px, 38vh, 504px)`.
- Grid columns: `minmax(380px, 1fr) minmax(520px, 2fr)`.
- Grid rows: one fixed row using the panel height variable.
- Gap: `1rem`.
- Bottom margin: `1.5rem`.
- Panels stretch to identical height.
- RGB stream takes the left column.
- Model Musings takes the wider right column.

Tablet and mobile:

- Single column.
- Auto rows using the panel height variable.
- At `max-width: 720px`, panel height becomes `clamp(210px, 39vh, 364px)`.

RGB stream:

- Uses normal stream panel treatment.
- Viewer consumes remaining vertical space inside the fixed-height panel.
- Image/canvas uses `object-fit: contain`.
- Placeholder appears centered when no RGB frame is available.

## Dashboard Workspace

Desktop workspace:

- Grid columns: `minmax(200px, 240px) minmax(0, 1fr)`.
- Gap: `1rem`.
- Align items: start.

Sidebar:

- Sticky at `top: 5.25rem`.
- Contains vertical dashboard tabs.

Active panel:

- Background:
  - `radial-gradient(circle at top right, rgba(0, 255, 136, 0.09), transparent 35%)`
  - over `--bg-card`.
- Border: `1px solid --border`.
- Padding: `1.25rem`.
- `min-width: 0`.

The radial accent is allowed only here as a low-opacity orientation cue. Avoid additional decorative gradients elsewhere.

Tablet:

- At `max-width: 1024px`, workspace becomes one column.
- Sidebar becomes static.
- Tabs become a responsive grid with `repeat(auto-fit, minmax(220px, 1fr))`.

Mobile:

- Panel padding: `1rem`.
- Tabs stay in a responsive grid.
- Hover transform must be disabled on tabs to prevent horizontal jitter.

## Dashboard Tabs

Tabs are the primary navigation between analysis domains.

Tabs:

- Layout: flex row with shortcut square and copy block.
- Width: `100%`.
- Padding: `1rem`.
- Gap: `0.85rem`.
- Background: `rgba(0, 255, 136, 0.08)`.
- Border: `1px solid rgba(0, 255, 136, 0.35)`.
- Text color: `--text`.
- Text align: left.
- Cursor: pointer.
- Transition: border, background, transform over `0.2s`.

Hover:

- Border: accent.
- Background: `rgba(0, 255, 136, 0.12)`.
- Desktop transform: `translateX(2px)`.
- No transform on mobile.

Active:

- Border: accent.
- Background: accent.
- Text: `#001b0f`.
- Inset shadow: `inset 0 0 0 1px rgba(0, 27, 15, 0.2)`.

Shortcut square:

- Size: `2rem x 2rem`.
- Fixed flex basis: `2rem`.
- Border: `1px solid rgba(0, 255, 136, 0.35)`.
- Background: `rgba(0, 255, 136, 0.08)`.
- Text: accent, `0.7rem`, weight `700`, uppercase, `0.08em`.

Active shortcut:

- Border: `rgba(0, 27, 15, 0.3)`.
- Background: `rgba(0, 27, 15, 0.08)`.
- Text: `#001b0f`.

Tab copy:

- Column layout.
- Gap: `0.25rem`.
- Label: `0.86rem`, weight `600`, uppercase, `0.04em`.
- Description: `0.75rem`, line-height `1.5`, dim text.
- Active description: `rgba(0, 27, 15, 0.72)`.

Tabs:

1. Segmentation.
2. Motion Capture.
3. Sport Understanding.
4. Sensor Data.
5. Localization + Mapping.

## Summary Metrics

Summary metrics are small, repeated cards for frame position, FPS, source, device ID, object counts, GPS position, and analysis totals.

Grid:

- `repeat(auto-fit, minmax(150px, 1fr))`.
- Gap: `1rem`.
- Bottom margin: `1.25rem`.

Metric card:

- Background: `--bg-card`.
- Border: `1px solid --border`.
- Radius: `0`.
- Padding: `1rem`.
- Text aligned center.

Value:

- `1.5rem` to `1.75rem`.
- Weight `700`.
- Color accent.
- Bottom margin `0.25rem`.
- Long values must shrink or wrap without overflowing.

Label:

- `0.7rem` to `0.8rem`.
- Dim text.
- Uppercase, `0.08em`.

## Segmentation Panel

Layout:

- Grid columns: `minmax(0, 2fr) minmax(260px, 1fr)`.
- Gap: `1rem`.
- On mobile: single column.

Segmentation viewer:

- Normal stream viewer.
- Shows the segmentation masks only, on black, without drawing the underlying
  RGB video frame.
- Holds last valid segmentation image briefly during live gaps to reduce flicker.
- Background remains black.
- Uses fixed floor matching: the latest annotation at or before the current
  frame. Do not show Exact/Floor/Nearest controls in the UI.

Legend:

- Uses stream panel structure.
- Flex column.
- Scrollable content.
- Empty state: `No objects detected`, dim `0.75rem`.

Legend row:

- Display: flex.
- Align items: flex-start.
- Gap: `0.5rem`.
- Swatch: square `12px x 12px`, margin-top `2px`.
- Swatch fill: mask RGB at `0.7` opacity.
- Swatch border: mask RGB at `1.0` opacity.
- Label: `0.78rem`, line-height `1.35`, normal text, wraps anywhere.

Highlight behavior:

- If an object is selected in a future rewrite, highlight both its legend row and mask contour using the same color with a white or accent outline.
- Keep mask opacity high enough for standalone inspection on black while still
  preserving distinctions between overlapping masks.

## Motion Capture Panel

Layout:

- Grid columns: `minmax(0, 2fr) minmax(260px, 1fr)`.
- Gap: `1rem`.
- On mobile: single column.

Motion viewer:

- Aspect ratio: `16 / 9`.
- Background: black.
- Border: `1px solid --border`.
- Position: relative.
- Overflow hidden.
- Contains:
  - Low-resolution RGB canvas using the current displayed frame.
  - Motion heatmap canvas layer from the current motioncap frame.
  - SVG trajectory overlay.
  - Empty state fallback.

Layers:

- RGB canvas: absolute inset `0`, full width/height, `object-fit: contain`.
- Motioncap RGB/heatmap canvas may be downsampled internally, currently capped
  at `640px` draw width, while still fitting the full viewer bounds.
- Heatmap overlay opacity: `0.55`.
- Heatmap colorization uses a jet ramp with low motion values transparent.
- SVG: absolute inset `0`, full width/height, pointer-events none, overflow visible.
- SVG viewBox must match the current RGB frame dimensions so track coordinates
  stay inline with video playback.

Mode control:

- Two columns: RAFT, Segmentation.
- Width: `min(100%, 300px)`.
- Border: `1px solid rgba(0, 255, 136, 0.35)`.
- Background: `#030303`.
- Buttons:
  - Min height `2rem`.
  - Padding `0 0.75rem`.
  - Border right `1px solid rgba(0, 255, 136, 0.22)` except last.
  - Font `0.68rem`, weight `700`, uppercase, `0.06em`.
  - Inactive color dim.
  - Hover: text, background `rgba(255, 255, 255, 0.04)`.
  - Active: accent background, text `#001b0f`.

Trajectory overlay:

- Tail length: 30 frames.
- Tail line alpha uses a nonlinear fade so recent points are strongest.
- RAFT track current marker:
  - Circle centered at current position.
  - Radius `6`.
  - Stroke width `2`.
  - Fill none.
- RAFT interpolated marker:
  - Crosshair lines `10px` wide/tall.
  - Stroke width `1`.
- Segmentation current marker:
  - Diamond square: `10px x 10px`, rotated 45 degrees.
  - Stroke width `2`.
  - Fill none.
- Segmentation interpolated marker:
  - X marker with two diagonal lines, `10px` bounds.
- Text label:
  - Prefix `T` for RAFT, `S` for segmentation.
  - Offset from current point by roughly `8px`.
  - Font: `13px Arial, sans-serif`.
  - Fill: track color at `0.95` alpha.

Legend:

- Panel title: `RAFT Tracks` or `Segmentation Tracks`.
- List column gap: `0.5rem`.
- Row grid columns: `2rem minmax(0, 1fr) auto`.
- Row gap: `0.75rem`.
- Row padding: `0.5rem 0`.
- Divider: `1px solid rgba(255, 255, 255, 0.05)`.
- Swatch: `1.8rem x 0.25rem`, radius `999px`, box shadow using same color at `0.35`.
- Label: `0.8rem`, uppercase, `0.05em`, ellipsis.
- Meta: `0.72rem`, dim, percentage.

Highlight behavior:

- Current frame marker is the strongest visual.
- Trail opacity increases toward the current frame.
- Interpolated points use cross/X markers so they cannot be confused with observed detections.
- Active mode uses solid accent background.
- Empty and unavailable states must keep panel height stable.

## Sport Understanding Panel

Layout:

- Two equal columns: surface pose estimation and 3D trajectory understanding.
- Gap: `1rem`.
- Align items: start.
- At `max-width: 1024px`, stack into one column.

### Surface Pose Estimation

Panel header:

- Contains title and a three-state segmented control.
- On small screens, stack title and mode control.

Mode control:

- Three columns: Hull Generation, PnP Estimates, Global Pose.
- Width: `min(100%, 520px)`.
- Border: `1px solid rgba(0, 255, 136, 0.35)`.
- Background: `#030303`.
- Buttons:
  - Min height `2.25rem`.
  - Padding `0 0.75rem`.
  - Font `0.7rem`, weight `700`, uppercase, `0.06em`.
  - Inactive dim.
  - Hover background `rgba(255, 255, 255, 0.04)`.
  - Active accent background and dark text.

Viewer:

- Position relative.
- Width `100%`.
- Aspect ratio `16 / 9`.
- Minimum height `360px`.
- Background black.
- Border `1px solid --border`.
- Overflow hidden.
- Image layer uses `object-fit: contain`.
- SVG overlay uses matching viewBox and `preserveAspectRatio="xMidYMid meet"`.

Overlay strokes:

- Vector effect: non-scaling stroke.
- Table outline: white, width `3`.
- Net: magenta `#ff35ff`, width `2`.
- Midline: red `#ff3030`, width `2`.
- Ball: fill `#ffd84d`, stroke `#07120b`, width `2`.
- Ball radius: clamp from confidence, min `4px`, max `10px`.
- Offscreen rectangle: error red, width `6`.

Overlay message:

- Position: bottom center.
- Bottom: `1rem`.
- Transform: `translateX(-50%)`.
- Padding: `0.45rem 0.75rem`.
- Border: `1px solid rgba(255, 255, 255, 0.16)`.
- Background: `rgba(0, 0, 0, 0.72)`.
- Text: dim, `0.72rem`, uppercase, `0.06em`.

Footer:

- Flex row, `justify-content: space-between`.
- Gap: `1rem`.
- Margin top: `0.75rem`.
- Text: dim, `0.72rem`, uppercase, `0.06em`.
- Shows frame number, score/IOU, sport or ball count.

### 3D Trajectory Understanding

Panel:

- Same stream card shell.
- Viewer height: `clamp(360px, 48vh, 640px)`.
- Background: `#050706`.
- Border: `1px solid --border`.
- Cursor: grab; active cursor: grabbing.
- Canvas fills width and height.

Controls:

- Header controls align right on desktop, left on stacked tablet/mobile.
- Gap: `0.75rem`.
- Pose corrections toggle:
  - Min height `2rem`.
  - Padding `0 0.75rem`.
  - Border `1px solid rgba(255, 255, 255, 0.18)`.
  - Background `#050706`.
  - Text dim, `0.72rem`, uppercase, `0.06em`.
  - Active border `rgba(0, 255, 136, 0.52)`.
  - Active background `rgba(0, 255, 136, 0.12)`.
  - Active text `--text`.
  - Focus-visible outline `2px solid --accent`, offset `2px`.
- Status text: dim, `0.72rem`, uppercase, no wrap.

3D scene:

- Camera: perspective, high oblique table view.
- Orbit controls:
  - Damping enabled.
  - Minimum distance `1.5`.
  - Maximum distance based on table length, at least `7`.
  - Max polar angle near `0.48 * PI`.
- Lighting:
  - Ambient white, intensity `0.58`.
  - Key directional white, intensity `1.15`.
  - Fill directional aqua `#7fffd2`, intensity `0.24`.
- Renderer:
  - Antialias enabled.
  - Device pixel ratio capped at `2`.
  - Shadows enabled.
  - sRGB output color space.

Highlight behavior:

- Latest visible bounce is active:
  - Larger ball radius than prior bounces.
  - Higher emissive intensity.
  - Active color `#ff5d35`.
  - Label texture uses accent green background and dark text.
- Corrected bounces use cyan `#5fd1ff`.
- Inside-table bounces use yellow `#ffd84d`.
- Outside-table bounces use gray `#a7acb2`.
- Labels should remain legible but secondary to markers.
- Snooker balls use their physical colors and steady labels; no active bounce color mapping.

## Sensor Data Panel

The sensor tab combines live coverage, IMU charts, geometry panels, GPS, and SLAM path.

Chart grid:

- Desktop: two columns, `repeat(2, minmax(0, 1fr))`.
- Gap: `1rem`.
- Mobile: single column.

Streams grid:

- Use `repeat(auto-fill, minmax(320px, 1fr))`.
- Gap: `1rem`.
- Contains depth map, point cloud, and plane detection.

Path grid:

- `repeat(auto-fit, minmax(320px, 1fr))`.
- Gap: `1rem`.
- Contains SLAM path and GPS route.

### Signal Coverage

Shown for live mode.

Header:

- Section title: `Signal Coverage`.
- Inline window label: `0.75rem`, opacity `0.5`.
- Bottom margin: `0.75rem`.

Panel:

- Background `--bg-card`.
- Border `1px solid --border`.
- Padding `0.75rem 1rem`.

Row:

- Display flex, aligned center.
- Gap `0.75rem`.
- Padding `0.35rem 0`.
- Label width `140px`, `0.8rem`, opacity `0.7`.
- Bar background `rgba(255,255,255,0.06)`.
- Bar height `4px`.
- Value width `80px`, right aligned, `0.85rem`, weight `600`.
- Bar transition: width and background over `0.4s`.

Coverage colors:

- `>= 80%`: accent green.
- `>= 40%`: `#e6a817`.
- `< 40%`: `#c0392b`.
- Text-only rows use accent for the value.

### IMU Charts

Charts:

- Four cards: Accelerometer, Gyroscope, Gravitometer, Magnetometer.
- Each uses line chart in a stream card.
- Padding around canvas: `0.5rem`.
- Axis data colors: `#ff4466`, `#00ff88`, `#00d4ff`, `#ffaa00`.
- Line width: `1.5`.
- Point radius: `0`.
- Tension: `0`.
- Animation disabled.
- Legend: top, box width `12`, font size `10`.
- Y-axis title font size `10`.
- Y-axis tick font size `9`.
- X-axis hidden.
- Current frame line:
  - White `rgba(255,255,255,0.5)`.
  - Width `1`.
  - Dashed `[4, 4]`.

Highlight behavior:

- File mode centers a 600-frame window around current index: 300 frames before and 300 after.
- Live mode shows a rolling 5-minute window.
- Current-line plugin must update without rebuilding the chart instance.

### Geometry Viewers

Point cloud:

- Black viewer background.
- Points use `rgb(31, 188, 210)`.
- Points should be small and sparse enough to reveal structure.

Plane detection:

- Black viewer background.
- Plane fills are translucent.
- Plane strokes are high opacity.
- Type colors use the data visualization colors above.

### SLAM Path

Canvas:

- Fixed logical size `800 x 600`.
- Rendered width `100%`, height auto.
- Background `#1a1a2e`.
- Grid: white at `0.08` alpha every `50px`.
- Origin crosshair: white at `0.2` alpha, dashed `[5, 5]`.
- Path: cyan `rgba(0, 200, 255, alpha)`, line width `2`.
- Path dots: cyan `rgba(0, 200, 255, alpha)`, radius `2`.
- Start marker: green `#00ff88`, radius `6`, label `START` in white `10px monospace`.
- Current marker: white, radius `6`, outer ring white `0.4` alpha radius `10`.

Footer:

- Display flex.
- Gap `1.5rem`.
- Font `0.8rem`.
- Opacity `0.7`.
- Margin top `0.5rem`.
- Monospace.
- Shows current position, point count, scale.

Highlight behavior:

- File mode highlights the point at the current frame index.
- Live mode highlights the latest accumulated point.
- Scale must be computed from all file points once, not recomputed per seek.

### GPS Route

Panel:

- Background `--bg-card`.
- Border `1px solid --border`.
- Overflow hidden.

Header:

- Padding `0.5rem 0.75rem`.
- Bottom border.
- Title font `0.85rem`, weight `600`.
- Coordinate text `0.7rem`, opacity `0.6`.

Map:

- Height `300px`.
- Width `100%`.
- Placeholder background `#1a1a2e`.
- OpenStreetMap tiles.
- Route line `#3498db`, weight `3`, opacity `0.7`.
- Current marker circle: radius `6`, fill/stroke `#e74c3c`, fill opacity `1`, stroke weight `2`.

Footer:

- Grid columns: repeat 3, equal.
- Gap `0.5rem`.
- Padding `0.5rem 0.75rem`.
- Font `0.75rem`.
- Opacity `0.7`.
- Top border.
- Shows altitude, speed, bearing.

## Localization And Mapping Panel

Layout:

- All localization rows use two equal columns on desktop:
  `repeat(2, minmax(0, 1fr))`.
- Rows collapse to one column at `max-width: 1024px`.
- Gap: `1rem`.
- Bottom margin: `1rem`.

Canvas logical sizes:

- SLAM map canvases: `640 x 360`.
- RGB-backed localization canvases: `640 x 360`.
- The smaller logical sizes keep two panels visible per row while preserving
  enough pixel density for tracks, road edges, and SIFT overlays.

Canvas style:

- Width `100%`.
- Height auto.
- Display block.
- Background `#030303`.
- Border `1px solid --border`.
- Clickable canvases use crosshair cursor.

Shared canvas background:

- Fill `#030303`.
- Grid `#161616`, width `1`, every `60px`.

SLAM overview:

- Render two separate maps:
  - Pre-optimization SLAM from `frame_poses`.
  - Post-optimization SLAM from `refined_frame_poses`.
- Do not silently substitute raw poses into the post-optimization panel. If
  refined poses are unavailable, show the empty state for that panel.
- Convert each pose `world_pose.position` to a 3D point.
- Compute a 2D PCA projection from the pose cloud:
  - Mean-center all pose positions.
  - Build the 3D covariance matrix.
  - Use power iteration to get the dominant eigenvector.
  - Deflate the first component and use power iteration again for the second
    axis.
  - Project each 3D position onto those two axes.
- Fit the projected 2D trajectory into the canvas with a `38px` margin and
  preserve the fitted trajectory for current-pose drawing.
- Trajectory line: white, width `2.4`.
- Current pose: yellow `#ffd400`, radius `6.5`.
- Empty label: dim `#707070`, `13px monospace`, centered.

SIFT correspondences:

- Draw RGB frame contained in canvas.
- Select the `pair_debug` record nearest to the displayed frame index.
- Overlay source-to-target correspondence lines on the same contained RGB frame,
  using the RGB image scale and offset. Do not render correspondences as a
  split-screen pair unless the artifact explicitly provides paired-frame image
  pixels.
- Downsample correspondences to a maximum visual density of roughly 900 items.
- Road correspondence lines: `rgba(255, 64, 64, 0.55)`.
- Non-road correspondence lines: `rgba(255, 255, 255, 0.22)`.
- Road points: `#ff3838`, radius `2.8`.
- Non-road points: white, radius `2`.

Road mask and edge estimates:

- Draw RGB frame contained in canvas.
- Use the decoded segmentation masks for the current displayed frame. The
  localization tab must load the same floor-matched segmentation window used by
  the segmentation panel.
- Combine masks whose lower-case labels are `road`, `pavement`, or `bike` into
  the road corridor mask.
- Keep the `bike` mask separately when present and use its pixel centroid as the
  row-scanning anchor. If no bike mask exists, use the image center.
- Extract road edges from mask rows:
  - Scan from `height - 24` upward to `16%` of mask height in `6px` steps.
  - Build contiguous road segments per row.
  - Ignore segments narrower than `max(8px, 2% of mask width)`.
  - Require the chosen road width to be at least `max(60px, 16% of mask width)`.
  - Use the outer left/right bounds of the road segments, anchored by bike
    center or image center.
  - Reject sudden left/right jumps greater than `160px`.
  - Skip an edge sample if the bike mask occludes that edge pixel.
  - Add a midline sample when both edges are accepted.
- Road mask overlay: `[47, 136, 255, 95]`.
- Left edge line and points: `#ff5bd5`.
- Right edge line and points: `#46d884`.
- Midline: white at `0.75` alpha.
- Edge line width: `3`.
- Midline width: `2`.
- Edge point radius: `2.5`.
- Selected image point: yellow `#ffd400`, radius `7`.
- Click interaction selects the nearest image/ground road projection point.

Ground plane projection:

- Background and grid as above.
- Use current-frame camera intrinsics retained by the frame decoder.
- Use `plane_width_summary_json.pitch_deg` and
  `plane_width_summary_json.camera_height_m` from IDOSLAM when present,
  defaulting to `18deg` and `1.45m`.
- Construct the image-to-ground projector by scaling intrinsics to the mask
  resolution, applying the camera-to-ground basis transform, and intersecting
  image rays with the ground plane at camera height.
- Sample road mask pixels with a step of
  `max(6px, sqrt(mask_width * mask_height / 3000))`.
- Keep projected road points only when they are plausible for the near field:
  forward distance `0..45m` and lateral distance within `25m`.
- Project accepted left/right road edge samples through the same projector.
- Road ground pixels: blue `rgba(47, 136, 255, 0.58)`, small `2.4px` squares.
- Left edge: magenta `#ff5bd5`, width `2.5`.
- Right edge: green `#46d884`, width `2.5`.
- Selected ground point: yellow `#ffd400`, radius `7`.
- Footer text on canvas: dim `#707070`, `11px monospace`, bottom-left.

Camera attitude:

- Background and grid as above.
- Ground grid: `rgba(47, 136, 255, 0.22)`.
- Center axis: `rgba(255, 255, 255, 0.28)`.
- Camera wireframe: white, width `2`.
- Vertical/forward axis: `#46d884`, width `2`.
- Attitude text: `#f5f5f5`, `15px monospace`, top-left.

Highlight behavior:

- Current pose and selected road point always use yellow `#ffd400`.
- Clickable canvases use crosshair cursor.
- Selecting a point must update both image and ground views when possible.
- Selection resets when the current frame changes.

## Model Musings Panel

The Model Musings panel is a scrollable analysis/chat panel in the top analysis grid.

Panel:

- Uses stream card shell.
- Flex column.
- Fixed top-grid height.
- No nested card styling except tool-call details.

Header:

- Gap `1rem`.
- Align items flex-start.
- Title and recording name in a column.
- Recording name:
  - Dim text.
  - `0.68rem`.
  - Letter spacing `0.04em`.
  - Ellipsis.

Action button and send button:

- Border `1px solid rgba(0, 255, 136, 0.42)`.
- Background `rgba(0, 255, 136, 0.1)`.
- Text accent.
- Font `0.7rem`, weight `700`, uppercase, `0.06em`.
- Min height `2rem`.
- Padding `0 0.75rem`.
- Hover border accent, background `rgba(0, 255, 136, 0.16)`.
- Disabled border `--border`, text dim, background `#050505`, cursor not allowed.

Scroll area:

- Flex `1 1 0`.
- Min height `0`.
- Overflow-y auto.
- Right padding `0.35rem`.

Messages:

- Base message border-left: `2px solid rgba(0, 255, 136, 0.36)`.
- User message border-left: `2px solid rgba(255, 255, 255, 0.32)`.
- Margin bottom `1rem`.
- Padding `0.15rem 0 0.1rem 0.85rem`.
- Role label:
  - Dim text.
  - `0.66rem`.
  - Weight `700`.
  - Uppercase, `0.08em`.
  - Bottom margin `0.4rem`.

Tool call:

- Border `1px solid rgba(255, 255, 255, 0.1)`.
- Background `#050505`.
- Summary row min height `2rem`, padding `0 0.65rem`, gap `0.5rem`.
- Tool label: accent, `0.66rem`, weight `700`, uppercase, `0.08em`.
- Tool name: text, `0.74rem`, wraps anywhere.
- Pre/result sections: top border `rgba(255, 255, 255, 0.08)`, padding `0.75rem`.
- Pre max height: `180px`, scrollable, `0.72rem`, line-height `1.5`.

Input:

- Border top `1px solid --border`.
- Grid columns: `minmax(0, 1fr) auto`.
- Gap `0.75rem`.
- Margin top `0.75rem`.
- Padding top `0.75rem`.
- Textarea:
  - Background `#030303`.
  - Border `1px solid --border`.
  - Color text.
  - Font `0.8rem`.
  - Line-height `1.45`.
  - Min height `3rem`.
  - Padding `0.65rem 0.75rem`.
  - Resize vertical.
  - Focus border `rgba(0, 255, 136, 0.48)`, no default outline.
- On mobile, input grid becomes one column.

Markdown:

- Body `0.8rem`, line-height `1.55`.
- Consecutive block margin top `0.75rem`.
- Headings uppercase, text color, weight `700`, letter spacing `0.03em`.
- Links accent; hover underlines.
- Inline code and inline math:
  - Background `rgba(255, 255, 255, 0.06)`.
  - Border `rgba(255, 255, 255, 0.08)`.
  - Padding `0.05rem 0.25rem`.
- Code blocks:
  - Background `#030303`.
  - Border `1px solid --border`.
  - Font `0.76rem`.
  - Line-height `1.55`.
  - Padding `0.75rem`.
  - Horizontal scroll.
- Math blocks:
  - Background `#030303`.
  - Border `1px solid --border`.
  - Font family Times/Cambria serif.
  - Font size `1.08rem`.
  - Line-height `1.85`.
  - Text aligned center.
- Tables:
  - Wrapper border `1px solid --border`, horizontal scroll.
  - Cells padding `0.55rem 0.65rem`.
  - Header background `rgba(0, 255, 136, 0.08)`.
  - Header text accent, `0.68rem`, uppercase, `0.06em`.
  - Body cell text `0.76rem`.

## Highlight And Interaction Rules

### State Highlights

Use exactly one strong highlight per interaction group:

- Active dashboard tab: solid accent background.
- Active segmented-control option: solid accent background.
- Current playback frame: range thumb position plus all frame-linked markers.
- Current chart frame: dashed white vertical line.
- Current SLAM point: white marker for generic SLAM path, yellow marker in localization views.
- Selected localization point: yellow marker.
- Latest pong bounce: orange/red active marker.
- Connected endpoint: green state.
- Failed endpoint: red state.

### Hover

Hover styling should be subtle and should not change layout:

- Buttons: tint background or border.
- Dashboard tabs: desktop-only horizontal translation by `2px`.
- Recording rows: `#111`.
- Segmented buttons: `rgba(255, 255, 255, 0.04)`.
- Endpoint action buttons: accent border/text.

### Focus

All keyboard-focusable controls need a visible focus indicator.

Default focus target:

- Outline: `2px solid --accent`.
- Outline offset: `2px`.

Controls that already have a strong active treatment still need focus-visible styling when focused by keyboard.

### Selection

Selection must be stable across panels:

- The selected frame index is global.
- All panel-specific selected artifacts derive from that frame.
- Frame changes clear stale per-frame selections where the selected object no longer exists.
- Selection color should not depend on object data. Use yellow `#ffd400` for user selections.

### Loading And Error

Loading:

- Use centered dim text inside the destination panel.
- Keep the panel mounted and sized.
- Do not replace the whole page with a loading surface after initial shell render.

Error:

- Inline errors use `--error`.
- Endpoint dialog errors use bordered error blocks.
- Panel-level unavailable states use dim text unless the underlying endpoint failed.

## Responsive Rules

Breakpoints:

- Desktop: above `1024px`.
- Tablet: `max-width: 1024px`.
- Mobile: `max-width: 720px`.

At `max-width: 1024px`:

- Top analysis grid becomes one column.
- Dashboard workspace becomes one column.
- Sidebar is no longer sticky.
- Tabs become a responsive grid.
- Localization and mapping rows become one column.
- Sport understanding becomes one column.
- Surface pose, motioncap, and 3D table headers stack where needed.
- Segmented controls expand to full width.

At `max-width: 720px`:

- Container padding: `1rem`.
- Header padding: `0.75rem 1rem`.
- Header content stacks vertically.
- Dashboard panel padding: `1rem`.
- Top analysis height: `clamp(210px, 39vh, 364px)`.
- Genspark input row becomes one column.
- Segmentation, motioncap, chart, and path grids become one column.
- Dashboard tab hover transform disabled.

Text wrapping:

- Long recording names, endpoint URLs, object labels, GPS coordinates, and markdown content must wrap or ellipsize.
- Buttons should use `white-space: nowrap` only when their container is guaranteed to fit or can wrap at the group level.

## Scrollbars And Range Controls

Scrollbar:

- Width: `4px`.
- Track: `--bg-dark`.
- Thumb: `--border`.
- Use the same styling for scrollable chat, endpoint list, modal rows, and pre blocks.

Range inputs:

- Accent color: `--accent`.
- Track radius: `0`.
- Thumb radius: `0`.
- The slider must remain operable on touch screens; visual square styling must not reduce hit area.

## Accessibility Requirements

- Use real buttons for status, tabs, segmented controls, playback controls, modal close, refresh, and toggles.
- Use `role="tablist"` and `role="tab"` for dashboard tabs and segmented controls.
- Set `aria-selected` on active tabs.
- Set `aria-controls` and stable IDs for dashboard tab panels.
- Use `aria-pressed` for binary toggles such as pose correction.
- Modals/dialogs need a dismiss button and Escape dismissal.
- Canvas-only panels need text labels or accessible names.
- Video/canvas placeholders must be readable to screen readers where possible.
- Do not encode important state only in color. Pair colored states with labels such as `OK`, `FAILED`, `CONNECTED`, `DISCONNECTED`, `ACTIVE`, or frame counts.

## Rewrite Layout Components

Use these visual components as the baseline primitives:

1. `AppShell`: body, sticky header, centered main container.
2. `HeaderStatusButton`: aggregate connection status and health-dialog trigger.
3. `EndpointHealthDialog`: endpoint table, refresh action, detailed failure text.
4. `PlaybackBar`: live/file controls, time/frame readout, scrubber.
5. `Panel`: square bordered container with optional header actions.
6. `ViewerFrame`: stable black 16:9 frame for image/canvas/SVG overlays.
7. `DashboardTabs`: responsive tab rail/grid.
8. `MetricGrid` and `MetricCard`.
9. `SegmentedControl`: RAFT/Segmentation and Hull/PnP/Global controls.
10. `LegendList`: swatches, labels, metadata percentages.
11. `CanvasPanel`: chart/trajectory/localization canvas with stable sizing.
12. `DataUnavailableState`: shared empty/loading/unavailable presentation.

## Visual QA Checklist

Before shipping the rewrite:

- Header remains sticky and does not overlap modal/dialog content.
- No panel shifts height while seeking through a recording.
- Top analysis grid maintains equal panel heights on desktop.
- Mobile layout has no horizontal scroll.
- Status button opens and closes endpoint health dialog.
- Active tab, active mode, current frame, selected point, and active bounce are visually distinct.
- Empty states preserve layout.
- Long endpoint URLs and recording names do not overflow.
- All canvas/SVG overlays align with their underlying image or frame coordinate
  system at desktop, tablet, and mobile sizes.
- Range slider remains usable on mobile.
- Chart current-frame line updates during seek without chart re-creation.
- Motioncap playback renders a low-resolution RGB stream, motion heatmap, and
  track overlays. Heatmap inflation/colorization is worker-side, throttled, and
  stale heatmap responses are ignored.
- Segmentation masks render without the original RGB frame underneath.
- 3D table viewer is nonblank, framed correctly, and interactive at desktop and mobile sizes.
- Keyboard focus is visible on all interactive elements.
