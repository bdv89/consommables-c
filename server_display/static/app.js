const state = {
  q: "",
  sort: "",
  dir: "asc",
  low: false,
  limit: 200,
  offset: 0,
  columns: [],
};

const searchInput = document.getElementById("search");
const thead = document.getElementById("thead");
const tbody = document.getElementById("tbody");
const stats = document.getElementById("stats");
const statsSeuil = document.getElementById("stats-seuil");
const statsRupture = document.getElementById("stats-rupture");
const meta = document.getElementById("meta");
// Two pagers (above and below the table) share these classes and are driven together.
const pagerPrevs = document.querySelectorAll(".pager-prev");
const pagerNexts = document.querySelectorAll(".pager-next");
const pagerInfos = document.querySelectorAll(".pager-info");
const pagerSizes = document.querySelectorAll(".pager-size-select");

function debounce(fn, delay) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

// Default-visible columns first, then toggleable extras.
const viewColumns = [
  "code",
  "photo",
  "designation",
  "reference",
  "categorie",
  "emplacement",
  "quantite",
  "seuil_min",
  "unite",
  "contenu_par_unite",
  "fournisseur",
  "remarque",
];

const columnLabels = {
  code: "Code",
  photo: "Photo",
  designation: "Désignation",
  reference: "Référence",
  categorie: "Catégorie",
  emplacement: "Emplacement",
  quantite: "Quantité",
  seuil_min: "Seuil",
  unite: "Unité",
  contenu_par_unite: "Contenu",
  fournisseur: "Fournisseur",
  remarque: "Remarque",
};

// Every column is toggleable. These start hidden by default; the rest start visible.
const DEFAULT_HIDDEN = new Set(["code", "photo", "unite", "contenu_par_unite", "fournisseur", "remarque"]);

function defaultVisibility() {
  const v = {};
  viewColumns.forEach((c) => { v[c] = !DEFAULT_HIDDEN.has(c); });
  return v;
}

let columnVisibility = defaultVisibility();

function loadColumnVisibility() {
  columnVisibility = defaultVisibility();
  const saved = localStorage.getItem("columnVisibilityConsommables");
  if (saved) {
    try {
      const s = JSON.parse(saved);
      Object.keys(columnVisibility).forEach((k) => {
        if (k in s) columnVisibility[k] = !!s[k];
      });
    } catch (e) {}
  }
}

function saveColumnVisibility() {
  localStorage.setItem("columnVisibilityConsommables", JSON.stringify(columnVisibility));
}

// --- Stock -----------------------------------------------------------------

function stockState(row) {
  const q = Number(row.quantite || 0);
  const seuil = Number(row.seuil_min || 0);
  if (q <= 0) return "rupture";
  if (seuil > 0 && q < seuil) return "bas";
  return "ok";
}

// Every stock change goes through the movements API, never through a plain update:
// the quantity and the journal are written in one transaction server-side.
async function postMovement(body, path) {
  try {
    const res = await fetch(path || "api/movements", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 429) {
      alert("Trop de requêtes, réessayez dans un instant");
      return null;
    }
    const data = await res.json();
    if (!data.success) alert(data.error || "Mouvement refusé");
    return data;
  } catch (err) {
    console.error("movement failed:", err);
    return null;
  }
}

async function moveStock(code, delta) {
  const data = await postMovement({ code, delta });
  if (data && data.success) search();
}

function startQuantityEdit(span, row) {
  const input = document.createElement("input");
  input.type = "number";
  input.min = "0";
  input.className = "qty-input";
  input.value = row.quantite ?? 0;
  span.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (saveIt) => {
    if (done) return;
    done = true;
    const target = parseInt(input.value, 10);
    if (saveIt && !Number.isNaN(target) && target !== Number(row.quantite || 0)) {
      // An absolute recount, journalled as a `correction` delta by the server.
      await postMovement({ code: row.code, quantite: target }, "api/movements/set");
    }
    search();
  };

  input.addEventListener("blur", () => finish(true));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
}

