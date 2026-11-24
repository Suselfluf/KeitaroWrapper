const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
const form = document.getElementById("editor-form");
const campaignInput = document.getElementById("campaign-id");
const message = document.getElementById("editor-notice");
const content = document.getElementById("editor-content");
const loadButton = document.getElementById("load");
const RESULT_NOTICE_DURATION_MS = 5000;

let campaign = null;
let busy = false;
let confirmation = null;
let noticeTimer;
const drafts = new Map();
const searches = new Map();

function activateTab(tab, focus = false) {
  for (const item of tabs) {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) tab.focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateTab(tab));
  tab.addEventListener("keydown", (event) => {
    const indexByKey = {ArrowLeft: 1 - index, ArrowRight: 1 - index, Home: 0, End: 1};
    if (!Object.hasOwn(indexByKey, event.key)) return;
    event.preventDefault();
    activateTab(tabs[indexByKey[event.key]], true);
  });
});

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function button(label, action, streamId, offerId, className = "button") {
  const element = node("button", label, className);
  element.type = "button";
  element.dataset.action = action;
  if (streamId !== undefined) element.dataset.stream = streamId;
  if (offerId !== undefined) element.dataset.id = offerId;
  element.disabled = busy;
  return element;
}

function notice(kind, text) {
  window.clearTimeout(noticeTimer);
  noticeTimer = undefined;
  if (!text) {
    message.replaceChildren();
    return;
  }

  const entry = node("p", text, `message ${kind}`);
  message.replaceChildren(entry);
  if (kind === "ok" || kind === "bad") {
    noticeTimer = window.setTimeout(() => notice(), RESULT_NOTICE_DURATION_MS);
  }
  return entry;
}

async function request(path, method = "GET", body) {
  let response;
  let data;
  try {
    response = await fetch(path, {
      method,
      ...(body === undefined ? {} : {
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      }),
    });
    data = await response.json();
  } catch {
    throw new Error("Ответ не получен. Проверьте соединение и состояние потока.");
  }
  if (!response.ok) throw new Error(data.error?.message ?? "Операция не выполнена.");
  return data;
}

function idFrom(value) {
  const id = Number(value);
  return /^\d+$/.test(value.trim()) && Number.isSafeInteger(id) && id > 0 ? id : null;
}

function shareText(share) {
  return `${share}%`;
}

function drawOffer(offer, streamId, archived, confirmedIds) {
  const row = node("div", undefined, `offer-row ${archived ? "archive-row" : "active-row"}`);
  row.dataset.offer = offer.id;
  const identity = node("div");
  const name = offer.name ?? `Оффер № ${offer.id}`;
  identity.append(node("strong", name), node("small", `ID ${offer.id}`));
  const controls = node("div", undefined, "offer-actions");
  if (archived) {
    const restore = button("Вернуть", "restore", streamId, offer.id);
    restore.setAttribute("aria-label", `Вернуть ${name} (ID ${offer.id})`);
    controls.append(restore);
  } else {
    const pinText = offer.pinned ? "Снять закрепление" : "Закрепить";
    const pin = button(pinText, "pin", streamId, offer.id);
    pin.setAttribute("aria-label", `${pinText} ${name} (ID ${offer.id})`);
    pin.setAttribute("aria-pressed", String(offer.pinned));
    pin.disabled = busy || !confirmedIds.has(offer.id);
    const remove = button("Убрать", "remove", streamId, offer.id, "button danger");
    remove.setAttribute("aria-label", `Убрать ${name} (ID ${offer.id})`);
    if (confirmedIds.has(offer.id)) controls.append(pin);
    controls.append(remove);
  }
  row.append(identity, node("span", shareText(offer.share), "weight"), controls);
  return row;
}

