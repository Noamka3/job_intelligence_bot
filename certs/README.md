# certs/

Extra root CA certificates the Docker build and the containers need to
trust. The Dockerfile copies this whole directory into the image's trust
store and runs `update-ca-certificates`; an empty directory is a harmless
no-op, so none of this is needed on a machine without a TLS-inspecting
antivirus.

**Why this exists on this dev machine**: Avast does TLS inspection with
its own root certificate. Docker Desktop's container traffic goes through
the same intercepted network, so without that root in the image, every
HTTPS call the crawler makes fails with `CERTIFICATE_VERIFY_FAILED`, and
the browser fallback fails with `ERR_CERT_AUTHORITY_INVALID`.

## The certificate expires or gets replaced — this will happen again

Avast rotates this root. When it does, the copy baked into the image
stops matching and **every crawl fails silently**: the dashboard keeps
working, the scheduler keeps ticking, sources keep getting "crawled", and
nothing is ever found. It looks exactly like "no new jobs today".

Seen on 2026-09-24: 151 crawls failed in 8 minutes, 0 jobs found, while
the same URLs loaded fine in the browser on the host.

### How to tell this is what happened

Either of these:

```powershell
# 1. Are crawls failing, and with what?
docker compose exec -T postgres psql -U job_bot -d job_bot -c "select status, count(*) from crawl_runs where started_at > now() - interval '15 minutes' group by 1"
docker compose exec -T postgres psql -U job_bot -d job_bot -c "select left(error_type,24) as err, count(*), left(max(error_message),80) as sample from crawl_runs where started_at > now() - interval '15 minutes' and status='FAILED' group by 1 order by 2 desc"
```

`CERTIFICATE_VERIFY_FAILED` or `ERR_CERT_AUTHORITY_INVALID` in the sample
means the certificate is stale. (The dashboard's status page shows the
same thing as a pile of failing sources.)

```powershell
# 2. Does the certificate in this folder still match the one Windows trusts?
$stored = (Get-Content certs\avast-root.crt -Raw) -replace "\s",""
$live = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -match 'Avast' } | Select-Object -First 1
$livePem = ("-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($live.RawData,'InsertLineBreaks') + "`n-----END CERTIFICATE-----") -replace "\s",""
if ($stored -eq $livePem) { "certificate is current" } else { "STALE - refresh it (below)" }
```

### How to fix it

Run this from the project root. It exports whatever Avast root Windows
currently trusts, writes it here, and rebuilds the images.

```powershell
cd <path-to>\Bot_career

# 1. Export the certificate Windows trusts right now
$live = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -match 'Avast' } | Select-Object -First 1
$pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($live.RawData,'InsertLineBreaks') + "`n-----END CERTIFICATE-----`n"
[System.IO.File]::WriteAllText("$PWD\certs\avast-root.crt", $pem, [System.Text.UTF8Encoding]::new($false))

# 2. Rebuild with it (a few minutes - it re-downloads Chromium)
docker compose build worker beat
docker compose up -d worker beat

# 3. The failed crawls pushed every source into exponential backoff.
#    Clear it, or nothing will be crawled for hours.
docker compose exec -T postgres psql -U job_bot -d job_bot -c "update career_sources s set next_check_at = null, consecutive_failures = 0 from companies c where c.id = s.company_id and s.enabled and c.enabled and s.source_type <> 'UNSUPPORTED'"
```

Then wait for the next dispatcher tick (up to 5 minutes) plus ~5 minutes
for a full pass, and check that jobs are being found:

```powershell
docker compose exec -T postgres psql -U job_bot -d job_bot -c "select status, count(*), coalesce(sum(jobs_created),0) as new_jobs from crawl_runs where started_at > now() - interval '15 minutes' group by 1"
```

`SUCCESS` with a non-zero `new_jobs` means it is working again.

### Verifying the containers can reach the internet

If the certificate is current and crawls still fail, check the layers
separately from inside the worker — DNS, raw TCP, TLS, then HTTP:

```powershell
docker compose exec -T worker python -c "import socket,ssl,httpx; socket.create_connection(('1.1.1.1',443),5).close(); print('tcp ok'); ssl.create_default_context().wrap_socket(socket.create_connection(('boards-api.greenhouse.io',443),5), server_hostname='boards-api.greenhouse.io').close(); print('tls ok'); print('http', httpx.get('https://boards-api.greenhouse.io/v1/boards/torq/jobs', timeout=15).status_code)"
```

TLS failing while TCP succeeds is always the certificate.

## Notes

- `certs/*.crt` and `*.pem` are gitignored: the certificate is
  machine-specific and there is no reason to commit it.
- The image also sets `NODE_EXTRA_CA_CERTS` (Playwright's Node downloader
  has its own bundle) and registers the certificate in Chromium's NSS
  store for the non-root user, because the browser fallback does not use
  the system bundle. Both are in the Dockerfile; refreshing the `.crt`
  and rebuilding covers all three.
- On a machine without a TLS-inspecting antivirus, leave this directory
  empty and ignore all of the above.
