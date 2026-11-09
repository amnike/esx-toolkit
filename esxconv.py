#!/usr/bin/env python
# esxconv — PDF estimate -> Xactimate ESX (standalone)
# usage:
#   esxconv <input.pdf> <output.ESX>          single convert
#   esxconv <input.pdf>                       -> same name .ESX beside input
#   esxconv --dir <folder>                    convert all PDFs in folder
#   esxconv --watch <folder>                  keep running, convert new PDFs
#   esxconv --verify <file.ESX>               decrypt+validate an ESX we made
# container: ZIP[ XACTDOC.ZIPXML = 04 04 0A pad + AES-256-ECB(zip[XACTDOC.XML]+pad) + FF FE FF FE ]
import re, sys, io, os, time, zipfile, binascii, uuid
from Crypto.Cipher import AES

K2 = binascii.unhexlify('5870596D26246928242363647323342C3E2E3F7A2D2B3A3B7D5B7D7D5B373121')
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template.xml')

# ---------------- container ----------------
def pack_zixxml(xml_bytes):
    ib = io.BytesIO()
    z = zipfile.ZipFile(ib, 'w', zipfile.ZIP_DEFLATED)
    z.writestr('XACTDOC.XML', xml_bytes)
    z.close()
    raw = ib.getvalue()
    pad = (-len(raw)) % 16
    enc = AES.new(K2, AES.MODE_ECB).encrypt(raw + b'\x00' * pad)
    return bytes([4, 4, 10, pad]) + enc + b'\xff\xfe\xff\xfe'

def pack_esx(zixxml_bytes, out_path):
    ob = io.BytesIO()
    z = zipfile.ZipFile(ob, 'w', zipfile.ZIP_DEFLATED)
    z.writestr('XACTDOC.ZIPXML', zixxml_bytes)
    z.close()
    with open(out_path, 'wb') as f:
        f.write(ob.getvalue())

def verify_esx(path):
    """roundtrip check: decrypt our own container and re-parse the XML"""
    data = open(path, 'rb').read()
    outer = zipfile.ZipFile(io.BytesIO(data))
    assert 'XACTDOC.ZIPXML' in outer.namelist(), 'missing XACTDOC.ZIPXML'
    zix = outer.read('XACTDOC.ZIPXML')
    assert zix[:3] == b'\x04\x04\x0a', 'bad header'
    pad = zix[3]
    body = zix[4:-4]
    assert zix[-4:] == b'\xff\xfe\xff\xfe', 'bad trailer'
    assert len(body) % 16 == 0, 'body not 16-aligned'
    dec = AES.new(K2, AES.MODE_ECB).decrypt(body)
    payload = dec[:len(dec) - pad]
    assert payload[:4] == b'PK\x03\x04', 'inner not zip'
    inner = zipfile.ZipFile(io.BytesIO(payload))
    xml = inner.read('XACTDOC.XML').decode('utf-8')
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    items = root.findall('.//ITEM')
    rooms = [g for g in root.findall('.//GROUP') if g.get('isRoom') == '1']
    total = sum(float(i.get('total') or 0) for i in items)
    return len(rooms), len(items), total

# ---------------- coordinate extraction ----------------
QTY_RE = re.compile(r'^([\d,]+(?:\.\d+)?)\s*([A-Z%/]{1,3})$')
MONEY_RE = re.compile(r'^\(?\$?-?[\d,]+\.\d{2}\)?$')
NUM_ITEM = re.compile(r'^(\d{1,3})\.\s+(.*)$')

COLS = {'qty': 205, 'remove': 280, 'reset': 340, 'replace': 390, 'tax': 448, 'op': 499, 'total': 553}
def col_of(x):
    best, bd = None, 1e9
    for c, cx in COLS.items():
        d = abs(x - cx)
        if d < bd:
            best, bd = c, d
    return best if bd < 45 else None

KNOWN_ROOMS = ['kitchen','bathroom','bedroom','living room','family room','dining room','hallway','hall','closet','garage','basement','attic','foyer','entry','entry/foyer','laundry','den','office','study','game room','wet bar','stairs','staircase','pantry','mudroom','sunroom','porch','deck','patio','carport','utility','sitting room','master bedroom','guest bathroom','guest bedroom','hall bathroom','primary bedroom','primary bathroom','bathroom vanity','water closet','under stairs closet','hall closet','bedroom closet','linen closet','exterior','interior']

