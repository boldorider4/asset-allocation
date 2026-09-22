const GALLERY = document.getElementById("gallery");
const STATUS = document.getElementById("status");
const VERSION_LABEL = document.getElementById("version-label");
const VERSION_TEXT = VERSION_LABEL ? VERSION_LABEL.textContent.trim() : "";
const POLL_MS = 2000;
const EQUITY_GROUP_COLOR = "#d4a574";

let lastSignature = "";

function basename(href) {
  try {
    const url = new URL(href, window.location.href);
    return decodeURIComponent(url.pathname.split("/").pop() || "");
  } catch {
    return decodeURIComponent(String(href).split("/").pop() || "");
  }
}

function listRawHrefs(listingText) {
  const matches = [...listingText.matchAll(/href\s*=\s*["']([^"']+)["']/gi)];
  const names = [];
  for (const match of matches) {
    const name = basename(match[1]);
    if (name.toLowerCase().endsWith(".raw") && !name.includes("..")) {
      names.push(name);
    }
  }
  return [...new Set(names)].sort();
}

async function scanRawFiles() {
  const response = await fetch("data/", { cache: "no-store" });
  if (!response.ok) {
    return [];
  }
  const text = await response.text();
  return listRawHrefs(text).map((name) => `data/${encodeURIComponent(name)}`);
}

