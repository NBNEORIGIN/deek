# Facebook Close Friend Post Notification

This email is a standard Facebook notification informing the recipient that someone they've designated as a "close friend" has shared or posted content. These automated emails are triggered when users on your close friends list engage with content, and contain tracking parameters, deep links to Facebook, and unsubscribe options.

## Email Structure

### 1. Subject Line
- Format: `👋 [Name] just posted`
- Uses emoji and personalization to increase open rates
- Indicates the specific user who posted

### 2. Primary Content
- Direct link to the Facebook post (appears twice in the email)
- Brief description of the activity (e.g., "Mike Brown shared Paul Dawson's post")
- Explanation of why the email was triggered

### 3. URL Parameters
The Facebook URLs contain multiple tracking parameters:
- `aref` - User card identifier (redacted in example)
- `medium=email` - Indicates the traffic source
- `mid` - Message/notification identifier
- `bcode` - Contains timestamp and tracking code with postcode data
- `n_m` - Recipient email address
- `n_sg` - Security/session token
- `from_close_friend=1` - Indicates close friend trigger

### 4. Footer Elements
- Recipient email address confirmation
- Unsubscribe link with user ID and message tracking
- Meta corporate address (1 Meta Way, Menlo Park, CA 94025)

## Common Characteristics

### Legitimacy Indicators
- **Sender domain**: Should be from `@facebookmail.com` or `@meta.com` (check actual headers)
- **Links point to**: `facebook.com/nd/` (notification dispatcher)
- **Corporate branding**: References Meta Platforms, Inc.
- **Privacy controls**: Includes unsubscribe mechanism

### Data Points Present
- Recipient email: `toby@originjewellery.co.uk`
- Poster name: Mike Brown
- Original content creator: Paul Dawson
- User ID in unsubscribe link: `100011518660061`

## Operator Notes

⚠️ **Warning**: These emails contain significant tracking data. When processing:
- Multiple identifiers are present that could correlate user activity
- The `n_m` parameter exposes the recipient's email in the URL
- The `bcode` parameter may contain geographic information (postcode references)
- Deep links bypass standard Facebook login flow

⚠️ **Common Pitfall**: The same URL appears twice in the body, which is unusual for legitimate emails but is standard for Facebook's notification format. Don't flag as suspicious based solely on this repetition.

## Processing Recommendations

### 1. User Intent Classification
- **Primary purpose**: Engagement/retention notification
- **Action required**: None (informational only)
- **Urgency**: Low (social media notification)

### 2. Privacy Considerations
When handling these emails:
- Redact all `aref` parameters (contain user card numbers)
- Redact postcode data in `bcode` parameters
- Be aware that email address appears in plaintext in URL parameters
- User ID is not considered highly sensitive but may need redaction depending on policy

### 3. Spam/Phishing Assessment
Legitimate Facebook notifications will:
- Have consistent URL structure pointing to `facebook.com`
- Include proper unsubscribe mechanisms
- Reference specific user actions
- Not request login credentials in the email body

## Related Topics

- **Facebook Notification Email Types**: Overview of various Facebook automated emails
- **Social Media Platform Identifiers**: Understanding tracking parameters across platforms
- **Close Friends Feature**: How Facebook's relationship tiers work
- **Email Tracking Parameter Analysis**: Deep dive into URL parameter structures
- **PII Redaction Standards**: Guidelines for handling exposed email addresses and identifiers
- **Meta Email Authentication**: Verifying legitimate Meta/Facebook emails via SPF/DKIM

## Technical Reference

**Notification ID Format**: `[hex]G[hex]G[hex]G[hex]`
**Standard recipient field**: `n_m` parameter
**Unsubscribe token**: `k` parameter in unsubscribe URL
**User identifier**: `u` parameter (Facebook UID)