# Sundark landing page

The public product introduction is separate from the local reader in `web/`.
This directory contains only public, static files. It does not run the reader or
accept papers, credentials, or AI requests.

## Structure

```text
site/
  index.html          Korean product copy, illustrated reader, installation, FAQ
  assets/
    styles.css        Responsive layout, focus styles, reduced-motion support
    favicon.svg       Brand mark
  README.md           Maintenance and delivery instructions
```

The reader illustration is HTML, explicitly labeled as an illustrative example.
There is no JavaScript, frontend framework, build step, or external asset request.
Use relative asset paths so the site works under the `/sundark/` project prefix.
Keep installation commands aligned with the root README.

## Preview

From the repository root:

```sh
python3 -m http.server 8744 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8744`. Check narrow and wide viewports, keyboard navigation,
the skip link, FAQ disclosure controls, and installation links before publishing.

## GitHub Pages

`.github/workflows/pages.yml` uploads only `site/` using GitHub's official Pages
artifact and deployment actions. Pushes to `main` that change the site or workflow
trigger deployment; manual dispatch is also available. Enable **GitHub Actions**
as the publishing source in the repository's **Settings > Pages** first.

The expected project URL is `https://jmy-org.github.io/sundark/`; a successful
workflow and an HTTP check are required before treating it as published.
The deployment does not publish `app/`, `web/`, `data/`, or `papers/`.
To roll back, restore the previous site revision on `main` and let Pages redeploy.
