"use strict";

/*
 * The page holds no authority. Everything it shows is the last response it received, and
 * every write carries the revision the operator was looking at when they composed it.
 * Nothing here recomputes a diff, invents an id, or retries a conflict on its own.
 */

const POLL_MS = 6000;

/* While an agent is working the board is the progress display, so look more often. */
const FAST_POLL_MS = 2000;

const state = {
  config: null,
  catalog: null,
  challenges: [],
  challengeId: null,
  board: null,
  armed: null,
  busy: false,
  guessReason: "",
  coreUp: null,
  agent: null,
  agentStatus: null,
};

const $ = (id) => document.getElementById(id);

/* ---------------------------------------------------------------- transport */

async function api(method, path, body) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    return {
      ok: false,
      status: 0,
      body: { webui_error: { code: "browser_request_failed", message: String(err) } },
    };
  }
  const text = await response.text();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      parsed = text;
    }
  }
  return { ok: response.ok, status: response.status, body: parsed };
}

function describeError(result) {
  const body = result.body;
  if (body && typeof body === "object") {
    if (body.error && typeof body.error === "object" && body.error.code) {
      return {
        source: "Memory Core",
        code: body.error.code,
        message: body.error.message || "",
        details: body.error.details || null,
      };
    }
    if (body.webui_error && typeof body.webui_error === "object") {
      return {
        source: "ctfmem-web",
        code: body.webui_error.code,
        message: body.webui_error.message || "",
        details: null,
      };
    }
    if (Array.isArray(body.detail)) {
      return {
        source: "request validation",
        code: "HTTP " + result.status,
        message: body.detail
          .map((entry) => (entry.loc || []).join(".") + ": " + entry.msg)
          .join("\n"),
        details: null,
      };
    }
    if (typeof body.detail === "string") {
      return {
        source: "server",
        code: "HTTP " + result.status,
        message: body.detail,
        details: null,
      };
    }
  }
  return {
    source: "unrecognised response",
    code: "HTTP " + result.status,
    message: typeof body === "string" ? body : JSON.stringify(body),
    details: null,
  };
}

/* ------------------------------------------------------------------ banners */

function showBanner(id, kind, title, message) {
  let node = $("banner-" + id);
  if (!node) {
    node = document.createElement("div");
    node.id = "banner-" + id;
    $("banner-area").appendChild(node);
  }
  node.className = "banner " + kind;
  node.innerHTML = "";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const text = document.createElement("pre");
  text.textContent = message;
  node.append(heading, text);
  return node;
}

function hideBanner(id) {
  const node = $("banner-" + id);
  if (node) node.remove();
}

function reportError(id, title, result) {
  const described = describeError(result);
  let message = described.source + " / " + described.code + "\n" + described.message;
  if (described.details) {
    message += "\ndetails: " + JSON.stringify(described.details);
  }
  if (described.code === "summarizer_unavailable") {
    message += RETRY_HINT[id] || RETRY_HINT.default;
  }
  showBanner(id, "error", title, message);
  return described;
}

const RETRY_HINT = {
  ingest:
    "\n\nThe Summarizer is down or too slow. What you submitted was journalled before it ran," +
    " so nothing was lost; retrying will not duplicate the record.",
  query: "\n\nThe Summarizer is down or too slow. A query writes nothing, so retrying is free.",
  default: "\n\nThe Summarizer is down or too slow. This is retryable.",
};

/* -------------------------------------------------------------------- arming */

function arm(formId) {
  if (!state.challengeId || !state.board) return;
  if (state.armed && state.armed.formId === formId) return;
  state.armed = {
    formId,
    challengeId: state.challengeId,
    revision: state.board.revision,
  };
  renderArmed();
}

function disarm() {
  state.armed = null;
  renderArmed();
}

function armedRevision(formId) {
  const armed = state.armed;
  if (!armed || armed.formId !== formId) return null;
  if (armed.challengeId !== state.challengeId) return null;
  if (!state.board || armed.revision !== state.board.revision) return null;
  return armed.revision;
}

/*
 * The checklist is deliberately absent from this list. Arming protects a form that is
 * composed over a window of time and submitted later; a checkbox has no such window, so
 * its expected_revision is simply the one on screen when it was clicked. A concurrent
 * change still produces a real 409 rather than a self-inflicted stale capture.
 */
const ARMED_TARGETS = [
  { form: "op", label: "op-armed", button: "op-submit" },
  { form: "template", label: "tpl-armed", button: "tpl-submit" },
];

