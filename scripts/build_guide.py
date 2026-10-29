"""Build the illustrated Russian PDF guide to Agentic Infrastructure."""

import argparse
import shutil
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.guide_data import GuideDataError, GuideFacts, load_facts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
W = PAGE_W - 2 * MARGIN

HEX = colors.HexColor
INK = HEX("#0F172A")
TEXT = HEX("#1E293B")
MUTED = HEX("#64748B")
LINE = HEX("#CBD5E1")
SOFT = HEX("#F1F5F9")
WHITE = colors.white
BLUE = HEX("#2563EB")
BLUE_L = HEX("#DBEAFE")
GREEN = HEX("#15803D")
GREEN_L = HEX("#DCFCE7")
AMBER = HEX("#B45309")
AMBER_L = HEX("#FEF3C7")
RED = HEX("#B91C1C")
RED_L = HEX("#FEE2E2")
VIOLET = HEX("#6D28D9")
VIOLET_L = HEX("#EDE9FE")

LIMITATIONS = (
    "Лексический BM25 находит только совпадающие слова, без синонимов и смысла.",
    "Каждый поиск заново читает разрешённые версии; учитываются до 200 документов.",
    "Приём документов — привилегированная настройка лаборатории, не HTTP.",
    "Bearer-токен действует как его агент до истечения или отзыва; только loopback.",
    "Хеш короткого запроса или вопроса в истории можно подобрать.",
    "Офлайн-провайдер цитирует найденные фрагменты, а не рассуждает.",
    "Цикл агента не может прервать зависший провайдер; лимита частоты нет.",
    "Источник Handbook вымышленный; реальный корпоративный сервис не проверялся.",
    "Отзыв прав в источнике виден после синхронизации или истечения подтверждения.",
    "Согласованность списка и неизменность текста одной revision задаёт контракт.",
    "Системный DNS может оставить поток после таймаута; сетевых потоков до четырёх.",
    "Удалённые версии остаются в БД для истории, но закрыты для чтения и поиска.",
    "Откат выключает зеркала; повторный upgrade не восстанавливает владение источника.",
    "Владелец PostgreSQL может напрямую изменить документы, права и историю.",
    "Compose-лаборатория и временный кластер — не развёрнутый сервис.",
    "Compose подключается к БД суперпользователем; секреты — в переменных окружения.",
    "Оценки покрывают только описанное поведение; проверка цитат — для офлайн-модели.",
    "Учения требуют прав владельца БД и показывают обнаружение, а не защиту.",
    "Замеры описывают один ноутбук и несколько вымышленных документов.",
)

SECTIONS = (
    ("Проект за минуту", "что строим и что уже работает"),
    ("Архитектура и путь запроса", "слои, модули и границы доверия"),
    ("Этап 01. Основа API", "FastAPI, настройки и /health"),
    ("Этап 02. Правила доступа", "пересечение прав сотрудника и агента"),
    ("Этап 03. PostgreSQL и аудит", "версии, транзакции и повторы"),
    ("Этап 04. Личность агента", "bearer-токены и API памяти"),
    ("Этап 05. Приём и поиск", "фрагменты, BM25 и фильтр прав"),
    ("Этап 06. Инструмент агента", "один инструмент, лимиты и ссылки"),
    ("Этап 07. Источник для чтения", "Handbook, синхронизация и срок доступа"),
    ("Этап 08. Контейнеры и оценки", "Compose, оценки, копия и восстановление"),
    ("Практика: полный сценарий", "команды лаборатории по порядку"),
    ("Проверки, CI и публикация", "фактические результаты"),
    ("Типичные ошибки", "симптом и что проверить"),
    ("Ограничения и дальше", "что не реализовано и что дальше"),
)


def register_fonts() -> None:
    """Register Cyrillic PT fonts on macOS or DejaVu fonts on Linux."""
    mac_sans = Path("/System/Library/Fonts/Supplemental/PTSans.ttc")
    mac_mono = Path("/System/Library/Fonts/Supplemental/PTMono.ttc")
    if mac_sans.is_file() and mac_mono.is_file():
        files = {
            "Sans": (mac_sans, 0),
            "Sans-Bold": (mac_sans, 7),
            "Sans-Italic": (mac_sans, 1),
            "Sans-BoldItalic": (mac_sans, 6),
            "Mono": (mac_mono, 1),
            "Mono-Bold": (mac_mono, 0),
        }
    else:
        root = Path("/usr/share/fonts/truetype/dejavu")
        files = {
            "Sans": (root / "DejaVuSans.ttf", 0),
            "Sans-Bold": (root / "DejaVuSans-Bold.ttf", 0),
            "Sans-Italic": (root / "DejaVuSans-Oblique.ttf", 0),
            "Sans-BoldItalic": (root / "DejaVuSans-BoldOblique.ttf", 0),
            "Mono": (root / "DejaVuSansMono.ttf", 0),
            "Mono-Bold": (root / "DejaVuSansMono-Bold.ttf", 0),
        }
    missing = [str(path) for path, _ in files.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Не найдены шрифты с кириллицей: " + ", ".join(missing))
    for name, (path, index) in files.items():
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=index))
    pdfmetrics.registerFontFamily(
        "Sans",
        normal="Sans",
        bold="Sans-Bold",
        italic="Sans-Italic",
        boldItalic="Sans-BoldItalic",
    )
    pdfmetrics.registerFontFamily("Mono", normal="Mono", bold="Mono-Bold")


BODY = ParagraphStyle("body", fontName="Sans", fontSize=10, leading=14, textColor=TEXT)
BODY_S = ParagraphStyle("small", parent=BODY, fontSize=8.6, leading=11.6)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.4, leading=11)
CELL_H = ParagraphStyle("cellh", parent=CELL, fontName="Sans-Bold", textColor=WHITE)
H2 = ParagraphStyle(
    "h2",
    parent=BODY,
    fontName="Sans-Bold",
    fontSize=12,
    leading=16,
    textColor=INK,
    spaceBefore=8,
    spaceAfter=4,
)
CODE = ParagraphStyle(
    "code", fontName="Mono", fontSize=7.8, leading=10.4, textColor=INK
)


def p(text: str, style: ParagraphStyle = BODY) -> Any:
    """Make a paragraph from trusted markup written in this module."""
    return Paragraph(text, style)


def mono(text: str) -> str:
    """Wrap escaped text in the monospace font for inline code."""
    return f'<font name="Mono">{escape(text)}</font>'


def code_block(text: str) -> Any:
    """Show commands in a shaded box that keeps line breaks."""
    block = Preformatted(text.strip("\n"), CODE)
    table = Table([[block]], colWidths=[W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def grid(rows: list[list[str]], widths: list[float], head: Any = INK) -> Any:
    """Render a zebra table whose first row is a header."""
    data = [
        [p(cell, CELL_H if index == 0 else CELL) for cell in row]
        for index, row in enumerate(rows)
    ]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), head),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row in range(2, len(rows), 2):
        style.append(("BACKGROUND", (0, row), (-1, row), SOFT))
    table.setStyle(TableStyle(style))
    return table


