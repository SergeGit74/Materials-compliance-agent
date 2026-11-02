"""Streamlit app with run controls (README sections 7.2 to 7.6).  Start: streamlit run app.py

Reads and writes the same results/results.csv and results/items/ files as the CLI and the static
dashboard. Mock models by default; the sidebar switch turns on the real clients (src/clients.py), which spend API credits.
"""
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from src import adopt, appcore, clients, config, intake, inventory, pipeline, realruns, reviewview, settings, store
from src.batch import BatchRunner
from src.evaluate import run_costs
from src.clients import make_models
from src.models import MockModels

UPLOAD_DIR = config.RESULTS_DIR / "uploads"
ACTIVE_UPLOAD = UPLOAD_DIR / "inventory_uploaded.csv"
PAGES = ["Run", "Queue", "Review", "Rulebooks", "Charts", "Check a new item", "Upload"]
ACTIVE_STATES = ("running", "paused", "waiting_continue")


def models_factory(real: bool | None = None):
    """Mock by default. Real clients spend API credits and are used only when the sidebar switch is on."""
    if real is None:
        real = bool(st.session_state.get("use_real", False))
    return make_models(real)


# ---------- data helpers ----------

def inventory_path() -> Path:
    return ACTIVE_UPLOAD if ACTIVE_UPLOAD.exists() else config.INVENTORY_CSV


def load_state():
    items = inventory.load_all(inventory_path())
    rows = store.read_rows()
    return items, rows, {r["item_id"]: r for r in rows}


def fmt_s(seconds) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def usd_aed(x: float) -> str:
    return f"${x:,.2f} (AED {x * appcore.AED_PER_USD:,.2f})"


def batch_active() -> bool:
    r = st.session_state.get("runner")
    return bool(r and r.snapshot()["state"] in ACTIVE_STATES)


def result_card(row: dict, report_path: Path | None = None) -> None:
    st.markdown(f"**{row['item_id']}: {row['normalized_name']}**")
    c = st.columns(4)
    c[0].metric("Verdict", row["compliance_verdict"])
    c[1].metric("Fit", row["fit_status"] or "n/a")
    c[2].metric("Confidence", row["confidence"])
    c[3].metric("Human decision", row["human_decision"])
    if row.get("notes"):
        st.caption(row["notes"])
    path = report_path or config.ITEMS_DIR / f"{row['item_id']}.md"
    if Path(path).exists():
        with st.expander("Evidence report"):
            st.markdown(Path(path).read_text(encoding="utf-8"))


def decision_buttons(item_id: str, key: str, extra: dict | None = None) -> str | None:
    """Approve / Reject / Skip (Skip leaves the decision pending). Returns the button clicked."""
    labels = ["Approve", "Reject", "Skip"] + list((extra or {}).keys())
    cols = st.columns(len(labels))
    clicked = None
    for col, label in zip(cols, labels):
        if col.button(label, key=f"{key}_{label}_{item_id}"):
            clicked = label
    if clicked == "Approve":
        store.set_decision(item_id, "approved")
    elif clicked == "Reject":
        store.set_decision(item_id, "rejected")
    return clicked


# ---------- Run: Mode A ----------

def run_single(item: dict, redo: bool) -> None:
    started = time.time()
    holder = st.empty()

    def progress(step, budget):
        used = budget.usage.input_tokens + budget.usage.output_tokens
        holder.info(f"Step: **{step}**  |  elapsed {fmt_s(time.time() - started)}  |  tokens so far {used:,}")

    result = pipeline.run_item(item, models_factory(), pipeline.Budget.from_env(), progress=progress)
    (store.replace_row if redo else store.append_row)(result["row"])
    store.write_report(item["Item_ID"], result["report"])
    holder.empty()
    st.session_state.awaiting = item["Item_ID"]
    st.session_state.last_run = {
        "id": item["Item_ID"], "elapsed": time.time() - started, "usage": result["usage"],
        "safety": result["safety"], "questions": result["open_questions"],
        "tier": inventory.estimate_tier(item["Manufacturer_Listed"], item["Part_Number"], item["Key_Specs"]),
    }


def mode_a(items, rows_by_id) -> None:
    st.subheader("Mode A: run one item")
    st.toggle("Stop after each item", value=True, disabled=True,
              help="Always on in Mode A: nothing runs until you Approve, Reject or Skip the last result.")
    awaiting = st.session_state.get("awaiting")
    blocked = bool(awaiting) or batch_active()
    nxt = inventory.next_item(items, set(rows_by_id))

    if batch_active():
        st.warning("A batch is active. Mode A is disabled until it finishes or is stopped.")
    if nxt:
        est = inventory.estimate_tier(nxt["Manufacturer_Listed"], nxt["Part_Number"], nxt["Key_Specs"])
        st.write(f"Next item: **{nxt['Item_ID']}**, {nxt['Internal_Description']}")
        st.caption(f"Estimate (tier {est['tier']}, planning figures): {est['time']}, {est['cost']}, "
                   f"{est['tokens']} input tokens")
        if appcore.is_safety_item(nxt):
            st.caption("Safety-critical: a person must make the final decision.")
    else:
        st.success("No waiting items.")

    if st.button("Run next item", type="primary", disabled=blocked or nxt is None):
        run_single(nxt, redo=False)
        st.rerun()

    with st.expander("Run this item (reruns and replaces that item's row)"):
        ids = [i["Item_ID"] for i in items]
        pick = st.selectbox("Item", ids, format_func=lambda x: f"{x}  {next(i for i in items if i['Item_ID'] == x)['Internal_Description'][:60]}")
        if st.button("Run this item", disabled=blocked):
            run_single(next(i for i in items if i["Item_ID"] == pick), redo=pick in rows_by_id)
            st.rerun()

    if awaiting and awaiting in rows_by_id:
        st.divider()
        row = rows_by_id[awaiting]
        result_card(row)
        info = st.session_state.get("last_run")
        if info and info["id"] == awaiting:
            u = info["usage"]
            cmp = pd.DataFrame({
                "Estimate (typical)": [info["tier"]["time"], info["tier"]["tokens"], info["tier"]["cost"]],
                "Actual": [fmt_s(info["elapsed"]), f"{u.input_tokens + u.output_tokens:,} tokens", f"${u.cost_usd:.2f}"],
            }, index=["Time", "Tokens", "Cost"])
            st.table(cmp)
            if info["safety"]:
                st.warning("Safety-critical: a person must make the final decision.")
            for q in info["questions"]:
                st.write(f"Open: {q}")
        st.write("Choose one to unlock **Run next item**:")
        if decision_buttons(awaiting, "a"):
            st.session_state.awaiting = None
            st.rerun()


