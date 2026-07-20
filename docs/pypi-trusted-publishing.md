# PyPI Trusted Publishing — Configuratiegids

> **Status:** Moet nog worden uitgevoerd (externe stappen in GitHub UI en PyPI UI).
> De workflows in `.github/workflows/release.yml` en `.github/workflows/publish.yml` zijn al ingericht op `id-token: write`. Alleen de environment en de PyPI trusted publisher moeten nog worden aangemaakt.

## Overzicht

In plaats van een langdurig PyPI API token (dat kan lekken, verlopen, of vergeten worden te roteren), gebruikt deze repository **Trusted Publishing** via OpenID Connect (OIDC).

**Hoe het werkt:** GitHub Actions vraagt een kortdurend OIDC-token aan (`id-token: write`). PyPI vertrouwt dat token omdat het gecryptografisch is ondertekend door GitHub. Geen gedeelde geheimen, geen tokens in secrets.

## Stap 1 — GitHub Environment `pypi` aanmaken

1. Ga naar **GitHub → bound → Settings → Environments**
2. Klik **"New environment"**
3. Naam: `pypi`
4. Klik **"Configure environment"**

### Optional: Manual approval (aanbevolen)

Voeg in het environment `pypi` een **Required reviewers** toe:

1. Vink **"Required reviewers"** aan
2. Voeg ten minste één GitHub-gebruiker toe (bijv. `Danny-de-bree`)
3. Klik **"Save protection rules"**

> **Waarom?** Een manual approval voorkomt dat een `workflow_dispatch` per ongeluk een verkeerde versie naar PyPI publiceert. De build en GitHub Release kunnen al lopen; de PyPI-publicatie wacht op een handmatige goedkeuring.

### Environment secrets (niet nodig)

Omdat we Trusted Publishing gebruiken, is **geen** PyPI-token nodig als environment secret. De `publish-pypi` job gebruikt alleen `id-token: write`.

## Stap 2 — PyPI Trusted Publisher configureren

1. Ga naar **[PyPI → Account settings → Publishing](https://pypi.org/manage/account/publishing/)**
2. Klik **"Add a new pending publisher"** (of **"Add publisher"**)
3. Vul de volgende gegevens in:

| Veld | Waarde |
|------|--------|
| **PyPI Project Name** | `bound-policy` |
| **Owner** | `Danny-de-bree` |
| **Repository name** | `bound` |
| **Workflow name** | `release.yml` |
| **Environment name** | `pypi` |

4. Klik **"Add"**

> **Let op:** De workflow-naam is `release.yml` (niet `publish.yml`). De `publish.yml` (recovery) gebruikt dezelfde environment en kan daardoor ook publiceren, maar alleen van bestaande GitHub Release assets — hij bouwt nooit opnieuw.

## Stap 3 — Verifieer dat workflows correct zijn ingesteld

### release.yml — `publish-pypi` job

```yaml
publish-pypi:
    name: Publish to PyPI
    needs: [quality-and-build]
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - name: Download release bundle
        uses: actions/download-artifact@v4
        with:
          name: release-bundle-${{ inputs.version }}
          path: release/
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: release/python/
```

### publish.yml — `publish` job

```yaml
publish:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
      contents: read
    steps:
      # Download van GitHub Release (niet opnieuw bouwen)
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: release/python/
```

## Stap 4 — Build job heeft geen write permissions

De `quality-and-build` job in `release.yml` heeft **geen** `id-token: write` en **geen** `contents: write`. Dit is correct: de build job mag alleen lezen en schrijven naar Actions artifact storage.

## Stap 5 — Verwijder eventuele hardcoded PyPI tokens

🔍 **Geen gevonden** — Er staan geen `PYPI_TOKEN` of `PYPI_API_TOKEN` secrets in workflows of configuratie. De repository is schoon.

## Testen

Na configuratie:

1. Start de `Release` workflow handmatig met een **nieuwe** versie (bv. `0.7.1`)
2. De `publish-pypi` job zou moeten wachten op environment approval (als geconfigureerd)
3. Keur goed in de GitHub Actions UI
4. Controleer: `pip install bound-policy==<versie>` werkt
5. Controleer: `uvx --from bound-policy==<versie> bound --help` werkt

## Bijlage: Waarom Trusted Publishing?

| Aspect | API Token | Trusted Publishing (OIDC) |
|--------|-----------|--------------------------|
| Levensduur | Maanden/jaren | Minuten (per run) |
| Rotatie | Handmatig | Automatisch |
| Scope | Heel PyPI project | Enkel 1 workflow + environment |
| Risico bij lek | Volledige publish-machtiging | Token is onbruikbaar na 15 min |
| Secrets management | Nodig | Overbodig |