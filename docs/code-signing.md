# Code signing with Microsoft Artifact Signing

Status (2026-11-06): the release and beta builders are ready to sign, and no
signing account exists yet. Every build therefore prints
`code signing: unsigned: no Artifact Signing configuration on this machine`
and carries on.

## What signing does for the trainer

A signature names a publisher on each file. Windows shows that name in the
file's Properties and in SmartScreen's prompt. On 2026-11-06 Defender
quarantined our own renderer, an anonymous and never-seen file, as
"Trojan:Win32/Bearfoos.A!ml". Microsoft's
[developer guidance](https://learn.microsoft.com/en-us/defender-xdr/developer-faq)
says consistent signing "helps our research team quickly identify the source
of a program". Signing is one fix among several. The others change what the
trainer does, so that it never looks like malware in the first place.

Signing does not stop SmartScreen warnings at once. Microsoft's own page says
"Even when signed, a newly created binary could still show a SmartScreen
warning until its hash or publisher certificate accumulates sufficient
evidence of positive reputation". It also says reputation "cannot transfer
from previous versions unless both were signed using the same publisher
identity". So one identity, kept for every release, is the point.

## What only you can do

These steps use your identity and your money, so an agent cannot do them.

1. **An Azure subscription** in a Microsoft Entra tenant (the quickstart's
   prerequisites).
2. **Register the resource provider** `Microsoft.CodeSigning`: Azure portal,
   Subscriptions, your subscription, Resource providers.
3. **Create an Artifact Signing account** (portal: search "Artifact Signing
   Accounts", Create). Pick a region and remember it: the endpoint must match
   it, for example West US 2 is `https://wus2.codesigning.azure.net`. Pick the
   **Basic** pricing tier.
4. **Give yourself two roles** on that account: *Artifact Signing Identity
   Verifier* (to validate) and *Artifact Signing Certificate Profile Signer*
   (to sign).
5. **Validate your identity as an Individual, Public.** Identity validations,
   then Organization, Individual, New Identity, Public.
   - "Individual developers must be located in the United States or Canada."
   - The form copies your **billing account**. Its type must be Individual,
     and "The legal name and sold-to address on the billing account must
     match the information on the government-issued ID". Fix the billing
     account first if they differ.
   - You verify through AU10TIX with a government photo ID and your phone,
     then add a Verified ID to Microsoft Authenticator.
   - **What becomes public:** your legal name, and "The city,
     state/province, and country/region" of your address, appear on the
     certificate, so on every signed file. "Your email address and street
     address aren't included in the certificate."
6. **Create a certificate profile** of type Public Trust, and pick the
   validated identity for "Verified CN and O".
7. **Tell the agent the three names:** the account endpoint (from the
   region), the account name and the certificate profile name. None of them
   is a secret, but they stay out of this repository.

### Cost

From Azure's public retail price list (prices.azure.com, "Trusted Signing",
fetched 2026-11-06) and the pricing page's quota table:

| Tier | Monthly price | Signatures included each month | After that |
| --- | --- | --- | --- |
| Basic | $9.99 | 5,000 | $0.005 each |
| Premium | $99.99 | 100,000 | $0.005 each |

A release signs four files (the app's `SM64Trainer.exe` and the three
natives), so Basic covers far more releases than we make.

## What an agent then does on the build machine

1. Install Microsoft's client tools (SignTool, .NET 8 and the signing dlib):
   `winget install -e --id Microsoft.Azure.ArtifactSigningClientTools`.
2. Write Microsoft's `metadata.json` **outside the repository**, for example
   `%LOCALAPPDATA%\SM64Trainer\signing\metadata.json`:
   ```json
   {"Endpoint": "https://wus2.codesigning.azure.net",
    "CodeSigningAccountName": "<account name>",
    "CertificateProfileName": "<certificate profile name>"}
   ```
3. Write the trainer's own `%LOCALAPPDATA%\SM64Trainer\signing\signing.json`
   (or name another file in `SM64_SIGNING_CONFIG`):
   ```json
   {"dlib": "C:/.../bin/x64/Azure.CodeSigning.Dlib.dll",
    "metadata": "C:/Users/<you>/AppData/Local/SM64Trainer/signing/metadata.json",
    "signtool": "C:/.../x64/signtool.exe"}
   ```
   `signtool` is optional; without it the newest Windows SDK x64 SignTool is
   used. The dlib must match SignTool's architecture.
4. Sign in to Azure once on that machine, for example `az login`: the dlib
   authenticates through DefaultAzureCredential.
5. Check: `uv run python tools/code_signing.py --status`, then sign one
   scratch file with `uv run python tools/code_signing.py <file>`.

## What gets signed, and where

`tools/code_signing.py` runs Microsoft's documented SignTool command, then
`signtool verify /pa` on each file. `tools/build_exe.py` signs the packaged
natives and `SM64Trainer.exe` after PyInstaller and before the zip and the
manifest exist, so the candidate record and the update manifest carry the
signed bytes; `tools/build_beta.py` does the same for a legacy private beta.
A signing failure stops the build. Microsoft Defender then scans the signed
files (`tools/defender_scan.py`).

Not signed yet: the public bootstrap `SM64TrainerSetup.exe` (a PyInstaller
one-file program) and the private beta installers (a stub with an appended
payload that is read from the end of the file). A signature appends a
certificate table to the file. Neither reader has been tried against a signed
file, so each needs one real signed test before it joins the list.

Artifact Signing certificates last three days. The signature stays valid
afterwards because SignTool timestamps it through
`http://timestamp.acs.microsoft.com`.

## Sources (fetched 2026-11-06)

- [Artifact Signing quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart):
  prerequisites, regions and endpoints, roles, individual identity
  validation, certificate profiles.
- [Set up signing integrations](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations):
  the client tools, `metadata.json`, the SignTool command, the three-day
  certificates and the timestamp server.
- [Artifact Signing FAQ](https://learn.microsoft.com/en-us/azure/artifact-signing/faq):
  `signtool verify /pa` when SignTool reports success but the file looks
  unsigned; SmartScreen reputation for signed files.
- [Artifact Signing pricing](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/)
  (quota per tier) and the Azure retail prices API
  (`https://prices.azure.com/api/retail/prices?$filter=serviceName eq 'Trusted Signing'`)
  for the monthly prices, which the pricing page fills in with script.
- [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).