def norm_room(s):
    return re.sub(r'[^a-z /]', '', (s or '').lower()).strip()

def room_from_line(txt):
    l = norm_room(txt)
    if not l or len(l) < 3 or len(l) > 30:
        return None
    for r in KNOWN_ROOMS:
        if l == r:
            return r
    return None

CITY_RE = re.compile(r'^\s*(.+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$')

def property_block(full):
    """The property's street, city, state and ZIP, and its client, from the cover. pdfminer gives
    the labels first, then the values: the client's name, the street, then "City, ST 12345". The
    address is the first "City, ST 12345" line after the Property label, with the street on the
    line above it and the client on the line above that. {} when the cover has no such lines."""
    lines = full.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r'^\s*Property\s*:', line):
            continue
        for j in range(i + 1, min(i + 12, len(lines))):
            m = CITY_RE.match(lines[j])
            if not m:
                continue
            above = [re.sub(r'^\s*Property\s*:\s*', '', lines[k]).strip() for k in range(j - 1, i - 1, -1)]
            above = [a for a in above if a and not a.endswith(':')]
            street = above[0] if above and re.search(r'\d', above[0]) else ''
            out = {'street': street[:60], 'city': m.group(1).strip()[:30], 'state': m.group(2), 'postal': m.group(3)}
            if street and len(above) > 1:
                out['client'] = above[1][:80]
            return out
        return {}
    return {}

def number_after(label, full, flags=0):
    """A claim, policy or file number after its label. It has a digit, so a label next to it
    ("Policy", "Price", "Type") is never taken for one."""
    m = re.search(label + r'\s*(?:Number|#|No\.?)\s*[:\s]\s*([A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)', full, flags)
    return m.group(1).strip() if m else None

