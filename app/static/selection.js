const SEARCH_DELAY_MS = 180;

export function mountSelection(root) {
  const field = root.querySelector("input");
  const panel = root.querySelector(".selection-panel");
  const status = panel.querySelector(".selection-status");
  const options = panel.querySelector(".selection-options");
  let items = [];
  let highlighted = -1;
  let pending;
  let timer;

  function close() {
    clearTimeout(timer);
    pending?.abort();
    pending = undefined;
    items = [];
    highlighted = -1;
    panel.hidden = true;
    status.hidden = true;
    options.hidden = true;
    options.replaceChildren();
    field.setAttribute("aria-expanded", "false");
    field.removeAttribute("aria-activedescendant");
  }

  function showStatus(text, error = false) {
    status.textContent = text;
    status.dataset.error = String(error);
    status.hidden = false;
  }

  function choose(index) {
    field.value = String(items[index].id);
    close();
    field.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function renderOptions(results) {
    items = results;
    if (!items.length) {
      showStatus("Совпадений нет. Можно ввести точный ID.");
      return;
    }

    const rows = items.map(({ id, name }, index) => {
      const row = document.createElement("li");
      row.className = "selection-option";
      row.id = `${field.id}-option-${index}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", "false");
      const number = document.createElement("small");
      number.textContent = `№ ${id}`;
      const label = document.createElement("span");
      label.textContent = name;
      row.append(number, label);
      row.addEventListener("pointerdown", (event) => event.preventDefault());
      row.addEventListener("click", () => choose(index));
      return row;
    });
    status.hidden = true;
    options.replaceChildren(...rows);
    options.hidden = false;
  }

  async function find(url, operation) {
    try {
      const response = await fetch(url, { signal: operation.signal, cache: "no-store" });
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (!Array.isArray(result.items)) throw new Error();
      if (pending === operation) renderOptions(result.items);
    } catch {
      if (pending === operation) showStatus("Поиск недоступен. Введите точный ID.", true);
    }
  }

  function search() {
    close();
    panel.hidden = false;
    field.setAttribute("aria-expanded", "true");
    showStatus("Ищем варианты…");
    const url = new URL(root.dataset.selection, window.location.origin);
    url.searchParams.set("q", field.value);
    const operation = new AbortController();
    pending = operation;
    timer = setTimeout(() => find(url, operation), SEARCH_DELAY_MS);
  }

  function highlight(index) {
    options.children[highlighted]?.setAttribute("aria-selected", "false");
    highlighted = index;
    const row = options.children[index];
    row.setAttribute("aria-selected", "true");
    field.setAttribute("aria-activedescendant", row.id);
    row.scrollIntoView({ block: "nearest" });
  }

  field.addEventListener("focus", search);
  field.addEventListener("click", () => {
    if (panel.hidden) search();
  });
  field.addEventListener("input", search);
  field.addEventListener("blur", close);
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !panel.hidden) {
      event.preventDefault();
      if (highlighted >= 0) choose(highlighted);
    } else if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (panel.hidden) {
        search();
      } else if (items.length) {
        const forward = event.key === "ArrowDown";
        const next = highlighted < 0 && !forward
          ? items.length - 1
          : Math.max(0, Math.min(items.length - 1, highlighted + (forward ? 1 : -1)));
        highlight(next);
      }
    }
  });
}
