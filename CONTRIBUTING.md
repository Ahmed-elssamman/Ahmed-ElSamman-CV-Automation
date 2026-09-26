# Contributing

Read workAI.md before changing behavior. It is the persistent operational contract. Update it and the support matrix whenever architecture, adapter coverage, or autonomy changes.

Use the local virtual environment and editable installation. Run focused tests during changes and the full suite before a stable commit. Keep test candidate data in temporary fixtures, never production `data/` or `applications/`. Avoid live submission in automated tests. A browser fixture must exercise actual DOM behavior; a click mock does not prove confirmation handling.

Every candidate fact needs source evidence. Preserve original documents and historical snapshots. Do not improve test scores by adding employer-required skills to the candidate. Add negative tests for unsupported facts, unknown legal answers, salary unit ambiguity, dynamic required fields, duplicates and uncertain outcomes when those paths change.

New discovery adapters must retain real source URLs, full descriptions and restrictions. New browser adapters need a read-only inspection of the actual form, scoped host configuration, verified controls, an unknown-field gate, a durable pre-submit callback and a positive receipt rule. CAPTCHA/authentication is a legitimate block, never an invitation to bypass controls.

Prefer stable main commits; branches are optional. Before pushing run `workai security-audit`, inspect the staged diff, and verify the GitHub repository is private. Candidate data, credentials, cookies, PDFs and browser sessions stay out of Git. Keep dependency versions reproducible in requirements.lock.
