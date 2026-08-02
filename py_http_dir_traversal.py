import os
import sys
import socketserver
from urllib.parse import unquote, quote, urlparse, parse_qs
from datetime import datetime
from html import escape
import io
import json
import http.server
from py_translate_dir import translate_name
import threading
from googletrans import Translator
import asyncio
import signal

# Client-side helpers for the per-row "Rename / Undo" button. A rename is only
# performed when the user clicks the button; nothing is renamed on disk before that.
RENAME_SCRIPT = """
const rowState = {};

function registerRow(idx, name, isDir) {
  rowState[idx] = {current: name, isDir: isDir, translated: null, original: null};
}

function refreshRow(idx) {
  const st = rowState[idx];
  const link = document.getElementById('link-' + idx);
  const btn = document.getElementById('rename-' + idx);
  const suffix = st.isDir ? '/' : '';
  link.textContent = st.current + suffix;
  link.setAttribute('href', encodeURIComponent(st.current) + suffix);
  if (st.original !== null) {
    btn.textContent = 'Undo';
    btn.title = 'Restore "' + st.original + '"';
    btn.disabled = false;
  } else if (st.translated && st.translated !== st.current) {
    btn.textContent = 'Rename';
    btn.title = 'Rename to "' + st.translated + '"';
    btn.disabled = false;
  } else {
    btn.textContent = 'Rename';
    btn.title = st.translated ? 'Name is already translated' : 'Waiting for translation';
    btn.disabled = true;
  }
}

async function fetchTranslation(idx) {
  const st = rowState[idx];
  const cell = document.getElementById('translation-' + idx);
  try {
    const response = await fetch('/translate?name=' + encodeURIComponent(st.current));
    const data = await response.json();
    st.translated = data.translated_name;
    cell.textContent = st.translated + (st.isDir ? '/' : '');
  } catch (err) {
    cell.textContent = 'Translation failed';
  }
  refreshRow(idx);
}

async function toggleRename(idx) {
  const st = rowState[idx];
  const btn = document.getElementById('rename-' + idx);
  const target = st.original !== null ? st.original : st.translated;
  if (!target) { return; }
  const previous = btn.textContent;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const response = await fetch('/rename', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dir: DIR_PATH, old: st.current, new: target})
    });
    const data = await response.json();
    if (!response.ok) { throw new Error(data.error || 'Rename failed'); }
    st.original = st.original !== null ? null : st.current;
    st.current = data.name;
  } catch (err) {
    btn.textContent = previous;
    alert('Rename failed: ' + err.message);
  }
  refreshRow(idx);
}
"""

class DirectoryHandler(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        try:
            list = os.listdir(path)
        except os.error:
            self.send_error(http.HTTPStatus.NOT_FOUND, "No permission to list directory")
            return None
        # Directories first, then files; each group sorted alphabetically.
        list.sort(key=lambda a: (not os.path.isdir(os.path.join(path, a)), a.lower()))
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
        r.append('td.rename-action { padding-left: 1em; }')
        r.append('td.rename-action button { cursor: pointer; }')
        r.append('td.rename-action button[disabled] { cursor: default; opacity: 0.5; }')
        r.append('td.translated-name { padding-left: 1em; }')
        icons_css_path = os.path.join(os.path.dirname(__file__), 'icons.css')
        if os.path.exists(icons_css_path):
            with open(icons_css_path, 'r') as f:
                r.append(f.read())
        r.append('</style>')
        r.append('<script>')
        r.append('const DIR_PATH = %s;' % json.dumps(urlparse(self.path).path))
        r.append(RENAME_SCRIPT)
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
            is_dir = os.path.isdir(fullname)
            displayname = name + "/" if is_dir else name
            linkname = quote(name, errors='surrogatepass') + ("/" if is_dir else "")
            r.append('<tr>')
            r.append('<td><i class="icon icon-_blank"></i></td>')
            r.append('<td class="perms"><code>(%s)</code></td>' % self.get_permissions(fullname))
            r.append('<td class="last-modified">%s</td>' % self.get_last_modified(fullname))
            r.append('<td class="file-size"><code>%s</code></td>' % (self.get_size(fullname) if not is_dir else ''))
            r.append('<td class="display-name"><a id="link-%d" href="%s">%s</a></td>' % (idx, linkname, escape(displayname)))
            if translate_flag:
                r.append('<td class="rename-action">'
                         '<button id="rename-%d" type="button" disabled onclick="toggleRename(%d)">Rename</button>'
                         '</td>' % (idx, idx))
                r.append('<td class="translated-name" id="translation-%d">Translating...</td>' % idx)
                r.append('<script>registerRow(%d, %s, %s); fetchTranslation(%d);</script>'
                         % (idx, json.dumps(name), json.dumps(is_dir), idx))
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

    def send_json(self, payload, status=http.HTTPStatus.OK):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/translate':
            query = parse_qs(parsed_path.query)
            name = query.get('name', [None])[0]
            if name:
                translated_name = self.translate_text(name)
                self.send_json({'translated_name': translated_name})
            else:
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Missing 'name' parameter")
        else:
            super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != '/rename':
            self.send_error(http.HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            self.send_json({'error': 'Invalid request body'}, http.HTTPStatus.BAD_REQUEST)
            return

        old_name = (payload.get('old') or '').strip()
        new_name = (payload.get('new') or '').strip().rstrip('/')
        for candidate in (old_name, new_name):
            if not candidate or candidate in ('.', '..') or '/' in candidate or '\\' in candidate:
                self.send_json({'error': 'Invalid file name'}, http.HTTPStatus.BAD_REQUEST)
                return

        # Keep the operation inside the directory that is being served.
        root = os.path.realpath(os.getcwd())
        dir_path = os.path.realpath(self.translate_path(payload.get('dir') or '/'))
        if dir_path != root and not dir_path.startswith(root + os.sep):
            self.send_json({'error': 'Directory outside served root'}, http.HTTPStatus.FORBIDDEN)
            return

        old_path = os.path.join(dir_path, old_name)
        new_path = os.path.join(dir_path, new_name)
        if not os.path.exists(old_path):
            self.send_json({'error': '"%s" no longer exists' % old_name}, http.HTTPStatus.NOT_FOUND)
            return
        # A case-only rename on Windows targets the same entry, so allow it.
        same_entry = os.path.normcase(old_path) == os.path.normcase(new_path)
        if os.path.exists(new_path) and not same_entry:
            self.send_json({'error': '"%s" already exists' % new_name}, http.HTTPStatus.CONFLICT)
            return

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            self.send_json({'error': str(e)}, http.HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json({'name': new_name})

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

    try:
        for root, dirs, files in os.walk(path):
            print(f"Processing directory: {root}")
            for name in files + dirs:
                if name not in cache:
                    translated_name = await translate_name(name, translator, fast_translation=fast_translation)
                    cache[name] = translated_name
                processed_items += 1
                print(f"Translated {processed_items}/{total_items} items ({(processed_items / total_items) * 100:.2f}%)")
    except Exception as e:
        print(f"An error occurred: {e}")


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
