import { openCampaign } from "./editor.js";

const form = document.getElementById("create-form");
const createButton = document.getElementById("create");
const notice = document.getElementById("create-notice");
const country = document.getElementById("country");
const offer = document.getElementById("create-offer");
const theme = document.getElementById("theme");

theme.addEventListener("change", () => {
  document.documentElement.dataset.theme = theme.value;
});

function updateRoute() {
  const code = country.value.trim().toUpperCase();
  document.getElementById("country-route").textContent = code ? `${code} → Google` : "Google";
  document.getElementById("offer-route").textContent = offer.value
    ? `Оффер № ${offer.value}`
    : "Выбранный оффер";
}

country.addEventListener("input", updateRoute);
offer.addEventListener("input", updateRoute);

function showNotice(kind, title, description, campaignId) {
  const panel = document.createElement("div");
  panel.classList.add("message", kind);
  const heading = document.createElement("p");
  heading.className = "mark";
  heading.textContent = title;
  panel.append(heading);
  if (description) {
    const detail = document.createElement("p");
    detail.textContent = description;
    panel.append(detail);
  }
  if (campaignId !== undefined) {
    const open = document.createElement("button");
    open.type = "button";
    open.className = "button";
    open.textContent = "Открыть в редакторе";
    open.addEventListener("click", () => openCampaign(campaignId));
    panel.append(open);
  }
  notice.replaceChildren(panel);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (createButton.disabled) return;

  const offerId = Number(offer.value);
  if (!Number.isSafeInteger(offerId) || offerId < 1) {
    showNotice("bad", "Укажите положительный ID оффера.");
    offer.focus();
    return;
  }
  const payload = {
    name: form.elements.namedItem("name").value,
    country_code: country.value,
    offer_id: offerId,
  };

  createButton.disabled = true;
  showNotice("note", "Подтверждаем настройки кампании в Keitaro…");
  try {
    const response = await fetch("/api/campaigns", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      const error = result.error;
      showNotice(
        "bad",
        error.message,
        error.campaign_id == null ? "" : `Кампания № ${error.campaign_id}.`,
      );
      return;
    }
    const [redirectId, fallbackId] = result.stream_ids;
    showNotice(
      "ok",
      `Кампания № ${result.campaign_id} создана и включена.`,
      `Поток на Google № ${redirectId}. Поток на оффер № ${fallbackId}.`,
      result.campaign_id,
    );
  } catch {
    showNotice("bad", "Ответ не получен. Проверьте кампанию в Keitaro перед повтором.");
  } finally {
    createButton.disabled = false;
  }
});
