import os
import sys
import socketserver
from urllib.parse import unquote, urlparse, parse_qs
from datetime import datetime
import io
import json
import http.server
from py_translate_dir import translate_name
import threading
from googletrans import Translator
import asyncio
import signal

class DirectoryHandler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        try:
            list = os.listdir(path)
        except os.error:
            self.send_error(http.HTTPStatus.NOT_FOUND, "No permission to list directory")
            return None
        list.sort(key=lambda a: a.lower())
        r = []
        displaypath = unquote(self.path)
        r.append('<!doctype html>')
        r.append('<html>')
        r.append('<head>')
        r.append('<meta charset="utf-8">')
        r.append('<meta name="viewport" content="width=device-width">')
        r.append('<title>Index of %s</title>' % displaypath)
        r.append('<style type="text/css">')
        r.append('i.icon { display: block; height: 16px; width: 16px; }')
        r.append('table tr { white-space: nowrap; }')
        r.append('td.perms {}')
        r.append('td.file-size { text-align: right; padding-left: 1em; }')
        r.append('td.display-name { padding-left: 1em; }')
        icons_css_path = os.path.join(os.path.dirname(__file__), 'icons.css')
        if os.path.exists(icons_css_path):
            with open(icons_css_path, 'r') as f:
                r.append(f.read())
        r.append('</style>')
        r.append('<script>')
        r.append('async function fetchTranslation(name, elementId) {')
        r.append('  const response = await fetch(`/translate?name=${encodeURIComponent(name)}`);')
        r.append('  const data = await response.json();')
        r.append('  document.getElementById(elementId).innerText = data.translated_name;')
        r.append('}')
        r.append('</script>')
        r.append('</head>')
        r.append('<body>')
        r.append('<h1>Index of %s</h1>' % displaypath)
        r.append('<table>')

        # Add link to parent directory if not in root
        if self.path != '/':
            parent_path = os.path.dirname(self.path.rstrip('/'))
            if parent_path == '':
                parent_path = '/'
            r.append('<tr>')
            r.append('<td><i class="icon icon-_blank"></i></td>')
            r.append('<td class="perms"><code>(d---------)</code></td>')
            r.append('<td class="last-modified"></td>')
            r.append('<td class="file-size"></td>')
            r.append('<td class="display-name"><a href="%s">Parent Directory</a></td>' % parent_path)
            r.append('</tr>')

        for idx, name in enumerate(list):
            fullname = os.path.join(path, name)
            displayname = linkname = name
            if os.path.isdir(fullname):
                displayname = name + "/"
            linkname = name + "/"
            element_id = f'translation-{idx}'
            r.append('<tr>')
            r.append('<td><i class="icon icon-_blank"></i></td>')
            r.append('<td class="perms"><code>(%s)</code></td>' % self.get_permissions(fullname))
            r.append('<td class="last-modified">%s</td>' % self.get_last_modified(fullname))
            r.append('<td class="file-size"><code>%s</code></td>' % (self.get_size(fullname) if not os.path.isdir(fullname) else ''))
            r.append('<td class="display-name"><a href="%s">%s</a></td>' % (linkname, displayname))
            if translate_flag:
                r.append('<td class="translated-name" id="%s">Translating...</td>' % element_id)
                r.append('<script>fetchTranslation("%s", "%s");</script>' % (displayname, element_id))
            r.append('</tr>')
        r.append('</table>')
        r.append('<br><address>Python HTTP server</address>')
        r.append('</body></html>')
        encoded = '\n'.join(r).encode('utf-8', 'surrogateescape')
        f = io.BytesIO()
        f.write(encoded)
        f.seek(0)
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        return f

    def get_permissions(self, path):
        st = os.stat(path)
        is_dir = 'd' if os.path.isdir(path) else '-'
        perm = oct(st.st_mode)[-3:]
        return is_dir + ''.join(['r' if int(perm[i]) & 4 else '-' for i in range(3)]) + ''.join(['w' if int(perm[i]) & 2 else '-' for i in range(3)]) + ''.join(['x' if int(perm[i]) & 1 else '-' for i in range(3)])

    def get_last_modified(self, path):
        st = os.stat(path)
        return datetime.fromtimestamp(st.st_mtime).strftime('%d-%b-%Y %H:%M')

    def get_size(self, path):
        st = os.stat(path)
        return st.st_size

    def translate_text(self, text):
        cache_dir = os.path.join(os.path.dirname(__file__), '__pycache__')
        cache_file = os.path.join(cache_dir, 'translation_cache.json')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        else:
            cache = {}
        if text in cache:
            return cache[text]

        loop = asyncio.get_event_loop()
        if not hasattr(self, 'translator'):
            self.translator = Translator()
        translated_text = loop.run_until_complete(translate_name(text, self.translator, fast_translation=fast_translation))

        cache[text] = translated_text
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=4)
        return translated_text

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/translate':
            query = parse_qs(parsed_path.query)
            name = query.get('name', [None])[0]
            if name:
                translated_name = self.translate_text(name)
                self.send_response(http.HTTPStatus.OK)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({'translated_name': translated_name}).encode('utf-8'))
            else:
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Missing 'name' parameter")
        else:
            super().do_GET()

async def pretranslate_directory(path, translator):
    cache_dir = os.path.join(os.path.dirname(__file__), '__pycache__')
    cache_file = os.path.join(cache_dir, 'translation_cache.json')
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    else:
        cache = {}

    total_items = sum(len(files) + len(dirs) for _, dirs, files in os.walk(path))
    processed_items = 0

    for root, dirs, files in os.walk(path):
        print(f"Processing directory: {root}")
        for name in files + dirs:
            if name not in cache:
                translated_name = await translate_name(name, translator, fast_translation=fast_translation)
                cache[name] = translated_name
            processed_items += 1
            print(f"Translated {processed_items}/{total_items} items ({(processed_items / total_items) * 100:.2f}%)")

    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    def signal_handler(signal, frame):
        print("\nShutting down the server...")
        if 'httpd' in globals():
            threading.Thread(target=httpd.shutdown).start()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    import argparse
    parser = argparse.ArgumentParser(description="HTTP Directory Traversal Server")
    parser.add_argument("--port", "-p", type=int, help="Port number to serve on", default=8000)
    parser.add_argument("--directory", "-d", type=str, help="Directory to serve", default=".")
    parser.add_argument("--translate", "-t", action="store_true", help="Translate file and directory names")
    parser.add_argument("--pretranslate", "-pt", action="store_true", help="Pre-translate all file and directory names")
    parser.add_argument("--quicktranslate", "-qt", action="store_true", help="Enable quick translation mode")
    args = parser.parse_args()

    port = args.port
    directory = args.directory
    translate_flag = args.translate
    fast_translation = args.quicktranslate

    if args.pretranslate:
        translator = Translator()
        asyncio.run(pretranslate_directory(directory, translator))
        print("Pre-translation completed.")
        sys.exit(0)

    os.chdir(directory)

    handler = DirectoryHandler
    httpd = socketserver.TCPServer(("", port), handler)

    print(f"Serving HTTP on port {port} (http://localhost:{port}/) ...")
    httpd.serve_forever()
