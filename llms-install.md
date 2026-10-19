# Install ArtifactSmith in Cline

Use the hosted server first. It needs nothing installed on this machine.

## Hosted

Ask the user for their token. They get it at https://artifactsmith.neuralami.ai/try.

Edit Cline's MCP settings file, `cline_mcp_settings.json`. The Cline CLI stores it at `~/.cline/data/settings/cline_mcp_settings.json`. In the editor, open the Cline panel, click the MCP Servers icon, open the Configure tab, and click Configure MCP Servers.

Add a remote server named `artifactsmith` under `mcpServers`. Leave other servers in the file. Set `type` to `streamableHttp`. If you omit `type`, Cline uses the old SSE transport.

```json
{
  "mcpServers": {
    "artifactsmith": {
      "type": "streamableHttp",
      "url": "https://artifactsmith.neuralami.ai/mcp",
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Replace `<TOKEN>` with the token the user gave you. The header name is `Authorization`. The value starts with `Bearer `.

## Self-hosted

Run ArtifactSmith on this machine. You need Docker Engine with the Compose plugin.

The server reads `AM_LLM_BASE`, `AM_LLM_KEY` or `OPENAI_API_KEY`, and `AM_DEFAULT_MODEL` from `.env`. `AM_LLM_BASE` is the OpenAI-compatible endpoint. `OPENAI_API_KEY` or `AM_LLM_KEY` is the key. `AM_DEFAULT_MODEL` is the model. Change `AM_LLM_BASE` and `AM_DEFAULT_MODEL` only if you are not using the default OpenAI host and model.

Leave the object-store keys empty and run the script below, or set `AM_STORE_KEY` and `AM_STORE_SECRET` yourself.

From the repo root:

```bash
cp .env.example .env
./scripts/ensure-local-env.sh
docker compose up -d --build
docker compose exec server artifactsmith token add --name agent --workspace alpha
```

`artifactsmith token add` prints the token once. Use that value as `<TOKEN>`.

Add the same remote server to `cline_mcp_settings.json`. Point it at this machine.

```json
{
  "mcpServers": {
    "artifactsmith": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8780/mcp",
      "headers": {
        "Authorization": "Bearer <TOKEN>"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Run `docker compose up -d` again after a change to `.env`.

## Verify

Call the `status` tool. A reply that `artifact_id` is required means the server answered.

Then create a tiny web page with `create`. Pass `display_name`, `verbatim_request`, and `source_content`.

```json
{
  "display_name": "Install check",
  "verbatim_request": "One short page that says the install check passed.",
  "source_content": "The install check passed."
}
```

Call `status` with the returned `job_id` and `wait` set to 90. When `status` is `done`, call `share` with the `artifact_id`. Return the share link in `url`.

On a server you run yourself, `create` needs `OPENAI_API_KEY` or `AM_LLM_KEY`.
