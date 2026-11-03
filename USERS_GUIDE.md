# Users Guide: Materials & Inventory Compliance Check (Dubai)

For reviewers and compliance staff. No programming needed. Technical details are in `README.md`.

## 1. What this tool does, and what it does not

For each inventory item it works out what the item is, looks for makers and datasheets, finds the Dubai rules that apply, and gives a **verdict** with its reasons and the documents behind it. It then waits for **you** to decide.

It does not make the final decision, and a verdict is never a complete compliance answer. It uses only the English Dubai rulebooks stored in the `rules/` folder (plus the UAE Fire and Life Safety Code, which the Dubai Building Code adopts for Dubai). Federal, other-emirate and project-specific rules are outside it. Fire, lift, cladding, work-at-height and gas-detection items always need a person to decide.

## 2. Starting the app

Open a terminal in the project folder and run `streamlit run app.py`. A browser page opens. By default it uses **mock models**: nothing is sent to the internet and no money is spent. The sidebar switch **Use real models (spends API credits)** turns real checks on; leave it off unless you want to run a new check.

In the sidebar, type **Your name**. It is recorded with each decision. It starts as "Project owner". To remember a personal name on this computer only, tick "Remember on this computer only"; it is never exported.

## 3. Reading a verdict

| Verdict | Meaning |
|---|---|
| **ALLOWED** | A cited Dubai rule and an opened maker datasheet (or standards document) both support use, and the item's technical parameters meet the rule. Still a person decides. |
| **NEEDS_DOCUMENTS** | A rule applies, and a named document would settle it (for example a datasheet for the exact model, a Civil Defence listing, a test certificate). The report says which. |
| **NOT_ALLOWED** | A cited rule prohibits the item or it clearly fails a requirement. |
| **UNKNOWN** | No rule in `rules/` applies to this item as a product, or the row is too vague. The report says what kind of source is missing. |

**Confidence** is a number from 0 to 1. It is not a probability. It starts from how clearly the item was identified and is capped by the verdict: UNKNOWN at most 0.40, NEEDS_DOCUMENTS 0.65, ALLOWED and NOT_ALLOWED 0.75. It is lowered if no producer was found, the best candidate does not fit, or a core claim was rejected. Each row's notes explain the exact steps.

**Fit** says whether the found product matches the item's specifications: FIT, PARTIAL, NO_FIT or UNKNOWN.

A product from a different maker with the same technical parameters counts as valid evidence; the maker alone is never a reason for rejection.

## 4. The screens

- **Run**: process one item at a time (Mode A) or a batch (Mode B, with a spend cap). Mode A needs your Approve, Reject or Skip before the next item.
- **Queue**: which items are processed, waiting or skipped.
- **Review**: where you read results and decide (section 5).
- **Rulebooks**: the files in `rules/` with their source, version, who confirmed them, how many items cite each, and the rulebooks still needed.
- **Charts**: verdict mix, categories, confidence, cost.
- **Check a new item**: type any piece of equipment or material and have it checked like the items from the CSV file (section 4a).
- **Upload**: load a different inventory (.xlsx or .csv); errors are explained in plain language.

## 4a. Checking a new item you type in

1. Open **Check a new item** and type the item or piece of equipment, for example `circuit breaker, 3 pole, 160 A, 36 kA`. Maker, part number, key specs, the equipment it serves and a category are optional (under "Add details") and make the search faster and cheaper. The category list holds the categories already in the inventory; leave it on "Unspecified" and the app works it out. Do not type prices, stock levels, site names, asset codes or project names.
2. Turn on **Use real models (spends API credits)** in the sidebar. If it is off, the screen offers only a free mock run that makes no search and gives a placeholder.
3. **Before you run**, the screen shows the estimated cost and time (typically about $0.14 and about a minute, range $0.09 to $0.20 and 50 to 80 seconds; an entry with no maker or part number can cost more, for example $0.26 and almost two minutes). The button shows the same figures. Nothing is spent until you press it.
4. **While it runs**, a progress bar and a log say what is happening and how many seconds have passed: identifying the item, searching the web for makers and analogues, opening each datasheet, extracting analogues and their specifications, choosing the relevant Dubai rules, checking them against the technical parameters, and the independent verification.
5. **The result** shows the verdict with its reasons, the open points (when documents are missing, one line per document to obtain), actual time and cost against the estimate, the makers and analogues found (a product from any maker with the same technical parameters counts), the rules cited, the open points and the full report.
6. **Then the screen suggests updating the databases.** Nothing is written until you choose: **Save entry and result** (adds the entry to `data/manual_items.csv` and the result to the results, with your decision still pending), **Save the entry only** (run it later), or **Discard** (nothing is written and the saved documents are removed). Your sidebar name is logged. Safety-critical items always wait for a person.