function renderArmed() {
  for (const target of ARMED_TARGETS) {
    const label = $(target.label);
    const button = target.button ? $(target.button) : null;
    if (!label) continue;
    label.innerHTML = "";
    const armed = state.armed;
    const mine = armed && armed.formId === target.form;
    let usable = false;
    if (!state.challengeId || !state.board) {
      label.textContent = "no challenge selected";
    } else if (!mine) {
      label.textContent = "composes against r" + state.board.revision + " when you start typing";
      usable = true;
    } else if (armedRevision(target.form) !== null) {
      label.textContent = "composed against r" + armed.revision;
      usable = true;
    } else {
      label.textContent =
        "composed against r" +
        armed.revision +
        ", whiteboard is now r" +
        state.board.revision +
        " — ";
      const again = document.createElement("button");
      again.type = "button";
      again.textContent = "recompose against r" + state.board.revision;
      again.addEventListener("click", () => {
        disarm();
        arm(target.form);
      });
      label.appendChild(again);
    }
    if (button) button.disabled = state.busy || !usable;
  }
}

/* --------------------------------------------------------------------- board */

function findItem(board, itemId) {
  for (const layer of ["fact", "assumption"]) {
    const sections = board[layer] || {};
    for (const section of Object.keys(sections)) {
      for (const item of sections[section] || []) {
        if (item.id === itemId) return { layer, section, item };
      }
    }
  }
  return null;
}

function sectionsOf(layer) {
  if (!state.board) return [];
  return Object.keys(state.board[layer] || {});
}

function renderBoard() {
  const factHost = $("fact-sections");
  const assumptionHost = $("assumption-sections");
  factHost.innerHTML = "";
  assumptionHost.innerHTML = "";

  if (!state.board) {
    factHost.textContent = "";
    assumptionHost.textContent = "";
    return;
  }

  const hideEmpty = $("hide-empty").checked;
  let total = 0;

  for (const [layer, host] of [
    ["fact", factHost],
    ["assumption", assumptionHost],
  ]) {
    const sections = state.board[layer] || {};
    for (const section of Object.keys(sections)) {
      const items = sections[section] || [];
      total += items.length;
      if (!items.length && hideEmpty) continue;

      const block = document.createElement("div");
      block.className = "section-block";
      const name = document.createElement("div");
      name.className = "section-name";
      name.textContent = section + " (" + items.length + ")";
      block.appendChild(name);

      for (const item of items) {
        block.appendChild(renderItem(item));
      }
      host.appendChild(block);
    }
  }

  if (total === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent =
      "Nothing on the whiteboard yet. Submit an observation under Observe; the Summarizer " +
      "will decide where it lands.";
    factHost.appendChild(empty);
  }
}

function renderItem(item) {
  const node = document.createElement("div");
  node.className = "item";

  const identifier = document.createElement("span");
  identifier.className = "item-id";
  identifier.textContent = item.id;
  identifier.title = "use this id in Override";
  identifier.addEventListener("click", () => {
    $("op-id").value = item.id;
    arm("op");
    syncOperationFields();
    $("op-id").scrollIntoView({ block: "center" });
  });

  const text = document.createElement("span");
  text.textContent = " " + item.text;

  node.append(identifier, text);

  const bits = [];
  if (item.evidence && item.evidence.length) bits.push("evidence " + item.evidence.join(", "));
  if (item.tags && item.tags.length) bits.push("tags " + item.tags.join(", "));
  if (item.promoted_from) bits.push("from " + item.promoted_from);
  if (item.source_actor) bits.push("by " + item.source_actor);
  if (bits.length) {
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.textContent = bits.join(" · ");
    node.appendChild(meta);
  }
  return node;
}

/* ------------------------------------------------------------------ guessing */

function labelsForCatalog(catalogId) {
  if (!state.catalog) return null;
  const template = state.catalog.templates.find((entry) => entry.catalog === catalogId);
  if (!template) return null;
  const byId = {};
  for (const label of template.guessing) byId[label.id] = label;
  return byId;
}

