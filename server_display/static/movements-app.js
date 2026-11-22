/**
 * movements-app.js — the stock movement journal (read-only).
 *
 * The journal is append-only server-side: this page never offers to edit or delete a
 * line, because a history you can rewrite answers no question.
 */

const state = { code: "", motif: "", limit: 100, offset: 0 };

const filterCode = document.getElementById("filter-code");
const filterMotif = document.getElementById("filter-motif");
const tbody = document.getElementById("tbody");
const stats = document.getElementById("stats");
const meta = document.getElementById("meta");
const pagerPrev = document.querySelector(".pager-prev");
const pagerNext = document.querySelector(".pager-next");
const pagerInfo = document.querySelector(".pager-info");

const MOTIF_LABELS = {
  import: "import",
  sortie: "sortie",
  reappro: "réappro",
  correction: "correction",
};

function debounce(fn, delay) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}

function formatDate(ts) {
  if (!ts) return "";
  // SQLite datetime('now') is UTC in "YYYY-MM-DD HH:MM:SS"; show it as local time.
  const d = new Date(ts.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("fr-BE", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

let motifsBuilt = false;

function buildMotifOptions(motifs) {
  if (motifsBuilt || !filterMotif) return;
  motifs.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = MOTIF_LABELS[m] || m;
    filterMotif.appendChild(opt);
  });
  motifsBuilt = true;
}

function renderRows(rows) {
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const delta = Number(row.delta || 0);

    const cells = [
      ["timestamp", formatDate(row.timestamp)],
      ["article_code", row.article_code || ""],
      ["designation", row.designation || "(article supprimé)"],
      ["delta", (delta > 0 ? "+" : "") + delta],
      ["quantite_apres", row.quantite_apres ?? ""],
      ["motif", MOTIF_LABELS[row.motif] || row.motif || ""],
      ["user_ip", row.user_ip || "—"],
    ];

    cells.forEach(([col, text]) => {
      const td = document.createElement("td");
      td.dataset.col = col;
      td.textContent = text;
      if (text) td.title = String(text);
      // Sign carried by the "+"/"−" glyph as well as the colour (WCAG 1.4.1).
      if (col === "delta") td.classList.add(delta >= 0 ? "delta-in" : "delta-out");
      tr.appendChild(td);
    });

    // Clicking an article code filters the journal down to that article.
    const codeCell = tr.querySelector('[data-col="article_code"]');
    if (codeCell && row.article_code) {
      codeCell.classList.add("clickable");
      codeCell.title = "Filtrer sur cet article";
      codeCell.onclick = () => {
        filterCode.value = row.article_code;
        state.code = row.article_code;
        state.offset = 0;
        load();
      };
    }

    tbody.appendChild(tr);
  });
}

async function load() {
  const params = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.offset),
  });
  if (state.code) params.set("code", state.code);
  if (state.motif) params.set("motif", state.motif);

  const res = await fetch(`api/movements?${params.toString()}`);
  const data = await res.json();

  buildMotifOptions(data.motifs || []);
  renderRows(data.rows || []);

  const total = data.total ?? 0;
  const returned = data.returned ?? 0;
  const from = total === 0 ? 0 : state.offset + 1;
  stats.textContent = `${from} – ${state.offset + returned} / ${total} mouvements`;

  const filters = [state.code, state.motif].filter(Boolean);
  meta.textContent = filters.length ? `Filtre : ${filters.join(" · ")}` : "";

  const pageCount = Math.max(1, Math.ceil(total / state.limit));
  const page = Math.floor(state.offset / state.limit) + 1;
  pagerInfo.textContent = `Page ${page} / ${pageCount}`;
  pagerPrev.disabled = state.offset <= 0;
  pagerNext.disabled = state.offset + state.limit >= total;
}

filterCode.addEventListener("input", debounce(() => {
  state.code = filterCode.value.trim();
  state.offset = 0;
  load();
}, 200));

filterMotif.addEventListener("change", () => {
  state.motif = filterMotif.value;
  state.offset = 0;
  load();
});

pagerPrev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  load();
});

pagerNext.addEventListener("click", () => {
  state.offset += state.limit;
  load();
});

load();
