# Portal Authentication

Signing in to the mAIvn Developer Portal is how you get to your dashboard,
projects, and account settings in the web app. This guide walks through
what the sign-in flow does, how to reset a forgotten password, and how to
clear up the login problems people hit most often.

> **See also:** [Authentication & API Keys](authentication.md) — authenticating the SDK with an API key (distinct from signing in to the portal web app covered here).

## Sign-In Flow

1. Enter your email and password on the portal login page.
2. On success, the platform creates a secure server-managed session and redirects you to your dashboard.
3. Protected pages require an active signed-in session and automatic request security checks.

![Developer Portal landing and sign-in entry](/developer_portal/maivn_portal_landing.png "Use the public portal entry to sign in or start account creation")

## Common Login Issues

### Invalid email or password

- Re-enter your credentials carefully.
- Confirm Caps Lock is off.
- Use password reset if needed.

### Account not verified yet

- Check your inbox for an account verification email.
- Open the verification link, then sign in again.

### Session expired

- Sign in again to refresh your session.
- If this repeats frequently, contact your workspace admin.

### Authentication temporarily unavailable

- Refresh the page and try again.
- If the issue persists, contact support.

![Portal login form with invalid-credentials message](/developer_portal/placeholders/login-invalid-credentials.png "Sign-in form when credentials are rejected")

## Password Reset

1. Select **Forgot password?** on the login page.
2. Enter your account email.
3. Open the reset link sent to your inbox.
4. Set a new password and sign in again.

![Forgot-password request and password-reset confirmation screens](/developer_portal/placeholders/password-reset-flow.png "Password reset request flow")

## Session Behavior

- Signing out immediately ends your active session.
- Opening protected routes while signed out redirects you to `/login`.
- Profile, organization, project, and billing pages require authentication.
- Security-sensitive actions (for example profile or credential updates) require a valid in-session CSRF token.

## Related

- [Authentication & API Keys](authentication.md) — authenticate the SDK
  with an API key (distinct from the portal sign-in covered here).
- [Workspace Operations](workspace-operations.md) — what you can do once
  you are signed in: organizations, projects, and navigation.