function renderGuessing() {
  const host = $("guessing-list");
  const note = $("guessing-note");
  host.innerHTML = "";
  note.innerHTML = "";

  if (!state.board || !state.board.guessing) {
    note.textContent = "no challenge selected";
    return;
  }

  const guessing = state.board.guessing;
  const techniqueState = guessing.state || {};
  const labels = labelsForCatalog(guessing.catalog);
  const ids = Object.keys(techniqueState).sort();

  const head = document.createElement("div");
  head.className = "muted";
  head.textContent = "catalog " + guessing.catalog + " · " + ids.length + " techniques";
  note.appendChild(head);

  if (labels === null) {
    showBanner(
      "catalog-drift",
      "warn",
      "Template file not found for this catalog",
      "The challenge records catalog " +
        guessing.catalog +
        ", but no file in " +
        (state.config ? state.config.templates_dir : "the templates directory") +
        " declares it. Technique titles are unavailable; ids and tried state below still " +
        "come from Memory Core. Point --templates at the same directory the core was " +
        "started with.",
    );
  } else {
    hideBanner("catalog-drift");
  }

  const reason = document.createElement("label");
  reason.className = "block";
  reason.textContent = "reason (required for any change below)";
  const reasonInput = document.createElement("input");
  reasonInput.type = "text";
  reasonInput.id = "guess-reason";
  reasonInput.value = state.guessReason;
  reasonInput.addEventListener("input", (event) => {
    state.guessReason = event.target.value;
  });
  reason.appendChild(reasonInput);
  const against = document.createElement("div");
  against.id = "guessing-armed";
  against.className = "muted";
  against.textContent = "each change is applied against r" + state.board.revision;
  note.append(reason, against);

  if (!ids.length) {
    host.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "This template ships no Guessing techniques.";
    host.appendChild(empty);
    return;
  }

  for (const id of ids) {
    host.appendChild(renderGuessRow(id, techniqueState[id] || {}, labels ? labels[id] : null));
  }
}

function renderGuessRow(id, techniqueState, label) {
  const row = document.createElement("div");
  row.className = "guess-row" + (techniqueState.tried ? " tried" : "");

  const tried = document.createElement("input");
  tried.type = "checkbox";
  tried.checked = Boolean(techniqueState.tried);
  tried.title = "mark tried";

  const identifier = document.createElement("span");
  identifier.className = "guess-id";
  identifier.textContent = id;

  const title = document.createElement("span");
  title.className = "guess-title";
  title.textContent = label ? label.title : "(no title in the template files)";

  const noteInput = document.createElement("input");
  noteInput.type = "text";
  noteInput.className = "guess-note";
  noteInput.value = techniqueState.note || "";
  noteInput.placeholder = "note";

  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "save note";

  tried.addEventListener("change", () => {
    submitMarkTried(id, tried.checked, noteInput.value);
  });
  save.addEventListener("click", () => {
    submitMarkTried(id, tried.checked, noteInput.value);
  });

  row.append(tried, identifier, title, noteInput, save);
  if (label && label.tags && label.tags.length) {
    title.title = label.tags.join(", ");
  }
  return row;
}

/* -------------------------------------------------------------------- result */

const DIFF_KEYS = [
  "fact_added",
  "fact_updated",
  "fact_removed",
  "assumption_added",
  "assumption_updated",
  "assumption_removed",
  "promoted",
  "demoted",
];

function renderResult(label, envelope, landing) {
  const host = $("result-body");
  host.innerHTML = "";
  host.className = "";

  const heading = document.createElement("div");
  heading.textContent =
    label + " · r" + envelope.revision_before + " → r" + envelope.revision_after;
  host.appendChild(heading);

  if (envelope.revision_before === envelope.revision_after) {
    const noop = document.createElement("div");
    noop.className = "muted";
    noop.textContent = "No change: the Summarizer found nothing worth storing. No revision spent.";
    host.appendChild(noop);
  }

  if (envelope.summary) {
    const summary = document.createElement("div");
    summary.textContent = envelope.summary;
    host.appendChild(summary);
  }

  const diff = envelope.diff || {};
  for (const key of DIFF_KEYS) {
    const ids = diff[key] || [];
    if (!ids.length) continue;
    const line = document.createElement("div");
    line.className = "diff-line";
    const name = document.createElement("span");
    name.className = "diff-key";
    name.textContent = key;
    const value = document.createElement("span");
    value.textContent = ids.join(", ");
    line.append(name, value);
    host.appendChild(line);
  }
  if (diff.template_changed) {
    const line = document.createElement("div");
    line.className = "diff-line";
    line.textContent =
      "template " + diff.template_changed.from + " → " + diff.template_changed.to;
    host.appendChild(line);
  }

  for (const text of landing || []) {
    const line = document.createElement("div");
    line.className = "landing muted";
    line.textContent = text;
    host.appendChild(line);
  }
}

function describeLanding(diff) {
  const touched = [
    ...(diff.fact_added || []),
    ...(diff.assumption_added || []),
    ...(diff.fact_updated || []),
    ...(diff.assumption_updated || []),
  ];
  const lines = [];
  for (const id of touched) {
    const found = findItem(state.board, id);
    if (found) {
      lines.push(id + " is in " + found.layer + " / " + found.section);
    } else {
      lines.push(id + " is not in the snapshot read just now");
    }
  }
  return lines;
}

