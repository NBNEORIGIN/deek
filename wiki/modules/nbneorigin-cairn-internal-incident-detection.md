# NBNEORIGIN/cairn - Internal Incident Detection

## Summary

This article covers the GitGuardian security alert system integrated with NBNE's Cairn knowledge base. GitGuardian monitors internal repositories for exposed secrets, credentials, and sensitive data. When an incident is detected in the NBNEORIGIN/cairn system, operators receive automated email notifications requiring immediate triage and remediation. Understanding these alerts and responding appropriately is critical for maintaining operational security.

## Alert Anatomy

GitGuardian alerts for NBNEORIGIN/cairn follow a standardized format:

1. **Subject Line**: Always begins with "NBNEORIGIN/cairn - [number] internal incident detected"
2. **Email Body**: Contains HTML-formatted incident details with embedded styling
3. **Incident Count**: Indicates how many distinct secrets or vulnerabilities were found
4. **Repository Context**: Links to specific commits or files where exposure occurred

## Incident Response Procedure

### 1. Initial Assessment (0-15 minutes)

- **DO NOT** ignore these alerts - they indicate potential security compromises
- Log into the GitGuardian dashboard immediately to view full incident details
- Identify the type of secret exposed (API key, token, password, certificate, etc.)
- Determine the scope: Which repository, branch, and commit contains the exposure?

### 2. Validate the Finding (15-30 minutes)

Not all detections are true positives. Check for:

- **Test credentials**: Development or example keys that aren't production secrets
- **False positives**: Strings matching secret patterns but aren't actual credentials
- **Already rotated secrets**: Keys that have been previously invalidated

If confirmed as a false positive, mark it as resolved in GitGuardian with appropriate justification.

### 3. Immediate Containment (30-60 minutes)

For validated incidents:

1. **Revoke the exposed credential immediately** through the relevant service portal
2. Generate a replacement credential with appropriate access controls
3. Update the credential in secure storage (Vault, Secrets Manager, etc.)
4. Never commit the new credential to the repository

### 4. Remediation (1-4 hours)

- Remove the secret from repository history using `git filter-branch` or BFG Repo-Cleaner
- Update all services and applications using the revoked credential
- Verify no downstream services are broken by the rotation
- Document the incident in the security log with ticket reference

### 5. Post-Incident Actions

- Review access logs for the compromised credential to detect potential unauthorized use
- Assess whether additional credentials need rotation as a precaution
- Update documentation to prevent similar exposures
- Mark the incident as resolved in GitGuardian only after complete remediation

## Common Pitfalls

⚠️ **WARNING: Repository History Persistence**
Simply deleting a secret from the current commit does NOT remove it from Git history. The exposed credential remains accessible in previous commits and must be purged using proper tools.

⚠️ **WARNING: Public Fork Exposure**
If NBNEORIGIN/cairn has been forked, exposed secrets may exist in external repositories. Check for forks and contact repository owners if necessary.

⚠️ **WARNING: Cached Artifacts**
CI/CD pipelines, Docker images, and build artifacts may contain the exposed secret. These must be rebuilt after rotation.

## Prevention Best Practices

1. **Use environment variables** for all secrets - never hardcode in source
2. **Enable pre-commit hooks** with GitGuardian client-side scanning
3. **Implement secrets management** through HashiCorp Vault or AWS Secrets Manager
4. **Regular secret rotation** on a defined schedule (30, 60, or 90 days)
5. **Team training** on secure credential handling

## Email Rendering Issues

The GitGuardian alert emails contain extensive inline CSS and HTML formatting. If your email client displays raw HTML or CSS code (as shown in the truncated example), this doesn't affect the alert's validity:

- Use the GitGuardian web dashboard for full incident details
- Configure your email client to render HTML properly
- Consider setting up Slack or PagerDuty integrations for cleaner notifications

## Escalation Criteria

Escalate immediately to the security team if:

- The exposed secret is a production database credential
- The secret has been public for more than 24 hours
- Access logs show suspicious activity using the credential
- The incident involves customer data or PII
- You're unable to revoke/rotate the credential within 1 hour

## Related Topics

- **Cairn Security Architecture**: Overview of security controls in the Cairn system
- **Secret Rotation Procedures**: Detailed playbooks for rotating various credential types
- **Git History Rewriting**: Technical guide to removing sensitive data from repositories
- **Incident Response Playbook**: General security incident handling procedures
- **GitGuardian Dashboard Access**: How to request and configure GitGuardian access