async function chartFileLastModified(url) {
  const tryFetch = async (method) => {
    const response = await fetch(url, { method, cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    const header = response.headers.get("Last-Modified");
    if (!header) {
      return null;
    }
    const ts = Date.parse(header);
    return Number.isNaN(ts) ? null : ts;
  };
  try {
    const head = await tryFetch("HEAD");
    if (head != null) {
      return head;
    }
  } catch {
    // Some static servers reject HEAD; fall through to GET.
  }
  try {
    return await tryFetch("GET");
  } catch {
    return null;
  }
}

async function readChartsLastModified(urls) {
  const times = await Promise.all(urls.map(chartFileLastModified));
  let latest = null;
  for (const ts of times) {
    if (ts == null) {
      continue;
    }
    if (latest == null || ts > latest) {
      latest = ts;
    }
  }
  return latest == null ? null : new Date(latest);
}

function formatLastUpdated(date) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function renderFooterLastUpdated(date) {
  if (!VERSION_LABEL) {
    return;
  }
  if (!date) {
    VERSION_LABEL.textContent = VERSION_TEXT;
    return;
  }
  VERSION_LABEL.textContent = `${VERSION_TEXT} - Last updated: ${formatLastUpdated(date)}`;
}

async function loadChart(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${url}`);
  }
  const chart = await response.json();
  if (!chart || !Array.isArray(chart.wedges)) {
    throw new Error(`Invalid chart payload: ${url}`);
  }
  return chart;
}

function polar(cx, cy, r, angle) {
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

function wedgePath(cx, cy, rOuter, rInner, start, end) {
  const large = end - start > Math.PI ? 1 : 0;
  const [x0, y0] = polar(cx, cy, rOuter, start);
  const [x1, y1] = polar(cx, cy, rOuter, end);
  const [x2, y2] = polar(cx, cy, rInner, end);
  const [x3, y3] = polar(cx, cy, rInner, start);
  return [
    `M ${x0} ${y0}`,
    `A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1}`,
    `L ${x2} ${y2}`,
    `A ${rInner} ${rInner} 0 ${large} 0 ${x3} ${y3}`,
    "Z",
  ].join(" ");
}

function formatGrouped(n, decimals = 2) {
  const fixed = Number(n).toFixed(decimals);
  const [intPart, frac] = fixed.split(".");
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, "'");
  return frac != null ? `${grouped}.${frac}` : grouped;
}

function groupThousandsInText(text) {
  return String(text).replace(/-?\d[\d']*\.\d+/g, (raw) => {
    const n = Number(raw.replace(/'/g, ""));
    if (Number.isNaN(n)) {
      return raw;
    }
    const frac = raw.split(".").pop().replace(/'/g, "");
    return formatGrouped(n, frac.length);
  });
}

function formatPct(weight, total) {
  if (total <= 0) {
    return "0%";
  }
  return `${((weight / total) * 100).toFixed(1)}%`;
}

function formatSegmentValue(wedge) {
  if (wedge.value == null || Number.isNaN(Number(wedge.value))) {
    return "";
  }
  const formatted = formatGrouped(wedge.value, 2);
  const unit = wedge.unit ? ` ${wedge.unit}` : "";
  return `${formatted}${unit}`;
}

const _measureCtx = document.createElement("canvas").getContext("2d");

function measureLabel(text, fontSize) {
  _measureCtx.font = `600 ${fontSize}px "Segoe UI", system-ui, sans-serif`;
  return _measureCtx.measureText(text).width;
}

function splitLabel(label) {
  const i = label.lastIndexOf(" ");
  if (i <= 0) {
    return null;
  }
  return [label.slice(0, i), label.slice(i + 1)];
}

// Single font size for every wedge label: labels are never shrunk to
// fit. A label that fits at this size is drawn, otherwise it wraps (when
// a neighbor forces a restriction) or is dropped.
const WEDGE_LABEL_FONT_SIZE = 10;

function fitWedgeLabel(label, span, rLabel, ringWidth) {
  const chord = 2 * rLabel * Math.sin(Math.min(span, Math.PI) / 2);
  // Generous cap: labels slightly wider than their chord still get fitted —
  // the overlap pass below confines anything that actually touches a
  // neighbor, so this gate only rejects the hopeless cases. Both labels
  // of a tight pair may wrap; neither is dropped for the other's sake.
  const maxWidth = chord * 1.5;
  const fontSize = WEDGE_LABEL_FONT_SIZE;
  if (maxWidth < 10 || ringWidth < 12) {
    return null;
  }
  if (measureLabel(label, fontSize) <= maxWidth && fontSize + 2 <= ringWidth) {
    return { lines: [label], fontSize };
  }
  const parts = splitLabel(label);
  if (
    parts &&
    fontSize * 2.05 <= ringWidth &&
    measureLabel(parts[0], fontSize) <= maxWidth &&
    measureLabel(parts[1], fontSize) <= maxWidth
  ) {
    return { lines: parts, fontSize };
  }
  return null;
}

const WEDGE_LABEL_MAX_LINES = 4;
const WEDGE_LABEL_LINE_HEIGHT = 1.15; // em between baselines of wrapped lines

function wrapLabel(label, maxWidth, fontSize, maxLines) {
  const words = String(label).split(/\s+/).filter(Boolean);
  if (words.length === 0 || maxLines < 1) {
    return null;
  }
  const lines = [];
  let current = "";
  for (const word of words) {
    const trial = current ? `${current} ${word}` : word;
    if (measureLabel(trial, fontSize) <= maxWidth) {
      current = trial;
      continue;
    }
    if (!current) {
      return null; // single word wider than maxWidth
    }
    lines.push(current);
    if (lines.length >= maxLines) {
      return null; // no line left for `word`
    }
    current = word;
    if (measureLabel(current, fontSize) > maxWidth) {
      return null; // single word wider than maxWidth
    }
  }
  if (current) {
    if (lines.length >= maxLines) {
      return null;
    }
    lines.push(current);
  }
  return lines.length > 0 ? lines : null;
}

function fitWedgeLabelRestricted(label, maxWidth, ringWidth) {
  // Like fitWedgeLabel, but the label boundary width is capped (used when
  // an adjacent wedge label would otherwise touch) and the text may wrap
  // onto up to WEDGE_LABEL_MAX_LINES lines at the constant label font
  // size. Prefers fewer lines.
  const fontSize = WEDGE_LABEL_FONT_SIZE;
  if (maxWidth < 10 || ringWidth < 12) {
    return null;
  }
  for (let n = 1; n <= WEDGE_LABEL_MAX_LINES; n++) {
    if (fontSize * n > ringWidth) {
      continue;
    }
    const lines = wrapLabel(label, maxWidth, fontSize, n);
    if (lines) {
      return { lines, fontSize };
    }
  }
  return null;
}

// Padding per side added to label boxes for collision detection:
// measureText reports tight glyph advances, but the rendered label also
// carries a 2.4px outline stroke plus glyph side bearings, so visually
// touching boxes overlap before their measured boxes do.
const WEDGE_LABEL_PAD_X = 4;
const WEDGE_LABEL_PAD_Y = 1;

function labelBox(item, cx, cy, rLabel) {
  const mid = item.start + (item.end - item.start) / 2;
  const [x, y] = polar(cx, cy, rLabel, mid);
  const width =
    Math.max(...item.lines.map((line) => measureLabel(line, item.fontSize))) +
    2 * WEDGE_LABEL_PAD_X;
  const height =
    item.lines.length * item.fontSize * WEDGE_LABEL_LINE_HEIGHT +
    2 * WEDGE_LABEL_PAD_Y;
  return { x, y, width, height };
}

function boxesOverlap(a, b) {
  // Stringent: any true clearance below the padding above counts as
  // touching, so marginal collisions are resolved instead of missed.
  return (
    Math.abs(a.x - b.x) < (a.width + b.width) / 2 &&
    Math.abs(a.y - b.y) < (a.height + b.height) / 2
  );
}

function restrictedCandidate(item, span, rLabel, ringWidth) {
  // Re-fit one label confined to its own wedge's arc width. Returns the
  // re-fitted item, or null when restriction cannot improve it (unfittable
  // or already identical) — never a deletion marker.
  const arcWidth = rLabel * span * 0.95;
  const refit = fitWedgeLabelRestricted(item.label, arcWidth, ringWidth);
  if (!refit || refit.lines.join("\n") === item.lines.join("\n")) {
    return null;
  }
  return { ...item, ...refit };
}

function resolveLabelOverlaps(labels, cx, cy, rLabel, ringWidth) {
  // Labels arrive in angular (wedge) order. When two adjacent labels would
  // touch, try restricting each side to its own arc width and apply the
  // change that actually resolves the overlap: fewest lines wins, then
  // biggest overflow, then narrower wedge. If neither side can resolve it
  // (e.g. a tall wrapped label next to an already-minimal neighbor), both
  // stay — a marginal touch beats a vanished label. Labels are only ever
  // dropped at initial fit (wedge too small for a single word), never as
  // a side effect of a neighbor. Wrap-only, never any font shrinking.
  if (labels.length < 2) {
    return labels;
  }
  const boxOf = (item) => labelBox(item, cx, cy, rLabel);
  const resolved = labels.slice();
  for (let pass = 0; pass < 4; pass++) {
    let changed = false;
    for (let i = 0; i < resolved.length; i++) {
      const j = (i + 1) % resolved.length;
      if (!resolved[i] || !resolved[j]) {
        continue;
      }
      const boxI = boxOf(resolved[i]);
      const boxJ = boxOf(resolved[j]);
      if (!boxesOverlap(boxI, boxJ)) {
        continue;
      }
      const spanI = resolved[i].end - resolved[i].start;
      const spanJ = resolved[j].end - resolved[j].start;
      const candI = restrictedCandidate(resolved[i], spanI, rLabel, ringWidth);
      const candJ = restrictedCandidate(resolved[j], spanJ, rLabel, ringWidth);
      const okI = candI && !boxesOverlap(boxOf(candI), boxJ);
      const okJ = candJ && !boxesOverlap(boxI, boxOf(candJ));
      let pick = -1;
      if (okI && okJ) {
        if (candI.lines.length !== candJ.lines.length) {
          pick = candI.lines.length < candJ.lines.length ? i : j;
        } else {
          const overI = boxI.width - rLabel * spanI;
          const overJ = boxJ.width - rLabel * spanJ;
          if (overI === overJ) {
            pick = spanI <= spanJ ? i : j;
          } else {
            pick = overI > overJ ? i : j;
          }
        }
      } else if (okI) {
        pick = i;
      } else if (okJ) {
        pick = j;
      }
      if (pick === i) {
        resolved[i] = candI;
        changed = true;
      } else if (pick === j) {
        resolved[j] = candJ;
        changed = true;
      }
      // Else: unresolvable at constant font — keep both as they are.
    }
    if (!changed) {
      break;
    }
  }
  return resolved;
}

function renderDonut(wedges) {
  const total = wedges.reduce((sum, w) => sum + Number(w.weight || 0), 0);
  const size = 320;
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = 152;
  const rInner = 88;
  const ringWidth = rOuter - rInner;
  const rMid = (rOuter + rInner) / 2;
  const rLabel = rInner + ringWidth * 0.72;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("class", "donut");
  svg.setAttribute("role", "img");

  const labels = [];
  let angle = -Math.PI / 2;
  for (const wedge of wedges) {
    const share = total > 0 ? Number(wedge.weight) / total : 0;
    const next = angle + share * Math.PI * 2;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const span = next - angle;
    const label = String(wedge.label || "");
    const outlineEquity = isEquityLabel(label);
    if (span >= Math.PI * 2 - 1e-9) {
      const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ring.setAttribute("cx", String(cx));
      ring.setAttribute("cy", String(cy));
      ring.setAttribute("r", String(rMid));
      ring.setAttribute("fill", "none");
      ring.setAttribute("stroke", wedge.color || "#d4a574");
      ring.setAttribute("stroke-width", String(ringWidth));
      svg.appendChild(ring);
      if (outlineEquity) {
        for (const r of [rInner, rOuter]) {
          const rim = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          rim.setAttribute("cx", String(cx));
          rim.setAttribute("cy", String(cy));
          rim.setAttribute("r", String(r));
          rim.setAttribute("fill", "none");
          rim.setAttribute("stroke", EQUITY_GROUP_COLOR);
          rim.setAttribute("stroke-width", "1.75");
          svg.appendChild(rim);
        }
      }
    } else if (span > 1e-9) {
      path.setAttribute("d", wedgePath(cx, cy, rOuter, rInner, angle, next));
      path.setAttribute("fill", wedge.color || "#d4a574");
      if (outlineEquity) {
        path.setAttribute("stroke", EQUITY_GROUP_COLOR);
        path.setAttribute("stroke-width", "1.75");
        path.setAttribute("stroke-linejoin", "round");
      }
      svg.appendChild(path);
    }
    const fitted = label && span > 1e-9 ? fitWedgeLabel(label, span, rLabel, ringWidth) : null;
    if (fitted) {
      labels.push({ start: angle, end: next, label, ...fitted });
    }
    angle = next;
  }
  const resolved = resolveLabelOverlaps(labels, cx, cy, rLabel, ringWidth);

  for (const item of resolved) {
    if (!item) {
      continue;
    }
    const mid = item.start + (item.end - item.start) / 2;
    const [x, y] = polar(cx, cy, rLabel, mid);
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("class", "wedge-label");
    text.setAttribute("x", String(x));
    text.setAttribute("y", String(y));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "middle");
    text.setAttribute("font-size", String(item.fontSize));
    if (item.lines.length === 1) {
      text.textContent = item.lines[0];
    } else {
      // Center the wrapped block on (x, y): first baseline sits half the
      // block height above, the rest step down one line height at a time.
      const firstDy = (-(item.lines.length - 1) * WEDGE_LABEL_LINE_HEIGHT) / 2;
      item.lines.forEach((line, i) => {
        const tspan = document.createElementNS("http://www.w3.org/2000/svg", "tspan");
        tspan.setAttribute("x", String(x));
        tspan.setAttribute("dy", i === 0 ? `${firstDy}em` : `${WEDGE_LABEL_LINE_HEIGHT}em`);
        tspan.textContent = line;
        text.appendChild(tspan);
      });
    }
    svg.appendChild(text);
  }
  return svg;
}

function isEquityLabel(label) {
  return String(label || "").startsWith("Equity");
}

function equityGroupWedge(wedges) {
  const parts = wedges.filter((w) => isEquityLabel(w.label));
  if (parts.length === 0) {
    return null;
  }
  const weight = parts.reduce((sum, w) => sum + Number(w.weight || 0), 0);
  const grouped = { label: "Equity", weight, grouped: true };
  let valueSum = 0;
  let hasValue = false;
  let unit = "";
  for (const wedge of parts) {
    if (wedge.value == null || Number.isNaN(Number(wedge.value))) {
      continue;
    }
    valueSum += Number(wedge.value);
    hasValue = true;
    if (!unit && wedge.unit) {
      unit = wedge.unit;
    }
  }
  if (hasValue) {
    grouped.value = valueSum;
    if (unit) {
      grouped.unit = unit;
    }
  }
  return grouped;
}

function legendEntries(wedges) {
  const group = equityGroupWedge(wedges);
  if (!group) {
    return wedges;
  }
  const equity = wedges.filter((w) => isEquityLabel(w.label) && w.label !== "Equity");
  const rest = wedges.filter((w) => !isEquityLabel(w.label));
  return [...equity, group, ...rest];
}

function renderLegendRow(wedge, total) {
  const row = document.createElement("tr");
  if (wedge.grouped) {
    row.className = "group-total";
  }

  const nameCell = document.createElement("td");
  nameCell.className = "name";
  const nameWrap = document.createElement("span");
  nameWrap.className = "name-cell";
  const swatch = document.createElement("span");
  swatch.className = wedge.grouped ? "swatch grouped" : "swatch";
  if (!wedge.grouped) {
    swatch.style.background = wedge.color || "#d4a574";
  } else {
    swatch.style.background = EQUITY_GROUP_COLOR;
  }
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = wedge.label;
  nameWrap.append(swatch, label);
  nameCell.appendChild(nameWrap);

  const pct = document.createElement("td");
  pct.className = "pct";
  pct.textContent = formatPct(Number(wedge.weight || 0), total);

  const val = document.createElement("td");
  val.className = "val";
  val.textContent = formatSegmentValue(wedge);

  row.append(nameCell, pct, val);
  return row;
}

function renderCard(chart) {
  const card = document.createElement("article");
  card.className = "card";

  const title = document.createElement("h2");
  title.textContent = groupThousandsInText(chart.name || "Untitled");
  card.appendChild(title);

  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  wrap.appendChild(renderDonut(chart.wedges));
  card.appendChild(wrap);

  const total = chart.wedges.reduce((sum, w) => sum + Number(w.weight || 0), 0);
  const legend = document.createElement("table");
  legend.className = "legend";
  const body = document.createElement("tbody");
  for (const wedge of legendEntries(chart.wedges)) {
    body.appendChild(renderLegendRow(wedge, total));
  }
  legend.appendChild(body);
  card.appendChild(legend);
  if (chart.closing_title) {
    const closing = document.createElement("p");
    closing.className = "closing-title";
    closing.textContent = groupThousandsInText(chart.closing_title);
    card.appendChild(closing);
  }
  return card;
}

function renderEmpty() {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.innerHTML =
    "<strong>No charts yet</strong>Drop <code>*.raw</code> files into <code>data/</code>, or run <code>asalloc</code> / <code>make web-example</code>.";
  GALLERY.appendChild(empty);
}

async function refresh() {
  try {
    const files = await scanRawFiles();
    const lastUpdated = await readChartsLastModified(files);
    renderFooterLastUpdated(lastUpdated);
    const signature = `${files.join("|")}@${lastUpdated ? lastUpdated.getTime() : ""}`;
    if (signature === lastSignature) {
      STATUS.hidden = true;
      return;
    }
    lastSignature = signature;
    const charts = [];
    for (const file of files) {
      try {
        charts.push(await loadChart(file));
      } catch (err) {
        console.warn(err);
      }
    }
    GALLERY.replaceChildren();
    if (charts.length === 0) {
      renderEmpty();
    } else {
      for (const chart of charts) {
        GALLERY.appendChild(renderCard(chart));
      }
    }
    STATUS.hidden = true;
  } catch (err) {
    STATUS.hidden = false;
    STATUS.textContent =
      "Could not scan data/*.raw. Serve the _visualizer folder over HTTP (for example: python -m http.server).";
    console.error(err);
  }
}

refresh();
setInterval(refresh, POLL_MS);