/* --------------------------------------------------------------------- loads */

async function loadHealth() {
  const result = await api("GET", "/api/healthz");
  const node = $("core-status");
  if (result.ok) {
    node.textContent = "core: up";
    node.className = "status up";
    hideBanner("core");
  } else {
    node.textContent = "core: unreachable";
    node.className = "status down";
    reportError("core", "Memory Core is not answering", result);
  }
  return result.ok;
}

async function loadChallenges() {
  const result = await api("GET", "/api/v1/challenges");
  if (!result.ok) {
    reportError("challenges", "Could not list challenges", result);
    return;
  }
  hideBanner("challenges");
  state.challenges = result.body || [];

  const select = $("challenge-select");
  select.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = state.challenges.length ? "select a challenge" : "no challenges yet";
  select.appendChild(none);
  for (const id of state.challenges) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = id;
    select.appendChild(option);
  }
  if (state.challengeId && state.challenges.includes(state.challengeId)) {
    select.value = state.challengeId;
  } else if (state.challengeId) {
    showBanner(
      "gone",
      "warn",
      "The selected challenge is gone",
      "Challenge " + state.challengeId + " is no longer listed by Memory Core.",
    );
    state.challengeId = null;
    state.board = null;
  }

  if (!state.challenges.length) {
    showBanner(
      "empty",
      "info",
      "No challenges yet",
      "Create one above. Classification happens once, at creation, from the initial input.",
    );
  } else {
    hideBanner("empty");
  }
}

async function loadBoard() {
  if (!state.challengeId) {
    state.board = null;
    renderBoard();
    renderGuessing();
    renderHeader();
    renderArmed();
    return;
  }
  const result = await api(
    "GET",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/memory",
  );
  if (!result.ok) {
    const described = describeError(result);
    if (described.source === "Memory Core" && described.code === "challenge_not_found") {
      await forgetSelection(
        "Challenge " +
          state.challengeId +
          " is not there. Memory Core answered challenge_not_found, which usually means it " +
          "was started against a different --runs directory than the one that challenge " +
          "lives in. Nothing was lost; pick a challenge below, or create one.",
      );
      return;
    }
    reportError("board", "Could not read the whiteboard", result);
    return;
  }
  hideBanner("board");
  hideBanner("gone");
  state.board = result.body;
  renderHeader();
  renderBoard();
  renderGuessing();
  renderSectionChoices();
  renderArmed();
}

/*
 * Drop a selection the core no longer knows about. Without this the poll re-requests a
 * dead challenge every few seconds forever, and the picker keeps offering it.
 */
async function forgetSelection(message) {
  state.challengeId = null;
  state.board = null;
  state.guessReason = "";
  disarm();
  hideBanner("board");
  showBanner("gone", "warn", "That challenge is not there", message);
  await loadChallenges();
  renderHeader();
  renderBoard();
  renderGuessing();
  renderArmed();
}

function renderHeader() {
  $("revision-badge").textContent = state.board ? "r" + state.board.revision : "r?";
  const challenge = state.board ? state.board.challenge : null;
  $("template-badge").textContent = challenge
    ? challenge.template + "@" + challenge.template_version
    : "";
  $("actor-badge").textContent = state.config ? "actor " + state.config.actor : "";
  $("foot").textContent = state.config
    ? "core " + state.config.api_url + " · templates " + state.config.templates_dir
    : "";
}

/* ----------------------------------------------------------------- overrides */

const FIELDS_BY_OP = {
  add: ["layer", "section", "text", "evidence", "tags"],
  replace: ["id", "text", "evidence", "tags"],
  remove: ["id"],
  promote: ["id", "section"],
  demote: ["id", "section"],
  move: ["id", "section"],
};

function syncOperationFields() {
  const op = $("op").value;
  const wanted = FIELDS_BY_OP[op] || [];
  for (const node of document.querySelectorAll("#stack-b [data-for]")) {
    node.hidden = !wanted.includes(node.dataset.for);
  }
  renderSectionChoices();
}

function targetLayerForOperation() {
  const op = $("op").value;
  if (op === "add") return $("op-layer").value;
  if (op === "promote") return "fact";
  if (op === "demote") return "assumption";
  if (op === "move") {
    const found = state.board ? findItem(state.board, $("op-id").value.trim()) : null;
    return found ? found.layer : null;
  }
  return null;
}

