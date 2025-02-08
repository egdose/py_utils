import os
import sys
import socketserver
from urllib.parse import unquote
from datetime import datetime
import io

import http.server

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
        r.append('</head>')
        r.append('<body>')
        r.append('<h1>Index of %s</h1>' % displaypath)
        r.append('<table>')
        for name in list:
            fullname = os.path.join(path, name)
            displayname = linkname = name
            if os.path.isdir(fullname):
                displayname = name + "/"
                linkname = name + "/"
            r.append('<tr>')
            r.append('<td><i class="icon icon-_blank"></i></td>')
            r.append('<td class="perms"><code>(%s)</code></td>' % self.get_permissions(fullname))
            r.append('<td class="last-modified">%s</td>' % self.get_last_modified(fullname))
            r.append('<td class="file-size"><code>%s</code></td>' % (self.get_size(fullname) if not os.path.isdir(fullname) else ''))
            r.append('<td class="display-name"><a href="%s">%s</a></td>' % (linkname, displayname))
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

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python py_http_dir_traversal.py <port> <directory>")
        sys.exit(1)

    port = int(sys.argv[1])
    directory = sys.argv[2]

    os.chdir(directory)

    handler = DirectoryHandler
    httpd = socketserver.TCPServer(("", port), handler)

    print(f"Serving HTTP on port {port} (http://localhost:{port}/) ...")
    httpd.serve_forever()