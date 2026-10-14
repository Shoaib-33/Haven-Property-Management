"""Build a polished, unfilled Dutch residential tenancy agreement demo PDF."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'Haven_Dutch_Residential_Tenancy_Agreement_Demo.pdf'
GREEN = colors.HexColor('#146B55')
DARK = colors.HexColor('#183C33')
MUTED = colors.HexColor('#60766E')
LINE = colors.HexColor('#D7E3DC')
PALE = colors.HexColor('#EFF5F1')
GOLD = colors.HexColor('#B78331')


class AgreementDoc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(filename, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                         topMargin=22*mm, bottomMargin=19*mm,
                         title='Residential Tenancy Agreement — Haven Demo Template',
                         author='Haven Property Management')
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      id='main', leftPadding=0, rightPadding=0, topPadding=2, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='agreement', frames=[frame], onPage=self.page_chrome))

    def page_chrome(self, canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(GREEN)
        canvas.roundRect(20*mm, h-16*mm, 8*mm, 8*mm, 2*mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', 12)
        canvas.drawCentredString(24*mm, h-13.8*mm, 'h')
        canvas.setFillColor(DARK)
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawString(31*mm, h-12.9*mm, 'HAVEN')
        canvas.setFillColor(MUTED)
        canvas.setFont('Helvetica', 7.5)
        canvas.drawRightString(w-20*mm, h-12.9*mm, 'RESIDENTIAL TENANCY AGREEMENT · DEMO TEMPLATE')
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(.6)
        canvas.line(20*mm, h-18*mm, w-20*mm, h-18*mm)
        canvas.line(20*mm, 14*mm, w-20*mm, 14*mm)
        canvas.setFillColor(MUTED)
        canvas.setFont('Helvetica', 7)
        canvas.drawString(20*mm, 9.5*mm, 'DEMO ONLY · Complete all fields and obtain Dutch legal review before use')
        canvas.drawRightString(w-20*mm, 9.5*mm, f'{doc.page}')
        canvas.restoreState()


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='Hero', parent=styles['Title'], fontName='Helvetica-Bold',
    fontSize=23, leading=27, textColor=DARK, alignment=TA_LEFT, spaceAfter=5))
styles.add(ParagraphStyle(name='SubtitleCustom', parent=styles['Normal'], fontSize=9.5,
    leading=14, textColor=MUTED, spaceAfter=12))
styles.add(ParagraphStyle(name='Section', parent=styles['Heading2'], fontName='Helvetica-Bold',
    fontSize=12.5, leading=16, textColor=DARK, spaceBefore=9, spaceAfter=6, keepWithNext=True))
styles.add(ParagraphStyle(name='BodyCustom', parent=styles['BodyText'], fontSize=8.7,
    leading=13, textColor=DARK, spaceAfter=5))
styles.add(ParagraphStyle(name='SmallCustom', parent=styles['BodyText'], fontSize=7.5,
    leading=10.5, textColor=MUTED, spaceAfter=3))
styles.add(ParagraphStyle(name='Label', parent=styles['BodyText'], fontName='Helvetica-Bold',
    fontSize=7.5, leading=10, textColor=MUTED, spaceAfter=3))
styles.add(ParagraphStyle(name='FieldText', parent=styles['BodyText'], fontSize=8.6,
    leading=12, textColor=DARK))
styles.add(ParagraphStyle(name='Notice', parent=styles['BodyText'], fontSize=8,
    leading=11.5, textColor=DARK))
styles.add(ParagraphStyle(name='SignatureMarker', parent=styles['BodyText'], fontName='Courier',
    fontSize=8, leading=11, textColor=GREEN))
styles.add(ParagraphStyle(name='CenteredSmall', parent=styles['SmallCustom'], alignment=TA_CENTER))


def P(text, style='BodyCustom'):
    return Paragraph(text, styles[style])


def fields(items, columns=2):
    cells = []
    for label, placeholder in items:
        cells.append([P(label.upper(), 'Label'), P(placeholder, 'FieldText')])
    rows = []
    for i in range(0, len(cells), columns):
        row = []
        for cell in cells[i:i+columns]:
            row.extend(cell)
        while len(row) < columns * 2:
            row.extend(['', ''])
        rows.append(row)
    widths = []
    for _ in range(columns):
        widths.extend([27*mm, (170*mm/columns)-27*mm])
    t = Table(rows, colWidths=widths, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),colors.white),
        ('BOX',(0,0),(-1,-1),.55,LINE),
        ('INNERGRID',(0,0),(-1,-1),.45,LINE),
        ('BACKGROUND',(0,0),(0,-1),PALE),
        ('BACKGROUND',(2,0),(2,-1),PALE),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
    ]))
    return t


def bullet(text):
    return P(f'<font color="#146B55">•</font> &nbsp;{text}')


story = [
    Spacer(1, 3*mm),
    P('Residential tenancy<br/>agreement', 'Hero'),
    P('A structured agreement template for a residential property in the Netherlands. Replace every bracketed field and attach the referenced schedules before signing.', 'SubtitleCustom'),
    Table([[P('<b>DEMO TEMPLATE</b><br/>This PDF is intentionally unfilled. It is not a ready-to-sign legal agreement. Confirm the correct tenancy type, rent, deposit, service-cost arrangements, required tenant information and current Dutch legal rules before use.', 'Notice')]], colWidths=[170*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),PALE),('BOX',(0,0),(-1,-1),.7,LINE),
        ('LINEBEFORE',(0,0),(0,0),3,GREEN),('LEFTPADDING',(0,0),(-1,-1),10),
        ('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
    ])),
    Spacer(1, 5*mm),
    P('1. The parties', 'Section'),
    P('The parties agree to rent the residential premises described below, subject to this agreement and its completed schedules.', 'BodyCustom'),
    fields([('Landlord / legal entity','[Full legal name]'),('Company / registration number','[If applicable]'),('Landlord address','[Street, postcode, city, country]'),('Landlord email and phone','[Contact details]'),('Tenant 1','[Full legal name]'),('Tenant 1 email and phone','[Contact details]'),('Tenant 2 (if applicable)','[Full legal name or “Not applicable”]'),('Tenant 2 email and phone','[Contact details or “Not applicable”]')]),
    P('2. The rented premises', 'Section'),
    fields([('Full address and unit','[Street, number, unit, postcode, city]'),('Property description','[Apartment / room / house; rooms and facilities]'),('Included areas','[Storage, garden, parking, shared areas, if any]'),('Furnishing / inventory','[Unfurnished / furnished; see Schedule A]')]),
    P('3. Start and duration', 'Section'),
    fields([('Start date','[YYYY-MM-DD]'),('Agreed duration / tenancy type','[Complete only after checking applicable rules]'),('End date (if lawful and applicable)','[YYYY-MM-DD or “Not applicable”]'),('Key handover date and time','[Date and time]')]),
    P('The parties record that any fixed end date, renewal, notice or termination arrangement must comply with the applicable mandatory Dutch tenancy rules. Attach the agreed written terms and any required explanation as Schedule B.', 'SmallCustom'),
    PageBreak(),
    P('Financial terms', 'Hero'),
    P('Record the rent and each additional amount separately. Do not describe an advance as a final service-cost settlement.', 'SubtitleCustom'),
    P('4. Rent and payment', 'Section'),
    fields([('Basic monthly rent','€ [Amount]'),('Monthly service-cost advance','€ [Amount]'),('Other agreed amount and basis','€ [Amount / “None”]'),('Total monthly amount due','€ [Amount]'),('Payment due date','[Day of each month]'),('First payment date / pro rata amount','[Date and amount]'),('Payment method / instructions','[Bank details or agreed method]'),('Reference to use','[Payment reference]')]),
    P('The basic rent and service-cost advance are separate amounts. Any service-cost advance and settlement must follow the parties’ written agreement and applicable rules. The landlord will provide the required itemized annual service-cost overview.', 'BodyCustom'),
    P('5. Deposit', 'Section'),
    fields([('Agreed deposit','€ [Amount]'),('Payment due date','[Date]'),('Payment instructions','[Method / reference]'),('Deposit receipt record','[To be completed when received]')]),
    P('The parties will record any permitted deduction and the supporting explanation in writing. Complete and check the deposit terms against current Dutch rules before signature.', 'SmallCustom'),
    P('6. Use and occupancy', 'Section'),
    fields([('Permitted residential use','[Describe the agreed use]'),('Named occupants','[Names of permitted occupants]'),('Shared facilities / house rules','[Attach Schedule C or state “None”]'),('Pets / other agreed conditions','[Terms or “None”]')]),
    P('7. Repairs, access and communication', 'Section'),
    P('Tenant contact for repairs: [email / telephone / portal]. Landlord or property manager contact: [name / email / telephone]. The parties will use the agreed repair-reporting process and follow applicable rules concerning repairs, access and emergencies. Attach any agreed maintenance responsibilities as Schedule D.', 'BodyCustom'),
    P('8. Rent changes and notices', 'Section'),
    P('Any rent change, service-cost adjustment, notice or termination must be made in writing and comply with the agreement and applicable rules. Do not use a percentage or notice period unless it has been checked for this tenancy. Contact for formal notices: [postal and email address].', 'BodyCustom'),
    PageBreak(),
    P('Schedules and confirmation', 'Hero'),
    P('Complete the relevant schedules and give the tenant a copy of the signed agreement and required accompanying information.', 'SubtitleCustom'),
    P('9. Documents attached to this agreement', 'Section'),
    bullet('<b>Schedule A —</b> Inventory, condition report, meter readings and key handover record.'),
    bullet('<b>Schedule B —</b> Tenancy type, duration and any additional lawful terms.'),
    bullet('<b>Schedule C —</b> House rules and shared-space arrangements, if applicable.'),
    bullet('<b>Schedule D —</b> Maintenance contacts and agreed repair-reporting procedure.'),
    bullet('<b>Schedule E —</b> Rent / points assessment (WWS) and required tenant information.'),
    bullet('<b>Service costs —</b> Agreed services, advance amount and annual settlement basis, if applicable.'),
    Spacer(1, 3*mm),
    P('10. Agreement and copies', 'Section'),
    P('By signing, each party confirms that the completed agreement and its attached schedules reflect the terms agreed between them. Each party should receive a copy of the completed and signed agreement.', 'BodyCustom'),
    fields([('Place','[City]'),('Date','[YYYY-MM-DD]')]),
    Spacer(1, 6*mm),
    P('LANDLORD SIGNATURE', 'Label'),
    P('{{HAVEN_LANDLORD_SIGNATURE}}', 'SignatureMarker'),
    Spacer(1, 11*mm),
    P('TENANT SIGNATURE', 'Label'),
    P('{{HAVEN_TENANT_SIGNATURE}}', 'SignatureMarker'),
    Spacer(1, 3*mm),
    P('Additional tenant signature (if applicable): [Add a signature line for each additional tenant.]', 'SmallCustom'),
    Spacer(1, 5*mm),
    Table([[P('<b>Before sending:</b> Replace all bracketed fields, complete the schedules, verify the agreement for the specific property and tenant, and attach any required statutory information. This demo template does not determine whether a tenancy term, rent or clause is lawful.', 'Notice')]], colWidths=[170*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#FFF8E9')),('BOX',(0,0),(-1,-1),.6,colors.HexColor('#EAD7B0')),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
    ])),
]

AgreementDoc(str(OUTPUT)).build(story)
print(OUTPUT)