def callout(text: str, color: Any = BLUE, fill: Any = BLUE_L) -> Any:
    """Highlight one key idea with a colored left edge."""
    table = Table([[p(text, BODY_S)]], colWidths=[W])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


class Diagram(Flowable):
    """Reserve space and draw a schematic with canvas primitives."""

    def __init__(self, height: float, draw: Callable[[Any, float, float], None]):
        """Store the drawing callback and its fixed height."""
        super().__init__()
        self.height = height
        self.draw_function = draw

    def wrap(
        self, available_width: float, available_height: float
    ) -> tuple[float, float]:
        """Use the full content width and the configured height."""
        return W, self.height

    def draw(self) -> None:
        """Delegate drawing to the callback in local coordinates."""
        self.draw_function(self.canv, W, self.height)


class SectionTitle(Flowable):
    """Draw a numbered section banner with a subtitle."""

    def __init__(self, number: int, title: str, subtitle: str, color: Any):
        """Keep banner text and accent color."""
        super().__init__()
        self.number, self.title, self.subtitle, self.color = (
            number,
            title,
            subtitle,
            color,
        )

    def wrap(
        self, available_width: float, available_height: float
    ) -> tuple[float, float]:
        """Occupy one banner row across the content width."""
        return W, 46

    def draw(self) -> None:
        """Paint the number badge, title and subtitle."""
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 6, 34, 34, 6, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont("Sans-Bold", 15)
        c.drawCentredString(17, 17, f"{self.number:02d}")
        c.setFillColor(INK)
        c.setFont("Sans-Bold", 16)
        c.drawString(44, 24, self.title)
        c.setFillColor(MUTED)
        c.setFont("Sans", 9.5)
        c.drawString(44, 9, self.subtitle)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(44, 2, W, 2)


def box(
    c: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    lines: tuple[str, ...] = (),
    fill: Any = BLUE_L,
    edge: Any = BLUE,
) -> None:
    """Draw a rounded node with a bold title and centered detail lines."""
    c.setFillColor(fill)
    c.setStrokeColor(edge)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 6, stroke=1, fill=1)
    total = 12 + 10 * len(lines)
    top = y + h / 2 + total / 2 - 10
    c.setFillColor(INK)
    c.setFont("Sans-Bold", 9)
    c.drawCentredString(x + w / 2, top, title)
    c.setFont("Sans", 7.6)
    c.setFillColor(TEXT)
    for index, line in enumerate(lines, start=1):
        c.drawCentredString(x + w / 2, top - 11 * index, line)


def arrow(
    c: Any,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: Any = MUTED,
    label: str = "",
) -> None:
    """Draw a straight arrow with an optional label at its midpoint."""
    import math

    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.2)
    c.line(x1, y1, x2, y2)
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 5.5
    left = (
        x2 - size * math.cos(angle - 0.45),
        y2 - size * math.sin(angle - 0.45),
    )
    right = (
        x2 - size * math.cos(angle + 0.45),
        y2 - size * math.sin(angle + 0.45),
    )
    path = c.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(*left)
    path.lineTo(*right)
    path.close()
    c.drawPath(path, stroke=0, fill=1)
    if label:
        c.setFont("Sans", 7)
        c.setFillColor(MUTED)
        c.drawCentredString((x1 + x2) / 2, (y1 + y2) / 2 + 4, label)


def architecture(c: Any, w: float, h: float) -> None:
    """Show the request path from the agent to PostgreSQL and the domain rules."""
    bw = 134
    gap = (w - 3 * bw) / 2
    xs = (0, bw + gap, 2 * (bw + gap))
    top, bottom, bh = h - 66, 14, 56
    box(c, xs[0], top, bw, bh, "Агент сотрудника", ("HTTP-клиент", "Bearer aik1_…"))
    box(
        c,
        xs[1],
        top,
        bw,
        bh,
        "FastAPI",
        ("app/api: memory, answers", "маршруты /memory"),
    )
    box(
        c,
        xs[2],
        top,
        bw,
        bh,
        "Проверка токена",
        ("SHA-256, поиск в agent_credentials", "результат: Principal"),
        VIOLET_L,
        VIOLET,
    )
    box(
        c,
        xs[0],
        bottom,
        bw,
        bh,
        "Предметные правила",
        ("evaluate_scope / evaluate_access", "фрагменты, BM25"),
        GREEN_L,
        GREEN,
    )
    box(
        c,
        xs[1],
        bottom,
        bw,
        bh,
        "MemoryStore",
        ("READ COMMITTED", "блокировка компании"),
        AMBER_L,
        AMBER,
    )
    box(
        c,
        xs[2],
        bottom,
        bw,
        bh,
        "PostgreSQL",
        ("версии, права, токены", "аудит и история"),
        SOFT,
        MUTED,
    )
    arrow(c, xs[0] + bw, top + bh / 2, xs[1], top + bh / 2, label="запрос")
    arrow(c, xs[1] + bw, top + bh / 2, xs[2], top + bh / 2, label="токен")
    arrow(c, xs[2] + bw / 2, top, xs[1] + bw / 2 + 30, bottom + bh, label="Principal")
    arrow(c, xs[1], bottom + bh / 2, xs[0] + bw, bottom + bh / 2, label="решение")
    arrow(c, xs[1] + bw, bottom + bh / 2, xs[2], bottom + bh / 2, label="SQL")


def access_rule(c: Any, w: float, h: float) -> None:
    """Show that access is the intersection of employee, agent and scope rules."""
    left_w, right_x = 250, 310
    rows = (
        ("Право сотрудника на документ", "grants: read / write, активное"),
        ("Делегирование агенту в проекте", "delegations: действие, активное"),
        ("Область запроса", "компания, проект, агент сотрудника"),
    )
    for index, (title, line) in enumerate(rows):
        y = h - 46 - index * 50
        box(c, 0, y, left_w, 40, title, (line,))
        arrow(c, left_w, y + 20, right_x, h / 2)
    box(
        c,
        right_x,
        h / 2 - 34,
        w - right_x,
        68,
        "Решение AccessDecision",
        ("всё выполнено: allowed", "иначе отказ с кодом причины"),
        GREEN_L,
        GREEN,
    )