# ---------- Run: Mode B ----------

@st.dialog("Confirm batch run")
def confirm_dialog(ids: list[str], cfg: dict) -> None:
    n = len(ids)
    est = appcore.estimate(n, cfg["concurrency"])
    ms = appcore.models_summary()
    lo, ty, hi = est["cost_usd"]
    st.write(f"**Items:** {n}")
    st.write(f"**Estimated cost (low / typical / high):** {usd_aed(lo)} / {usd_aed(ty)} / {usd_aed(hi)}")
    st.write(f"**Estimated time (low / typical / high):** {est['minutes'][0]:.0f} / {est['minutes'][1]:.0f} / "
             f"{est['minutes'][2]:.0f} min at concurrency {cfg['concurrency']}")
    st.write(f"**Models:** normalize `{ms['normalize']}`, reason `{ms['reason']}`, verify `{ms['verify']}`")
    st.write(f"**Search method:** {ms['search']}")
    st.write(f"**Spend cap:** ${cfg['cap']:.2f}  |  **Stop after each item:** {'on' if cfg['stop_each'] else 'off'}"
             f"  |  **Safety-critical items:** {'excluded' if cfg['exclude_safety'] else 'INCLUDED'}")
    st.caption("Planning estimates from README 7.4, not measurements. MOCK MODE: this run makes no model or web calls.")
    ok = st.checkbox(f"I approve running {n} items with a cap of ${cfg['cap']:.2f}", key="dlg_ok")
    ok2 = True
    if not cfg["exclude_safety"]:
        ok2 = st.checkbox("I confirm that safety-critical items are included in this batch. "
                          "They will still end with human_decision = pending.", key="dlg_ok2")
    if st.button("Start", type="primary", disabled=not (ok and ok2)):
        runner = BatchRunner([i for i in cfg["items"]], (lambda r=bool(st.session_state.get("use_real", False)): models_factory(r)), concurrency=cfg["concurrency"],
                             spend_cap=cfg["cap"], stop_after_each=cfg["stop_each"])
        st.session_state.runner = runner
        runner.start()
        st.rerun()


def batch_settings(items, rows_by_id) -> None:
    st.write("Batch mode runs many items without a click between them. Review every result afterwards.")
    c1, c2 = st.columns(2)
    exclude = c1.checkbox("Exclude safety-critical items", value=True, key="b_excl",
                          help="Safety-critical items are then processed one by one in Mode A.")
    stop_each = c1.checkbox("Stop after each item", value=False, key="b_stop")
    conc = c2.slider("Concurrency", 1, 4, 1, key="b_conc", disabled=stop_each,
                     help="Forced to 1 when Stop after each item is on.")
    max_n = c2.number_input("Maximum items (0 = all eligible)", min_value=0, value=0, step=1, key="b_max")
    conc = 1 if stop_each else conc
    elig = appcore.eligible_for_batch(items, set(rows_by_id), exclude, int(max_n) or None)
    st.write(f"Eligible waiting items: **{len(elig)}**")
    cap = st.number_input("Spend cap (USD)", min_value=0.01, value=max(0.01, appcore.default_cap(len(elig))),
                          step=1.0, key=f"b_cap_{len(elig)}", help="Default is 1.5 times the typical estimate.")
    if st.button("Run batch...", type="primary", disabled=not elig):
        confirm_dialog([i["Item_ID"] for i in elig],
                       {"concurrency": conc, "cap": float(cap), "stop_each": stop_each,
                        "exclude_safety": exclude, "items": elig})


@st.fragment(run_every=1)
def batch_live() -> None:
    runner: BatchRunner = st.session_state.runner
    s = runner.snapshot()
    st.progress(s["done"] / s["total"] if s["total"] else 1.0, text=f"{s['done']} of {s['total']} items  |  state: {s['state']}")
    c = st.columns(4)
    c[0].metric("Elapsed", fmt_s(s["elapsed_s"]))
    c[1].metric("Time left (est.)", fmt_s(s["eta_s"]))
    c[2].metric("Cost / cap", f"${s['cost']:.2f} / ${s['cap']:.2f}")
    c[3].metric("Tokens", f"{s['tokens']:,}")
    if s["in_flight"]:
        st.caption("In progress: " + ", ".join(s["in_flight"]))
    if s["failed"]:
        st.error("Errors (no row written, processed again on resume): " + "; ".join(f"{i}: {m}" for i, m in s["failed"][-3:]))

    live = s["state"] in ACTIVE_STATES
    if live:
        new = st.toggle("Stop after each item", value=s["stop_after_each"], key="live_stop",
                        help="On: finish the items in progress, then wait for Continue after every item.")
        if new != s["stop_after_each"]:
            runner.set_stop_after_each(new)
        b = st.columns(2)
        if b[0].button("Stop after current item (recommended)", disabled=s["stopping"], key="live_stopcur"):
            runner.stop_after_current()
        if b[1].button("Cancel now", key="live_cancel", help="Items in progress are aborted and get no row."):
            runner.cancel_now()
        if s["stopping"]:
            st.info("Stopping: items in progress will finish, no new item starts.")

    if s["state"] == "paused":
        st.warning(f"Paused: {s['pause_reason']}. Decide before continuing.")
        new_cap = None
        if "spend cap" in s["pause_reason"]:
            new_cap = st.number_input("New spend cap (USD)", min_value=0.01, value=round(s["cap"] * 1.5, 2), key="live_newcap")
        if st.button("Resume", key="live_resume"):
            try:
                runner.resume(new_cap)
            except ValueError as e:
                st.error(str(e))

    if s["state"] == "waiting_continue" and s["last_finished"]:
        row = {r["item_id"]: r for r in store.read_rows()}.get(s["last_finished"])
        if row:
            st.divider()
            result_card(row)
            clicked = decision_buttons(s["last_finished"], "b", extra={"Continue": 0, "Stop": 0})
            if clicked == "Continue":
                runner.continue_()
            elif clicked == "Stop":
                runner.stop_after_current()

    if not live:
        st.success(f"Batch {s['state']}: {s['done']} of {s['total']} items written to results.csv.")
        st.caption("Every batch result has human_decision = pending. Approve or reject each one in Review.")
        if st.button("Start a new batch", key="live_new"):
            st.session_state.runner = None
            st.rerun(scope="app")


