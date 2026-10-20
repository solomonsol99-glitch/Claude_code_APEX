# Engine patches

Coreset modifications to MiroFish, applied in filename order at image build time
(`patch -p1` from the upstream repo root). Everything here is AGPL-3.0 and must be
published with the engine source (see ../SOURCE_OFFER.md).

None yet. Candidates (blueprint §2.4.4): CORS lock-down, OTel instrumentation, and a
profile-import endpoint if assumption A1 fails. Upstream already serves `GET /health`,
so a `/healthz` patch is not needed.
