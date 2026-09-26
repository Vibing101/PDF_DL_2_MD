/**
 * The list of parsed links: grouping by category, selection, filtering, and the
 * per-row status shown while a conversion runs.
 */

const STATES = {
  queued: { label: "queued", className: "skip" },
  working: { label: "downloading…", className: "working" },
  converted: { label: "converted", className: "ok" },
  converted_empty: { label: "no text found", className: "warn" },
  skipped_existing: { label: "already there", className: "skip" },
  download_failed: { label: "download failed", className: "fail" },
  convert_failed: { label: "convert failed", className: "fail" },
  write_failed: { label: "could not save", className: "fail" },
  cancelled: { label: "stopped", className: "skip" },
};

export class LinkList {
  /**
   * @param {HTMLElement} container
   * @param {() => void} onSelectionChange
   */
  constructor(container, onSelectionChange) {
    this.container = container;
    this.onSelectionChange = onSelectionChange;
    this.rows = [];
    this.selected = new Set();
    this.filter = "";
    this.openGroups = new Set();
    this.states = new Map();
    this.rowElements = new Map();
  }

  /** Replace the contents with a fresh parse result. */
  load(links) {
    this.rows = links;
    this.selected = new Set();
    this.states = new Map();
    this.openGroups = new Set();
    this.filter = "";
    this.render();
  }

  get visibleRows() {
    if (!this.filter) return this.rows;
    const needle = this.filter.toLocaleLowerCase();
    return this.rows.filter((row) =>
      [row.title, row.category_path, row.url, row.relative_path, row.document_type]
        .filter(Boolean)
        .some((value) => value.toLocaleLowerCase().includes(needle)),
    );
  }

  get selectedIds() {
    return [...this.selected].sort((a, b) => a - b);
  }

  setFilter(text) {
    this.filter = text.trim();
    this.render();
  }

  selectVisible(selected) {
    for (const row of this.visibleRows) {
      if (selected) this.selected.add(row.id);
      else this.selected.delete(row.id);
    }
    this.render();
    this.onSelectionChange();
  }

  setAllGroupsOpen(open) {
    if (open) for (const group of this.#groups()) this.openGroups.add(group.key);
    else this.openGroups.clear();
    this.render();
  }

  get allGroupsOpen() {
    const groups = this.#groups();
    return groups.length > 0 && groups.every((group) => this.openGroups.has(group.key));
  }

  /** Mark rows sharing this URL with a run status; only selected rows count. */
  setStatus(url, status) {
    for (const row of this.rows) {
      if (row.url !== url || !this.selected.has(row.id)) continue;
      this.states.set(row.id, status);
      this.#paintState(row.id);
    }
  }

  markQueued() {
    for (const id of this.selected) {
      this.states.set(id, "queued");
      this.#paintState(id);
    }
  }

  clearStatuses() {
    this.states.clear();
    this.render();
  }

  // ------------------------------------------------------------------ rendering

  render() {
    const groups = this.#groups();
    this.rowElements.clear();
    this.container.textContent = "";

    if (groups.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = this.rows.length
        ? "Nothing matches that filter."
        : "No PDF links found in this document.";
      this.container.append(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const group of groups) fragment.append(this.#renderGroup(group));
    this.container.append(fragment);
  }

  #groups() {
    const groups = new Map();
    for (const row of this.visibleRows) {
      const key = row.category_path || "(no category)";
      const group = groups.get(key) ?? { key, rows: [] };
      group.rows.push(row);
      groups.set(key, group);
    }
    return [...groups.values()];
  }

  #renderGroup(group) {
    const element = document.createElement("section");
    element.className = "group";
    const open = this.openGroups.has(group.key) || Boolean(this.filter);
    if (open) element.classList.add("open");

    const head = document.createElement("div");
    head.className = "group-head";

    const twisty = document.createElement("span");
    twisty.className = "twisty";
    twisty.textContent = open ? "▼" : "▶";

    const box = document.createElement("input");
    box.type = "checkbox";
    const chosen = group.rows.filter((row) => this.selected.has(row.id)).length;
    box.checked = chosen === group.rows.length;
    box.indeterminate = chosen > 0 && chosen < group.rows.length;
    box.title = "Select everything in this category";
    box.addEventListener("change", () => {
      for (const row of group.rows) {
        if (box.checked) this.selected.add(row.id);
        else this.selected.delete(row.id);
      }
      this.render();
      this.onSelectionChange();
    });

    const title = document.createElement("span");
    title.className = "group-title";
    title.textContent = group.key;

    const count = document.createElement("span");
    count.className = "group-count";
    count.textContent = chosen ? `${chosen} of ${group.rows.length}` : String(group.rows.length);

    const toggle = () => {
      if (this.filter) return; // while filtering, everything stays open
      if (this.openGroups.has(group.key)) this.openGroups.delete(group.key);
      else this.openGroups.add(group.key);
      this.render();
    };
    twisty.addEventListener("click", toggle);
    title.addEventListener("click", toggle);

    head.append(twisty, box, title, count);

    const rows = document.createElement("div");
    rows.className = "rows";
    if (open) {
      for (const row of group.rows) rows.append(this.#renderRow(row));
    }

    element.append(head, rows);
    return element;
  }

  #renderRow(row) {
    const element = document.createElement("div");
    element.className = "row";
    element.dataset.id = String(row.id);

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = this.selected.has(row.id);
    box.addEventListener("change", () => {
      if (box.checked) this.selected.add(row.id);
      else this.selected.delete(row.id);
      this.#refreshGroupHeads();
      this.onSelectionChange();
    });

    const body = document.createElement("div");
    body.className = "row-body";

    const title = document.createElement("div");
    title.className = "row-title";
    const label = document.createElement("span");
    label.textContent = row.title || row.relative_path;
    title.append(label);
    if (row.document_type) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = row.document_type;
      title.append(badge);
    }

    const path = document.createElement("div");
    path.className = "row-path";
    path.textContent = row.relative_path;
    path.title = row.url;

    body.append(title, path);

    const state = document.createElement("span");
    state.className = "state";

    element.append(box, body, state);
    this.rowElements.set(row.id, { element, state });
    this.#paintState(row.id);
    return element;
  }

  #paintState(id) {
    const entry = this.rowElements.get(id);
    if (!entry) return;
    const status = this.states.get(id);
    const info = status ? STATES[status] ?? { label: status, className: "" } : null;
    entry.state.textContent = info ? info.label : "";
    entry.state.className = `state ${info ? info.className : ""}`;
  }

  #refreshGroupHeads() {
    // Cheaper than a full re-render while the user is ticking boxes.
    for (const group of this.container.querySelectorAll(".group")) {
      const ids = [...group.querySelectorAll(".row")].map((row) => Number(row.dataset.id));
      if (ids.length === 0) continue;
      const chosen = ids.filter((id) => this.selected.has(id)).length;
      const box = group.querySelector(".group-head input[type=checkbox]");
      const count = group.querySelector(".group-count");
      if (box) {
        box.checked = chosen === ids.length;
        box.indeterminate = chosen > 0 && chosen < ids.length;
      }
      if (count) count.textContent = chosen ? `${chosen} of ${ids.length}` : String(ids.length);
    }
  }
}
