# Browser platform support

The current browser engine supports explicitly audited form adapters. It is validated against a local Chromium application fixture, including text/email, PDF upload, dropdown, radio group, checkbox, multiple steps, known/unknown answers, confirmation evidence and submission ambiguity. That fixture is not a real application, and no job board is claimed to have universal live support.

LinkedIn, Wuzzuf, Indeed, Bayt, GulfTalent, Naukrigulf, Akhtaboot and Forasna require platform-specific inspection, a permitted automation route and appropriate login state before enabling an adapter. Public job discovery support does not imply application support. An unsupported platform returns `BLOCKED_BY_PLATFORM`; other jobs continue. CAPTCHA, visible password/authentication controls, expired sessions and navigation outside the audited hosts also block the adapter. There is no challenge bypass.

Copy `config/platforms.example.yaml` to local `config/platforms.yaml` and audit every control against the actual form. Keep `enabled: false` until its allowed hosts, field semantics, intermediate controls, final submit and positive confirmation marker have been verified. Use accessible labels, roles or stable test IDs. Use CSS only when necessary. The job description cannot provide adapter configuration, executable instructions, arbitrary answers or upload paths. The adapter's field `question` can supply an unambiguous canonical question when a website's label is vague; this mapping must reflect the actual field meaning.

Every required visible form control must have an audited mapping. Required dynamic controls outside the mapping cause `BLOCKED_UNKNOWN_FIELDS`, including unknown controls already populated by a website. Optional unknown questions are stored and left unanswered. A mapped checkbox requires an explicit approved boolean; standard consent is not inferred from a generic candidate fact. Radio options map exact approved answers to exact controls. Unsupported custom controls fail closed until implemented and tested.

The engine loads an optional local Playwright `storage_state` file; it never logs cookies or credentials. Keep it below `data/browser-sessions/` and out of Git. Only the configured navigation hosts are accepted, including hosted form frames. Protect the local configuration file as operator-controlled input. Browser UI changes can invalidate an adapter and require another audit.

```python
from workai.browser import apply_job
result = apply_job(root, job, profile, cv, submit=False)  # fills; never clicks final submit
result = apply_job(root, job, profile, cv, submit=True, before_submit=durable_intent)
```

The caller performs eligibility, QA, duplicate checks and durable workflow claims before invoking the adapter. The optional `before_submit` callback must commit the submission intent before returning. It receives job ID, questions, sourced answers and uploaded PDF path/SHA-256/byte count. A failed callback prevents the submit click. The PDF is uploaded from the exact bytes whose hash was recorded.

A click alone is never success. `SUBMITTED` requires a previously absent configured confirmation marker with matching text, and any configured confirmation URL pattern. The result includes timestamp, marker evidence, URL, optional confirmation ID and upload digest. Post-click transport errors or mismatched/missing confirmation yield `SUBMISSION_UNCONFIRMED` with `retryable: false`; reconcile with the platform before another attempt. Preparation returns `READY_TO_APPLY`, never `SUBMITTED`. Pre-submit transient failures may be retryable, but the orchestrator owns bounded recovery.

Profile synchronization uses `workai.profile_sync.plan_profile_updates` for comparison and `sync_profile(root, platform, profile, write=False)` for audited browser inspection. Set `write=True` only under the user’s standing profile-completion authorization. The adapter’s optional `profile` block supplies `url`, text/select `fields`, `save` and `confirmation` with the same locator syntax. It populates only missing known facts, leaves populated fields unchanged, reports conflicts and unknowns, and saves immutable local snapshots under `data/platform-profiles`. A positive previously absent save marker is required for `UPDATED`; ambiguity returns `SAVE_UNCONFIRMED`. The local fixture verifies comparison, dry-run inspection, missing email completion and preservation of a conflicting existing name. No live profile adapter is enabled until its editor is audited.

## Public form inspection on 2026-09-26

Read-only Chromium inspection of [Bosta’s Junior Software Quality Engineer application](https://jobs.lever.co/Bosta/170452d5-4e53-4ce6-a802-f010770ecacb/apply) confirmed a real Lever form with CV upload, full name, email, phone, current location, current company and links. Required custom questions ask for quality-engineering years, automation-testing years, graduation year, salary expectations and military status; career vision is optional. The page includes hCaptcha integration. Nothing was filled or uploaded and no application was submitted. This QA vacancy is not a suitable frontend target, and no positive submission confirmation was verified; a live Lever adapter remains disabled. These observations prove public-form inspection only, not permission to bypass challenges or successful live application support.


## Scoped salary and legal answers

The approved clarification supplies net monthly salary targets and country-specific legal facts. A salary field must identify currency, pay period and net/gross basis; explicitly requested base or total compensation cannot use an unspecified component. Missing local-currency conversion evidence triggers a current dated public FX lookup, with age/pair validation and source attribution. Gross/net payroll conversion is not implemented and remains UNKNOWN.

Gulf sponsorship is approved in `work_arrangement: relocation` context. Conditional relocation resolves to true only when trusted field context confirms both `visa_and_work_authorization_provided: true` and `reasonable_relocation_support: true`; absent evidence yields UNKNOWN with the known conditions. User facts about remotely working from Egypt do not imply foreign work authorization.

## Additional public inspections

Workable Robusta forms were read through the public vacancy detail and form endpoints loaded by its normal page. The React role required 3–5 frontend years and custom answers about current salary, English proficiency, contract acceptance and React/Next duration; the other full-stack role required 5+ years and PHP/Laravel. Both were incompatible; no fields were entered or documents uploaded. FlairsTech’s normal Apply button opened a registration-required dialog on the shared careers page. Distinct vacancy IDs are retained; a shared page is not treated as a direct application link or a confirmation. These observations do not enable a production adapter.
