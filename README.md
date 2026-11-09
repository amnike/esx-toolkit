# esx-toolkit

Read and write Xactimate ESX estimate files in pure Python.

- **Read**: decrypt any `.ESX` / `.XESX` / `.ESN` file into plain `XACTDOC` XML, or straight to structured JSON (project, contacts, address, rooms, line items, sketch dimensions).
- **Write**: build valid ESX files from data — includes a PDF-estimate → ESX converter with four auto-detected layout parsers.

Works offline, no external services, single dependency: `pycryptodome`.

## Install

```bash
pip install pycryptodome
```

## Reading ESX files (esxread.py)

```python
from esxread import open_esx, to_json

xml_bytes = open_esx("estimate.ESX")     # -> raw XACTDOC.XML bytes
data = to_json(xml_bytes)                # -> dict with rooms/items/dims
```

CLI:

```bash
python esxread.py estimate.ESX           # -> estimate.xml + estimate.json
python esxread.py --dir batch/           # every ESX in folder
```

### Container format

```
file.ESX
└── ZIP
    └── XACTDOC.ZIPXML                   header: 04 04 0A <pad>
        │                                body:   AES-256-ECB (fixed key)
        │                                trailer: FF FE FF FE
        └── ZIP
            └── XACTDOC.XML              the actual estimate
```

The inner XML is attribute-driven:

| Element | Meaning |
|---|---|
| `PROJECT_INFO` | claim header: name, profile, dates, GUID |
| `CONTACT` | client / estimator / claim rep |
| `GROUP` (nested) | room or level; `isRoom="1"` marks rooms |
| `ITEMS/ITEM` | line item: `cat`/`sel` codes, `act`, `desc`, `qty`, `unit`, `remove`/`reset`/`replace` prices, `tax`, `total` |
| `DIM_VARS_SUM` | calculated room quantities: `C` ceiling SF, `F` floor SF, `W` wall SF, `V` volume, `PC`/`PF` perimeters LF |
| `SKETCHROOM` / `SKETCHWALL` | sketch geometry, ceiling height/type |
| `PARAMS` | tax / O&P / depreciation setup |

## Writing ESX files (esxconv.py)

Converts PDF estimates (adjuster/contractor exports) into importable ESX:

```bash
python esxconv.py input.pdf output.ESX
python esxconv.py --dir estimates/       # batch
python esxconv.py --verify output.ESX    # roundtrip check
```

### Supported PDF layouts

| Layout | Columns | Typical source |
|---|---|---|
| A | QTY / REMOVE / RESET / REPLACE / TAX / O&P / TOTAL | Xactimate PDF export |
| AP | QUANTITY / UNIT / TAX / O&P / RCV / DEP / ACV (two-row items) | Appraisal documents |
| SF | QUANTITY / UNIT PRICE / TAX / GCO&P / RCV | State Farm customer copy |
| SC | qty only, no prices | Scope reports |

### Accuracy against the PDFs

Each converted file's line items, added up, against the line items the PDF itself prints (read
and checked against the PDF's own room and grand totals). 11 real estimate PDFs in 4 print
layouts, kept out of this repo:

| | Totals equal to the PDF's, to the cent | The others, off by |
|---|---|---|
| Before the 2026-11-09 fixes | 4 of 11 | 0.05% to 50% (two were missing about half their dollars) |
| After | 5 of 11 | 0.05% to 3.2% (four within 0.4%) |

What's left is a few lines a file: a line read twice, a quantity missed, or a note or page-footer
text added to a line's words. `itemcheck_all.py` checks that each line's own numbers agree with
each other, not that a file adds up to its PDF, so check a converted file's total against the PDF
before relying on it.

## Files

| File | Purpose |
|---|---|
| `esxread.py` | ESX → XML/JSON reader |
| `esxconv.py` | PDF → ESX writer (needs `template.xml` beside it) |
| `template.xml` | decoded estimate skeleton used by the writer |
| `samples/real_sample.xml` | decrypted real estimate — test fixture |
| `samples/real_sample.json` | same estimate, parsed |
| `samples/sample-estimate.ESX` | a fictional estimate (53 lines, $19,732.20) converted by `esxconv.py`: its total equals its PDF's |
| `itemcheck_all.py` | total-accuracy validation harness |

## Converter fixes (2026-11-09)

`template.xml` is a real Xactimate test project (Taylor & Morgan Reyes, claim FR-26-48817, 2217
Birchwood Lane). `esxconv.py` now replaces every one of its details in each file it makes, or
leaves the field empty when the PDF doesn't have it:

- **Project:** each file gets its own project ID, name and times. `set_attr` looked in the XML
  declaration, so every file kept the template's.
- **Address:** read from the cover: the first "City, ST 12345" line after Property, and the street
  above it. Before, a failed match left 2217 Birchwood Lane in every file.
- **Claim and policy numbers:** a number must contain a digit, so a label beside it ("Policy",
  "Price", "Type") is never taken for one. The template's FR-26-48817 and HO3-5521908 are never
  left in, including in its history entries.
- **Client:** from the cover, or empty. The template's phone number is cleared too.
- **Type of loss:** "Fire" only for a fire.
- **Sketch:** the template's own kitchen sketch is left out. It was keyed to group GRP3, which is
  whatever room a file writes third.
- **Line totals:** a 0.00 total stays 0.00. It used to be worked out again from the line's price.
- **R&R lines:** the unit price is the remove price plus the replace price. In print layouts
  with a RESET column, the remove price is read from that column.
- **Date entered:** the PDF's own, when it has one.
- **Columns by their headers:** in the "QUANTITY UNIT PRICE | TAX | O&P | RCV | DEPREC. | ACV"
  layout (not recognised before) and the appraisal layout, each number goes to the header it sits
  flush right under. Before, a fixed position was used, so a wide depreciation number such as
  "(162.98)" replaced the line's RCV total, and prices were read from the wrong columns.

Checked on 11 real estimate PDFs (not in this repo):

- none of the template's details are left in any file;
- every file has its own project ID;
- each file's lines and totals stay the same or come closer to the PDF (see "Accuracy against the PDFs");
- `--verify` passes on every file;
- on R&R lines, the unit prices now match the PDF.

Not fixed: 6 of the 11 converted files still differ from their PDF by a few lines (see "Accuracy
against the PDFs").

## Integration notes

Built as a drop-in reader for an import pipeline that routes files by content sniffing: `open_esx()` accepts path or bytes, raises `ValueError` with a reason on anything it can't parse. Already-plain variants (zip with XACTDOC.XML directly inside) pass through untouched.

Export direction: `esxconv.py` builds the full container (ZIP → AES → ZIP) with byte-perfect framing, roundtrip-verified.
