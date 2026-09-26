# Runtime AI analysis

WORKAI supports OpenAI Responses API structured outputs for source-cited requirements review and CV fact ranking. Configure `OPENAI_API_KEY` and `WORKAI_OPENAI_MODEL` in the ignored local `.env` or process environment; an optional `OPENAI_PROJECT_ID` selects the API project. No model or paid request is selected automatically. `workai doctor` reports configuration presence without displaying secrets.

```bash
workai ai-analyze JOB_ID
```

The autonomous pipeline invokes the configured provider after deterministic eligibility passes. It receives the job description and a catalog of existing professional facts, without contact information or legal/salary facts. Its output contains exact job-description quotes and IDs of existing candidate skills, projects and bullets. It cannot supply new CV wording, factual claims, URLs, tools, or submission actions. Validated rankings reorder the original text and preserve every fact and source citation.

Local checks reject quotes not present in the job description, unsupported skill references, unknown/duplicate/wrong-category fact IDs, extra properties, incomplete responses, refusals and malformed JSON. Mandatory concerns flagged by the model remain unresolved until supported by factual evidence; AI cannot override deterministic exclusions or authorize a submission. A valid JSON response is not proof of semantic correctness, so the model's classifications and limitations are recorded alongside the source.

Requests use strict JSON schema and `store: false`; this controls Responses storage and does not claim that it overrides every provider retention policy. Responses IDs, model names, token usage, source references and immutable validated results are stored privately in `data/ai-reviews/`. Results are reused only for the same model, source description and professional fact catalog. API/credential failures are explicit; no partial model output enters a CV.

The implementation contract was checked against [official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs) and [the Responses create API reference](https://developers.openai.com/api/reference/resources/responses/methods/create). API-shape and negative-case tests use a local HTTP transport fixture. A real model invocation is still pending runtime credentials; mocked transport tests do not establish live model access or analysis quality.
