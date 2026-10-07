// Pending choices stay local. Browsing never changes the practice target.
import { useEffect, useRef, useState } from "preact/hooks";
import { getJSON, pollJob, send } from "./api.js";
import { belongsHere, importSelection, libraryRows, openingTarget, overallSelection,
         raceChoice, resolveShortcut, rowId, selectionOf, uniqueRows } from "./ghostlibrary.js";

const URL = "/api/ghost/library";
export const emptyFilters = () => ({query: "", source: "local", target: null,
  practice: false, sort: "fastest", bests: false});
const initialState = () => ({catalog: null, page: "home", author: "", rememberedAuthor: "", nameReturn: "export", nameBack: "home",
  selected: new Set(), filters: emptyFilters(), view: "browse", reviewBack: "export",
  selectionLabel: "", busy: false, progress: null, error: "", preview: null,
  fileName: "", importMode: "all", includeNoBest: false, receipt: null,
  shortcutName: "", saved: false, picker: false});

function sectionName(opening, t) {
  if (!opening) return "Your practice target";
  if (opening.kind === "segment") return (t?.segments || []).find(row => String(row.id) === opening.entity_key)?.name || "This segment";
  const [courseId, starId] = opening.entity_key.split("-").map(Number);
  const course = t?.view?.catalog?.courses?.find(row => row.id === courseId);
  return course?.stars?.[starId] ? `${course.name} · ${course.stars[starId]}` : "This star";
}

function currentIdentities(selection) {
  const ids = new Set((selection.playing || []).map(rowId));
  for (const id of selection.ghosts?.attempts || []) ids.add(`attempt:${id}`);
  for (const id of selection.ghosts?.imports || []) ids.add(id);
  if (selection.ghosts?.pb !== false && selection.pb?.attempt_id) ids.add(rowId(selection.pb));
  return ids;
}

function libraryData(state, fixed, t) {
  const {catalog, selected, filters} = state;
  const rows = uniqueRows([...(catalog?.ghosts || []), ...(catalog?.bests || [])]);
  const bests = catalog?.bests || [];
  const chosen = rows.filter(row => row.available && selected.has(rowId(row)));
  const visible = libraryRows(filters.bests ? bests : catalog?.ghosts || [], filters, fixed.opening);
  const ids = currentIdentities(fixed.selection);
  const current = rows.filter(row => belongsHere(row, fixed.opening) && ids.has(rowId(row)));
  const openingName = rows.find(row => belongsHere(row, fixed.opening))?.target_name || sectionName(fixed.opening, t);
  return {...fixed, rows, bests, chosen, visible, current, openingName,
    ...selectionData(state, fixed, chosen)};
}

function selectionData(state, fixed, chosen) {
  const {catalog, selected, preview} = state;
  const choice = raceChoice(chosen, fixed.raceTarget, fixed.selection.ghosts?.pb ?? true);
  const raceCount = choice.attempts.length + choice.imports.length;
  const raceFits = raceCount > 0 && raceCount + Number(choice.pb) <= (catalog?.max_race || 0);
  const exportFits = chosen.length > 0 && chosen.length <= (catalog?.max_export || 0);
  const importRows = preview?.ghosts || [];
  const importChosen = importRows.filter(row => selected.has(row.id || row.import_id) && row.importable && !row.duplicate);
  return {choice, raceCount, raceFits, exportFits, importRows, importChosen};
}

function commonActions(state, patch, resources, onClose) {
  const go = page => patch({page, error: "", progress: null});
  const load = async () => {const catalog = await getJSON(URL); patch({catalog}); return catalog;};
  const run = async task => {
    if (resources.running.current) return null;
    resources.setRunning(true); patch({busy: true, error: ""});
    try {return await task();}
    catch (problem) {patch({error: problem.message}); return null;}
    finally {resources.setRunning(false); patch({busy: false});}
  };
  const job = async start => {
    patch({progress: {state: "running", progress: 0, message: "Preparing ghosts…"}});
    const answer = await start();
    return pollJob(`${URL}/jobs/${answer.job_id}`, progress => patch({progress}));
  };
  const discard = async () => {
    const identity = resources.preview.current;
    if (!identity) return;
    try {await send("DELETE", `${URL}/import/preview/${identity}`);}
    catch (problem) {if (problem.status !== 404) throw problem;}
    if (resources.preview.current === identity) resources.clearPreview();
    patch({preview: null});
  };
  // Closing remains possible when an expired lease or offline server prevents
  // cleanup. The server expires abandoned staging; nothing was published.
  const close = () => run(async () => {try {await discard();} finally {onClose();}});
  const filter = delta => patch(before => ({filters: {...before.filters, ...delta}}));
  const toggle = row => patch(before => {
    const selected = new Set(before.selected), identity = rowId(row);
    if (selected.has(identity)) selected.delete(identity); else selected.add(identity);
    return {selected, ...(before.page === "import-review" ? {importMode: "custom"} : {})};
  });
  return {go, load, run, job, discard, close, filter, toggle,
    setSelected: value => patch(before => ({selected: typeof value === "function" ? value(before.selected) : value})),
    setAuthor: author => patch({author}), setShortcutName: shortcutName => patch({shortcutName}),
    setPicker: picker => patch({picker})};
}