def numbered_flow(
    steps: tuple[tuple[str, str], ...], fill: Any, edge: Any
) -> Callable[[Any, float, float], None]:
    """Lay out numbered steps left-to-right, then right-to-left on a second row."""

    def draw(c: Any, w: float, h: float) -> None:
        """Draw boxes in a zigzag with arrows between consecutive steps."""
        per_row = 3
        bw = 140
        gap = (w - per_row * bw) / (per_row - 1)
        bh = 46
        centers: list[tuple[float, float, float]] = []
        for index, (title, line) in enumerate(steps):
            row, col = divmod(index, per_row)
            if row % 2:
                col = per_row - 1 - col
            x = col * (bw + gap)
            y = h - bh - 6 - row * (bh + 26)
            box(c, x, y, bw, bh, f"{index + 1}. {title}", (line,), fill, edge)
            centers.append((x, y, row))
        for (x1, y1, r1), (x2, y2, r2) in zip(centers, centers[1:], strict=False):
            if r1 == r2:
                if x2 > x1:
                    arrow(c, x1 + bw, y1 + bh / 2, x2, y2 + bh / 2)
                else:
                    arrow(c, x1, y1 + bh / 2, x2 + bw, y2 + bh / 2)
            else:
                arrow(c, x1 + bw / 2, y1, x2 + bw / 2, y2 + bh)

    return draw


def token_flow(c: Any, w: float, h: float) -> None:
    """Show credential verification and the in-transaction recheck."""
    bw, bh = 112, 48
    gap = (w - 4 * bw) / 3
    labels = (
        ("Заголовок", ("Authorization:", "Bearer aik1_…")),
        ("SHA-256", ("токен в БД", "не хранится")),
        ("agent_credentials", ("active, expires_at", "агент + сотрудник")),
        ("Principal", ("company, employee,", "agent, credential")),
    )
    top = h - bh - 4
    for index, (title, lines) in enumerate(labels):
        x = index * (bw + gap)
        box(c, x, top, bw, bh, title, lines, VIOLET_L, VIOLET)
        if index:
            arrow(c, x - gap, top + bh / 2, x, top + bh / 2)
    box(
        c,
        0,
        6,
        w * 0.48,
        44,
        "Любой сбой проверки: 401",
        ("одно тело ответа, без бизнес-аудита",),
        RED_L,
        RED,
    )
    box(
        c,
        w * 0.52,
        6,
        w * 0.48,
        44,
        "MemoryStore проверяет снова",
        ("под блокировкой; отзыв даёт credential_unavailable",),
        AMBER_L,
        AMBER,
    )
    arrow(c, 3 * (bw + gap) + bw / 2, top, w * 0.76, 50)


def roadmap(c: Any, w: float, h: float, done: int) -> None:
    """Draw eight stages with implemented ones highlighted."""
    names = (
        "API",
        "Правила",
        "PostgreSQL",
        "Личность",
        "Поиск",
        "Инструмент",
        "Источник",
        "Выпуск",
    )
    step = w / len(names)
    y = h - 30
    c.setStrokeColor(LINE)
    c.setLineWidth(2)
    c.line(step / 2, y, w - step / 2, y)
    for index, name in enumerate(names, start=1):
        x = step * (index - 0.5)
        ready = index <= done
        c.setFillColor(GREEN if ready else WHITE)
        c.setStrokeColor(GREEN if ready else MUTED)
        c.setLineWidth(1.4)
        c.circle(x, y, 13, stroke=1, fill=1)
        c.setFillColor(WHITE if ready else MUTED)
        c.setFont("Sans-Bold", 9)
        c.drawCentredString(x, y - 3, f"{index:02d}")
        c.setFillColor(INK if ready else MUTED)
        c.setFont("Sans", 7.8)
        c.drawCentredString(x, y - 28, name)
        c.drawCentredString(x, y - 39, "готово" if ready else "план")


def stat_tiles(
    c: Any, x: float, y: float, w: float, items: list[tuple[str, str]]
) -> None:
    """Draw equal tiles with a large value and a small caption."""
    gap = 10
    tile = (w - gap * (len(items) - 1)) / len(items)
    for index, (value, caption) in enumerate(items):
        left = x + index * (tile + gap)
        c.setFillColor(HEX("#1E3A8A"))
        c.roundRect(left, y, tile, 64, 8, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont("Sans-Bold", 22)
        c.drawCentredString(left + tile / 2, y + 30, value)
        c.setFillColor(BLUE_L)
        c.setFont("Sans", 8)
        c.drawCentredString(left + tile / 2, y + 13, caption)


def cover_painter(facts: GuideFacts) -> Callable[[Any, Any], None]:
    """Create the cover page painter bound to the verified facts."""

    def paint(c: Any, doc: Any) -> None:
        """Draw title, verified numbers and the table of contents."""
        c.saveState()
        c.setFillColor(INK)
        c.rect(0, PAGE_H - 250, PAGE_W, 250, stroke=0, fill=1)
        c.setFillColor(BLUE_L)
        c.setFont("Sans", 11)
        c.drawString(
            MARGIN, PAGE_H - 70, "Учебный проект · вымышленные компании Alpha и Beta"
        )
        c.setFillColor(WHITE)
        c.setFont("Sans-Bold", 30)
        c.drawString(MARGIN, PAGE_H - 112, "Agentic Infrastructure")
        c.setFont("Sans", 15)
        c.drawString(
            MARGIN, PAGE_H - 138, "Общая корпоративная память для агентов сотрудников"
        )
        c.setFont("Sans", 10)
        c.setFillColor(LINE)
        c.drawString(
            MARGIN,
            PAGE_H - 162,
            f"Наглядное руководство · этапы 01–{facts.stage:02d} из 08 реализованы",
        )
        stat_tiles(
            c,
            MARGIN,
            PAGE_H - 238,
            W,
            [
                (str(facts.tests_total), "тестов прошло"),
                (str(facts.tests_unit), "без базы данных"),
                (str(facts.tests_postgres), "с PostgreSQL"),
                (f"{facts.stage}/8", "этапов готово"),
            ],
        )
        c.setFillColor(INK)
        c.setFont("Sans-Bold", 13)
        c.drawString(MARGIN, PAGE_H - 290, "Содержание")
        column = W / 2
        for index, (title, subtitle) in enumerate(SECTIONS, start=1):
            col, row = divmod(index - 1, 7)
            x = MARGIN + col * column
            y = PAGE_H - 320 - row * 38
            c.setFillColor(BLUE)
            c.roundRect(x, y - 4, 22, 18, 4, stroke=0, fill=1)
            c.setFillColor(WHITE)
            c.setFont("Sans-Bold", 9)
            c.drawCentredString(x + 11, y + 1, f"{index:02d}")
            c.setFillColor(INK)
            c.setFont("Sans-Bold", 10)
            c.drawString(x + 30, y + 4, title)
            c.setFillColor(MUTED)
            c.setFont("Sans", 8)
            c.drawString(x + 30, y - 7, subtitle)
        c.setStrokeColor(LINE)
        c.line(MARGIN, 108, PAGE_W - MARGIN, 108)
        c.setFillColor(MUTED)
        c.setFont("Sans", 8.5)
        notes = (
            f"Проверка: {facts.checked_at}. Исходный commit: {facts.base_commit[:12]}.",
            f"Числа этапа {facts.stage:02d} взяты из verification.json; "
            "публикация и CI — в разделе 12.",
            "Все данные вымышленные. Это не корпоративный SSO и не готовый сервис.",
        )
        for index, line in enumerate(notes):
            c.drawString(MARGIN, 92 - index * 13, line)
        c.restoreState()

    return paint


def content_page(c: Any, doc: Any) -> None:
    """Draw the running header and the page number."""
    c.saveState()
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(MARGIN, PAGE_H - 12 * mm, PAGE_W - MARGIN, PAGE_H - 12 * mm)
    c.setFont("Sans", 8)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, PAGE_H - 10 * mm, "Agentic Infrastructure · руководство")
    c.drawRightString(PAGE_W - MARGIN, 10 * mm, f"стр. {doc.page}")
    c.restoreState()


