# Keeping program information current

This page is for the analyst who keeps KLEAR's program rules and contacts up to date. Rules live in data/rules (one YAML file per rule, with the income limits and other figures a program uses). Programs live in data/programs (one YAML file per program, with its page, phone number and the rules it uses).

## What runs on its own

Every Monday at 8:00 HST the freshness workflow (.github/workflows/freshness.yml) runs this command. Anyone with write access can also start it from the Actions tab: open "freshness" and press "Run workflow".

    uv run python -m klear.rules check

The command reads every official page live, one request per page, and never a saved copy. It checks that:

- Every quote in a rule file is still on its page, and each value still appears in its quote.
- The poverty guidelines still match the HHS API, and the HUD income limits still match the HUD workbook.
- Every program page still opens, and its phone number still appears on its phone_source page.
- No rule is within 30 days of its review_by date, the day its figures are due to change.
- No program was last checked by a person (its verified date) more than 90 days ago.

A run passes when nothing changed. It fails when a quote, value or phone number changed, when a page is gone, or when a date above is due. A failed run opens one issue titled "Program information needs review", or updates it if it is still open. The issue lists each item, what was expected and the page to look at. The same list is on the run's summary page.

Some sites block scripts. Med-QUEST and the Social Security pages answer HTTP 403, and HUD sends an empty page. The check lists these under "Could not check, needs a person" and does not fail for them. Open each one in a browser and compare it by hand.

## What the website shows

Each program on the site ends with "Checked against official pages on" and its verified date. When that date is more than 90 days old, the line changes to "Check the official page. These details were last checked on" the date, in bold.

## Update a rule

Do this when the issue lists a rule, or when a date in the calendar below comes up.

1. Open the rule's file in data/rules, then open its fetch_url (or its source_url when there is no fetch_url) in a browser.
2. For each entry under values, find the new figure on the page. Set value to the new number and copy the words around it from the page into quote, exactly as printed. The value must appear in the quote the way the page prints it, for example 1,655 for 1655. Spaces and line breaks do not matter.
3. Set effective to the first day the new figures apply. If the rule has an effective_source, update its quote and shown to the page's own words for that date.
4. Set review_by to the next day the figures are due to change (see the calendar), retrieved to today, and verified_by to how you read the page.
5. Check your work. The first command must list nothing under "Changed" for the rule. The build must end with exit code 0, and the tests must pass.

        uv run python -m klear.rules check
        uv run python -m klear.etl build
        uv run pytest -q

6. Open a pull request with the changed file and the rebuilt docs/VALIDATION.md.

Keep the file name and rule_id. Moving to a new year's id (for example snap_fy2027 to snap_fy2028) also needs code changes in klear/rules/registry.py and in the programs that list the rule, so ask a developer. The poverty guidelines (API address with the year in klear/rules/registry.py) and the HUD workbook (file name in klear/validation/rules.py) also need a developer each new year.

## Recheck a program

Do this when the issue lists a program, or when the site says "Check the official page".

1. Open the program's file in data/programs. Open its url and confirm the page still describes the program and how to apply.
2. Open its phone_source and confirm the phone number is still printed there. Change phone and phone_label if they changed.
3. Set verified to today and say in verification how you checked, for example "Fetched the page (HTTP 200). It prints the phone".
4. Run the check and the tests as in step 5 above, then open a pull request.

## Add a program

1. Add data/programs/NAME.yaml, where NAME is the new program_id. Copy an existing file, such as data/programs/snap.yaml, and fill in every field: name, description, serves, agency, url (an https page on a .gov site), phone, phone_label (optional), phone_source (an https page that prints the phone), rules, needs, verified and verification.
2. rules lists the rule files that hold the program's figures. A rule belongs to one program only. If the program has figures, add a rule file in data/rules first, with a review_by date. A developer adds its id to REQUIRED_RULE_IDS in klear/rules/registry.py.
3. needs lists the district figures that signal need for the program. The names allowed are in NEEDS in klear/programs.py.
4. Run the check and the tests, then open a pull request.

## Calendar

When each rule's figures change, in order of its review_by date. The check starts asking 30 days before each date.

| Rule | Program | review_by | When the figures change | Where new figures appear |
| --- | --- | --- | --- | --- |
| aca_ptc_2026 | Marketplace premium tax credits | 2027-01-01 | Each January 1. The IRS sets the next year's percentages in a Revenue Procedure the summer before. | irs.gov Revenue Procedures |
| eitc_federal_ty2025 | Federal EITC | 2027-01-01 | Returns filed from January 2027 use tax year 2026 figures, so this rule is then out of date. | irs.gov EITC tables |
| hi_state_credits | Hawaiʻi tax credits | 2027-01-01 | New Form N-11 instructions each winter for the tax year just ended. | tax.hawaii.gov forms |
| ssi_federal_2026 | SSI | 2027-01-01 | Each January 1, after the cost-of-living raise announced in October. | Federal Register, ssa.gov |
| ssi_hi_supplement | SSI | 2027-01-01 | Each January 1, with the federal amount. | ssa.gov publication 05-11108 |
| fpg_2026_hi | Medicare Savings Programs | 2027-01-15 | HHS posts new poverty guidelines in mid-January. | aspe.hhs.gov poverty guidelines |
| medquest_2026 | Med-QUEST | 2027-01-15 | Med-QUEST posts a new income chart after the poverty guidelines. | medquest.hawaii.gov rules and policy |
| hpha_limits | Public Housing | 2027-04-01 | Follows the HUD income limits. | hpha.hawaii.gov |
| hud_il_fy2026_hi | Public Housing | 2027-04-01 | HUD posts new income limits each spring. | huduser.gov income limits |
| preschool_open_doors | Preschool Open Doors | 2027-05-29 | Each school year, in late May. | humanservices.hawaii.gov |
| h_heap_fy2026 | H-HEAP | 2027-06-01 | A new application packet each June. | humanservices.hawaii.gov LIHEAP |
| kupuna_care | Kūpuna Care | 2027-07-01 | Only when the Legislature changes HRS 349-17. New laws often take effect July 1. | capitol.hawaii.gov |
| wic_2026_27 | WIC | 2027-07-01 | Each July 1. USDA posts the income guidelines in the spring. | fns.usda.gov WIC |
| snap_fy2027 | SNAP | 2027-10-01 | Each October 1. USDA posts the new limits in August. | fns.usda.gov SNAP cost-of-living page |
| eitc_federal_ty2026 | Federal EITC | 2028-01-01 | Returns filed from January 2028 use tax year 2027 figures, which the IRS posts in the fall before. | irs.gov Revenue Procedures |