def mode_b(items, rows_by_id) -> None:
    st.subheader("Mode B: run batch (opt-in)")
    if st.session_state.get("awaiting"):
        st.info("Mode A has a result waiting for Approve, Reject or Skip. Settle it before starting a batch.")
        return
    if st.session_state.get("runner"):
        batch_live()
    else:
        batch_settings(items, rows_by_id)


def page_run(items, rows, rows_by_id) -> None:
    st.header("Run")
    st.warning("MOCK MODE: no model or web call is made. Rows are written in the real format.")
    ta, tb = st.tabs(["Mode A: one item (default)", "Mode B: batch"])
    with ta:
        mode_a(items, rows_by_id)
    with tb:
        mode_b(items, rows_by_id)


# ---------- other pages ----------

def page_queue(items, rows, rows_by_id) -> None:
    st.header("Queue")
    data = [{"Item_ID": i["Item_ID"], "Category": i["Category"], "Description": i["Internal_Description"],
             "Status": appcore.status_of(i["Item_ID"], rows_by_id),
             "Safety-critical": appcore.is_safety_item(i)} for i in items]
    df = pd.DataFrame(data)
    c = st.columns(3)
    for col, s in zip(c, ("processed", "waiting", "skipped")):
        col.metric(s.capitalize(), int((df["Status"] == s).sum()))
    pick = st.multiselect("Show", ["processed", "waiting", "skipped"], default=["processed", "waiting", "skipped"])
    st.dataframe(df[df["Status"].isin(pick)], width="stretch", hide_index=True)


def verdict_banner(row: dict) -> None:
    v = row["compliance_verdict"]
    msg = f"**{v}**, confidence {row['confidence']}, fit {row['fit_status'] or 'n/a'}"
    {"ALLOWED": st.success, "NEEDS_DOCUMENTS": st.warning, "NOT_ALLOWED": st.error}.get(v, st.info)(msg)


def real_trial_tab(row: dict, trial: dict | None) -> None:
    """Second opinion from the real model clients, shown next to the main result. It never changes results.csv or the decision."""
    if not trial:
        st.info("No real-mode trial for this item. Run one with:  python -m src.realtrial " + row["item_id"] + "  (spends API credits).")
        return
    status = realruns.compare(row, trial)
    msg = (f"Real-mode verdict {trial['verdict']} (confidence {trial['confidence']}) against the main result {row['compliance_verdict']} "
           f"(confidence {row['confidence']}).")
    (st.success if status == "same" else st.warning)(("Same verdict. " if status == "same" else "Different verdict. ") + msg)
    c = st.columns(4)
    c[0].metric("Cost", f"${float(trial['cost_usd'] or 0):.3f}")
    c[1].metric("Documents opened", trial["docs_opened"])
    c[2].metric("Tokens", f"{int(trial['tokens'] or 0):,}")
    c[3].metric("Run at", trial["run_at"][:16].replace("T", " "))
    st.caption(f"Models: {trial['models']} | {trial['source']}")
    left, right = st.columns(2)
    left.markdown("**Rules cited, main result**")
    for r in reviewview.rules_cited(row):
        left.markdown(f"- {r['file'][:34]}...#{r['clause']}")
    right.markdown("**Rules cited, real-mode run**")
    for r in reviewview.rules_cited({"dubai_rule_refs": trial["rule_refs"]}):
        right.markdown(f"- {r['file'][:34]}...#{r['clause']}")
    st.markdown("**Why the real-mode run reached this verdict, including how its confidence was set**")
    st.write(trial["notes"] or "n/a")
    rp = realruns.report_path(row["item_id"])
    if rp.exists():
        with st.expander("Real-mode report"):
            st.markdown(rp.read_text(encoding="utf-8"))
    adopt_panel(row, trial)


