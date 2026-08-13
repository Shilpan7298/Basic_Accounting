# Payment terms — the dialect the parser targets

This is the reference for `backend/app/services/payment_terms.py`. It is also
the place to record wording that the parser gets wrong, so the fix lands in the
rule set rather than in a one-off patch.

## The confirmed dialect

Customer POs use **percentage splits tied to named events**:

> 30% advance along with PO, 60% before dispatch, 10% after commissioning

The parser is built for that shape and nothing more ambitious. It splits the
text into clauses, finds a percentage in each, classifies the event, and then
refuses the whole thing unless the percentages sum to **exactly 100**.

## Trigger events

| Trigger | Recognised wording | Due date |
|---|---|---|
| `ON_PO` | advance, along with PO, with order, at the time of order, token advance, booking amount | PO date |
| `BEFORE_DISPATCH` | before/prior to dispatch or despatch, against proforma, against PI, before shipment, ready for dispatch | delivery due date |
| `ON_DELIVERY` | on/against/after delivery, on receipt of material, delivery at site, against LR/GR, on dispatch | delivery due date |
| `AFTER_COMMISSIONING` | after/on/post commissioning, installation, successful testing, site acceptance | **none** — not knowable until it happens |
| `NET_DAYS` | within N days, net N, after N days | base date + N |
| `MANUAL` | anything else | none |

Rules are matched **most specific first**, so "prior to despatch of the
material to the delivery site" is `BEFORE_DISPATCH`, not `ON_DELIVERY`.

An `AFTER_COMMISSIONING` milestone deliberately has no due date. Inventing one
would put a false figure in the receivables ageing report.

## Clause splitting

Clauses split on `;`, newline, `,` (outside brackets), "and then",
"thereafter", and on "and" **only when a new milestone clearly follows** — a
percentage, or the word balance/remaining/rest. That last condition is what
keeps "packing and forwarding" from being torn in half.

## "Balance" wording

> 70% against proforma prior to despatch, balance 30% within 30 days of commissioning

A single `balance` / `remaining` / `rest` / `thereafter` clause resolves to
whatever is left of 100%. **Two** such clauses are ambiguous, so the parser
refuses rather than picking one.

## What it refuses, and why that is the feature

Anything below is handed to the manual schedule builder with nothing saved:

| Input | Outcome |
|---|---|
| `50% advance, 40% before dispatch` | sums to 90% → refused, confidence 0.25 |
| `60% advance, 60% before dispatch` | sums to 120% → refused |
| `Payment by NEFT as mutually agreed` | no percentages → nothing parsed |
| `balance on delivery, balance after commissioning` | two balance clauses → ambiguous |
| `100% as per mutually agreed schedule` | trigger unrecognised → confidence capped at 0.55 |

A wrong milestone split becomes a wrong proforma, which becomes a customer
conversation. Refusing is cheaper. The manual builder is a first-class screen
on the order page, not a hidden fallback.

## Amount allocation

Percentages become amounts with the **last milestone absorbing the rounding
remainder**, so the parts always sum to the order value exactly:

```
₹100 split 33.33% / 33.33% / 33.33%  →  33.33 + 33.33 + 33.34 = 100.00
```

Without this, three-way splits quietly lose a paisa and the final invoice never
closes the order.

## Wording seen in the wild that we do *not* yet parse

Add rows here as real POs arrive; each one should become a test in
`backend/tests/test_payment_terms.py` before the rule is written.

| Wording | Why it fails today |
|---|---|
| `70% against LC at sight, 30% on commissioning` | LC is not a trigger yet; it parses as `MANUAL` and lowers confidence |
| `10% retention released after defect liability period` | retention is not modelled; treated as a plain milestone |
| terms printed in a scanned annexure | no text layer — extraction cannot see it at all |
