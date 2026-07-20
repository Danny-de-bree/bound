# Release Integrity — Beveiliging & Controles

> **Status:** De release.yml workflow bevat de meeste integrity checks.  
> **Aandachtspunt:** GitHub Actions zijn nog niet gepind op SHA; zie aanbevelingen onderaan.

## 1. Concurrency Lock

```yaml
concurrency:
  group: release-${{ inputs.version }}
  cancel-in-progress: false
```

- **Doel:** Voorkomt dat dezelfde versie twee keer tegelijk wordt gebouwd.
- `cancel-in-progress: false` — als een release al draait voor deze versie, wacht de tweede in de queue.
- **Let op:** Dit werkt alleen voor dezelfde versie-invoer. Een release `0.7.0` en `0.7.1` kunnen parallel lopen.

## 2. Tag-bestaat-nog-niet Check

```yaml
- name: Check tag does not exist yet
  run: |
    TAG="v${{ inputs.version }}"
    if git rev-parse "$TAG" >/dev/null 2>&1; then
      echo "❌ Tag $TAG already exists — refusing to release"
      exit 1
    fi
```

- **Doel:** Voorkomt dat een bestaande tag wordt overschreven.
- **Beperking:** Checkt alleen lokaal. Als de tag extern bestaat maar niet in de lokale clone, wordt dit gemist.
- **Aanbevolen verbetering:** Voeg remote check toe:
  ```bash
  if git ls-remote --tags origin "$TAG" | grep -q .; then exit 1; fi
  ```
## 3. Versieconsistentie Checks

### pyproject.toml vs Input
```yaml
- name: Verify version matches pyproject.toml
  run: |
    PACKAGE_VERSION=$(uv run python -c \
      "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
    test "$PACKAGE_VERSION" = "${{ inputs.version }}"
```

### `bound.__version__` vs Input
```yaml
- name: Verify version matches bound.__version__
  run: |
    CODE_VERSION=$(uv run python -c "import bound; print(bound.__version__)")
    test "$CODE_VERSION" = "${{ inputs.version }}"
```

- **Doel:** Drie bronnen moeten gelijk zijn: `pyproject.toml`, `bound.__version__`, en workflow input.
- **Effect:** Als iemand vergeet een versie te updaten, faalt de workflow vóór de build.

## 4. Skills ZIP Structuur Check

```yaml
- name: Verify Skills ZIP contains bound/SKILL.md
  run: |
    unzip -l release/skills/BOUND-agent-skill.zip | grep -q "bound/SKILL.md"
```

- **Doel:** Valideert dat de Skills ZIP de juiste directorystructuur heeft.
- Dit is een minimale check; uitgebreidere validatie kan worden toegevoegd.

## 5. Checksum Generatie en Verificatie

```yaml
- name: Generate checksums
  run: |
    cd release
    sha256sum python/* skills/* > SHA256SUMS.txt
    cat SHA256SUMS.txt
```

- **Alle artifacts** (wheel, sdist, Skills ZIP) krijgen een SHA256 checksum.
- De `SHA256SUMS.txt` wordt **ook** als release asset geüpload.
- **Verificatie door gebruiker:** `sha256sum -c SHA256SUMS.txt`

## 6. Build-once Principe

- **quality-and-build** bouwt ALLES (wheel, sdist, Skills ZIP, checksums) en uploadt als 1 Actions-artifact.
- **publish-github** en **publish-pypi** downloaden **hetzelfde** artifact — er wordt nooit opnieuw gebouwd.
- **Recovery** (publish.yml) downloadt van GitHub Release, niet van Actions artifact — maar ook nooit rebuild.

## 7. Minimale Permissies

| Job | Permissions | Waarom |
|-----|-------------|--------|
| `quality-and-build` | `contents: read` (root) | Alleen code lezen, tests draaien, artifacts uploaden |
| `publish-github` | `contents: write` | Tag pushen + GitHub Release aanmaken |
| `publish-pypi` | `id-token: write` | OIDC-token voor PyPI Trusted Publishing |

- **Root permissions** in `release.yml`: `contents: read` — geen write permissie voor niet-publish jobs.
- `ci.yml`: `contents: read` — alleen testen, geen write nodig.

## 8. Artifact Retention

```yaml
- name: Upload release bundle
  uses: actions/upload-artifact@v4
  with:
    name: release-bundle-${{ inputs.version }}
    path: release/
    retention-days: 90
```

- Artifacts worden **90 dagen** bewaard.
- Zodra de GitHub Release is aangemaakt, zijn de artifacts ook permanent beschikbaar als release assets.

## 9. Aanbevelingen: Action Pinning

### Huidige situatie

| Action | Huidige pin | Risico |
|--------|-------------|--------|
| `actions/checkout` | `@v7` | Major-version tag (v7→v7.1 is mutable) |
| `astral-sh/setup-uv` | `@v7` | Idem |
| `actions/upload-artifact` | `@v4` | Idem |
| `actions/download-artifact` | `@v4` | Idem |
| `softprops/action-gh-release` | `@v2` | Idem |
| `pypa/gh-action-pypi-publish` | `@release/v1` | **Branch tag** — mutable! |

### Advies

**Kortetermijn:** Major-version tags zijn semver-stabiel. De `@release/v1` tag van `pypa/gh-action-pypi-publish` is een git branch, maar wordt alleen door de maintainers bijgewerkt na tests.

**Lange termijn (aanbevolen):** Pinnen op SHA-commit en Dependabot gebruiken voor updates:

```yaml
# In plaats van:
- uses: actions/checkout@v7
- uses: pypa/gh-action-pypi-publish@release/v1

# Liever:
- uses: actions/checkout@<SHA-van-v7.0.0>
- uses: pypa/gh-action-pypi-publish@<SHA-van-release-v1>
```

**Voordelen SHA-pinning:**
- 🔒 Action kan niet ongemerkt veranderen (supply chain security)
- 🔁 Reproduceerbare builds

**Nadelen:**
- ⚠️ Geen automatische security updates — Dependabot/Renovate nodig
- 📖 Minder leesbaar

**Dependabot config:**
```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

## 10. Dubbele Publicatie Voorkomen

- **Concurrency lock** + **tag check** voorkomen dat dezelfde versie twee keer wordt gepubliceerd.
- **PyPI** staat hoe dan ook geen dubbele publicatie van dezelfde versie toe — laatste safety net.

## 11. Overzicht Integrity Checks

| Check | Waar | Status |
|-------|------|--------|
| ✅ Concurrency lock | `release.yml` concurrency group | Gïmplementeerd |
| ✅ Tag bestaat nog niet | quality-and-build step | Gïmplementeerd (lokaal) |
| ✅ Versie match pyproject.toml | quality-and-build step | Gïmplementeerd |
| ✅ Versie match `__version__` | quality-and-build step | Gïmplementeerd |
| ✅ Ruff lint | quality-and-build step | Gïmplementeerd |
| ✅ Tests | quality-and-build step | Gïmplementeerd |
| ✅ Skills ZIP structuur | quality-and-build step | Gïmplementeerd |
| ✅ SHA256 checksums | quality-and-build step | Gïmplementeerd |
| ✅ Artifact retention (90d) | quality-and-build step | Gïmplementeerd |
| ✅ Minimale permissions | Job-level permissies | Gïmplementeerd |
| ✅ Build-once (immutable artifact) | Architectuur | Gïmplementeerd |
| ⚠️ Tag remote check | Aanbevolen verbetering | Nog niet |
| ⚠️ Action SHA-pinning | Aanbevolen verbetering | Nog niet |
| ✅ Geen hardcoded PyPI tokens | Repository scan | Schoon |