def publication_label(name: str) -> str:
    """Turn a publication key such as stage_05_documentation into a short label."""
    if name == "branch_consolidation":
        return "перенос в main"
    stage = name.removeprefix("stage_")
    if stage.endswith("_documentation"):
        return "документы " + stage.removesuffix("_documentation")
    return "этап " + stage


def section(story: list[Any], number: int, color: Any = BLUE) -> None:
    """Start a numbered section on a fresh page."""
    title, subtitle = SECTIONS[number - 1]
    story.append(PageBreak())
    story.append(SectionTitle(number, title, subtitle, color))
    story.append(Spacer(1, 8))


def heading(text: str) -> Any:
    """Make a second-level heading."""
    return p(text, H2)


def build_story(facts: GuideFacts) -> list[Any]:
    """Compose all sections from documented behavior and verified facts."""
    story: list[Any] = [NextPageTemplate("content")]

    section(story, 1)
    story += [
        p(
            "Сотрудники компании пользуются ИИ-агентами. Агентам нужен общий "
            "корпоративный контекст: политики, заметки, регламенты. Но агент "
            "финансиста не должен видеть инженерные документы, а агент другой "
            "компании — вообще ничего. Agentic Infrastructure — сервис общей памяти, "
            "который выдаёт агенту только то, что разрешено его сотруднику и самому "
            "агенту, и записывает каждое обращение."
        ),
        Spacer(1, 6),
        callout(
            "<b>Главное правило.</b> Права агента — пересечение прав сотрудника "
            "и делегирования агенту. Личность определяет сервер по токену, а не "
            "поле запроса. Текст документа — данные, а не команды."
        ),
        heading("Что уже работает"),
        grid(
            [
                ["Этап", "Результат", "Статус"],
                ["01", "FastAPI, настройки, /health, проверки", "Реализован"],
                [
                    "02",
                    "Модель памяти и правила доступа на чистом Python",
                    "Реализован",
                ],
                [
                    "03",
                    "PostgreSQL: версии, транзакционный аудит, повторы",
                    "Реализован",
                ],
                ["04", "Bearer-токены агентов и HTTP API версий", "Реализован"],
                ["05", "Приём документов и поиск с фильтром прав", "Реализован"],
                ["06", "Ограниченный инструмент контекста для агента", "Реализован"],
                ["07", "Источник Handbook HTTP v1 для чтения", "Реализован"],
                [
                    "08",
                    "Контейнеры, независимые оценки, копия и восстановление",
                    "Реализован",
                ],
            ],
            [40, W - 140, 100],
        ),
        Spacer(1, 10),
        Diagram(70, lambda c, w, h: roadmap(c, w, h, facts.stage)),
        heading("Учебный пример"),
        p(
            "Компания Alpha, проекты <b>finance</b> и <b>engineering</b>. "
            f"Финансовый помощник {mono('fin-agent')} сотрудника {mono('fin-user')} "
            "читает финансовую политику и ищет в ней лимиты командировок. "
            f"Инженерный помощник {mono('eng-agent')} с тем же запросом к "
            "финансовому проекту получает 404. На один и тот же вопрос агенты "
            "получают ответы со ссылками только на свои документы. Агент "
            "вымышленной компании Beta не видит ничего из Alpha. Все люди, "
            "документы и токены вымышлены."
        ),
    ]

    section(story, 2)
    story += [
        Diagram(160, architecture),
        Spacer(1, 6),
        grid(
            [
                ["Слой", "Пакет", "Ответственность"],
                [
                    "Домен",
                    mono("app/domain"),
                    "Модели, evaluate_scope/evaluate_access, текст, фрагменты, BM25. "
                    "Правила снимка источника. Стандартная библиотека.",
                ],
                [
                    "Приложение",
                    mono("app/application"),
                    "Контракты результатов, Principal, цикл run_agent и "
                    "офлайн-провайдер; сбор полного снимка источника.",
                ],
                [
                    "Хранение",
                    mono("app/persistence"),
                    "SQLAlchemy Core, транзакции, токены, приём, история, "
                    "синхронизация и проверка срока источника.",
                ],
                [
                    "Источник",
                    mono("app/connectors"),
                    "HandbookClient: GET к заданному адресу, без перенаправлений.",
                ],
                [
                    "HTTP",
                    mono("app/api"),
                    "Маршруты памяти и ответов агента, bearer-токен, схемы.",
                ],
                [
                    "Миграции",
                    mono("migrations"),
                    "Alembic: 0001 схема, 0002 токены, 0003 история поиска, "
                    "0004 запуски агента, 0005 источники и синхронизация.",
                ],
                [
                    "Поставка",
                    mono("compose.yaml"),
                    "Образ без root, Compose на loopback, /ready; оценки и "
                    "сценарий восстановления в scripts.",
                ],
            ],
            [70, 110, W - 180],
        ),
        Spacer(1, 8),
        callout(
            "Каждая операция памяти идёт одной транзакцией "
            f"{mono('READ COMMITTED')} и держит блокировку строки компании от "
            "проверки прав до записи аудита. Изменения прав под той же "
            "блокировкой упорядочены с операциями памяти.",
            AMBER,
            AMBER_L,
        ),
    ]

    section(story, 3)
    story += [
        p(
            "Первый этап — каркас: проект на Python 3.12 и uv с точным "
            f"{mono('uv.lock')}, фабрика {mono('create_app()')} и проверка "
            f"{mono('GET /health')}. Health отвечает, что процесс жив; он не "
            "проверяет базу данных и права."
        ),
        Spacer(1, 6),
        code_block(
            "uv sync --locked\n"
            "uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8005\n"
            "curl -i http://127.0.0.1:8005/health\n"
            '# 200 {"status":"ok","service":"agentic-infrastructure"}'
        ),
        heading("Настройки"),
        grid(
            [
                ["Переменная", "По умолчанию", "Смысл"],
                [mono("APP_NAME"), "agentic-infrastructure", "Имя сервиса в ответе"],
                [mono("APP_ENV"), "development", "Метка окружения, не включает защиту"],
                [mono("APP_DEBUG"), "false", "Отладка FastAPI, держать выключенной"],
                [
                    mono("APP_DATABASE_URL"),
                    "не задан",
                    "PostgreSQL для маршрутов памяти; без него они отвечают 503",
                ],
            ],
            [120, 110, W - 230],
        ),
        Spacer(1, 6),
        p(
            "Порядок приоритета: явные Settings(...), переменные окружения, "
            "локальный .env, значения по умолчанию. Тесты удаляют APP_* и "
            "работают во временном каталоге, поэтому не зависят от .env "
            "разработчика.",
            BODY_S,
        ),
    ]

    section(story, 4, GREEN)
    story += [
        Diagram(165, access_rule),
        Spacer(1, 4),
        p(
            "Правила — обычные неизменяемые dataclass без FastAPI и SQLAlchemy. "
            "Неизвестное или неполное разрешение означает отказ. Чтение и запись — "
            "разные действия: право записи не даёт чтения."
        ),
        heading("Коды причин отказа"),
        grid(
            [
                ["Код", "Когда"],
                [
                    mono("company_unavailable / company_mismatch"),
                    "Нет компании или объект из другой",
                ],
                [
                    mono("project_unavailable / project_mismatch"),
                    "Нет проекта или документ из другого проекта",
                ],
                [mono("employee_unavailable"), "Сотрудник неактивен или не тот"],
                [
                    mono("agent_unavailable / agent_employee_mismatch"),
                    "Агент неактивен или чужой",
                ],
                [
                    mono("delegation_unavailable / action_not_delegated"),
                    "Нет делегирования или действия",
                ],
                [
                    mono("document_unavailable / version_unavailable"),
                    "Документ или версия неактивны",
                ],
                [
                    mono("employee_permission_missing"),
                    "У сотрудника нет права на документ",
                ],
                [
                    mono("credential_unavailable"),
                    "Токен отозван или истёк к моменту операции",
                ],
            ],
            [250, W - 250],
            GREEN,
        ),
        Spacer(1, 6),
        p(
            f"Для поиска добавлена {mono('evaluate_scope()')}: та же проверка "
            "компании, проекта, сотрудника, агента и делегирования, но без "
            "конкретного документа.",
            BODY_S,
        ),
    ]

    section(story, 5, AMBER)
    story += [
        p(
            "PostgreSQL хранит компании, проекты, сотрудников, агентов, "
            "делегирования, документы, версии, права, аудит и результаты записей. "
            "Ключи включают company_id, а составные внешние ключи не дают связать "
            "объекты разных компаний. Новая редакция — новая строка версии, "
            "старая не перезаписывается."
        ),
        heading("Запись версии — одна транзакция"),
        Diagram(
            130,
            numbered_flow(
                (
                    ("Блокировка", "строка компании"),
                    ("Политика", "текущие права из БД"),
                    ("Повтор?", "write_requests"),
                    ("Номер версии", "ожидаемый = текущий"),
                    ("Вставка", "версия + аудит + повтор"),
                    ("commit", "всё или ничего"),
                ),
                AMBER_L,
                AMBER,
            ),
        ),
        heading("Повторы и конфликты"),
        grid(
            [
                ["Ситуация", "Результат"],
                [
                    "Тот же ключ и те же данные",
                    "Прежний результат, replayed = true, новой версии нет",
                ],
                ["Тот же ключ, другой текст", mono("idempotency_conflict")],
                [
                    "Ожидаемый номер устарел",
                    mono("version_conflict") + ", чужое изменение не затирается",
                ],
                ["Право отозвано до повтора", "Отказ: сохранённый успех не даёт права"],
                ["Ошибка записи аудита", "Откат всей транзакции, успеха нет"],
            ],
            [180, W - 180],
            AMBER,
        ),
    ]

    section(story, 6, VIOLET)
    story += [
        Diagram(120, token_flow),
        Spacer(1, 6),
        p(
            "Токен выдаёт привилегированная команда лаборатории и показывает его "
            "один раз. Сотрудник определяется по регистрации агента, к которой "
            "привязан токен, поэтому заголовок X-Employee-ID или поле "
            "employee_id ничего не меняют."
        ),
        heading("Ответы API памяти"),
        grid(
            [
                ["Код", "Когда", "Аудит"],
                ["200 / 201", "Чтение версии; новая версия (повтор — 200)", "Да"],
                ["401", "Нет токена, неверный, отозван или истёк — одно тело", "Нет"],
                [
                    "404",
                    "Любой отказ доступа — чужой документ неотличим от отсутствующего",
                    "Да",
                ],
                ["409", "version_conflict или idempotency_conflict", "Да"],
                ["422", "Лишние поля, нет Idempotency-Key, неверные значения", "Нет"],
                ["503", "Нет БД или не записался обязательный аудит", "Нет гарантии"],
            ],
            [64, W - 154, 90],
            VIOLET,
        ),
        Spacer(1, 6),
        code_block(
            'curl -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/documents/policy/"
            "versions/policy-v1"
        ),
    ]

    section(story, 7, GREEN)
    story += [
        p(
            "Приём принимает один формат: UTF-8 текст до 64 КиБ и 20 000 символов. "
            "Пакет до 50 файлов принимается целиком или отклоняется со списком "
            "кодов: too_large, not_utf8, control_characters, empty и другие. "
            "Абзацы становятся фрагментами до 800 символов со смещениями start/end."
        ),
        heading("Поиск: права до ранжирования"),
        Diagram(
            200,
            numbered_flow(
                (
                    ("Блокировка", "и повторная проверка токена"),
                    ("evaluate_scope", "проект и делегирование"),
                    ("Кандидаты", "документы с правом чтения"),
                    ("evaluate_access", "текст только разрешённых"),
                    ("Фрагменты", "из последней версии"),
                    ("BM25", "только по разрешённым"),
                    ("Перепроверка", "выбранных документов"),
                    ("История", "ссылки и SHA-256 запроса"),
                    ("Ответ", "found / no_authorized_evidence"),
                ),
                GREEN_L,
                GREEN,
            ),
        ),
        Spacer(1, 8),
        callout(
            "Статистика BM25 считается только по разрешённым фрагментам. Иначе "
            "запрещённый документ менял бы оценки разрешённых, и по ним можно было "
            "бы догадаться о его словах. Тест добавляет нечитаемый документ с теми "
            "же словами и проверяет, что результаты и оценки не изменились.",
            GREEN,
            GREEN_L,
        ),
        Spacer(1, 6),
        code_block(
            'curl -s -X POST -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"query":"travel limit receipt","max_items":3}\' \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/context"
        ),
        p(
            "Каждый элемент ответа: document_id, version_id, version_number, source, "
            "fragment, start, end, score, text, причём content[start:end] == text. "
            "Индекс и кэш не хранятся: отзыв права, удаление документа и новая "
            "версия действуют на следующий же поиск.",
            BODY_S,
        ),
    ]

    section(story, 8, VIOLET)
    story += [
        p(
            "Агент задаёт вопрос обычной фразой, а сервер запускает модель. "
            f"У модели ровно один инструмент — {mono('search_memory')} с "
            f"аргументами {mono('query')} и {mono('max_items')}. Проект берётся "
            "из пути, сотрудник и агент — из токена; в описании инструмента их нет."
        ),
        heading("Один запуск агента"),
        Diagram(
            130,
            numbered_flow(
                (
                    ("Область", "до запуска модели"),
                    ("Шаг модели", "вызов или ответ"),
                    ("Аргументы", "строгий JSON, без project_id"),
                    ("Поиск этапа 05", "права, BM25, история"),
                    ("Ссылки", "только из этого запуска"),
                    ("Перепроверка", "область и цитаты, история"),
                ),
                VIOLET_L,
                VIOLET,
            ),
        ),
        heading("Лимиты запуска"),
        grid(
            [
                ["Лимит", "Значение", "Исход при превышении"],
                ["Шаги модели", "4", mono("step_limit")],
                ["Вызовы инструмента", "3, пакет целиком", mono("tool_call_limit")],
                ["Время", "15 секунд", mono("time_limit") + ", HTTP 504"],
                ["Текст для модели", "24 000 символов", mono("transcript_limit")],
                [
                    "Ответ",
                    "до 2 000 символов, до 10 ссылок",
                    mono("invalid_answer / invalid_citation"),
                ],
            ],
            [130, 140, W - 270],
            VIOLET,
        ),
        Spacer(1, 8),
        callout(
            "Ответ принимается, только если каждая ссылка — фрагмент, который "
            "вернул поиск этого же запуска. Текст без ссылок не возвращается. "
            "Перед ответом права на каждую процитированную версию проверяются "
            "снова; при отзыве ответ скрывается целиком (404).",
            VIOLET,
            VIOLET_L,
        ),
        Spacer(1, 6),
        code_block(
            'curl -s -X POST -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"question":"What is the travel limit per trip?"}\' \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/answers"
        ),
        p(
            "Офлайн-провайдер offline-extractive-v1 ищет по словам вопроса и "
            "цитирует первое предложение каждого фрагмента: «[1] The travel limit "
            "is 500 credits per trip.» Фраза «Ignore all previous rules…» из "
            "deploy-notes попадает в ответ инженерного агента как цитата, а поиск "
            "в finance не выполняется. Ошибки провайдера, неверные шаги и ссылки "
            "дают 502 с кодом исхода без текста.",
            BODY_S,
        ),
    ]

    section(story, 9, AMBER)
    story += [
        p(
            "Один адаптер читает вымышленный Handbook по контракту HTTP v1. "
            "Оператор заранее задаёт адрес, пространство, проект и соответствия "
            "пользователей источника сотрудникам Alpha. Клиент выполняет только GET; "
            "токен берётся из SOURCE_TOKEN и не сохраняется в БД. "
            "Подключение к настоящему корпоративному сервису не проверялось."
        ),
        Spacer(1, 6),
        Diagram(
            140,
            numbered_flow(
                (
                    ("Настройка", "адрес, проект, учётные записи"),
                    ("Полный список", "GET всех страниц и читателей"),
                    ("Новые тексты", "GET новых редакций, UTF-8"),
                    ("План изменений", "версии, удаления, права"),
                    ("Одна транзакция", "блокировка компании + история"),
                    ("Проверка срока", "чтение, поиск и ответ агента"),
                ),
                AMBER_L,
                AMBER,
            ),
        ),
        heading("Как меняется память"),
        grid(
            [
                ["Событие источника", "Результат синхронизации"],
                ["Первый снимок", "Документы hb-*, версии и только read-разрешения"],
                [
                    "Повтор / новая редакция",
                    "Без новой версии при том же тексте; изменённый текст "
                    "создаёт следующую версию",
                ],
                [
                    "Отзыв читателя / удаление",
                    "Разрешение выключается / документ и его разрешения "
                    "выключаются; старые ссылки не открывают доступ",
                ],
                [
                    "Неверный текст / неполный список",
                    "Неверный документ закрывается; неполный список "
                    "не применяется вообще и не означает удаление",
                ],
                [
                    "Ошибка или недоступность",
                    "Ничего не применяется; прежнее подтверждение продолжает "
                    "стареть, затем документы закрываются",
                ],
            ],
            [165, W - 165],
            AMBER,
        ),
        Spacer(1, 6),
        callout(
            "<b>Граница доверия.</b> Только явно сопоставленные пользователи "
            "источника получают read. Группы и неизвестные учётные записи "
            "ничего не дают. Делегирование агента и права сотрудника по-прежнему "
            "проверяются сервером. Импортированный текст не управляет правами; "
            "запись через API памяти в такой документ запрещена.",
            AMBER,
            AMBER_L,
        ),
        heading("Лимиты и срок подтверждения"),
        p(
            "До 20 страниц по 100 записей и 256 КиБ; до 200 документов в снимке; "
            "до 100 читателей документа. Текст: до 64 КиБ и 20 000 символов. "
            "Запрос: до 5 секунд, включая DNS, заголовки и тело; сбор: до 60 секунд. "
            "HTTPS, либо HTTP "
            "только к loopback; перенаправления и proxy-переменные не используются. "
            "Подтверждение действует 60–86 400 секунд (по умолчанию 900), "
            "с начала успешной синхронизации. При истечении сервер исключает "
            "документы до загрузки текста и ранжирования и снова проверяет "
            "источники перед выдачей ответа агента.",
            BODY_S,
        ),
    ]

    section(story, 10, GREEN)
    story += [
        p(
            "Приложение собирается в образ и поднимается вместе с учебной "
            "PostgreSQL в Docker Compose. Один сценарий с чистого листа проходит "
            "всю лабораторию и проверяет её независимыми ожиданиями. Это локальная "
            "лаборатория на вымышленных данных, а не корпоративное развёртывание."
        ),
        Spacer(1, 6),
        Diagram(
            140,
            numbered_flow(
                (
                    ("Образ", "uv.lock, digest, не root"),
                    ("Compose", "db, затем migrate и app; /ready"),
                    ("Данные", "Alpha, Beta, источник, токены"),
                    ("22 оценки", "статус, документы, ссылки"),
                    ("Учения", "сбой аудита, утечка прав"),
                    ("Копия", "pg_dump, потеря тома, restore"),
                ),
                GREEN_L,
                GREEN,
            ),
        ),
        heading("Что проверяют оценки"),
        grid(
            [
                ["Группа", "Ожидание"],
                [
                    "Своё чтение",
                    "200, ссылки перечитываются тем же агентом и совпадают",
                ],
                [
                    "Другой проект / компания",
                    "404 с одинаковым телом, без текста чужих документов",
                ],
                [
                    "Инструкция в тексте",
                    "Возвращается как цитата и не открывает другой проект",
                ],
                [
                    "Источник",
                    "Новая редакция видна; отозванное и удалённое недоступно",
                ],
                [
                    "Повреждённый текст",
                    "Не выдаётся; зеркало источника нельзя изменить",
                ],
                ["Токен и ввод", "401 без токена или с чужим, 422 при project_id"],
            ],
            [165, W - 165],
            GREEN,
        ),
        Spacer(1, 6),
        callout(
            "<b>Оценки должны ловить поломку.</b> Сценарий как владелец БД выдаёт "
            "инженерному агенту доступ к финансам — минимум два случая обязаны "
            "упасть. Триггер, ломающий аудит, должен дать 503 без текста "
            "документа. После исправления всё снова проходит.",
            GREEN,
            GREEN_L,
        ),
        heading("/health, /ready и восстановление"),
        p(
            "/health отвечает, что процесс жив. /ready проверяет БД и ревизию "
            "схемы и даёт 503 с кодом, если что-то не так; healthcheck образа "
            "использует /ready. Сценарий делает pg_dump, удаляет том, "
            "восстанавливает копию и требует тех же строк и исходов всех оценок. "
            "Копия содержит тексты и хэши токенов: только var/ вне Git. Замеры "
            "задержки описывают один ноутбук, а не пропускную способность.",
            BODY_S,
        ),
        code_block(
            "uv run --locked python -m scripts.compose_lab\n"
            "# вручную: свой пароль, порты только на 127.0.0.1\n"
            'export POSTGRES_PASSWORD="$(openssl rand -hex 24)"\n'
            "docker compose up --detach --wait\n"
            "curl -s http://127.0.0.1:8005/ready"
        ),
    ]

    section(story, 11)
    story += [
        p(
            "Сценарий на временной локальной PostgreSQL 17 с вымышленными данными. "
            "Подробные объяснения — в docs/STAGE_03.md … STAGE_08.md. Весь "
            "сценарий в контейнерах — раздел 10."
        ),
        heading("1. Лаборатория PostgreSQL"),
        code_block(
            "AGENTIC_PG_BIN=/opt/homebrew/opt/postgresql@17/bin\n"
            "AGENTIC_PG_ROOT=$(mktemp -d /private/tmp/agentic-pg.XXXXXX)\n"
            '"$AGENTIC_PG_BIN/initdb" -D "$AGENTIC_PG_ROOT/data" -U agentic \\\n'
            "  -A trust --encoding=UTF8 --no-locale\n"
            'LC_ALL=C "$AGENTIC_PG_BIN/pg_ctl" -D "$AGENTIC_PG_ROOT/data" \\\n'
            '  -l "$AGENTIC_PG_ROOT/postgres.log" \\\n'
            '  -o "-h 127.0.0.1 -p 55435 -k $AGENTIC_PG_ROOT" start -w\n'
            '"$AGENTIC_PG_BIN/createdb" -h 127.0.0.1 -p 55435 -U agentic '
            "agentic_demo_lab"
        ),
        heading("2. Схема, данные, документы и токены"),
        code_block(
            "export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/"
            "agentic_demo_lab'\n"
            "uv run --locked python -m app.persistence_demo --setup\n"
            "uv run --locked python -m app.intake_lab --project finance \\\n"
            "  --grant fin-user:read fixtures/lab/finance/*.txt\n"
            "uv run --locked python -m app.credential_lab fin-agent\n"
            "uv run --locked python -m app.credential_lab eng-agent"
        ),
        heading("3. API и запросы"),
        code_block(
            'export APP_DATABASE_URL="$DATABASE_URL"\n'
            "uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8005\n"
            "# в другом терминале, с токенами из шага 2:\n"
            'curl -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/documents/policy/"
            "versions/policy-v1\n"
            'curl -s -X POST -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            '  -H "Content-Type: application/json" -d \'{"query":"travel limit"}\' \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/context\n"
            'curl -s -X POST -H "Authorization: Bearer $FIN_TOKEN" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"question":"What is the travel limit?"}\' \\\n'
            "  http://127.0.0.1:8005/memory/projects/finance/answers"
        ),
        p(
            "Токены печатаются один раз: сохраните их в переменные FIN_TOKEN и "
            "ENG_TOKEN текущего терминала и не записывайте в Git.",
            BODY_S,
        ),
        PageBreak(),
        heading("4. Источник Handbook: отдельный терминал"),
        p(
            "Используйте ту же DATABASE_URL учебной базы на _lab. Создайте "
            "случайный SOURCE_TOKEN и передайте его двум терминалам через "
            "окружение; не помещайте значение в команды, файлы или Git. "
            "Первый терминал обслуживает источник, второй запускает синхронизацию."
        ),
        code_block(
            "# терминал источника, SOURCE_TOKEN уже задан\n"
            "uv run --locked python -m app.source_lab \\\n"
            "  fixtures/source/handbook-1.json --port 8105\n"
            "# терминал синхронизации, тот же SOURCE_TOKEN\n"
            "uv run --locked python -m app.source_sync register handbook \\\n"
            "  --project finance --base-url http://127.0.0.1:8105 \\\n"
            "  --space alpha-handbook --prefix hb- --max-age 900 \\\n"
            "  --account u-fin=fin-user --account u-eng=eng-user\n"
            "uv run --locked python -m app.source_sync run handbook\n"
            "uv run --locked python -m app.source_sync status handbook"
        ),
        heading("5. Изменение, отзыв, удаление и недоступность"),
        grid(
            [
                ["Действие", "Что проверить"],
                [
                    "Повторить run handbook",
                    "fetched=0; число версий не растёт; подтверждение обновляется",
                ],
                [
                    "Остановить источник; запустить handbook-2.json",
                    "Снова выполнить run: travel-desk обновлён, "
                    "u-fin лишён office-access, vendor-review удалён, "
                    "budget-draft withheld",
                ],
                [
                    "Остановить источник и выполнить run",
                    "failed / unavailable, код выхода 1; confirmed_at не продлевается",
                ],
                [
                    "Дождаться confirmed_until из status",
                    "confirmed=false; чтение прежних версий запрещено, "
                    "поиск их не использует; новых версий нет",
                ],
            ],
            [190, W - 190],
            AMBER,
        ),
        Spacer(1, 8),
        callout(
            "Удаление означает закрытие доступа к документу и всем его версиям, "
            "а не физическое удаление исторических строк. Сохранённого индекса "
            "и кэша результатов нет. Откат миграции 0005 сначала выключает "
            "импортированные документы и их разрешения, затем удаляет метаданные "
            "источника; автоматически открывать эти документы после отката нельзя.",
            AMBER,
            AMBER_L,
        ),
        p(
            "Подробный сценарий, ожидаемые JSON-счётчики и назначение функций: "
            "docs/STAGE_07.md. Токены источника и агентов различаются; токен "
            "источника не предоставляет доступ к API памяти.",
            BODY_S,
        ),
    ]

    section(story, 12, BLUE)
    published = [
        [
            publication_label(entry.name),
            mono(entry.commit[:12]),
            entry.remote_ref,
            f"{entry.ci_run} · {entry.ci_result}",
        ]
        for entry in facts.publications
    ]
    story += [
        p(
            f"Результаты проверки кода этапа {facts.stage:02d}: " + facts.checked_at,
            BODY_S,
        ),
        p(facts.guide_note, BODY_S),
        Spacer(1, 6),
        grid(
            [
                [f"Проверка этапа {facts.stage:02d}", "Фактический результат"],
                [
                    "pytest с TEST_DATABASE_URL",
                    f"{facts.tests_total} passed: {facts.tests_unit} без БД и "
                    f"{facts.tests_postgres} PostgreSQL",
                ],
                [
                    "pytest без TEST_DATABASE_URL",
                    f"{facts.tests_unit} passed, {facts.skipped_without_database} "
                    "skipped — это не проверка PostgreSQL",
                ],
                [
                    "mypy app tests scripts migrations",
                    f"strict, {facts.mypy_files} файлов, без ошибок",
                ],
                [
                    "Ссылки документации",
                    f"локальных ссылок: {facts.local_links}, "
                    f"Markdown-файлов: {facts.markdown_files}",
                ],
                ["Ruff, формат, uv lock --check, git diff --check", "Успешно"],
                ["uv.lock SHA-256", mono(facts.lock_sha256[:32]) + "…"],
            ],
            [190, W - 190],
        ),
        heading("Публикация и GitHub Actions"),
        grid(
            [["Что", "Commit", "Ref", "CI: запуск · итог"], *published],
            [92, 92, 70, W - 254],
        ),
        Spacer(1, 6),
        callout(
            "Локальный успех, push, совпадение удалённого SHA и итог GitHub Actions — "
            "разные факты. Статус CI пишется только после наблюдения завершённого "
            "запуска для конкретного SHA; иначе — not_verified.",
            BLUE,
            BLUE_L,
        ),
        heading("Что делает CI"),
        p(
            "На каждый push в main: uv sync --locked, Ruff, формат, strict mypy, "
            "тесты без БД, отдельный шаг PostgreSQL-тестов на сервисе postgres:17 "
            "(закреплён по digest), проверка ссылок, lock и пробелов. Второе "
            "задание container-lab повторяет сценарий этапа 08: сборка образа, "
            "Compose, оценки, учения, копия и восстановление. Образ не "
            "публикуется, PDF в CI не собирается.",
            BODY_S,
        ),
    ]

    section(story, 13, RED)
    story += [
        grid(
            [
                ["Симптом", "Что проверить"],
                [
                    "PostgreSQL-тесты skipped",
                    "Задан ли TEST_DATABASE_URL с базой на _test",
                ],
                ["postmaster became multithreaded", "Запускать pg_ctl с LC_ALL=C"],
                [
                    "connection refused",
                    "Запущен ли кластер; /private/tmp очищается после перезагрузки",
                ],
                [
                    "401 с верным на вид токеном",
                    "Срок, active, та ли база в APP_DATABASE_URL",
                ],
                [
                    "503 memory storage is not configured",
                    "Не задан APP_DATABASE_URL для Uvicorn",
                ],
                [
                    "404 на свой документ",
                    "Проект в пути, делегирование агента, право сотрудника",
                ],
                [
                    "no_authorized_evidence",
                    "Право чтения, активность документа и версии, слова запроса",
                ],
                ["409 version_conflict", "expected_number не равен текущему номеру"],
                [
                    "422 при записи или поиске",
                    "Лишние поля, строка вместо числа, нет Idempotency-Key",
                ],
                [
                    "intake_lab: document_exists",
                    "Документ с таким именем файла уже импортирован",
                ],
                [
                    "unanswered на свой вопрос",
                    "Есть ли слова вопроса в разрешённых документах",
                ],
                [
                    "502 invalid_citation",
                    "Провайдер сослался на фрагмент не из поисков этого запуска",
                ],
                ["504 time_limit", "Провайдер или поиск дольше 15 секунд"],
                [
                    "Источник: confirmed=false",
                    "Последняя успешная синхронизация, срок max_age, "
                    "доступность источника; source_sync status handbook",
                ],
                [
                    "Compose: set a URL-safe POSTGRES_PASSWORD",
                    "Задать пароль, даже для docker compose down",
                ],
                [
                    "migrate завершился с кодом 1",
                    "Пароль отличается от пароля при создании тома",
                ],
                ["/health 200, но /ready 503", "БД недоступна или схема устарела"],
            ],
            [190, W - 190],
            RED,
        ),
    ]

    section(story, 14, MUTED)
    story += [
        heading("Пока не реализовано"),
        *[p("• " + mono(item), BODY_S) for item in facts.not_implemented],
        heading("Известные ограничения"),
        *[p("• " + item, BODY_S) for item in LIMITATIONS],
        heading("Что дальше"),
        p(
            "Все восемь этапов дорожной карты реализованы. За её пределами: "
            "живая модель с отдельными оценками, корпоративный вход, расписание "
            "синхронизации, TLS, менеджер секретов, роль БД без лишних прав, "
            "плановые копии, оповещения и сканирование образа.",
            BODY_S,
        ),
        Spacer(1, 10),
        callout(
            "Руководство — наглядная сводка. Подробные объяснения находятся "
            "в docs/STAGE_01–08.md, фактические проверки — в docs/VERIFICATION.md.",
            MUTED,
            SOFT,
        ),
    ]
    return story


