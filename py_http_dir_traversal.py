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

BULK_STYLE = """
#bulk-panel { margin: 2em 0 1em; padding: 1em; border: 1px solid #999; max-width: 60em; }
#bulk-panel h2 { margin: 0 0 0.5em; font-size: 1.1em; }
#bulk-panel .hint { color: #666; font-size: 0.85em; margin: 0 0 0.75em; }
#ext-list { display: flex; flex-wrap: wrap; gap: 0.25em 1.25em; margin-bottom: 0.75em; }
#ext-list label { white-space: nowrap; }
#custom-types { margin-bottom: 0.75em; }
#custom-types .label { font-size: 0.9em; margin-bottom: 0.25em; }
#custom-list { display: flex; flex-wrap: wrap; gap: 0.35em; margin-bottom: 0.35em; }
#custom-list .row { display: inline-flex; align-items: center; gap: 0.2em; }
#custom-list input { width: 8em; }
#bulk-panel .options { margin-bottom: 0.75em; }
#bulk-panel .options label { margin-right: 1.5em; }
#bulk-status { margin-left: 1em; color: #444; }
#bulk-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.5);
              display: flex; align-items: center; justify-content: center; }
#bulk-modal.hidden { display: none; }
#bulk-modal .box { background: #fff; padding: 1em; max-width: 90vw; max-height: 85vh;
                   overflow: auto; border: 1px solid #333; }
#bulk-modal h2 { margin: 0 0 0.5em; font-size: 1.1em; }
#bulk-modal table { border-collapse: collapse; font-size: 0.9em; }
#bulk-modal td, #bulk-modal th { padding: 0.15em 0.75em 0.15em 0; text-align: left; }
#bulk-modal .actions { margin-top: 1em; }
#bulk-modal .actions button { margin-right: 0.5em; }
.status-conflict, .status-error, .status-invalid { color: #b00; }
.status-unchanged { color: #777; }
.status-renamed { color: #070; }
"""

BULK_PANEL_HTML = """
<div id="bulk-panel">
  <h2>Bulk translate &amp; rename</h2>
  <p class="hint">Pick the file types to include, then preview the changes. Nothing is
     renamed on disk until you confirm.</p>
  <div id="ext-list"></div>
  <div id="custom-types">
    <div class="label">Custom file types (not limited to what is listed above):</div>
    <div id="custom-list"></div>
    <button type="button" onclick="addCustomType()">+ Add file type</button>
  </div>
  <div class="options">
    <label><input type="checkbox" id="bulk-recursive" onchange="onRecursiveToggle()">
           Include subdirectories (recursive)</label>
    <label><input type="checkbox" id="bulk-include-dirs"> Also rename folder names</label>
  </div>
  <button type="button" id="bulk-go" onclick="bulkPreview()">Preview translations</button>
  <span id="bulk-status"></span>
</div>
<div id="bulk-modal" class="hidden">
  <div class="box">
    <h2 id="bulk-modal-title"></h2>
    <div id="bulk-modal-body"></div>
    <div class="actions" id="bulk-modal-actions"></div>
  </div>
</div>
"""