function renderSectionChoices() {
  const select = $("op-section");
  const layer = targetLayerForOperation();
  const previous = select.value;
  select.innerHTML = "";

  const op = $("op").value;
  if (op === "promote" || op === "demote") {
    const keep = document.createElement("option");
    keep.value = "";
    keep.textContent = "let Memory Core choose";
    select.appendChild(keep);
  }

  if (!layer) {
    const unknown = document.createElement("option");
    unknown.value = "";
    unknown.textContent = "enter an id first";
    select.appendChild(unknown);
    return;
  }

  for (const section of sectionsOf(layer)) {
    const option = document.createElement("option");
    option.value = section;
    option.textContent = section;
    select.appendChild(option);
  }
  if (previous) select.value = previous;
}

function splitList(value) {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function buildOperation() {
  const op = $("op").value;
  const id = $("op-id").value.trim();
  const section = $("op-section").value;
  const text = $("op-text").value;
  const evidence = splitList($("op-evidence").value);
  const tags = splitList($("op-tags").value);

  if (op === "add") {
    const operation = { op: "add", layer: $("op-layer").value, text };
    if (section) operation.section = section;
    if (evidence.length) operation.evidence = evidence;
    if (tags.length) operation.tags = tags;
    return operation;
  }
  if (op === "replace") {
    const operation = { op: "replace", id, text };
    if (evidence.length) operation.evidence = evidence;
    if (tags.length) operation.tags = tags;
    return operation;
  }
  if (op === "remove") return { op: "remove", id };
  if (op === "promote") {
    const operation = { op: "promote", id, to: "fact" };
    if (section) operation.section = section;
    return operation;
  }
  if (op === "demote") {
    const operation = { op: "demote", id, to: "assumption" };
    if (section) operation.section = section;
    return operation;
  }
  return { op: "move", id, section };
}

/* --------------------------------------------------------------------- writes */

function beginWrite() {
  state.busy = true;
  renderArmed();
}

async function endWrite() {
  state.busy = false;
  await loadBoard();
  renderArmed();
}

async function afterMutation(bannerId, label, result, composedRevision, formId) {
  if (result.ok) {
    hideBanner(bannerId);
    hideBanner("conflict");
    const envelope = result.body;
    await endWrite();
    renderResult(label, envelope, describeLanding(envelope.diff || {}));
    if (formId && state.armed && state.armed.formId === formId) disarm();
    return true;
  }

  const described = describeError(result);
  await endWrite();

  if (described.source === "Memory Core" && described.code === "revision_conflict") {
    const current =
      described.details && described.details.current_revision !== undefined
        ? described.details.current_revision
        : "unknown";
    showBanner(
      "conflict",
      "warn",
      "Nothing was written: the whiteboard moved",
      "You composed against r" +
        composedRevision +
        "; Memory Core is at r" +
        current +
        ". The board above has been re-read. Look at what changed, then recompose and " +
        "submit again. Nothing is retried automatically, because a blind retry is how a " +
        "concurrent change gets silently overwritten.",
    );
    return false;
  }

  if (described.source === "Memory Core" && described.code === "template_not_found") {
    showBanner(
      bannerId,
      "error",
      label + " failed: template not found",
      described.message +
        "\n\nThis challenge records a template Memory Core cannot load. Every mutation on " +
        "it will fail the same way until the core is pointed at a templates directory that " +
        "declares it, or the challenge is reclassified to one that exists.",
    );
    return false;
  }

  reportError(bannerId, label + " failed", result);
  return false;
}

async function submitNote() {
  const content = $("note-content").value;
  if (!content.trim()) {
    showBanner("ingest", "warn", "Nothing to submit", "The note is empty.");
    return;
  }
  beginWrite();
  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/ingest",
    { type: "note", content },
  );
  if (result.ok) {
    hideBanner("ingest");
    const envelope = result.body;
    await endWrite();
    renderResult("note", envelope, describeLanding(envelope.diff || {}));
    $("note-content").value = "";
  } else {
    await endWrite();
    reportError("ingest", "Note was not accepted", result);
  }
}

async function submitCommandResult() {
  const argv = $("cmd-argv")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!argv.length) {
    showBanner("ingest", "warn", "Nothing to submit", "Enter at least one argument.");
    return;
  }
  beginWrite();
  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/ingest",
    {
      type: "command_result",
      command: argv,
      exit_code: Number($("cmd-exit").value),
      stdout: $("cmd-stdout").value,
      stderr: $("cmd-stderr").value,
    },
  );
  if (result.ok) {
    hideBanner("ingest");
    const envelope = result.body;
    await endWrite();
    renderResult("command result", envelope, describeLanding(envelope.diff || {}));
  } else {
    await endWrite();
    reportError("ingest", "Command result was not accepted", result);
  }
}

