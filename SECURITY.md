# Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Instead, open a [GitHub Security Advisory](https://github.com/JasonPuglisi/e-note-ion/security/advisories/new)
to report privately. Include as much detail as you can: the nature of the issue,
steps to reproduce, and any potential impact.

## API Key Handling

This project stores secrets (Vestaboard API key, integration credentials) in
`config.toml`, which is git-ignored. Never commit `config.toml` or include
secrets in content JSON files or Docker image builds. When running with Docker,
mount `config.toml` at runtime via `-v /path/to/config.toml:/app/config.toml`,
not baked into the image.