def parse_pdf(path):
    from pdfminer.high_level import extract_pages, extract_text
    from pdfminer.layout import LTTextContainer
    full = extract_text(path)
    header = {}
    for key, label, flags in (('file', 'File', 0), ('claim', 'Claim', re.I), ('policy', 'Policy', re.I)):
        got = number_after(label, full, flags)
        if got:
            header[key] = got
    header.update(property_block(full))
    m = re.search(r'Client\s*:\s*\n\s*(?!Property|Operator|Estimator)(.+)', full)
    if m: header.setdefault('client', m.group(1).strip()[:80])
    if not header.get('client'):
        m = re.search(r'^([A-Z][a-zA-Z]+,?\s+[A-Z][a-zA-Z]+)\s*$', '\n'.join(full.splitlines()[:15]), re.M)
        if m and not re.match(r'Property|Estimate', m.group(1)): header['client'] = m.group(1)
    m = re.search(r'Estimator\s*:\s*\n?\s*([A-Za-z .,-]+)', full)
    if m: header['estimator'] = m.group(1).strip()[:60]
    m = re.search(r'Type of Estimate\s*:\s*(.+)', full)
    if m and not m.group(1).strip().endswith(':'): header['type'] = m.group(1).strip()[:60]
    m = re.search(r'Date Entered\s*:\s*([\d/]+)', full)
    if m: header['date'] = m.group(1)

    rooms = {}
    order = []
    buf = []  # items awaiting their room (room = next 'Totals: X' row)
    hint = None
    def norm_key(nm):
        # canonical key for dedupe: lowercase, collapse spaces, strip stray quotes
        k = re.sub(r'\s+', ' ', (nm or '').strip().lower())
        k = k.replace("'", '').replace('"', '').strip()
        k = re.sub(r'\s*toilet\s*$', 'water closet', k)
        return k
    def flush_to(nm):
        if buf:
            k = norm_key(nm)
            for existing in order:
                if norm_key(existing) == k:
                    rooms[existing].extend(buf)
                    buf.clear()
                    return
            rooms[nm] = list(buf)
            order.append(nm)
            buf.clear()
    cur = room_scratch = None
    layout_hint = []
    hint = None
    pending = None  # AP layout: item awaiting its qty/price row
    has_totals = False

    for pno, page in enumerate(extract_pages(path)):
        rows = {}
        for el in page:
            if not isinstance(el, LTTextContainer):
                continue
            for line in el:
                if not hasattr(line, 'get_text'):
                    continue
                txt = line.get_text().strip()
                if not txt:
                    continue
                y = round(line.y0)
                rows.setdefault(y, []).append((line.x0, txt))
        merged = []
        for y in sorted(rows.keys(), reverse=True):
            if merged and abs(merged[-1][0] - y) <= 3:
                merged[-1] = (merged[-1][0], merged[-1][1] + rows[y])
            else:
                merged.append((y, rows[y]))
        # ---- layout detection per page ----
        page_layout = None
        for y, cells in merged:
            texts = {c[1].strip().upper() for c in cells}
            joined = ' '.join(sorted(texts))
            if 'QUANTITY' in texts and ('AGE/LIFE' in joined or 'COND.' in joined):
                page_layout = 'AP'
                break
            if 'QUANTITY' in texts:
                page_layout = 'SF'
                break
            if 'DESCRIPTION' in texts and 'QTY' in texts and 'TOTAL' in texts:
                page_layout = 'A'
                break
            if 'DESCRIPTION' in texts and 'QTY' in texts:
                page_layout = 'SC'
                break
        if page_layout is None and merged:
            page_layout = layout_hint[0] if layout_hint else 'A'
        if page_layout and (not layout_hint or layout_hint[0] != page_layout):
            layout_hint.clear()
            layout_hint.append(page_layout)
        if pno == 0:
            for y, cells in merged:
                cells = sorted(cells)
                for idx, (cx, ct) in enumerate(cells):
                    base = ct.rstrip(':').strip()
                    if base == 'Client' and idx + 1 < len(cells):
                        v = cells[idx + 1][1]
                        if v not in ('Property', 'Operator', 'Estimator') and not v.endswith(':'):
                            header.setdefault('client', v.strip()[:80])
                    if base == 'File Number' and idx + 1 < len(cells):
                        header.setdefault('file', cells[idx + 1][1].strip()[:30])
        for y, cells in merged:
            cells = sorted(cells)
            joined_txt = ' '.join(c[1] for c in cells)
            # ---- Totals row: terminates a section; buffered items -> that room ----
            tm = None
            for cx, ctxt in cells:
                if 0 <= cx < 220 and ctxt.startswith('Totals'):
                    tm = re.match(r'^Totals:?\s*(.*)$', ctxt)
                    break
            if tm:
                has_totals = True
                if pending is not None:
                    buf.append(pending)
                    pending = None
                nm = re.sub(r'\s+', ' ', tm.group(1)).strip()[:30] or hint or 'UNSPECIFIED'
                flush_to(nm)
                hint = nm
                continue
            # ---- room hint line (section header / CONTINUED) ----
            if len(cells) <= 2:
                txt0 = cells[0][1]
                m2 = re.match(r'^CONTINUED\s*-\s*(.+)$', txt0)
                cand = m2.group(1) if m2 else txt0
                r = room_from_line(cand)
                if r:
                    if has_totals is False and buf and hint and norm_key(hint) != norm_key(r):
                        flush_to(hint)
                    hint = r
                    continue
            if page_layout == 'AP':
                # Appraisal: desc row (NUM. text), then qty/unit-price/tax/op/rcv/dep/acv row
                first_x, first_txt = cells[0]
                m = NUM_ITEM.match(first_txt)
                if m and first_x < 120:
                    if pending:
                        buf.append(pending)
                    pending = {'num': int(m.group(1)), 'desc': m.group(2).strip(), 'qty': None, 'unit': None,
                               'remove': None, 'reset': None, 'replace': None, 'tax': None, 'op': None, 'total': None}
                    continue
                if re.match(r'^(LI|AAG)-', joined_txt):
                    continue
                if pending is None:
                    continue
                # measurement/detail row for pending item
                if cells[0][0] < 120 and not re.match(r'^[\d,]+(?:\.\d+)?\s*[A-Z]{1,3}$', cells[0][1]):
                    if not MONEY_RE.match(cells[0][1]) and not re.search(r'DOI LIC|Yellow Claims|Page:\s*\d|Accurate as|Appraisal Exhibit', joined_txt):
                        pending['desc'] += ' ' + cells[0][1].strip()
                        continue
                # qty + numeric columns: [qty unit] [unit-price] [tax] [op] [rcv] [age] [cond] [dep%] [dep] [acv]
                nums = []
                for cx, ctxt in cells:
                    t = ctxt.strip()
                    qm = re.match(r'^([\d,]+(?:\.\d+)?)\s*([A-Z%/]{1,3})$', t)
                    if qm and cx < 120:
                        pending['qty'] = float(qm.group(1).replace(',', ''))
                        pending['unit'] = qm.group(2)
                    elif MONEY_RE.match(t):
                        nums.append((cx, float(t.replace('$', '').replace(',', '').strip('()'))))
                if nums:
                    # AP col order: unit-price ~142, tax ~196, op ~246, rcv ~286-298, acv ~544-557
                    for cx, v in nums:
                        if cx < 175: pending['replace'] = v
                        elif cx < 225: pending['tax'] = v
                        elif cx < 270: pending['op'] = v
                        elif cx < 500: pending['total'] = v
                        else: pending.setdefault('acv', v)
                continue
            if page_layout == 'SC':
                # Scope: single-row items, qty only, no prices
                first_x, first_txt = cells[0]
                m = NUM_ITEM.match(first_txt)
                if m and first_x < 120:
                    item = {'num': int(m.group(1)), 'desc': m.group(2).strip(), 'qty': None, 'unit': None,
                            'remove': None, 'reset': None, 'replace': None, 'tax': None, 'op': None, 'total': None}
                    for cx, ctxt in cells[1:]:
                        qm = QTY_RE.match(ctxt.strip())
                        if qm:
                            item['qty'] = float(qm.group(1).replace(',', ''))
                            item['unit'] = qm.group(2)
                    buf.append(item)
                    continue
                if buf and first_x < 120 and len(cells) <= 2 and not NUM_ITEM.match(first_txt):
                    if not QTY_RE.match(first_txt) and not re.search(r'DOI LIC|Yellow Claims|Page:\s*\d|ACCURATE AS', joined_txt):
                        buf[-1]['desc'] += ' ' + first_txt.strip()
                continue
            if page_layout == 'SF':
                # State Farm customer copy: single row, QUANTITY|UNIT PRICE|TAX|GCO&P|RCV, numbered items
                first_x, first_txt = cells[0]
                m = re.match(r'^\*?\s*(\d{1,3})\.\s+(.*)$', first_txt)
                if m and first_x < 120:
                    item = {'num': int(m.group(1)), 'desc': m.group(2).strip(), 'qty': None, 'unit': None,
                            'remove': None, 'reset': None, 'replace': None, 'tax': None, 'op': None, 'total': None}
                    for cx, ctxt in cells[1:]:
                        t = ctxt.strip()
                        qm = re.match(r'^([\d,]+(?:\.\d+)?)\s*([A-Z%/]{1,3})$', t)
                        if qm and 150 < cx < 250:
                            item['qty'] = float(qm.group(1).replace(',', ''))
                            item['unit'] = qm.group(2)
                        elif MONEY_RE.match(t):
                            v = float(t.replace('$', '').replace(',', '').strip('()'))
                            if cx < 350: item['replace'] = v
                            elif cx < 440: item['tax'] = v
                            elif cx < 510: item['op'] = v
                            else: item['total'] = v
                    buf.append(item)
                    continue
                if joined_txt.startswith('Summary') or joined_txt.startswith('Amount'):
                    continue
                if buf and first_x < 120 and len(cells) <= 2 and not MONEY_RE.match(first_txt):
                    if NUM_ITEM.match(first_txt) is None and not re.search(r'DOI LIC|Yellow Claims|Page:\s*\d|State Farm', joined_txt):
                        buf[-1]['desc'] += ' ' + first_txt.strip()
                continue
            # ---- Layout A (original single-row) ----
            # find numbered item cell anywhere on the left (dimension labels may sort first)
            item_cell = None
            for cx, ctxt in cells:
                if 0 <= cx < 130 and NUM_ITEM.match(ctxt):
                    item_cell = (cx, ctxt)
                    break
            if item_cell:
                m = NUM_ITEM.match(item_cell[1])
                item = {'num': int(m.group(1)), 'desc': m.group(2).strip(), 'qty': None, 'unit': None,
                        'remove': None, 'reset': None, 'replace': None, 'tax': None, 'op': None, 'total': None}
                for cx, ctxt in cells:
                    if cx == item_cell[0] and ctxt == item_cell[1]:
                        continue
                    c = col_of(cx)
                    if c == 'qty':
                        qm = QTY_RE.match(ctxt.strip())
                        if qm:
                            item['qty'] = float(qm.group(1).replace(',', ''))
                            item['unit'] = qm.group(2)
                    elif c in ('remove', 'reset', 'replace', 'tax', 'op', 'total'):
                        if MONEY_RE.match(ctxt.strip()):
                            v = ctxt.strip().replace('$', '').replace(',', '')
                            neg = v.startswith('(')
                            v = v.strip('()')
                            item[c] = float(v) * (-1 if neg else 1)
                buf.append(item)
                continue
            # wrapped desc / qty line for the previous item
            first_x, first_txt = cells[0]
            if re.match(r'^(LI|AAG)-', joined_txt):
                continue
            if buf and first_x < 120 and len(cells) <= 3:
                placed_q = False
                junk = re.search(r'DOI LIC|Yellow Claims|Page:\s*\d|^\d{1,2}$', joined_txt)
                for cx, ctxt in cells:
                    c = col_of(cx)
                    qm = QTY_RE.match(ctxt.strip()) if c == 'qty' else None
                    if qm and buf[-1]['qty'] is None:
                        buf[-1]['qty'] = float(qm.group(1).replace(',', ''))
                        buf[-1]['unit'] = qm.group(2)
                        placed_q = True
                if not placed_q and not junk and cells[0][0] < 120 and not QTY_RE.match(cells[0][1]) and not MONEY_RE.match(cells[0][1]):
                    if NUM_ITEM.match(cells[0][1]) is None:
                        buf[-1]['desc'] += ' ' + cells[0][1].strip()
            elif buf and first_x >= 120 and buf[-1]['total'] is None:
                # money-only continuation row (y-split): absorb into previous item
                for cx, ctxt in cells:
                    c = col_of(cx)
                    if c == 'qty':
                        qm = QTY_RE.match(ctxt.strip())
                        if qm and buf[-1]['qty'] is None:
                            buf[-1]['qty'] = float(qm.group(1).replace(',', ''))
                            buf[-1]['unit'] = qm.group(2)
                    elif c in ('remove', 'reset', 'replace', 'tax', 'op', 'total') and buf[-1].get(c) is None:
                        if MONEY_RE.match(ctxt.strip()):
                            v = ctxt.strip().replace('$', '').replace(',', '')
                            neg = v.startswith('(')
                            v = v.strip('()')
                            buf[-1][c] = float(v) * (-1 if neg else 1)
    if pending:
        buf.append(pending)
    if buf:
        flush_to(hint or 'UNSPECIFIED')
    client = (header.get('client') or '').split('\n')[0].strip()
    if client.endswith(':') or client.upper() in ('DESCRIPTION', 'QTY', 'PROPERTY', 'OPERATOR', 'ESTIMATOR'):
        client = ''
    header['client'] = client
    return header, [(nm, rooms[nm]) for nm in order if rooms[nm]]

