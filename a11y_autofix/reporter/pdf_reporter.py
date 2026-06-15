"""
Gerador de relatórios PDF no estilo Google Lighthouse.

Estrutura do relatório:
  1. Capa   — score circular + métricas chave
  2. Resumo — breakdown por categoria e por nível WCAG
  3. Findings por arquivo — cards coloridos por severidade
  4. Rodapé — versões de ferramentas + execution ID
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _xesc(text: object) -> str:
    """Escapa texto dinâmico para a mini-marcação XML do reportlab.

    Mensagens de scanner ("Ensure <img> elements have alt text") e seletores
    CSS ("div > button", "[aria-label]") contêm <, > e & que o parser de
    Paragraph do reportlab interpreta como marcação e aborta a geração do PDF.
    Escapar APENAS os valores dinâmicos preserva as tags <font>/<b> nossas.
    """
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ─── Paleta de cores Lighthouse ─────────────────────────────────────────────
_GREEN  = (0.06, 0.74, 0.35)    # ≥ 90
_ORANGE = (1.00, 0.56, 0.00)    # 50–89
_RED    = (1.00, 0.22, 0.22)    # < 50

_BLUE_DARK  = (0.07, 0.39, 0.93)
_BLUE_LIGHT = (0.93, 0.96, 1.00)
_GRAY_BG    = (0.97, 0.97, 0.97)
_GRAY_DARK  = (0.24, 0.24, 0.28)
_GRAY_MID   = (0.55, 0.55, 0.58)
_GRAY_LIGHT = (0.91, 0.91, 0.93)
_WHITE      = (1.00, 1.00, 1.00)

_IMPACT_COLOR = {
    "critical": (0.86, 0.08, 0.08),
    "serious":  (0.93, 0.36, 0.08),
    "moderate": (1.00, 0.69, 0.00),
    "minor":    (0.35, 0.60, 0.35),
}

_IMPACT_LABEL = {
    "critical": "Crítico",
    "serious":  "Sério",
    "moderate": "Moderado",
    "minor":    "Menor",
}

_TYPE_ICON = {
    "contrast":  "◑",
    "aria":      "⊕",
    "keyboard":  "⌨",
    "label":     "⊙",
    "semantic":  "❮❯",
    "alt-text":  "⊡",
    "focus":     "◎",
    "other":     "•",
}

_TYPE_COLOR = {
    "contrast":  (0.86, 0.30, 0.08),
    "aria":      (0.07, 0.39, 0.93),
    "keyboard":  (0.50, 0.08, 0.86),
    "label":     (0.08, 0.55, 0.55),
    "semantic":  (0.08, 0.47, 0.08),
    "alt-text":  (0.86, 0.08, 0.47),
    "focus":     (0.55, 0.40, 0.08),
    "other":     (0.45, 0.45, 0.45),
}

_WCAG_LABELS = {
    "1.1.1": "Non-text Content",
    "1.3.1": "Info and Relationships",
    "1.4.1": "Use of Color",
    "1.4.3": "Contrast (Minimum)",
    "1.4.6": "Contrast (Enhanced)",
    "1.4.11": "Non-text Contrast",
    "2.1.1": "Keyboard",
    "2.1.2": "No Keyboard Trap",
    "2.4.3": "Focus Order",
    "2.4.7": "Focus Visible",
    "2.4.11": "Focus Appearance",
    "4.1.2": "Name, Role, Value",
    "4.1.3": "Status Messages",
}


def _score_color(score: float) -> tuple[float, float, float]:
    if score >= 90:
        return _GREEN
    if score >= 50:
        return _ORANGE
    return _RED


def _score_label(score: float) -> str:
    if score >= 90:
        return "Bom"
    if score >= 50:
        return "Precisa melhorar"
    return "Ruim"


def _r(c: tuple[float, float, float]) -> float:
    return c[0]


def _g(c: tuple[float, float, float]) -> float:
    return c[1]


def _b(c: tuple[float, float, float]) -> float:
    return c[2]


class PDFReporter:
    """
    Gera relatório PDF no estilo Google Lighthouse.

    Usa reportlab (platypus) para layout de documento multipágina.
    Inclui gauge circular de score de acessibilidade, breakdowns
    por categoria e por critério WCAG, e findings detalhados por arquivo.
    """

    # ─── Constantes de layout ────────────────────────────────────────────────
    PAGE_W = 595.28    # A4 width  (pontos)
    PAGE_H = 841.89    # A4 height (pontos)
    MARGIN = 40.0

    def generate(
        self,
        report_data: dict[str, Any],
        output_dir: Path,
        filename: str = "accessibility_report.pdf",
    ) -> Path:
        """
        Gera o PDF e salva em disco.

        Args:
            report_data: Dados do JSONReporter (mesma estrutura do report.json).
            output_dir:  Diretório de saída.
            filename:    Nome do arquivo PDF gerado.

        Returns:
            Caminho absoluto do PDF gerado.
        """
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate
        except ImportError as exc:
            raise RuntimeError(
                "reportlab não instalado. Execute: pip install reportlab"
            ) from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=self.MARGIN,
            rightMargin=self.MARGIN,
            topMargin=self.MARGIN,
            bottomMargin=self.MARGIN,
            title="Accessibility Report",
            author="a11y-autofix",
        )

        story = self._build_story(report_data)
        doc.build(
            story,
            onFirstPage=self._draw_cover_background,
            onLaterPages=self._draw_page_header,
        )

        log.info("pdf_report_generated", path=str(output_path))
        return output_path

    # ─── Story builder ───────────────────────────────────────────────────────

    def _build_story(self, data: dict[str, Any]) -> list[Any]:
        from reportlab.platypus import PageBreak, Spacer
        from reportlab.lib.units import cm

        summary = data.get("summary", {})
        files   = data.get("files", [])
        env     = data.get("environment", {})
        config  = data.get("configuration", {})

        score = self._compute_score(summary, files)

        story: list[Any] = []

        # ── Capa ─────────────────────────────────────────────────────────────
        story += self._build_cover(data, summary, score)
        story.append(PageBreak())

        # ── Resumo executivo ─────────────────────────────────────────────────
        story += self._build_executive_summary(summary, files, env, config)
        story.append(PageBreak())

        # ── Breakdown por categoria ──────────────────────────────────────────
        story += self._build_category_breakdown(files)

        # ── Breakdown por WCAG ───────────────────────────────────────────────
        story += self._build_wcag_breakdown(files)
        story.append(PageBreak())

        # ── Findings por arquivo ─────────────────────────────────────────────
        story += self._build_file_findings(files)

        # ── Rodapé técnico ───────────────────────────────────────────────────
        story.append(PageBreak())
        story += self._build_footer(data)

        return story

    # ─── Capa ────────────────────────────────────────────────────────────────

    def _build_cover(
        self,
        data: dict[str, Any],
        summary: dict[str, Any],
        score: float,
    ) -> list[Any]:
        from reportlab.platypus import Spacer, Paragraph, HRFlowable
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus.flowables import KeepTogether
        import reportlab.platypus as plat

        story: list[Any] = []

        # Espaço para o fundo azul desenhado no callback
        story.append(Spacer(1, 3.8 * cm))

        # Gauge circular de score (desenho vetorial)
        story.append(self._make_score_gauge(score))
        story.append(Spacer(1, 0.4 * cm))

        # Título e subtítulo abaixo do gauge
        label_style = self._style(
            name="CoverLabel",
            fontSize=11,
            textColor=colors.HexColor("#FFFFFF"),
            alignment=1,
            leading=16,
        )
        story.append(Paragraph(_score_label(score).upper(), label_style))
        story.append(Spacer(1, 1.5 * cm))

        # Cards de métricas chave
        story.append(self._make_cover_metrics(summary))
        story.append(Spacer(1, 1.2 * cm))

        # Linha divisória e meta-info
        story.append(HRFlowable(
            width="100%", thickness=1,
            color=colors.HexColor("#6688CC"),
        ))
        story.append(Spacer(1, 0.4 * cm))

        wcag_level  = data.get("wcag_level", "WCAG2AA")
        report_label = data.get("report_label", "desktop").upper()
        model_name   = data.get("environment", {}).get("llm_model", "unknown")  # type: ignore[union-attr]
        ts_raw       = data.get("timestamp", "")
        ts_fmt = ts_raw[:10] if ts_raw else datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        meta_style = self._style(
            name="CoverMeta",
            fontSize=9,
            textColor=colors.HexColor("#CCDDFF"),
            alignment=1,
            leading=14,
        )
        story.append(Paragraph(
            f"Nível WCAG: <b>{wcag_level}</b> &nbsp;·&nbsp; "
            f"Modo: <b>{report_label}</b> &nbsp;·&nbsp; "
            f"Modelo: <b>{model_name}</b> &nbsp;·&nbsp; "
            f"Data: <b>{ts_fmt}</b>",
            meta_style,
        ))

        return story

    def _draw_cover_background(self, canvas: Any, doc: Any) -> None:
        """Preenche o topo da capa com gradiente azul estilo Lighthouse."""
        from reportlab.lib.colors import HexColor
        w = self.PAGE_W

        canvas.saveState()
        canvas.setFillColor(HexColor("#1A3A6B"))
        canvas.rect(0, self.PAGE_H - 300, w, 300, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#0D47A1"))
        canvas.rect(0, self.PAGE_H - 160, w, 160, fill=1, stroke=0)
        # Linha de brilho no topo
        canvas.setFillColor(HexColor("#2962FF"))
        canvas.rect(0, self.PAGE_H - 5, w, 5, fill=1, stroke=0)
        # Watermark de fundo
        canvas.setFillColor(HexColor("#1565C0"))
        canvas.setFont("Helvetica", 80)
        canvas.setFillAlpha(0.08)
        canvas.drawCentredString(w / 2, self.PAGE_H - 260, "a11y")
        canvas.restoreState()

        self._draw_page_footer(canvas, doc)

    def _draw_page_header(self, canvas: Any, doc: Any) -> None:
        """Cabeçalho compacto para páginas internas."""
        from reportlab.lib.colors import HexColor
        w = self.PAGE_W
        canvas.saveState()
        canvas.setFillColor(HexColor("#0D47A1"))
        canvas.rect(0, self.PAGE_H - 28, w, 28, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(HexColor("#FFFFFF"))
        canvas.drawString(self.MARGIN, self.PAGE_H - 18, "♿  Accessibility Report — a11y-autofix")
        canvas.restoreState()
        self._draw_page_footer(canvas, doc)

    def _draw_page_footer(self, canvas: Any, doc: Any) -> None:
        from reportlab.lib.colors import HexColor
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(HexColor("#999999"))
        canvas.drawCentredString(
            self.PAGE_W / 2,
            18,
            f"Gerado por a11y-autofix v2.0  ·  Página {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    # ─── Gauge circular ──────────────────────────────────────────────────────

    def _make_score_gauge(self, score: float) -> Any:
        """
        Cria gauge circular à la Lighthouse com arco colorido e score central.

        Usa Wedge (anel) para simular o arco de progresso — técnica comum
        quando Arc não está disponível no namespace de shapes do ReportLab.
        """
        from reportlab.graphics.shapes import Drawing, Circle, Wedge, String, Group
        from reportlab.graphics import renderPDF
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors

        SIZE  = 160
        cx    = SIZE / 2
        cy    = SIZE / 2
        R_OUT = 64          # raio externo do anel
        R_IN  = 50          # raio interno (buraco) do anel

        score_color = _score_color(score)

        # ── Anel de fundo completo (cinza, 240° da volta) ──────────────────
        BG_START  = -30.0   # 30° abaixo da direita (= -30 no sistema RL)
        BG_EXTENT = 240.0   # 240° no sentido anti-horário

        d = Drawing(SIZE, SIZE)

        def _wedge(
            start_deg: float,
            extent_deg: float,
            color: tuple[float, float, float],
            r_out: float = R_OUT,
            r_in: float  = R_IN,
        ) -> Wedge:
            w = Wedge(
                cx, cy,
                r_out,
                startangledegrees=start_deg,
                endangledegrees=start_deg + extent_deg,
                radius1=r_in,  # inner radius → creates ring/arc effect
                fillColor=self._rl_color(color),
                strokeColor=None,
                strokeWidth=0,
            )
            return w

        # Fundo cinza
        d.add(_wedge(BG_START, BG_EXTENT, (0.88, 0.88, 0.90)))

        # Arco de score (mesma janela de 240°, proporcional ao score)
        if score > 0:
            filled_extent = BG_EXTENT * (score / 100.0)
            # começa no mesmo ponto que o fundo
            d.add(_wedge(BG_START, filled_extent, score_color))

        # Círculo branco central (sobrepõe os wedges para criar o "anel")
        inner = Circle(
            cx, cy, R_IN - 1,
            fillColor=self._rl_color(_WHITE),
            strokeColor=None,
        )
        d.add(inner)

        # ── Textos no centro ───────────────────────────────────────────────
        d.add(String(
            cx, cy + 10,
            str(int(round(score))),
            fontSize=28,
            fontName="Helvetica-Bold",
            fillColor=self._rl_color(score_color),
            textAnchor="middle",
        ))
        d.add(String(
            cx, cy - 5,
            "/100",
            fontSize=9,
            fontName="Helvetica",
            fillColor=self._rl_color(_GRAY_MID),
            textAnchor="middle",
        ))
        d.add(String(
            cx, cy - 20,
            "ACESSIBILIDADE",
            fontSize=7,
            fontName="Helvetica-Bold",
            fillColor=self._rl_color(_GRAY_DARK),
            textAnchor="middle",
        ))

        # ── Centrar o desenho na página ────────────────────────────────────
        from reportlab.platypus.flowables import Flowable

        class _DrawingFlowable(Flowable):
            def __init__(self, drawing: Drawing, width: float, height: float) -> None:
                Flowable.__init__(self)
                self._drawing = drawing
                self.width  = width
                self.height = height

            def draw(self) -> None:
                renderPDF.draw(self._drawing, self.canv, 0, 0)

        wrapper = Table(
            [[_DrawingFlowable(d, SIZE, SIZE)]],
            colWidths=[self.PAGE_W - 2 * self.MARGIN],
        )
        wrapper.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        return wrapper

    # ─── Cards de métricas da capa ───────────────────────────────────────────

    def _make_cover_metrics(self, summary: dict[str, Any]) -> Any:
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib import colors

        metrics = [
            (str(summary.get("total_files", 0)),       "Arquivos"),
            (str(summary.get("total_issues", 0)),       "Issues"),
            (str(summary.get("high_confidence_issues", 0)), "Alta Confiança"),
            (str(summary.get("issues_fixed", 0)),       "Corrigidos"),
            (f"{summary.get('success_rate', 0):.0f}%",  "Taxa Sucesso"),
        ]

        header_style = self._style(
            "MetricVal",
            fontSize=22,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#FFFFFF"),
            alignment=1,
            leading=26,
        )
        label_style = self._style(
            "MetricLbl",
            fontSize=8,
            textColor=colors.HexColor("#AACCFF"),
            alignment=1,
            leading=12,
        )

        from reportlab.platypus import Paragraph
        cells = [[
            [Paragraph(v, header_style), Paragraph(l, label_style)]
            for v, l in metrics
        ]]
        col_w = (self.PAGE_W - 2 * self.MARGIN) / len(metrics)
        t = Table(cells, colWidths=[col_w] * len(metrics), rowHeights=[55])
        t.setStyle(TableStyle([
            ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",   (0, 0), (-1, -1), "CENTER"),
            ("LINEAFTER", (0, 0), (-2, -1), 0.5, colors.HexColor("#3366AA")),
        ]))
        return t

    # ─── Resumo executivo ────────────────────────────────────────────────────

    def _build_executive_summary(
        self,
        summary: dict[str, Any],
        files: list[dict[str, Any]],
        env: dict[str, Any],
        config: dict[str, Any],
    ) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib import colors

        story: list[Any] = []
        story.append(Spacer(1, 35))  # espaço para o cabeçalho das páginas internas
        story.append(Paragraph("Resumo Executivo", self._h1()))
        story.append(Spacer(1, 10))

        # Descrição do score
        all_issues = [i for f in files for i in f.get("issues", [])]
        score = self._compute_score(summary, files)
        color = _score_color(score)

        intro = (
            f"Esta análise avaliou <b>{summary.get('total_files', 0)}</b> arquivo(s) "
            f"e encontrou <b>{summary.get('total_issues', 0)}</b> issue(s) de acessibilidade. "
            f"O score de acessibilidade geral é <b>{int(round(score))}/100</b> "
            f"(<b>{_score_label(score)}</b>). "
            f"Foram corrigidos automaticamente <b>{summary.get('issues_fixed', 0)}</b> "
            f"issue(s) com taxa de sucesso de "
            f"<b>{summary.get('success_rate', 0):.1f}%</b>."
        )
        story.append(Paragraph(intro, self._body()))
        story.append(Spacer(1, 14))

        # Tabela de métricas resumidas
        rows = [
            ["Métrica", "Valor"],
            ["Arquivos analisados", str(summary.get("total_files", 0))],
            ["Arquivos com issues", str(summary.get("files_with_issues", 0))],
            ["Issues totais", str(summary.get("total_issues", 0))],
            ["Issues alta confiança (≥2 ferramentas)", str(summary.get("high_confidence_issues", 0))],
            ["Issues corrigidos", str(summary.get("issues_fixed", 0))],
            ["Issues pendentes", str(summary.get("issues_pending", 0))],
            ["Taxa de sucesso", f"{summary.get('success_rate', 0):.1f}%"],
            ["Tempo total de scan", f"{summary.get('total_time_seconds', 0):.1f}s"],
        ]

        cw = (self.PAGE_W - 2 * self.MARGIN) * 0.5
        t = Table(rows, colWidths=[cw, cw])
        t.setStyle(self._table_style())
        story.append(t)
        story.append(Spacer(1, 16))

        # Ambiente
        story.append(Paragraph("Ambiente de Execução", self._h2()))
        story.append(Spacer(1, 6))

        tool_versions = env.get("tool_versions", {}) or {}
        env_rows = [
            ["Componente", "Valor"],
            ["Modelo LLM", env.get("llm_model", "—")],
            ["Python", env.get("python_version", "—")],
            ["Sistema Operacional", env.get("os", "—")],
            ["Consenso mínimo (ferramentas)", str(config.get("min_tool_consensus", "—"))],
            ["Tentativas máximas", str(config.get("max_retries", "—"))],
        ]
        for tool, ver in sorted(tool_versions.items()):
            env_rows.append([f"Scanner: {tool}", ver])

        t2 = Table(env_rows, colWidths=[cw, cw])
        t2.setStyle(self._table_style())
        story.append(t2)

        return story

    # ─── Breakdown por categoria ─────────────────────────────────────────────

    def _build_category_breakdown(self, files: list[dict[str, Any]]) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        story: list[Any] = []
        story.append(Paragraph("Breakdown por Categoria", self._h1()))
        story.append(Spacer(1, 8))

        all_issues = [i for f in files for i in f.get("issues", [])]
        if not all_issues:
            story.append(Paragraph("Nenhum issue encontrado.", self._body()))
            return story

        by_type: Counter[str] = Counter(i.get("type", "other") for i in all_issues)
        total = len(all_issues)

        rows = [["Categoria", "Issues", "% do Total", "Gravidade Média"]]
        for issue_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
            type_issues = [i for i in all_issues if i.get("type") == issue_type]
            impacts = [i.get("impact", "minor") for i in type_issues]
            impact_score = sum(
                {"critical": 4, "serious": 3, "moderate": 2, "minor": 1}.get(imp, 1)
                for imp in impacts
            ) / len(impacts)
            if impact_score >= 3.5:
                avg_impact = "Crítico/Sério"
            elif impact_score >= 2.5:
                avg_impact = "Moderado/Sério"
            elif impact_score >= 1.5:
                avg_impact = "Moderado"
            else:
                avg_impact = "Menor"

            icon = _TYPE_ICON.get(issue_type, "•")
            rows.append([
                f"{icon}  {issue_type.replace('-', ' ').title()}",
                str(count),
                f"{count/total*100:.1f}%",
                avg_impact,
            ])

        cw = (self.PAGE_W - 2 * self.MARGIN)
        t = Table(rows, colWidths=[cw * 0.40, cw * 0.15, cw * 0.22, cw * 0.23])
        style = self._table_style()

        # Colorir linha da categoria com a cor do tipo
        type_list = sorted(by_type.keys(), key=lambda k: -by_type[k])
        for row_i, issue_type in enumerate(type_list, start=1):
            c = _TYPE_COLOR.get(issue_type, _GRAY_MID)
            rl_c = self._rl_color(c)
            style.add("TEXTCOLOR", (0, row_i), (0, row_i), rl_c)
            style.add("FONTNAME",  (0, row_i), (0, row_i), "Helvetica-Bold")

        t.setStyle(style)
        story.append(t)
        story.append(Spacer(1, 16))

        # Mini-barras de proporção (visual à la Lighthouse)
        story.append(Paragraph("Distribuição Visual por Categoria", self._h2()))
        story.append(Spacer(1, 6))
        story.append(self._make_bar_chart(by_type, total))

        return story

    def _make_bar_chart(self, counter: Counter[str], total: int) -> Any:
        """Cria barras horizontais simples como in Lighthouse."""
        from reportlab.platypus import Table, TableStyle, Paragraph
        from reportlab.graphics.shapes import Drawing, Rect
        from reportlab.graphics import renderPDF
        from reportlab.platypus.flowables import Flowable
        from reportlab.lib import colors

        BAR_W = self.PAGE_W - 2 * self.MARGIN - 160
        BAR_H = 14
        ROW_H = 22

        class _BarFlowable(Flowable):
            def __init__(self_, drawing: Drawing) -> None:
                Flowable.__init__(self_)
                self_._d = drawing
                self_.width  = BAR_W
                self_.height = BAR_H + 4

            def draw(self_) -> None:
                renderPDF.draw(self_._d, self_.canv, 0, 0)

        rows = []
        for issue_type, count in sorted(counter.items(), key=lambda x: -x[1]):
            pct = count / total if total > 0 else 0
            c = _TYPE_COLOR.get(issue_type, _GRAY_MID)

            d = Drawing(BAR_W, BAR_H + 4)
            d.add(Rect(0, 2, BAR_W, BAR_H,
                       fillColor=self._rl_color(_GRAY_LIGHT), strokeColor=None))
            filled = max(4.0, BAR_W * pct)
            d.add(Rect(0, 2, filled, BAR_H,
                       fillColor=self._rl_color(c), strokeColor=None))

            lbl_style = self._style("BarLabel", fontSize=8,
                                    textColor=colors.HexColor("#444444"))
            count_style = self._style("BarCount", fontSize=8,
                                      fontName="Helvetica-Bold",
                                      textColor=self._rl_color(c))

            rows.append([
                Paragraph(f"<b>{issue_type.replace('-', ' ').title()}</b>", lbl_style),
                _BarFlowable(d),
                Paragraph(f"{count} ({pct*100:.0f}%)", count_style),
            ])

        lw = 100
        bw = BAR_W
        rw = 60
        t = Table(rows, colWidths=[lw, bw, rw], rowHeights=[ROW_H] * len(rows))
        t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return t

    # ─── Breakdown por WCAG ──────────────────────────────────────────────────

    def _build_wcag_breakdown(self, files: list[dict[str, Any]]) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        story: list[Any] = []
        story.append(Spacer(1, 16))
        story.append(Paragraph("Breakdown por Critério WCAG", self._h1()))
        story.append(Spacer(1, 8))

        all_issues = [i for f in files for i in f.get("issues", [])]
        if not all_issues:
            story.append(Paragraph("Nenhum issue encontrado.", self._body()))
            return story

        by_wcag: defaultdict[str, list[dict]] = defaultdict(list)
        for issue in all_issues:
            key = issue.get("wcag_criteria") or "N/A"
            by_wcag[key].append(issue)

        rows = [["Critério WCAG", "Descrição", "Issues", "Pior Impacto"]]
        impact_rank = {"critical": 4, "serious": 3, "moderate": 2, "minor": 1}

        for crit, issues in sorted(by_wcag.items(), key=lambda x: -len(x[1])):
            desc = _WCAG_LABELS.get(crit, "—")
            worst = max(issues, key=lambda i: impact_rank.get(i.get("impact", "minor"), 1))
            worst_impact = worst.get("impact", "minor")
            rows.append([crit, desc, str(len(issues)), _IMPACT_LABEL.get(worst_impact, worst_impact)])

        cw = self.PAGE_W - 2 * self.MARGIN
        t = Table(rows, colWidths=[cw * 0.18, cw * 0.45, cw * 0.12, cw * 0.25])
        style = self._table_style()

        # Colorir a coluna de pior impacto
        impact_list = [
            max(issues, key=lambda i: impact_rank.get(i.get("impact", "minor"), 1)).get("impact", "minor")
            for _, issues in sorted(by_wcag.items(), key=lambda x: -len(x[1]))
        ]
        for row_i, imp in enumerate(impact_list, start=1):
            c = _IMPACT_COLOR.get(imp, _GRAY_MID)
            style.add("TEXTCOLOR", (3, row_i), (3, row_i), self._rl_color(c))
            style.add("FONTNAME",  (3, row_i), (3, row_i), "Helvetica-Bold")

        t.setStyle(style)
        story.append(t)

        return story

    # ─── Findings por arquivo ────────────────────────────────────────────────

    def _build_file_findings(self, files: list[dict[str, Any]]) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, KeepTogether, HRFlowable
        from reportlab.lib import colors

        story: list[Any] = []
        story.append(Paragraph("Findings Detalhados por Arquivo", self._h1()))
        story.append(Spacer(1, 8))

        files_with_issues = [f for f in files if f.get("issues")]
        if not files_with_issues:
            story.append(Paragraph("Nenhum arquivo com issues encontrado. ✓", self._body()))
            return story

        for file_entry in files_with_issues:
            story.append(Spacer(1, 8))
            story += self._build_file_card(file_entry)

        return story

    def _build_file_card(self, file_entry: dict[str, Any]) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
        from reportlab.lib import colors

        story: list[Any] = []
        issues  = file_entry.get("issues", [])
        fix     = file_entry.get("fix")
        fname   = _xesc(Path(str(file_entry.get("file", "unknown"))).name)
        tools   = _xesc(", ".join(file_entry.get("tools_used", [])))
        n_issues = len(issues)
        scan_mode = file_entry.get("scan_mode", "desktop")
        screenshot_path = file_entry.get("screenshot_path")

        fixed_count  = fix.get("issues_fixed", 0) if fix else 0
        fix_ok       = fix.get("success", False)  if fix else False

        # ── Cabeçalho do arquivo ──
        status_icon = "✓" if fix_ok else ("⚠" if n_issues > 0 else "✓")
        status_color = _GREEN if fix_ok else (_RED if n_issues > 0 else _GREEN)

        header_left = Paragraph(
            f"<b>{fname}</b>  "
            f"<font color='#{self._hex(status_color)}'>{status_icon}</font>",
            self._style(
                "FileHeader",
                fontSize=10,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor("#1A237E"),
                leading=14,
            ),
        )
        header_right = Paragraph(
            f"<font color='#666666'>{n_issues} issue(s) &nbsp;·&nbsp; "
            f"Corrigidos: {fixed_count} &nbsp;·&nbsp; "
            f"Modo: {scan_mode} &nbsp;·&nbsp; "
            f"Tools: {tools or '—'}</font>",
            self._style("FileHeaderRight", fontSize=7.5, textColor=colors.HexColor("#888888"), alignment=2),
        )

        hdr_table = Table(
            [[header_left, header_right]],
            colWidths=[(self.PAGE_W - 2 * self.MARGIN) * 0.55,
                       (self.PAGE_W - 2 * self.MARGIN) * 0.45],
        )
        hdr_table.setStyle(TableStyle([
            ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#0D47A1")),
            ("TOPPADDING",    (0, 0), (-1, 0), 4),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ]))
        story.append(hdr_table)
        story.append(Spacer(1, 4))

        # ── Screenshot do componente (se disponível) ──
        if screenshot_path:
            shot_p = Path(str(screenshot_path))
            if shot_p.exists():
                try:
                    from reportlab.platypus import Image as RLImage
                    max_w = self.PAGE_W - 2 * self.MARGIN
                    img = RLImage(str(shot_p))
                    aspect = img.imageHeight / img.imageWidth if img.imageWidth > 0 else 0.5
                    draw_w = min(max_w, img.imageWidth)
                    draw_h = draw_w * aspect
                    # Limitar altura a 180pt para não dominar a página
                    if draw_h > 180:
                        draw_h = 180.0
                        draw_w = draw_h / aspect
                    img.drawWidth  = draw_w
                    img.drawHeight = draw_h
                    caption = Paragraph(
                        f"<i><font color='#888888' size='7'>Preview do componente: {fname}</font></i>",
                        self._style("ScreenshotCaption", fontSize=7, leading=10,
                                    textColor=colors.HexColor("#888888"), alignment=1),
                    )
                    shot_table = Table(
                        [[img], [caption]],
                        colWidths=[draw_w],
                    )
                    shot_table.setStyle(TableStyle([
                        ("ALIGN",          (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
                        ("TOPPADDING",     (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
                        ("BOX",            (0, 0), (-1, 0),  0.5, colors.HexColor("#CCCCCC")),
                        ("BACKGROUND",     (0, 0), (-1, 0),  colors.HexColor("#F8F8F8")),
                    ]))
                    # Centrar o card de screenshot na largura da página
                    outer = Table(
                        [[shot_table]],
                        colWidths=[max_w],
                    )
                    outer.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
                    story.append(outer)
                    story.append(Spacer(1, 8))
                except Exception as e:
                    log.debug("pdf_screenshot_embed_failed", error=str(e)[:120])

        # ── Issues do arquivo ──
        for issue in issues[:20]:
            story.append(self._build_issue_card(issue))

        if len(issues) > 20:
            story.append(Paragraph(
                f"<i>… e mais {len(issues) - 20} issue(s) não exibidos.</i>",
                self._style("MoreIssues", fontSize=8, textColor=colors.HexColor("#888888")),
            ))

        story.append(Spacer(1, 6))
        story.append(HRFlowable(
            width="100%", thickness=0.5,
            color=colors.HexColor("#DDDDDD"),
        ))

        return story

    def _build_issue_card(self, issue: dict[str, Any]) -> Any:
        from reportlab.platypus import Table, TableStyle, Paragraph, Spacer
        from reportlab.lib import colors

        impact      = issue.get("impact", "minor")
        issue_type  = issue.get("type", "other")
        wcag        = _xesc(issue.get("wcag_criteria") or "N/A")
        confidence  = _xesc(issue.get("confidence", "low"))
        # message e selector vêm dos scanners e contêm <, >, & → escapar para
        # não quebrar o parser de Paragraph do reportlab (bug do scan Angular).
        message     = _xesc((issue.get("message", "") or "")[:160])
        selector    = _xesc((issue.get("selector", "") or "")[:80])
        found_by    = _xesc(", ".join(issue.get("found_by", [])))
        consensus   = issue.get("tool_consensus", 1)

        impact_color = _IMPACT_COLOR.get(impact, _GRAY_MID)
        type_color   = _TYPE_COLOR.get(issue_type, _GRAY_MID)

        type_icon = _TYPE_ICON.get(issue_type, "•")

        # Coluna esquerda: indicador de impacto
        indicator = Paragraph(
            f"<font color='#{self._hex(impact_color)}'>"
            f"<b>{_IMPACT_LABEL.get(impact, impact).upper()}</b>"
            f"</font>",
            self._style("ImpactLbl", fontSize=7, fontName="Helvetica-Bold", alignment=1, leading=9),
        )

        # Conteúdo principal
        title_p = Paragraph(
            f"<font color='#{self._hex(type_color)}'><b>{type_icon} {issue_type.replace('-',' ').title()}</b></font>"
            f"  <font color='#444444' size='8'>{message}</font>",
            self._style("IssueTitle", fontSize=8.5, leading=12),
        )
        meta_p = Paragraph(
            f"<font color='#888888' size='7'>"
            f"WCAG <b>{wcag}</b> &nbsp;·&nbsp; "
            f"Selector: <font face='Courier'>{selector}</font> &nbsp;·&nbsp; "
            f"Confiança: <b>{confidence}</b> "
            f"({consensus} ferramenta{'s' if consensus > 1 else ''}) &nbsp;·&nbsp; "
            f"Detectado por: {found_by}"
            f"</font>",
            self._style("IssueMeta", fontSize=7, leading=10, textColor=colors.HexColor("#666666")),
        )

        left_w  = 55
        right_w = self.PAGE_W - 2 * self.MARGIN - left_w - 4

        t = Table(
            [[indicator, [title_p, Spacer(1, 2), meta_p]]],
            colWidths=[left_w, right_w],
            rowHeights=None,
        )

        border_c = self._rl_color(impact_color)
        t.setStyle(TableStyle([
            ("VALIGN",         (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",    (0, 0), (0, -1), 4),
            ("RIGHTPADDING",   (0, 0), (0, -1), 4),
            ("TOPPADDING",     (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
            ("LEFTPADDING",    (1, 0), (1, -1), 8),
            ("BACKGROUND",     (0, 0), (0, -1), self._rl_color(
                tuple(min(1.0, v + 0.82) for v in impact_color)  # type: ignore[arg-type]
            )),
            ("BACKGROUND",     (1, 0), (1, -1), colors.HexColor("#FAFAFA")),
            ("LINEBEFORE",     (0, 0), (0, -1), 3, border_c),
            ("LINEBELOW",      (0, 0), (-1, -1), 0.3, colors.HexColor("#EEEEEE")),
            ("TOPPADDING",     (0, 0), (0, -1), 8),
        ]))

        from reportlab.platypus import Spacer as _Sp
        from reportlab.platypus import KeepTogether
        return KeepTogether([t, _Sp(1, 3)])

    # ─── Rodapé técnico ──────────────────────────────────────────────────────

    def _build_footer(self, data: dict[str, Any]) -> list[Any]:
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        story: list[Any] = []
        story.append(Spacer(1, 35))
        story.append(Paragraph("Informações Técnicas", self._h1()))
        story.append(Spacer(1, 8))

        exec_id = data.get("execution_id", "—")
        schema  = data.get("schema_version", "—")
        proto   = data.get("protocol_version", "—")
        ts      = data.get("timestamp", "—")

        rows = [
            ["Campo", "Valor"],
            ["Execution ID", exec_id],
            ["Schema Version", schema],
            ["Protocol Version", proto],
            ["Timestamp", ts],
            ["Nível WCAG", data.get("wcag_level", "—")],
            ["Modo do relatório", data.get("report_label", "desktop")],
            ["Modos de scan", ", ".join(data.get("scan_modes", []))],
        ]

        tool_versions = (data.get("environment") or {}).get("tool_versions") or {}
        for tool, ver in sorted(tool_versions.items()):
            rows.append([f"Versão: {tool}", ver])

        cw = (self.PAGE_W - 2 * self.MARGIN) * 0.5
        t = Table(rows, colWidths=[cw, cw])
        t.setStyle(self._table_style())
        story.append(t)
        story.append(Spacer(1, 20))

        # Nota metodológica
        note = (
            "Este relatório foi gerado automaticamente pelo sistema <b>a11y-autofix</b> "
            "usando protocolo de detecção multi-ferramenta com consenso. "
            "Os scores são calculados com base na proporção de issues de alta confiança "
            "em relação ao total de issues detectados. "
            "Scores ≥ 90 = Bom, 50–89 = Precisa melhorar, &lt; 50 = Ruim."
        )
        story.append(Paragraph(note, self._style(
            "Note", fontSize=8, textColor=colors.HexColor("#666666"), leading=12,
        )))

        return story

    # ─── Helpers de score ────────────────────────────────────────────────────

    def _compute_score(self, summary: dict[str, Any], files: list[dict[str, Any]]) -> float:
        """
        Calcula score 0-100 inspirado no Lighthouse.

        Fórmula: 100 - (penalidade por issues ponderados por impacto e confiança)
        Máximo de penalidade = 100 (score mínimo = 0).
        """
        all_issues = [i for f in files for i in f.get("issues", [])]
        if not all_issues:
            return 100.0

        impact_w = {"critical": 4.0, "serious": 2.5, "moderate": 1.5, "minor": 0.5}
        conf_w   = {"high": 1.0, "medium": 0.6, "low": 0.3}

        total_files = max(summary.get("total_files", 1), 1)
        penalty = 0.0
        for issue in all_issues:
            iw = impact_w.get(issue.get("impact", "minor"), 0.5)
            cw = conf_w.get(issue.get("confidence", "low"), 0.3)
            penalty += iw * cw

        # Normalizar pelo número de arquivos e escalar para 0-100
        norm_penalty = min(100.0, penalty / total_files * 10)
        return max(0.0, round(100.0 - norm_penalty, 1))

    # ─── Helpers de estilo ───────────────────────────────────────────────────

    def _rl_color(self, c: tuple[float, ...]) -> Any:
        from reportlab.lib.colors import Color
        return Color(_r(c), _g(c), _b(c))  # type: ignore[arg-type]

    def _hex(self, c: tuple[float, float, float]) -> str:
        return "{:02X}{:02X}{:02X}".format(
            int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)
        )

    def _style(self, name: str, **kwargs: Any) -> Any:
        from reportlab.lib.styles import ParagraphStyle
        return ParagraphStyle(name=name, **kwargs)

    def _h1(self) -> Any:
        from reportlab.lib.colors import HexColor
        return self._style(
            "H1",
            fontSize=14,
            fontName="Helvetica-Bold",
            textColor=HexColor("#0D47A1"),
            leading=18,
            spaceAfter=4,
        )

    def _h2(self) -> Any:
        from reportlab.lib.colors import HexColor
        return self._style(
            "H2",
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=HexColor("#1A237E"),
            leading=15,
            spaceAfter=3,
        )

    def _body(self) -> Any:
        from reportlab.lib.colors import HexColor
        return self._style(
            "Body",
            fontSize=9,
            textColor=HexColor("#333333"),
            leading=14,
        )

    def _table_style(self) -> Any:
        from reportlab.platypus import TableStyle
        from reportlab.lib import colors
        return TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0D47A1")),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F4FF")]),
            ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ])
