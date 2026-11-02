"""Extract only observed training MCC titles from a local official Visa manual.

Requires pdfplumber (bundled document runtime is sufficient). No transaction
rows or labels are sent to any external service. Public PDFs stay in ignored runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def extract(pdf_path, feature_path, output):
    import pdfplumber
    with sqlite3.connect(Path(feature_path).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        observed = {str(r[0]) for r in connection.execute("SELECT DISTINCT mcc FROM features WHERE split='train'")}
    entries, conflicts = {}, {}
    with pdfplumber.open(pdf_path) as document:
        for page_no, page in enumerate(document.pages, 1):
            if page_no < 27 or page_no > 107:
                continue
            words = page.extract_words(extra_attrs=['fontname'])
            codes = [w for w in words if w['x0'] < 80 and 'Bold' in w['fontname'] and
                     re.fullmatch('[0-9]{4}', w['text']) and 80 < w['top'] < 755]
            for word in codes:
                code = word['text']
                if code not in observed:
                    continue
                next_top = min([w['top'] for w in codes if w['top'] > word['top'] + 3] + [755])
                travel = 3000 <= int(code) <= 3999
                title_words = [w for w in words if 83 < w['x0'] < 290 and
                               word['top'] - 1 <= w['top'] < min(next_top - 1, word['top'] + (19 if travel else 48)) and
                               (travel or 'Bold' in w['fontname'])]
                title = ' '.join(w['text'] for w in title_words)
                if not title:
                    raise ValueError(f'Empty title for {code}')
                value = {'title': title, 'pdf_page': page_no, 'extraction': 'first-column code + title-column words; bold general titles'}
                if code in conflicts:
                    conflicts[code].append(value)
                elif code in entries and entries[code]['title'] != title:
                    conflicts[code] = [entries.pop(code), value]
                else:
                    entries[code] = value
    manifest = {'source': {'url': 'https://by.visa.com/content/dam/VCOM/download/merchants/visa-merchant-data-standards-manual.pdf',
                           'edition': 'October 2026', 'retrieved_at': '2026-11-01',
                           'sha256': hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest(),
                           'historical_status': 'Modern reference dictionary, NOT proven available in 2016; retrospective research only',
                           'rights': 'Copyright Visa; public manual kept locally; only observed category titles extracted'},
                'entries': dict(sorted(entries.items())), 'conflicting_source_codes': conflicts,
                'unmapped_training_codes': sorted(observed - set(entries))}
    Path(output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'mapped': len(entries), 'unmapped': manifest['unmapped_training_codes']}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--features', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    extract(args.pdf, args.features, args.output)


if __name__ == '__main__':
    main()