# ---------------- description -> code mapping ----------------
RULES = [
    (r'drywall|sheetrock', 'DRY', 'HNG', ''),
    (r'tape.*joint|joint.*tape', 'DRY', 'TAPE', ''),
    (r'texture', 'DRY', 'TEXT', ''),
    (r'insulation|batt insul', 'INS', 'BAT', ''),
    (r'base shoe|shoe mold', 'MIL', 'SHOE', ''),
    (r'baseboard|base\b', 'MIL', 'BASE', ''),
    (r'crown mold', 'MIL', 'CROWN', ''),
    (r'casing|trim', 'MIL', 'CASE', ''),
    (r'paint.*ceil|ceil.*paint', 'PNT', 'CEIL', ''),
    (r'paint.*(door|window|opening)', 'PNT', 'OPEN', ''),
    (r'paint|prime|seal/prime', 'PNT', 'WALL', ''),
    (r'floor.*(protec|cover|mask)|protection.*floor', 'CLN', 'FPR', ''),
    (r'carpet', 'FLR', 'CARP', ''),
    (r'(wood|hardwood)', 'FLR', 'WOOD', ''),
    (r'leveling cement|self.level', 'CNC', 'LEVEL', ''),
    (r'tile|ceramic', 'FLR', 'TILE', ''),
    (r'vinyl|linoleum', 'FLR', 'VIN', ''),
    (r'chandelier', 'ELC', 'CHAN', ''),
    (r'light fixture|light\b', 'ELC', 'FIX', ''),
    (r'outlet|switch.*cover|receptacle', 'ELC', 'OUT', ''),
    (r'register|heat/ac', 'HVC', 'REG', ''),
    (r'drapery|drapes|curtain|window treatment', 'MIS', 'DRAP', ''),
    (r'window', 'WIN', 'REP', ''),
    (r'door', 'DOR', 'INT', ''),
    (r'toilet|commode', 'PLB', 'WC', ''),
    (r'sink', 'PLB', 'SINK', ''),
    (r'vanity', 'MIL', 'VAN', ''),
    (r'cabinet', 'CAB', 'BAS', ''),
    (r'countertop|counter top', 'CAB', 'TOP', ''),
    (r'water extract|extraction', 'WTR', 'EXT', ''),
    (r'dehumidif', 'WTR', 'DEHUM', ''),
    (r'air mover|blower|fan.*dry', 'WTR', 'AIR', ''),
    (r'antimicrobial|disinfect|sanitiz', 'CLN', 'APPLY', ''),
    (r'toilet.*(temp|month)', 'CLN', 'POT', ''),
    (r'dumpster|debris.*haul|haul.*debris', 'DMO', 'DUMP', ''),
    (r'permit', 'FEES', 'PERMIT', ''),
    (r'door.*knob|knob', 'DOR', 'HW', ''),
    (r'washer|washing machine', 'APP', 'WASH', ''),
    (r'dryer', 'APP', 'DRY', ''),
    (r'p-trap|supply line|plumb', 'PLB', 'GEN', ''),
    (r'sprinkler', 'FIRE', 'SPRK', ''),
    (r'mask.*n.?95|protective mask', 'CLN', 'PPE', ''),
    (r'debris|trash|haul', 'DMO', 'HAUL', ''),
    (r'scaffold|ladder.*jack|plank', 'GC', 'SCAF', ''),
]

