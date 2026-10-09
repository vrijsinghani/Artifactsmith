# Output formats

You pick a format when you create an artifact. HTML is the default. Markdown, PDF, Word (DOCX), and Excel (XLSX) are also available. Every format runs the same checks: scripts and remote URLs are stripped, private-link hosts and likely secrets fail the build, and size and time caps apply.

## How a file is produced

The model writes content. A renderer on the server turns that content into bytes. You do not get model-written code executed, and the renderer does not fetch the network.

`artifactsmith.renderers.Renderer` is the contract.

`format` is the wire name: `html`, `markdown`, `pdf`, `docx`, or `xlsx`.

`model_filename` is the name the model must put in `===FILE: …===`.

`render(title, body)` returns a `RenderOutput`.

`get_renderer(fmt)` looks up the implementation. `create(..., format=)` stores the choice on the artifact. `edit` keeps that choice.

## Libraries

| Format | Library | License | Notes |
| ------ | ------- | ------- | ----- |
| HTML | none (sanitize, then store) | | The house-style prompt already emits a complete document. |
| Markdown | stdlib | | Stored as UTF-8 text. |
| PDF | WeasyPrint ≥62 | BSD-3-Clause | HTML and CSS to PDF through Pango and Cairo. |
| DOCX | python-docx | MIT | Writes Office Open XML. |
| XLSX | openpyxl | MIT | Markdown tables become sheets. Remaining prose goes on a Notes sheet. |

WeasyPrint needs Pango, Cairo, GdkPixbuf, and a font. `docker/Dockerfile` installs those packages.

PDF uses WeasyPrint because it runs in the slim image without Chromium. Browser print-to-PDF would add a browser. ReportLab would mean laying out the page without CSS.

## Prompts

`html` uses the house-style system prompt and `===FILE: index.html===`. `tests/unit/test_house_style.py` locks the prompt markers and the safety checks.

`markdown`, `pdf`, `docx`, and `xlsx` use a content prompt and `===FILE: content.md===`. Writing rules stay. Visual HTML rules do not apply.

PDF, DOCX, and XLSX store the rendered file and `content.md`. `edit` loads `content.md` as the base file.

## Safety

`artifactsmith.renderers.safety` runs inside `builder.run_build`.

Likely secrets fail the build (PEM keys and common cloud, GitHub, Slack, and OpenAI token shapes).

When `AM_BLOCK_PRIVATE_LINKS=true`, private, loopback, and link-local hosts fail the build.

When `AM_ALLOWED_LINK_DOMAINS` is set, any other host fails the build.

`<script>` tags and `http(s)` / `ftp` URLs are stripped. After that, HTML still has to be a complete document.

`AM_RENDER_TIMEOUT` caps one renderer call. `AM_MAX_OUTPUT_BYTES` caps stored bytes.

Renderers do not call `httpx` and do not open sockets. WeasyPrint receives an in-memory HTML string with `base_url="."` and no remote stylesheets.