## 5. Reviewing an item

1. Open **Review**. The top shows the counts. Use the filters: verdict, decision, category, safety-critical only, a search box, and **Real-mode trial** (see section 6). The table shows the verdict, confidence, rules cited, documents saved and any real-mode result.
2. Choose an item under the table. Its tabs:
   - **Rules cited**: each rule, its clause, which rulebook, and whether the file is in `rules/`. "Federal" means the UAE Fire Code, cited because the Building Code adopts it.
   - **Evidence**: the documents that were opened and saved (download them) and the links recorded.
   - **What is missing**: the documents or confirmations that would settle the item.
   - **Notes and fit**, **Full report**.
   - **Real-mode run**: a second opinion (section 6).
   - **Decision**: **Approve**, **Reject** or **Set back to pending**. Your name is recorded with the time and the verdict at that moment. Approving something that is not ALLOWED needs an extra tick, because you are accepting it without the evidence the system asks for. Safety-critical items show a warning: only a person may decide them.
3. Every decision, adoption and undo is listed in `results/decisions.csv`.

## 6. Real-mode checks and adopting them

The 50 original results were produced in a chat session. A **real-mode trial** runs the live checks again (web search, reading datasheets, the clause index, an independent verifier). Trials are stored separately and never change the main result by themselves.

- In **Review**, the columns "Real-mode" and "Match" compare each trial with the main result. The filter "Real-mode verdict differs" lists the items to look at first.
- The **Real-mode run** tab shows the trial's verdict, the rules each side cited, the documents it opened, its cost, and the reasons, including how its confidence was set.
- To use a trial result as the main result, open that tab and press **Adopt real-mode result**. It needs your name, and for ALLOWED, NOT_ALLOWED and safety-critical items a tick that you read the report. It replaces the verdict, confidence, rules cited, evidence links and notes, keeps the item name and producers, resets any earlier decision on that item to pending, and saves the previous result. **Undo the last adoption** brings it back. Adopting does not approve anything.
- To run new trials: `python -m src.realtrial INV-0001 INV-0034`. It asks you to type `spend` and costs about $0.14 per item.

## 7. Adding or changing rulebooks

Only files in `rules/` can be cited, and each needs a row in `rules/INDEX.md` with the issuer, title, version, source link and **who confirmed it**. Your compliance team decides what belongs there and that it is current.

Finding candidates: `python -m src.docsearch search --yes` (costs a few cents) and `python -m src.docsearch download` put files in `rules_staging/downloads/`. These are **not evidence**.

Bringing a found rulebook in: on the **Rulebooks** screen, "Staged downloads you can add to rules/" lists readable English files from Dubai issuers (federal files and copies of rulebooks you already hold are not offered). Select them and press **Add selected to rules/ as pending**, or run `python -m src.docsearch intake`. Each file is copied into `rules/` and gets a row in `INDEX.md` marked **PENDING**. A pending file is not searched and cannot be cited. Under **Waiting for confirmation**, a named person checks the language, issuer, scope and edition, then presses **Confirm** (the row then shows their name and the date and the file is indexed at once) or **Remove** (the file moves to `rules_staging/rejected/`). A file dropped into `rules/` by hand with no row in `INDEX.md` is ignored. The search index reloads by itself when `rules/` changes, and **Re-index rulebooks now** forces it. Rulebooks that cannot be read (scanned or garbled text) are listed as skipped.

