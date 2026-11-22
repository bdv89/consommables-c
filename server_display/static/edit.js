/**
 * edit.js - Inventory editing functionality (separate from app.js display logic)
 * Provides modal-based editing with Basic Auth support.
 */

// Closed vocabulary: a STATIC list, not one derived from the DB via api/suggestions.
// A derived list would be self-reinforcing (one typo becomes everyone's suggestion).
const UNITES = ["pièce", "paire", "boîte", "sachet", "paquet", "rouleau", "carton"];

// Editable fields configuration (consumables inventory).
// `quantite` is here for creation only: afterwards the stock moves exclusively through
// the movements API, so the journal and the quantity can never disagree (see
// createOnly handling in buildForm).
const EDITABLE_FIELDS = [
  { key: "code", label: "Code", type: "text", readOnlyOnEdit: true, hint: "auto (CONS-…)" },
  { key: "designation", label: "Désignation", type: "text" },
  { key: "reference", label: "Référence fournisseur", type: "text" },
  { key: "categorie", label: "Catégorie", type: "text", datalist: "categorie" },
  { key: "emplacement", label: "Emplacement", type: "text", datalist: "emplacement" },
  { key: "quantite", label: "Quantité initiale", type: "number", createOnly: true },
  { key: "unite", label: "Unité", type: "text", values: UNITES },
  { key: "contenu_par_unite", label: "Contenu par unité", type: "text", hint: "ex. 100 pour un sachet de 100" },
  { key: "seuil_min", label: "Seuil d'alerte", type: "number", hint: "0 = aucune alerte" },
  { key: "fournisseur", label: "Fournisseur", type: "text", datalist: "fournisseur" },
  { key: "photo", label: "Photo", type: "photo" },
  { key: "remarque", label: "Remarque", type: "text" },
];

// Current row being edited
let currentRowId = null;
let currentVersion = null;

// Cached autocomplete suggestions (distinct values per field), loaded once.
let SUGGESTIONS = {};

async function loadSuggestions() {
  try {
    const res = await fetch("api/suggestions");
    SUGGESTIONS = await res.json();
  } catch (err) {
    console.error("Failed to load suggestions:", err);
  }
}

// Pre-fill the code field with the next free article code (server decides, so two people
// creating at once can't collide silently).
async function fillNextCode(input) {
  try {
    const res = await fetch("api/next-code");
    const data = await res.json();
    if (data.next) input.value = data.next;
  } catch (err) {
    console.error("next-code failed:", err);
  }
}

// Upload a photo and store the returned relative path in the (hidden) field value.
async function uploadPhoto(file, hidden, preview) {
  const body = new FormData();
  body.append("file", file);
  try {
    const res = await fetch("api/upload-photo", { method: "POST", body });
    const data = await res.json();
    if (!data.success) {
      showStatus(data.error || "Envoi de la photo impossible", true);
      return;
    }
    hidden.value = data.path;
    preview.src = "media/" + data.path;
    preview.classList.remove("hidden");
  } catch (err) {
    showStatus("Envoi de la photo impossible : " + err.message, true);
  }
}

// Write operations are open (no password required); rate-limited server-side.
async function authFetch(url, options = {}) {
  options.headers = {
    ...options.headers,
    "Content-Type": "application/json",
  };

  const resp = await fetch(url, options);

  if (resp.status === 429) {
    throw new Error("Trop de requetes, reessayez dans un instant");
  }

  return resp;
}

// Modal management
function getModal() {
  return document.getElementById("edit-modal");
}

function showModal() {
  const modal = getModal();
  if (modal) {
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }
}

function hideModal() {
  const modal = getModal();
  if (modal) {
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }
  currentRowId = null;
  currentVersion = null;
}