def adopt_panel(row: dict, trial: dict) -> None:
    """Replace the main result with this trial's, by a named person. Never approves anything and always keeps a backup."""
    iid = row["item_id"]
    st.divider()
    st.subheader("Adopt this real-mode result")
    st.caption("Replaces the verdict, confidence, fit status, rules cited, evidence links and notes in the main results. The name, producers and fit "
               "notes stay. A decision already made on the old result is reset to pending. The previous result and report are backed up and can be restored. "
               "Adopting does not approve or reject anything.")
    who = st.session_state.get("reviewer", "")
    if not who.strip():
        st.warning("Enter your name in the sidebar first. It is recorded with the adoption.")
    comment = st.text_input("Why (optional)", key=f"adopt_why_{iid}")
    probs = adopt.problems(trial, pipeline.rule_files())
    for pr in probs:
        st.error("Cannot adopt: " + pr)
    same = realruns.compare(row, trial) == "same" and trial["confidence"] == row["confidence"]
    if same:
        st.info("The real-mode result has the same verdict and confidence as the main result, so there is nothing to adopt.")
    needs_tick = trial["verdict"] in ("ALLOWED", "NOT_ALLOWED") or reviewview.is_safety(row, {})
    ok = True
    if needs_tick:
        st.warning(f"The real-mode verdict is {trial['verdict']}. Read the real-mode report, the rules cited and the documents it opened before adopting it.")
        ok = st.checkbox("I have read the real-mode report and checked this result", key=f"adopt_tick_{iid}")
    if st.button("Adopt real-mode result", key=f"adopt_btn_{iid}", disabled=bool(probs) or same or not ok):
        try:
            res = adopt.adopt_real_result(iid, who, comment)
            st.session_state["adopt_msg"] = (f"{iid}: main result changed from {res['from'][0]} {res['from'][1]} to {res['to'][0]} {res['to'][1]}."
                                             + (" The earlier decision was reset to pending." if res["decision_reset"] else ""))
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if adopt.latest_backup(iid):
        if st.button("Undo the last adoption (restore the previous result)", key=f"adopt_undo_{iid}"):
            try:
                res = adopt.restore_adoption(iid, who)
                st.session_state["adopt_msg"] = f"{iid}: restored the previous result ({res['restored'][0]} {res['restored'][1]})."
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def review_detail(row: dict, items_by_id: dict, trial: dict | None = None) -> None:
    iid = row["item_id"]
    item = items_by_id.get(iid, {})
    st.subheader(f"{iid}: {row['normalized_name']}")
    verdict_banner(row)
    safety = reviewview.is_safety(row, items_by_id)
    if safety:
        st.warning("Safety-critical item. Only a person can approve or reject it, and the name is recorded.")
    c = st.columns(4)
    c[0].metric("Human decision", row["human_decision"])
    c[1].metric("Category", row["category"])
    c[2].metric("Producer", row["matched_producer"] or "n/a")
    c[3].metric("Qty", f"{item.get('Qty', '')} {item.get('Unit', '')}".strip() or "n/a")
    if item:
        st.caption(f"Part {item.get('Part_Number') or 'n/a'} | listed maker {item.get('Manufacturer_Listed') or 'n/a'} | "
                   f"key specs {item.get('Key_Specs') or 'n/a'} | serves {item.get('Equipment_Served') or 'n/a'} | "
                   f"site {item.get('Site_Location') or 'n/a'} | last bought {item.get('Last_Purchase_Date') or 'n/a'}")
    tabs = st.tabs(["Rules cited", "Evidence", "What is missing", "Notes and fit", "Full report", "Real-mode run", "Decision"])
    with tabs[0]:
        cited = reviewview.rules_cited(row)
        if not cited:
            st.info("No rule from rules/ is cited for this item, so it can never be ALLOWED yet.")
        else:
            st.dataframe(pd.DataFrame(cited).rename(columns={"in_rules_folder": "file in rules/"}), width="stretch", hide_index=True)
            if any(c["scope"].startswith("Federal") for c in cited):
                st.caption("Federal means the UAE Fire and Life Safety Code, cited because the Dubai Building Code (A.4.3) adopts it for Dubai.")
    with tabs[1]:
        files = reviewview.evidence_files(iid)
        if files:
            st.markdown("**Documents opened and saved**")
            for f in files:
                st.download_button(f"Download {f.name}", f.read_bytes(), f.name, key=f"ev_{iid}_{f.name}")
        else:
            st.info("No supplier document has been opened and saved for this item. The links below are search results or pages that were not saved.")
        urls = reviewview.evidence_urls(row)
        if urls:
            st.markdown("**Links recorded**")
            for u in urls:
                st.markdown(f"- {u}")
    with tabs[2]:
        miss = reviewview.missing_documents(iid)
        if miss:
            for m in miss:
                st.markdown(f"- {m}")
        else:
            st.info("The report lists nothing missing.")
    with tabs[3]:
        st.markdown("**Fit notes**")
        st.write(row["fit_notes"] or "n/a")
        st.markdown("**Notes**")
        st.write(row["notes"] or "n/a")
        if row["alias_notes"]:
            st.markdown("**Alias notes**")
            st.write(row["alias_notes"])
    with tabs[4]:
        rp = config.ITEMS_DIR / f"{iid}.md"
        st.markdown(rp.read_text(encoding="utf-8")) if rp.exists() else st.info("No report file.")
    with tabs[5]:
        real_trial_tab(row, trial)
    with tabs[6]:
        hist = reviewview.decision_history(iid)
        if hist:
            st.markdown("**Decision history**")
            st.dataframe(pd.DataFrame(hist), width="stretch", hide_index=True)
        if not st.session_state.get("reviewer", "").strip():
            st.warning("Enter your name in the sidebar before approving or rejecting. It is recorded with the decision.")
        else:
            st.caption(f"Decisions are recorded as: {st.session_state['reviewer']} (set in the sidebar).")
        comment = st.text_area("Comment (optional)", key=f"rv_comment_{iid}")
        ok = True
        warn = reviewview.needs_confirmation(row, "approved")
        if warn:
            st.warning(warn)
            ok = st.checkbox("I understand and still want to approve", key=f"rv_confirm_{iid}")
        b = st.columns(3)
        action = None
        if b[0].button("Approve", key=f"rv_a_{iid}", disabled=not ok):
            action = "approved"
        if b[1].button("Reject", key=f"rv_r_{iid}"):
            action = "rejected"
        if b[2].button("Set back to pending", key=f"rv_p_{iid}"):
            action = "pending"
        if action:
            try:
                reviewview.record_decision(iid, action, st.session_state.get("reviewer", ""), comment)
                st.rerun()
            except ValueError as e:
                st.error(str(e))


