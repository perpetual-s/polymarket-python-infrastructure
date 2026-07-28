# Security policy

## Protect credentials

Never commit private keys, API keys, passphrases, wallet exports, `.env` files,
or authenticated response captures. If a credential is exposed, remove it
from repository history and rotate it immediately; redaction after exposure
is not sufficient.

Use a secret manager or an ignored local environment file. Prefer a dedicated,
low-value wallet when exercising authenticated behavior.

## Report a vulnerability

Please use GitHub's private vulnerability reporting for this repository when
available. Do not open a public issue containing an exploit, private key,
credential, wallet export, or unredacted authenticated response.

Include the affected version or commit, reproduction steps that do not require
real funds, impact, and any suggested remediation. Reports involving live
accounts should use sanitized identifiers and responses.

## Scope

The default tests are offline and hermetic. Passing them does not establish
live exchange availability, regional eligibility, wallet entitlement,
allowances, balances, or safe profitability.
