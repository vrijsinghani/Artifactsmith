# Output formats

You pick a format when you create an artifact. HTML is the default. Markdown, PDF, Word (DOCX), and Excel (XLSX) are also available. Every format runs the same checks: scripts are stripped, public http(s) citations may remain as clickable links, private-link hosts and likely secrets fail the build, and size and time caps apply. Images and other subresources stay self-contained so a file opens offline.

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
| PDF | WeasyPrint ≥70 | BSD-3-Clause | HTML and CSS to PDF through Pango and Cairo. ObjectFetcher is the object API. |
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

`<script>` tags are stripped. Public `http://` and `https://` links to global hosts are kept so research write-ups can cite sources. HTML adds `rel="noopener noreferrer nofollow"` and `target="_blank"`. Markdown may linkify bare URLs outside code. Protocol-relative `//host` is upgraded to `https://` when the host is public. Remote image syntax (`![…](https://…)`) and remote `<img src>` become a normal clickable link to the image URL (alt text, or `image`, as the label) in HTML, Markdown, PDF, DOCX, and XLSX — nothing loads the image on open. PDF emits link annotations without fetching. DOCX and XLSX write plain hyperlinks (XLSX never uses `=HYPERLINK()` formulas; when a cell has several public URLs, the first is the cell hyperlink and the others stay visible as text). Tip: put each link in its own cell so every citation stays clickable. `javascript:`, `vbscript:`, `data:`, `file:`, and obfuscated forms of those schemes are neutralized. CSS `url()`, fonts, and iframes are not allowed — documents stay self-contained. HTML still has to be a complete document.

`AM_RENDER_TIMEOUT` caps one renderer call. `AM_MAX_OUTPUT_BYTES` caps stored bytes.

Renderers do not call `httpx` and do not open sockets. WeasyPrint receives an in-memory HTML string with `base_url="."`, a deny-all URL fetcher, and no remote stylesheets.
