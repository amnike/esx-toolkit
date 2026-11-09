#!/usr/bin/env python
"""
esxread — decrypt ESX/XESX/ESN containers into plain XACTDOC XML + JSON.

For Paperboy's import pipeline: identifies ESX by contents, decrypts the
inner XACTDOC, emits:
  <name>.xml  — the exact XACTDOC.XML, unencrypted, UTF-8
  <name>.json — claim/rooms/items/sketch/totals already structured

Run anywhere Python 3.10+ runs (pycryptodome is the only dep).

Usage:
  python esxread.py <file.ESX> [out.xml]      single file
  python esxread.py --dir <folder>            every ESX in folder -> .xml + .json
  python esxread.py --xml-only <file.ESX>     just the XML
"""
import sys, os, io, json, zipfile, binascii

KEY = binascii.unhexlify('5870596D26246928242363647323342C3E2E3F7A2D2B3A3B7D5B7D7D5B373121')

def sniff(data):
    """Content-sniff an ESX-family file. Returns ('esx'|'plain-zip-xml'|'unknown', detail)."""
    if data[:2] == b'PK':
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
            if 'XACTDOC.ZIPXML' in names:
                return 'esx', 'zip>XACTDOC.ZIPXML'
            if 'XACTDOC.XML' in names:
                return 'plain-zip-xml', 'zip>XACTDOC.XML'
            return 'unknown', f'zip:{names[:3]}'
        except Exception as e:
            return 'unknown', str(e)
    if data[:3] == b'\x04\x04\x0a':
        return 'esx', 'bare container'
    return 'unknown', 'no magic'

def open_esx(path_or_bytes):
    """Return inner XACTDOC.XML bytes from any ESX-family input."""
    data = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, 'rb').read()
    kind, _ = sniff(data)
    if kind == 'plain-zip-xml':
        return zipfile.ZipFile(io.BytesIO(data)).read('XACTDOC.XML')
    zix = zipfile.ZipFile(io.BytesIO(data)).read('XACTDOC.ZIPXML') if kind == 'esx' and data[:2] == b'PK' else data
    if zix[:3] != b'\x04\x04\x0a':
        if zix[:2] == b'PK':
            return zipfile.ZipFile(io.BytesIO(zix)).read('XACTDOC.XML')
        raise ValueError('bad container header')
    pad = zix[3]
    body = zix[4:-4]
    assert zix[-4:] == b'\xff\xfe\xff\xfe', 'bad trailer'
    assert len(body) % 16 == 0, 'misaligned'
    from Crypto.Cipher import AES
    dec = AES.new(KEY, AES.MODE_ECB).decrypt(body)
    inner = dec[:len(dec) - pad]
    assert inner[:4] == b'PK\x03\x04', 'inner not zip'
    return zipfile.ZipFile(io.BytesIO(inner)).read('XACTDOC.XML')

# ---------------- structural JSON ----------------
def dims_of(el):
    """DIM_VARS_SUM -> dict (C=ceiling SF, F=floor SF, PC=ceil perimeter, PF=floor perimeter, W=walls SF, V=volume)"""
    d = {}
    for dv in el.findall('./DIM/DIM_VARS_SUM'):
        for k in dv.keys():
            d[k] = dv.get(k)
    return d

def to_json(xml_bytes):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    g = lambda el, k: el.get(k, '') if el is not None else ''
    out = {}

    pi = root.find('.//PROJECT_INFO')
    out['project'] = {k: g(pi, k) for k in ('name','profile','userID','userName','created',
                        'modified','status','projGUID','version','claimNumber','policyNumber')} if pi is not None else {}

    ad = root.find('.//ADDRESS')
    if ad is not None:
        out['address'] = {k: g(ad, k) for k in ('street','street2','city','state','postal','country')}

    contacts = []
    for c in root.findall('.//CONTACT'):
        contacts.append({k: c.get(k) for k in ('type','name','company') if c.get(k)})
    out['contacts'] = contacts

    def walk_groups(parent, depth=0):
        rooms = []
        for grp in parent.findall('./GROUP'):
            r = {
                'id': g(grp, 'id'), 'code': g(grp, 'code'), 'type': g(grp, 'type'),
                'desc': g(grp, 'desc'), 'isRoom': g(grp, 'isRoom'),
                'dims': dims_of(grp),
                'items': [], 'subrooms': walk_groups(grp, depth + 1),
            }
            for it in grp.findall('./ITEMS/ITEM'):
                item = {k: it.get(k) for k in
                        ('cat','sel','act','desc','qty','unit','remove','reset','replace',
                         'tax','total','type','taxable','notes') if it.get(k) is not None}
                r['items'].append(item)
            rooms.append(r)
        return rooms
    # groups nest under the doc root (after PROJECT_INFO); find top GROUP
    top = root.find('.//GROUP')
    out['rooms'] = walk_groups(root)

    # sketch
    sketch = []
    for sr in root.findall('.//SKETCHROOM'):
        sketch.append({
            'id': g(sr, 'id'), 'name': g(sr, 'name'), 'dimCode': g(sr, 'dimCode'),
            'ceilingHeight': g(sr, 'ceilingHeight'), 'ceilingType': g(sr, 'ceilingType'),
            'lastDims': g(sr, 'lastDims'), 'groupKey': g(sr, 'key'),
        })
    out['sketchRooms'] = sketch

    # PARAMS (price-list refs, tax setup, O&P flags)
    pm = root.find('.//PARAMS')
    if pm is not None:
        out['params'] = {k: pm.get(k) for k in pm.keys()}

    return out

def convert(src, xml_dst, json_dst=None):
    xml = open_esx(src)
    with open(xml_dst, 'wb') as f:
        f.write(xml)
    if json_dst:
        with open(json_dst, 'w', encoding='utf-8') as f:
            json.dump(to_json(xml), f, indent=1)
    return len(xml), xml.count(b'<ITEM ')

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)
    xml_only = False
    if args[0] == '--xml-only':
        xml_only = True; args = args[1:]
    if args[0] == '--dir':
        folder = args[1]
        for f in sorted(os.listdir(folder)):
            if not f.upper().endswith(('.ESX', '.XESX', '.ESN')):
                continue
            src = os.path.join(folder, f)
            base = os.path.join(folder, os.path.splitext(f)[0])
            try:
                n, items = convert(src, base + '.xml', None if xml_only else base + '.json')
                print(f'OK   {f} -> {items} items, {n} bytes')
            except Exception as e:
                print(f'FAIL {f}: {e}')
        return
    src = args[0]
    dst = args[1] if len(args) > 1 else os.path.splitext(src)[0] + '.xml'
    jdst = None if xml_only else os.path.splitext(dst)[0] + '.json'
    n, items = convert(src, dst, jdst)
    print(f'wrote {dst} ({n} bytes, {items} items)' + (f' + {jdst}' if jdst else ''))

if __name__ == '__main__':
    main()
