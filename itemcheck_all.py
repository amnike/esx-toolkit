from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
from esxconv import parse_pdf
import re, os

def doc_items(path):
    doc = {}
    for pno, page in enumerate(extract_pages(path)):
        rows = {}
        for el in page:
            if not isinstance(el, LTTextContainer): continue
            for line in el:
                if not hasattr(line, 'get_text'): continue
                txt = line.get_text().strip()
                if not txt: continue
                y = round(line.y0)
                rows.setdefault(y, []).append((line.x0, txt))
        merged = []
        for y in sorted(rows.keys(), reverse=True):
            if merged and abs(merged[-1][0] - y) <= 3:
                merged[-1] = (merged[-1][0], merged[-1][1] + rows[y])
            else:
                merged.append((y, rows[y]))
        for y, cells in merged:
            cells = sorted(cells)
            for cx, ctxt in cells:
                m = re.match(r'^\*?\s*(\d{1,3})\.\s+\S', ctxt) if cx < 130 else None
                if m:
                    num = int(m.group(1))
                    moneys = [(x, t) for x, t in cells if x != cx and re.match(r'^\$?[\d,]+\.\d{2}\(?$', t.strip())]
                    if moneys and num not in doc:
                        v = moneys[-1][1].strip().replace('$', '').replace(',', '').strip('()')
                        doc[num] = float(v)
                    break
    return doc

folder = os.environ.get('ESTIMATES_DIR', 'samples')
for name in ['Rossmoyne 12-22-22 estimate.pdf', 'ARA Gutierrez.pdf', 'ARA est 03-30-21.pdf',
             'Tagvoryan Byers.pdf', 'Gutierrez v State Farm 240801_ww (1).pdf',
             'TAGVORYAN_Carrier Appraiser.pdf', 'Customer Copy Final Draft[20508].pdf']:
    path = os.path.join(folder, name)
    doc = doc_items(path)
    h, rooms = parse_pdf(path)
    ours = {}
    for nm, items in rooms:
        for it in items:
            ours.setdefault(it['num'], []).append(it['total'] or 0)
    bad = 0
    for num in sorted(doc):
        cands = ours.get(num, [0])
        if abs(doc[num] - max(cands)) > 0.005:
            bad += 1
    ds, os_ = sum(doc.values()), sum(sum(v) for v in ours.values())
    print(f'{name[:40]:40} items={len(doc):3}/{sum(len(v) for v in ours.values()):3} bad={bad:2} doc={ds:>11,.2f} ours={os_:>11,.2f} diff={os_-ds:+.2f}')
