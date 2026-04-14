/* =============================================
   TableFinder — Search & Booking JS
   ============================================= */

let currentMeta = {};
let pendingBooking = null;

/* ---------- Helpers ---------- */
function fmt12(time24) {
  if (!time24) return "";
  const [h, m] = time24.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const hour   = h % 12 || 12;
  return m === 0 ? `${hour}${suffix}` : `${hour}:${String(m).padStart(2,"0")}${suffix}`;
}

function stars(rating) {
  if (!rating) return "";
  const full  = Math.floor(rating);
  const half  = rating - full >= 0.5 ? 1 : 0;
  const empty = 5 - full - half;
  return "★".repeat(full) + (half ? "½" : "") + "☆".repeat(empty);
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

  // UI: loading state
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

    currentMeta = data.meta || {};
    renderMeta(currentMeta);
    renderResults(data.results || [], currentMeta);

  } catch (err) {
    showError("Couldn't reach the server. Check your connection.");
    console.error(err);
  } finally {
    el("search-btn").disabled = false;
    hide("search-spinner");
    show("search-btn-text");
  }
}

/* ---------- Render meta bar ---------- */
function renderMeta(meta) {
  const parts = [];
  if (meta.date)       parts.push(`<strong>${meta.date}</strong>`);
  if (meta.time_label) parts.push(meta.time_label);
  if (meta.party_size) parts.push(`party of ${meta.party_size}`);
  if (meta.query)      parts.push(`"${meta.query}"`);
  if (meta.neighborhood) parts.push(`in ${meta.neighborhood}`);

  el("meta-text").innerHTML = parts.join(" &nbsp;·&nbsp; ");
  show("meta-bar");
}

/* ---------- Render result cards ---------- */
function renderResults(results, meta) {
  const grid = el("results-grid");
  grid.innerHTML = "";

  if (!results.length) {
    show("empty-state");
    return;
  }

  results.forEach((r, idx) => {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = buildCardHTML(r, idx, meta);
    grid.appendChild(card);
  });

  show("results-section");
}

function buildCardHTML(r, idx, meta) {
  const rankLabel = idx === 0 ? "#1 Pick" : `#${idx + 1}`;
  const ratingHTML = r.rating
    ? `<span class="card-rating"><span class="star">${stars(r.rating)}</span> ${r.rating.toFixed(1)}</span>
       <span class="card-dot">●</span>
       <span class="card-reviews">${(r.review_count || 0).toLocaleString()} reviews</span>`
    : `<span class="card-reviews">No rating yet</span>`;

  const tags = [];
  if (r.cuisine)       tags.push(`<span class="tag">${r.cuisine}</span>`);
  if (r.neighborhood)  tags.push(`<span class="tag tag-neighborhood">${r.neighborhood}</span>`);

  const slotsHTML = r.slots.map(s => `
    <div class="slot-item" onclick="openModal(${JSON.stringify(JSON.stringify(r))}, ${JSON.stringify(JSON.stringify(s))}, ${JSON.stringify(JSON.stringify(meta))})">
      <span class="slot-time">${fmt12(s.time)}</span>
      <span class="slot-type">${s.type || "Table"}</span>
      <span class="slot-arrow">→</span>
    </div>
  `).join("");

  return `
    <div class="card-header">
      <span class="card-rank">${rankLabel}</span>
      <div class="card-name">${r.name}</div>
      <div class="card-meta">
        ${ratingHTML}
        <span class="card-dot">●</span>
        <span class="card-price">${r.price || "$$"}</span>
      </div>
    </div>
    <div class="card-body">
      <div class="card-tags">${tags.join("")}</div>
      <p class="slots-label">Available times</p>
      <div class="slot-list">${slotsHTML}</div>
    </div>
    <div class="card-footer"></div>
  `;
}

/* ---------- Modal ---------- */
function openModal(rJSON, sJSON, metaJSON) {
  const r    = JSON.parse(rJSON);
  const slot = JSON.parse(sJSON);
  const meta = JSON.parse(metaJSON);

  // Reset modal state
  hide("modal-confirm");
  hide("modal-success");
  show("modal-slots");

  el("modal-venue-name").textContent = r.name;
  el("modal-meta").textContent = [
    meta.date,
    `Party of ${meta.party_size || 2}`,
  ].filter(Boolean).join(" · ");

  // Build slot buttons — the one that was clicked, plus any others
  const slotsEl = el("modal-slots");
  slotsEl.innerHTML = r.slots.map(s => `
    <button
      class="modal-slot-btn"
      onclick="selectSlot(
        ${JSON.stringify(JSON.stringify(r))},
        ${JSON.stringify(JSON.stringify(s))},
        ${JSON.stringify(JSON.stringify(meta))}
      )"
    >
      <div>
        <div class="modal-slot-time">${fmt12(s.time)}</div>
        <div class="modal-slot-type">${s.type || "Table"}</div>
      </div>
      <span class="modal-slot-cta">Book →</span>
    </button>
  `).join("");

  show("booking-modal");
  document.body.style.overflow = "hidden";
}

function selectSlot(rJSON, sJSON, metaJSON) {
  const r    = JSON.parse(rJSON);
  const slot = JSON.parse(sJSON);
  const meta = JSON.parse(metaJSON);

  pendingBooking = { r, slot, meta };

  el("confirm-details").innerHTML =
    `${r.name}<br/>${fmt12(slot.time)}${slot.type ? " &nbsp;·&nbsp; " + slot.type : ""}<br/>${meta.date} · Party of ${meta.party_size || 2}`;

  hide("modal-slots");
  show("modal-confirm");
}

function cancelConfirm() {
  pendingBooking = null;
  hide("modal-confirm");
  show("modal-slots");
}

async function confirmBook() {
  if (!pendingBooking) return;
  const { r, slot, meta } = pendingBooking;

  el("confirm-book-btn").disabled = true;
  hide("confirm-btn-text");
  show("confirm-spinner");

  try {
    const res = await fetch("/api/book", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug:       r.slug,
        date:       slot.date,
        time:       slot.time,
        party_size: meta.party_size || 2,
      }),
    });

    const data = await res.json();

    if (!res.ok || data.error) {
      alert("Booking failed: " + (data.error || "Unknown error"));
      return;
    }

    // Success!
    el("success-details").innerHTML =
      `<strong>${data.venue}</strong><br/>${fmt12(data.time)} on ${data.date}`;
    hide("modal-confirm");
    show("modal-success");

  } catch (err) {
    alert("Network error — please try again.");
    console.error(err);
  } finally {
    el("confirm-book-btn").disabled = false;
    hide("confirm-spinner");
    show("confirm-btn-text");
  }
}

/* ---------- Close modal ---------- */
function closeModal(event) {
  if (event.target.id === "booking-modal") closeModalDirect();
}
function closeModalDirect() {
  hide("booking-modal");
  document.body.style.overflow = "";
  pendingBooking = null;
}

/* ---------- Error display ---------- */
function showError(msg) {
  const banner = el("error-banner");
  banner.innerHTML = `<span class="alert-icon">⚠</span> ${msg}`;
  show("error-banner");
  banner.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- Enter key on textarea ---------- */
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
