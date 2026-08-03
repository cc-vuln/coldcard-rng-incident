# Security policy

## Reporting a vulnerability in this project

If you find a security problem in this repository or on cc-vuln.org, email
**info@cc-vuln.org**. Please include enough detail to reproduce it. You can
expect an acknowledgement within a few days. There is no bounty program.

In scope:

- The website (cc-vuln.org) and its build pipeline
- The capture tooling under `scripts/`
- The integrity of the published record: anything that could let the archive
  be silently altered, or make it misrepresent what a source said
- Personal-information leakage in published output. This archive publishes
  what people published themselves, but not what they did not: if you find
  material that was never public, or anything identifying this project's
  operators, in the site or the repository, report it privately rather than
  opening a public issue. An author who wants their own captured post taken
  down should write to the same address

## The COLDCARD vulnerability itself

This project documents the incident; it did not discover it and does not
maintain the affected firmware. Reports about COLDCARD devices or firmware
belong with the vendor, Coinkite, via support.coinkite.com. Reports about
sweep or recovery services belong with whoever operates them. Nothing in this
repository is an exploit, and requests for exploitation tooling will not
receive a response.

## Keys and provenance

Commits to `main` are SSH-signed. The archive's integrity model is documented
in the repository: every capture is hashed at collection time, and published
excerpts link back to those hashes so any claim can be re-checked.