Currently missing or weak: an English edition of the structural codes guideline, a cleaner seismic code PDF, Dubai Municipality technical guidelines no. 1, 5 and 6 (helmets, harnesses, protective clothing), DEWA's water meter specification and approved-product list, and the paid BS EN and IEC standards.

## 8. Typical results you will see

- Many **NEEDS_DOCUMENTS**: the usual missing piece is the datasheet or listing for the exact model bought. The row's part number is often an internal code. Purchase orders or delivery notes help most.
- Fire products need a **Civil Defence Material Approval listing** for the exact model; a UL or EN test report alone is not enough.
- Some makers (Hikvision, HID, Legrand and others) block automated reading of their sites. Open the page yourself and attach the datasheet.
- Items with no applicable rule stay UNKNOWN: compressor oil, V-belts, spare control boards, a mixed spares lot.

## 9. Costs

Mock mode and reading results cost nothing. A real-mode check is about $0.09 to $0.20 per item (average $0.14) and takes about a minute. A rulebook search costs about $0.001 per query. Set a monthly spend limit for the API key in the provider's console before large runs.

## 10. Privacy and publishing

Keys are in `.env` and are never shown or exported. Your personal name stays on your computer. To publish the project, run `python -m src.export_clean C:\path\to\new-folder`. To refresh a folder that is already a git repository (for example the copy on GitHub), add `--update` to the command: it keeps `.git` and needs no new folder. It removes personal reviewer names, leaves out keys, caches, downloads, backups and large files, then checks its own output. The 145 MB Fire Code PDF is too large for GitHub (limit 100 MB).

**Getting the rulebook PDFs after downloading the repository.** The repository does not contain the rulebook PDFs, only the list of them in `rules/INDEX.md` (with the web address of each) and `rules/CHECKSUMS.json`. After you download it, set up the Python environment as in the README, then run:

    python -m src.fetch_rules --dry-run     (shows what will be downloaded; nothing is fetched)
    python -m src.fetch_rules               (downloads the missing PDFs into rules/)

The tool only fetches from Dubai and UAE government sites, never replaces a file you already have, and tells you if a file could not be downloaded (move it into `rules/` by hand, using the file name in INDEX.md). If the publisher has replaced a document with a newer edition, the new file is **not** put into `rules/`; it waits in `rules_staging/downloads/`, because the confirmation in INDEX.md was for the older edition. A person then checks it, and either updates the INDEX row and confirms it again, or runs the command with `--accept-changed`. Without the PDFs, the app starts but cites nothing from the missing rulebooks (the Rulebooks screen shows them as missing).

## 11. If something looks wrong

| Problem | What to do |
|---|---|
| "Save entry and result" is greyed out | It is a mock run, or the sidebar name is empty. Turn on real models and run again, and enter your name. |
| "The check stopped" | Nothing was saved. Try again; if it repeats, check the `.env` settings and the network. |
| The app says `.env` is missing or invalid | The message lists the variable names; fill them in `.env` (never paste keys into chat). |
| A trial seems stuck | Real-mode runs take about a minute per item; they run one at a time. |
| "Cannot adopt" | The message says why (for example ALLOWED without a supplier link). Adopt only when the trial cites rules in `rules/` and a link. |
| A wrong adoption | Open the item's Real-mode run tab and press **Undo the last adoption**. |
| A rulebook file is not found | Check `rules/INDEX.md` and the file name; the Rulebooks screen shows "In rules/" as false. |

## 12. Glossary

**Analogue**: a product from the same or another maker with the same technical parameters, used as evidence. **Core claim**: that a rule applies, that the parameters meet it, or that a document states them. **Staging**: the folder where downloaded candidates wait for human checking. **Trial**: a real-mode run kept apart from the main result. **Adopt**: make a trial result the main result, by a named person.
