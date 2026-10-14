"""Annual service-charge statement (jaarafrekening servicekosten) as a clear, detailed PDF: letterhead with the logo, tenant and property
details, a summary of costs, advances and the balance, an itemised cost table with the allocation method for each line, how to settle
the balance and the tenant's right to inspect the supporting invoices. Headings are Dutch with English underneath."""
import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .agreement_pdf import GREEN, INK, LINE, MUTED, PALE, NumberedCanvas

METHODS = {'equal': ('Gelijk deel per woning', 'Equal share per unit'), 'floor_area': ('Aandeel naar woonoppervlak', 'Share by floor area'),
           'fixed_percentage': ('Overeengekomen percentage', 'Agreed percentage'), 'tenancy_days': ('Kosten per bezettingsdag', 'Cost per occupied day')}


def statement_pdf_bytes(company_name, tenant_name, address, year, occupied_days, year_days, allocations, cost_total, advances, balance, *,
                        company=None, property_label='', reference='', issued_on=None, logo=None):
    company = company or {'name': company_name}
    issued_on = issued_on or date.today().isoformat()
    esc = lambda value: escape(str(value or ''))
    money = lambda cents: f'€ {cents / 100:,.2f}'
    base = getSampleStyleSheet()
    body = ParagraphStyle('b', parent=base['Normal'], fontSize=9.4, leading=13.4, textColor=INK)
    st = {
        'body': body, 'bold': ParagraphStyle('bo', parent=body, fontName='Helvetica-Bold'),
        'label': ParagraphStyle('l', parent=body, fontName='Helvetica-Bold', fontSize=7.6, leading=10.5, textColor=MUTED),
        'small': ParagraphStyle('s', parent=body, fontSize=8, leading=11.2, textColor=MUTED),
        'right': ParagraphStyle('r', parent=body, alignment=2), 'rightb': ParagraphStyle('rb', parent=body, alignment=2, fontName='Helvetica-Bold'),
        'brand': ParagraphStyle('br', parent=body, fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=GREEN),
        'title': ParagraphStyle('t', parent=body, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=INK, alignment=2),
        'sub': ParagraphStyle('su', parent=body, fontName='Helvetica-Oblique', fontSize=9.5, leading=13, textColor=MUTED, alignment=2),
        'meta': ParagraphStyle('m', parent=body, fontSize=8.2, leading=12, textColor=MUTED, alignment=2),
        'h2': ParagraphStyle('h2', parent=body, fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=GREEN, spaceBefore=9, spaceAfter=0),
        'h2en': ParagraphStyle('h2e', parent=body, fontName='Helvetica-Oblique', fontSize=8.4, leading=11, textColor=MUTED, spaceAfter=5),
        'big': ParagraphStyle('bg', parent=body, fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=INK),
        'bigw': ParagraphStyle('bw', parent=body, fontName='Helvetica-Bold', fontSize=17, leading=21, textColor=colors.white),
        'white': ParagraphStyle('w', parent=body, fontName='Helvetica-Bold', fontSize=8, leading=11, textColor=colors.HexColor('#cfe8dd')),
    }
    P = lambda text, style='body': Paragraph(text, st[style])
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=13 * mm, bottomMargin=21 * mm,
                            title=f'Jaarafrekening servicekosten {year}', author=company.get('name', ''), subject=reference)
    width = doc.width
    story = []

    # ---- letterhead
    if logo:
        try:
            iw, ih = ImageReader(io.BytesIO(logo)).getSize()
            scale = min(52 * mm / iw, 17 * mm / ih)
            left = Image(io.BytesIO(logo), width=iw * scale, height=ih * scale, hAlign='LEFT')
        except Exception:
            left = P(esc(company.get('name')), 'brand')
    else:
        left = P(esc(company.get('name')), 'brand')
    contact = f'{esc(company.get("name"))}<br/>{esc(company.get("address"))}' + (f'<br/>KvK {esc(company.get("kvk"))}' if company.get('kvk') else '')
    story.append(Table([[left, [P('JAARAFREKENING SERVICEKOSTEN', 'title'), P('Annual service-charge statement', 'sub'), Spacer(1, 3), P(contact, 'meta')]]], colWidths=[width * .42, width * .58],
                       style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)])))
    story += [Spacer(1, 4), HRFlowable(width='100%', thickness=2.2, color=GREEN), Spacer(1, 9)]

    # ---- who, what, when
    def box(label_nl, label_en, lines):
        return [P(f'{label_nl} / {label_en}'.upper(), 'label'), Spacer(1, 3)] + [P(x) for x in lines]
    details = Table([[box('Huurder', 'Tenant', [f'<b>{esc(tenant_name)}</b>', esc(address)]),
                      box('Woning', 'Property', [f'<b>{esc(property_label or address)}</b>', f'Kalenderjaar {year}', f'Bewoond {occupied_days} van {year_days} dagen']),
                      box('Afrekening', 'Statement', [f'Nummer: <b>{esc(reference)}</b>', f'Datum: {esc(issued_on)}', f'Periode: 1 jan {year} – 31 dec {year}'])]],
                    colWidths=[width * .31, width * .35, width * .34],
                    style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BACKGROUND', (0, 0), (-1, -1), PALE), ('BOX', (0, 0), (-1, -1), .6, LINE), ('INNERGRID', (0, 0), (-1, -1), .4, LINE),
                                      ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    story += [details, Spacer(1, 10)]

    # ---- three figures
    due = balance > 0
    cards = Table([[
        [P('TOTALE KOSTEN / TOTAL COSTS', 'label'), Spacer(1, 4), P(money(cost_total), 'big')],
        [P('BETAALDE VOORSCHOTTEN / ADVANCES PAID', 'label'), Spacer(1, 4), P(money(advances), 'big')],
        [P('TE BETALEN / AMOUNT DUE' if due else 'TERUG TE ONTVANGEN / REFUND DUE' if balance < 0 else 'SALDO / BALANCE', 'white'), Spacer(1, 4), P(money(abs(balance)), 'bigw')]]],
        colWidths=[width * .33, width * .33, width * .34],
        style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (1, 0), .6, LINE), ('INNERGRID', (0, 0), (1, 0), .4, LINE), ('BACKGROUND', (2, 0), (2, 0), GREEN),
                          ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 11), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    story += [cards]

    # ---- itemised costs
    story += [P('Specificatie van de kosten', 'h2'), P('Itemised costs and how your share was calculated', 'h2en')]
    rows = [[P('KOSTEN / COST', 'label'), P('GEBOUWKOSTEN<br/>BUILDING COST', 'label'), P('VERDELING / ALLOCATION', 'label'), P('UW AANDEEL<br/>YOUR SHARE', 'label')]]
    for item in allocations:
        method = item.get('allocation_method', 'tenancy_days')
        nl, en = METHODS.get(method, ('Verdeling', 'Allocation'))
        weight = item.get('unit_weight', 1)
        occupied = item.get('occupied_days', occupied_days)
        days = item.get('year_days', year_days)
        calc = f'{nl}<br/><font color="#687b74">{en}</font><br/>{weight * 100:.2f}% × {occupied}/{days} dagen / days'
        rows.append([P(f'<b>{esc(item.get("category"))}</b><br/><font color="#687b74">{esc(item.get("supplier"))}</font>'), P(money(item['amount_cents']), 'right'), P(calc, 'small'), P(f'<b>{money(item["allocated_cents"])}</b>', 'right')])
    if len(rows) == 1:
        rows.append([P('Geen kosten in deze periode / No costs in this period', 'small'), '', '', P(money(0), 'right')])
    rows.append([P('<b>Totaal uw aandeel / Total of your share</b>'), '', '', P(f'<b>{money(cost_total)}</b>', 'right')])
    table = Table(rows, colWidths=[width * .27, width * .19, width * .36, width * .18], repeatRows=1,
                  style=TableStyle([('BACKGROUND', (0, 0), (-1, 0), PALE), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -2), .4, LINE), ('BOX', (0, 0), (-1, -1), .6, LINE),
                                    ('SPAN', (0, -1), (2, -1)), ('BACKGROUND', (0, -1), (-1, -1), PALE), ('LINEABOVE', (0, -1), (-1, -1), .8, INK),
                                    ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    story += [table]

    # ---- settlement
    story += [P('Verrekening', 'h2'), P('Settlement', 'h2en')]
    settle = Table([[P('Uw aandeel in de kosten / Your share of the costs', 'body'), P(money(cost_total), 'right')],
                    [P('Af: betaalde voorschotten / Less: advances paid', 'body'), P(f'− {money(advances)}', 'right')],
                    [P('<b>Te betalen / Amount due</b>' if due else '<b>Terug te ontvangen / Refund due</b>' if balance < 0 else '<b>Saldo / Balance</b>', 'body'), P(f'<b>{money(abs(balance))}</b>', 'rightb')]],
                   colWidths=[width * .72, width * .28],
                   style=TableStyle([('LINEBELOW', (0, 0), (-1, 1), .4, LINE), ('LINEABOVE', (0, 2), (-1, 2), .9, INK), ('BACKGROUND', (0, 2), (-1, 2), PALE),
                                     ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    story += [settle, Spacer(1, 8)]
    if due and company.get('payment_instructions'):
        story += [P(f'<b>Betaling / How to pay:</b> {esc(company["payment_instructions"])} Vermeld het afrekeningnummer {esc(reference)}.', 'body')]
    elif due:
        story += [P('Neem contact op met uw verhuurder voor de betaalgegevens. / Contact your landlord for the payment details.', 'body')]
    elif balance < 0:
        story += [P('Het te veel betaalde bedrag wordt met u verrekend of aan u terugbetaald, zoals met uw verhuurder afgesproken. / The overpaid amount is settled or refunded as agreed with your landlord.', 'body')]

    # ---- notes
    notes = ['De kosten zijn verdeeld over de bezettingsdagen van uw huurperiode. Leegstand komt voor rekening van de verhuurder. / Costs are allocated over the occupied days of your tenancy. Vacancy remains the landlord’s share.',
             'U kunt de onderliggende facturen en bescheiden inzien. Vraag er gerust naar bij uw verhuurder. / You may inspect the supporting invoices and documents. Ask your landlord at any time.',
             'Bent u het niet eens met deze afrekening? Meld dit schriftelijk, bij voorkeur via het huurdersportaal. / If you disagree with this statement, tell your landlord in writing, preferably through the tenant portal.']
    story += [Spacer(1, 4), KeepTogether([P('Opmerkingen', 'h2'), P('Notes', 'h2en')] + [P('• ' + esc(n), 'small') for n in notes])]

    footer = (reference + '  ·  ' if reference else '') + str(company.get('name', ''))[:26]

    def make_canvas(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.footer_text = footer
        c.initials = False
        return c

    doc.build(story, canvasmaker=make_canvas)
    return stream.getvalue()
