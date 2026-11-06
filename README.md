# Cypache

A fast, security-first HTTP server for Python on Linux, written in Cython (C++ mode) on top of fastepoll. Python handlers run natively in the server's worker processes; there is no CGI or FastCGI hop. It serves static files and Python apps over HTTP/1.1 and, with TLS, HTTP/2, and is strict by design: a request that a proxy in front could read differently is refused, static files are confined to their root by the kernel, and every limit and timeout has a safe default.

## Install

A server, on Debian or Ubuntu, as root: Cypache as a systemd service on ports 80 and 443, serving each folder in `/var/www/sites` as a site named after its host:

```sh
git clone https://github.com/Source61/cypache && cypache/tools/install
```

It installs Cypache itself, from PyPI, into `/opt/cypache/venv`: there's nothing to `pip install` first. The sites run on the system's Python, which on Debian 13 is 3.13, so without t-strings (Python 3.14). Run `tools/install` again after `git pull` in the clone to upgrade.

On its own, as a library: `pip install cypache` in a virtual environment (`apt install python3-venv` on Debian and Ubuntu), which installs fastepoll too. The wheels are for CPython 3.12 to 3.15 on Linux x86-64 with glibc 2.34 or newer, OpenSSL 3 and libnghttp2 1.60 or newer (`libnghttp2-14`), which stay the system's, so their security updates apply.

## Quick start

On a server from `tools/install`, a site is a folder, and each `.py` in its `controllers/` answers the path it's named after:

```sh
mkdir -p /var/www/sites/example.com/public /var/www/sites/example.com/controllers
echo "<h1>Hello</h1>" > /var/www/sites/example.com/public/index.html
printf 'def get(request):\n  return "<h1>News</h1>"\n' > /var/www/sites/example.com/controllers/news.py
```

`http://example.com/` is the static page and `/news` the handler, served a second or so after saving. Code that doesn't load is reported in `journalctl -u cypache`, and the previous code goes on serving. At a start (`systemctl restart cypache`, a reboot) there is none, so a site whose code doesn't load stops the server, for every site, until it's fixed.

For HTTPS, get each site a certificate of its own once it answers over HTTP. It's served over HTTPS from then on, with plain HTTP redirected there:

```sh
certbot certonly --webroot -w /var/www/acme -d example.com -d www.example.com --deploy-hook /opt/cypache/tools/certbot-deploy-hook
```

Certbot names a certificate after its first `-d`, and it goes to the site of that name alone: one certificate for two sites leaves the second without HTTPS. `www.example.com` is the same site as `example.com`, unless it has a folder of its own, such as one that redirects to `example.com` (`man cypache`, SITES).

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
