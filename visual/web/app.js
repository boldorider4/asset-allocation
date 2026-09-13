const GALLERY = document.getElementById("gallery");
const STATUS = document.getElementById("status");
const POLL_MS = 2000;

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
  const formatted = Number(wedge.value).toLocaleString("de-CH", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const unit = wedge.unit ? ` ${wedge.unit}` : "";
  return `${formatted}${unit}`;
}

function renderDonut(wedges) {
  const total = wedges.reduce((sum, w) => sum + Number(w.weight || 0), 0);
  const size = 320;
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = 118;
  const rInner = 68;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("class", "donut");
  svg.setAttribute("role", "img");

  let angle = -Math.PI / 2;
  for (const wedge of wedges) {
    const share = total > 0 ? Number(wedge.weight) / total : 0;
    const next = angle + share * Math.PI * 2;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const span = next - angle;
    if (span >= Math.PI * 2 - 1e-9) {
      const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ring.setAttribute("cx", String(cx));
      ring.setAttribute("cy", String(cy));
      ring.setAttribute("r", String((rOuter + rInner) / 2));
      ring.setAttribute("fill", "none");
      ring.setAttribute("stroke", wedge.color || "#d4a574");
      ring.setAttribute("stroke-width", String(rOuter - rInner));
      svg.appendChild(ring);
    } else if (span > 1e-9) {
      path.setAttribute("d", wedgePath(cx, cy, rOuter, rInner, angle, next));
      path.setAttribute("fill", wedge.color || "#d4a574");
      svg.appendChild(path);
    }
    angle = next;
  }
  return svg;
}

function renderCard(chart) {
  const card = document.createElement("article");
  card.className = "card";

  const title = document.createElement("h2");
  title.textContent = chart.name || "Untitled";
  card.appendChild(title);

  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  wrap.appendChild(renderDonut(chart.wedges));
  card.appendChild(wrap);

  const total = chart.wedges.reduce((sum, w) => sum + Number(w.weight || 0), 0);
  const legend = document.createElement("ul");
  legend.className = "legend";
  for (const wedge of chart.wedges) {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = wedge.color || "#d4a574";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = wedge.label;
    const pct = document.createElement("span");
    pct.className = "pct";
    const share = formatPct(Number(wedge.weight || 0), total);
    const amount = formatSegmentValue(wedge);
    pct.textContent = amount ? `${share} · ${amount}` : share;
    item.append(swatch, label, pct);
    legend.appendChild(item);
  }
  card.appendChild(legend);
  if (chart.closing_title) {
    const closing = document.createElement("p");
    closing.className = "closing-title";
    closing.textContent = chart.closing_title;
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
    const signature = files.join("|");
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
