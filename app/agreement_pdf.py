"""A professional Dutch-style tenancy agreement PDF: letterhead with the company logo, key-facts table, numbered articles with the
English translation under each clause, initials boxes and page numbers in the footer ("Pagina x van y"), an appendix with the tenant's
details and a signature block. The text comes from the (editable) contract text, see app/lease_starter.py for the layout syntax."""
import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import HRFlowable, Image, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor('#19372d')
GREEN = colors.HexColor('#176b55')
MUTED = colors.HexColor('#687b74')
LINE = colors.HexColor('#dce7e1')
PALE = colors.HexColor('#f1f6f3')


class NumberedCanvas(pdfcanvas.Canvas):
    """Two passes, so every page can say 'Pagina x van y'."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._total = total
            self.draw_footer()
            super().showPage()
        super().save()

    def draw_footer(self):
        width, _ = A4
        self.saveState()
        self.setStrokeColor(LINE)
        self.line(22 * mm, 17 * mm, width - 22 * mm, 17 * mm)
        self.setFont('Helvetica', 7.5)
        self.setFillColor(MUTED)
        self.drawString(22 * mm, 12 * mm, getattr(self, 'footer_text', ''))
        self.drawCentredString(width / 2, 12 * mm, f'Pagina {self._pageNumber} van {self._total}  ·  Page {self._pageNumber} of {self._total}')
        if not getattr(self, 'initials', True):
            self.restoreState()
            return
        # initials boxes, as on a paper agreement
        self.drawRightString(width - 22 * mm - 32 * mm, 12 * mm, 'Paraaf / Initials')
        for x in (width - 22 * mm - 28 * mm, width - 22 * mm - 13 * mm):
            self.setStrokeColor(colors.HexColor('#9aaba4'))
            self.roundRect(x, 8.6 * mm, 12 * mm, 6.4 * mm, 1.2 * mm, stroke=1, fill=0)
        self.restoreState()


def make_styles():
    base = getSampleStyleSheet()
    body = ParagraphStyle('body', parent=base['Normal'], fontName='Helvetica', fontSize=9.6, leading=14, textColor=INK, spaceAfter=2)
    return {
        'body': body,
        'en': ParagraphStyle('en', parent=body, fontName='Helvetica-Oblique', fontSize=8.6, leading=12, textColor=MUTED, spaceAfter=8),
        'title': ParagraphStyle('title', parent=body, fontName='Helvetica-Bold', fontSize=22, leading=26, textColor=INK, spaceAfter=0),
        'subtitle': ParagraphStyle('subtitle', parent=body, fontName='Helvetica-Oblique', fontSize=11.5, leading=15, textColor=MUTED, spaceAfter=0),
        'h2': ParagraphStyle('h2', parent=body, fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=GREEN, spaceBefore=11, spaceAfter=0),
        'h2en': ParagraphStyle('h2en', parent=body, fontName='Helvetica-Oblique', fontSize=8.8, leading=12, textColor=MUTED, spaceAfter=4),
        'meta': ParagraphStyle('meta', parent=body, fontSize=8.2, leading=12, textColor=MUTED, alignment=2),
        'brand': ParagraphStyle('brand', parent=body, fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=GREEN),
        'small': ParagraphStyle('small', parent=body, fontSize=8, leading=11.5, textColor=MUTED),
        'cap': ParagraphStyle('cap', parent=body, fontSize=8.4, leading=12, textColor=MUTED, spaceAfter=0),
        'cell': ParagraphStyle('cell', parent=body, fontSize=9.2, leading=13),
        'cellb': ParagraphStyle('cellb', parent=body, fontName='Helvetica-Bold', fontSize=8.6, leading=12, textColor=MUTED),
    }


def agreement_pdf_bytes(text, *, company, logo=None, reference='', signatures=None, subject=''):
    """company: dict with name, address, kvk. signatures: [{'label','caption','image'}]."""
    st = make_styles()
    esc = lambda value: escape(str(value or ''))
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=18 * mm, bottomMargin=24 * mm,
                            title=subject or 'Huurovereenkomst woonruimte', author=company.get('name', ''), subject=reference)
    story = []

    # ---- masthead: logo left, reference right, a green rule
    if logo:
        try:
            iw, ih = ImageReader(io.BytesIO(logo)).getSize()
            scale = min(52 * mm / iw, 17 * mm / ih)
            left = Image(io.BytesIO(logo), width=iw * scale, height=ih * scale, hAlign='LEFT')
        except Exception:
            left = Paragraph(esc(company.get('name')), st['brand'])
    else:
        left = Paragraph(esc(company.get('name')), st['brand'])
    right = Paragraph(f'<b>{esc(company.get("name"))}</b><br/>{esc(company.get("address"))}<br/>KvK {esc(company.get("kvk"))}' + (f'<br/>Kenmerk / Reference: <b>{esc(reference)}</b>' if reference else ''), st['meta'])
    story += [Table([[left, right]], colWidths=[84 * mm, 82 * mm], style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)])),
              Spacer(1, 6), HRFlowable(width='100%', thickness=2.2, color=GREEN), Spacer(1, 14)]

    lines = text.strip().split(chr(10))
    rows, bullets = [], []

    def flush_rows():
        if rows:
            table = Table([[Paragraph(esc(a), st['cellb']), Paragraph(esc(b), st['cell'])] for a, b in rows], colWidths=[62 * mm, 104 * mm],
                          style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BACKGROUND', (0, 0), (0, -1), PALE), ('BOX', (0, 0), (-1, -1), .6, LINE), ('INNERGRID', (0, 0), (-1, -1), .4, LINE),
                                            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
            story.append(table)
            story.append(Spacer(1, 4))
            rows.clear()

    def flush_bullets():
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(esc(b), st['body']), leftIndent=12) for b in bullets], bulletType='bullet', start='•', leftIndent=14))
            bullets.clear()

    def flush():
        flush_rows()
        flush_bullets()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif line.startswith('# '):
            flush()
            nl, _, en = line[2:].partition(' | ')
            story.append(Paragraph(esc(nl), st['title']))
            if en:
                story.append(Paragraph(esc(en), st['subtitle']))
            story.append(Spacer(1, 10))
        elif line.startswith('## '):
            flush()
            nl, _, en = line[3:].partition(' | ')
            block = [Paragraph(esc(nl), st['h2'])]
            if en:
                block.append(Paragraph(esc(en), st['h2en']))
            else:
                block.append(Spacer(1, 3))
            story.append(KeepTogether(block))
        elif line.startswith('|') and line.rstrip().endswith('|'):
            flush_bullets()
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            rows.append((cells[0], cells[1] if len(cells) > 1 else ''))
        elif line.startswith('(EN) '):
            flush()
            story.append(Paragraph(esc(line[5:]), st['en']))
        elif line.startswith('- '):
            flush_rows()
            bullets.append(line[2:])
        else:
            flush()
            story.append(Paragraph(esc(line), st['body']))
    flush()

    # ---- signatures
    if signatures:
        cells = []
        for sig in signatures:
            parts = []
            if sig.get('image'):
                iw, ih = ImageReader(io.BytesIO(sig['image'])).getSize()
                scale = min(60 * mm / iw, 22 * mm / ih)
                parts.append(Image(io.BytesIO(sig['image']), width=iw * scale, height=ih * scale, hAlign='LEFT'))
                parts.append(Spacer(1, max(0, 24 * mm - ih * scale)))
            else:
                parts.append(Spacer(1, 24 * mm))
            parts += [HRFlowable(width='92%', thickness=0.7, color=colors.HexColor('#9aaba4'), hAlign='LEFT'), Spacer(1, 2),
                      Paragraph('<b>' + esc(sig['label']) + '</b><br/>' + esc(sig['caption']).replace(chr(10), '<br/>'), st['cap'])]
            cells.append(parts)
        table = Table([cells], colWidths=[doc.width / len(cells)] * len(cells), style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
        story += [Spacer(1, 16), KeepTogether([Paragraph('Ondertekening', st['h2']), Paragraph('Signatures', st['h2en']), table])]

    footer = (reference + '  ·  ' if reference else '') + str(company.get('name', ''))[:26]

    def make_canvas(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.footer_text = footer
        return c

    doc.build(story, canvasmaker=make_canvas)
    return stream.getvalue()