function drawSearch(streamId) {
  const form = node("form", undefined, "offer-search");
  form.dataset.stream = streamId;
  const search = searches.get(streamId) ?? {query: ""};
  const label = node("label", "ID оффера для поиска", "field");
  const input = node("input");
  input.name = "offer_id";
  input.inputMode = "numeric";
  input.pattern = "[0-9]+";
  input.required = true;
  input.autocomplete = "off";
  input.disabled = busy;
  input.value = search.query;
  label.append(input);
  const submit = node("button", "Найти оффер", "button");
  submit.type = "submit";
  submit.disabled = busy;
  form.append(label, submit);
  const block = node("div");
  block.append(form);
  if (search.result) {
    const result = node("div", undefined, "lookup-result");
    result.append(
      node("span", `${search.result.name} · ID ${search.result.id}`),
      button("Добавить", "add", streamId, search.result.id),
    );
    block.append(result);
  }
  return block;
}

function drawStream(confirmed) {
  const draft = drafts.get(confirmed.id);
  const stream = draft ?? confirmed;
  const card = node("section", undefined, `stream-card${draft ? " stream-draft" : ""}`);
  card.dataset.stream = stream.id;
  const heading = node("div", undefined, "stream-heading");
  heading.append(node("h3", stream.name), node("small", `Поток № ${stream.id}`));
  card.append(heading);
  if (stream.kind === "redirect") return card;

  const total = stream.active.reduce((sum, offer) => sum + offer.share, 0);
  const totalText = new Intl.NumberFormat("ru", {maximumFractionDigits: 10}).format(total);
  heading.append(node("small", `Общий вес ${totalText}%`, "share-total"));
  const confirmedIds = new Set(confirmed.active.map(({id}) => id));
  if (stream.active.length) {
    const columnLabels = node("div", undefined, "offer-row offer-labels");
    for (const label of ["Оффер", "Вес", "Действия"]) columnLabels.append(node("span", label));
    card.append(columnLabels);
    for (const offer of stream.active) card.append(drawOffer(offer, stream.id, false, confirmedIds));
  } else {
    card.append(node("p", "В потоке нет активных офферов.", "editor-empty"));
  }
  if (stream.archived.length) {
    const archive = node("section", undefined, "offer-archive");
    archive.append(node("h3", "Архив"));
    for (const offer of stream.archived) archive.append(drawOffer(offer, stream.id, true, confirmedIds));
    card.append(archive);
  }
  card.append(drawSearch(stream.id));

  if (draft) {
    const bar = node("div", undefined, "draft-bar");
    const text = node("div");
    text.append(
      node("strong", "Черновик"),
      node("p", "Keitaro сохраняет последний подтверждённый состав до отправки."),
    );
    const controls = node("div", undefined, "offer-actions");
    controls.append(
      button("Отменить черновик", "cancel", stream.id),
      button("Отправить в Keitaro", "push", stream.id, undefined, "button primary"),
    );
    bar.append(text, controls);
    card.append(bar);
  }
  return card;
}

function render() {
  loadButton.disabled = busy;
  campaignInput.disabled = busy;
  if (!campaign) return;
  const heading = node("div", undefined, "stream-heading editor-campaign");
  heading.append(node("strong", campaign.name), node("small", `Кампания № ${campaign.id}`));
  const blocks = [heading];
  if (confirmation !== null && drafts.size) {
    const warning = node("div", undefined, "draft-bar");
    const controls = node("div", undefined, "offer-actions");
    controls.append(button("Оставить черновик", "keep"), button("Сбросить и загрузить", "reload"));
    warning.append(node("strong", "Загрузка заменит открытые черновики."), controls);
    blocks.push(warning);
  }
  if (campaign.streams.length === 0) {
    blocks.push(node("p", "В кампании нет потоков.", "editor-empty"));
  }
  blocks.push(...campaign.streams.map(drawStream));
  content.replaceChildren(...blocks);
}

async function perform(action, waiting) {
  if (busy) return;
  busy = true;
  const pending = notice("note", waiting);
  render();
  try {
    await action();
  } catch (error) {
    notice("bad", error.message);
  } finally {
    if (pending && message.firstElementChild === pending) notice();
    busy = false;
    render();
  }
}

function loadCampaign(id) {
  if (busy) return;
  if (drafts.size) {
    confirmation = id;
    render();
    content.querySelector('[data-action="keep"]').focus();
    return;
  }
  return reloadCampaign(id);
}

