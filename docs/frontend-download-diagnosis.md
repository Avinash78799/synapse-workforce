# Frontend download investigation — 20 September 2026

## Finding and limits

The earlier attempts were blocked during dependency/image acquisition, before Next.js compilation. Logs show severe download slowness, not a compiler failure. A later bounded npm install succeeded, and the full Next.js production build then passed, including TypeScript, static page generation and page optimization.

The evidence supports a transient network/download-throughput problem as the likely cause. It does **not** identify an ISP, proxy, CDN or registry outage, and the original attempts were manually stopped before a terminal timeout error. Do not report a confirmed registry timeout or disk fault. No DNS/proxy/TLS configuration was changed to obtain the successful build.

## Evidence

| Observation | Measured result / interpretation |
| --- | --- |
| Earlier Docker Node layer | 10.49 MB of 55.68 MB after 443.7 seconds, roughly 24 KB/s; slow progress rather than a completed build failure |
| Earlier npm install | Successful HTTP 200 transfers took about 19–103 seconds for several packages; no terminal timeout or ENOSPC recorded before cancellation |
| Current host disk check | Approximately 77.9 GB free on C:; no evidence of host disk exhaustion |
| Current Docker filesystem check | About 1.00 TB logically available in the guest filesystem; this is sparse virtual capacity, not additional physical host space |
| Fresh registry metadata probe | HTTP 200; DNS 0.055s, TLS completed at 0.589s, total 0.989s |
| Fresh locked Next.js tarball probe | HTTP 206; first 1 MiB in 1.541s, about 681 KB/s, no DNS/TLS error |
| npm default settings | 300,000ms request timeout, two retries, 10–60s retry delays; this can make slow requests appear silent for several minutes |
| Controlled retry | Locked `npm ci` succeeded: 47 packages; uncached Next.js and Windows SWC transfers took 77.4s and 75.0s. Existing cache also helped, so the improvement cannot be attributed solely to changed settings. |
| Production build | Next.js 16.3.5 compiled in 21.8s, TypeScript finished in 6.4s, static generation and final optimization completed; command exit 0 |

## Reproducible approach if it recurs

Before a build retry, check host free space, Docker health, and a bounded download of the actual locked tarball. On this Windows machine:

```powershell
Get-PSDrive C
curl.exe --connect-timeout 10 --max-time 45 --range 0-1048575 --output NUL --write-out 'code=%{http_code} total=%{time_total} bytes=%{size_download} speed=%{speed_download}\n' https://registry.npmjs.org/next/-/next-16.3.5.tgz
```

Use the URL from the current lockfile after an intentional dependency change. Record the HTTP status, timings and bytes, rather than repeatedly restarting a whole Docker build. A small range probe does not prove sustained throughput for every image layer.

The successful install used the following per-invocation settings from `apps/web`; no global npm settings were changed:

```powershell
npm.cmd ci --no-audit --no-fund --fetch-timeout=60000 --fetch-retries=1 --fetch-retry-mintimeout=1000 --fetch-retry-maxtimeout=5000 --maxsockets=4 --loglevel=http
if ($LASTEXITCODE -eq 0) { npm.cmd run build }
```

Timeouts apply to requests; they are not a total wall-clock cap for the whole install. Preserve the install log and build log separately. If a request now fails, use its concrete error and timing to distinguish DNS, TLS, HTTP status, timeout or disk failure. Keep TLS certificate checks enabled and retain lockfile integrity checks. Do not change package versions or clear a healthy cache to treat download slowness.

The initial successful build used local Windows Node. Follow-up on 21 September: Docker API and frontend images built successfully, and the normal local stack was upgraded. The frontend compiled in Docker in 13.6s; TypeScript, static generation and image export completed. Cached dependencies were reused, so this is not evidence of a fresh uncached download speed. Eleven certificate-validated HTTPS deployment checks passed. Browser interaction remains pending because the local self-signed certificate requires user handling in the browser. Build success does not change the [sandbox-only client readiness statement](client-readiness.md).