# Client-side logic for the bulk panel: scan extensions, preview, apply, undo.
BULK_SCRIPT = """
let lastApplied = [];

function initBulkPanel() {
  renderExtensions(INITIAL_EXTENSIONS);
  addCustomType('', false);
}

// Accepts "txt", ".TXT", "*.txt" and normalises them all to ".txt".
function normaliseExtension(value) {
  let ext = (value || '').trim().toLowerCase();
  while (ext.startsWith('*')) { ext = ext.slice(1); }
  if (ext && !ext.startsWith('.')) { ext = '.' + ext; }
  return ext;
}

// One field may hold several types, e.g. "mp4, mkv avi".
function customExtensions() {
  const values = [];
  document.querySelectorAll('#custom-list input').forEach(input => {
    input.value.split(/[\\s,;]+/).forEach(part => {
      const ext = normaliseExtension(part);
      if (ext) { values.push(ext); }
    });
  });
  return values;
}

function addCustomType(value, focus) {
  const row = document.createElement('span');
  row.className = 'row';
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = '.epub';
  input.value = value || '';
  input.onkeydown = event => {
    if (event.key === 'Enter') { event.preventDefault(); addCustomType(); }
  };
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.textContent = '\\u00d7';
  remove.title = 'Remove this file type';
  remove.onclick = () => {
    row.remove();
    if (!document.querySelectorAll('#custom-list input').length) { addCustomType(); }
  };
  row.appendChild(input);
  row.appendChild(remove);
  document.getElementById('custom-list').appendChild(row);
  if (focus !== false) { input.focus(); }
}

function checkedExtensions() {
  return Array.from(document.querySelectorAll('#ext-list input:checked')).map(cb => cb.value);
}

function selectedExtensions() {
  return Array.from(new Set(checkedExtensions().concat(customExtensions())));
}

function renderExtensions(extensions, keep) {
  const container = document.getElementById('ext-list');
  const checked = new Set(keep || checkedExtensions());
  container.innerHTML = '';
  if (!extensions.length) {
    container.textContent = 'No files found here.';
    return;
  }
  extensions.forEach(item => {
    const label = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = item.ext;
    cb.checked = checked.has(item.ext);
    label.appendChild(cb);
    label.appendChild(document.createTextNode(
      ' ' + (item.ext || '(no extension)') + ' (' + item.count + ')'));
    container.appendChild(label);
  });
}

async function onRecursiveToggle() {
  const recursive = document.getElementById('bulk-recursive').checked;
  const keep = checkedExtensions();
  setStatus('Scanning...');
  try {
    const url = '/bulk-scan?dir=' + encodeURIComponent(DIR_PATH) +
                '&recursive=' + (recursive ? '1' : '0');
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) { throw new Error(data.error || 'Scan failed'); }
    renderExtensions(data.extensions, keep);
    setStatus('');
  } catch (err) {
    setStatus('Scan failed: ' + err.message);
  }
}

function setStatus(text) {
  document.getElementById('bulk-status').textContent = text;
}

function bulkRequest(apply) {
  return fetch('/bulk-rename', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      dir: DIR_PATH,
      extensions: selectedExtensions(),
      recursive: document.getElementById('bulk-recursive').checked,
      include_dirs: document.getElementById('bulk-include-dirs').checked,
      apply: apply
    })
  });
}

function resultsTable(items) {
  if (!items.length) { return '<p>Nothing matched the selected file types.</p>'; }
  const rows = items.map(it => {
    const where = it.dir === '.' ? '' : it.dir + '/';
    const note = it.error ? ' (' + it.error + ')' : '';
    return '<tr><td>' + escapeHtml(where + it.old) + '</td><td>&rarr;</td><td>' +
           escapeHtml(it.new) + '</td><td class="status-' + it.status + '">' +
           it.status + escapeHtml(note) + '</td></tr>';
  });
  return '<table><tr><th>Current</th><th></th><th>Translated</th><th>Status</th></tr>' +
         rows.join('') + '</table>';
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function openModal(title, bodyHtml, actions) {
  document.getElementById('bulk-modal-title').textContent = title;
  document.getElementById('bulk-modal-body').innerHTML = bodyHtml;
  const box = document.getElementById('bulk-modal-actions');
  box.innerHTML = '';
  actions.forEach(action => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = action.label;
    btn.onclick = action.onClick;
    box.appendChild(btn);
  });
  document.getElementById('bulk-modal').classList.remove('hidden');
}

function closeModal(reload) {
  document.getElementById('bulk-modal').classList.add('hidden');
  if (reload) { window.location.reload(); }
}

async function bulkPreview() {
  const button = document.getElementById('bulk-go');
  if (!selectedExtensions().length &&
      !document.getElementById('bulk-include-dirs').checked) {
    setStatus('Select at least one file type.');
    return;
  }
  button.disabled = true;
  setStatus('Translating... this can take a while the first time.');
  try {
    const response = await fetch_preview();
    const pending = response.items.filter(it => it.status === 'pending');
    openModal(
      'Preview: ' + pending.length + ' of ' + response.items.length + ' will be renamed',
      resultsTable(response.items),
      pending.length
        ? [{label: 'Rename ' + pending.length + ' item(s)', onClick: bulkApply},
           {label: 'Cancel', onClick: () => closeModal(false)}]
        : [{label: 'Close', onClick: () => closeModal(false)}]);
    setStatus('');
  } catch (err) {
    setStatus('Failed: ' + err.message);
  }
  button.disabled = false;
}

async function fetch_preview() {
  const response = await bulkRequest(false);
  const data = await response.json();
  if (!response.ok) { throw new Error(data.error || 'Preview failed'); }
  return data;
}

async function bulkApply() {
  openModal('Renaming...', '<p>Applying changes, please wait.</p>', []);
  try {
    const response = await bulkRequest(true);
    const data = await response.json();
    if (!response.ok) { throw new Error(data.error || 'Rename failed'); }
    lastApplied = data.items.filter(it => it.status === 'renamed');
    const actions = [{label: 'Close', onClick: () => closeModal(true)}];
    if (lastApplied.length) {
      actions.unshift({label: 'Undo all', onClick: bulkUndo});
    }
    openModal('Renamed ' + lastApplied.length + ' item(s)', resultsTable(data.items), actions);
  } catch (err) {
    openModal('Rename failed', '<p>' + escapeHtml(err.message) + '</p>',
              [{label: 'Close', onClick: () => closeModal(true)}]);
  }
}

async function bulkUndo() {
  openModal('Undoing...', '<p>Restoring original names, please wait.</p>', []);
  try {
    const response = await fetch('/bulk-undo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dir: DIR_PATH, items: lastApplied})
    });
    const data = await response.json();
    if (!response.ok) { throw new Error(data.error || 'Undo failed'); }
    lastApplied = [];
    openModal('Undo complete', resultsTable(data.items),
              [{label: 'Close', onClick: () => closeModal(true)}]);
  } catch (err) {
    openModal('Undo failed', '<p>' + escapeHtml(err.message) + '</p>',
              [{label: 'Close', onClick: () => closeModal(true)}]);
  }
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
        r.append(BULK_STYLE)
        icons_css_path = os.path.join(os.path.dirname(__file__), 'icons.css')
        if os.path.exists(icons_css_path):
            with open(icons_css_path, 'r') as f:
                r.append(f.read())
        r.append('</style>')
        r.append('<script>')
        r.append('const DIR_PATH = %s;' % json.dumps(urlparse(self.path).path))
        r.append('const INITIAL_EXTENSIONS = %s;'
                 % json.dumps(self.scan_extensions(path, False) if translate_flag else []))
        r.append(RENAME_SCRIPT)
        r.append(BULK_SCRIPT)
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
        if translate_flag:
            r.append(BULK_PANEL_HTML)
            r.append('<script>initBulkPanel();</script>')
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

    # The translation cache is shared by every request; a new handler instance is
    # created per request, so it is kept on the class rather than the instance.
    translation_cache = None
    translator = None

    @staticmethod
    def cache_file_path():
        cache_dir = os.path.join(os.path.dirname(__file__), '__pycache__')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        return os.path.join(cache_dir, 'translation_cache.json')

    @classmethod
    def get_cache(cls):
        if cls.translation_cache is None:
            cache_file = cls.cache_file_path()
            cls.translation_cache = {}
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cls.translation_cache = json.load(f)
                except ValueError:
                    pass
        return cls.translation_cache

    @classmethod
    def save_cache(cls):
        with open(cls.cache_file_path(), 'w', encoding='utf-8') as f:
            json.dump(cls.get_cache(), f, ensure_ascii=False, indent=4)

    def translate_text(self, text, save=True):
        cache = self.get_cache()
        if text in cache:
            return cache[text]

        loop = asyncio.get_event_loop()
        if DirectoryHandler.translator is None:
            DirectoryHandler.translator = Translator()
        translated_text = loop.run_until_complete(
            translate_name(text, DirectoryHandler.translator, fast_translation=fast_translation))

        cache[text] = translated_text
        if save:
            self.save_cache()
        return translated_text

    @staticmethod
    def ext_of(name):
        return os.path.splitext(name)[1].lower()

    @staticmethod
    def normalise_ext(value):
        """Accept "txt", ".TXT" or "*.txt" and return the ".txt" form used for matching."""
        ext = str(value or '').strip().lower().lstrip('*')
        return '.' + ext if ext and not ext.startswith('.') else ext

    @staticmethod
    def valid_name(name):
        return bool(name) and name not in ('.', '..') and '/' not in name and '\\' not in name

    def resolve_dir(self, url_path):
        """Map a URL path to a directory inside the served root, or None if it escapes it."""
        root = os.path.realpath(os.getcwd())
        dir_path = os.path.realpath(self.translate_path(url_path or '/'))
        if dir_path != root and not dir_path.startswith(root + os.sep):
            return None
        return dir_path if os.path.isdir(dir_path) else None

    def scan_extensions(self, dir_path, recursive):
        """Count the file extensions present, for the bulk panel's checkbox list."""
        counts = {}
        try:
            if recursive:
                for _, _, files in os.walk(dir_path):
                    for name in files:
                        counts[self.ext_of(name)] = counts.get(self.ext_of(name), 0) + 1
            else:
                for name in os.listdir(dir_path):
                    if os.path.isfile(os.path.join(dir_path, name)):
                        counts[self.ext_of(name)] = counts.get(self.ext_of(name), 0) + 1
        except OSError:
            return []
        return [{'ext': ext, 'count': count} for ext, count in sorted(counts.items())]

    def collect_targets(self, dir_path, extensions, recursive, include_dirs):
        """List (parent, name, is_dir) in an order that is safe to rename in sequence.

        With os.walk(topdown=False) the deepest entries come first, so a directory is
        only renamed once everything inside it has already been handled.
        """
        exts = set(extensions)
        targets = []
        if recursive:
            for root, dirs, files in os.walk(dir_path, topdown=False):
                for name in sorted(files):
                    if self.ext_of(name) in exts:
                        targets.append((root, name, False))
                if include_dirs:
                    for name in sorted(dirs):
                        targets.append((root, name, True))
        else:
            for name in sorted(os.listdir(dir_path)):
                is_dir = os.path.isdir(os.path.join(dir_path, name))
                if is_dir and include_dirs:
                    targets.append((dir_path, name, True))
                elif not is_dir and self.ext_of(name) in exts:
                    targets.append((dir_path, name, False))
        return targets

    def bulk_run(self, dir_path, extensions, recursive, include_dirs, apply_changes):
        """Translate every matching entry, and rename it only when apply_changes is set."""
        results = []
        for parent, name, is_dir in self.collect_targets(dir_path, extensions, recursive, include_dirs):
            entry = {
                'dir': os.path.relpath(parent, dir_path).replace(os.sep, '/'),
                'old': name,
                'is_dir': is_dir,
            }
            try:
                new_name = (self.translate_text(name, save=False) or '').strip().rstrip('/')
            except Exception as e:
                entry.update({'new': name, 'status': 'error', 'error': str(e)})
                results.append(entry)
                continue
            entry['new'] = new_name
            old_path = os.path.join(parent, name)
            new_path = os.path.join(parent, new_name)
            same_entry = os.path.normcase(old_path) == os.path.normcase(new_path)
            if not self.valid_name(new_name):
                entry['status'] = 'invalid'
            elif new_name == name:
                entry['status'] = 'unchanged'
            elif os.path.exists(new_path) and not same_entry:
                entry['status'] = 'conflict'
            elif not apply_changes:
                entry['status'] = 'pending'
            else:
                try:
                    os.rename(old_path, new_path)
                    entry['status'] = 'renamed'
                except OSError as e:
                    entry['status'] = 'error'
                    entry['error'] = str(e)
            results.append(entry)
        self.save_cache()
        return results

    def send_json(self, payload, status=http.HTTPStatus.OK):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed_path = urlparse(self.path)
        query = parse_qs(parsed_path.query)
        if parsed_path.path == '/translate':
            name = query.get('name', [None])[0]
            if name:
                translated_name = self.translate_text(name)
                self.send_json({'translated_name': translated_name})
            else:
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Missing 'name' parameter")
        elif parsed_path.path == '/bulk-scan':
            dir_path = self.resolve_dir(query.get('dir', ['/'])[0])
            if dir_path is None:
                self.send_json({'error': 'Invalid directory'}, http.HTTPStatus.FORBIDDEN)
                return
            recursive = query.get('recursive', ['0'])[0] == '1'
            self.send_json({'extensions': self.scan_extensions(dir_path, recursive)})
        else:
            super().do_GET()

    def read_json_body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ('/rename', '/bulk-rename', '/bulk-undo'):
            self.send_error(http.HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        payload = self.read_json_body()
        if not isinstance(payload, dict):
            self.send_json({'error': 'Invalid request body'}, http.HTTPStatus.BAD_REQUEST)
            return

        # Every endpoint operates relative to a directory inside the served root.
        dir_path = self.resolve_dir(payload.get('dir') or '/')
        if dir_path is None:
            self.send_json({'error': 'Invalid directory'}, http.HTTPStatus.FORBIDDEN)
            return

        if route == '/bulk-rename':
            self.handle_bulk_rename(dir_path, payload)
            return
        if route == '/bulk-undo':
            self.handle_bulk_undo(dir_path, payload)
            return

        old_name = (payload.get('old') or '').strip()
        new_name = (payload.get('new') or '').strip().rstrip('/')
        if not self.valid_name(old_name) or not self.valid_name(new_name):
            self.send_json({'error': 'Invalid file name'}, http.HTTPStatus.BAD_REQUEST)
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

    def handle_bulk_rename(self, dir_path, payload):
        extensions = payload.get('extensions') or []
        if not isinstance(extensions, list):
            self.send_json({'error': 'Invalid extension list'}, http.HTTPStatus.BAD_REQUEST)
            return
        include_dirs = bool(payload.get('include_dirs'))
        if not extensions and not include_dirs:
            self.send_json({'error': 'No file types selected'}, http.HTTPStatus.BAD_REQUEST)
            return
        try:
            items = self.bulk_run(dir_path, [self.normalise_ext(e) for e in extensions],
                                  bool(payload.get('recursive')), include_dirs,
                                  bool(payload.get('apply')))
        except OSError as e:
            self.send_json({'error': str(e)}, http.HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        renamed = sum(1 for item in items if item['status'] == 'renamed')
        self.send_json({'items': items, 'renamed': renamed})

    def handle_bulk_undo(self, dir_path, payload):
        """Reverse a bulk rename, newest change first, so parents are restored
        before the entries recorded underneath their original names."""
        items = payload.get('items')
        if not isinstance(items, list):
            self.send_json({'error': 'Nothing to undo'}, http.HTTPStatus.BAD_REQUEST)
            return
        results = []
        for item in reversed(items):
            if not isinstance(item, dict):
                continue
            old_name = str(item.get('old') or '')
            new_name = str(item.get('new') or '')
            rel_dir = str(item.get('dir') or '.')
            parts = [p for p in rel_dir.split('/') if p not in ('', '.')]
            parent = os.path.realpath(os.path.join(dir_path, *parts))
            entry = {'dir': rel_dir, 'old': new_name, 'new': old_name,
                     'is_dir': bool(item.get('is_dir'))}
            if (not self.valid_name(old_name) or not self.valid_name(new_name)
                    or not (parent == dir_path or parent.startswith(dir_path + os.sep))):
                entry['status'] = 'invalid'
            elif not os.path.exists(os.path.join(parent, new_name)):
                entry['status'] = 'error'
                entry['error'] = 'no longer exists'
            else:
                try:
                    os.rename(os.path.join(parent, new_name), os.path.join(parent, old_name))
                    entry['status'] = 'renamed'
                except OSError as e:
                    entry['status'] = 'error'
                    entry['error'] = str(e)
            results.append(entry)
        self.send_json({'items': results,
                        'restored': sum(1 for r in results if r['status'] == 'renamed')})

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
