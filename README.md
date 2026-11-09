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

## Integration notes

Built as a drop-in reader for an import pipeline that routes files by content sniffing: `open_esx()` accepts path or bytes, raises `ValueError` with a reason on anything it can't parse. Already-plain variants (zip with XACTDOC.XML directly inside) pass through untouched.

Export direction: `esxconv.py` builds the full container (ZIP → AES → ZIP) with byte-perfect framing, roundtrip-verified.