// Build form fields
function buildForm(row = {}) {
  const form = document.getElementById("edit-form");
  if (!form) return;

  form.innerHTML = "";
  const isCreate = currentRowId === null;

  EDITABLE_FIELDS.forEach((field) => {
    // The initial quantity is only meaningful at creation: on an existing article the
    // stock belongs to the movements journal, and a field here would invite a silent
    // edit that the server refuses anyway.
    if (field.createOnly && !isCreate) return;

    const div = document.createElement("div");
    div.className = "form-group";

    const label = document.createElement("label");
    label.textContent = field.label;
    label.htmlFor = `field-${field.key}`;

    const input = document.createElement("input");
    input.type = field.type === "number" ? "number" : "text";
    input.id = `field-${field.key}`;
    input.name = field.key;
    input.value = row[field.key] ?? "";
    if (field.type === "number") {
      input.step = "1";
      input.min = "0";
    }
    if (field.hint) {
      input.placeholder = field.hint;
    }
    if (field.readOnlyOnEdit && !isCreate) {
      input.readOnly = true;
      input.title = "Le code identifie l'article dans le journal de mouvements — non modifiable";
    }

    div.appendChild(label);

    if (field.type === "photo") {
      // Hidden text field holds the stored path; the file input only feeds it.
      input.type = "hidden";
      div.appendChild(input);

      const preview = document.createElement("img");
      preview.className = "photo-preview";
      preview.alt = "";
      if (input.value) preview.src = "media/" + input.value;
      else preview.classList.add("hidden");
      div.appendChild(preview);

      const file = document.createElement("input");
      file.type = "file";
      file.accept = "image/*";
      file.className = "photo-input";
      file.addEventListener("change", () => {
        if (file.files && file.files[0]) uploadPhoto(file.files[0], input, preview);
      });
      div.appendChild(file);

      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "btn-photo-clear";
      clear.textContent = "Retirer";
      clear.addEventListener("click", () => {
        input.value = "";
        preview.classList.add("hidden");
        file.value = "";
      });
      div.appendChild(clear);
    } else {
      div.appendChild(input);
    }

    if (field.key === "code" && isCreate) fillNextCode(input);

    // Autocomplete via a native <datalist>: either a static `values` list (closed
    // vocabularies) or values derived from existing rows through api/suggestions.
    if (field.datalist || field.values) {
      const listId = `dl-${field.key}`;
      input.setAttribute("list", listId);
      const dl = document.createElement("datalist");
      dl.id = listId;
      (field.values || SUGGESTIONS[field.datalist] || []).forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v;
        dl.appendChild(opt);
      });
      div.appendChild(dl);
    }

    form.appendChild(div);
  });
}

// Get form data
function getFormData() {
  const data = {};
  EDITABLE_FIELDS.forEach((field) => {
    const input = document.getElementById(`field-${field.key}`);
    if (!input) return;   // createOnly field not rendered in edit mode
    data[field.key] = field.type === "number"
      ? (parseInt(input.value, 10) || 0)
      : input.value;
  });
  return data;
}

// Show status message
function showStatus(message, isError = false) {
  const status = document.getElementById("edit-status");
  if (status) {
    status.textContent = message;
    status.className = isError ? "edit-status error" : "edit-status success";
    status.classList.remove("hidden");
    setTimeout(() => status.classList.add("hidden"), 3000);
  }
}

// ---------------------------------------------------------------------------
// Etape 1 : selecteur de modele (creation seulement)
//
// On ne remplit pas le formulaire au fil de la frappe : on choisit un article deja
// inventorie et on le reprend. La donnee vient uniquement de l'inventaire local
// (endpoint api/suggest) : ni reseau externe, ni cache a gerer.
//
// Le selecteur est aussi le garde-fou anti-doublon : dans le modele quantite, deux
// lignes pour le meme article scindent son stock et faussent son seuil. D'ou le bouton
// « Ouvrir cette fiche », qui bascule sur l'article existant au lieu d'en creer un second.
// ---------------------------------------------------------------------------

// Le code identifie l'article dans le journal de mouvements et la quantite appartient au
// journal : les deux repartent de zero, tout le reste est repris du modele.
const NOT_CLONED = ["code", "quantite"];

const DETAIL_FIELDS = [
  { key: "reference", label: "Référence" },
  { key: "categorie", label: "Catégorie" },
  { key: "emplacement", label: "Emplacement" },
  { key: "quantite", label: "Stock actuel" },
  { key: "unite", label: "Unité" },
  { key: "contenu_par_unite", label: "Contenu par unité" },
  { key: "seuil_min", label: "Seuil d'alerte" },
  { key: "fournisseur", label: "Fournisseur" },
  { key: "remarque", label: "Remarque" },
];

let pickerRows = [];
let pickerIndex = -1;
let pickerDebounce = null;
let pickerRequestId = 0;

function setStep(step) {
  const picker = step === "picker";
  document.getElementById("picker-step").classList.toggle("hidden", !picker);
  document.getElementById("form-step").classList.toggle("hidden", picker);
  // Le selecteur a besoin d'un modal plus large que le formulaire.
  getModal().classList.toggle("picker-mode", picker);
}