function browseActions(state, data, patch, actions, write, onClose) {
  const startBrowser = (view, bests = false, target = null) => {
    patch({view, filters: {...emptyFilters(), source: view === "race" ? "all" : "local", bests, target},
      selected: view === "race" ? new Set(data.current.filter(row => row.available).map(rowId)) : new Set(), saved: false});
    actions.go("browser");
  };
  const currentBests = () => {
    patch({selected: overallSelection(data.bests, data.opening), reviewBack: "target-bests",
      selectionLabel: `${data.openingName} · Personal bests`, view: "export"}); actions.go("target-bests");
  };
  const allBests = () => {
    patch({selected: new Set(data.bests.filter(row => row.available).map(rowId)), reviewBack: "export",
      selectionLabel: "All personal bests", view: "export"}); actions.go("review");
  };
  const review = () => {
    if (!data.exportFits) return;
    patch({reviewBack: state.page, selectionLabel: state.page === "target-bests" ? `${data.openingName} · Personal bests` : "Custom selection", nameReturn: "review"});
    actions.go(state.author.trim() ? "review" : "name");
  };
  const race = () => actions.run(async () => {
    await send("POST", `${URL}/prepare-race`, {ghosts: data.choice, target: data.raceTarget});
    const answer = await write({ghosts: data.choice, target: data.raceTarget});
    if (answer?.applied === false) throw new Error(answer.error);
    onClose();
  });
  return {startBrowser, currentBests, allBests, review, race};
}

function exportActions(state, data, patch, actions) {
  const startExport = () => {patch({view: "export", author: state.rememberedAuthor, nameReturn: "export", nameBack: "home"}); actions.go(state.rememberedAuthor.trim() ? "export" : "name");};
  const changeName = () => {patch({author: state.rememberedAuthor, nameReturn: state.page, nameBack: state.page}); actions.go("name");};
  const saveName = () => actions.run(async () => {
    const answer = await send("PUT", `${URL}/author`, {author: state.author});
    patch({author: answer.author, rememberedAuthor: answer.author}); actions.go(state.nameReturn);
  });
  const useShortcut = shortcut => {
    patch({selected: new Set(resolveShortcut(state.catalog, shortcut.criteria).map(rowId)),
      filters: {...emptyFilters(), ...shortcut.criteria}, view: "export", reviewBack: "export", selectionLabel: shortcut.name}); actions.go("review");
  };
  const exportSelected = () => actions.run(async () => {
    const answer = await actions.job(() => send("POST", `${URL}/export`, {selected: selectionOf(data.chosen), author: state.author}));
    patch({receipt: {kind: "export", ...answer}}); actions.go("receipt");
    const link = document.createElement("a");
    link.href = answer.download_url; link.download = "sm64-ghost-collection.sm64ghosts";
    document.body.appendChild(link); link.click(); link.remove();
  });
  const saveShortcut = () => actions.run(async () => {
    const answer = await send("POST", `${URL}/shortcuts`, {name: state.shortcutName, criteria: state.filters});
    patch(before => ({catalog: {...before.catalog, shortcuts: answer.shortcuts}, saved: true}));
  });
  const removeShortcut = shortcut => actions.run(async () => {
    const answer = await send("DELETE", `${URL}/shortcuts/${shortcut.id}`);
    patch(before => ({catalog: {...before.catalog, shortcuts: answer.shortcuts}}));
  });
  return {startExport, changeName, saveName, useShortcut, exportSelected, saveShortcut, removeShortcut};
}

function importActions(state, data, patch, actions, resources) {
  const cancelImport = () => actions.run(async () => {
    await actions.discard(); patch({selected: new Set(), fileName: ""}); actions.go("home");
  });
  const previewFile = event => {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    actions.run(async () => {
      await actions.discard();
      const answer = await actions.job(() => getJSON(`${URL}/import/preview`, {method: "POST", body: file,
        headers: {"Content-Type": "application/octet-stream"}}));
      resources.setPreview(answer.preview_id);
      patch({preview: answer, fileName: file.name, selected: importSelection(answer.ghosts, "all"), importMode: "all", includeNoBest: false});
      actions.go("import-review");
    });
  };
  const selectImport = (mode, include = state.includeNoBest) => {
    patch({importMode: mode, includeNoBest: include,
      ...(mode === "custom" ? {} : {selected: importSelection(data.importRows, mode, include)})});
  };
  const commitImport = () => actions.run(async () => {
    const answer = await actions.job(() => send("POST", `${URL}/import/commit`, {
      preview_id: state.preview.preview_id, selected: data.importChosen.map(row => row.id || row.import_id)}));
    resources.clearPreview(); patch({preview: null}); await actions.load();
    patch({receipt: {kind: "import", ...answer}}); actions.go("receipt");
  });
  return {cancelImport, previewFile, selectImport, commitImport};
}

export function useGhostLibrary({entityKey, session, write, onClose, t}) {
  const fixed = useRef({opening: openingTarget(entityKey, session?.target),
    raceTarget: session?.target ? {...session.target} : null, selection: session?.selection || {}}).current;
  const [state, setState] = useState(initialState);
  const preview = useRef(null), running = useRef(false), upload = useRef(null);
  const patch = delta => setState(before => ({...before, ...(typeof delta === "function" ? delta(before) : delta)}));
  const resources = {preview, running, setRunning: value => {running.current = value;},
    setPreview: identity => {preview.current = identity;}, clearPreview: () => {preview.current = null;}};
  useEffect(() => {
    let alive = true;
    getJSON(URL).then(catalog => {if (alive) patch({catalog, author: catalog.author || "", rememberedAuthor: catalog.author || ""});})
      .catch(problem => {if (alive) patch({error: problem.message});});
    return () => {alive = false;};
  }, []);
  useEffect(() => () => {
    if (preview.current) send("DELETE", `${URL}/import/preview/${preview.current}`).catch(() => {});
  }, []);
  const data = libraryData(state, fixed, t), actions = commonActions(state, patch, resources, onClose);
  return {...state, ...data, ...actions, upload,
    ...browseActions(state, data, patch, actions, write, onClose),
    ...exportActions(state, data, patch, actions), ...importActions(state, data, patch, actions, resources)};
}