function renderQuantite(row) {
  const wrap = document.createElement("div");
  wrap.className = "qty-cell qty-" + stockState(row);
  const qte = Number(row.quantite || 0);

  const minus = document.createElement("button");
  minus.className = "qty-btn qty-minus";
  minus.textContent = "−";
  minus.disabled = qte <= 0;
  minus.title = "Sortie d'une unité";
  minus.setAttribute("aria-label", `Sortir une unité de ${row.designation || row.code}`);
  minus.onclick = () => moveStock(row.code, -1);

  const value = document.createElement("span");
  value.className = "qty-value";
  value.textContent = qte;
  value.title = "Cliquer pour saisir une quantité (recomptage)";
  value.onclick = () => startQuantityEdit(value, row);

  const plus = document.createElement("button");
  plus.className = "qty-btn qty-plus";
  plus.textContent = "+";
  plus.title = "Réapprovisionnement d'une unité";
  plus.setAttribute("aria-label", `Ajouter une unité de ${row.designation || row.code}`);
  plus.onclick = () => moveStock(row.code, 1);

  wrap.append(minus, value, plus);

  // A text badge, not colour alone: the state must survive a colour-blind reader and a
  // black-and-white print (WCAG 1.4.1).
  const st = stockState(row);
  if (st !== "ok") {
    const badge = document.createElement("span");
    badge.className = "stock-badge stock-" + st;
    badge.textContent = st === "rupture" ? "rupture" : "bas";
    badge.title = st === "rupture"
      ? "Stock épuisé"
      : `Sous le seuil de ${row.seuil_min}`;
    wrap.appendChild(badge);
  }
  return wrap;
}

function renderPhoto(row) {
  const path = (row.photo || "").trim();
  if (!path) {
    const span = document.createElement("span");
    span.className = "photo-empty";
    span.textContent = "—";
    return span;
  }
  const a = document.createElement("a");
  a.href = "media/" + path;
  a.target = "_blank";
  a.rel = "noopener";
  const img = document.createElement("img");
  img.className = "photo-thumb";
  img.src = "media/" + path;
  img.loading = "lazy";
  img.alt = row.designation || row.code || "";
  a.appendChild(img);
  return a;
}

// --- Remark ----------------------------------------------------------------

function startRemarkEdit(td, row) {
  if (td.querySelector("textarea")) return; // already editing
  const original = row.remarque || "";
  td.textContent = "";
  td.classList.remove("empty-hint");
  td.onclick = null;
  td.title = "";

  const ta = document.createElement("textarea");
  ta.className = "remarque-edit";
  ta.value = original;
  td.appendChild(ta);
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);

  let done = false;
  const finish = async (saveIt) => {
    if (done) return;
    done = true;
    const newVal = ta.value;
    if (saveIt && newVal !== original) {
      await saveRemark(row.rowid, newVal, row.version);
    }
    search(); // re-render the table
  };

  ta.addEventListener("blur", () => finish(true));
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ta.blur(); // triggers save
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false); // cancel
    }
  });
}