def page_review(items, rows, rows_by_id) -> None:
    st.header("Review")
    items_by_id = {i["Item_ID"]: i for i in items}
    real = realruns.load()
    if st.session_state.get("adopt_msg"):
        st.success(st.session_state.pop("adopt_msg"))
    s = reviewview.summary(rows, items_by_id)
    if not s["total"]:
        st.info("No results yet. Process items first.")
        return
    c = st.columns(6)
    c[0].metric("Items", s["total"])
    c[1].metric("ALLOWED", s["verdicts"].get("ALLOWED", 0))
    c[2].metric("NEEDS_DOCUMENTS", s["verdicts"].get("NEEDS_DOCUMENTS", 0))
    c[3].metric("UNKNOWN", s["verdicts"].get("UNKNOWN", 0))
    c[4].metric("Pending", s["pending"])
    c[5].metric("Safety-critical pending", len(s["safety_pending"]))
    rs = reviewview.trial_cost_summary(real)
    if rs["trials"]:
        st.caption(f"Real-mode trials: {rs['trials']} items, ${rs['cost_usd']} in total. They sit beside the main result and never change it or the decision.")
    st.caption("A verdict is the system's evidence check against the Dubai rulebooks. Approve or Reject is a person's decision and is logged with a name. "
               "A verdict is never a complete compliance answer: federal, other-emirate and project-specific rules are out of scope unless adopted by a Dubai rulebook.")
    with st.expander("Filters", expanded=True):
        f1, f2, f3 = st.columns(3)
        verdicts = f1.multiselect("Verdict", ["ALLOWED", "NEEDS_DOCUMENTS", "UNKNOWN", "NOT_ALLOWED"], default=[])
        decisions = f2.multiselect("Human decision", ["pending", "approved", "rejected"], default=["pending"])
        cats = f3.multiselect("Category", sorted({r["category"] for r in rows}), default=[])
        g1, g2, g3 = st.columns([1, 2, 2])
        safety_only = g1.checkbox("Safety-critical only")
        query = g2.text_input("Search (ID, name, maker, notes)")
        order = g3.selectbox("Sort", ["Verdict (best first)", "Confidence (low first)", "Confidence (high first)", "Item ID"])
        real_filter = st.selectbox("Real-mode trial", list(reviewview.REAL_FILTERS), format_func=lambda k: reviewview.REAL_FILTERS[k])
    shown = reviewview.sort_rows(reviewview.filter_rows(rows, items_by_id, verdicts or None, decisions or None, cats or None, safety_only, query,
                                                         real, real_filter), order)
    st.write(f"{len(shown)} items match.")
    if not shown:
        st.info("Nothing matches these filters.")
        return
    table = pd.DataFrame([{
        "Item": r["item_id"], "Name": r["normalized_name"][:70], "Category": r["category"], "Verdict": r["compliance_verdict"],
        "Confidence": r["confidence"], "Safety": "yes" if reviewview.is_safety(r, items_by_id) else "",
        "Rules": len(reviewview.rules_cited(r)), "Docs saved": len(reviewview.evidence_files(r["item_id"])),
        "Real-mode": reviewview.real_cells(r, real.get(r["item_id"]))[0], "Match": reviewview.real_cells(r, real.get(r["item_id"]))[1],
        "Decision": r["human_decision"]} for r in shown])
    st.dataframe(table, width="stretch", hide_index=True)
    labels = {r["item_id"]: f"{r['item_id']} | {r['compliance_verdict']} | {r['normalized_name'][:60]}" for r in shown}
    pick = st.selectbox("Open an item", list(labels), format_func=lambda k: labels[k])
    review_detail(rows_by_id[pick], items_by_id, real.get(pick))


def rulebook_admin() -> None:
    """Index status, re-index, confirmation of rulebooks added automatically, and adding staged downloads. Nothing here costs API credits."""
    who = st.session_state.get("reviewer", "").strip()
    st.subheader("Search index")
    st.caption("Only files with a confirmed row in rules/INDEX.md are searched and can be cited. The index reloads by itself when a file in rules/ or INDEX.md changes; "
               "the button forces it. No API credits are used.")
    if st.button("Re-index rulebooks now", key="reindex"):
        clients.reset_index()
        idx = clients.current_index()
        st.success(f"Indexed {sum(1 for r in idx.report if r['status'] == 'indexed')} file(s), {len(idx.chunks):,} clauses.")
    rep = clients.current_index().report
    st.dataframe(pd.DataFrame([{"File": r["file"], "Status": r["status"], "Clauses": r["clauses"]} for r in rep]), width="stretch", hide_index=True)

    waiting = intake.pending()
    if waiting:
        st.subheader("Waiting for confirmation")
        st.warning("These files were added to rules/ automatically from a search. They are NOT citable until a person checks the language (English), issuer (Dubai), scope and edition, and confirms.")
        if not who:
            st.info("Enter your name in the sidebar to confirm or remove.")
        for r in waiting:
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.markdown(f"**{r['title'][:100]}**  \n`{r['file']}`  \n{r['source_url'][:110]}")
            if c2.button("Confirm", key=f"rb_ok_{r['file']}", disabled=not who):
                try:
                    intake.confirm(r["file"], who)
                    reviewview._log(r["file"], "confirmed-rulebook", who, r["title"][:100], "", "")
                    st.session_state["rb_msg"] = f"Confirmed {r['file']}. It is now indexed and citable."
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            if c3.button("Remove", key=f"rb_rm_{r['file']}", disabled=not who):
                try:
                    intake.remove(r["file"], who)
                    reviewview._log(r["file"], "removed-pending-rulebook", who, r["title"][:100], "", "")
                    st.session_state["rb_msg"] = f"Removed {r['file']} from rules/ (moved to rules_staging/rejected)."
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    plan_path = config.ROOT / "rules_staging" / "needed_rulebooks.json"
    if plan_path.exists():
        import json
        cands = intake.candidates(json.loads(plan_path.read_text(encoding="utf-8")))
        if cands:
            st.subheader("Staged downloads you can add to rules/")
            st.caption("Readable English files from Dubai issuers found by the search. Adding one puts it in rules/ with a PENDING row in INDEX.md; it is not citable until confirmed above. "
                       "Federal files and copies of rulebooks you already have are not offered.")
            labels = {c["path"]: f"{c['rulebook']}: {c['title'][:80]}" for c in cands}
            pick = st.multiselect("Add these", list(labels), format_func=lambda k: labels[k], key="rb_pick")
            if st.button("Add selected to rules/ as pending", key="rb_add", disabled=not pick):
                done = 0
                for c in cands:
                    if c["path"] in pick:
                        try:
                            intake.add_pending(config.ROOT / c["path"], c["url"], c["title"])
                            done += 1
                        except ValueError as e:
                            st.error(f"{c['title'][:60]}: {e}")
                if done:
                    st.session_state["rb_msg"] = f"Added {done} file(s) to rules/ as PENDING. Confirm them in the Waiting for confirmation list."
                    st.rerun()


