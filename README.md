# Cypache

A fast, security-first HTTP server for Python on Linux, written in Cython (C++ mode) on top of fastepoll. Python handlers run natively in the server's worker processes; there is no CGI or FastCGI hop.

## Quick start

A config is a Python script that builds its sites and calls `serve()`:

```python
import cypache

def hello(request):
  return "<h1>Hello</h1>"

def user(request):
  return {"path": request.path, "query": request.query}

site = cypache.Site("example.com", "www.example.com", routes={"/": hello, "/api/user": user})
cypache.serve([site], http=8080)
```

```sh
python3 -I config.py
```

Run it as an unprivileged user: handlers run with the server's privileges. The config runs as code with them too, so it must not be writable by the server's user. To run it as a service, see Production.

## Install

`pip install cypache` in a virtual environment, which installs fastepoll too. The wheels are for CPython 3.12 to 3.15 on Linux x86-64 with glibc 2.34 or newer, OpenSSL 3 and libnghttp2 1.60 or newer (`libnghttp2-14`; Debian 13 has 1.64), which stay the system's, so their security updates apply. For a server, see Production.

## Sites

`Site(*hosts, routes=None, root=None, index=("index.html",), precompressed=False, cacheControl=None, apps=None, views=None, tls=None, hsts=31536000, default=False)`
- `hosts`: bare names or addresses (`"example.com"`, `"127.0.0.1"`, `"[::1]"`), matched case-insensitively against the request's Host without its port or a trailing dot. IPv6 addresses match in any spelling.
- A request for a host no Site lists goes to the Site with `default=True`, or gets `421 Misdirected Request` if there is none. There is no implicit default, so a site never answers for domains pointed at it by others (DNS rebinding).
- A request is served by the first of these that takes it:
  1. `routes`: exact, normalized paths to `handler(request)`;
  2. `root`: a directory of static files, for GET and HEAD. A file that isn't there, or a directory requested without its trailing slash, is left to the apps when there are any (like nginx's `try_files`);
  3. `apps` and `views`: directories of Python modules, see Apps below.
- `index`: the files tried, in order, for a static path ending in `/`.
- `precompressed`: serve `style.css.br` or `.gz` copies next to the files (see below). Off by default, since looking for them costs two opens per request.
- `cacheControl`: a `Cache-Control` value for static responses, such as `"public, max-age=3600"`. Without it caches guess freshness from the file's age.
- `tls`, `hsts`: see HTTPS below.

## HTTPS

```python
site = cypache.Site("example.com", "www.example.com", root="/srv/example/www", tls="/etc/cypache/tls/example.com")
cypache.serve([site], http=80, https=443, acme="/var/www/acme")
```

- **`tls`:** a directory with `fullchain.pem` and `privkey.pem` (certbot's layout), or a `(certificate, key)` pair of files. Keys must be unencrypted.
- **Certificates per Site:** each Site's certificate is sent to the names it serves (SNI). Clients without SNI, or with a name no Site has, get the certificate of the default Site, or of the first Site with `tls` if that one has none.
- **One Site per connection:** a request whose Host belongs to another Site than the certificate the client checked gets `421`. One certificate can't be used to reach another site on the same connection (domain fronting).
- **Plain HTTP:** a Site with `tls` answers plain HTTP with `308` to the same URL over HTTPS (on the first `https` port). A request with a body is redirected without reading it, so the body isn't sent in the clear. Sites without `tls` are served over plain HTTP as before.
- **HSTS:** every HTTPS response carries `Strict-Transport-Security: max-age=31536000`, so browsers that visited refuse plain HTTP for that domain for a year.
  - `hsts=0` turns it off. Turn it off before dropping HTTPS for a domain, then wait out the old max-age.
  - `includeSubDomains` and `preload` aren't sent, since they commit other names too.
- **`acme`:** a certbot webroot. Over plain HTTP, `/.well-known/acme-challenge/` is served from it for every Site, with or without `tls`. Without `acme`, challenge requests for Sites with `tls` are redirected to HTTPS like everything else, which Let's Encrypt follows.
- **`Request.scheme`:** `"https"` or `"http"`.
- **TLS itself is fastepoll's:**
  - TLS 1.2 and 1.3 with forward-secret AEAD ciphers only, and post-quantum hybrid key exchange where OpenSSL has it;
  - no renegotiation, 0-RTT or compression;
  - `handshakeTimeout` (10 s) to finish the handshake;
  - zero-copy `sendfile` through kernel TLS when the `tls` module is loaded, on Linux 6.14 or later. Put `tls` into `/etc/modules-load.d/`, since only root can load it. Older kernels can't change TLS 1.3 keys on a live connection, so there fastepoll encrypts in userspace (stock Debian 13's 6.12 among them; trixie-backports has newer kernels).

### HTTP/2

HTTPS speaks HTTP/2 to clients that offer it (ALPN `h2`), and HTTP/1.1 to the rest; `serve(http2=False)` turns it off. There is no HTTP/2 over plain HTTP (h2c) and no server push. The framing is nghttp2's, the library curl and Apache use, with its defenses against known floods (rapid resets, CONTINUATION frames, settings, pings, HPACK).