async function saveRemark(rowid, value, version) {
  try {
    const body = { remarque: value };
    if (version != null) body._version = version;
    const res = await fetch(`api/inventory/${rowid}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 429) {
      alert("Trop de requêtes, réessayez dans un instant");
      return;
    }
    const data = await res.json();
    if (!data.success) alert(data.error || "Erreur d'enregistrement");
  } catch (err) {
    console.error("saveRemark failed:", err);
  }
}

// --- Table -----------------------------------------------------------------

async function fetchColumns() {
  const res = await fetch("api/columns");
  const data = await res.json();
  const available = data.columns || [];

  state.columns = viewColumns.filter(
    (c) => available.includes(c) && (c in columnVisibility ? columnVisibility[c] : true)
  );
  renderHeader();
}

// Columns that carry a control rather than a value: sorting them is meaningless.
const NOT_SORTABLE = new Set(["photo"]);

function renderHeader() {
  const row = document.createElement("tr");

  if (window.EDIT_ENABLED) {
    const th = document.createElement("th");
    th.textContent = "";
    // Largeur pilotee par le CSS (th:not([data-col])) : un style inline la figeait a 80px
    // et rendait la regle CSS inoperante.
    row.appendChild(th);
  }

  state.columns.forEach((col) => {
    const th = document.createElement("th");
    th.textContent = columnLabels[col] || col;
    th.dataset.col = col;
    const sortable = !NOT_SORTABLE.has(col);
    // Sans cette distinction, `cursor: pointer` s'appliquait a TOUS les en-tetes :
    // affordance trompeuse sur les colonnes qui ne se trient pas.
    th.classList.add(sortable ? "sortable" : "not-sortable");
    if (sortable) {
      th.title = `Trier par ${columnLabels[col] || col}`;
      if (state.sort === col) {
        th.classList.add(state.dir === "asc" ? "sort-asc" : "sort-desc");
        th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
      }
      th.addEventListener("click", () => {
        if (state.sort === col) {
          state.dir = state.dir === "asc" ? "desc" : "asc";
        } else {
          state.sort = col;
          state.dir = "asc";
        }
        state.offset = 0;
        renderHeader();
        search();
      });
    }
    row.appendChild(th);
  });
  thead.innerHTML = "";
  thead.appendChild(row);
}

async function search() {
  const params = new URLSearchParams({
    q: state.q,
    sort: state.sort,
    dir: state.dir,
    limit: String(state.limit),
    offset: String(state.offset),
  });
  if (state.low) params.set("low", "1");
  const res = await fetch(`api/search?${params.toString()}`);
  const data = await res.json();
  renderRows(data.rows || []);
  const total = data.total ?? 0;
  const returned = data.returned ?? 0;
  const from = total === 0 ? 0 : state.offset + 1;
  const to = state.offset + returned;
  stats.textContent = `${from} – ${to} / ${total} articles`;
  // These two count the WHOLE inventory, not the current page — that is the point.
  if (statsSeuil) statsSeuil.textContent = `Sous seuil ${data.count_sous_seuil ?? 0}`;
  if (statsRupture) statsRupture.textContent = `Rupture ${data.count_rupture ?? 0}`;
  meta.textContent = state.q ? `Filtre: "${state.q}"` : "";
  renderPagination(total);
}

function renderPagination(total) {
  const pageCount = Math.max(1, Math.ceil(total / state.limit));
  const page = Math.floor(state.offset / state.limit) + 1;
  const atStart = state.offset <= 0;
  const atEnd = state.offset + state.limit >= total;
  pagerInfos.forEach((el) => { el.textContent = `Page ${page} / ${pageCount}`; });
  pagerPrevs.forEach((el) => { el.disabled = atStart; });
  pagerNexts.forEach((el) => { el.disabled = atEnd; });
}

function renderRows(rows) {
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");

    if (window.EDIT_ENABLED) {
      const tdEdit = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "btn-edit";
      btn.textContent = "✎";
      btn.title = "Modifier cet article";
      btn.setAttribute("aria-label", `Modifier ${row.designation || row.code || "cet article"}`);
      btn.onclick = () => EditModal.open(row.rowid);
      tdEdit.appendChild(btn);
      tr.appendChild(tdEdit);
    }

    state.columns.forEach((col) => {
      const td = document.createElement("td");
      // Permet au CSS de cibler une colonne precise (alignement des valeurs numeriques).
      td.dataset.col = col;
      if (col === "quantite") {
        td.appendChild(window.EDIT_ENABLED ? renderQuantite(row) : document.createTextNode(row.quantite ?? 0));
      } else if (col === "photo") {
        td.appendChild(renderPhoto(row));
      } else if (col === "remarque") {
        const text = row.remarque || "";
        td.className = "wrap cell-remarque";
        if (window.EDIT_ENABLED) {
          td.classList.add("editable");
          if (text) {
            td.textContent = text;
            td.title = text;
          } else {
            td.textContent = "✎";
            td.classList.add("empty-hint");
            td.title = "Ajouter une remarque";
          }
          td.onclick = () => startRemarkEdit(td, row);
        } else {
          td.textContent = text;
          if (text) td.title = text;
        }
      } else if (col === "seuil_min") {
        // 0 means "no alerting" — showing a bare 0 reads like a real threshold of zero.
        const seuil = Number(row.seuil_min || 0);
        td.textContent = seuil > 0 ? seuil : "—";
        td.title = seuil > 0 ? `Alerte sous ${seuil}` : "Aucun seuil défini (pas d'alerte)";
      } else {
        // Les colonnes ont une largeur fixe et tronquent (ellipsis) : le title garantit
        // que la valeur complete reste accessible au survol.
        const text = row[col] ?? "";
        td.textContent = text;
        if (text) td.title = String(text);
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

// --- Controls --------------------------------------------------------------

const onInput = debounce((e) => {
  state.q = e.target.value.trim();
  state.offset = 0;
  search();
}, 120);

searchInput.addEventListener("input", onInput);

// « / » place le focus dans la recherche depuis n'importe ou (sauf si on tape deja
// dans un champ). Echap vide la recherche. Deux gestes de moins a la souris.
document.addEventListener("keydown", (e) => {
  const dansUnChamp = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "/" && !dansUnChamp) {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  } else if (e.key === "Escape" && e.target === searchInput && searchInput.value) {
    searchInput.value = "";
    onInput({ target: searchInput });
  }
});

const lowToggle = document.getElementById("low-toggle");
if (lowToggle) {
  lowToggle.addEventListener("click", () => {
    state.low = !state.low;
    state.offset = 0;
    lowToggle.classList.toggle("active", state.low);
    lowToggle.setAttribute("aria-pressed", String(state.low));
    search();
  });
}

// Changing page keeps the scroll position at the pager (bottom of the list), so go back
// up to the search bar. The site header is sticky, so its height is measured live and
// subtracted — otherwise the search bar would land hidden behind it.
function scrollToSearch() {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const behavior = reduce ? "auto" : "smooth";
  const target = document.querySelector(".controls");
  if (!target) {
    window.scrollTo({ top: 0, behavior });
    return;
  }
  const header = document.querySelector(".site-header");
  const headerH = header ? header.getBoundingClientRect().height : 0;
  const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - headerH - 8);
  // Only ever scroll UP. Using the top pager already leaves the search bar in view, and
  // scrolling down to "reach" it would yank the page for no reason.
  if (window.scrollY <= top) return;
  window.scrollTo({ top, behavior });
}

// Pagination controls — the same handler is bound to the top and bottom pagers.
function goToPage(mutateOffset) {
  return () => {
    mutateOffset();
    search();
    scrollToSearch();
  };
}

pagerPrevs.forEach((btn) =>
  btn.addEventListener("click", goToPage(() => {
    state.offset = Math.max(0, state.offset - state.limit);
  }))
);
pagerNexts.forEach((btn) =>
  btn.addEventListener("click", goToPage(() => {
    state.offset += state.limit;
  }))
);
pagerSizes.forEach((sel) =>
  sel.addEventListener("change", goToPage(() => {
    state.limit = parseInt(sel.value, 10) || 200;
    state.offset = 0;
    // Keep both selects showing the same page size.
    pagerSizes.forEach((other) => { other.value = sel.value; });
  }))
);

// Export button handler — exports what is currently filtered, not the whole table.
const exportBtn = document.getElementById("export-btn");
if (exportBtn) {
  exportBtn.addEventListener("click", () => {
    const params = new URLSearchParams({ q: state.q, format: "csv" });
    if (state.low) params.set("low", "1");
    window.location.href = `api/export?${params.toString()}`;
  });
}

// Build one visibility toggle per column (all columns are toggleable).
function buildToggleButtons() {
  const rowEl = document.getElementById("toggle-row");
  if (!rowEl) return;
  rowEl.innerHTML = "";
  viewColumns.forEach((col) => {
    const btn = document.createElement("button");
    btn.className = "toggle-btn";
    btn.dataset.column = col;
    btn.textContent = columnLabels[col] || col;
    btn.classList.toggle("active", !!columnVisibility[col]);
    btn.addEventListener("click", () => {
      columnVisibility[col] = !columnVisibility[col];
      btn.classList.toggle("active", columnVisibility[col]);
      saveColumnVisibility();
      fetchColumns().then(() => search());
    });
    rowEl.appendChild(btn);
  });
}

(async function init() {
  loadColumnVisibility();
  buildToggleButtons();
  await fetchColumns();
  await search();
})();
