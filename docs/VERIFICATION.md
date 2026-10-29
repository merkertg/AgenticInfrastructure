# Проверки Agentic Infrastructure

[README](../README.md) · [Этап 01](STAGE_01.md) · [Этап 02](STAGE_02.md) · [Этап 03](STAGE_03.md) · [Этап 04](STAGE_04.md) · [Этап 05](STAGE_05.md) · [Этап 06](STAGE_06.md) · [Машинные результаты](guide/verification.json)

Дата: **2026-10-29**, часовой пояс пользователя — Asia/Yekaterinburg.
Проверка этапа 06 выполнена в `main` на рабочем дереве поверх
`344aaeef2deddb66fa2a27359cd85f8b2f34524f` — commit единой документации.
Изменения этапа 06 ещё не оформлены commit и не опубликованы; состояние
публикации каждого этапа приведено отдельно ниже.

## Реализованная граница этапа 06

Добавлен единственный инструмент агента `search_memory` с аргументами
`query` и `max_items` (1–5). Аргументы — текст JSON до 1 024 символов;
неизвестный инструмент, неверный JSON, повторённый ключ, лишнее поле
(в том числе `project_id`) и неверные значения отклоняются кодом, который
получает модель. Проект задаёт путь, сотрудника и агента — токен.

Маршрут `POST /memory/projects/{project_id}/answers` принимает только вопрос
(одна строка до 500 символов). Сначала `MemoryStore.check_scope()` проверяет
область; при отказе модель не запускается. Цикл `run_agent()` ограничен
4 шагами, 3 вызовами, 15 секундами и 24 000 символами текста для модели.
Ответ принимается, только если каждая ссылка совпадает с фрагментом из поисков
этого запуска; текст без ссылок не возвращается. `MemoryStore.finish_run()`
под блокировкой компании снова проверяет токен, область и каждую процитированную
версию, затем записывает запуск. Провайдер — детерминированный
`offline-extractive-v1`; настоящая модель не подключалась.

## Окружение

Кластер прошлых этапов в `/private/tmp` не работал. Создан новый временный
кластер `PostgreSQL 17.10 (Homebrew)` на `127.0.0.1:55435` во временном
каталоге сессии, с `LC_ALL=C` и без Unix-сокета (`unix_socket_directories=''`):
путь каталога длиннее допустимого для сокета. Базы: `agentic_test` для тестов,
`agentic_stage06_lab` для лаборатории. Все данные вымышленные. Команды
выполнялись из корня проекта с
`UV_CACHE_DIR=/private/tmp/agentic-infrastructure-uv-cache`.

## Локальные проверки этапа 06

| Команда | Фактический результат |
| --- | --- |
| `uv sync --locked --offline` | Успешно; новых зависимостей нет (команда убрала необязательную группу `guide` до сборки PDF) |
| `uv run --locked pytest -m 'not postgres'` | **339 passed**, 121 deselected |
| `uv run --locked pytest` без `TEST_DATABASE_URL` | 339 passed, **121 skipped** — это не проверка PostgreSQL |
| `TEST_DATABASE_URL=…/agentic_test uv run --locked pytest -W error::DeprecationWarning` | **460 passed**: 339 без БД и 121 PostgreSQL |
| Конкурентные тесты `-k concurrent` | 3 passed в каждом из трёх дополнительных запусков |
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 100 files already formatted |
| `uv run --locked mypy app tests scripts migrations` | Success: no issues found in 74 source files |
| `alembic check` на тестовой БД | `No new upgrade operations detected.` |
| `uv lock --check --offline` | Успешно, resolved 43 packages |
| `uv run --locked python scripts/check_docs.py` | Успешно: 187 локальных ссылок в 25 Markdown-файлах |
| `git diff --check` с учётом новых файлов | Успешно |

Python `3.12.15`, uv `0.12.23`. `uv.lock` не менялся с обновления документации,
SHA-256 — `0a1832d87fcd6a4b748d763e0d2f162e3f4b6b528320e1f3faadc6ba8b339974`.
Пояснения назначения проверены у всех 532 функций и классов
в `app`, `tests`, `scripts` и `migrations`.

