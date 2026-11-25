# Keitaro Wrapper

## Где искать требования

- [Исходное задание и вложения](docs/assignment/README.md).
- [Создание кампаний](docs/specs/campaign/spec.md), [редактор офферов](docs/specs/editor/spec.md) и [материалы сдачи](docs/specs/delivery/spec.md).
- [Архитектура](docs/ARCHITECTURE.md), [архитектурные решения](docs/decisions) и [трассировка разработки](TRACE.md).

## Как работать с проектом

- Точка входа FastAPI — `app/main.py`. Прикладные сценарии находятся в `app/campaigns.py` и `app/editor.py`, HTTP-клиент Keitaro — в `app/keitaro.py`, интерфейс — в `app/templates/` и `app/static/`.
- Модель SQLite находится в `app/storage.py`, история схемы — в `migrations/`. Конфигурация и запуск описаны в [README](README.md#быстрый-запуск).
- При изменении поведения обновляйте соответствующую спецификацию. Для проверки используйте проектный lockfile. Тесты подставляют ответы Keitaro и работают без выданного ключа.
- Храните свои параметры подключения в локальном `.env`. Для проб записи используйте свою учебную кампанию.

```bash
uv sync --locked
uv run --locked --no-env-file pytest -q
uv run --locked --no-env-file ruff check
uv run --locked --no-env-file ruff format --check
```