- **The same request handling:** each stream's request goes to the HTTP/1 parser as an HTTP/1.1 message with version `HTTP/2.0`, so every rule above applies to it: paths, Host, limits, routes, uploads and their budgets, `minBodyRate`, and the access log. `request.version` is `"HTTP/2.0"`.
  - `:authority` is the Host; a `host` field that differs from it gets `400`. Cookie fields are joined into one. `expect: 100-continue` is dropped (there's no 100 Continue in between), and other expectations get `417`.
  - A field nghttp2 would silently drop (control characters, surrounding whitespace) resets the stream instead, so nothing a client sent goes missing unseen. Malformed pseudo-header fields and connection-specific fields (`connection`, `transfer-encoding`, `te` other than `trailers`) reset the stream, or nghttp2 ends the connection.
  - Those resets happen before there's a request, so they get no access log line. A client that provokes more than 1000 of them at once (then 33 a second) is disconnected (MadeYouReset, CVE-2025-8671).
- **One Site per connection:** a request for another Site than the certificate's gets `421`, after which browsers open a new connection (they share HTTP/2 connections between names one certificate covers).
- **Streams:** at most 32 per connection. A stream the client resets keeps counting for as long as its handler runs, so resetting streams (CVE-2023-44487) can't run more than 32 handlers at once; beyond that, new streams are refused (`REFUSED_STREAM`, which clients retry). A handler whose client left is skipped if it hasn't started, as in HTTP/1.
- **Request bodies:** a stream may send 1 MiB ahead, a connection 16 MiB, all of it counted against `maxBodyMemory` and `maxSpoolBytes` as it arrives. A response complete before its request (`413`, say) tells the client to stop sending.
- **Slow readers:** a connection holds up to 32 responses, each with its body or file, so its client must take them at `minBodyRate` on average, as it sends request bodies: each byte sent buys 1/`minBodyRate` seconds, banked up to `headerTimeout` ahead. A connection that falls behind is closed. While its responses wait, no more of its requests are read.
- **Timeouts:** a connection without streams gets GOAWAY after `headerTimeout`, as an idle HTTP/1.1 connection closes, and is closed if it's still there `headerTimeout` later. One with a header block left unfinished for `headerTimeout` is closed.
- **Output** is gathered: the frames that are ready go out in one write of up to 256 KiB, since each write costs a `send()` and a TLS record. Files are read into it frame by frame, rather than sent with a `sendfile()` per 16 KiB frame. A file that shrinks before its response is out resets the stream.
- **Files held:** a response waiting for the client's flow control holds its file open; per worker, such responses may hold a quarter of the open-file limit. A stream over that is refused, and logged as `503`.
- **Responses nghttp2 can't send** (header fields over its 64 KiB for a block) reset their stream.
- **Shutdown:** SIGTERM sends GOAWAY; streams already received finish.
- **Dependency:** nghttp2 is the system's `libnghttp2.so.14` (Ubuntu and Debian install it with curl), so its security updates come with the system's.

Known issues, waiting on fastepoll:
- **Retry handshakes:** in a TLS 1.3 handshake with a HelloRetryRequest, a client can send a different server name the second time. Then the certificate it was shown and the Site its requests are bound to can differ.
- **Session tickets:** a TLS 1.3 ticket from one Site's connection can resume a connection for another Site's name, without that Site's certificate being shown. Clients are supposed to prevent this themselves (RFC 8446 4.6.1), so only a dishonest client can do it, and it gains nothing a fresh handshake wouldn't give.

### Certbot

Cypache runs as an unprivileged user, and certbot's private keys are readable only by root. `tools/certbot-deploy-hook` copies each new or renewed certificate for the `cypache` group, then has Cypache reload its certificates (`systemctl try-reload-or-restart cypache`, which leaves a stopped server alone). For a name's first certificate it restarts Cypache instead (`try-restart`), so a config that looks for certificates by name, like the default one, serves it:

```sh
certbot certonly --webroot -w /var/www/acme -d example.com -d www.example.com --deploy-hook /opt/cypache/tools/certbot-deploy-hook
```

- **The copy:**
  - The hook checks that certificate and key match.
  - It writes them into a new directory and switches the symlink `/etc/cypache/tls/<name>` to it in one rename, so Cypache never reads a certificate and key from different renewals.
  - It keeps the previous pair and removes older ones.
- **The reload** (SIGHUP, via `ExecReload=kill -HUP $MAINPID` in the systemd unit) loads the new certificates for new connections; open ones keep theirs. If the new files don't load, the previous certificates stay in use and the error is printed.
- **Per certificate:** pass the hook with `--deploy-hook`, as above. In `renewal-hooks/deploy` it would copy every certificate's key for the `cypache` group, also those of other services.
- **Ownership:** certbot runs the hook as root on every renewal. The hook, every directory above it, `/etc/cypache` and the webroot must be root's and writable by no one else.
  - A cypache user that could change the hook would become root.
  - One that could change the webroot could point certbot's root-run writes elsewhere, for example by making it a symlink.
  - The hook refuses to run if `/etc/cypache` or `/etc/cypache/tls` isn't root's, is a symlink, or is writable by its group or others.
  - It also refuses if `/etc/cypache/tls/<name>` is a real directory rather than its symlink; move such a directory away once.

## Static files

- **Confinement:** every file is opened with `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV)` relative to the root.
  - The kernel refuses anything outside the root.
  - No symlinks inside it are followed, so the name that's checked is the file that's served. The root itself may be a symlink.
  - No mount points inside it are crossed, so every file is on the root's own, checked filesystem.
  - Hard links are the same file under another name: whoever can create files in the root decides what they expose.
- **Deploys:** the root is reopened every second. If it's a symlink, `ln -s release2 next && mv -T next current` switches to a new release atomically. Replace single files by renaming a new one over them too: a response already being sent keeps the old file, while rewriting a file in place can send a mix of old and new content.
- **Never served:**
  - hidden files: any path segment starting with `.`, except a leading `/.well-known/` (certbot's challenges, `security.txt`);
  - in any case, Python and Cython source, bytecode and notebooks (`.py`, `.pyc`, `.pyw`, `.py3`, `.pyx`, `.pxd`, `.ipynb`), also compressed or as editor, RCS and backup copies (`app.py.gz`, `app.py~`, `#app.py#`, `app.py,v`);
  - backup files (`~`, `.bak`, `.old`, `.orig`, `.save`, `.swp`);
  - symlinks, and anything but regular files and directories (FIFOs, devices, sockets).

  These all get `404`, unreadable files `403`.
- **Filesystems:** the root must be on ext2/3/4, btrfs, XFS, tmpfs, F2FS, overlayfs, ZFS or bcachefs, and not in a casefold directory.
  - Anything else is refused at startup, including FAT, exFAT, NTFS (also through FUSE), SMB, NFS and 9p. There, other spellings of a name (`APP.PY`, `GIT~1`, `.env.`) could get past the rules above.
  - Keep casefold directories out of the tree below the root as well.
  - Don't use ZFS datasets with `casesensitivity=insensitive` or XFS with `ascii-ci`, which can't be detected.
- **Directories:** a request without the trailing slash gets `308` to add it. There are no directory listings.
- **Methods:** `GET` and `HEAD`; anything else gets `405`.
- **Content-Type:** from a fixed table of extensions, with `charset=utf-8` for text. Unknown extensions are `application/octet-stream`, which `nosniff` keeps browsers from executing.
- **Caching and conditionals:**
  - `ETag` is made of the size, the nanosecond mtime and the inode, so a newly deployed file gets a new one even when its mtime was reset. Hard links and `chmod` keep it. ETags differ between servers behind a load balancer, which only costs revalidations.
  - `Last-Modified` is never later than `Date`.
  - `If-None-Match`, `If-Modified-Since`, `If-Match` and `If-Unmodified-Since` are evaluated in RFC 9110 order.
  - `If-Range` only accepts an entity tag, because a date can't tell apart versions written within the same second.
- **Ranges:** one byte range per request (`206`/`416`). A request for several ranges gets the whole file, which avoids multipart responses and range amplification.
- **Precompressed files:** with `precompressed=True`, `style.css.br` or `style.css.gz` next to `style.css` is sent to clients that accept it. This applies to text-like types and not to range requests, with `Vary: Accept-Encoding`. Cypache doesn't compress on the fly.
- **Sending:** files up to 16 KiB go out with the head in one write. Larger ones use fastepoll's zero-copy `sendfile`, which stays zero-copy under kernel TLS. Reading a file that isn't in the page cache blocks the worker, as in nginx without thread pools.
- **One response in flight:** a connection gets its next pipelined request answered only once the previous response has gone out. A client that doesn't read can therefore hold at most one open file.
- **Open files:** `serve()` raises the soft open-file limit to 4 × `maxConnections` + 64 to match fastepoll's quota for queued files. If the hard limit is lower, it refuses to start.
- **FIFOs:** opening one can't block a worker (the open is non-blocking), but it does wake a process waiting to write to it. Only someone who can create files in the root can set that up.

## Handlers

A handler gets a `Request` and returns one of:
- `str`: HTML (`text/html; charset=utf-8`)
- `bytes`: `application/octet-stream`
- `dict` or `list`: JSON
- `Response(body=b"", status=200, headers=(), contentType=None)`: anything else. Headers are a dict or a list of pairs (for repeated headers such as `Set-Cookie`). Names that the server controls (`Content-Length`, `Transfer-Encoding`, `Connection`, `Date`, ...) and values with control characters are refused when the `Response` is built. Spaces and tabs around a value are dropped, as HTTP/1 clients would drop them (HTTP/2 doesn't allow them).

`Request` has:
- **From the request:**
  - `method`, `path` (percent-decoded, dot segments resolved, slashes merged), `query` (raw), `host` (lowercase, no port), `version`, `scheme`;
  - `headers` (lowercase names; repeated fields joined with `", "`, cookies with `"; "`) and `remote` (the peer address).
  - `body`: the whole body as bytes; `stream`: a new read-only file over it. A body over `spoolBytes` is in a spool file (see Uploads), and `body` reads it into memory at first use, where `stream` doesn't.
  - **Parsed on first use:**
    - `args` (the query) and `cookies`;
    - `form`: the fields of an urlencoded or multipart body;
    - `files` and `uploads`: a multipart body's files, by name or all of them.

    A repeated name keeps its last value, and a repeated cookie its first. `args` and `form` are dicts with `getlist(name)` for all of a name's values, in order: `request.form.getlist("options[]")` for a multiple choice, or checkboxes sharing a name.
- **For the response,** when a handler returns a bare body or nothing:
  - `status`, `contentType`, `setHeader(name, value)`;
  - `setCookie(name, value, maxAge=None, path="/", domain=None, secure=None, httpOnly=True, sameSite="Lax")`, which is `Secure` by default over HTTPS.

  Headers and cookies set this way are also added to a `Response` the handler returns.
- **Apps only:** `params`, `view()`, `dispatch()` and `stop()`.
- **Anything else** (`request.user = ...`) is free for the handlers along one request to share. The attributes above are read-only, except `status` and `contentType`.

`redirect(location, status=303)` makes a redirect response (301, 302, 303, 307 or 308). Characters a URL can't hold, non-ASCII and backslashes among them, are percent-encoded.

**Threads:**
- **The pool:** handlers run in threads in each worker (`serve(threads=16)`), so one waiting on a database doesn't hold up the worker's other connections.
  - They run concurrently, so module-level state (a cache dict, a shared connection) is shared between requests. Use thread-safe objects or locks, or one connection per thread; code ported from PHP's one-request-at-a-time model needs a look for this.
  - Python's thread switch interval is set to 1 ms, so a CPU-bound handler can't hold up the event loop for the default 5 ms.
- **Inline:** a handler or view `render` that never blocks can be marked `@cypache.inline` to run on the worker's event loop instead, which is faster. An apps walk starts on the loop if its first handler is marked, and moves to the pool at the first handler or view that isn't.
- **Waiting requests:** while a handler runs, its connection keeps what the client sends, up to `maxHeaderBytes`, and answers it afterwards, in order. Its request body stays counted against `maxBodyMemory` until the handler is done.
- **Overload:** with 16 jobs per thread already waiting, a request gets `503`.
- **A client that leaves:** a job doesn't run if its connection is already gone (reset, or dropped by a timeout) when its turn comes. A client that only half-closes over plain HTTP still gets its answer, so its job runs.
- **Stuck handlers:** a thread can't be stopped from outside, so a handler that hangs (an endless loop, a stuck query) keeps its thread.
  - Past `handlerTimeout` (120 s), the worker prints where it's stuck, with a stack trace, once.
  - When all the worker's threads are stuck, it can't run handlers anymore. It then shuts down like on SIGTERM (other connections finish, the stuck ones are cut at `shutdownTimeout`), and fastepoll starts a fresh one. With `workers=1` the server exits.
  - `handlerTimeout=None` turns this off.
  - fastepoll's `idleTimeout` still drops the connection of a handler with no I/O for that long.
- **Shutdown:** SIGTERM lets responses in progress finish, then closes their connections, within `shutdownTimeout`. Idle connections close at once, but one without a request yet, or with part of a head, gets a second more: its request may be on its way. The threads don't hold up the exit.
- **Forking:** a process forked in a handler must end with `os._exit()`. One that returns into the server instead is ended with a message.
- **Half-close:** over HTTPS a client that half-closes its connection while its handler runs gets no response, since TLS has no half-close: the connection is gone, and the access log shows `499`.

An exception in a handler, or an invalid return value, gives the client a plain `500` and prints the traceback (at most 10 per 10 s per worker; the rest are counted). Raising `cypache.BadRequest` gives `400` without a log line, as do `args` and `form` with more than 1000 fields. Only `SystemExit` and `KeyboardInterrupt` stop the worker, as in fastepoll, also when the client left before the handler finished.

## Uploads

A site that takes uploads raises `maxBodyBytes`, for example `serve(..., maxBodyBytes=100 << 20)`:

- **Spooling:** a body over `spoolBytes` (1 MiB) is written to a spool file while it arrives: an unnamed temporary file in `spoolDir` (`O_TMPFILE`: it has no name, and is gone once closed).
  - It counts against `maxSpoolBytes` (1 GiB per worker) instead of `maxBodyMemory` for as long as the file is open; past that, `503`. The file closes once nothing holds the request, or an upload from it, anymore.
  - `spoolDir` defaults to `/var/tmp`, which is on disk where `/tmp` is often in memory (tmpfs). It's opened once at startup, must belong to root or the server's user, and its filesystem must support `O_TMPFILE` (ext4, XFS, btrfs and tmpfs do). `serve()` checks this, and warns when workers × `maxSpoolBytes` is more than the free space there.
  - The writes happen on the event loop, one per read however small the chunks, so `spoolDir` belongs on fast local storage.
  - No other process can open a spool file: it has no name, and the server isn't dumpable, so not even processes of its own user reach it through `/proc` (see Production). On disk its data may be written out, and on tmpfs swapped out.
- **Files:** `request.files["photo"]` (the last file per name) and `request.uploads` (all of them, as an `<input type=file multiple>` sends them) are `Upload`s:
  - `name`, `contentType` and `size`;
  - `filename`: the client's name for the file. Directories are removed (also slashes that NFKC normalization would make), as are control and bidi characters; a name of only dots and spaces becomes `""`. Never use it as a path on the server;
  - `read()` for the bytes and `open()` for a file object;
  - `save(path, mode=0o640)`: writes the file to `path`, which must not exist yet. It's written unnamed, copied in the kernel from a spool file, and then linked into place, so it appears whole or not at all. Nothing at `path` is overwritten or followed, a symlink included; the directories leading there are the app's own, and are followed. The filesystem must support `O_TMPFILE`. It refuses a path among the code, where the file would run (see Reload): in any Site's apps or views, in the directory of other code the server loaded, such as a site's package, or in a directory on Python's import path (`sys.path`), such as a site's folder in the default config.
- **No copies:** an upload stays where it is in the body (in memory, or in the spool file). Copies of it share the spool file, and it can't be pickled.
- **Memory:** `request.body` reads a spooled body into memory, so a handler that may get large bodies reads `request.stream` or the uploads instead. An urlencoded `form` is parsed in memory, so it's limited to 1 MiB (`400` beyond).
- **The multipart parser** is strict. Anything another parser in front (a WAF, a proxy) could read differently gets `400`:
  - the body must start with its boundary, and the boundary may appear only as a delimiter line of its own (after a CRLF), with nothing after the last one but a CRLF. A form with no entries (`--boundary--`) is fine;
  - `Content-Type` (one per request) and `Content-Disposition` parameters follow RFC 9110: `name=token` or `name="quoted"`, separated by `;` with spaces (not tabs) around it, and nothing else. No whitespace around `=`, no repeated names, no `*` in a name (RFC 2231 continuations), no `'` or `*` in an unquoted value, and no quoted value ending in a backslash (browsers send backslashes as they are, other parsers read them as escapes);
  - `filename*` only unquoted, next to `filename` and with the same name, as .NET sends them, since parsers pick one or the other;
  - every part has one `form-data` disposition with a UTF-8 name, no control characters but tabs in its header fields, and no `Content-Transfer-Encoding` but `7bit`, `8bit` or `binary`. Its `Content-Type` is a `type/subtype` other than `multipart/*` or `message/*`, has no `name` parameter, and for a plain field, no charset but UTF-8 (or US-ASCII). A file with content has a filename;
  - at most 1000 parts and 16 KiB of header fields per part;
  - the part headers and the plain fields, which are kept in memory, come to at most 1 MiB together.

  Some uploads get `400` because of this: a filename ending in a backslash, a filename with a `"` from clients that send it as `\"` (Go's `mime/multipart`, aiohttp; browsers, curl and Python's requests send `%22`), and a non-ASCII filename from .NET, whose `filename` is then RFC 2047-encoded. Names and filenames are kept as sent: the `%22`, `%0A` and `%0D` browsers send for `"`, LF and CR aren't decoded, and backslashes are literal. The parser runs at first use, in the handler's thread: a handler marked inline runs on the event loop, so it mustn't read a large form.
- **Slow clients:** a body must keep arriving at `minBodyRate` (8 KiB/s) on average. Each byte buys 1/`minBodyRate` seconds, banked up to `bodyTimeout` (60 s) ahead:
  - a body arriving that fast never times out, whatever its size;
  - one that stalls gets `408` within `bodyTimeout`, however much arrived before;
  - one that's slower gets `bodyTimeout` at least, and more the closer it comes to the rate.

  Sites with clients on very slow links lower `minBodyRate`. The rate is also what holding a budget costs: a client that sends most of a body at once and the rest at `minBodyRate` keeps that much of `maxBodyMemory` or `maxSpoolBytes` for up to `maxBodyBytes / minBodyRate` seconds, a legitimate slow upload just the same. With `maxBodyBytes=1 << 30` that's 36 hours at 8 KiB/s, so a site taking large uploads raises `minBodyRate`, or its budgets, to match.

## Apps

File-based routing like a PHP router's, for a layout such as:

```
sites/example/
  public/            Site(root=...)     static files, tried first
  controllers/       Site(apps=...)
    auto.py          def auto(request): ...      runs first, for every request
    index.py         def get(request): ...       takes what nothing deeper matches
    news.py          def get(request): ...       /news
    world/
      auto.py        def auto(request): ...      runs for /world/...
      character.py   def get(request): ...       /world/character/Bubble: request.params == ("Bubble",)
                     def post(request): ...
      map.png.py     def get(request): ...       /world/map.png
      _queries.py    shared code, imported with "from . import _queries"
  views/             Site(views=...)
    header.py        def render(request): return "<html>..."
    news.py          def render(request): return ...  the view mirroring controllers/news.py
```

- **The walk:** the path is walked against the apps directory one segment at a time.
  - Every `auto.py` on the way runs its `auto(request)`, the top one first, whatever the method.
  - The deepest `name.py` named like the next segment is the endpoint, or else the nearest `index.py` on the way.
  - `request.params` holds the segments the walk didn't consume, for each `auto` and the endpoint.
- **Methods:** the endpoint's `get`, `post`, `put`, `delete`, `patch` or `options` handles the request, and HEAD uses `get`. Only functions the module defines itself count, so an imported `get` or `delete` is no handler. A decorator on a handler must keep its identity with `functools.wraps`; otherwise the wrapper looks imported and the method gets `405`.
  - Other methods get `405` (`OPTIONS` gets `204`), with `Allow` listing the ones it has.
  - That answer comes after the autos on the walk ran, so an auth `auto.py` keeps it from revealing what's behind it.
- **Ending a request:** an `auto` or endpoint that returns something ends the request with it as the response, such as `cypache.redirect("/login")` from an `auto.py` that checks a session.
- **Views:** when nothing returns a response, the views render. `request.view(path, slot="body")` queues one into the `header`, `body` or `footer` slot. The path is absolute from the views directory, or relative to the running handler's directory, and `name` finds `name.py` or `name/index.py`.
  - An endpoint that queues no body view and dispatches nothing gets the view mirroring its own path.
  - The slots render in order, and each view's `render(request)` returns a `str`.
- **`request.dispatch(path)`** walks another path in the same request, after what's queued, skipping `auto.py` files that already ran: an `auto` that checks the specific resource of the first path isn't asked again. One request runs at most 64 handlers, so a dispatch loop ends in a `500`.
- **No body:** with `request.status` set to 204, 205 or 304, no views render.
- **`request.stop()`** cancels the endpoint of the walk the running `auto` belongs to, so an `auto.py` can take over the routing.
- **Loading:**
  - Modules are imported at startup, as a package per Site, so relative imports work.
  - **Reload:** with `serve(reload=True)`, the default, a changed or new `.py` is served a second or so after it's saved, much as PHP picks up an edit:
    - The process that runs the config binds the ports and becomes the launcher (`python -m cypache`, with the same PID), which keeps the listening sockets and forks a child, a generation, to run the config again and serve them. So the config runs twice at startup, and it must be a file, a directory or zipfile with a `__main__.py`, or a module run with `-m`. The launcher imports nothing from the working directory.
    - The launcher looks twice a second at every `.py` in the apps and views directories, and at the files of the other modules the generation loaded, such as the config and a site's own package (not Python's own, nor installed packages).
    - `watch` names folders the config looks into, such as the one it makes a site of each subfolder of: a subfolder appearing or going there reloads too, but not files, which come and go as a site serves. The default config watches `/var/www/sites` and each site's folder, so a new site or a site's new `public/` is served without a restart.
    - Once the changes settle, it starts the next generation, which loads all the code while the previous one serves. If it loads, it serves the launcher's sockets at once, and the previous one finishes its requests as on SIGTERM. The ports never go free: a client that connects as generations change waits in the queue, and one in its TLS handshake with the previous generation is served by it. At most two generations finish their requests at a time; further changes wait for one of them.
    - Code that doesn't load, or a config that now listens on other ports or addresses (which takes a restart), is reported on stderr, and the previous code keeps serving. What serves is always what loaded, also when files change meanwhile. Paths are watched as configured, so a deploy that switches a symlink to another release reloads too.
    - `reload=False` imports once, in the calling process: code changes then need a restart, and a file added later never runs. With reload, a `.py` runs a moment after it appears, so the code directories must be writable by root only (see Production), and `Upload.save()` refuses them. Turning reload off in the config stops the reloads, and takes a restart to serve without the launcher.
    - The launcher passes SIGTERM, SIGQUIT and SIGHUP on to the serving generation, and SIGINT too unless it came from the terminal, which sends Ctrl-C to the generation itself. A generation that ends by itself ends the launcher at once, with its exit status. On a stop, the ports stay open until the server is done: clients that connect meanwhile wait, and get a reset at the end.
    - Cypache's processes write no `__pycache__`: Python takes a `.pyc` for current while its source's size and mtime in whole seconds match, so one written in the same second as a later edit would hide that edit.
  - Names starting with `.` or `_` aren't routed, and symlinks are refused.
  - A routed module must define at least one method function (or `auto` for `auto.py`, `render` for a view). Still, give helpers `_` names: a `cache.py` that defines `get(key)` would be routed.
  - Each module is loaded from exactly its `name.py`, never a compiled module of the same name next to it.
  - Directories are packages without running an `__init__.py`. A `name.py` and a `name/` directory side by side are refused, since they'd be the same module.
- **Separation:** no Site's static root, nor the `acme` directory, may overlap any Site's apps or views directories. This is checked at startup, and again every second for a root that is a symlink, which then gets `503` if it was switched onto code.
- **Static files come first:** a file under the static root is served without any `auto.py` running, so everything there is public.

## Options

`serve(sites, http=8080, https=None, host=None, workers=0, acme=None, ...)`:

| Option | Default | |
| --- | --- | --- |
| `http` | `8080` | port or list of ports, `None` for none |
| `https` | `None` | port or list of ports; needed when a Site has `tls` |
| `host` | `None` | address(es) to listen on; `None` is all interfaces |
| `acme` | `None` | certbot webroot served for `/.well-known/acme-challenge/` over plain HTTP |
| `workers` | `0` | worker processes, `0` = one per CPU it may run on (its affinity, as `taskset` or a container sets it), `1` = in the calling process |
| `threads` | `16` | handler threads per worker |
| `handlerTimeout` | `120` | seconds before a running handler is reported as stuck; `None` turns it off |
| `headerTimeout` | `15` | seconds to receive a complete request head, from connect or the previous request; idle keep-alive connections close after it |
| `bodyTimeout` | `60` | seconds a request body may fall behind `minBodyRate`: to start, or stalled (see Uploads) |
| `minBodyRate` | `8192` | bytes per second a body must arrive at, on average, to get more time than `bodyTimeout`; and a response be taken at (below) |
| `idleTimeout` | `60` | seconds without any I/O progress, such as a client that stops reading |
| `handshakeTimeout` | `10` | seconds to finish the TLS handshake |
| `maxConnections` | `10000` | per worker; needs an open file limit of 4 × this + 64 (`LimitNOFILE=` in systemd) |
| `shutdownTimeout` | `10` | seconds to finish open requests after SIGTERM/SIGINT |
| `maxHeaderBytes` | `16384` | request line plus headers (`431`, or `414` for the request line) |
| `maxHeaders` | `100` | header fields (`431`) |
| `maxBodyBytes` | `1048576` | request body (`413`) |
| `maxBodyMemory` | `67108864` | request body bytes received and buffered at once per worker (`503`); the memory holding them can reach about twice that while bodies grow |
| `spoolBytes` | `1048576` | larger bodies go to a spool file instead of memory |
| `maxSpoolBytes` | `1073741824` | spooled body bytes at once per worker (`503`) |
| `spoolDir` | `/var/tmp` | where spool files are made |
| `accessLog` | `None` | file for the access log (see below), `None` for none |
| `http2` | `True` | HTTP/2 over HTTPS, for clients that offer it |
| `serverName` | `"Cypache"` | `Server` header value, `""` to omit it |
| `reload` | `True` | serve the new code when a `.py` changes, once it loads (see Apps, Loading) |
| `watch` | `()` | folders whose subfolders appearing or going reload too, with `reload` (see Apps, Loading) |

Timeouts can't be turned off. A client that is still receiving an earlier response gets more time, but only while it takes that response at `minBodyRate` on average: each byte buys 1/`minBodyRate` seconds, banked up to `headerTimeout` ahead, as over HTTP/2. One that falls behind is disconnected, and the rest of its response dropped, so a client reading slowly can't hold a connection for hours (slow read). Sites with clients on very slow links lower `minBodyRate`.

Clients that really send `maxBodyMemory` (or `maxSpoolBytes`) worth of body bytes and then stall can make other uploads on that worker get `503` for up to `bodyTimeout`; clients that keep sending at `minBodyRate` hold their share until their bodies are complete. Size the budgets for the uploads a site expects.

## Access log

`serve(..., accessLog="/var/log/cypache/access.log")` writes a line per response, in the combined format that log analyzers and fail2ban read, followed by the Host and the seconds the response took:

```
203.0.113.7 - - [28/Oct/2026:12:03:16 +0100] "GET /news HTTP/1.1" 200 5120 "https://example.com/" "Mozilla/5.0 ..." "example.com" 0.006
```

- **Fields:**
  - the client's address and the local time;
  - the request line as received;
  - the status and the body bytes sent (0 for HEAD and 304);
  - `Referer` and `User-Agent`;
  - the Host the request was routed by, `-` for a request that failed before its head was parsed;
  - the seconds from the complete request head to the response being handed on, which for a file is before its bytes are sent.
- **Escaping:** as in nginx, `"`, `\` and bytes outside printable ASCII appear as `\xHH`, so a client can't forge a line or break the quoting. An empty field is `-`.
- **What gets a line:** every response Cypache sends, its own errors and redirects included, and as `499` (nginx's code) a request whose connection closed before its handler was done: the client reset it (or over HTTPS, which has no half-close, closed its side), or a timeout or shutdown cut it. A connection that closes while idle, or before its request has fully arrived, gets none.
- **Rotation:** each worker checks once a second whether the file was renamed or removed, and opens it anew; lines from that second may still go to the old file. `tools/cypache.logrotate` rotates daily and keeps 14 days, without signals; the server creates the new file itself.
- **Writes:** the log must be a regular file. Each line is one write to it, opened for appending, so the workers' lines never mix. When writing fails (a full disk), the server goes on serving: it prints the error once, and once the log works again, how many lines were lost. A line cut short by a full disk can run into another worker's next one.

## Production

Installed as below, as root, Cypache runs as a systemd service on ports 80 and 443 with its config, `/etc/cypache/config.py` (`tools/config.py`). Its man pages cover this too: `man cypache` for running it, `man cypache-config` for the config, `man cypache-apps` for the sites' Python. That config serves each folder in `/var/www/sites` as a site, named after its host:

```
/var/www/sites/example.com/public/        static files
/var/www/sites/example.com/controllers/   apps (see Apps)
/var/www/sites/example.com/views/         their views
/var/www/sites/example.com/example/       the site's own package, which its controllers can import
```

- **Names:** a folder answers for its name and `www.` its name. One named `default`, or a symlink to a site's folder, answers for every other name, also for domains others point at the server (see Sites). Until there's a site at all, a page says Cypache works. A `lost+found` (where `/var/www/sites` is a filesystem of its own) is no site; any other folder whose name isn't a host name stops the config with an error.
- **Packages:** the sites' folders are on one import path, so a site's package (or module) needs a name no other site's package, and none of Python's or the installed ones, has. The config stops with an error otherwise, rather than let one site's `import` load another's code, or the wrong module.
- **Sites share the workers:** every site's code runs in the same worker processes, and those also hold every site's TLS private key (nginx's workers hold the keys too, but run no site code). So code that one site runs can read the other sites' data and keys: sites that don't trust each other belong on separate servers, each with a Cypache of its own.
- **Changes:** a changed or new `.py`, the config's included, is served a second or so after you save it (see Apps, Loading); static files at once, and a new site's folder or a site's new `public/`, `controllers/` or `views/` too. Code that doesn't load is reported in `journalctl -u cypache`, and the previous code keeps serving. After changing the ports, `systemctl restart cypache`. Check `systemctl status cypache`: a config that fails at a restart is restarted again and again, ever more slowly, and its errors are in the journal too.
- **HTTPS:** once a site answers over plain HTTP, get its certificate (see Certbot). The deploy hook restarts Cypache for a name's first certificate, and from then on the site is served over HTTPS, with plain HTTP redirected to it.
- **The config** is Python, root's (mode 644, or 640 with group `cypache` if it holds secrets), run at every start. Other options go into its `serve()` (see Options), such as `maxBodyBytes` for uploads over 1 MiB. Upgrades keep your changes to it.

On Ubuntu or Debian, from PyPI and this repository's `tools/` and `man/`:

```sh
apt install python3-venv certbot libnghttp2-14
umask 022 # what the cypache user must read
useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin cypache
python3 -m venv --system-site-packages /opt/cypache/venv
/opt/cypache/venv/bin/pip install cypache
install -D -m 755 tools/certbot-deploy-hook /opt/cypache/tools/certbot-deploy-hook
install -m 644 tools/cypache.service /etc/systemd/system/
install -m 644 tools/cypache.logrotate /etc/logrotate.d/cypache
install -D -m 644 tools/config.py /etc/cypache/config.py
install -D -m 644 tools/welcome.html /usr/share/cypache/welcome/index.html
for page in man/*; do install -D -m 644 $page /usr/local/share/man/man${page##*.}/${page##*/}; done
install -d -m 755 /var/www/acme /var/www/sites
echo tls > /etc/modules-load.d/tls.conf && modprobe tls
systemctl enable --now cypache
```

- **Paths:** the unit runs `/opt/cypache/venv/bin/python -I /etc/cypache/config.py`; `systemctl edit cypache` changes that. `-I` keeps Python from importing modules from the config's directory or the working directory, and from reading `PYTHON*` variables.
- **Sites** can live anywhere outside `/home`, given a config that names them; the default one's are in `/var/www/sites`.
  - Code and static files belong to root and are writable by no one else, nor are the directories above them. Apps are imported at every start, and reloaded when they change, so a `.py` (or a `__pycache__/*.pyc`) the server's user could plant would run within a second.
  - A directory an app writes to, such as the one its uploads are saved in, belongs to `cypache` with mode 750, outside the static root (where an uploaded `.html` or `.svg` would be served as the site's own) and outside the code. It has to be listed in `ReadWritePaths=` (`systemctl edit cypache`); everything else is read-only to the server, and `save()` fails with `EROFS` there.
- **The unit** (`tools/cypache.service`):
  - runs as `cypache`, with no privilege but binding ports 80 and 443 (`CAP_NET_BIND_SERVICE`, and `SocketBindAllow=` for those two ports only, so code the server runs can't take another privileged port), and `NoNewPrivileges`. A config with other ports changes `SocketBindAllow=`;
  - sees the system read-only and no home directories, except `/var/log/cypache` (mode 750) and its own private `/tmp`, `/var/tmp` (where spool files go) and `/dev/shm`. So a compromised handler can't change the code, the config, certificates or static files;
  - can't see other processes, devices, or kernel tunables, modules and logs, nor set the clock or the hostname;
  - has IPv4, IPv6 and Unix sockets only, and the system calls of `@system-service` without privileged ones, io_uring, keyrings, reading or writing other processes' memory (`process_vm_readv`, `process_vm_writev`), and changing priorities, scheduling or resource limits (`os.nice()` fails with `EPERM`);
  - can't make memory writable and executable (`MemoryDenyWriteExecute`). Python and Cypache don't need that; a library with a JIT, or ctypes callbacks, may, and then needs the line removed;
  - lacks `RestrictSUIDSGID=`, which blocks the `openat2()` static files are opened with. A Site's `root` refuses to start where `openat2()` fails, rather than answer `404`. So code the server runs could leave a setuid or setgid file wherever it can write, which is why those directories are mode 750: other users can't reach it there;
  - reloads certificates on `systemctl reload cypache` (SIGHUP), and stops with SIGTERM: open requests get `shutdownTimeout`, and systemd kills what's left after 20 s (raise `TimeoutStopSec=` with `shutdownTimeout`). The signals go to the main process only (`KillMode=mixed`), the reload launcher, which passes them on to the server once; with `control-group`, the server would get a stop twice, and the second cuts requests off. Processes a handler left behind are killed once the server has stopped;
  - allows 65536 open files (`LimitNOFILE=`), enough for `maxConnections` up to 16368.
- **Not dumpable:** importing `cypache` makes the process non-dumpable (`PR_SET_DUMPABLE`), and so the server, its workers, the reload launcher and the generations it forks, which start non-dumpable. Like any process, the server is dumpable at its start, until the config imports `cypache`, and with reload the launcher once more after it takes the process's place: some tens of milliseconds at each start, but none at reloads. The launcher gets the environment as it was when `cypache` was imported, not what the config added. Processes of the same user, such as a program a handler runs, can't attach to them, nor read their memory, environment or open files (request bodies, spool files) through `/proc`. Root still can: debug or profile them (`gdb`, `py-spy`) as root. There are no core dumps, and the server can't change its own `/proc/self` settings such as `oom_score_adj` (use the unit's `OOMScoreAdjust=`).
- **Logs:** the access log goes to its file; everything else (startup errors, handler tracebacks, stuck handlers) to stderr, which systemd puts in the journal: `journalctl -u cypache`.
- **Site libraries:** the venv sees the system's Python packages, so a site's libraries come from apt, with the system's security updates: `apt install python3-pymysql python3-pil python3-bcrypt python3-magic`. A library Ubuntu doesn't package goes into the venv with `/opt/cypache/venv/bin/python -I -m pip install <name>` (`-I`, or root would run a `pip.py` from the current directory; with `python3-pip` installed), and is then updated by hand.

### Debian package

`tools/build-deb` builds one from a commit (HEAD by default) and the fastepoll wheel the commit's `wheels/SHA256SUMS` names, with the development venv and `dpkg-dev`. It refuses a wheel that doesn't match the checksum, and modules linked with a sanitizer:

```sh
tools/build-deb                                   # prints the dist/cypache_<version>_amd64.deb it built
apt install ./dist/cypache_<version>_amd64.deb    # as root, where it runs
```

- **What it installs:** what the steps above do, the man pages `cypache(8)`, `cypache-config(5)` and `cypache-apps(7)` (from `man/`), with the unit in `/usr/lib/systemd/system`, the `cypache` user from `/usr/lib/sysusers.d/cypache.conf`, and the `tls` module loaded at install and at boot (`/usr/lib/modules-load.d/cypache.conf`).
- **Refusals:** it doesn't install where `/opt`, `/etc/cypache`, `/var/www/acme` or `/var/www/sites` isn't root's or is writable by others, since root writes into them (the deploy hook, certbot) or the server runs the code in them, and warns about such a `/var/www`. A first install refuses an existing `/opt/cypache`: remove a manual install first. A manual install's `/etc/systemd/system/cypache.service` would hide the package's unit, with its hardening, so it's warned about; make changes with `systemctl edit cypache` instead.
- **Starting:** a first install enables and starts the server, like nginx's or Apache's; another server on port 80 keeps it retrying until that port is free. An upgrade restarts it if it's enabled or running. Without `/etc/cypache/config.py` (deleted), nothing starts.
- **Removing** stops and disables the server, also for a later reinstall, and purging deletes `/var/log/cypache`. `/etc/cypache` (the config, the certificate copies) and the `cypache` user stay.
- **Python** is the system's, the development venv's version (`python3.14`), so its security updates come with the system's; a bundled Python's would be ours to ship. A new Python minor version needs a new build.
- **Other distros:** a package runs on the distro it was built on, with its Python, and so do the sites it serves: a site with t-strings needs Python 3.14, so not Debian 13's. `tools/distro-deb debian13 [commit]` runs the commit's tests on Debian 13 (Python 3.13), then builds its package there, into `dist/debian13/`. Debian 13 is unpacked from its Docker Hub image into `~/.cache/cypache-distros` and run with bubblewrap, without root; the build has no network.
- **Versions** are Cypache's, the commit's time (UTC), the commit and the distro built on, such as `0.1.0~dev0+20261028142208.dd61c3e+debian13`, so a later commit's package upgrades an earlier one's, and an older commit built later doesn't. Each distro's package of a commit has its own version, as its contents differ. A new fastepoll wheel comes with a commit of its checksum.

## What the parser refuses

Parsing is strict on purpose, so a proxy in front can't read a request differently from Cypache (request smuggling). Each of these gets an error response and the connection closes:
- anything but CRLF line endings, obs-fold, whitespace before a colon, control characters in a field
- both `Content-Length` and `Transfer-Encoding`, repeated `Content-Length`, a length that isn't plain digits, any transfer coding but `chunked`, chunked framing in HTTP/1.0
- a missing or repeated `Host` in HTTP/1.1, or a malformed one
- targets that aren't origin-form (`/path?query`) or `*` for `OPTIONS`; `#` in a target
- paths with bad escapes, NUL or control characters, `\`, an escaped `/` (`%2F`), invalid UTF-8, or `..` above the root
- `TRACE` and `CONNECT` (`405`), unknown methods (`501`), HTTP versions other than 1.x (`505`)
- more than `maxHeaderBytes` of empty lines before a request, or of chunk extensions in one body

A path spelled in a way a proxy's path rules could read differently gets a `308` redirect to its clean form, with the query kept:
- dot segments and empty segments: `//admin`, `/x/../admin`
- escapes of characters a path never needs escaped (letters, digits, `-._~$&+,;=:@`): `/%61dmin`, `/odata/%24batch`
- lowercase hex: `/caf%c3%a9`

A proxy in front then applies its rules to exactly the path that gets served. Escaping anything else is optional (`[` or `%5B`, `!` or `%21`), since proxies re-encode those each their own way and a redirect could loop. A redirected request with a body gets its redirect right away, and the connection closes without reading the body.

Responses the server generates itself (errors, redirects) carry `Cache-Control: no-store`, so a cache in front can't be made to store an error or redirect a client provoked for everyone else.

Responses always carry `X-Content-Type-Options: nosniff`. A client that pipelines requests without reading the responses stops being read until it catches up.

## Development

Cypache uses a snapshot of the fastepoll wheel in its own venv, never the fastepoll source tree. The wheel itself isn't committed, its checksum is: after replacing it, run `sha256sum fastepoll-*.whl > SHA256SUMS` in `wheels/` and commit that. Building needs `libnghttp2-dev`, and the tests use the `h2` package as an HTTP/2 client:

```sh
python3 -m venv .venv
.venv/bin/pip install wheels/fastepoll-*.whl "Cython>=3.1" setuptools pytest build h2
.venv/bin/python setup.py build_ext --inplace     # hardened development build
.venv/bin/python -m pytest -q tests               # CYPACHE_FUZZ_ITERATIONS=200000 for a longer fuzz run
```

Sanitizer run:

```sh
CYPACHE_BUILD=sanitize .venv/bin/python setup.py build_ext --inplace --force
LD_PRELOAD="$(g++ -print-file-name=libasan.so) $(g++ -print-file-name=libstdc++.so)" ASAN_OPTIONS=detect_leaks=0 .venv/bin/python -m pytest -q tests
```

The source is closed: distribute release wheels only, never an sdist.

- **`tools/release [commit]`** builds the release wheels from a commit (HEAD by default), one per Python from 3.12 to 3.15 (`CYPACHE_BUILD=release`: the Python modules but `__init__` and `__main__` are compiled too, libstdc++ is linked in, `manylinux_2_34`). It checks each with `tools/check-wheel` (no source, stripped, only `PyInit_` exported, glibc 2.34 at most, no library but the system's libnghttp2), installs it with the pinned fastepoll wheel, and runs the commit's tests against it (`CYPACHE_TEST_INSTALLED=1`). The wheels go to `dist/release/`. Pythons other than the system's 3.14 come from fastepoll's `tools/pythons.sh`.
- **`tools/publish fastepoll|cypache|all`** publishes a release: it stages it in `publish/`, shows what goes where, and once you answer y uploads the wheels to PyPI (fastepoll's first, as Cypache depends on it; an upload can't be undone), tags the release commit and pushes the tag, and pushes the public repository github.com/Source61/<name>, tagged too. For fastepoll it takes the wheel fastepoll's `tools/release.sh` built; for Cypache it runs `tools/release` for HEAD. A release is of HEAD in a tree without uncommitted changes, with a final version PyPI doesn't have yet. The public repositories get the README and LICENSE, and Cypache's also the man pages and the files from `tools/` the Debian package installs, for installs by hand; no source. Once: `.venv/bin/pip install twine`, and make github.com/Source61/cypache on github.com, empty.

## Benchmarks

`tools/benchmark.py` runs Cypache and nginx 1.28 (`sendfile` on, access log off) with 4 workers each on CPUs 0-3, and the client with 4 threads and 100 requests at a time on CPUs 4-7, on an Intel Core Ultra 9 275HX capped at 50% CPU use. The cap holds the absolute numbers down; it applies to both servers and the client alike, so the comparison holds. Plain HTTP is measured with wrk (100 keep-alive connections), HTTPS with h2load (HTTP/1.1 on 100 connections, or HTTP/2 with 4 streams on each of 25). The kernel's `tls` module wasn't loaded, so both encrypted in userspace.

| 10 s per run, 2026-10-28 | requests/s | latency |
| --- | ---: | ---: |
| Python handler, thread pool: cypache, HTTP/1.1 | 225,239 | 1.14ms p99 |
| Python handler, thread pool: cypache, access log, HTTP/1.1 | 209,976 | 1.32ms p99 |
| Python handler, thread pool: cypache, HTTPS, HTTP/1.1 | 189,990 | 533us mean |
| Python handler, thread pool: cypache, HTTPS, HTTP/2 | 159,148 | 669us mean |
| Python handler, inline: cypache, HTTP/1.1 | 732,829 | 430.00us p99 |
| Python handler, inline: cypache, HTTPS, HTTP/1.1 | 489,060 | 204us mean |
| Python handler, inline: cypache, HTTPS, HTTP/2 | 536,551 | 187us mean |
| Apps walk: auto.py, page.py, two views: cypache, HTTP/1.1 | 143,272 | 1.69ms p99 |
| Apps walk: auto.py, page.py, two views: cypache, HTTPS, HTTP/2 | 105,354 | 958us mean |
| Fixed response: nginx, HTTP/1.1 | 835,579 | 427.00us p99 |
| Fixed response: nginx, HTTPS, HTTP/1.1 | 548,099 | 180us mean |
| Fixed response: nginx, HTTPS, HTTP/2 | 673,359 | 127us mean |
| Static file, 1 KiB: cypache, HTTP/1.1 | 470,004 | 585.00us p99 |
| Static file, 1 KiB: nginx, HTTP/1.1 | 535,351 | 293.00us p99 |
| Static file, 1 KiB: cypache, HTTPS, HTTP/1.1 | 334,558 | 301us mean |
| Static file, 1 KiB: nginx, HTTPS, HTTP/1.1 | 410,915 | 244us mean |
| Static file, 1 KiB: cypache, HTTPS, HTTP/2 | 319,749 | 325us mean |
| Static file, 1 KiB: nginx, HTTPS, HTTP/2 | 458,864 | 219us mean |
| Static file, 1 MiB: cypache, HTTP/1.1 | 23,564 | 4.43ms p99 |
| Static file, 1 MiB: nginx, HTTP/1.1 | 21,892 | 4.63ms p99 |
| Static file, 1 MiB: cypache, HTTPS, HTTP/2 | 4,219 | 22.41ms mean |
| Static file, 1 MiB: nginx, HTTPS, HTTP/2 | 5,403 | 17.85ms mean |

Repeated runs differ by about 5%, and nginx's p99 latencies by much more.

- **Inline handlers** (`@cypache.inline`) run on the event loop, with Python's overhead only: about 90% of nginx answering from its config over HTTP, 80% over HTTP/2.
- **The thread pool** costs two thread handoffs per request; it's what lets a handler wait on a database without holding up the worker.
- **Small files** reach about 90% of nginx over HTTP, 80% over HTTPS, and 70% over HTTP/2: each request opens (`openat2`) and checks its file, with no cache of open files, and an HTTP/2 stream also goes through the HTTP/1 parser and back.
- **Large files** go out with `sendfile`, as fast as nginx over HTTP; over HTTP/2 without kernel TLS, at about 80% of nginx.
- **The access log** costs a write per request: 1-7% here, with the log on tmpfs.
- **With kernel TLS** (the `tls` module loaded, which fastepoll then uses on Linux 6.14+; nginx doesn't without `ssl_conf_command Options KTLS`), a later HTTPS run gave 1 MiB files over HTTP/2 about 8% more (4,558/s), but small responses less: the inline handler 462k/s over HTTP/1.1 and 401k/s over HTTP/2 (from 489k and 537k), and 1 KiB files 323k and 310k.

## Roadmap

1. HTTP/1.1 core: strict parser, keep-alive, pipelining, limits, timeouts (done, reviewed)
2. Static files: `openat2(RESOLVE_BENEATH)`, zero-copy `sendfile`, MIME types, conditional and range requests, precompressed files (done, reviewed)
3. HTTPS: fastepoll TLS with SNI per Site, HTTP-to-HTTPS redirects, HSTS, certbot webroot challenges and reload on SIGHUP (done, reviewed)
4. Python apps: file-based routing with `auto.py`/`index.py`, views, a per-worker thread pool (done, reviewed); multipart uploads, with large request bodies spooled to disk (done, reviewed)
5. Production: hardened systemd unit, access log, benchmarks; workers not dumpable (`PR_SET_DUMPABLE`), so other processes of the same user can't reach their files through `/proc`; a minimum upload rate instead of one deadline for a whole body (done, reviewed)
6. HTTP/2 over TLS, with nghttp2: each stream handled as an HTTP/1.1 request (done, reviewed)
7. Debian package: the Production setup, on the system's Python, with fastepoll pinned by checksum (done, reviewed)
8. Reload: a changed or new `.py` restarts the server once it loads, and the previous code keeps serving if it doesn't (done)
