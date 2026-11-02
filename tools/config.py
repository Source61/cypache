"""Cypache's config: Python, run as the cypache user at every start. Changes to it and to the sites' Python load by
themselves, a second or so after saving, and so do new sites and their new folders (watch=).

Each folder in /var/www/sites is a site, named after its host:
  /var/www/sites/example.com/public/        static files, served as they are
  /var/www/sites/example.com/controllers/   Python: controllers/news.py answers /news (cypache-apps(7))
  /var/www/sites/example.com/views/         the controllers' views
  /var/www/sites/example.com/example/       the site's own package, for its controllers to import
It answers for example.com and www.example.com. A folder named default, or a symlink to a site's, answers for any other
name; until there's a site at all, a page says Cypache works. The sites share one import path, so a site's package (or
module) needs a name of its own: one that Python or an installed package already has stops the config, and one that
another site has too (each site's serve.py, say) fails when imported.

HTTPS: once a site answers over HTTP, get its certificate, and it's served over HTTPS (cypache(8), HTTPS):
  certbot certonly --webroot -w /var/www/acme -d example.com -d www.example.com --deploy-hook /opt/cypache/tools/certbot-deploy-hook

Errors: systemctl status cypache, journalctl -u cypache. Logs: /var/log/cypache/access.log.
"""
import importlib.util, os, pkgutil, sys
import cypache

SITES = "/var/www/sites"
TLS = "/etc/cypache/tls" # where the certbot deploy hook puts certificates


def folder(path):
  return path if os.path.isdir(path) else None


class Shared:
  """Refuses to import a module more than one site has, rather than give a site another's"""
  def find_spec(self, name, path=None, target=None):
    if len(owners.get(name, ())) > 1:
      raise ImportError(f"{' and '.join(owners[name])} each have {name!r}; give it a name of its own")


sites, folders, owners = [], [SITES], {}
for name in sorted(os.listdir(SITES)):
  path = f"{SITES}/{name}"
  if name.startswith(".") or name == "lost+found" or not os.path.isdir(path): # lost+found: /var/www/sites as a filesystem of its own
    continue
  for module in pkgutil.iter_modules([path]):
    found = None if module.name in owners else importlib.util.find_spec(module.name)
    if found is not None: # Python's or an installed one, which an import of it would get instead
      raise ValueError(f"{path}/{module.name}: Python imports {module.name!r} from {found.origin or ', '.join(found.submodule_search_locations)} already; give it a name of its own")
    owners.setdefault(module.name, []).append(path)
  sys.path.append(path)
  folders.append(path)
  dirs = {"root": folder(f"{path}/public"), "apps": folder(f"{path}/controllers"), "views": folder(f"{path}/views")}
  if name == "default":
    sites.append(cypache.Site(default=True, **dirs))
  else:
    hosts = (name,) if name.startswith(("www.", "[")) else (name, "www." + name)
    sites.append(cypache.Site(*hosts, tls=folder(f"{TLS}/{name}"), **dirs))
if not sites:
  sites.append(cypache.Site(root="/usr/share/cypache/welcome", default=True))
sys.meta_path.insert(0, Shared())

# Other options, such as maxBodyBytes for uploads over 1 MiB: cypache-config(5), OPTIONS
cypache.serve(sites, http=80, https=443 if any(site.tls for site in sites) else None, acme="/var/www/acme", accessLog="/var/log/cypache/access.log", watch=folders)