def page_rulebooks(items, rows, rows_by_id) -> None:
    st.header("Rulebooks")
    if st.session_state.get("rb_msg"):
        st.success(st.session_state.pop("rb_msg"))
    st.caption("Only the files in rules/ can be cited as evidence. Each needs a reviewer's confirmation in rules/INDEX.md.")
    idx = reviewview.read_index()
    counts = reviewview.citations_per_rulebook(rows)
    if idx:
        st.dataframe(pd.DataFrame([{
            "File": e["file"], "Scope": reviewview.rulebook_scope(e), "Title": e["title"][:90], "Version": e["version"][:30],
            "Added": e["date_added"], "Cited by items": counts.get(e["file"], 0),
            "In rules/": (config.RULES_DIR / e["file"]).exists(), "Confirmed by": e["confirmed_by"][:70]} for e in idx]),
            width="stretch", hide_index=True)
    else:
        st.info("rules/INDEX.md has no rows.")
    rulebook_admin()
    plan_path = config.ROOT / "rules_staging" / "needed_rulebooks.json"
    if plan_path.exists():
        import json
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        st.subheader("Rulebooks the items need")
        st.caption("From rules_staging/needed_rulebooks.json. A staged download is not evidence until a person promotes it to rules/.")
        st.dataframe(pd.DataFrame([{
            "ID": r["id"], "Needed rulebook": r["title"][:80], "Items": len(r["items"]), "In rules/": r["status_in_rules"],
            "Promoted as": r.get("promoted_as") or "", "Staged files": sum(m.get("status") == "document downloaded" for m in r.get("more_downloads", []))
            + (r.get("download", {}).get("status") == "document downloaded")} for r in plan["rulebooks"]]),
            width="stretch", hide_index=True)
        unmatched = [r for r in rows if not r["dubai_rule_refs"].strip() and r["human_decision"] != "skipped"]
        st.write(f"{len(unmatched)} items cite no rule yet: " + (", ".join(r["item_id"] for r in unmatched) or "none"))


def page_charts(items, rows, rows_by_id) -> None:
    st.header("Charts")
    done = [r for r in rows if r["human_decision"] != "skipped"]
    if not done:
        st.info("No results yet.")
        return
    df = pd.DataFrame(done)
    st.subheader("Verdict mix")
    st.bar_chart(df["compliance_verdict"].value_counts())
    st.subheader("Category breakdown")
    st.bar_chart(df.groupby(["category", "compliance_verdict"]).size().unstack(fill_value=0))
    st.subheader("Confidence")
    conf = pd.to_numeric(df["confidence"], errors="coerce").dropna()
    st.bar_chart(pd.cut(conf, [0, .2, .4, .6, .8, 1.0], include_lowest=True).value_counts().sort_index().rename(lambda x: str(x)))
    st.subheader("Cost per item")
    costs = run_costs([r["item_id"] for r in done])
    if costs:
        cdf = pd.DataFrame(costs).T
        st.bar_chart(cdf["cost"])
        st.caption(f"Average ${cdf['cost'].mean():.2f} per item, total ${cdf['cost'].sum():.2f}. "
                   "Estimate (README 7.4): about $0.60 per typical item.")
    else:
        st.info("No cost data in item reports yet.")


STAGES = [
    ("Load and validate", "Reading the entry and converting units"),
    ("Normalize", "Identifying the item"),
    ("Discover producers", "Searching for makers and analogues"),
    ("Fit check", "Comparing technical parameters (done in code)"),
    ("Compliance check", "Checking the Dubai rulebooks"),
    ("Verify", "Independent verification of every claim"),
    ("Write results", "Preparing the result"),
]


def text_row(row: dict) -> dict:
    """The pipeline's row with list fields joined, the way results.csv stores them."""
    return {k: (";".join(v) if isinstance(v, (list, tuple)) else v) for k, v in row.items()}


def run_manual_check(item: dict, real: bool, est: dict) -> None:
    """Run one manual entry through the same pipeline as the CSV items and show each stage while it runs."""
    started = time.time()
    typical = max(est["seconds"][1], 1)
    bar = st.progress(0.0, text="Starting")
    try:
        models = make_models(real) if real else MockModels()
    except RuntimeError as e:  # missing .env settings: names only, never values
        bar.empty()
        st.error(str(e))
        return

    with st.status("Working on: " + item["Internal_Description"], expanded=True) as status:
        def say(msg: str) -> None:
            st.write(f"`{time.time() - started:5.0f} s`  {msg}")

        if hasattr(models, "say"):
            models.say = say
        if not real:
            say("Mock mode: no web search and no AI call is made. Turn on the sidebar switch for a real check.")
        names = dict(STAGES)
        order = [s for s, _ in STAGES]

        def progress(step, budget):
            i = order.index(step) if step in order else 0
            elapsed = time.time() - started
            bar.progress(min(0.98, (i + 0.5) / len(order)), text=f"Step {i + 1} of {len(order)}: {names.get(step, step)}. {elapsed:.0f} s elapsed, about {typical} s expected.")
            status.update(label=f"{names.get(step, step)} ({elapsed:.0f} s)")
            say(f"STEP {i + 1}/{len(order)}: {names.get(step, step)}")

        try:
            result = pipeline.run_item(item, models, pipeline.Budget.from_env(), progress=progress)
        except Exception as e:  # shown, not hidden; nothing has been written
            status.update(label="The check failed", state="error")
            st.error(f"The check stopped: {type(e).__name__}. Nothing was saved. Try again; if it repeats, look at the key and network settings.")
            return
        elapsed = time.time() - started
        bar.progress(1.0, text=f"Done in {elapsed:.0f} s")
        status.update(label=f"Done in {elapsed:.0f} s", state="complete", expanded=False)
    st.session_state["manual_run"] = {
        "item": item, "result": result, "real": real, "elapsed": elapsed, "est": est,
        "evidence_files": [str(f) for f in getattr(models, "saved_evidence", [])]}


