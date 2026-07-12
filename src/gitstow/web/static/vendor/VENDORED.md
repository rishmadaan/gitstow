# Vendored web assets

These assets are stored in the package so the web dashboard works without an
internet connection.

## htmx

- File: `htmx.min.js`
- Version: 1.9.10
- Source: <https://unpkg&#46;com/htmx.org@1.9.10/dist/htmx.min.js>
- SHA-256: `b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e`
- License: BSD 2-Clause

## Google Fonts

The stylesheet was fetched from the Google Fonts CSS2 API below using the
Chrome 120 user agent specified in the task brief, then reduced to its latin
and latin-ext `@font-face` blocks and rewritten to use local paths:

<https://fonts&#46;googleapis&#46;com/css2?family=Bricolage+Grotesque:opsz,wdth,wght@12..96,75..100,300..700&family=JetBrains+Mono:wght@400;500;600&display=swap>

Both font families are licensed under the SIL Open Font License 1.1 (OFL-1.1).

- File: `../fonts/bricolage-grotesque-latin-ext-var.woff2`
  - Source: https://fonts.gstatic.com/s/bricolagegrotesque/v9/3y996as8bTXq_nANBjzKo3IeZx8z6up5L-aNGfyOPPtQPw.woff2
  - SHA-256: `104b93499342ede4da68d37234be0e5229345f0be0b9509328f3071f5fb9e8c8`
  - License: OFL-1.1
- File: `../fonts/bricolage-grotesque-latin-var.woff2`
  - Source: https://fonts.gstatic.com/s/bricolagegrotesque/v9/3y996as8bTXq_nANBjzKo3IeZx8z6up5L-iNGfyOPPs.woff2
  - SHA-256: `9fee080fcc2d2e0ea8c7ce2a58abaa8ba1f40c6e603643327cd5eb6f07db06a8`
  - License: OFL-1.1
- File: `../fonts/jetbrains-mono-latin-ext-var.woff2`
  - Source: https://fonts.gstatic.com/s/jetbrainsmono/v24/tDbv2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKwBNntkaToggR7BYRbKPx7cwgknk-6nFg.woff2
  - SHA-256: `9c38cb2d0d2d93c1ee6e21fa78db76f13ea7e15e15cc64214c7ca89b6aaa35c4`
  - License: OFL-1.1
- File: `../fonts/jetbrains-mono-latin-var.woff2`
  - Source: https://fonts.gstatic.com/s/jetbrainsmono/v24/tDbv2o-flEEny0FZhsfKu5WU4zr3E_BX0PnT8RD8yKwBNntkaToggR7BYRbKPxDcwgknk-4.woff2
  - SHA-256: `2c32b9b3ee358c119e210f6f5195f9bd34894d78a785ff2e95d60e718e400af4`
  - License: OFL-1.1
