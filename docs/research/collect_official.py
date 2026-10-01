"""Read and archive public sources; no business/database changes."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.links = []
        self.skip = 0
        self.link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag in ('p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'header', 'footer'):
            self.text_parts.append('\n')
        if tag == 'a' and not self.skip:
            self.link = [attrs.get('href', ''), []]

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4'):
            self.text_parts.append('\n')
        if tag == 'a' and self.link:
            self.links.append({'href': self.link[0], 'text': ''.join(self.link[1]).strip()})
            self.link = None

    def handle_data(self, data):
        if not self.skip:
            self.text_parts.append(data)
            if self.link:
                self.link[1].append(data)

    def text(self):
        return '\n'.join(line.strip() for line in ''.join(self.text_parts).splitlines() if line.strip())


DEST = Path(__file__).resolve().parent / 'sources'
DEST.mkdir(parents=True, exist_ok=True)


def fetch(url):
    ident = hashlib.sha256(url.encode()).hexdigest()[:12]
    try:
        quoted = urllib.parse.quote(url, safe=':/?=&%+#')
        request = urllib.request.Request(quoted, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36'})
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
            kind = response.headers.get('Content-Type', '')
            final_url = response.url
            charset = response.headers.get_content_charset() or 'utf-8'
        meta = {'requested_url': url, 'final_url': final_url, 'fetched_at_utc': datetime.now(timezone.utc).isoformat(), 'content_type': kind, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'id': ident}
        if 'html' in kind or content.lstrip().startswith(b'<'):
            # GB2312/GBK public-sector archives often omit a response charset.
            early = content[:8192].decode('ascii', errors='ignore')
            match = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', early, re.I)
            if match:
                charset = match.group(1)
            page = content.decode(charset, errors='replace')
            parser = PageParser()
            parser.feed(page)
            meta['text_path'] = str(DEST / f'{ident}.txt')
            meta['raw_path'] = str(DEST / f'{ident}.html')
            (DEST / f'{ident}.html').write_bytes(content)
            (DEST / f'{ident}.txt').write_text(parser.text(), encoding='utf-8')
            meta['links'] = [{'url': urllib.parse.urljoin(final_url, link['href']), 'text': link['text']} for link in parser.links]
            unique_links = list({item['url']: item for item in meta['links'] if item['text']}.values())
            print(json.dumps({'url': final_url, 'id': ident, 'text': parser.text()[:23000], 'links': unique_links[:150]}, ensure_ascii=False), flush=True)
        else:
            suffix = Path(urllib.parse.urlparse(final_url).path).suffix or '.bin'
            meta['raw_path'] = str(DEST / f'{ident}{suffix}')
            Path(meta['raw_path']).write_bytes(content)
            print(json.dumps(meta, ensure_ascii=False), flush=True)
        (DEST / f'{ident}.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        print(json.dumps({'url': url, 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), flush=True)


def search(query):
    url = 'https://www.bing.com/search?' + urllib.parse.urlencode({'q': query, 'mkt': 'zh-CN', 'setlang': 'zh-hans'})
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode('utf-8', errors='replace')
    items = []
    for block in re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.S):
        parser = PageParser()
        parser.feed(block)
        links = []
        for link in parser.links:
            candidate = link['href']
            parsed = urllib.parse.urlparse(candidate)
            encoded = urllib.parse.parse_qs(parsed.query).get('u', [''])[0]
            if encoded.startswith('a1'):
                try:
                    candidate = base64.urlsafe_b64decode(encoded[2:] + '=' * (-len(encoded[2:]) % 4)).decode()
                except Exception:
                    pass
            links.append({'url': candidate, 'text': link['text']})
        items.append({'text': parser.text(), 'links': links})
    print(json.dumps({'query': query, 'results': items}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--url', action='append', default=[])
    args.add_argument('--search', action='append', default=[])
    options = args.parse_args()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, options.url))
        list(pool.map(search, options.search))
