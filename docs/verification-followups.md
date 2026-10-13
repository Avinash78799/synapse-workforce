# Verification follow-ups

Owner: platform maintainer (Core platform implementation and verification).
Baseline recorded: 20 September 2026. These are open items, not accepted permanently.

| ID | Status and trigger | Required work / closure evidence |
| --- | --- | --- |
| DEP-001 | OPEN; review before any FastAPI, Starlette, HTTPX or AnyIO bump | Starlette TestClient warns that its HTTPX integration is deprecated in favor of HTTPX2. Evaluate a supported test transport with the pinned framework; HTTPX is also used by runtime providers, so do not blindly replace it globally. Close with transport/import compatibility and the full native suite passing without this warning. |
| DEP-002 | OPEN; same dependency-bump gate | Starlette TestClient references the deprecated `anyio.abc.BlockingPortal` alias. Qualify an upstream Starlette version using `anyio.from_thread.BlockingPortal`. Do not patch site-packages or silence every deprecation. Close after the warning is absent and native concurrency/lifespan tests pass. |

The reviewed combination is FastAPI 0.141.1, Starlette 1.6.0, HTTPX 0.28.1 and AnyIO 4.15.1. `tests/conftest.py` stops pytest before collection if that combination changes, directing the maintainer here. This is a compatibility review gate, not a recommendation to retain old dependencies indefinitely. A dependency/security update must resolve or explicitly re-evaluate these items, update the lockfiles and baseline together, and record the outcome here.

`pytest.ini` treats deprecations as errors except for the two exact known messages at their observed modules. Those two remain visible in every applicable test run. New or changed deprecations fail verification. When an item closes, remove its exception and update the reviewed version baseline; do not widen the filters to make a bump pass.

## Frontend download investigation

See [frontend-download-diagnosis.md](frontend-download-diagnosis.md) for historical logs, bounded probes, retry settings and the remaining uncertainty. A package download stall is not a Next.js compiler result. Keep install, build and browser-test outcomes separate.

## Client communication

[Client readiness](client-readiness.md) supplies the canonical prospective-client wording and the evidence required to promote a specific vendor from unverified. Include its sandbox qualification wherever this milestone's test results are summarized.

21 September regression: 185 passed; DEP-001 and DEP-002 remain open and visible. Docker build and local HTTPS deployment checks passed; see [deployment evidence](deployment-verification-20260921.md).
