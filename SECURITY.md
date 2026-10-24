# Security policy

## Supported versions

Only the latest commit on `main` receives fixes. There are no maintained release branches.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's advisory form:
https://github.com/hellpuffyt/csvsurgeon/security/advisories/new

Do not open a public issue for a suspected vulnerability. Include the version or commit,
a minimal reproduction, and the impact you observed. You should receive an acknowledgement
within a few days; fixes are released as soon as they are verified.

## Scope

csvsurgeon reads CSV files and expressions supplied on the command line. Relevant reports include: any expression that reaches Python objects, attributes, imports, files or the network (the language is designed so this is impossible); unbounded memory or recursion triggered by input; and any path where csvsurgeon overwrites or loses data it was asked only to read.
