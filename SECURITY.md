# Security Policy

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Instead, open a [GitHub Security Advisory](https://github.com/JasonPuglisi/e-note-ion/security/advisories/new)
to report privately. Include as much detail as you can: the nature of the issue,
steps to reproduce, and any potential impact.

## API Key Handling

This project requires a Vestaboard Read/Write API key via the `VESTABOARD_API_KEY`
environment variable. Keep this key out of version control and never include it
in content JSON files or Docker image builds. When running with Docker, pass it
at runtime via `-e VESTABOARD_API_KEY=...` or an env file, not in the `Dockerfile`
or image layers.
