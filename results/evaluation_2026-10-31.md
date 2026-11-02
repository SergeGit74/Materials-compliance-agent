# Evaluation

Processed items: 50 (skipped, not scored: 0)
Verdict mix: {'ALLOWED': 5, 'NEEDS_DOCUMENTS': 39, 'UNKNOWN': 5, 'NOT_ALLOWED': 1}
UNKNOWN rate: 10%
False ALLOWED (structural, target 0): 0 []
ALLOWED verdicts to read against the evaluator notes: 5
Safety-critical items not left pending (target 0): 0 []
Cost per item: $0.15 over 9 items (total $1.38)

Alias items (tagged in Eval_Notes): 9
Alias coverage (answered, not verified correct): 100%
Alias accuracy needs a person: compare matched_producer and alias_notes with the notes below.

## To review by a person

- INV-0001: verdict ALLOWED, producer 'Schneider Electric', alias notes 'Listed 'Schneider' = Schneider Electric. Internal part NSX250-3P points to the ComPact NSX250 family; legacy ref LV431630 shown as discontinued 2023-06-15 with C25F3TM250 as replacement (se.com IL page); GoPact is a second Schneider range with a 3P 250A 36kA unit; RS PRO is a distributor brand, OEM not stated'
  tags: ALIAS_SHORT_NAME
  good run: Resolves the short brand name to the full producer.
- INV-0003: verdict ALLOWED, producer '', alias notes 'No manufacturer listed, so nothing to match. Olex datasheet is issued with a Nexans customer-service contact, so Olex appears to be a Nexans brand (source: the Olex datasheet contact line). Draka and Prysmian relationship not checked'
  tags: MISSING_MFR, MIXED_UNITS
  good run: Does not guess a brand. Searches by specs and returns candidate producers, or asks a human. Normalises units in code and shows the conversion.
- INV-0005: verdict NEEDS_DOCUMENTS, producer 'Signify (Philips brand)', alias notes 'Listed 'Philips' = Philips-branded lighting made and sold by Signify N.V. (formerly Philips Lighting, renamed 2018; Signify uses the Philips brand under licence from Royal Philips; source: Signify press release). Ledvance and Tracon are different companies, shown as spec-matched alternatives, not aliases'
  tags: ALIAS_RENAMED_COMPANY
  good run: Finds the current company or brand name and records the old-to-new lineage.
- INV-0006: verdict ALLOWED, producer 'Legrand', alias notes 'Listed 'Legrand' matched as is; DX3 range, catalogue no. 410968 per a retailer listing (search text only, not a Legrand page). Schneider, ABB and Hager are other makers of the same spec, not aliases'
  tags: MISSING_PN
  good run: Does not invent a part number. Proposes candidates from specs, or flags for human input.
- INV-0011: verdict UNKNOWN, producer 'Daikin Industries, Ltd. (Daikin)', alias notes 'Listed 'Daikin Industries' = Daikin Industries, Ltd., the Osaka parent company of the Daikin brand. The Daikin Middle East and Africa site (fetched) says Daikin Europe N.V. is a fully owned subsidiary of Daikin Industries Limited, and that Daikin Middle East and Africa, set up in March 2006, is the regional sales office for the UAE, GCC, Middle East and Africa. The page uses 'Daikin' as the brand and mentions no renaming. Part RZQ-PCB-A is not a part number found in any source: RZQ appears in the results as Daikin's Sky Air outdoor series (RZQ71, RZQ200), not VRV, so the part code and the 'VRV' description may not agree'
  tags: ALIAS_FULL_COMPANY_NAME
  good run: Treats the long and short company names as the same producer.
- INV-0012: verdict ALLOWED, producer 'Honeywell (Genetron brand)', alias notes 'Honeywell's refrigerants business is sold under the Genetron and Solstice brands and was planned for spin-off into Solstice Advanced Materials (Honeywell press release; the search result says the spin-off completed on 2025-10-30). The row's last purchase (2025-02-03) predates that. Check which legal entity and brand supplies new stock'
  tags: REGULATED_SUBSTANCE, MISSING_PN
  good run: Checks the rulebook corpus. If not covered, returns UNKNOWN. Does not invent a part number. Proposes candidates from specs, or flags for human input.