def manual_result_panel(items, rows, rows_by_id) -> None:
    run = st.session_state.get("manual_run")
    if not run:
        return
    item, res = run["item"], run["result"]
    row = text_row(res["row"])
    st.divider()
    st.subheader(f"Result for: {item['Internal_Description']}")
    verdict_banner(row)
    if res["safety"]:
        st.warning("Safety-critical: only a person can approve or reject this item.")
    c = st.columns(4)
    est = run["est"]
    c[0].metric("Time taken", f"{run['elapsed']:.0f} s", f"estimate {est['seconds'][1]} s", delta_color="off")
    c[1].metric("Cost", f"${res['usage'].cost_usd:.3f}", f"estimate ${est['cost'][1]:.2f}", delta_color="off")
    c[2].metric("Documents opened", len(res["opened_documents"]))
    c[3].metric("Producers / analogues", len(res["candidates"]))

    st.markdown("**Makers and analogues found** (a product from any maker with the same technical parameters counts)")
    if res["candidates"]:
        st.dataframe(pd.DataFrame([{"Maker": x["producer"], "Source type": x.get("source_type") or "", "Document read": "yes" if x.get("document_opened") else "no",
                                    "Specifications stated": x["specs_text"][:140], "Link": x["source_url"]} for x in res["candidates"]]),
                     width="stretch", hide_index=True)
    else:
        st.info("No sourced candidate was found." + (" (Mock mode makes no search.)" if not run["real"] else ""))
    t1, t2, t3, t4 = st.tabs(["Rules cited", "Missing and open points", "Notes", "Full report"])
    with t1:
        cited = reviewview.rules_cited(row)
        st.dataframe(pd.DataFrame(cited).rename(columns={"in_rules_folder": "file in rules/"}), width="stretch", hide_index=True) if cited else st.info("No rule from rules/ is cited.")
    with t2:
        for q in res["open_questions"] or ["Nothing listed."]:
            st.markdown(f"- {q}")
        st.caption("What would settle the verdict is described in the notes.")
    with t3:
        st.write(row["notes"] or "n/a")
        st.caption(f"Fit: {row['fit_status']}  |  {row['fit_notes']}")
    with t4:
        st.markdown(res["report"])

    st.divider()
    st.subheader("Next step: update the databases?")
    new_id = item["Item_ID"]
    st.markdown(
        f"- **Input database:** add this entry as **{new_id}** to `data/manual_items.csv`. This is the one file in `data/` the app may write, and it keeps the entry for later runs.\n"
        f"- **Output database:** add the result to `results/results.csv` and its report to `results/items/{new_id}.md`, with human decision **pending**. "
        "The sidebar name is logged. It then appears in Queue, Review and Charts.")
    if not run["real"]:
        st.warning("This was a mock run, so its result is only a placeholder. You can still save the entry as input and run a real check on it later.")
    who = st.session_state.get("reviewer", "").strip()
    if not who:
        st.warning("Enter your name in the sidebar first. It is recorded with the change.")
    b = st.columns(3)
    save_both = b[0].button("Save entry and result", type="primary", disabled=(not who) or (not run["real"]), key="m_save_both")
    save_in = b[1].button("Save the entry only", disabled=not who, key="m_save_in")
    discard = b[2].button("Discard", key="m_discard")
    done = None
    if save_both or save_in:
        try:
            ids = set(rows_by_id)
            inventory.save_manual_item(item, results_ids=ids)
            if save_both:
                store.append_row(res["row"])
                store.write_report(new_id, res["report"])
            reviewview._log(new_id, "added-manual-item" if save_both else "added-manual-entry-only", who,
                            f"{res['row']['compliance_verdict']} {res['row']['confidence']}" if save_both else "entry saved without a result",
                            res["row"]["compliance_verdict"] if save_both else "", res["row"]["confidence"] if save_both else "")
            done = (f"Saved {new_id} to data/manual_items.csv" + (" and its result to results/results.csv." if save_both else ". Run it later from the Run screen."))
        except (inventory.InventoryError, ValueError) as e:
            st.error(str(e))
    if discard:
        for f in run["evidence_files"]:
            Path(f).unlink(missing_ok=True)
        done = "Discarded. Nothing was written, and the documents saved during the check were removed."
    if done:
        st.session_state.pop("manual_run", None)
        st.session_state["manual_msg"] = done
        st.rerun()


