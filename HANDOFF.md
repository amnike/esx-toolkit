# Paperboy ESX Integration — Handoff

## Context

Paperboy spots ESX files in its import pipeline but can't open them — the estimate inside is encrypted. This toolkit solves both directions:

- **Read (ESX → Paperboy):** decrypt any `.ESX`/`.XESX`/`.ESN` into plain XML or structured JSON.
- **Write (Paperboy → ESX):** package a Paperboy estimate into a valid ESX file that Xactimate can import.

Repo: **https://github.com/amnike/esx-toolkit**

Pure Python, single dependency: `pycryptodome`. Runs in our Linux Docker container as-is. No network calls, no external services — everything is offline file format work.

---

## Part 1 — ESX Reader (import pipeline)

**Start from:** `esxread.py` (self-contained). Test fixtures: `samples/real_sample.xml` (decrypted real estimate), `samples/real_sample.json` (parsed output).

### Do this:

1. **Wire the reader into the import pipeline.** The pipeline already routes files by content sniffing. For anything tagged ESX/XESX/ESN, call:

   ```python
   from esxread import open_esx, to_json

   xml_bytes = open_esx(file_bytes_or_path)   # -> raw XACTDOC.XML bytes
   data = to_json(xml_bytes)                  # -> structured dict
   ```

   Corrupt files raise `ValueError` with a reason — wrap in try/except and route to the failure queue. Already-plain variants (zip with XACTDOC.XML directly inside) pass through untouched.

2. **Map the JSON onto our Estimate model.** `to_json()` gives:

   | JSON path | Content |
   |---|---|
   | `project` | name, profile, dates, GUID |
   | `address` | street/city/state/postal |
   | `contacts[]` | client, estimator, claim rep |
   | `rooms[]` | nested groups; `isRoom="1"` marks rooms; each has `code`, `type`, `desc` |
   | `rooms[].items[]` | cat, sel, act, desc, qty, unit, remove/reset/replace prices, tax, total |
   | `rooms[].dims` | C (ceiling SF), F (floor SF), W (wall SF), V (volume), PC/PF (perimeters LF) |
   | `sketchRooms[]` | name, dimCode, ceilingHeight, ceilingType, lastDims |
   | `params` | tax / O&P / depreciation setup flags |

3. **Second pass — parse raw XML for fields not yet structured.** The XML is attribute-driven (`samples/real_sample.xml` is the reference). Pull directly:
   - per-item RCV/ACV/depreciation attributes
   - material/labor/equipment price splits where present
   - `<PARAMS>` for tax/O&P setup
   - `<SKETCHWALL>` elements for wall lengths/openings
   - item↔room links via group nesting

4. **Validate.** Sum each room's `items[].total` — must match the PDF source totals. `itemcheck_all.py` shows the method; run it against any test ESX files in our fixtures.

### Container format (for debugging):

```
file.ESX
└── ZIP
    └── XACTDOC.ZIPXML          header: 04 04 0A <pad>
        │                        body:   AES-256-ECB (fixed key, in esxread.py)
        │                        trailer: FF FE FF FE
        └── ZIP
            └── XACTDOC.XML      the actual estimate (attribute-driven XML)
```

---

## Part 2 — ESX Writer (export to Xactimate)

**Start from:** `esxconv.py`'s builder functions — they take structured data, not PDFs. The PDF parsing is just one input path; we swap in Paperboy's own estimate objects.

### Do this:

1. **Reuse the container builder.** These two functions produce a byte-valid ESX from an XML string:

   ```python
   from esxconv import pack_zixxml, pack_esx

   esx_bytes = pack_esx(pack_zixxml(xactdoc_xml.encode('utf-8')), out_path)
   ```

   Roundtrip-verified: decrypt(parse(encrypt(xml))) == xml.

2. **Reuse the XML builder.** `build_xactdoc(header, rooms, template_xml)` constructs the full XACTDOC from:
   - `header`: dict with client/address/claim/policy numbers
   - `rooms`: list of `(room_name, [items])` where each item has desc, qty, unit, replace price, total
   - `template.xml`: the skeleton (in repo root, must sit beside esxconv.py)

   Feed it Paperboy's estimate data instead of PDF parse output. The builder handles room groups, item attributes (`cat`/`sel` codes via keyword mapping, `act`, `replace` + `replaceChanged="1"` so items carry their own prices), header substitution.

3. **Map Paperboy's model onto the builder's input:**
   - Paperboy room → `(room_name, items)` tuple; room name → `code`/`type`/`desc` attributes
   - Paperboy line item → item dict: `desc`, `qty`, `unit`, `replace` (unit price), `total`
   - Missing cat/sel codes: the keyword rules in `esxconv.py` map descriptions to Xactimate categories (DRY/HNG, PNT/WALL, MIL/BASE, FLR/CARP, ...); unmatched items default to MIS/GEN with their own price — Xactimate accepts them with `replaceChanged="1"`.

4. **Verify each export** with the roundtrip check:

   ```python
   from esxconv import verify_esx
   rooms, items, total = verify_esx(out_path)   # raises on bad container
   ```

5. **Test fixture:** generate an ESX from a Paperboy test estimate, decrypt it back with `esxread.py`, confirm rooms/items/totals match. That's the full loop — write with esxconv, read with esxread, diff the dicts.

---

## Repo layout

```
esxread.py            reader: open_esx() + to_json()
esxconv.py            writer: build_xactdoc() + pack_esx() + PDF input path
template.xml          estimate skeleton used by the writer
samples/
  real_sample.xml     decrypted real estimate — schema reference
  real_sample.json    same estimate, parsed — expected reader output
itemcheck_all.py      total-accuracy validation harness
```

## Out of scope (separate tasks)

- Photos/attachments extraction from ESX
- Roof facets, opening sizes, item↔sketch-room link extraction (raw XML has them; needs mapping)
- OCR for scanned/image-only PDFs
- Xactimate sketch format (.FML / SKX) reading