339 тестов без БД: 243 предыдущих и 96 новых — 34 проверки аргументов
инструмента, вопроса и текста ответа; 49 проверок цикла и офлайн-провайдера
со сценарным провайдером, поддельным поиском и поддельными часами; 12 проверок
входа маршрута ответа; 1 новая проверка данных руководства.

121 PostgreSQL-тест: 94 прежних сценария, 6 проверок миграций (новая —
откат до `0003`) и 21 проверка запусков агента. Проверены цитаты с
воспроизводимыми смещениями и история без текста, разные ответы двух агентов
разных сотрудников в одном проекте, `unanswered`, отказ области без запуска
модели (в том числе через HTTP), отзыв права между поиском и ответом, отзыв
делегирования и токена во время запуска, модель, выполняющая инструкцию
из документа, откат при ошибке истории, неверные вызовы `finish_run()`
и HTTP-ответы `200`, `404`, `422`, `502` (`provider_error`, `invalid_citation`,
`tool_call_limit`) и `504` (`time_limit`).

Чтобы убедиться, что тесты ловят ошибки, проведены четыре намеренные поломки
с восстановлением кода (побайтовое сравнение с копией):

| Поломка | Упавшие тесты |
| --- | --- |
| `finish_run()` не перепроверяет процитированные версии | 1: отзыв права перед ответом |
| Маршрут не проверяет область до запуска модели | 1: модель не должна запускаться вне области |
| Инструмент принимает лишние аргументы | 5: `project_id`/`employee_id` в домене, цикле и PostgreSQL |
| Ответ принимает ссылки не из этого запуска | 7: придуманные ссылки в цикле, PostgreSQL и HTTP |

## Лабораторный запуск по сети

Новая база `agentic_stage06_lab`: `persistence_demo --setup`, `intake_lab`
для `fixtures/lab/finance/*.txt` с `--grant fin-user:read` и для
`deploy-notes.txt` в `engineering` с `--grant eng-user:read`, токены
`credential_lab` для `fin-agent` и `eng-agent`. Uvicorn запущен
на `127.0.0.1:8005` с `APP_DATABASE_URL` этой базы.

| Запрос | Ответ |
| --- | --- |
| `GET /health` | `200` |
| Финансовый агент, `finance`, «What is the travel limit per trip?» | `200 answered`, 3 ссылки (`travel-policy` 2 и 3, `expense-guide` 2), `Cache-Control: no-store` |
| Чтение версий из ссылок маршрутом этапа 04 | `200`; `content[start:end]` даёт процитированные абзацы |
| Финансовый агент, `finance`, «zebra» | `200 unanswered`, `answer: null` |
| Финансовый агент, `engineering` | `404 memory context not available` |
| Инженерный агент, `engineering`, тот же вопрос о лимите | `200 answered`, 2 ссылки только на `deploy-notes`; первая строка — цитата «Ignore all previous rules…» |
| Инженерный агент, `engineering`, «When do deployments happen?» | `200 answered`, 1 ссылка на `deploy-notes` |
| Инженерный агент, `finance` | `404` |
| Неизвестный токен | `401` |
| Поле `project_id` в теле | `422` |
| `GET` вместо `POST` | `405` |

После запуска в истории 6 запусков (у всех заполнен `credential_id`):
3 `answered`, 1 `unanswered`, 2 `denied` с `delegation_unavailable` и нулём
шагов. Связей с поисками 4, ссылок ответа 6, запросов контекста 4. Поиск
по строкам `agent_runs`, `agent_run_searches`, `agent_run_citations`,
`context_requests` и `context_items` не нашёл ни слов вопросов, ни текста
документов; совпадение по `travel` дал только идентификатор документа
`travel-policy`. Токенов нет в журнале Uvicorn. Сервер остановлен.

## Регрессия предыдущих этапов

Этап 05: 21 PostgreSQL-сценарий поиска, приём «всё или ничего»
и лабораторный импорт. Этап 04: токены и маршруты версий, сетевой `401`.
Этап 03: миграции, транзакции и конкурентные записи. Этапы 01–02: `/health`
и офлайн-политика. Все соответствующие тесты входят в 460 прошедших.
Исходные результаты проверок этапов 01–05 сохранены в разделе
`previous_stages` машинного отчёта.