def map_item(desc):
    d = (desc or '').lower()
    act = ''
    if re.search(r'\b(remove|removal|demolish|demo|tear.?out|strip)\b', d):
        act = '-'
    if re.search(r'detach.{0,4}reset|d&r\b', d):
        act = 'RR'
    for rx, cat, sel, a in RULES:
        if re.search(rx, d):
            return cat, sel, (a or act)
    return 'MIS', 'GEN', act

# ---------------- XML builder ----------------
def esc(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

GROUP_TYPE = {'KITCHEN': 'Kitchen', 'BATHROOM': 'Bath', 'BEDROOM': 'Bedroom', 'LIVING ROOM': 'Living', 'FAMILY ROOM': 'Living', 'DINING ROOM': 'Living', 'HALLWAY': 'Living', 'HALL': 'Living', 'CLOSET': 'Living', 'GARAGE': 'Garage', 'BASEMENT': 'Living', 'ATTIC': 'Living', 'FOYER': 'Living', 'ENTRY': 'Living', 'ENTRY/FOYER': 'Living', 'LAUNDRY': 'Living', 'DEN': 'Living', 'OFFICE': 'Living', 'GAME ROOM': 'Living', 'WET BAR': 'Living', 'STAIRS': 'Stairs', 'PANTRY': 'Living', 'MUDROOM': 'Living', 'SUNROOM': 'Living', 'UTILITY': 'Living', 'SITTING ROOM': 'Living', 'MASTER BEDROOM': 'Bedroom', 'GUEST BEDROOM': 'Bedroom', 'UNSPECIFIED': 'Non_Room', 'EXTERIOR': 'Exterior', 'INTERIOR': 'Non_Room'}

def build_items_xml(items):
    parts = []
    total = 0.0
    for it in items:
        d = re.sub(r'\s+', ' ', it['desc']).strip()
        if not d or it.get('num') is None:
            continue
        cat, sel, act = map_item(d)
        qty, unit = it.get('qty'), it.get('unit') or ''
        rem, rep = it.get('remove') or 0.0, it.get('replace') or 0.0
        if re.match(r'r\s*&\s*r\b', d, re.I) and rep:
            # an R&R line (its own words say so) is its remove price and its replace price. It has
            # no detach & reset price, so a number read as one is its remove price (a layout with
            # a RESET column puts it there). On any other line the remove column isn't added in.
            rem = rem or (it.get('reset') or 0.0)
            up = rem + rep
        else:
            up = rep
        if not up and it.get('reset'):
            up = it['reset']
        if not up and rem:
            up = rem
        tot = it.get('total')  # the line's own total, 0.00 too; only a line with none is worked out
        if tot is None and qty:
            tot = round(qty * up, 2)
        if tot is not None:
            total += tot
        a = ['type="I"', f'cat="{cat}"', f'sel="{sel}"']
        if act:
            a.append(f'act="{act}"')
        a.append(f'desc="{esc(d[:1000])}"')
        if qty is not None:
            a.append(f'qty="{qty:g}"')
        if unit:
            a.append(f'unit="{unit}"')
        if up:
            a.append(f'replace="{up:.2f}" replaceChanged="1"')
        if tot is not None:
            a.append(f'total="{tot:.2f}"')
        parts.append('<ITEM ' + ' '.join(a) + '/>')
    return parts, total

def set_in(x, tag_re, values):
    """Set attributes on the first tag `tag_re` matches. Returns x unchanged when there's no tag."""
    m = re.search(tag_re, x)
    if m is None:
        return x
    tag = m.group(0)
    for attr, val in values:
        tag = re.sub(r'(\s' + re.escape(attr) + r'=")[^"]*(")', lambda mm, v=val: mm.group(1) + v + mm.group(2), tag, count=1)
    return x[:m.start()] + tag + x[m.end():]

def iso_date(mdy, now):
    """10/1/2026 -> 2026-10-01T00:00:00Z, as Xactimate keeps a date; `now` when it isn't one."""
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', (mdy or '').strip())
    return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}T00:00:00Z' if m else now

