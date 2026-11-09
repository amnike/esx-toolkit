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

### Validated accuracy

Every line item's total checked against its source PDF (penny-exact):

| Source PDF | Items | Total |
|---|---|---|
| Tagvoryan Byers | 560 | $239,963.38 |
| ARA Gutierrez | 470 | $172,875.41 |
| ARA est 03-30-21 | 445 | $164,711.99 |
| TAGVORYAN Carrier Appraiser | 433 | $165,209.42 |
| Gutierrez v State Farm | 406 | $87,876.77 |
| Rossmoyne 12-22-22 | 380 | $91,778.03 |
| Customer Copy Final Draft | 147 | $40,623.77 |

## Files

| File | Purpose |
|---|---|
| `esxread.py` | ESX → XML/JSON reader |
| `esxconv.py` | PDF → ESX writer (needs `template.xml` beside it) |
| `template.xml` | decoded estimate skeleton used by the writer |
| `samples/real_sample.xml` | decrypted real estimate — test fixture |
| `samples/real_sample.json` | same estimate, parsed |
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

Checked on 11 real estimate PDFs (not in this repo):

- none of the template's details are left in any file;
- every file has its own project ID;
- each file keeps the same lines and totals as before;
- `--verify` passes on every file;
- on R&R lines, the unit prices now match the PDF.

Not fixed: in print layouts with unit price, tax, O&P and RCV columns, some lines take the RCV
column as their unit price. Those lines' totals are still right.

## Integration notes

Built as a drop-in reader for an import pipeline that routes files by content sniffing: `open_esx()` accepts path or bytes, raises `ValueError` with a reason on anything it can't parse. Already-plain variants (zip with XACTDOC.XML directly inside) pass through untouched.

Export direction: `esxconv.py` builds the full container (ZIP → AES → ZIP) with byte-perfect framing, roundtrip-verified.
