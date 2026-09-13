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

function fitWedgeLabel(label, span, rLabel, ringWidth) {
  const chord = 2 * rLabel * Math.sin(Math.min(span, Math.PI) / 2);
  const maxWidth = chord * 1.35;
  if (maxWidth < 10 || ringWidth < 12) {
    return null;
  }
  let best = null;
  for (let fontSize = 11; fontSize >= 6; fontSize -= 0.5) {
    if (measureLabel(label, fontSize) <= maxWidth && fontSize + 2 <= ringWidth) {
      best = { lines: [label], fontSize };
      break;
    }
    const parts = splitLabel(label);
    if (
      parts &&
      fontSize * 2.05 <= ringWidth &&
      measureLabel(parts[0], fontSize) <= maxWidth &&
      measureLabel(parts[1], fontSize) <= maxWidth
    ) {
      best = { lines: parts, fontSize };
      break;
    }
  }
  return best;
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
    if (span >= Math.PI * 2 - 1e-9) {
      const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ring.setAttribute("cx", String(cx));
      ring.setAttribute("cy", String(cy));
      ring.setAttribute("r", String(rMid));
      ring.setAttribute("fill", "none");
      ring.setAttribute("stroke", wedge.color || "#d4a574");
      ring.setAttribute("stroke-width", String(ringWidth));
      svg.appendChild(ring);
    } else if (span > 1e-9) {
      path.setAttribute("d", wedgePath(cx, cy, rOuter, rInner, angle, next));
      path.setAttribute("fill", wedge.color || "#d4a574");
      svg.appendChild(path);
    }
    const label = String(wedge.label || "");
    const fitted = label && span > 1e-9 ? fitWedgeLabel(label, span, rLabel, ringWidth) : null;
    if (fitted) {
      labels.push({ start: angle, end: next, ...fitted });
    }
    angle = next;
  }

  for (const item of labels) {
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
      item.lines.forEach((line, i) => {
        const tspan = document.createElementNS("http://www.w3.org/2000/svg", "tspan");
        tspan.setAttribute("x", String(x));
        tspan.setAttribute("dy", i === 0 ? "-0.55em" : "1.15em");
        tspan.textContent = line;
        text.appendChild(tspan);
      });
    }
    svg.appendChild(text);
  }
  return svg;
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
  for (const wedge of chart.wedges) {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    nameCell.className = "name";
    const nameWrap = document.createElement("span");
    nameWrap.className = "name-cell";
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = wedge.color || "#d4a574";
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
    body.appendChild(row);
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