async function submitMutation() {
  const composed = armedRevision("op");
  if (composed === null) {
    renderArmed();
    return;
  }
  beginWrite();
  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/mutate",
    {
      expected_revision: composed,
      reason: $("op-reason").value,
      operations: [buildOperation()],
    },
  );
  const applied = await afterMutation("mutate", $("op").value, result, composed, "op");
  if (applied) {
    $("op-text").value = "";
    $("op-evidence").value = "";
    $("op-tags").value = "";
    $("op-reason").value = "";
  }
}

async function submitMarkTried(technique, tried, note) {
  const reasonNode = $("guess-reason");
  const reason = reasonNode ? reasonNode.value : "";
  if (!reason.trim()) {
    showBanner(
      "guessing",
      "warn",
      "A reason is required",
      "Every mutation is journalled with an actor and a reason. Fill in the reason above, " +
        "then set the technique again.",
    );
    await loadBoard();
    return;
  }
  if (!state.board) return;
  const composed = state.board.revision;
  beginWrite();
  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/mutate",
    {
      expected_revision: composed,
      reason,
      operations: [{ op: "mark_tried", technique, tried, note: note || null }],
    },
  );
  await afterMutation("guessing", "mark_tried " + technique, result, composed);
}

async function submitTemplate() {
  const composed = armedRevision("template");
  if (composed === null) {
    renderArmed();
    return;
  }
  beginWrite();
  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/template",
    {
      template: $("tpl-select").value,
      expected_revision: composed,
      reason: $("tpl-reason").value,
    },
  );
  const applied = await afterMutation("template", "reclassify", result, composed, "template");
  if (applied) $("tpl-reason").value = "";
}

async function submitCreate() {
  const id = $("create-id").value.trim();
  const body = { id, title: $("create-title").value };
  const template = $("create-template").value;
  const initial = $("create-initial").value;
  if (template) body.template = template;
  if (initial.trim()) body.initial_input = initial;

  beginWrite();
  const result = await api("POST", "/api/v1/challenges", body);
  state.busy = false;

  if (!result.ok) {
    const described = describeError(result);
    if (described.source === "Memory Core" && described.code === "challenge_exists") {
      showBanner(
        "create",
        "warn",
        "That challenge id is taken",
        "A challenge called " + id + " already exists. Pick another id, or select it above.",
      );
    } else {
      reportError("create", "Challenge was not created", result);
    }
    renderArmed();
    return;
  }

  hideBanner("create");
  hideBanner("empty");
  $("create-panel").hidden = true;
  $("create-id").value = "";
  $("create-title").value = "";
  $("create-initial").value = "";
  await loadChallenges();
  await selectChallenge(id);
}

async function submitGuess() {
  const seedRaw = $("guess-seed").value;
  const body = {
    count: Number($("guess-count").value),
    only_untried: $("guess-untried").checked,
  };
  if (seedRaw !== "") body.seed = Number(seedRaw);

  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/guess",
    body,
  );
  const host = $("guess-result");
  host.innerHTML = "";

  if (!result.ok) {
    reportError("guess", "Draw failed", result);
    return;
  }
  hideBanner("guess");

  const candidates = result.body.candidates || [];
  if (!candidates.length) {
    host.className = "muted";
    host.textContent = $("guess-untried").checked
      ? "Every technique in this catalog is already marked tried. That is a successful draw of nothing."
      : "This catalog is empty.";
    return;
  }

  host.className = "";
  for (const candidate of candidates) {
    const row = document.createElement("div");
    row.className = "guess-row" + (candidate.tried ? " tried" : "");
    const identifier = document.createElement("span");
    identifier.className = "guess-id";
    identifier.textContent = candidate.id;
    const title = document.createElement("span");
    title.className = "guess-title";
    title.textContent = candidate.title;
    row.append(identifier, title);
    host.appendChild(row);
  }

  if (candidates.length === 50) {
    const warn = document.createElement("div");
    warn.className = "muted";
    warn.textContent =
      "50 is the contract's maximum count. If this catalog holds more than 50 techniques " +
      "the draw is a random sample and this list is not the whole of it. The checklist " +
      "above is complete regardless, because it comes from the challenge itself.";
    host.appendChild(warn);
  }
}

async function submitQuery() {
  const layers = Array.from(document.querySelectorAll(".query-layer"))
    .filter((box) => box.checked)
    .map((box) => box.value);
  const body = { question: $("query-question").value };
  if (layers.length) body.layers = layers;

  const result = await api(
    "POST",
    "/api/v1/challenges/" + encodeURIComponent(state.challengeId) + "/query",
    body,
  );
  const host = $("query-result");
  host.innerHTML = "";

  if (!result.ok) {
    reportError("query", "Query failed", result);
    return;
  }
  hideBanner("query");

  const answer = document.createElement("div");
  answer.textContent = result.body.answer;
  host.appendChild(answer);
  const references = result.body.references || [];
  const cited = document.createElement("div");
  cited.className = "muted";
  cited.textContent = references.length
    ? "references " + references.join(", ")
    : "no references in the current snapshot";
  host.appendChild(cited);
}

