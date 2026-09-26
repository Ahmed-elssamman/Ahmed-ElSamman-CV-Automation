# Security

The application runs locally and processes private candidate data. Keep source documents, profile, Answers Bank, SQLite, logs, browser state and application snapshots in ignored runtime directories. Back them up with restricted access; GitHub is not their backup. A private repository still must not contain credentials.

Use `.env` or process environment for secrets; `.env.example` contains placeholders. Configuration must reference session paths, never inline cookies or passwords. Protect the OS account and local configuration. CLI runtime files use a private creation mask. Logs redact recognizable credential patterns and configured secret environment values, but avoid logging raw HTTP bodies, DOM fields, or authentication requests at all.

Job descriptions and websites are untrusted content. They cannot override operational instructions, supply facts about the candidate, select arbitrary local upload files, or alter allowed browser hosts. LaTeX content is escaped and compiled without shell escape. Browser uploads use the exact validated PDF bytes and record SHA-256. Secret and private runtime paths are excluded from Git.

No CAPTCHA, authentication or anti-bot bypass is implemented. Unsupported/challenged forms are recorded as blocked. Any possible submission with missing confirmation requires reconciliation before retrying. Unknown legal/personal/compensation facts must not be guessed.

Run `workai security-audit` against staged/tracked files before commits and pushes. This checks sensitive paths and common credential signatures; also review the staged diff because pattern scanning cannot identify every possible secret. Verify repository privacy using `gh repo view --json visibility,isPrivate`. Do not paste token values into terminal commands or reports.

To respond to exposure: stop affected operations, revoke exposed credentials through their provider, remove affected tracked artifacts without deleting local application evidence, inspect Git history and remote exposure, rotate sessions, document the incident privately, and resume only after fixing the cause. Never publish candidate details in a public issue.
