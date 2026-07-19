# Azure AI Foundry (Microsoft Foundry) provider

OneManCompany supports Azure AI Foundry / Azure OpenAI models as a first-class
provider. Foundry exposes an **OpenAI-compatible v1 API**, so the same
`ChatOpenAI` client used for every other OpenAI-style provider works against it.

## Onboarding wizard

Run `onemancompany-init` and pick **Azure AI Foundry** at the provider step.
You'll be asked for three things:

1. **Resource name or endpoint URL** — either a bare resource name (`myres`) or a
   full endpoint. A bare name is expanded to
   `https://<resource>.services.ai.azure.com/openai/v1/`. A pasted URL is
   normalized to the `/openai/v1/` form (both `*.services.ai.azure.com` and
   `*.openai.azure.com` hosts work).
2. **API key** — your Azure resource key. It is sent as `Authorization: Bearer`.
3. **Deployment name** — this becomes the model id. **Azure uses the deployment
   name, not the base model id** (e.g. your deployment of `gpt-5.5` might be named
   `gpt-5-5-prod`).

## Manual `.onemancompany/.env`

You can also configure it by hand:

```env
DEFAULT_API_PROVIDER=azure
AZURE_API_KEY=<azure resource key>
DEFAULT_API_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1/
DEFAULT_LLM_MODEL=<deployment name>   # Azure deployment name, not the base model id
```

Notes:

- The endpoint **must** end with `/openai/v1/` — that is the OpenAI-compatible
  surface. Both `https://<resource>.services.ai.azure.com/openai/v1/` and
  `https://<resource>.openai.azure.com/openai/v1/` are accepted.
- No `CUSTOM_CHAT_CLASS` is needed — Azure uses the OpenAI chat format by default.
- Per-employee model tiering works: set a different deployment name (and optional
  `api_base_url`) on each employee's `profile.yaml`.

## `--auto` (non-interactive init)

`onemancompany-init --auto` reads an existing `.onemancompany/.env` and
regenerates the company config from it. It preserves `DEFAULT_API_BASE_URL` and
`CUSTOM_CHAT_CLASS`, so the Azure endpoint survives a re-init.

## Relationship to the `custom` provider

Before first-class Azure support, Foundry worked through the generic `custom`
provider (`DEFAULT_API_PROVIDER=custom` + `CUSTOM_CHAT_CLASS=openai` +
`DEFAULT_API_BASE_URL=...`). That still works; the `azure` provider just removes
the guesswork and gives you a labelled onboarding path.
