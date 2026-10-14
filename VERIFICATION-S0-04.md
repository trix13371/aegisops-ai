# S0-04 — краткая приёмка

14 октября 2026. Read-only collector и нормализация готовы.

- `observe --include-logs` проверен на всех 10 Pod локального kind.
- Полный прогон с PostgreSQL и kind: 208 passed, 1 skipped (Windows symlink).
- Ruff, mypy, import-linter, JSON Schema и package assets прошли проверку.
- pip-audit: известных уязвимостей зависимостей не найдено; локальный пакет
  не опубликован в PyPI. Gitleaks проверяет staged изменения и историю Git.
- DeepSeek подготовил основу сборщика, Gemini — отдельные тесты и ревью.
  Два замечания ревью не подтвердились: exit_code уже StrictInt с тестом
  на bool; path_params уже нормализованы и проверяются до отправки запроса.

Events привязаны к UID/namespace; страницы и UTF-8 логи ограничены.
Настоящий владелец ReplicaSet сохранён (ADR-006). CLI выводит только счётчики.
Очистка текста, детектор и контекст — S0-05. Удалённый CI не запускался.
