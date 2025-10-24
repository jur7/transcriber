Prerequisites:
- A domain name pointing to your server's public IP (A/AAAA records).
- Ports 80 and 443 open to the internet.
- Docker Desktop / Docker Engine + Docker Compose v2.

Files added:
- `nginx/conf.d/transcriber.conf` – Nginx vhost. Replace `yourdomain.com` with your real domain in both the `server_name` and the `ssl_certificate` paths.
- `docker-compose.yml` – Adds `nginx` and a `certbot` helper service; removes host port exposure from `transcriber` in favor of reverse proxy.

Steps:
1) Build and start app + Nginx (HTTP only initially):
   - `docker compose up -d --build`

2) Obtain the initial certificate (replace domain/email):
   - `docker compose run --rm certbot certonly --webroot -w /var/www/certbot -d yourdomain.com --email you@example.com --agree-tos --no-eff-email`

3) Reload Nginx to pick up the cert:
   - `docker compose exec nginx nginx -s reload`

4) Visit `https://yourdomain.com`.

Renewal:
- Certificates expire every ~90 days. Renew and reload Nginx periodically:
  - `docker compose run --rm certbot renew`
  - `docker compose exec nginx nginx -s reload`
- Automate with a scheduled task (cron or Windows Task Scheduler) to run the two commands daily.

Notes:
- Large uploads are supported (Nginx `client_max_body_size 200m`). Adjust as needed.
- Static resources from `app/static` and user uploads in `uploads` can be served directly by Nginx.
- Flask is configured to trust proxy headers and prefer HTTPS URLs, and Gunicorn is set to allow forwarded headers.