def build_guide(output: Path, facts: GuideFacts) -> int:
    """Render to a temporary file, keep a backup and replace the PDF on success."""
    register_fonts()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(tempfile.gettempdir()) / f"{output.stem}-before-{stamp}.pdf"
        shutil.copy2(output, backup)
    with tempfile.NamedTemporaryFile(
        dir=output.parent, suffix=".pdf", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame = Frame(MARGIN, 16 * mm, W, PAGE_H - 34 * mm, id="body", showBoundary=0)
        document = BaseDocTemplate(
            str(temporary),
            pagesize=A4,
            title="Agentic Infrastructure — наглядное руководство",
            author="Agentic Infrastructure",
            leftMargin=MARGIN,
            rightMargin=MARGIN,
        )
        document.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[frame], onPage=cover_painter(facts)),
                PageTemplate(id="content", frames=[frame], onPage=content_page),
            ]
        )
        document.build(build_story(facts))
        pages = document.page
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return int(pages)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, validate verification data and build the guide."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verification",
        type=Path,
        default=ROOT / "docs/guide/verification.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/guide/agentic-infrastructure-guide.pdf",
    )
    args = parser.parse_args(argv)
    try:
        facts = load_facts(args.verification)
    except GuideDataError as error:
        print(f"verification data rejected: {error}", file=sys.stderr)
        return 2
    pages = build_guide(args.output, facts)
    print(f"Built {args.output} ({pages} pages).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