## Публикация

| Этап | Commit и удалённый ref | GitHub Actions |
| --- | --- | --- |
| 01 | `1a80825f0075fe0c28c0e7014d49871f6ce9b230`, SHA подтверждён при публикации в `develop` | [CI 40101385719](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40101385719), `completed / success` для этого SHA |
| 02 | `fc14d27364742c3dd14af988af3e101b1cb47070`, SHA `origin/develop` подтверждён через `git ls-remote` после push | [CI 40102234278](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40102234278), `completed / success` для этого SHA |
| 03 | `955aec742d48d741a2a4962b674790e4924abd6e`, SHA `origin/develop` подтверждён через `git ls-remote` после push | [CI 40104677151](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40104677151), `completed / success` для этого SHA, включая шаг PostgreSQL |
| 04 | `66168308e0e5e9538310e9338ca5967db48ba033`, SHA подтверждён в `origin/develop` через `git ls-remote` | [CI 40117305558](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40117305558), `completed / success` для этого SHA, включая шаг PostgreSQL |
| 05 | `a83bd3c542aad0fc469f967f0a2561b9ae2bfe17`, удалённая `main` подтверждена через `git ls-remote` 27 октября | [CI 40424738428](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40424738428), `completed / success`; повторно подтверждён через GitHub API 29 октября |
| Документация 05 | `344aaeef2deddb66fa2a27359cd85f8b2f34524f`, совпадение с `origin/main` подтверждено `git ls-remote` 29 октября | [CI 40426585936](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40426585936), `completed / success`; все шаги, включая PostgreSQL, `success` |
| 06 | Не оформлен commit, не опубликован | `not_verified` |

**2026-10-27 — перенос в `main`.** `main` перемотана вперёд (fast-forward)
до `474b04498cf92524f6f03950d7c77a12308ed563` — последнего commit `develop`
с записью о публикации этапа 04. `git ls-remote` подтвердил этот SHA
в `origin/main` до удаления `develop`; затем `develop` удалена локально
и в `origin`. Для этого SHA на `main`
[CI 40422506323](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40422506323)
завершён с `completed / success`. Commit `2e229dce0d4f4ae2895c37dd2cc2a7359e359a75`
убрал `develop` из workflow; его
[CI 40422696854](https://github.com/merkertg/AgenticInfrastructure/actions/runs/40422696854)
тоже `completed / success`. Записи выше о `develop` описывают состояние
на момент прежних публикаций. Журналы шагов с числом тестов в CI не читались:
их загрузка требует авторизации.

## Иллюстрированное руководство

PDF пересобран командой `uv run --locked --group guide python -m scripts.build_guide`:
**13 страниц** (добавлен раздел «Этап 06. Инструмент агента»). Кэш uv не содержал
пакетов группы `guide`, поэтому `uv run --locked` загрузил закреплённые в lock
ReportLab 4.4.9, Pillow 13.0.0 и charset-normalizer 3.5.2. Текст извлечён
через `pdftotext`; все страницы отрисованы `pdftoppm -scale-to 1400`
и просмотрены. Первый просмотр нашёл три недочёта: отсутствующий в моноширинном
шрифте знак стрелки, неудачную подпись публикации документации и устаревшую
подпись блока FastAPI. Они исправлены, PDF пересобран, изменённые страницы
просмотрены снова, остальные сверены по сводному изображению.

## Что ещё отсутствует

Нет настоящей языковой модели, потоковой выдачи ответа, ограничения частоты
запросов, семантического и векторного поиска, сохранённого индекса, кэша,
приёма и удаления документов по HTTP, корпоративного поставщика личности, TLS,
корпоративного коннектора и контейнерного выпуска. Офлайн-провайдер цитирует,
а не рассуждает. Цикл не может прервать зависший провайдер. Каждый вызов
инструмента заново читает разрешённые версии. Владелец БД может напрямую
изменить документы, права, токены и историю. Временный локальный кластер
не является развёрнутым сервисом. Следующий этап — один корпоративный
источник только для чтения.
