# Cypache

A fast, security-first HTTP server for Python on Linux, written in Cython (C++ mode) on top of fastepoll. Python handlers run natively in the server's worker processes; there is no CGI or FastCGI hop. It serves static files and Python apps over HTTP/1.1 and, with TLS, HTTP/2, and is strict by design: a request that a proxy in front could read differently is refused, static files are confined to their root by the kernel, and every limit and timeout has a safe default.

## Install

A server, on Ubuntu or Debian, as root: Cypache as a systemd service on ports 80 and 443, serving each folder in `/var/www/sites` as a site named after its host:

```sh
git clone https://github.com/Source61/cypache && cypache/tools/install
```

Run `tools/install` again after `git pull` in the clone to upgrade.

On its own, as a library: `pip install cypache` in a virtual environment, which installs fastepoll too. The wheels are for CPython 3.12 to 3.15 on Linux x86-64 with glibc 2.34 or newer, OpenSSL 3 and libnghttp2 1.60 or newer (`libnghttp2-14`), which stay the system's, so their security updates apply.

## Quick start

On a server from `tools/install`, a site is a folder, and each `.py` in its `controllers/` answers the path it's named after:

```sh
mkdir -p /var/www/sites/example.com/public /var/www/sites/example.com/controllers
echo "<h1>Hello</h1>" > /var/www/sites/example.com/public/index.html
printf 'def get(request):\n  return "<h1>News</h1>"\n' > /var/www/sites/example.com/controllers/news.py
```

`http://example.com/` is the static page and `/news` the handler, served a second or so after saving. For HTTPS, get a certificate with certbot (`man cypache`).

As a library, a config is a Python script that builds its sites and calls `serve()`:

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

Run it as an unprivileged user: handlers run with the server's privileges. The config runs as code with them too, so it must not be writable by the server's user.

## Documentation

The man pages are the reference. `tools/install` installs them; in a clone, read them with `man -l man/<page>`.

- `man cypache`: running it as a service: sites in `/var/www/sites`, HTTPS with certbot, upgrading, signals, security, logs and files.
- `man cypache-config`: the config: `Site` and `serve()` with every option, timeouts, HTTPS and HTTP/2, static files, reloading, the access log, and what requests are refused.
- `man cypache-apps`: a site's Python: handlers, the request, file-based apps and views, threads, uploads.

## License

See `LICENSE.txt`.