async function fetchTemplates(q) {
  // Une reponse en retard ne doit jamais ecraser une frappe plus recente.
  const requestId = ++pickerRequestId;
  let rows = [];
  try {
    const resp = await fetch(`api/suggest?q=${encodeURIComponent(q)}`);
    if (resp.ok) {
      rows = (await resp.json()).rows || [];
    }
  } catch (err) {
    console.error("suggest:", err);
  }
  if (requestId !== pickerRequestId) return;
  pickerRows = rows;
  renderPickerList();
}

function renderPickerList() {
  const list = document.getElementById("picker-list");
  const input = document.getElementById("picker-input");
  list.innerHTML = "";

  pickerRows.forEach((row, i) => {
    const li = document.createElement("li");
    li.className = "picker-option";
    li.id = `picker-opt-${i}`;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", "false");

    const name = document.createElement("span");
    name.className = "picker-option-name";
    name.textContent = row.designation || "(sans désignation)";

    const meta = document.createElement("span");
    meta.className = "picker-option-meta";
    const stock = `${row.quantite ?? 0} ${row.unite || ""}`.trim();
    meta.textContent = `${row.code} · ${row.reference || "sans référence"} · ${stock}`;

    li.appendChild(name);
    li.appendChild(meta);
    li.addEventListener("click", () => selectTemplate(i));
    li.addEventListener("dblclick", () => EditModal.take());
    list.appendChild(li);
  });

  const n = pickerRows.length;
  input.setAttribute("aria-expanded", n ? "true" : "false");
  document.getElementById("picker-count").textContent = n
    ? `${n} article${n > 1 ? "s" : ""} existant${n > 1 ? "s" : ""}`
    : "Aucun article ne correspond — créez une fiche vierge";

  selectTemplate(n ? 0 : -1);
}

function selectTemplate(index) {
  pickerIndex = index;
  const input = document.getElementById("picker-input");

  document.querySelectorAll(".picker-option").forEach((li, i) => {
    const active = i === index;
    li.classList.toggle("active", active);
    li.setAttribute("aria-selected", active ? "true" : "false");
    if (active) li.scrollIntoView({ block: "nearest" });
  });

  if (index >= 0) {
    input.setAttribute("aria-activedescendant", `picker-opt-${index}`);
  } else {
    input.removeAttribute("aria-activedescendant");
  }

  document.getElementById("picker-take").disabled = index < 0;
  document.getElementById("picker-open").disabled = index < 0;
  renderPickerDetail(pickerRows[index]);
}

function renderPickerDetail(row) {
  const detail = document.getElementById("picker-detail");
  detail.innerHTML = "";

  if (!row) {
    const empty = document.createElement("p");
    empty.className = "picker-detail-empty";
    empty.textContent = "Sélectionnez un article pour voir sa fiche.";
    detail.appendChild(empty);
    return;
  }

  const title = document.createElement("h3");
  title.textContent = row.designation || "(sans désignation)";
  detail.appendChild(title);

  if (row.photo) {
    const img = document.createElement("img");
    img.className = "picker-detail-photo";
    img.src = "media/" + row.photo;
    img.alt = "";
    detail.appendChild(img);
  }

  const dl = document.createElement("dl");
  DETAIL_FIELDS.forEach((field) => {
    const value = row[field.key];
    // 0 est une valeur porteuse de sens ici (stock nul, alerte desactivee) : seuls le
    // vide et l'absence sont sautes.
    if (value === null || value === undefined || value === "") return;
    const dt = document.createElement("dt");
    dt.textContent = field.label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.appendChild(dt);
    dl.appendChild(dd);
  });
  detail.appendChild(dl);
}