def page_check(items, rows, rows_by_id) -> None:
    st.header("Check a new item")
    if st.session_state.get("manual_msg"):
        st.success(st.session_state.pop("manual_msg"))
    real = bool(st.session_state.get("use_real"))
    st.caption("Type any piece of equipment or material. It goes through the same steps as the items from the CSV file: identify it, search for makers and "
               "analogues from other producers, compare the technical parameters, check the Dubai rulebooks, and verify. Nothing is saved until you say so.")
    st.warning("Type only the product, not prices, stock levels, site names, asset codes or project names. Only the product type, specs and brand are used for web search.")
    name = st.text_input("Item or piece of equipment to check (required, 3 to 160 characters)", max_chars=160, key="n_name",
                         placeholder="for example: circuit breaker, 3 pole, 160 A, 36 kA")
    with st.expander("Add details (optional, they make the search faster and cheaper)"):
        manu = st.text_input("Manufacturer", key="n_manu", max_chars=200)
        part = st.text_input("Part number", key="n_part", max_chars=200)
        specs = st.text_input("Key specs", key="n_specs", max_chars=200)
        equip = st.text_input("Equipment it serves", key="n_equip", max_chars=200)
    try:
        clean = inventory.validate_name(name) if name.strip() else ""
        err = ""
    except inventory.InventoryError as e:
        clean, err = "", str(e)
    if name.strip() and err:
        st.error(err)
    est = appcore.manual_estimate(real, realruns.load())
    if not clean:
        return
    st.markdown("**Estimate before you run**")
    e = st.columns(3)
    e[0].metric("Cost", f"${est['cost'][1]:.2f}", f"range ${est['cost'][0]:.2f} to ${est['cost'][2]:.2f}", delta_color="off")
    e[1].metric("Time", f"about {est['seconds'][1]} s", f"range {est['seconds'][0]} to {est['seconds'][2]} s", delta_color="off")
    e[2].metric("Mode", "Real models" if real else "Mock (no search)")
    st.caption(est["basis"])
    if not real:
        st.info("The sidebar switch **Use real models (spends API credits)** is off, so this would only be a mock run with no search. Turn it on for a real check.")
    if config.is_safety_critical("", clean):
        st.warning("This looks safety-critical, so a person must make the final decision.")
    dupes = inventory.find_duplicates(items, clean)
    allow = True
    if dupes:
        st.warning(f"An item with this name already exists: {', '.join(d['Item_ID'] for d in dupes)}.")
        allow = st.checkbox("Check it anyway", key="n_dup")
    busy = batch_active()
    if busy:
        st.warning("A batch is running. Wait for it to finish or stop it first.")
    label = f"Run check (about ${est['cost'][1]:.2f}, about {est['seconds'][1]} s)" if real else "Run mock check (free)"
    if st.button(label, type="primary", disabled=not allow or busy, key="m_run"):
        item = inventory.make_manual_item(clean, manu, part, specs, equip, results_ids=set(rows_by_id))
        run_manual_check(item, real, est)
    manual_result_panel(items, rows, rows_by_id)
    with st.expander("Or only add it to the queue, without running"):
        if st.button("Add to queue (no cost)", key="m_queue", disabled=not allow):
            row = inventory.add_manual_item(clean, manu, part, specs, equip, results_ids=set(rows_by_id))
            st.success(f"Added {row['Item_ID']} to data/manual_items.csv. Run it from the Run screen. Nothing has run yet.")


def page_upload(items, rows, rows_by_id) -> None:
    st.header("Upload inventory")
    st.write(f"Active inventory: **{inventory_path().name}** ({len(items)} items incl. manual items)")
    up = st.file_uploader("Inventory file (.xlsx or .csv)", type=["xlsx", "csv"])
    if up is not None:
        tmp = UPLOAD_DIR / f"incoming{Path(up.name).suffix.lower()}"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(up.getvalue())
        try:
            new_items = inventory.load_file(tmp)
            ids = [i["Item_ID"] for i in new_items]
            if not new_items or any(not i for i in ids) or len(set(ids)) != len(ids):
                raise inventory.InventoryError("Every row needs a unique Item_ID, and the file must not be empty.")
        except inventory.InventoryError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Could not read this file: {e}. Check that it is a real .xlsx or .csv.")
            return
        finally:
            tmp.unlink(missing_ok=True)
        overlap = set(ids) & set(rows_by_id)
        st.success(f"{len(new_items)} items look valid.")
        if overlap:
            st.warning(f"{len(overlap)} Item_IDs already have a row in results.csv and will count as processed.")
        if st.button("Use this inventory"):
            import csv
            with ACTIVE_UPLOAD.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=config.INVENTORY_COLUMNS)
                w.writeheader()
                w.writerows(new_items)
            st.rerun()
    if ACTIVE_UPLOAD.exists() and st.button("Go back to the sample inventory"):
        ACTIVE_UPLOAD.unlink()
        st.rerun()
    st.caption("Uploads are stored in results/uploads/ (not committed). Files in data/ are never modified.")


# ---------- main ----------

def main() -> None:
    st.set_page_config(page_title="Materials Compliance Agent (Dubai)", layout="wide")
    missing = config.missing_env()
    if missing:
        st.error("Missing or invalid .env settings: " + ", ".join(missing))
        st.stop()
    try:
        items, rows, rows_by_id = load_state()
    except inventory.InventoryError as e:
        st.error(str(e))
        st.stop()

    st.sidebar.title("Materials Compliance (Dubai)")
    if "reviewer" not in st.session_state:
        st.session_state["reviewer"] = settings.get_reviewer() or settings.DEFAULT_REVIEWER
    st.sidebar.text_input("Your name (recorded with decisions)", key="reviewer")
    if st.session_state["reviewer"].strip() not in (settings.get_reviewer(), settings.DEFAULT_REVIEWER):
        if st.sidebar.checkbox("Remember on this computer only (never exported)", key="remember_reviewer"):
            settings.set_reviewer(st.session_state["reviewer"])
            st.sidebar.caption("Saved to reviewer.local.json, which git and the export tool ignore.")
    page = st.sidebar.radio("Screen", PAGES, key="page")
    st.sidebar.caption("Rulebooks: Dubai, English only. A verdict here is never a complete compliance answer.")
    st.sidebar.checkbox("Use real models (spends API credits)", key="use_real",
                        help="Off: mock models, no model or web call. On: Anthropic, Serper and Voyage calls with the models set in .env.")
    if st.session_state.get("use_real"):
        st.sidebar.warning("Real mode: every run spends API credits. Check the estimate and spend cap before you run.")
    if config.RESULTS_CSV.exists():
        st.sidebar.download_button("Download results.csv", config.RESULTS_CSV.read_bytes(), "results.csv", "text/csv")
    {"Run": page_run, "Queue": page_queue, "Review": page_review, "Rulebooks": page_rulebooks, "Charts": page_charts,
     "Check a new item": page_check, "Upload": page_upload}[page](items, rows, rows_by_id)


main()
