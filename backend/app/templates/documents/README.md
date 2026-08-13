# Editing the document templates

Written for whoever edits these in Word. **You do not need a developer to
change how a document looks**, and you cannot break the numbers by editing a
template — every value is calculated before it reaches you.

## How it works

- Templates live in this folder, one sub-folder per document type:
  `invoice/` and `purchase_order/`.
- A file is named `<name>-v<number>.docx` (Word) or `<name>-v<number>.html` (PDF),
  for example `standard-v1.docx`.
- The app finds templates by **looking in the folder**. Drop a new file in and
  it appears — nobody needs to change any code.
- Every document records which template and version printed it, so reprinting an
  old invoice uses the template it was originally printed with, not the new one.

## Making a change

1. Copy `standard-v1.docx` to `standard-v2.docx`.
2. Open it in Word and change whatever you like — logo, fonts, column widths,
   wording, the order of the blocks.
3. Save it. The app picks the **highest version number** automatically.
4. Press **Validate** on the Templates screen. It renders your template against
   a sample invoice and tells you if you have mistyped a placeholder.

To go back, delete the new file. The old version is still there.

## The placeholders

Anything in `{{ curly braces }}` is filled in when the document prints.
Type them exactly, including the dots.

### The company (you)

| Placeholder | Prints |
|---|---|
| `{{ company.legal_name }}` | Urjapod Energy Private Limited |
| `{{ company.gstin }}` | your GSTIN |
| `{{ company.pan }}` · `{{ company.cin }}` | PAN / CIN |
| `{{ company.bank_name }}` `{{ company.bank_account_no }}` `{{ company.bank_ifsc }}` `{{ company.bank_branch }}` | bank details |

Address lines are a list, so they need a loop:
`{% for l in company.address_lines %}{{ l }}, {% endfor %}`

### The document

| Placeholder | Prints |
|---|---|
| `{{ document.title }}` | TAX INVOICE / PROFORMA INVOICE / PURCHASE ORDER |
| `{{ document.number }}` | URJ/25-26/0042 |
| `{{ document.date }}` | 12-08-2025 |
| `{{ document.due_date }}` | payment due date, if any |
| `{{ document.po_reference }}` · `{{ document.po_date }}` | the customer's PO |
| `{{ document.notes }}` | free-text notes |

### The other party

`{{ party.name }}`, `{{ party.gstin }}`, `{{ party.state_name }}`,
`{{ party.contact_name }}`, and the address lists
`party.billing_address_lines` / `party.shipping_address_lines`.

### Totals

| Placeholder | Prints |
|---|---|
| `{{ totals.taxable_value.fmt }}` | 25,00,000.00 |
| `{{ totals.tax_total.fmt }}` | total GST |
| `{{ totals.round_off.fmt }}` | the round-off line |
| `{{ totals.grand_total.fmt }}` | 29,50,000.00 |
| `{{ totals.amount_in_words }}` | Rupees Twenty Nine Lakh Fifty Thousand Only |
| `{{ tax.cgst_total.fmt }}` `{{ tax.sgst_total.fmt }}` `{{ tax.igst_total.fmt }}` | tax by head |

> **Always add `.fmt`** to a money value. That is the version already formatted
> the Indian way (1,23,456.78). The `.raw` version is for the computer.

### Which taxes to show

Gujarat customers pay CGST + SGST; everyone else pays IGST. Ask the document
which applies rather than guessing:

```
{% if tax.is_intra_state %}CGST {{ tax.cgst_total.fmt }} / SGST {{ tax.sgst_total.fmt }}
{% else %}IGST {{ tax.igst_total.fmt }}{% endif %}
```

### Line items — the one tricky bit

In **Word**, a repeating table needs three rows:

| row | what goes in it |
|---|---|
| a row containing only `{%tr for line in lines %}` | disappears when printed |
| the row with your `{{ line.… }}` placeholders | repeats once per item |
| a row containing only `{%tr endfor %}` | disappears when printed |

Put the `for` and the `endfor` in **their own rows**. If you put both in the
same row as the placeholders, that row is deleted and the document will not
print.

Available on each line: `line.no`, `line.description`, `line.hsn_code`,
`line.qty.fmt`, `line.uom`, `line.unit_price.fmt`, `line.taxable_value.fmt`,
`line.gst_rate_percent.fmt`, `line.cgst.fmt`, `line.sgst.fmt`, `line.igst.fmt`,
`line.total.fmt`.

In **HTML** (the PDF template) it is the ordinary form:
`{% for line in lines %}` … `{% endfor %}`.

### Proformas

A proforma is **not** a tax invoice and must say so. That is handled by:

```
{% if document.is_proforma %}This is not a tax invoice. No GST liability
arises on this document.{% endif %}
```

Keep that block. `{{ payment.milestone_label }}` and
`{{ payment.milestone_percent.fmt }}` print which milestone is being billed.

## Rules

1. **Never do arithmetic in a template.** No `{{ a * b }}`. Every number you
   need is already computed — if one is missing, ask for it to be added.
2. **Do not rename a file that has already printed documents.** Copy it to a
   new version instead.
3. A misspelt placeholder prints as blank rather than crashing — which is why
   you should press **Validate** before using a new template for real.
4. Both the Word and the PDF template for a document type receive exactly the
   same values, so if you change one, change the other to match.