/* ---------------------------------------------------------------------- agent */

function fillSelect(id, values, firstLabel) {
  const select = $(id);
  select.innerHTML = "";
  if (firstLabel !== undefined) {
    const first = document.createElement("option");
    first.value = "";
    first.textContent = firstLabel;
    select.appendChild(first);
  }
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
}

async function loadAgentOptions() {
  const result = await api("GET", "/api/local/agent/options");
  if (!result.ok) return;
  state.agent = result.body;
  if (!state.agent.enabled) return;

  $("agent-panel").hidden = false;
  fillSelect("agent-mode", state.agent.modes);
  fillSelect("agent-session", state.agent.sessions);
  fillSelect("agent-policy", state.agent.policies);
  fillSelect("agent-scenario", state.agent.scenarios, "real Claude");
  $("agent-turns").value = state.agent.max_turns;
  $("agent-turns").max = state.agent.max_turns;
  $("agent-cost").value = state.agent.max_cost_usd;
  $("agent-cost").max = state.agent.max_cost_usd;
  $("agent-ceiling").textContent =
    "ceilings set at launch: " +
    state.agent.max_turns +
    " turns, $" +
    state.agent.max_cost_usd +
    " · workspace " +
    state.agent.workspace +
    " · asking for more than a ceiling silently gets the ceiling";
}

async function loadAgentStatus() {
  if (!state.agent || !state.agent.enabled || !state.challengeId) return;
  const result = await api(
    "GET",
    "/api/local/agent/" + encodeURIComponent(state.challengeId) + "/status",
  );
  if (!result.ok) {
    reportError("agent", "Could not read the agent status", result);
    return;
  }
  hideBanner("agent");
  state.agentStatus = result.body;
  renderAgentStatus();
}

function renderAgentStatus() {
  const host = $("agent-state");
  const log = $("agent-log");
  const status = state.agentStatus;

  if (!status) {
    host.textContent = "";
    log.hidden = true;
    return;
  }

  const running = Boolean(status.running);
  const bits = [status.status];
  if (status.mode) bits.push(status.mode + "/" + status.session);
  if (status.turns !== undefined) {
    bits.push(status.turns + " turns, " + status.productive_turns + " productive");
  }
  if (status.total_cost_usd !== undefined && status.total_cost_usd !== null) {
    bits.push("$" + Number(status.total_cost_usd).toFixed(4));
  }
  if (status.last_revision !== undefined && status.last_revision !== null) {
    bits.push("last revision r" + status.last_revision);
  }
  if (status.stopped_because) bits.push(status.stopped_because);
  if (status.pid) bits.push("pid " + status.pid);

  host.textContent = bits.join(" · ");
  host.className = running ? "running" : "muted";

  $("agent-run").disabled = running;
  $("agent-stop").disabled = !running;

  log.hidden = !status.log;
  log.textContent = status.log || "";
}

async function submitAgentRun() {
  const body = {
    prompt: $("agent-prompt").value,
    mode: $("agent-mode").value,
    session: $("agent-session").value,
    memory_policy: $("agent-policy").value,
    max_turns: Number($("agent-turns").value),
    max_cost_usd: Number($("agent-cost").value),
  };
  const scenario = $("agent-scenario").value;
  if (scenario) body.fake_agent = scenario;

  const result = await api(
    "POST",
    "/api/local/agent/" + encodeURIComponent(state.challengeId) + "/run",
    body,
  );
  if (!result.ok) {
    reportError("agent", "The agent did not start", result);
    return;
  }
  hideBanner("agent");
  $("agent-run").disabled = true;
  showBanner(
    "agent-started",
    "info",
    "Agent started",
    "pid " +
      result.body.pid +
      "\n" +
      result.body.argv.join(" ") +
      "\n\nWatch the whiteboard: every turn that finds something ingests it and moves the " +
      "revision. Stopping this page does not stop the run; use stop, or `ctfmem agent stop`.",
  );
  await loadAgentStatus();
}

