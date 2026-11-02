# Rulebooks folder (English, Dubai only)

Put the rulebooks the agent may cite in this folder. Nothing else counts as evidence.

## Admission rules
- Language: English version only.
- Scope: rules issued for the Emirate of Dubai only.
- Format: PDF, Markdown or text. Prefer text or Markdown, since scanned PDFs need OCR and lose section structure.
- Naming: `<issuer>_<short-title>_v<YYYY-MM>.<ext>`, for example `dubai-municipality_<title>_v2024-01.pdf`.
- Keep a row for each file in `INDEX.md` (issuer, title, version date, source URL, date added, who confirmed it).

## What not to put here
- Arabic-only versions, federal UAE rules (except a federal code that a Dubai rulebook in this folder adopts by name, such as the UAE Fire and Life Safety Code adopted by the Dubai Building Code A.4.3; see README 1.2), rules of other emirates, supplier brochures, or summaries written by a model.

## Who decides what belongs here
Your legal or compliance team. Authorities that commonly publish rules relevant to buildings in Dubai include the municipality, the electricity and water utility, and the civil defence authority, but confirm the exact list and the current documents yourselves. This project does not decide it.

## Citation format used by the agent
`<file name>#<section number or heading>`, for example `dubai-municipality_<title>_v2024-01.pdf#3.2`.