- INV-0016: verdict NEEDS_DOCUMENTS, producer 'Tyco (Johnson Controls International plc)', alias notes 'Listed 'Tyco'. In Johnson Controls' filing with the US securities regulator (fetched), Johnson Controls, Inc. and Tyco combined on 2 September 2016, with Tyco surviving as the parent, and Tyco then 'changed its name' to Johnson Controls International plc, moving its domicile to Ireland. So the old Tyco parent is now Johnson Controls International plc, and the 'Tyco' name continues as a brand on sprinkler products (Ferguson listings still sell Tyco-brand heads). Trade-press text (search only) adds that Tyco's fire business grew from acquisitions such as Grinnell and Simplex, and that a UK trade body says Tyco Fire and Integrated Solutions is still the trading name in the UK and Ireland. The legal entity that makes Tyco-brand sprinklers today (for example 'Tyco Fire Products LP') was not confirmed, and a search for a later sale of the business found nothing. Part SP-K56-68 looks like an internal code and was not searched'
  tags: ALIAS_BRAND_OWNER, SAFETY_CRITICAL
  good run: Finds the parent company that owns the brand today. Sets a higher bar. Any gap in evidence goes to human review.
- INV-0017: verdict NEEDS_DOCUMENTS, producer 'Notifier', alias notes 'Notifier is a Honeywell brand per the Honeywell-hosted data sheets in the search results. Part SD-ADR-1 looks like an internal code and was not searched'
  tags: ALIAS_BRAND_OWNER, SAFETY_CRITICAL
  good run: Finds the parent company that owns the brand today. Sets a higher bar. Any gap in evidence goes to human review.
- INV-0029: verdict NEEDS_DOCUMENTS, producer 'Lowara (Xylem Inc.)', alias notes 'Listed 'Lowara'. Lowara is a brand of Xylem Inc.: the current Xylem site has a Lowara brand page (the old lowara.com address redirects to it), and Xylem's 2011 spin-off filing with the US securities regulator lists Lowara among key brands of its Applied Water segment. Xylem was separated from ITT in 2011. A second-hand lineage from Xylem brochures and a trade article (search text only, not opened) says Lowara was founded in 1968 in Italy, bought by Goulds Pumps in 1986, and passed to ITT with its 1997 purchase of Goulds; one distributor says Xylem bought Lowara in 2008, which conflicts with the 2011 spin-off account and is treated as unreliable. Part SP-22 looks like an internal code and was not searched'
  tags: ALIAS_BRAND_OWNER
  good run: Finds the parent company that owns the brand today.
- INV-0036: verdict NEEDS_DOCUMENTS, producer 'Siemens', alias notes 'Listed 'Siemens AG'. Siemens building automation is sold under Siemens Smart Infrastructure and, in the US, Siemens Industry; the legal entity was not verified in this run. The row has no part number, so the model is not established'
  tags: ALIAS_FULL_COMPANY_NAME, MISSING_PN
  good run: Treats the long and short company names as the same producer. Does not invent a part number. Proposes candidates from specs, or flags for human input.
- INV-0043: verdict NEEDS_DOCUMENTS, producer 'Dow Silicones Corporation (formerly Dow Corning; DOWSIL brand)', alias notes 'Listed 'Dow Corning'. Dow's own annual report (fetched) says Dow Silicones Corporation, formerly Dow Corning Corporation and previously a 50:50 venture with Corning Incorporated, became a wholly owned subsidiary of Dow as a result of an ownership restructure (the search text gives the date 1 June 2016). A distributor page (fetched) says that from November 2017 most Dow Corning and Dow Corning Toray branded products moved to the new DOWSIL brand, while MOLYKOTE, XIAMETER and SILASTIC stay; the annual report lists 'DOWSIL' silicone products. So 'Dow Corning' in the row is today Dow Silicones Corporation's DOWSIL range. The rename date rests on a distributor page, not a Dow page. No part number in the row (SS-STR-1 looks internal), so the exact product is not established'
  tags: ALIAS_RENAMED_COMPANY
  good run: Finds the current company or brand name and records the old-to-new lineage.
- INV-0046: verdict NEEDS_DOCUMENTS, producer 'Miller (Honeywell)', alias notes 'Miller is a Honeywell fall protection brand per the Honeywell pages in the search results. The row has no model, and HRN-2PT looks like an internal code, so the Miller model is not established. MSA harnesses appeared in a first search that used the wrong brand and are not used'
  tags: ALIAS_BRAND_OWNER, SAFETY_CRITICAL
  good run: Finds the parent company that owns the brand today. Sets a higher bar. Any gap in evidence goes to human review.
- INV-0048: verdict ALLOWED, producer 'Schneider Electric', alias notes 'Listed 'Schneider Electric', the same part number NSX250-3P as INV-0001 (listed there as 'Schneider'). Treated as the same maker. The part number points to the ComPact NSX250 range, but the exact reference and trip unit are not in the row'
  tags: DUPLICATE_OF_INV-0001
  good run: Detects the duplicate and does not double-process blindly.