async function submitAgentStop() {
  const result = await api(
    "POST",
    "/api/local/agent/" + encodeURIComponent(state.challengeId) + "/stop",
  );
  if (!result.ok) {
    reportError("agent", "Could not request a stop", result);
    return;
  }
  hideBanner("agent-started");
  showBanner(
    "agent-stopping",
    "info",
    "Stop requested",
    "The loop checks the stop file between turns, so it finishes the turn it is on and " +
      "then stops. Its partial work is already on the board.",
  );
  await loadAgentStatus();
}

/* --------------------------------------------------------------------- wiring */

async function selectChallenge(id) {
  state.challengeId = id || null;
  state.guessReason = "";
  state.agentStatus = null;
  renderAgentStatus();
  disarm();
  $("challenge-select").value = state.challengeId || "";
  hideBanner("conflict");
  hideBanner("catalog-drift");
  hideBanner("agent-started");
  hideBanner("agent-stopping");
  await loadBoard();
  await loadAgentStatus();
}

function fillTemplatePickers() {
  if (!state.catalog) return;
  for (const selectId of ["create-template", "tpl-select"]) {
    const select = $(selectId);
    const keepFirst = selectId === "create-template";
    select.innerHTML = "";
    if (keepFirst) {
      const auto = document.createElement("option");
      auto.value = "";
      auto.textContent = "classify from initial input";
      select.appendChild(auto);
    }
    for (const template of state.catalog.templates) {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = template.description
        ? template.id + " — " + template.description.slice(0, 60)
        : template.id;
      select.appendChild(option);
    }
  }
  if (state.catalog.problems && state.catalog.problems.length) {
    showBanner(
      "templates",
      "warn",
      "Some template files could not be read",
      state.catalog.problems.join("\n") +
        "\n\nThe picker lists what could be read. Memory Core validates every choice anyway.",
    );
  }
}

function wire() {
  $("challenge-select").addEventListener("change", (event) => selectChallenge(event.target.value));
  $("refresh").addEventListener("click", async () => {
    state.coreUp = await loadHealth();
    await loadChallenges();
    await loadBoard();
  });
  $("toggle-create").addEventListener("click", () => {
    const panel = $("create-panel");
    panel.hidden = !panel.hidden;
  });
  $("create-submit").addEventListener("click", submitCreate);
  $("hide-empty").addEventListener("change", renderBoard);

  $("note-submit").addEventListener("click", submitNote);
  $("cmd-submit").addEventListener("click", submitCommandResult);

  $("op").addEventListener("change", syncOperationFields);
  $("op-layer").addEventListener("change", renderSectionChoices);
  $("op-id").addEventListener("input", renderSectionChoices);
  for (const id of ["op-id", "op-text", "op-evidence", "op-tags", "op-reason"]) {
    $(id).addEventListener("input", () => arm("op"));
  }
  $("op-submit").addEventListener("click", submitMutation);

  $("tpl-reason").addEventListener("input", () => arm("template"));
  $("tpl-select").addEventListener("change", () => arm("template"));
  $("tpl-submit").addEventListener("click", submitTemplate);

  $("guess-submit").addEventListener("click", submitGuess);
  $("query-submit").addEventListener("click", submitQuery);

  $("agent-run").addEventListener("click", submitAgentRun);
  $("agent-stop").addEventListener("click", submitAgentStop);
}

/*
 * Health first. A core that is down makes every other request a banner, and a core that
 * came back has to clear the banners its absence left behind - neither happens if the poll
 * only ever re-reads the board.
 */
async function poll() {
  if (state.busy) return;
  const up = await loadHealth();
  const recovered = up && state.coreUp === false;
  state.coreUp = up;
  if (!up) return;
  if (recovered) await loadChallenges();
  await loadAgentStatus();
  if (state.armed) return;
  if (state.challengeId) await loadBoard();
}

/*
 * Self-scheduling rather than a fixed interval, so the rate can follow what is happening.
 * A working agent is worth watching closely; an idle board is not. Agent status refreshes
 * even while a form is armed, because reading it changes nothing the operator composed.
 */
function schedulePoll() {
  const running = Boolean(state.agentStatus && state.agentStatus.running);
  setTimeout(
    async () => {
      await poll();
      schedulePoll();
    },
    running ? FAST_POLL_MS : POLL_MS,
  );
}

async function boot() {
  wire();
  syncOperationFields();

  const config = await api("GET", "/api/local/config");
  if (config.ok) state.config = config.body;
  const catalog = await api("GET", "/api/local/templates");
  if (catalog.ok) state.catalog = catalog.body;
  fillTemplatePickers();
  await loadAgentOptions();
  renderHeader();

  state.coreUp = await loadHealth();
  await loadChallenges();
  if (state.challenges.length === 1) await selectChallenge(state.challenges[0]);
  renderArmed();

  schedulePoll();
}

boot();
