# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Silas, **please do not open a public issue**.

Instead, report it privately via email to [david@feldhofer.cc](mailto:david@feldhofer.cc).

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You'll receive acknowledgment within 48 hours. We'll work with you on a fix before any public disclosure.

## Scope

Security-relevant areas of Silas include:

- **Approval system** — Ed25519 token signing, plan hash binding, nonce replay protection
- **Sandbox execution** — process isolation, network access controls
- **Gate system** — policy enforcement, input/output filtering
- **Credential handling** — OS keyring isolation, secret non-exposure
- **Access control** — per-connection scope isolation, tool filtering
- **Taint tracking** — trust classification propagation

## Supported Versions

Only the latest version on the `dev` branch receives security updates. There are no stable releases yet.
