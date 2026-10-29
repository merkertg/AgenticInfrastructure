# Наглядное руководство Agentic Infrastructure

[← README](../../README.md) · [Все материалы](../../README.md#documentation)

[PDF-руководство](agentic-infrastructure-guide.pdf) объясняет этапы 01–06:
архитектуру, права сотрудника и агента, транзакции PostgreSQL, bearer-токены,
приём документов, поиск и ограниченный инструмент агента. Оно дополняет
подробные русские руководства [01](../STAGE_01.md), [02](../STAGE_02.md),
[03](../STAGE_03.md), [04](../STAGE_04.md), [05](../STAGE_05.md),
[06](../STAGE_06.md).

## Исходники и данные

- [Генератор](../../scripts/build_guide.py) создаёт PDF только по явной команде.
- [Загрузчик](../../scripts/guide_data.py) проверяет типы, суммы тестов и
  явные статусы публикации без импорта приложения или ReportLab.
- [verification.json](verification.json) хранит проверки этапа 06, короткую
  подпись `guide_note` и исходные проверки этапов 01–05 в `previous_stages`.
- [Отчёт](../VERIFICATION.md) объясняет фактические результаты и ограничения.

## Пересборка

Из корня проекта с Python 3.12 и uv:

```bash
uv sync --locked --group guide
uv run --locked --group guide python -m scripts.build_guide
```

Отдельная группа `guide` содержит ReportLab 4.4.9 и Pillow 13. Обычный запуск
сервиса их не требует. Для Pillow сохранено узкое исключение из порога новых
выпусков до 17 октября; общий порог остальных пакетов не изменён.
См. [выпуск Pillow 13](https://github.com/python-pillow/Pillow/releases/tag/13.0.0).
Lock изменён только для этой группы сборки и её зависимостей.

На macOS используются системные PT Sans и PT Mono; на Linux нужны DejaVu Sans
и DejaVu Sans Mono (`fonts-dejavu-core`). При отсутствии шрифтов команда
завершается с понятной ошибкой. Перед заменой существующего PDF генератор
сохраняет его копию во временном каталоге системы. Сначала он полностью
формирует временный PDF, затем атомарно заменяет целевой файл.

Можно указать отдельные пути:

```bash
uv run --locked --group guide python -m scripts.build_guide \
  --verification docs/guide/verification.json \
  --output /tmp/agentic-infrastructure-guide.pdf
```

## Проверка результата

1. Выполни [проверки проекта](../instructions/testing.md), включая тесты
   `tests/test_guide_data.py`. Укажи реальные пропуски PostgreSQL отдельно.
2. Проверь текст и число страниц через `pdfinfo` и `pdftotext`.
3. Отрисуй все страницы: `pdftoppm -scale-to 1400 -png PDF /tmp/agentic-page`.
   Просмотри каждую: кириллица, таблицы, стрелки, поля, подписи и номера страниц
   должны быть читаемыми, без обрезки и наложения.
4. Запиши дату, результаты и ограничения в отчёт, журнал и JSON.

Генератор не выполняет тесты и не подтверждает CI. Исторические результаты
PostgreSQL и Actions не становятся новой проверкой из-за пересборки PDF.
В CI PDF пока не собирается; контейнерный выпуск остаётся планом этапа 08.
