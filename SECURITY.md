# Security Policy

## Reporting a Vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Use [GitHub's private vulnerability reporting](https://github.com/paulnsorensen/vaudeville/security/advisories/new) to report issues confidentially. Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

## Response Timeline

- **Acknowledgment**: within 72 hours
- **Initial assessment**: within 1 week
- **Fix or mitigation plan**: communicated after assessment

## Scope

In scope:
- Arbitrary code execution via rule YAML parsing
- Prompt injection in the SLM inference pipeline
- Path traversal or unsafe file handling in the daemon or hook runner
- Privilege escalation via the Unix socket interface

Out of scope:
- Vulnerabilities in upstream Phi-4-mini model weights
- Issues requiring physical access to the local machine
- Denial-of-service against the local daemon
