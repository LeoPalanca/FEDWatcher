# FakeFed Deployment

FakeFed is a static, synthetic copy of the Federal Reserve pages needed by the
FedWatcher scraper. It is designed for `https://fakefed.ellep.it` and should not
be presented as real Federal Reserve content.

## VM Layout

Use the same VM for both project domains:

- `fakefed.ellep.it`: Nginx static site from `/var/www/fakefed`.
- `fedwatcher.ellep.it`: future Nginx reverse proxy to the FastAPI/dashboard services.

Local-only VM credentials should be kept in `/Users/leonardo/FEDWatcher_Hide/.env`.
Do not commit VM passwords, SSH keys, Cloudflare tokens, or generated environment files.

## Cloudflare

Create DNS records pointing to the VM public IP:

```text
A fakefed    <VM_PUBLIC_IP>
A fedwatcher <VM_PUBLIC_IP>
```

Start with DNS-only mode until Nginx responds correctly. Enable the Cloudflare proxy after
the direct HTTP/HTTPS smoke test works.

## Nginx

Copy `deploy/nginx/fakefed.ellep.it.conf` to the VM:

```bash
sudo cp deploy/nginx/fakefed.ellep.it.conf /etc/nginx/sites-available/fakefed.ellep.it
sudo ln -s /etc/nginx/sites-available/fakefed.ellep.it /etc/nginx/sites-enabled/fakefed.ellep.it
sudo nginx -t
sudo systemctl reload nginx
```

For TLS after DNS is active:

```bash
sudo certbot --nginx -d fakefed.ellep.it --non-interactive --agree-tos \
  --register-unsafely-without-email --redirect
```

Certbot installs the certificate into the Nginx site and sets up automatic renewal.

## Deploy Static Files

On the VM:

```bash
git clone https://github.com/LeoPalanca/FEDWatcher.git
sudo mkdir -p /var/www/fakefed
sudo rsync -a --delete FEDWatcher/fakefed/ /var/www/fakefed/
sudo nginx -t
sudo systemctl reload nginx
```

For later updates:

```bash
cd FEDWatcher
git pull
sudo rsync -a --delete fakefed/ /var/www/fakefed/
sudo systemctl reload nginx
```

## Smoke Test

```bash
curl -I http://fakefed.ellep.it/monetarypolicy/fomccalendars.htm
curl -I http://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm
```

Then run FedWatcher against FakeFed:

```bash
FED_BASE_URL=https://fakefed.ellep.it python agents/monitor.py
FED_BASE_URL=https://fakefed.ellep.it python scripts/fetch_document_text.py
```

The fake statement text should be stored in the documents table.

## Future Admin Writing

The current FakeFed deployment is static and Git-deployed. For the educational dashboard,
add a protected admin workflow that can create or update synthetic statement HTML, then
sync or write it into the FakeFed static directory. Keep this separate from the clean
public app mode, which should use the official Federal Reserve source.
