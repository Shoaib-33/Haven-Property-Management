"""Starter tenancy agreement for Dutch residential lets, in Dutch with an English translation under each clause.
It follows the usual structure of the Dutch model agreements (key facts, then numbered articles) and the main rules of Boek 7 titel 4 BW.
It is a starting point: have it reviewed by a legal professional for your own situation before use.

Layout syntax used by the PDF renderer (app/agreement_pdf.py):
  # Dutch title | English title      the agreement title
  ## Dutch heading | English heading an article heading
  | label | value |                   a key-facts table row
  (EN) text                           the English translation of the paragraph above, set in smaller grey text
"""

NAME = 'Huurovereenkomst woonruimte (NL/EN)'

BODY = """# Huurovereenkomst woonruimte | Residential tenancy agreement

## Kerngegevens | Key facts
| Verhuurder / Landlord | {{company.name}}, {{company.address}} (KvK {{company.kvk}}) |
| Huurder / Tenant | {{tenant.name}}, {{tenant.email}} |
| Woonruimte / Property | {{property.address}}, {{property.postcode}} {{property.city}} |
| Ingangsdatum / Start date | {{contract.start_date}} |
| Duur / Term | {{contract.term}} / {{contract.term_en}} |
| Kale huur / Basic rent | {{rent.basic}} per maand / per month |
| Voorschot servicekosten / Service-charge advance | {{rent.service}} per maand / per month |
| Totaal per maand / Total per month | {{rent.total}} |
| Waarborgsom / Deposit | {{contract.deposit}} |

## Partijen | Parties
De ondertekenden: 1. {{company.name}}, gevestigd te {{company.address}}, KvK-nummer {{company.kvk}}, hierna te noemen "verhuurder"; en 2. {{tenant.name}}, e-mailadres {{tenant.email}}, hierna te noemen "huurder". De persoonsgegevens van de huurder (geboortedatum, identiteitsbewijs en adres) staan in de bijlage "Gegevens huurder" achter deze overeenkomst.
(EN) The undersigned: 1. {{company.name}}, located at {{company.address}}, chamber of commerce (KvK) number {{company.kvk}}, hereinafter "the landlord"; and 2. {{tenant.name}}, email address {{tenant.email}}, hereinafter "the tenant". The tenant's personal details (date of birth, identity document and address) are in the appendix "Tenant details" at the end of this agreement.

## Artikel 1. Het gehuurde | Article 1. The property
De verhuurder verhuurt aan de huurder en de huurder huurt van de verhuurder de woonruimte aan {{property.address}}, {{property.postcode}} {{property.city}} ("het gehuurde"), met een woonoppervlak van circa {{property.area}} m2, {{property.rooms}} kamer(s) en energielabel {{property.energy_label}}. Het gehuurde wordt uitsluitend verhuurd om te worden gebruikt als hoofdverblijf van de huurder.
(EN) The landlord lets to the tenant, and the tenant rents from the landlord, the home at {{property.address}}, {{property.postcode}} {{property.city}} ("the property"), with a floor area of approximately {{property.area}} m2, {{property.rooms}} room(s) and energy label {{property.energy_label}}. The property is let solely as the tenant's main residence.

## Artikel 2. Duur en opzegging | Article 2. Term and termination
De huurovereenkomst gaat in op {{contract.start_date}} en is aangegaan voor {{contract.term}}. De huurder kan de overeenkomst schriftelijk opzeggen met een opzegtermijn van één maand. De verhuurder kan de overeenkomst alleen opzeggen op de wettelijke gronden en met inachtneming van de wettelijke opzegtermijn (artikel 7:272 e.v. BW).
(EN) The tenancy starts on {{contract.start_date}} and has been entered into for {{contract.term_en}}. The tenant may terminate the agreement in writing with one month's notice. The landlord may only terminate on the statutory grounds and with the statutory notice period (article 7:272 et seq. of the Dutch Civil Code).

## Artikel 3. Huurprijs en huurverhoging | Article 3. Rent and rent increases
De kale huurprijs bedraagt {{rent.basic}} per maand. De huurprijs kan jaarlijks per 1 juli worden verhoogd, uitsluitend binnen de wettelijke maximering. Op deze overeenkomst zijn de dwingendrechtelijke bepalingen van Boek 7 titel 4 BW van toepassing, waaronder de regels over de maximale huurprijs en de huurverhoging (Woningwaarderingsstelsel en Wet betaalbare huur).
(EN) The basic rent is {{rent.basic}} per month. The rent may be increased each year on 1 July, only within the statutory limits. The mandatory provisions of Book 7, title 4 of the Dutch Civil Code apply to this agreement, including the rules on the maximum rent and on rent increases (Housing Valuation System and the Affordable Rent Act).

## Artikel 4. Servicekosten | Article 4. Service charges
Naast de huurprijs betaalt de huurder een voorschot voor de kosten van geleverde diensten en zaken van {{rent.service}} per maand. De verhuurder verstrekt de huurder binnen twaalf maanden na afloop van elk kalenderjaar een gespecificeerd overzicht van de werkelijke kosten. Een positief of negatief saldo wordt verrekend. De totale maandelijkse betaling bedraagt {{rent.total}}.
(EN) In addition to the rent, the tenant pays an advance for the cost of services and goods supplied of {{rent.service}} per month. Within twelve months after the end of each calendar year, the landlord provides the tenant with an itemised statement of the actual costs. Any surplus or shortfall is settled. The total monthly payment is {{rent.total}}.

## Artikel 5. Betaling | Article 5. Payment
De huurder betaalt de huur en het voorschot servicekosten per maand vooraf, uiterlijk op de eerste dag van de maand, op de wijze zoals vermeld op de factuur: {{company.payment_instructions}} Bij uitblijven van betaling stuurt de verhuurder eerst een betalingsherinnering en geeft de huurder een termijn van veertien dagen om alsnog te betalen. Pas daarna kunnen de wettelijke rente en redelijke incassokosten in rekening worden gebracht.
(EN) The tenant pays the rent and the service-charge advance monthly in advance, no later than the first day of the month, as stated on the invoice: {{company.payment_instructions}} If payment is missed, the landlord first sends a reminder and gives the tenant fourteen days to pay. Only after that may statutory interest and reasonable collection costs be charged.

## Artikel 6. Waarborgsom | Article 6. Deposit
De huurder betaalt een waarborgsom van {{contract.deposit}}. De waarborgsom bedraagt ten hoogste twee maanden kale huur. De verhuurder betaalt de waarborgsom terug binnen de wettelijke termijn na het einde van de huurovereenkomst en de oplevering van het gehuurde, na aftrek van aantoonbaar verschuldigde bedragen. Eventuele inhoudingen worden schriftelijk en gespecificeerd toegelicht.
(EN) The tenant pays a deposit of {{contract.deposit}}. The deposit is at most two months' basic rent. The landlord returns the deposit within the statutory period after the tenancy has ended and the property has been handed back, minus any amounts demonstrably owed. Any deductions are explained in writing and itemised.

## Artikel 7. Oplevering en staat van het gehuurde | Article 7. Handover and condition
Bij aanvang en bij einde van de huurovereenkomst leggen partijen de staat van het gehuurde en het aantal sleutels vast in een opnamerapport dat door beiden wordt ondertekend. De huurder verklaart dat hij het gehuurde bij aanvang bezichtigd heeft, tenzij in het opnamerapport anders is vermeld.
(EN) At the start and at the end of the tenancy, the parties record the condition of the property and the number of keys in an inspection report signed by both. The tenant confirms that he or she has viewed the property at the start, unless the inspection report states otherwise.

## Artikel 8. Onderhoud en gebreken | Article 8. Maintenance and defects
De verhuurder verricht het onderhoud aan het gehuurde en herstelt gebreken, met uitzondering van de kleine herstellingen die volgens het Besluit kleine herstellingen (artikel 7:217 BW) voor rekening van de huurder komen. De huurder meldt gebreken zo spoedig mogelijk via het huurdersportaal. De verhuurder handelt gemelde gebreken af binnen de reactietermijnen die in het portaal worden vermeld.
(EN) The landlord carries out maintenance and repairs defects, except for the minor repairs that are for the tenant's account under the Minor Repairs Decree (article 7:217 of the Dutch Civil Code). The tenant reports defects as soon as possible through the tenant portal. The landlord deals with reported defects within the response times shown in the portal.

## Artikel 9. Gebruik, onderhuur en wijzigingen | Article 9. Use, subletting and alterations
De huurder gebruikt het gehuurde als een goed huurder, veroorzaakt geen overlast voor omwonenden en houdt zich aan het huishoudelijk reglement indien dat van toepassing is. Het gehuurde mag niet in gebruik worden gegeven aan derden zonder voorafgaande schriftelijke toestemming van de verhuurder (artikel 7:244 BW), behoudens het recht van de huurder om onder de wettelijke voorwaarden een deel van de woonruimte onder te verhuren. Veranderingen of toevoegingen aan het gehuurde zijn alleen toegestaan met voorafgaande schriftelijke toestemming van de verhuurder.
(EN) The tenant uses the property as a good tenant, causes no nuisance to neighbours and complies with the house rules where they apply. The property may not be made available to third parties without the landlord's prior written consent (article 7:244 of the Dutch Civil Code), except for the tenant's right to sublet part of the home under the statutory conditions. Alterations or additions to the property are only allowed with the landlord's prior written consent.

## Artikel 10. Persoonsgegevens | Article 10. Personal data
De verhuurder verwerkt de persoonsgegevens van de huurder uitsluitend voor het sluiten en uitvoeren van deze overeenkomst, in overeenstemming met de Algemene verordening gegevensbescherming (AVG), en bewaart deze niet langer dan nodig.
(EN) The landlord processes the tenant's personal data solely to conclude and perform this agreement, in accordance with the General Data Protection Regulation (GDPR), and keeps it no longer than necessary.

## Artikel 11. Elektronische ondertekening | Article 11. Electronic signature
Partijen stemmen ermee in dat deze overeenkomst elektronisch wordt ondertekend. Een elektronische handtekening heeft dezelfde rechtsgevolgen als een handgeschreven handtekening voor zover de wet dat toelaat (artikel 3:15a BW). Het tijdstip van ondertekening, het IP-adres en een digitale vingerafdruk (SHA-256) van de overeenkomst worden vastgelegd.
(EN) The parties agree that this agreement is signed electronically. An electronic signature has the same legal effect as a handwritten signature to the extent the law allows (article 3:15a of the Dutch Civil Code). The time of signing, the IP address and a digital fingerprint (SHA-256) of the agreement are recorded.

## Artikel 12. Klachten, geschillen en toepasselijk recht | Article 12. Complaints, disputes and governing law
Klachten kan de huurder indienen via het huurdersportaal. Geschillen kunnen worden voorgelegd aan de Huurcommissie of aan de bevoegde rechter. Op deze overeenkomst is Nederlands recht van toepassing. Bij verschillen tussen de Nederlandse en de Engelse tekst is de Nederlandse tekst bepalend.
(EN) The tenant may submit complaints through the tenant portal. Disputes may be submitted to the Rent Tribunal (Huurcommissie) or to the competent court. Dutch law applies to this agreement. If the Dutch and English texts differ, the Dutch text prevails.

Aldus overeengekomen en elektronisch ondertekend.
(EN) Agreed and signed electronically.
"""