function reloadCampaign(id) {
  confirmation = null;
  return perform(async () => {
    campaign = await request(`/api/campaigns/${id}/sync`, "POST");
    campaignInput.value = id;
    drafts.clear();
    searches.clear();
    notice();
  }, "Загружаем потоки из Keitaro…");
}

export function openCampaign(id) {
  activateTab(tabs[1], true);
  campaignInput.value = id;
  return loadCampaign(id);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const id = idFrom(campaignInput.value);
  if (id === null) return notice("bad", "Укажите положительный ID кампании.");
  loadCampaign(id);
});

function streamPath(id) {
  return `/api/campaigns/${campaign.id}/streams/${id}`;
}

function updateConfirmed(stream) {
  campaign.streams = campaign.streams.map((existing) => existing.id === stream.id ? stream : existing);
}

function preview(streamId, revision, ids) {
  return request(streamPath(streamId) + "/draft", "POST", {revision, active_offer_ids: ids});
}

content.addEventListener("input", (event) => {
  if (event.target.name !== "offer_id") return;
  const streamId = Number(event.target.form.dataset.stream);
  const search = searches.get(streamId);
  if (search?.result) event.target.form.nextElementSibling?.remove();
  searches.set(streamId, {query: event.target.value});
});

content.addEventListener("submit", (event) => {
  event.preventDefault();
  const streamId = Number(event.target.dataset.stream);
  const value = event.target.elements.offer_id.value;
  const id = idFrom(value);
  if (id === null) return notice("bad", "Укажите положительный ID оффера.");
  searches.set(streamId, {query: value});
  perform(async () => {
    const result = await request(`/api/offers/${id}`);
    searches.set(streamId, {query: value, result});
  }, "Ищем оффер…").then(() => {
    if (searches.get(streamId)?.result) {
      content.querySelector(`[data-stream="${streamId}"] [data-action="add"]`)?.focus();
    }
  });
});

content.addEventListener("click", (event) => {
  const control = event.target.closest("button[data-action]");
  if (!control || busy) return;
  const {action} = control.dataset;
  if (action === "keep") {
    confirmation = null;
    render();
    loadButton.focus();
    return;
  }
  if (action === "reload") return reloadCampaign(confirmation);

  const streamId = Number(control.dataset.stream);
  const offerId = Number(control.dataset.id);
  const confirmed = campaign.streams.find(({id}) => id === streamId);
  const draft = drafts.get(streamId);
  const active = (draft ?? confirmed).active.map(({id}) => id);
  if (action === "cancel") {
    drafts.delete(streamId);
    notice();
    render();
    return;
  }
  if (action === "push") {
    return perform(async () => {
      const updated = await request(streamPath(streamId) + "/offers", "PUT", {
        revision: draft.revision,
        active_offer_ids: active,
      });
      updateConfirmed(updated);
      drafts.delete(streamId);
      searches.delete(streamId);
      notice("ok", "Изменения отправлены. Состав и веса подтверждены чтением из Keitaro.");
    }, "Отправляем изменения в Keitaro…");
  }
  if (action === "pin") {
    const current = confirmed.active.find(({id}) => id === offerId);
    return perform(async () => {
      const updated = await request(streamPath(streamId) + `/offers/${offerId}/pin`, "PUT", {
        revision: confirmed.revision,
        pinned: !current.pinned,
      });
      updateConfirmed(updated);
      if (draft) drafts.set(streamId, await preview(streamId, updated.revision, active));
    }, "Сохраняем закрепление…").then(() => {
      content.querySelector(`[data-stream="${streamId}"] [data-action="pin"][data-id="${offerId}"]`)?.focus();
    });
  }
  if (action === "remove" && active.length === 1) {
    return notice("bad", "В потоке должен остаться хотя бы один активный оффер.");
  }
  if (action === "add" && active.includes(offerId)) {
    return notice("bad", "Этот оффер уже активен в потоке.");
  }
  const requested = action === "remove" ? active.filter((id) => id !== offerId) : [...active, offerId];
  return perform(async () => {
    drafts.set(streamId, await preview(streamId, confirmed.revision, requested));
    if (action === "add") searches.delete(streamId);
  }, "Рассчитываем веса черновика…");
});