def empty_sample_sketch(x):
    """The template's own sketch (Verisk's test kitchen: its room, walls and corners) belongs to no
    estimate made from it: a sketch level that holds a room is left empty."""
    def one(m):
        return m.group(1) + '/>' if '<SKETCHROOM' in m.group(2) else m.group(0)
    return re.sub(r'(<SKETCHLEVEL\b[^>]*?)>(.*?)</SKETCHLEVEL>', one, x, flags=re.S)

def build_xactdoc(header, rooms, template_xml, now=None):
    """The XACTDOC.XML for an estimate. The template is a real Xactimate test project (Verisk's
    "Taylor & Morgan Reyes", claim FR-26-48817, 2217 Birchwood Lane): every one of its own details
    is replaced with the estimate's, or left empty when the PDF doesn't give it."""
    x = template_xml
    now = now or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    def attr_of(tag_re, attr):
        m = re.search(tag_re, x)
        a = re.search(r'\s' + attr + r'="([^"]*)"', m.group(0)) if m else None
        return a.group(1) if a else ''
    old_claim = attr_of(r'<COVERAGE_LOSS\b[^>]*>', 'claimNumber')
    old_policy = attr_of(r'<COVERAGE_LOSS\b[^>]*>', 'policyNumber')
    name = re.sub(r'[^\w\-]+', '_', (header.get('file') or header.get('claim') or 'IMPORT'))[:28]
    # its own project: a new ID, its name and times, set on PROJECT_INFO itself
    x = set_in(x, r'<PROJECT_INFO\b[^>]*>', [('name', esc(name)), ('created', now), ('statusChange', now),
                                            ('modified', now), ('projGUID', str(uuid.uuid4()))])
    claim, policy = esc(header.get('claim') or ''), esc(header.get('policy') or '')
    x = set_in(x, r'<COVERAGE_LOSS\b[^>]*>', [('claimNumber', claim), ('policyNumber', policy)])
    for old, new in ((old_claim, claim), (old_policy, policy)):  # the template's history of them
        if old:
            x = x.replace(f'to="{old}"', f'to="{new}"')
    x = set_in(x, r'<CONTACT type="Client"[^>]*>', [('name', esc(header.get('client') or ''))])
    x = set_in(x, r'<ADDRESS type="Property"[^>]*>', [(k, esc((header.get(k) or '')[:n])) for k, n in
                                                     (('street', 60), ('city', 30), ('state', 2), ('postal', 10))])
    x = re.sub(r'(<PHONE\b[^>]*?\snumber=")[^"]*(")', r'\1\2', x)  # the template's phone numbers
    x = set_in(x, r'<ADM\b[^>]*>', [('dateEntered', iso_date(header.get('date'), now))])
    if 'fire' not in (header.get('type') or '').lower():  # its "Fire" type of loss: a fire's only
        x = x.replace('<TOL code="FIRE" desc="Fire" group="4">', '<TOL>', 1)
    x = empty_sample_sketch(x)
    if header.get('estimator'):
        x = x.replace('userName="Local Admin"', 'userName="' + esc(header['estimator'][:40]) + '"', 1)
    g_start = x.index('<GROUP id="GRP1">')
    params_start = x.index('<PARAMS')
    pre = x[:params_start]
    g_end = pre.rindex('</GROUP>') + len('</GROUP>')
    parts = ['<GROUP id="GRP1"><ITEMS/>']
    gid = 2
    for nm, items in rooms:
        items_xml, it_total = build_items_xml(items)
        if not items_xml:
            continue
        rname = nm.upper()[:23]
        gtype = GROUP_TYPE.get(rname, 'Living')
        code = rname.replace(' ', '_').replace('/', '_')
        parts.append(f'<GROUP id="GRP{gid}" isRoom="1" covID="-1" code="{esc(code)}" type="{gtype}" desc="{esc(rname)}" source="Manual">')
        parts.append(f'<ITEMS total="{it_total:.2f}">')
        parts.extend(items_xml)
        parts.append('</ITEMS></GROUP>')
        gid += 1
    parts.append('</GROUP>')
    x = x[:g_start] + ''.join(parts) + x[g_end:]
    x = re.sub(r'lastGrpID="\d+"', f'lastGrpID="{gid - 1}"', x, count=1)
    return x

