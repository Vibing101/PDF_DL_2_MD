/**
 * pdf2md desktop — wiring.
 *
 * Loads a document through the bundled Python service, shows the PDF links it
 * found, and converts whichever ones the user ticks.
 */

import { open } from "@tauri-apps/plugin-dialog";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { documentDir, homeDir, join } from "@tauri-apps/api/path";
import { getCurrentWebview } from "@tauri-apps/api/webview";

import { Service, ServiceError } from "./service.js";
import { LinkList } from "./ui.js";

const DOCUMENT_FILTERS = [
  {
    name: "Documents with links",
    extensions: ["md", "markdown", "mdown", "txt", "text", "html", "htm", "docx", "pdf", "xlsx"],
  },
];

const element = (id) => document.getElementById(id);

const ui = {
  welcome: element("welcome"),
  welcomeStatus: element("welcome-status"),
  dropZone: element("drop-zone"),
  loadButton: element("load-button"),
  workspace: element("workspace"),
  documentName: element("document-name"),
  documentSummary: element("document-summary"),
  changeDocument: element("change-document"),
  search: element("search"),
  selectionCount: element("selection-count"),
  selectAll: element("select-all"),
  selectNone: element("select-none"),
  toggleGroups: element("toggle-groups"),
  tree: element("tree"),
  outputDir: element("output-dir"),
  pickOutput: element("pick-output"),
  workers: element("workers"),
  delay: element("delay"),
  keepPdfs: element("keep-pdfs"),
  overwrite: element("overwrite"),
  convert: element("convert"),
  cancel: element("cancel"),
  progress: element("progress"),
  progressBar: element("progress-bar"),
  progressText: element("progress-text"),
  summary: element("summary"),
  summaryTitle: element("summary-title"),
  summaryCounts: element("summary-counts"),
  summaryPath: element("summary-path"),
  summaryFailures: element("summary-failures"),
  failureList: element("failure-list"),
  reveal: element("reveal"),
  backToList: element("back-to-list"),
  banner: element("banner"),
  bannerText: element("banner-text"),
  bannerClose: element("banner-close"),
};

const service = new Service();
const list = new LinkList(ui.tree, refreshSelectionUi);
let running = false;
let lastOutputDir = "";

// --------------------------------------------------------------------- helpers

function showBanner(message) {
  ui.bannerText.textContent = message;
  ui.banner.hidden = false;
}

function describe(error) {
  if (error instanceof ServiceError) return error.message;
  return error?.message ?? String(error);
}

function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function show(step) {
  ui.welcome.hidden = step !== "welcome";
  ui.workspace.hidden = step !== "workspace";
  ui.summary.hidden = step !== "summary";
}

function refreshSelectionUi() {
  const chosen = list.selected.size;
  const total = list.rows.length;
  const shown = list.visibleRows.length;
  const parts = [`${chosen} of ${total} selected`];
  if (shown !== total) parts.push(`${shown} shown`);
  ui.selectionCount.textContent = parts.join(" · ");
  ui.convert.disabled = running || chosen === 0;
  ui.convert.textContent = chosen
    ? `Download & convert ${plural(chosen, "file")}`
    : "Download & convert";
  ui.toggleGroups.textContent = list.allGroupsOpen ? "Collapse all" : "Expand all";
}

// ----------------------------------------------------------------- the steps

async function loadDocument(path) {
  ui.welcomeStatus.classList.remove("error");
  ui.welcomeStatus.textContent = "Reading the document…";
  try {
    const result = await service.request("parse", { path });
    list.load(result.links);
    ui.documentName.textContent = result.document_name;
    ui.documentSummary.textContent = [
      plural(result.count, "PDF link"),
      plural(result.categories.length, "category", "categories"),
    ].join(" · ");
    ui.search.value = "";
    ui.welcomeStatus.textContent = "";
    refreshSelectionUi();
    show("workspace");
    if (result.count === 0) {
      showBanner(
        "No PDF links were found in that document. Links must point at .pdf files, " +
          "or say PDF in their text.",
      );
    }
  } catch (error) {
    ui.welcomeStatus.classList.add("error");
    ui.welcomeStatus.textContent = describe(error);
    show("welcome");
  }
}

async function pickDocument() {
  const path = await open({ multiple: false, directory: false, filters: DOCUMENT_FILTERS });
  if (typeof path === "string") await loadDocument(path);
}

async function pickOutputDir() {
  const path = await open({ directory: true, multiple: false, defaultPath: ui.outputDir.value });
  if (typeof path === "string") ui.outputDir.value = path;
}

async function convert() {
  const ids = list.selectedIds;
  const outputDir = ui.outputDir.value.trim();
  if (ids.length === 0) return;
  if (!outputDir) {
    showBanner("Choose a folder to save the Markdown into first.");
    return;
  }

  running = true;
  lastOutputDir = outputDir;
  list.markQueued();
  ui.convert.disabled = true;
  ui.cancel.hidden = false;
  ui.progress.hidden = false;
  ui.progressBar.style.width = "0%";
  ui.progressText.classList.remove("error");
  ui.progressText.textContent = `Starting on ${plural(ids.length, "file")}…`;

  try {
    const summary = await service.request("convert", {
      ids,
      output_dir: outputDir,
      workers: Math.max(1, Number(ui.workers.value) || 4),
      delay: Math.max(0, Number(ui.delay.value) || 0),
      keep_pdfs: ui.keepPdfs.checked ? await join(outputDir, "_pdfs") : null,
      overwrite: ui.overwrite.checked,
    });
    showSummary(summary);
  } catch (error) {
    ui.progressText.classList.add("error");
    ui.progressText.textContent = describe(error);
    showBanner(describe(error));
  } finally {
    running = false;
    ui.cancel.hidden = true;
    refreshSelectionUi();
  }
}

