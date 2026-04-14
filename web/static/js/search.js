/* =============================================
   TableFinder — Search JS (search-only mode)
   ============================================= */

/* ---------- Helpers ---------- */
function fmt12(time24) {
  if (!time24) return "";
  const [h, m] = time24.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const hour   = h % 12 || 12;
  return m === 0 ? `${hour}${suffix}` : `${hour}:${String(m).padStart(2,"0")}${suffix}`;
}

function show(id)  { document.getElementById(id).classList.remove("hidden"); }
function hide(id)  { document.getElementById(id).classList.add("hidden"); }
function el(id)    { return document.getElementById(id); }

/* ---------- Search ---------- */
async function runSearch() {
  const description = el("description").value.trim();
  if (!description) {
    showError("Tell us what you're looking for first.");
    return;
  }

  hide("error-banner");
  hide("results-section");
  hide("empty-state");
  hide("meta-bar");
  el("search-btn").disabled = true;
  hide("search-btn-text");
  show("search-spinner");

  try {
    const res = await fetch("/api/search", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ description }),
    });

    const data = await res.json();

    if (!res.ok || data.error) {
      showError(data.error || "Something went wrong — try again.");
      return;
    }

    renderMeta(data.meta || {});
    renderResults(data.results || []);

  } catch (err) {
    showError("Couldn't reach the server. Check your connection.");
    console.error(err);
  } finally {
    el("search-btn").disabled = false;
    hide("search-spinner");
    show("search-btn-text");
  }
}

/* ---------- Meta bar ---------- */
function renderMeta(meta) {
  const parts = [];
  if (meta.date)         parts.push(`<strong>${meta.date}</strong>`);
  if (meta.time_label)   parts.push(meta.time_label);
  if (meta.party_size)   parts.push(`party of ${meta.party_size}`);
  if (meta.query)        parts.push(`"${meta.query}"`);
  if (meta.neighborhood) parts.push(`in ${meta.neighborhood}`);
  el("meta-text").innerHTML = parts.join(" &nbsp;·&nbsp; ");
  show("meta-bar");
}

/* ---------- Results ---------- */
function renderResults(results) {
  const grid = el("results-grid");
  grid.innerHTML = "";

  if (!results.length) {
    show("empty-state");
    return;
  }

  results.forEach((r, idx) => {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = buildCardHTML(r, idx);
    grid.appendChild(card);
  });

  show("results-section");
}

function buildCardHTML(r, idx) {
  const rankLabel = idx === 0 ? "#1 Pick" : `#${idx + 1}`;

  const ratingHTML = r.rating
    ? `<span class="card-rating">${r.rating.toFixed(1)} ★</span>
       <span class="card-dot">·</span>
       <span class="card-reviews">${(r.review_count || 0).toLocaleString()} reviews</span>`
    : `<span class="card-reviews">No rating yet</span>`;

  const tags = [];
  if (r.cuisine)      tags.push(`<span class="tag">${r.cuisine}</span>`);
  if (r.neighborhood) tags.push(`<span class="tag tag-neighborhood">${r.neighborhood}</span>`);

  const resyUrl = `https://resy.com/cities/ny/${r.slug}`;

  const slotsHTML = r.slots.map(s => `
    <a class="slot-item" href="${resyUrl}" target="_blank" rel="noopener">
      <span class="slot-time">${fmt12(s.time)}</span>
      <span class="slot-type">${s.type || "Table"}</span>
      <span class="slot-arrow">Book on Resy →</span>
    </a>
  `).join("");

  return `
    <div class="card-header">
      <span class="card-rank">${rankLabel}</span>
      <div class="card-name">${r.name}</div>
      <div class="card-meta">
        ${ratingHTML}
        <span class="card-dot">·</span>
        <span class="card-price">${r.price || "$$"}</span>
      </div>
    </div>
    <div class="card-body">
      <div class="card-tags">${tags.join("")}</div>
      <p class="slots-label">Available times</p>
      <div class="slot-list">${slotsHTML}</div>
    </div>
  `;
}

/* ---------- Error ---------- */
function showError(msg) {
  const banner = el("error-banner");
  banner.innerHTML = `<span class="alert-icon">⚠</span> ${msg}`;
  show("error-banner");
}

/* ---------- Enter key ---------- */
document.addEventListener("DOMContentLoaded", () => {
  const ta = el("description");
  if (ta) {
    ta.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        runSearch();
      }
    });
  }
});