def convert(pdf_path, esx_path):
    header, rooms = parse_pdf(pdf_path)
    with open(TEMPLATE_PATH, 'rb') as f:
        tmpl = f.read().decode('utf-8')
    xml = build_xactdoc(header, rooms, tmpl)
    pack_esx(pack_zixxml(xml.encode('utf-8')), esx_path)
    return header, rooms

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    if args[0] == '--verify':
        r, i, t = verify_esx(args[1])
        print(f'VALID  rooms={r} items={i} total={t:.2f}')
        return
    if args[0] == '--dir':
        folder = args[1]
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith('.pdf'):
                continue
            src = os.path.join(folder, f)
            dst = os.path.join(folder, os.path.splitext(f)[0] + '.ESX')
            try:
                header, rooms = convert(src, dst)
                ni = sum(len(i) for _, i in rooms)
                nr, ir, tot = verify_esx(dst)
                print(f'OK   {f} -> {ni} items, {len(rooms)} rooms (verify: {ir} items, ${tot:,.2f})')
            except Exception as e:
                print(f'FAIL {f}: {e}')
        return
    if args[0] == '--watch':
        folder = args[1]
        print(f'watching {folder} (ctrl-c to stop)')
        done = set()
        while True:
            for f in sorted(os.listdir(folder)):
                if not f.lower().endswith('.pdf') or f in done:
                    continue
                src = os.path.join(folder, f)
                dst = os.path.join(folder, os.path.splitext(f)[0] + '.ESX')
                try:
                    header, rooms = convert(src, dst)
                    ni = sum(len(i) for _, i in rooms)
                    print(f'{time.strftime("%H:%M:%S")} OK   {f} -> {ni} items')
                except Exception as e:
                    print(f'{time.strftime("%H:%M:%S")} FAIL {f}: {e}')
                done.add(f)
            time.sleep(2)
    # single-file mode
    src = args[0]
    dst = args[1] if len(args) > 1 else os.path.splitext(src)[0] + '.ESX'
    header, rooms = convert(src, dst)
    ni = sum(len(i) for _, i in rooms)
    nr, ir, tot = verify_esx(dst)
    print(f'header: {header}')
    print(f'rooms: {len(rooms)}, items: {ni} (verified in output: {ir} items, ${tot:,.2f})')
    print(f'wrote {dst}')

if __name__ == '__main__':
    main()