function showSummary(summary) {
  const counts = summary.counts ?? {};
  const labels = {
    converted: "converted",
    converted_empty: "no text found (scans)",
    skipped_existing: "already there",
    download_failed: "download failed",
    convert_failed: "convert failed",
    write_failed: "could not save",
    cancelled: "stopped",
  };
  ui.summaryTitle.textContent = counts.cancelled ? "Stopped" : "Done";
  ui.summaryCounts.textContent = "";
  const entries = [
    [plural(summary.files_written ?? 0, "Markdown file"), "written"],
    ...Object.entries(counts).map(([status, count]) => [
      String(count),
      labels[status] ?? status.replace(/_/g, " "),
    ]),
  ];
  for (const [value, label] of entries) {
    const item = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = value;
    const span = document.createElement("span");
    span.className = "label";
    span.textContent = ` ${label}`;
    item.append(strong, span);
    ui.summaryCounts.append(item);
  }

  ui.summaryPath.textContent = summary.output_dir ? `Saved in ${summary.output_dir}` : "";

  const failures = summary.failures ?? [];
  ui.summaryFailures.hidden = failures.length === 0;
  ui.failureList.textContent = "";
  for (const failure of failures) {
    const item = document.createElement("li");
    item.textContent = `${failure.title || failure.url} — ${failure.error ?? failure.status}`;
    ui.failureList.append(item);
  }
  show("summary");
}

// ------------------------------------------------------------------- start-up

function wireEvents() {
  ui.loadButton.addEventListener("click", pickDocument);
  ui.changeDocument.addEventListener("click", pickDocument);
  ui.pickOutput.addEventListener("click", pickOutputDir);
  ui.convert.addEventListener("click", convert);
  ui.cancel.addEventListener("click", async () => {
    ui.cancel.disabled = true;
    ui.progressText.textContent = "Stopping after the downloads already under way…";
    try {
      await service.request("cancel");
    } catch (error) {
      showBanner(describe(error));
    } finally {
      ui.cancel.disabled = false;
    }
  });

  ui.search.addEventListener("input", () => {
    list.setFilter(ui.search.value);
    refreshSelectionUi();
  });
  ui.selectAll.addEventListener("click", () => list.selectVisible(true));
  ui.selectNone.addEventListener("click", () => list.selectVisible(false));
  ui.toggleGroups.addEventListener("click", () => {
    list.setAllGroupsOpen(!list.allGroupsOpen);
    refreshSelectionUi();
  });

  ui.backToList.addEventListener("click", () => show("workspace"));
  ui.reveal.addEventListener("click", async () => {
    try {
      await revealItemInDir(lastOutputDir);
    } catch (error) {
      showBanner(`Could not open ${lastOutputDir}: ${describe(error)}`);
    }
  });
  ui.bannerClose.addEventListener("click", () => {
    ui.banner.hidden = true;
  });

  service.on("progress", (step) => {
    list.setStatus(step.url, step.status);
    const percent = step.total ? Math.round((step.done / step.total) * 100) : 0;
    ui.progressBar.style.width = `${percent}%`;
    ui.progressText.textContent = `${step.done} of ${step.total} — ${step.path.split("/").pop()}`;
  });
  service.on("log", (entry) => {
    if (entry.level === "error") showBanner(entry.message);
  });
  service.on("closed", ({ message }) => {
    running = false;
    ui.convert.disabled = true;
    ui.cancel.hidden = true;
    showBanner(`${message} Restart pdf2md to try again.`);
  });
}

async function wireDragAndDrop() {
  try {
    await getCurrentWebview().onDragDropEvent(async (event) => {
      const { type, paths } = event.payload;
      if (type === "over" || type === "enter") ui.dropZone.classList.add("dragging");
      else ui.dropZone.classList.remove("dragging");
      if (type === "drop" && paths?.length && ui.summary.hidden && !running) {
        await loadDocument(paths[0]);
      }
    });
  } catch (error) {
    // Dropping is a convenience; the button always works.
    console.warn("pdf2md: drag and drop is unavailable", error);
  }
}

async function setDefaultOutputDir() {
  for (const base of [documentDir, homeDir]) {
    try {
      ui.outputDir.value = await join(await base(), "pdf2md");
      return;
    } catch {
      // Try the next one; the field stays empty if neither resolves.
    }
  }
}

async function start() {
  wireEvents();
  show("welcome");
  ui.welcomeStatus.textContent = "Starting the converter…";
  ui.loadButton.disabled = true;
  try {
    const ready = await service.start();
    ui.welcomeStatus.textContent = "";
    console.info(`pdf2md service ${ready?.version ?? "?"}, protocol ${ready?.protocol ?? "?"}`);
    ui.loadButton.disabled = false;
  } catch (error) {
    ui.welcomeStatus.classList.add("error");
    ui.welcomeStatus.textContent = `${describe(error)} — see the log for details.`;
    for (const line of service.log.slice(-5)) console.error(line);
    return;
  }
  await Promise.all([setDefaultOutputDir(), wireDragAndDrop()]);
}

window.addEventListener("beforeunload", () => {
  void service.stop();
});

void start();