// Navigation clavier : le focus reste dans le champ, la selection se deplace dans la
// listbox (pattern combobox).
function initPicker() {
  const input = document.getElementById("picker-input");
  if (!input) return;

  input.addEventListener("input", () => {
    clearTimeout(pickerDebounce);
    pickerDebounce = setTimeout(() => fetchTemplates(input.value), 200);
  });

  input.addEventListener("keydown", (e) => {
    if (!pickerRows.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectTemplate(Math.min(pickerIndex + 1, pickerRows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selectTemplate(Math.max(pickerIndex - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      EditModal.take();
    }
  });
}

// Public API
window.EditModal = {
  async open(rowid) {
    try {
      const resp = await fetch(`api/inventory/${rowid}`);
      if (!resp.ok) {
        alert("Erreur: ligne non trouvee");
        return;
      }
      const data = await resp.json();
      const row = data.row || {};

      currentRowId = rowid;
      currentVersion = row.version || 1;

      const title = document.getElementById("edit-modal-title");
      if (title) title.textContent = `Modifier : ${row.designation || row.code || ""}`;

      buildForm(row);
      setStep("form");
      showModal();
    } catch (err) {
      alert("Erreur: " + err.message);
    }
  },

  // Creation : on propose d'abord de reprendre un article existant.
  create() {
    currentRowId = null;
    currentVersion = null;

    const title = document.getElementById("edit-modal-title");
    if (title) title.textContent = "Nouvel article — reprendre un article existant";

    const input = document.getElementById("picker-input");
    if (input) input.value = "";
    pickerRows = [];
    renderPickerList();

    setStep("picker");
    showModal();
    if (input) input.focus();
    fetchTemplates("");
  },

  // Sortie de secours : le formulaire vierge d'avant.
  blank() {
    const title = document.getElementById("edit-modal-title");
    if (title) title.textContent = "Nouvel article";

    buildForm({ unite: "pièce", emplacement: "Mezzanine", quantite: 0, seuil_min: 0 });
    setStep("form");
  },

  // Reprise du modele : tout est copie sauf le code et la quantite.
  take() {
    const row = pickerRows[pickerIndex];
    if (!row) return;

    const template = { ...row };
    // Retirer le code plutot que le laisser ecraser par fillNextCode : sinon celui du
    // modele s'affiche puis clignote sous l'ecriture asynchrone du code neuf.
    NOT_CLONED.forEach((key) => delete template[key]);

    const title = document.getElementById("edit-modal-title");
    if (title) title.textContent = `Nouvel article : ${row.designation || ""}`;

    buildForm(template);
    setStep("form");

    const quantity = document.getElementById("field-quantite");
    if (quantity) quantity.focus();
  },

  // Garde-fou anti-doublon : l'article existe deja, on l'ouvre au lieu d'en creer un second.
  openSelected() {
    const row = pickerRows[pickerIndex];
    if (row) this.open(row.rowid);
  },

  close() {
    hideModal();
  },

  async save() {
    const data = getFormData();

    // Add version for conflict detection
    if (currentVersion !== null) {
      data._version = currentVersion;
    }

    try {
      let resp;
      if (currentRowId) {
        // Update existing
        resp = await authFetch(`api/inventory/${currentRowId}`, {
          method: "PUT",
          body: JSON.stringify(data),
        });
      } else {
        // Create new
        resp = await authFetch("api/inventory", {
          method: "POST",
          body: JSON.stringify(data),
        });
      }

      const result = await resp.json();

      if (result.success) {
        hideModal();
        showStatus(currentRowId ? "Modification enregistrée" : "Article créé");
        // Refresh the table
        if (typeof search === "function") {
          search();
        }
      } else {
        showStatus(result.error || "Erreur inconnue", true);
      }
    } catch (err) {
      showStatus(err.message, true);
    }
  },

  async delete() {
    if (!currentRowId) return;

    if (!confirm("Voulez-vous vraiment supprimer cet article ?")) {
      return;
    }

    try {
      const resp = await authFetch(`api/inventory/${currentRowId}`, {
        method: "DELETE",
      });

      const result = await resp.json();

      if (result.success) {
        hideModal();
        showStatus("Article supprimé");
        // Refresh the table
        if (typeof search === "function") {
          search();
        }
      } else {
        showStatus(result.error || "Erreur", true);
      }
    } catch (err) {
      showStatus(err.message, true);
    }
  },
};

// Check if editing is enabled and set up UI
async function initEdit() {
  try {
    const resp = await fetch("api/edit-enabled");
    const data = await resp.json();
    window.EDIT_ENABLED = data.enabled;

    if (data.enabled) {
      // Preload autocomplete suggestions for the edit form.
      loadSuggestions();
      initPicker();

      // Show add button if it exists
      const addBtn = document.getElementById("add-entry-btn");
      if (addBtn) {
        addBtn.classList.remove("hidden");
        addBtn.addEventListener("click", () => EditModal.create());
      }

      // Re-render table to show edit buttons (if app.js has already loaded)
      if (typeof renderHeader === "function") {
        renderHeader();
      }
      if (typeof search === "function") {
        search();
      }
    }
  } catch (err) {
    console.error("Failed to check edit status:", err);
    window.EDIT_ENABLED = false;
  }
}

// Close modal on escape key
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    hideModal();
  }
});

// Close modal when clicking outside
document.addEventListener("click", (e) => {
  const modal = getModal();
  if (modal && e.target === modal) {
    hideModal();
  }
});

// Initialize on load
initEdit();
