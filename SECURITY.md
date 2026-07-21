# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a Vulnerability

We take the security of privacy-local-agent seriously. If you believe you have found a
security vulnerability, please report it responsibly.

**Please do NOT open a public GitHub issue for security vulnerabilities.**

### How to Report

1. Email: Send details to the repository maintainer via GitHub Security Advisories
2. Navigate to: **Security → Advisories → Report a vulnerability**
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 7 days
- **Fix timeline**: Depends on severity (Critical: 7 days, High: 14 days, Medium: 30 days)

### Scope

The following are in scope:
- Authentication/authorization bypass
- Privacy budget manipulation
- Data leakage through DP noise parameters
- Remote code execution
- Denial of service via resource exhaustion

### Out of Scope

- Self-XSS (no impact on other users)
- Missing HTTP security headers (informational)
- Social engineering attacks

## Security Best Practices for Deployments

When deploying to production, enable:

```bash
# TLS
PRIVACY_TLS_ENABLED=true
PRIVACY_TLS_CERT_FILE=/certs/server.crt
PRIVACY_TLS_KEY_FILE=/certs/server.key

# Authentication
PRIVACY_AUTH_ENABLED=true
PRIVACY_AUTH_INTERNAL_KEYS_JSON='{"sk-xxx":{"name":"service","scopes":["*"]}}'

# Rate Limiting
PRIVACY_RATE_LIMIT_ENABLED=true
PRIVACY_RATE_LIMIT_DEFAULT="100/minute"
```

See [Production Security Documentation](./docs/production_security/) for details.
